from conftest import seed_folder_map
from fixtures_mapping import HTML_CORE
from mapping import (
    CUSTOM_FIELDS,
    build_create_payload,
    build_update_payload,
    customer_matches,
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
    assert "description" not in p  # description is section-merged separately, never PUT wholesale


def test_update_payload_custom_fields_are_update_eligible_only():
    cf = build_update_payload(_item())["customFields"]
    assert _fid("customer") not in cf  # create-only -> excluded on update
    assert _fid("brand_category") not in cf  # create-only (alert-on-change) -> excluded
    assert cf[_fid("material_codes")] == "007551CER DILLARD GLAZED MUG"  # update-eligible
    assert cf[_fid("season")] == "2026 FALL/HOLIDAY"


FOLDERS = {"WP": {"Winnie-the-Pooh": "fold_wp"}}   # {prefix -> {brand -> folder id}}
STAGING = {"*": {"": "fold_staging"}}
AUTHORS = {
    "CONFECTION": ("Jesse", "WRIKE_TOKEN_JESSE"),
    "*": ("Praveen", "WRIKE_TOKEN_PRAVEEN"),
}


def test_resolve_folder_single_folder_prefix_ignores_brand():
    # A prefix with one folder routes by prefix alone, regardless of the item's brand.
    assert resolve_folder("WP", "anything at all", {**FOLDERS, **STAGING}) == ("fold_wp", False)


def test_resolve_folder_brand_tiebreaks_a_shared_prefix():
    shared = {"MB": {"MR BEAST": "fold_beast", "MEAT BOARDS": "fold_meat"}, **STAGING}
    assert resolve_folder("MB", "MR BEAST", shared) == ("fold_beast", False)
    assert resolve_folder("MB", "MEAT BOARDS", shared) == ("fold_meat", False)


def test_resolve_folder_shared_prefix_no_brand_match_routes_to_staging():
    shared = {"MB": {"MR BEAST": "fold_beast", "MEAT BOARDS": "fold_meat"}, **STAGING}
    assert resolve_folder("MB", "SOMETHING ELSE", shared) == ("fold_staging", True)


def test_resolve_folder_unknown_prefix_routes_to_staging_row():
    assert resolve_folder("ZZ", "x", {**FOLDERS, **STAGING}) == ("fold_staging", True)


def test_resolve_folder_unknown_prefix_without_staging_row_is_none():
    assert resolve_folder("ZZ", "x", FOLDERS) == (None, True)


def test_resolve_author_maps_category_to_identity():
    assert resolve_author("CONFECTION", AUTHORS) == ("Jesse", "WRIKE_TOKEN_JESSE")


def test_resolve_author_falls_back_to_catch_all():
    # Any category without its own row resolves to the '*' row.
    assert resolve_author("BAKING", AUTHORS) == ("Praveen", "WRIKE_TOKEN_PRAVEEN")
    assert resolve_author("BRAND NEW CATEGORY", AUTHORS) == ("Praveen", "WRIKE_TOKEN_PRAVEEN")
    assert resolve_author("BAKING", {"CONFECTION": AUTHORS["CONFECTION"]}) is None


def test_load_folder_map_reads_the_db_table(pg_conn):
    seed_folder_map(pg_conn, {"WP": "4459532498", "LT": "fold_lt"})  # brand defaults to ''
    assert load_folder_map(pg_conn) == {"WP": {"": "4459532498"}, "LT": {"": "fold_lt"}}


def test_load_folder_map_nests_brands_sharing_a_prefix(pg_conn):
    with pg_conn.cursor() as cur:
        for brand, fid in (("MR BEAST", "fold_beast"), ("MEAT BOARDS", "fold_meat")):
            cur.execute(
                "INSERT INTO wrike_folder_map "
                "(prefix, wrike_folder_id, full_folder_name, brand, space_id) "
                "VALUES ('MB', %s, %s, %s, 'space-test')",
                (fid, f"MB - {brand}", brand))
    pg_conn.commit()
    assert load_folder_map(pg_conn) == {
        "MB": {"MR BEAST": "fold_beast", "MEAT BOARDS": "fold_meat"}}


def test_load_folder_map_empty_table_is_empty_map(pg_conn):
    assert load_folder_map(pg_conn) == {}


def test_customer_match_is_raw_exact():
    assert customer_matches("*CORE", "*CORE")
    assert customer_matches(None, "")          # Wrike stores every value as a string
    assert not customer_matches("*CORE", "*core")        # case-sensitive
    assert not customer_matches("*CORE", " *CORE")       # no trimming
    assert not customer_matches("*CORE", "*CORETJXCOMPANIES, HOMEGOODS")


def test_customer_match_fails_on_html_polluted_value():
    # Real MGF data quality: an HTML anchor pasted into the Customer field must
    # FAIL the raw match (and land in the review log), never silently match.
    assert not customer_matches("*CORE", HTML_CORE)


def test_author_map_loads_from_db(pg_conn):
    # The schema seed: CONFECTION -> Jesse plus the '*' catch-all -> Praveen.
    amap = load_category_author_map(pg_conn)
    assert amap["CONFECTION"] == ("Jesse", "WRIKE_TOKEN_JESSE")
    assert amap["*"] == ("Praveen", "WRIKE_TOKEN_PRAVEEN")
