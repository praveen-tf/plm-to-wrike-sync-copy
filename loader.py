"""Transform input rows (Excel `Wrike` sheet / synthetic CSV) into `plm_item` records."""
from __future__ import annotations

import csv
import re
from pathlib import Path


def _read_excel_wrike_sheet(path) -> list[dict]:
    import warnings

    import openpyxl

    with warnings.catch_warnings():
        # openpyxl warns about an unsupported data-validation extension in this file; benign.
        warnings.filterwarnings("ignore", message="Data Validation extension is not supported")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb["Wrike"]
        rows = ws.iter_rows(values_only=True)
        header = ["" if h is None else str(h) for h in next(rows)]
        out = []
        for r in rows:
            if all(c is None or str(c).strip() == "" for c in r):
                continue  # skip blank rows
            out.append({header[i]: ("" if c is None else str(c)) for i, c in enumerate(r)})
    return out


def _read_csv(path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_source_rows(excel_path, csv_path=None) -> list[dict]:
    """Return the union of the Excel `Wrike` sheet and (optional) synthetic CSV rows."""
    rows = _read_excel_wrike_sheet(excel_path)
    if csv_path and Path(csv_path).exists():
        rows += _read_csv(csv_path)
    return rows

# Source column header -> plm_item field name.
COLUMN_MAP = {
    "Key": "plm_internal_id",
    "PLM - Item #": "item_number",
    "PLM Item Name": "item_name",
    "Title": "title",
    "Folder": "folder",
    "Workflow": "workflow",
    "Status": "status",
    "Custom status": "custom_status",
    "Priority": "priority",
    "End Date": "end_date",
    "Description": "description",
    "Print Method": "print_method",
    "Previous Item No": "previous_item_no",
    "PLM - Contents": "contents",
    "PLM - Material Codes": "material_codes",
    "Brand Category": "brand_category",
    "Customer": "customer",
    "Product Category": "product_category",
    "Brand": "brand",
    "Season": "season",
    "PLM - Design Request": "design_request",
    "Design Brief / Additional Notes": "design_brief",
    "Image Link": "image_link",
}


PLM_ITEM_COLUMNS = [
    "plm_internal_id", "item_number", "family_id", "prefix", "code", "item_name",
    "customer", "title", "folder", "workflow", "status", "custom_status", "priority",
    "end_date", "description", "print_method", "previous_item_no", "contents",
    "material_codes", "brand_category", "product_category", "brand", "season",
    "design_request", "design_brief", "image_link", "ready_for_wrike",
    "created_at", "modified_at",
]


def load_plm_items(conn, items: list[dict]) -> None:
    """Upsert plm_item rows (keyed on plm_internal_id). Idempotent."""
    cols = ", ".join(PLM_ITEM_COLUMNS)
    placeholders = ", ".join(f"%({c})s" for c in PLM_ITEM_COLUMNS)
    updates = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in PLM_ITEM_COLUMNS if c != "plm_internal_id"
    )
    sql = (
        f"INSERT INTO plm_item ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT (plm_internal_id) DO UPDATE SET {updates}"
    )
    with conn.cursor() as cur:
        cur.executemany(sql, items)
    conn.commit()


def to_plm_item(row: dict, *, now) -> dict:
    item = {field: row.get(col) for col, field in COLUMN_MAP.items()}

    item_number = item["item_number"]
    prefix_match = re.match(r"^[A-Za-z]+", item_number)
    parts = item_number.split("-")
    item["family_id"] = item_number[:8]
    item["prefix"] = prefix_match.group(0) if prefix_match else ""
    item["code"] = parts[1] if len(parts) > 1 else ""

    # synthesized control columns (absent from the source Excel):
    item["ready_for_wrike"] = True
    item["created_at"] = now
    item["modified_at"] = now
    return item
