from datetime import datetime, timezone

import psycopg
import pytest

from conftest import task_map_entry, unmapped_log
from state import (
    get_task_map_entry_by_plm_id,
    get_watermark,
    log_unmapped,
    set_watermark,
    upsert_task_map,
)

NOW = datetime(2026, 6, 2, 17, 0, tzinfo=timezone.utc)
SNAP = {"material_codes": "M", "contents": "C", "title": "T", "brand_category": "B"}


def _upsert(conn, *, plm_id="1", task_id="TASK1", snapshot=SNAP):
    upsert_task_map(
        conn,
        plm_internal_id=plm_id,
        family_id="WP-71511",
        item_number="WP-71511-006-319",
        customer="*CORE",
        wrike_task_id=task_id,
        wrike_permalink=f"http://wrike/{task_id}",
        snapshot=snapshot,
        now=NOW,
    )


def test_task_map_absent_then_roundtrips(pg_conn):
    assert get_task_map_entry_by_plm_id(pg_conn, "1") is None
    _upsert(pg_conn)
    entry = get_task_map_entry_by_plm_id(pg_conn, "1")
    assert entry["wrike_task_id"] == "TASK1"
    assert entry["item_number"] == "WP-71511-006-319"
    assert entry["customer"] == "*CORE"
    assert entry["snapshot"] == SNAP
    # family_id is a descriptive column but still queryable.
    assert task_map_entry(pg_conn, "WP-71511")["plm_internal_id"] == "1"


def test_task_map_upsert_updates_snapshot(pg_conn):
    _upsert(pg_conn)
    _upsert(pg_conn, snapshot={**SNAP, "material_codes": "M2"})
    assert get_task_map_entry_by_plm_id(pg_conn, "1")["snapshot"]["material_codes"] == "M2"


def test_pure_duplicate_flip_repoints_the_cards_row(pg_conn):
    # Two *CORE rows ("latest updated wins"): the canonical can arrive with a NEW
    # plm_internal_id for the SAME card. The card keeps ONE row, re-pointed.
    _upsert(pg_conn, plm_id="1", task_id="TASK1")
    _upsert(pg_conn, plm_id="2", task_id="TASK1")
    assert get_task_map_entry_by_plm_id(pg_conn, "1") is None
    assert get_task_map_entry_by_plm_id(pg_conn, "2")["wrike_task_id"] == "TASK1"
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM wrike_task_map")
        assert cur.fetchone()[0] == 1


def test_one_plm_record_cannot_map_to_two_cards(pg_conn):
    # 1:1 guarantee: the PK on plm_internal_id rejects a second card for one record.
    _upsert(pg_conn, plm_id="1", task_id="TASK1")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _upsert(pg_conn, plm_id="1", task_id="TASK2")


def test_one_card_cannot_have_two_rows(pg_conn):
    # 1:1 guarantee: the UNIQUE on wrike_task_id rejects a second row for one card
    # on a direct insert (the upsert resolves this case by re-pointing instead).
    _upsert(pg_conn, plm_id="1", task_id="TASK1")
    with pytest.raises(psycopg.errors.UniqueViolation):
        with pg_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO wrike_task_map (plm_internal_id, wrike_task_id) "
                "VALUES ('2', 'TASK1')")


def test_log_unmapped_roundtrips(pg_conn):
    log_unmapped(pg_conn, item_number="WP-71511-006-319", customer="THERANGE",
                 wrike_task_id="HAND1", prefix="WP", reason="no_exact_match",
                 details="card title 'WP-71511 ... ---INTL'")
    assert unmapped_log(pg_conn) == [
        ("WP-71511-006-319", "THERANGE", "HAND1", "WP", "no_exact_match")]


def test_watermark_absent_then_roundtrips(pg_conn):
    assert get_watermark(pg_conn) is None
    set_watermark(pg_conn, NOW)
    assert get_watermark(pg_conn) == NOW
