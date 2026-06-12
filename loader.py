"""Load `plm_item` records into Postgres from the Centric 8 PLM API.

`style_to_plm_item` maps one Centric style to a plm_item record; `refresh_plm_items` pulls
the ready-for-Wrike delta from Centric and upserts it via `load_plm_items`.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from description import build_description
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
    """Plain text from a Centric HTML field: drop tags, unescape entities, trim blank
    lines (e.g. mgf_test_material -> contents)."""
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


def _format_centric_timestamp(dt: datetime) -> str:
    """A datetime as Centric's `modified_after` query string: ISO 8601 UTC with a 'Z'
    suffix, e.g. 2026-06-12T10:23:36Z. Verified on the sandbox - this is the ONLY accepted
    form; no-Z, space-separated, slash-dated, and date-only variants all return 400.
    Truncates to whole seconds (floor), so the delta never rounds past a changed item.
    """
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def style_to_plm_item(style: dict, resolve, *, now: datetime) -> dict:
    """Map one Centric style (raw API dict) to a plm_item record - see specs/002 §3.

    `resolve(endpoint, ref_id)` turns a reference id into its display name
    (CentricClient.resolve_ref); `now` is the modified_at fallback when a style carries no
    `_modified_at`. created_at reuses modified_at - the API exposes no creation date.
    """
    item_number = style.get("mgf_item_identifier") or ""
    parts = item_number.split("-")
    material_codes = style.get("mgf_material_code_item") or ""
    contents = strip_html(style.get("mgf_test_material"))
    item_name = style.get("node_name") or ""
    modified_at = _parse_centric_timestamp(style.get("_modified_at")) or now

    return {
        "plm_internal_id": style["id"],
        "item_number": item_number,
        "family_id": item_number[:8],
        "prefix": item_prefix(item_number),
        "code": parts[1] if len(parts) > 1 else "",
        "item_name": item_name,
        "customer": resolve("category2s", style.get("category_2")),          # *CORE / *CUSTOM
        "title": f"{item_number[:8]} {item_name}".strip(),
        "folder": None,
        "workflow": None,
        "status": None,
        "custom_status": None,
        "priority": None,
        "end_date": None,
        "description": build_description(material_codes, contents),
        "print_method": ", ".join(style.get("mgf_print_method") or []),
        "previous_item_no": style.get("mgf_previous_item_number") or "",
        "contents": contents,
        "material_codes": material_codes,
        "brand_category": style.get("mgf_brand_category_2") or "",
        "product_category": resolve("category1s", style.get("category_1")),  # drives author map
        "brand": resolve("collections", style.get("collection")),
        "season": resolve("seasons", style.get("parent_season")),
        "design_request": style.get("mgf_item_description") or "",
        "design_brief": "",
        "image_link": style.get("mgf_image_link") or "",
        "ready_for_wrike": bool(style.get("mgf_ready_for_wrike")),
        "created_at": modified_at,
        "modified_at": modified_at,
    }


def refresh_plm_items(conn, client, *, since: datetime, now: datetime) -> int:
    """Pull ready-for-Wrike styles changed since `since` from Centric, map them, and
    upsert into plm_item (the local mirror). Returns the number of rows upserted.

    Fetches only mgf_ready_for_wrike=true (the sync targets) - the fast path; `active` is
    re-checked client-side. `since` at/<= EPOCH (the first run) pulls the full ready set;
    later runs pull only the modified_after delta. The mirror is therefore ready-only;
    reconciliation reads it and may over-report not-ready cards as unmapped (accepted -
    see specs/002).
    """
    modified_after = None if since <= EPOCH else _format_centric_timestamp(since)
    styles = client.list_styles(modified_after=modified_after, mgf_ready_for_wrike="true")
    items = [style_to_plm_item(s, client.resolve_ref, now=now)
             for s in styles if s.get("active")]
    if items:
        load_plm_items(conn, items)
    return len(items)
