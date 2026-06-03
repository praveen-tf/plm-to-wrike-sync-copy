from datetime import datetime, timezone
from pathlib import Path

from loader import read_source_rows, to_plm_item

NOW = datetime(2026, 6, 2, tzinfo=timezone.utc)
DATA = Path(__file__).resolve().parent.parent / "data" / "input"


def _row(**over):
    """A representative input row keyed by the Excel/CSV column headers."""
    base = {
        "Key": "1",
        "Folder": "",
        "Title": "WP-71511 WINNIE-THE-POOH PANCAKE PAN",
        "Workflow": "",
        "Status": "Active",
        "Custom status": "",
        "Priority": "Normal",
        "End Date": "",
        "Description": "<h5>Design Brief:     </h5>REF WP-68151",
        "PLM - Item #": "WP-71511-006-319",
        "PLM Item Name": "WINNIE-THE-POOH PANCAKE PAN",
        "Print Method": "MATTE",
        "Previous Item No": "WP-68151",
        "PLM - Contents": "- WINNIE THE POOH PAN",
        "PLM - Material Codes": "WINNIE-THE-POOH PANCAKE PAN (007551CER)",
        "Brand Category": "THOUGHTFULLY GOURMET",
        "Customer": "*CORE",
        "Product Category": "BAKING",
        "Brand": "WINNIE-THE-POOH",
        "Season": "2026 FALL/HOLIDAY",
        "PLM - Design Request": "REF WP-68151 ARTWORK REFRESH",
        "Design Brief / Additional Notes": "",
        "Image Link": "",
    }
    base.update(over)
    return base


def test_family_id_is_first_8_chars_of_item_number():
    item = to_plm_item(_row(), now=NOW)
    assert item["family_id"] == "WP-71511"


def test_prefix_and_code_parsed_from_item_number():
    item = to_plm_item(_row(), now=NOW)
    assert item["prefix"] == "WP"
    assert item["code"] == "71511"


def test_control_columns_are_synthesized():
    # The source Excel has no Ready-for-Wrike / timestamps; the loader adds them.
    item = to_plm_item(_row(), now=NOW)
    assert item["ready_for_wrike"] is True
    assert item["created_at"] == NOW
    assert item["modified_at"] == NOW


def test_source_columns_map_to_item_fields():
    item = to_plm_item(_row(), now=NOW)
    assert item["plm_internal_id"] == "1"
    assert item["item_name"] == "WINNIE-THE-POOH PANCAKE PAN"
    assert item["customer"] == "*CORE"
    assert item["product_category"] == "BAKING"
    assert item["title"] == "WP-71511 WINNIE-THE-POOH PANCAKE PAN"
    assert item["status"] == "Active"
    assert item["priority"] == "Normal"
    assert item["material_codes"] == "WINNIE-THE-POOH PANCAKE PAN (007551CER)"
    assert item["contents"] == "- WINNIE THE POOH PAN"
    assert item["brand_category"] == "THOUGHTFULLY GOURMET"
    assert item["brand"] == "WINNIE-THE-POOH"
    assert item["season"] == "2026 FALL/HOLIDAY"
    assert item["print_method"] == "MATTE"
    assert item["previous_item_no"] == "WP-68151"
    assert item["design_request"] == "REF WP-68151 ARTWORK REFRESH"
    assert item["description"] == "<h5>Design Brief:     </h5>REF WP-68151"


def test_read_source_rows_unions_excel_and_synthetic_csv():
    rows = read_source_rows(
        DATA / "winnie_the_pooh_input.xlsx",
        DATA / "synthetic_jesse_confection.csv",
    )
    item_numbers = {r["PLM - Item #"] for r in rows}
    assert len(rows) == 11  # 6 real WP (Excel) + 5 synthetic LT (CSV)
    assert "WP-71511-006-319" in item_numbers  # from the Excel Wrike sheet
    assert "LT-68102-006-319" in item_numbers  # from the synthetic Jesse/Lindt CSV
