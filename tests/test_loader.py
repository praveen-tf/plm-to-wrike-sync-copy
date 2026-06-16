from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from loader import (
    _parse_centric_timestamp,
    refresh_plm_items,
    strip_html,
    style_to_plm_item,
)
from plm_reader import read_changed_items

T0 = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _row(**over):
    """A pre-resolved source row as returned by plm_source.read_ready_styles."""
    row = {
        "id": "S1",
        "item_number": "WP-71511-006-319",
        "item_name": "WINNIE-THE-POOH PANCAKE PAN",
        "mgf_print_method": '["MATTE", "FOIL"]',      # JSON array text
        "previous_item_number": "WP-68151",
        "contents": "<p>- WINNIE THE POOH PAN</p>",   # HTML, stripped by mapper
        "material_codes": "007251MET",
        "brand_category": "THOUGHTFULLY GOURMET",
        "customer": "*CORE",                           # pre-resolved via category2s JOIN
        "product_category": "BAKING",                 # pre-resolved via category1s JOIN
        "brand": "WINNIE-THE-POOH",                   # pre-resolved via collections JOIN
        "season": "2026 FALL/HOLIDAY",                # pre-resolved via seasons JOIN
        "design_request": "REF WP-68151",
        "mgf_image_link": "http://img/1.png",
        "mgf_ready_for_wrike": "true",                # text 'true'/'false', not bool
        "_modified_at": "2026-06-02T12:00:00Z",
    }
    row.update(over)
    return row


# --- style_to_plm_item field mapping ---------------------------------------

def test_maps_identity_and_pre_resolved_reference_fields():
    item = style_to_plm_item(_row(), now=T0)
    assert item["plm_internal_id"] == "S1"
    assert item["item_number"] == "WP-71511-006-319"
    assert item["family_id"] == "WP-71511"
    assert item["prefix"] == "WP"
    assert item["code"] == "71511"
    assert item["item_name"] == "WINNIE-THE-POOH PANCAKE PAN"
    assert item["title"] == "WP-71511 WINNIE-THE-POOH PANCAKE PAN"
    assert item["customer"] == "*CORE"
    assert item["product_category"] == "BAKING"
    assert item["brand"] == "WINNIE-THE-POOH"
    assert item["season"] == "2026 FALL/HOLIDAY"


def test_maps_scalar_fields():
    item = style_to_plm_item(_row(), now=T0)
    assert item["print_method"] == "MATTE, FOIL"           # JSON array text joined
    assert item["previous_item_no"] == "WP-68151"
    assert item["contents"] == "- WINNIE THE POOH PAN"     # HTML stripped
    assert item["material_codes"] == "007251MET"
    assert item["brand_category"] == "THOUGHTFULLY GOURMET"
    assert item["design_request"] == "REF WP-68151"
    assert item["image_link"] == "http://img/1.png"
    assert item["ready_for_wrike"] is True                 # 'true' string -> bool True


def test_ready_for_wrike_false_string_maps_to_false():
    item = style_to_plm_item(_row(mgf_ready_for_wrike="false"), now=T0)
    assert item["ready_for_wrike"] is False


def test_blanks_and_unsourced_native_fields():
    item = style_to_plm_item(_row(), now=T0)
    assert item["design_brief"] == ""
    assert item["status"] is None
    assert item["priority"] is None
    assert item["end_date"] is None


def test_season_blank_when_join_returns_null():
    item = style_to_plm_item(_row(season=None), now=T0)
    assert item["season"] == ""


def test_builds_description_from_material_codes_and_contents():
    item = style_to_plm_item(_row(), now=T0)
    assert item["description"] == (
        "<h5>Material Codes:     </h5>007251MET"
        "<br><h5>Contents:     </h5>- WINNIE THE POOH PAN")


def test_modified_and_created_at_from_timestamp():
    item = style_to_plm_item(_row(), now=T0)
    assert item["modified_at"] == T0
    assert item["created_at"] == item["modified_at"]


def test_modified_at_falls_back_to_now_when_missing():
    item = style_to_plm_item(_row(_modified_at=None), now=T0)
    assert item["modified_at"] == T0


def test_print_method_single_element():
    item = style_to_plm_item(_row(mgf_print_method='["MATTE & SPOT FOIL"]'), now=T0)
    assert item["print_method"] == "MATTE & SPOT FOIL"


def test_print_method_empty_produces_empty_string():
    item = style_to_plm_item(_row(mgf_print_method=None), now=T0)
    assert item["print_method"] == ""


# --- timestamp + html helpers ----------------------------------------------

def test_parse_timestamp_accepts_iso_z_and_centric_slash():
    assert _parse_centric_timestamp("2026-06-02T12:00:00Z") == T0
    assert _parse_centric_timestamp("2026/06/02T12:00:00") == T0


def test_parse_timestamp_blank_is_none():
    assert _parse_centric_timestamp("") is None
    assert _parse_centric_timestamp(None) is None


def test_parse_timestamp_unparseable_raises():
    with pytest.raises(ValueError):
        _parse_centric_timestamp("not-a-date")


def test_strip_html_removes_tags_and_unescapes():
    assert strip_html("<p>A &amp; B</p><br>C") == "A & B\nC"
    assert strip_html(None) == ""


# --- refresh_plm_items (DB-backed) -----------------------------------------

def test_refresh_upserts_rows(pg_conn):
    rows = [
        _row(id="S1", item_number="WP-71511-006-319"),
        _row(id="S2", item_number="WP-71512-006-319"),
    ]
    with patch("loader.read_ready_styles", return_value=rows):
        assert refresh_plm_items(pg_conn, None, since=EPOCH, now=T0) == 2
    upserted = read_changed_items(pg_conn, since=EPOCH)
    assert {r["plm_internal_id"] for r in upserted} == {"S1", "S2"}


def test_refresh_is_idempotent(pg_conn):
    rows = [_row()]
    with patch("loader.read_ready_styles", return_value=rows):
        refresh_plm_items(pg_conn, None, since=EPOCH, now=T0)
        refresh_plm_items(pg_conn, None, since=EPOCH, now=T0)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM plm_item")
        assert cur.fetchone()[0] == 1


def test_refresh_returns_zero_when_no_rows(pg_conn):
    with patch("loader.read_ready_styles", return_value=[]):
        assert refresh_plm_items(pg_conn, None, since=EPOCH, now=T0) == 0


def test_refresh_passes_source_conn_and_since_to_read_ready_styles(pg_conn):
    fake_src = object()
    with patch("loader.read_ready_styles", return_value=[]) as mock_read:
        refresh_plm_items(pg_conn, fake_src, since=T0, now=T0)
    mock_read.assert_called_once_with(fake_src, T0)
