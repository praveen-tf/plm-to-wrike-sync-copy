from datetime import datetime, timezone

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

# Stub resolver: tests pass the display value as the reference "id" so it round-trips.
_REFS = {
    ("category2s", "cat2"): "*CORE",
    ("category1s", "cat1"): "BAKING",
    ("collections", "coll"): "WINNIE-THE-POOH",
    ("seasons", "season1"): "2026 FALL/HOLIDAY",
}


def _resolve(endpoint, ref_id):
    return _REFS.get((endpoint, ref_id), "")


def _style(**over):
    style = {
        "id": "S1",
        "active": True,
        "mgf_item_identifier": "WP-71511-006-319",
        "node_name": "WINNIE-THE-POOH PANCAKE PAN",
        "category_2": "cat2",          # -> customer *CORE
        "category_1": "cat1",          # -> product_category BAKING
        "collection": "coll",          # -> brand
        "parent_season": "season1",    # -> season
        "mgf_print_method": ["MATTE", "FOIL"],
        "mgf_previous_item_number": "WP-68151",
        "mgf_test_material": "<p>- WINNIE THE POOH PAN</p>",
        "mgf_material_code_item": "007251MET",
        "mgf_brand_category_2": "THOUGHTFULLY GOURMET",
        "mgf_item_description": "REF WP-68151",
        "mgf_image_link": "http://img/1.png",
        "mgf_ready_for_wrike": True,
        "_modified_at": "2026-06-02T12:00:00Z",
    }
    style.update(over)
    return style


# --- style_to_plm_item field mapping ---------------------------------------

def test_maps_identity_and_resolved_reference_fields():
    item = style_to_plm_item(_style(), _resolve, now=T0)
    assert item["plm_internal_id"] == "S1"
    assert item["item_number"] == "WP-71511-006-319"
    assert item["family_id"] == "WP-71511"
    assert item["prefix"] == "WP"
    assert item["code"] == "71511"
    assert item["item_name"] == "WINNIE-THE-POOH PANCAKE PAN"
    assert item["title"] == "WP-71511 WINNIE-THE-POOH PANCAKE PAN"
    assert item["customer"] == "*CORE"               # resolved category_2
    assert item["product_category"] == "BAKING"      # resolved category_1
    assert item["brand"] == "WINNIE-THE-POOH"        # resolved collection
    assert item["season"] == "2026 FALL/HOLIDAY"     # resolved parent_season


def test_maps_scalar_and_list_fields():
    item = style_to_plm_item(_style(), _resolve, now=T0)
    assert item["print_method"] == "MATTE, FOIL"     # list joined
    assert item["previous_item_no"] == "WP-68151"
    assert item["contents"] == "- WINNIE THE POOH PAN"   # HTML stripped
    assert item["material_codes"] == "007251MET"
    assert item["brand_category"] == "THOUGHTFULLY GOURMET"
    assert item["design_request"] == "REF WP-68151"
    assert item["image_link"] == "http://img/1.png"
    assert item["ready_for_wrike"] is True


def test_blanks_and_unsourced_native_fields():
    item = style_to_plm_item(_style(), _resolve, now=T0)
    assert item["design_brief"] == ""
    assert item["status"] is None
    assert item["priority"] is None
    assert item["end_date"] is None


def test_season_blank_when_parent_season_missing():
    item = style_to_plm_item(_style(parent_season=None), _resolve, now=T0)
    assert item["season"] == ""


def test_builds_description_from_material_codes_and_contents():
    item = style_to_plm_item(_style(), _resolve, now=T0)
    assert item["description"] == (
        "<h5>Material Codes:     </h5>007251MET"
        "<br><h5>Contents:     </h5>- WINNIE THE POOH PAN")


def test_modified_and_created_at_from_centric_timestamp():
    item = style_to_plm_item(_style(), _resolve, now=T0)
    assert item["modified_at"] == T0
    assert item["created_at"] == item["modified_at"]  # API has no creation date


def test_modified_at_falls_back_to_now_when_missing():
    item = style_to_plm_item(_style(_modified_at=None), _resolve, now=T0)
    assert item["modified_at"] == T0


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

class FakeCentric:
    def __init__(self, styles):
        self._styles = styles
        self.modified_after = "UNSET"

    def list_styles(self, *, modified_after=None, **filters):
        self.modified_after = modified_after
        self.filters = filters
        return self._styles

    def resolve_ref(self, endpoint, ref_id):
        return _resolve(endpoint, ref_id)


def test_refresh_upserts_active_styles(pg_conn):
    client = FakeCentric([
        _style(id="S1", mgf_item_identifier="WP-71511-006-319"),
        _style(id="S2", mgf_item_identifier="WP-71512-006-319"),
    ])
    assert refresh_plm_items(pg_conn, client, since=EPOCH, now=T0) == 2
    rows = read_changed_items(pg_conn, since=EPOCH)
    assert {r["plm_internal_id"] for r in rows} == {"S1", "S2"}


def test_refresh_full_pull_omits_modified_after_at_epoch(pg_conn):
    client = FakeCentric([_style()])
    refresh_plm_items(pg_conn, client, since=EPOCH, now=T0)
    assert client.modified_after is None


def test_refresh_passes_formatted_modified_after_for_delta(pg_conn):
    client = FakeCentric([_style()])
    refresh_plm_items(pg_conn, client, since=T0, now=T0)
    assert client.modified_after == "2026-06-02T12:00:00Z"  # ISO 8601 UTC, Centric's only accepted form


def test_refresh_skips_inactive_styles(pg_conn):
    client = FakeCentric([
        _style(id="S1"),
        _style(id="S2", active=False, mgf_item_identifier="WP-99999-006-319"),
    ])
    assert refresh_plm_items(pg_conn, client, since=EPOCH, now=T0) == 1
    rows = read_changed_items(pg_conn, since=EPOCH)
    assert {r["plm_internal_id"] for r in rows} == {"S1"}


def test_refresh_requests_only_ready_styles(pg_conn):
    # The producer's refresh fetches the ready-for-Wrike set server-side (the fast path),
    # so the mirror is ready-only.
    client = FakeCentric([_style()])
    refresh_plm_items(pg_conn, client, since=EPOCH, now=T0)
    assert client.filters == {"mgf_ready_for_wrike": "true"}


def test_refresh_is_idempotent(pg_conn):
    client = FakeCentric([_style()])
    refresh_plm_items(pg_conn, client, since=EPOCH, now=T0)
    refresh_plm_items(pg_conn, client, since=EPOCH, now=T0)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM plm_item")
        assert cur.fetchone()[0] == 1
