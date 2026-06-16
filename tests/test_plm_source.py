"""Unit tests for plm_source.read_ready_styles.

No live database: the source connection is mocked so tests verify the SQL-builder
branch (delta clause appended vs omitted at EPOCH) and parameter passing without
needing a centric_8_plm schema.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from plm_source import read_ready_styles
from state import EPOCH

T0 = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)


def _mock_source_conn(rows=None):
    """Return a (conn, cursor) pair where cursor is a mock that returns `rows`."""
    cur = MagicMock()
    cur.__enter__ = lambda self: self
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchall.return_value = rows or []
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def test_full_pull_at_epoch_omits_delta_clause():
    conn, cur = _mock_source_conn()
    read_ready_styles(conn, EPOCH)
    sql = cur.execute.call_args[0][0]
    assert "_modified_at::timestamptz >" not in sql


def test_delta_pull_includes_modified_at_filter():
    conn, cur = _mock_source_conn()
    read_ready_styles(conn, T0)
    sql = cur.execute.call_args[0][0]
    assert "_modified_at::timestamptz >" in sql


def test_delta_pull_passes_since_param():
    conn, cur = _mock_source_conn()
    read_ready_styles(conn, T0)
    _, params = cur.execute.call_args[0]
    assert params == {"since": T0}


def test_full_pull_passes_empty_params():
    conn, cur = _mock_source_conn()
    read_ready_styles(conn, EPOCH)
    _, params = cur.execute.call_args[0]
    assert params == {}


def test_returns_rows_from_cursor():
    expected = [{"id": "S1", "item_number": "WP-71511-006-319"}]
    conn, _ = _mock_source_conn(rows=expected)
    result = read_ready_styles(conn, EPOCH)
    assert result == expected


def test_query_selects_ready_for_wrike_true():
    conn, cur = _mock_source_conn()
    read_ready_styles(conn, EPOCH)
    sql = cur.execute.call_args[0][0]
    assert "mgf_ready_for_wrike #>> '{}' = 'true'" in sql


def test_query_joins_reference_tables():
    conn, cur = _mock_source_conn()
    read_ready_styles(conn, EPOCH)
    sql = cur.execute.call_args[0][0]
    assert "category2s" in sql
    assert "category1s" in sql
    assert "collections" in sql
    assert "seasons" in sql
