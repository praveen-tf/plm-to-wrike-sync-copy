from datetime import datetime, timezone

from state import (
    get_task_map_entry,
    get_watermark,
    set_watermark,
    upsert_task_map,
)

NOW = datetime(2026, 6, 2, 17, 0, tzinfo=timezone.utc)
SNAP = {"material_codes": "M", "contents": "C", "title": "T", "brand_category": "B"}


def test_task_map_absent_then_roundtrips(pg_conn):
    assert get_task_map_entry(pg_conn, "WP-71511") is None
    upsert_task_map(
        pg_conn,
        family_id="WP-71511",
        plm_internal_id="1",
        wrike_task_id="TASK1",
        wrike_permalink="http://wrike/TASK1",
        snapshot=SNAP,
        now=NOW,
    )
    entry = get_task_map_entry(pg_conn, "WP-71511")
    assert entry["wrike_task_id"] == "TASK1"
    assert entry["snapshot"] == SNAP


def test_task_map_upsert_updates_snapshot(pg_conn):
    upsert_task_map(pg_conn, family_id="WP-71511", plm_internal_id="1",
                    wrike_task_id="TASK1", wrike_permalink="p", snapshot=SNAP, now=NOW)
    new_snap = {**SNAP, "material_codes": "M2"}
    upsert_task_map(pg_conn, family_id="WP-71511", plm_internal_id="1",
                    wrike_task_id="TASK1", wrike_permalink="p", snapshot=new_snap, now=NOW)
    assert get_task_map_entry(pg_conn, "WP-71511")["snapshot"]["material_codes"] == "M2"


def test_watermark_absent_then_roundtrips(pg_conn):
    assert get_watermark(pg_conn) is None
    set_watermark(pg_conn, NOW)
    assert get_watermark(pg_conn) == NOW
