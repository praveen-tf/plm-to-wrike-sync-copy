from datetime import datetime, timezone

from changes import (
    detect_alerts,
    detect_section_changes,
    format_alert_comment,
    format_change_comment,
)

SNAP = {
    "material_codes": "OLD MAT",
    "contents": "OLD CON",
    "title": "T1",
    "brand_category": "BC1",
}
NOW = datetime(2026, 6, 2, 17, 30, tzinfo=timezone.utc)


def test_detect_section_changes_returns_only_changed_sections():
    item = {"material_codes": "NEW MAT", "contents": "OLD CON"}
    assert detect_section_changes(SNAP, item) == {"material_codes": ("OLD MAT", "NEW MAT")}


def test_detect_section_changes_empty_when_unchanged():
    item = {"material_codes": "OLD MAT", "contents": "OLD CON"}
    assert detect_section_changes(SNAP, item) == {}


def test_detect_alerts_returns_changed_do_not_update_fields():
    item = {"title": "T2", "brand_category": "BC1"}
    assert detect_alerts(SNAP, item) == {"title": ("T1", "T2")}


def test_format_change_comment_has_label_old_new_and_timestamp():
    comment = format_change_comment({"material_codes": ("OLD MAT", "NEW MAT")}, NOW)
    assert "Material Codes" in comment
    assert "OLD MAT" in comment and "NEW MAT" in comment
    assert "6/2/2026 10:30 PT" in comment  # 17:30 UTC -> 10:30 PDT


def test_format_alert_comment_flags_field_change():
    comment = format_alert_comment({"title": ("T1", "T2")}, NOW)
    assert "Title" in comment
    assert "T1" in comment and "T2" in comment
    assert "6/2/2026 10:30 PT" in comment  # 17:30 UTC -> 10:30 PDT
