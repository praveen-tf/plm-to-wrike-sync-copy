from fixtures_mapping import load_sample_items, plm_row
from loader import load_plm_items


def _count(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM plm_item")
        return cur.fetchone()[0]


def test_load_inserts_all_rows(pg_conn):
    load_sample_items(pg_conn)
    assert _count(pg_conn) == 11  # 6 WP/Praveen + 5 LT/Jesse


def test_load_is_idempotent(pg_conn):
    load_sample_items(pg_conn)
    load_sample_items(pg_conn)  # a second load must not duplicate
    assert _count(pg_conn) == 11


def test_loaded_row_has_derived_and_control_fields(pg_conn):
    load_plm_items(pg_conn, [plm_row("1", "WP-71511-006-319")])
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT family_id, prefix, code, product_category, ready_for_wrike "
            "FROM plm_item WHERE plm_internal_id = '1'"
        )
        assert cur.fetchone() == ("WP-71511", "WP", "71511", "BAKING", True)
