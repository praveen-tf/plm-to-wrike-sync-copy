"""Load `plm_item` records into Postgres from the Postgres PLM source database.

`style_to_plm_item` maps one pre-resolved source row (from plm_source.read_ready_styles)
to a plm_item record; `refresh_plm_items` reads the ready-for-Wrike delta from the source
Postgres and upserts it via `load_plm_items`.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone

from description import build_description
from plm_source import read_ready_styles
from state import EPOCH

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


def item_prefix(item_number: str) -> str:
    """Leading alpha prefix of an item number, e.g. 'WP-71511-006-319' -> 'WP'."""
    match = re.match(r"^[A-Za-z]+", item_number)
    return match.group(0) if match else ""


def strip_html(text: str | None) -> str:
    """Plain text from an HTML field: drop tags, unescape entities, trim blank lines."""
    if not text:
        return ""
    text = re.sub(r"</(p|div)>|<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _parse_centric_timestamp(value) -> datetime | None:
    """Parse a Centric `_modified_at` value to an aware UTC datetime; None if absent.

    The inbound `_modified_at` is ISO 8601 UTC with a 'Z' (e.g. 2026-06-12T11:22:48.795Z);
    the slash-format parsers are kept as tolerant fallbacks. A present-but-unparseable value
    raises loudly rather than silently defaulting.
    """
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    parsers = (
        datetime.fromisoformat,
        lambda t: datetime.strptime(t, "%Y/%m/%dT%H:%M:%S"),
        lambda t: datetime.strptime(t, "%Y/%m/%d %H:%M:%S"),
    )
    for parse in parsers:
        try:
            dt = parse(text)
        except ValueError:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    raise ValueError(f"unrecognized Centric _modified_at timestamp: {value!r}")


def style_to_plm_item(row: dict, *, now: datetime) -> dict:
    """Map one pre-resolved source row (from plm_source.read_ready_styles) to a plm_item
    record. Reference fields (customer, product_category, brand, season) arrive already
    resolved via SQL JOINs. mgf_print_method is a JSON array text that is parsed and joined.
    """
    item_number = row.get("item_number") or ""
    parts = item_number.split("-")
    material_codes = row.get("material_codes") or ""
    contents = strip_html(row.get("contents"))
    item_name = row.get("item_name") or ""
    modified_at = _parse_centric_timestamp(row.get("_modified_at")) or now

    print_method_raw = row.get("mgf_print_method") or ""
    try:
        print_method = ", ".join(json.loads(print_method_raw)) if print_method_raw else ""
    except (json.JSONDecodeError, TypeError):
        print_method = str(print_method_raw)

    return {
        "plm_internal_id": row["id"],
        "item_number": item_number,
        "family_id": item_number[:8],
        "prefix": item_prefix(item_number),
        "code": parts[1] if len(parts) > 1 else "",
        "item_name": item_name,
        "customer": row.get("customer") or "",
        "title": f"{item_number[:8]} {item_name}".strip(),
        "folder": None,
        "workflow": None,
        "status": None,
        "custom_status": None,
        "priority": None,
        "end_date": None,
        "description": build_description(material_codes, contents),
        "print_method": print_method,
        "previous_item_no": row.get("previous_item_number") or "",
        "contents": contents,
        "material_codes": material_codes,
        "brand_category": row.get("brand_category") or "",
        "product_category": row.get("product_category") or "",
        "brand": row.get("brand") or "",
        "season": row.get("season") or "",
        "design_request": row.get("design_request") or "",
        "design_brief": "",
        "image_link": row.get("mgf_image_link") or "",
        "ready_for_wrike": row.get("mgf_ready_for_wrike") == "true",
        "created_at": modified_at,
        "modified_at": modified_at,
    }


def refresh_plm_items(conn, source_conn, *, since: datetime, now: datetime) -> int:
    """Read ready-for-Wrike styles changed since `since` from the source Postgres, map
    them, and upsert into plm_item (the local mirror). Returns the number of rows upserted.

    At EPOCH (first run) the full ready set is fetched; on later runs only the delta
    since `since`. See specs/004 for the source seam design.
    """
    rows = read_ready_styles(source_conn, since)
    items = [style_to_plm_item(row, now=now) for row in rows]
    if items:
        load_plm_items(conn, items)
    return len(items)
