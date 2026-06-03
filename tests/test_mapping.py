from mapping import (
    CUSTOM_FIELDS,
    build_create_payload,
    build_update_payload,
    load_category_author_map,
    load_folder_map,
    resolve_author,
    resolve_folder,
)


def _item(**over):
    base = {
        "title": "WP-71511 WINNIE-THE-POOH PANCAKE PAN",
        "status": "Active",
        "priority": "Normal",
        "end_date": "",
        "description": "<h5>Material Codes:</h5>007551CER",
        "item_number": "WP-71511-006-319",
        "item_name": "WINNIE-THE-POOH PANCAKE PAN",
        "print_method": "MATTE",
        "previous_item_no": "WP-68151",
        "contents": "- WINNIE THE POOH PAN",
        "material_codes": "007551CER DILLARD GLAZED MUG",
        "brand_category": "THOUGHTFULLY GOURMET",
        "customer": "*CORE",
        "product_category": "BAKING",
        "brand": "WINNIE-THE-POOH",
        "season": "2026 FALL/HOLIDAY",
        "design_request": "REF WP-68151 ARTWORK REFRESH",
        "design_brief": "",
        "image_link": "",
        "prefix": "WP",
    }
    base.update(over)
    return base


def _fid(name):
    return CUSTOM_FIELDS[name][0]


def test_create_payload_has_core_fields():
    p = build_create_payload(_item())
    assert p["title"] == "WP-71511 WINNIE-THE-POOH PANCAKE PAN"
    assert p["status"] == "Active"
    assert p["importance"] == "Normal"
    assert p["description"].startswith("<h5>")


def test_create_payload_stamps_custom_item_type_when_given():
    p = build_create_payload(_item(), item_type_id="ITYPE_RETAIL")
    assert p["customItemTypeId"] == "ITYPE_RETAIL"  # PRD: new items -> Item Type = Retail Item


def test_create_payload_omits_item_type_when_not_configured():
    p = build_create_payload(_item())  # no id configured -> key absent (don't send empty)
    assert "customItemTypeId" not in p


def test_update_payload_never_sets_item_type():
    # Item Type is create-only; updates must not carry customItemTypeId.
    assert "customItemTypeId" not in build_update_payload(_item())


def test_create_payload_includes_create_only_and_update_custom_fields():
    cf = build_create_payload(_item())["customFields"]
    assert cf[_fid("customer")] == "*CORE"  # create-only field present on create
    assert cf[_fid("material_codes")] == "007551CER DILLARD GLAZED MUG"  # update field too


def test_update_payload_omits_create_only_and_top_level_fields():
    p = build_update_payload(_item())
    assert "title" not in p
    assert "status" not in p
    assert "importance" not in p
    assert "description" not in p  # description handled by section-merge in Phase 3


def test_update_payload_custom_fields_are_update_eligible_only():
    cf = build_update_payload(_item())["customFields"]
    assert _fid("customer") not in cf  # create-only -> excluded on update
    assert _fid("brand_category") not in cf  # create-only (alert-on-change) -> excluded
    assert cf[_fid("material_codes")] == "007551CER DILLARD GLAZED MUG"  # update-eligible
    assert cf[_fid("season")] == "2026 FALL/HOLIDAY"


FOLDERS = {"WP": "fold_wp", "*": "fold_fallback"}
AUTHORS = {
    "BAKING": ("Praveen", "WRIKE_TOKEN_PRAVEEN"),
    "CONFECTION": ("Jesse", "WRIKE_TOKEN_JESSE"),
}


def test_resolve_folder_known_prefix():
    assert resolve_folder("WP", FOLDERS) == ("fold_wp", False)


def test_resolve_folder_unknown_prefix_uses_fallback_and_flags():
    assert resolve_folder("ZZ", FOLDERS) == ("fold_fallback", True)


def test_resolve_folder_unknown_prefix_without_fallback_is_none():
    assert resolve_folder("ZZ", {"WP": "fold_wp"}) == (None, True)


def test_resolve_author_maps_category_to_identity():
    assert resolve_author("BAKING", AUTHORS) == ("Praveen", "WRIKE_TOKEN_PRAVEEN")
    assert resolve_author("CONFECTION", AUTHORS) == ("Jesse", "WRIKE_TOKEN_JESSE")


def test_load_folder_map_reads_prefix_env_vars(monkeypatch):
    monkeypatch.setenv("WRIKE_FOLDER_WP", "4459532498")  # numeric permalink id is fine
    monkeypatch.setenv("WRIKE_FOLDER_LT", "fold_lt")
    fmap = load_folder_map()
    assert fmap["WP"] == "4459532498"
    assert fmap["LT"] == "fold_lt"


def test_load_folder_map_uppercases_keys_and_skips_blanks(monkeypatch):
    monkeypatch.setenv("WRIKE_FOLDER_aw", "fold_aw")  # lower-case suffix -> upper key
    monkeypatch.setenv("WRIKE_FOLDER_ZZ", "   ")       # blank -> not a mapping
    fmap = load_folder_map()
    assert fmap["AW"] == "fold_aw"
    assert "ZZ" not in fmap


def test_author_map_loads_from_db(pg_conn):
    amap = load_category_author_map(pg_conn)
    assert amap["BAKING"] == ("Praveen", "WRIKE_TOKEN_PRAVEEN")
    assert amap["CONFECTION"] == ("Jesse", "WRIKE_TOKEN_JESSE")
