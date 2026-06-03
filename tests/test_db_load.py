from datetime import datetime, timezone
from pathlib import Path

from loader import load_plm_items, read_source_rows, to_plm_item

NOW = datetime(2026, 6, 2, tzinfo=timezone.utc)
DATA = Path(__file__).resolve().parent.parent / "data" / "input"


def _all_items():
    rows = read_source_rows(
        DATA / "winnie_the_pooh_input.xlsx",
        DATA / "synthetic_jesse_confection.csv",
    )
    return [to_plm_item(r, now=NOW) for r in rows]


def _count(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM plm_item")
        return cur.fetchone()[0]


def test_load_inserts_all_rows(pg_conn):
    load_plm_items(pg_conn, _all_items())
    assert _count(pg_conn) == 11  # 6 WP/Praveen (Excel) + 5 LT/Jesse (CSV)


def test_load_is_idempotent(pg_conn):
    items = _all_items()
    load_plm_items(pg_conn, items)
    load_plm_items(pg_conn, items)  # second load must not duplicate
    assert _count(pg_conn) == 11


def test_loaded_row_has_derived_and_control_fields(pg_conn):
    load_plm_items(pg_conn, _all_items())
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT family_id, prefix, code, product_category, ready_for_wrike "
            "FROM plm_item WHERE plm_internal_id = '1'"
        )
        family_id, prefix, code, category, ready = cur.fetchone()
    assert (family_id, prefix, code, category, ready) == (
        "WP-71511", "WP", "71511", "BAKING", True,
    )
