from datetime import datetime, timedelta, timezone
from pathlib import Path

from loader import load_plm_items, read_source_rows, to_plm_item
from plm_reader import pick_canonical, read_changed_items, resolve_eligible_families

DATA = Path(__file__).resolve().parent.parent / "data" / "input"
T0 = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)


def _load_all(conn, now=T0):
    rows = read_source_rows(
        DATA / "winnie_the_pooh_input.xlsx",
        DATA / "synthetic_jesse_confection.csv",
    )
    load_plm_items(conn, [to_plm_item(r, now=now) for r in rows])


def _v(customer, *, created, modified, pid="x"):
    """A minimal variant row sharing one family_id."""
    return {
        "plm_internal_id": pid,
        "family_id": "WP-71511",
        "customer": customer,
        "created_at": datetime(2026, 1, created, tzinfo=timezone.utc),
        "modified_at": datetime(2026, 1, modified, tzinfo=timezone.utc),
    }


def test_canonical_prefers_core_over_custom():
    rows = [
        _v("*CUSTOM", created=1, modified=9, pid="cu"),
        _v("*CORE", created=2, modified=3, pid="co"),
    ]
    assert pick_canonical(rows)["plm_internal_id"] == "co"


def test_canonical_falls_back_to_custom_when_no_core():
    rows = [
        _v("NEXCOM", created=1, modified=9, pid="gm"),
        _v("*CUSTOM", created=2, modified=3, pid="cu"),
    ]
    assert pick_canonical(rows)["plm_internal_id"] == "cu"


def test_canonical_earliest_created_when_no_core_or_custom():
    rows = [
        _v("NEXCOM", created=5, modified=9, pid="late"),
        _v("TJX", created=2, modified=3, pid="early"),
    ]
    assert pick_canonical(rows)["plm_internal_id"] == "early"


def test_canonical_pure_duplicate_core_prefers_latest_updated():
    rows = [
        _v("*CORE", created=1, modified=4, pid="old"),
        _v("*CORE", created=1, modified=8, pid="new"),
    ]
    assert pick_canonical(rows)["plm_internal_id"] == "new"


def test_read_changed_items_respects_watermark(pg_conn):
    _load_all(pg_conn, now=T0)
    assert len(read_changed_items(pg_conn, since=T0 - timedelta(minutes=1))) == 11
    assert len(read_changed_items(pg_conn, since=T0 + timedelta(minutes=1))) == 0


def test_read_changed_items_excludes_not_ready(pg_conn):
    _load_all(pg_conn, now=T0)
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE plm_item SET ready_for_wrike = false WHERE plm_internal_id = '1'")
    items = read_changed_items(pg_conn, since=T0 - timedelta(minutes=1))
    assert len(items) == 10
    assert all(i["ready_for_wrike"] for i in items)


def test_resolve_eligible_families_one_per_family(pg_conn):
    _load_all(pg_conn, now=T0)
    fams = resolve_eligible_families(pg_conn, since=T0 - timedelta(minutes=1))
    assert len(fams) == 11
    assert len({f["family_id"] for f in fams}) == 11


def test_resolve_dedups_variants_to_canonical(pg_conn):
    _load_all(pg_conn, now=T0)
    # add a *CUSTOM sibling under the same family as WP-71511; canonical must stay *CORE
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO plm_item (plm_internal_id, item_number, family_id, prefix, code, "
            "customer, product_category, ready_for_wrike, created_at, modified_at) VALUES "
            "('1b','WP-71511-007-319','WP-71511','WP','71511','*CUSTOM','BAKING',true,%s,%s)",
            (T0, T0),
        )
    fams = resolve_eligible_families(pg_conn, since=T0 - timedelta(minutes=1))
    assert len(fams) == 11  # still one record per family
    wp = next(f for f in fams if f["family_id"] == "WP-71511")
    assert wp["customer"] == "*CORE"
