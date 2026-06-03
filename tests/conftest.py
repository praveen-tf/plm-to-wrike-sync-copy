import psycopg
import pytest

from db import connect


@pytest.fixture
def pg_conn():
    """A live Postgres connection with `plm_item` truncated for test isolation.

    Skips (rather than fails) when the local Postgres isn't reachable, so the
    pure-logic unit tests still run without Docker.
    """
    try:
        conn = connect()
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not reachable (is docker compose up?): {exc}")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE plm_item, wrike_task_map, sync_watermark, sync_dlq")
    yield conn
    conn.close()
