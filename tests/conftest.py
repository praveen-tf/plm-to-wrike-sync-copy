import os

# The suite TRUNCATEs the state tables, so it must NEVER touch the real `plm` database.
# Default to the dedicated `plm_test` DB before anything opens a connection (db.connect
# reads PG_* lazily); the pg_conn fixture also hard-fails if it ever lands on `plm`.
os.environ.setdefault("PG_DB", "plm_test")

import psycopg
import pytest
from psycopg.rows import dict_row

from db import connect

PRODUCTION_DB = "plm"


@pytest.fixture
def pg_conn():
    """A live Postgres connection with the state tables truncated for test isolation.

    Skips (rather than fails) when Postgres isn't reachable, so the pure-logic unit tests
    still run without a database. Hard-fails if pointed at the production `plm` database:
    the suite TRUNCATEs the state tables, so it must run against `plm_test`.

    Note: wrike_folder_map is truncated too - tests own its contents (see seed_folder_map).
    """
    try:
        conn = connect()
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not reachable (is it running, and are PG_* set?): {exc}")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        if cur.fetchone()[0] == PRODUCTION_DB:
            conn.close()
            pytest.fail(
                f"refusing to run tests against the production database {PRODUCTION_DB!r}: "
                f"the suite TRUNCATEs state tables. Point PG_DB/PG_CONN at 'plm_test'.")
        cur.execute(
            "TRUNCATE plm_item, wrike_task_map, sync_watermark, sync_dlq, "
            "wrike_folder_map, wrike_unmapped_log")
    yield conn
    conn.close()


def seed_folder_map(conn, mapping):
    """Insert {prefix -> folder id} rows into wrike_folder_map for a test (the
    pg_conn fixture truncates the table, so plain INSERTs suffice)."""
    with conn.cursor() as cur:
        for prefix, folder_id in mapping.items():
            cur.execute(
                "INSERT INTO wrike_folder_map "
                "(prefix, wrike_folder_id, full_folder_name, space_id) "
                "VALUES (%s, %s, %s, 'space-test')",
                (prefix, folder_id, f"{prefix} - Test Folder"),
            )
    conn.commit()


def task_map_entry(conn, family_id):
    """The map row for a family - a test convenience; production code resolves the
    map only by plm_internal_id."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM wrike_task_map WHERE family_id = %s", (family_id,))
        return cur.fetchone()


def unmapped_log(conn):
    """All wrike_unmapped_log rows (item#, customer, task id, prefix, reason), oldest first."""
    with conn.cursor() as cur:
        cur.execute("SELECT item_number, customer, wrike_task_id, prefix, reason "
                    "FROM wrike_unmapped_log ORDER BY id")
        return cur.fetchall()
