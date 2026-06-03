"""Map a plm_item record to Wrike payloads, enforcing the PRD create/update matrix.

`build_create_payload` sends the full field set; `build_update_payload` sends only
update-eligible fields (create-only fields are set once at creation). The description
is intentionally absent from the update payload — on update it is section-merged
separately (Phase 3), never overwritten wholesale.
"""
from __future__ import annotations

import os

# Wrike account-level custom-field id -> (plm_item field, behavior).
# behavior: "create" = set once at creation only; "update" = refreshed every sync.
CUSTOM_FIELDS = {
    "item_number":      ("IEAF5GXSJUAE4ZBN", "update"),   # PLM - Item #
    "item_name":        ("IEAF5GXSJUAE4ZBO", "update"),   # PLM Item Name
    "print_method":     ("IEAF5GXSJUAE4YZ5", "update"),
    "previous_item_no": ("IEAF5GXSJUAE4YZ6", "update"),
    "contents":         ("IEAF5GXSJUAE4ZBR", "update"),   # PLM - Contents
    "material_codes":   ("IEAF5GXSJUAE4ZBS", "update"),   # PLM - Material Codes
    "brand_category":   ("IEAF5GXSJUAE7BMW", "create"),
    "customer":         ("IEAF5GXSJUAE7WMT", "create"),
    "product_category": ("IEAF5GXSJUAE7WMA", "update"),
    "brand":            ("IEAF5GXSJUAE7WMB", "update"),
    "season":           ("IEAF5GXSJUAFBI7Y", "update"),
    "design_request":   ("IEAF5GXSJUAFFTQG", "create"),
    "design_brief":     ("IEAF5GXSJUAE7HLC", "create"),
    "image_link":       ("IEAF5GXSJUAE6YFF", "create"),
}


def _custom_fields(item: dict, behaviors: set[str]) -> dict:
    out = {}
    for field, (fid, behavior) in CUSTOM_FIELDS.items():
        if behavior in behaviors:
            out[fid] = item.get(field, "")
    return out


def _normalize(value) -> str:
    """Wrike stores every custom-field value as a string; normalize for comparison
    (None / missing == empty string), matching WrikeClient._to_body's serialization."""
    return "" if value is None else str(value)


def build_create_payload(item: dict, item_type_id: str | None = None) -> dict:
    """Full field set for creating a new Wrike card.

    `item_type_id` is the Wrike Custom Item Type to stamp on new cards (PRD: every
    new item is created with Item Type = "Retail Item"). Create-only: it is set once
    at creation and never sent on update, so the card keeps its type thereafter.
    """
    payload = {
        "title": item["title"],
        "status": item.get("status"),
        "importance": item.get("priority"),
        "description": item.get("description"),
        "customFields": _custom_fields(item, {"create", "update"}),
    }
    if item_type_id:
        payload["customItemTypeId"] = item_type_id
    if item.get("end_date"):
        payload["dates"] = {"due": item["end_date"]}
    return payload


def build_update_payload(item: dict, current_fields: dict | None = None) -> dict:
    """Update-eligible custom fields only, filtered to those that actually changed.

    No title/status/priority/dates; the description is section-merged separately, so
    it is not included here.

    `current_fields` maps Wrike custom-field id -> the value currently on the live
    card. When provided, every update-eligible field whose incoming value already
    matches the card is dropped, so the PUT carries only genuine changes (no redundant
    writes / version-history noise). When None, all update-eligible fields are sent
    (prior behavior).
    """
    desired = _custom_fields(item, {"update"})
    if current_fields is None:
        return {"customFields": desired}
    changed = {
        fid: val for fid, val in desired.items()
        if _normalize(val) != _normalize(current_fields.get(fid))
    }
    return {"customFields": changed}


def resolve_folder(prefix: str, folder_map: dict) -> tuple[str | None, bool]:
    """Return (wrike_folder_id, is_fallback). Unknown prefix -> the '*' fallback folder,
    or (None, True) if no fallback is configured (caller dead-letters/flags it)."""
    if prefix in folder_map:
        return folder_map[prefix], False
    return folder_map.get("*"), True


def resolve_author(product_category: str, author_map: dict):
    """Return (author, token_ref) for a product category, or None if unmapped."""
    return author_map.get(product_category)


_FOLDER_ENV_PREFIX = "WRIKE_FOLDER_"


def load_folder_map() -> dict[str, str]:
    """Build {prefix -> Wrike folder id} from WRIKE_FOLDER_* environment variables.

    Each variable names a family prefix after the WRIKE_FOLDER_ stem, e.g.
    ``WRIKE_FOLDER_WP=4459532498``. Keys are upper-cased to match item prefixes.
    Folder ids may be numeric permalink ids; the Wrike client resolves those to v4
    ids on use. Returns {} when none are set, so an unmapped prefix defers (see
    resolve_folder) rather than routing anywhere by default.
    """
    folder_map: dict[str, str] = {}
    for key, value in os.environ.items():
        if not key.startswith(_FOLDER_ENV_PREFIX):
            continue
        folder_id = value.strip()
        if folder_id:
            folder_map[key[len(_FOLDER_ENV_PREFIX):].upper()] = folder_id
    return folder_map


def load_category_author_map(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT product_category, author, token_ref FROM category_author_map")
        return {cat: (author, ref) for cat, author, ref in cur.fetchall()}
