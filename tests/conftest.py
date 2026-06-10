import psycopg
import pytest
from psycopg.rows import dict_row

from db import connect


@pytest.fixture
def pg_conn():
    """A live Postgres connection with the state tables truncated for test isolation.

    Skips (rather than fails) when the local Postgres isn't reachable, so the
    pure-logic unit tests still run without Docker.

    Note: wrike_folder_map is truncated too - tests own its contents (see
    seed_folder_map). On the shared dev DB the real seed rows are restored by
    re-running db/schema.sql (idempotent), same as reloading plm_item.
    """
    try:
        conn = connect()
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not reachable (is docker compose up?): {exc}")
    conn.autocommit = True
    with conn.cursor() as cur:
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
