"""Orchestrator: reader -> canonical resolver -> per-family create/update in Wrike.

One-way PLM -> Wrike. Create = POST under the family's author token + store the task
id and snapshot. Update = PUT only the update-eligible fields whose incoming value
differs from the live Wrike card (no redundant writes), section-merge the description
for changed sections, comment on Material Codes/Contents changes, and alert (comment,
no write) on Title / Brand Category changes. A card with nothing changed is left
untouched (status "unchanged"). Watermark advances to the max processed.
"""
from __future__ import annotations

import os

from changes import (
    SNAPSHOT_FIELDS,
    detect_alerts,
    detect_section_changes,
    format_alert_comment,
    format_change_comment,
)
from description import merge_description
from mapping import (
    build_create_payload,
    build_update_payload,
    load_category_author_map,
    load_folder_map,
    resolve_author,
    resolve_folder,
)
from plm_reader import sorted_eligible_families
from state import (
    EPOCH,
    dead_letter,
    get_task_map_entry,
    get_watermark,
    record_sync_result,
    set_watermark,
    upsert_task_map,
)


class OutOfScopeError(Exception):
    """A mapped/adopted card lives outside the configured WRIKE_FOLDER_* folders.

    Raised before any write so the card is dead-lettered, never updated. Guards
    against the local map (or the account-wide search) pointing the sync at a card
    created by an earlier run in another space, e.g. a real production card.
    """


def _snapshot(item: dict) -> dict:
    return {f: item.get(f) for f in SNAPSHOT_FIELDS}


def _current_custom_fields(task: dict) -> dict:
    """Map custom-field id -> current value from a live Wrike task. Wrike omits unset
    fields from the list, so a field absent here compares as empty (see _normalize)."""
    return {cf["id"]: cf.get("value") for cf in task.get("customFields") or []}


def _configured_folder_ids(client, folder_map: dict) -> set[str]:
    """The WRIKE_FOLDER_* folder ids resolved to Wrike v4 ids (numeric permalinks too)."""
    return {client.resolve_folder_id(fid) for fid in folder_map.values()}


def _assert_card_in_scope(task: dict, task_id: str, allowed: set[str]) -> None:
    """Refuse to touch a card unless it sits in a configured WRIKE_FOLDER_* folder.

    The folder mapping gates updates, not just creates: a card is only writable if at
    least one of its parent folders is one we were told to manage.
    """
    parents = set(task.get("parentIds") or [])
    if not parents & allowed:
        raise OutOfScopeError(
            f"refusing to update {task_id}: its folder(s) {sorted(parents)} are not among "
            f"the configured WRIKE_FOLDER_* folders {sorted(allowed)}"
        )


def _new_summary() -> dict:
    """Fresh run/message counters. run_sync aggregates across a batch; the Service Bus
    consumer uses a one-message-scoped summary per delivery."""
    return {"created": 0, "updated": 0, "unchanged": 0, "comments": 0,
            "alerts": 0, "failed": 0, "deferred": 0}


def process_family(conn, make_client, item, folder_map, author_map, now, summary,
                   item_type_id=None) -> str:
    """Create-or-update one PLM family's Wrike card. Shared by the batch loop (run_sync)
    and the Service Bus consumer (one call per message). Returns the outcome status."""
    author = resolve_author(item["product_category"], author_map)
    token_ref = author[1] if author else None
    client = make_client(token_ref)  # author identity: creates/updates the card in its folder
    allowed_folders = _configured_folder_ids(client, folder_map)  # the only folders we touch

    # Decide create vs update. The local map is a fast cache; on a miss we search ONLY the
    # configured folders (incl. subfolders) by title prefix — never other spaces.
    entry = get_task_map_entry(conn, item["family_id"])
    if entry:
        task_id, snapshot, permalink = (
            entry["wrike_task_id"], entry["snapshot"], entry.get("wrike_permalink"))
    else:
        task_id = client.find_task_by_title_prefix(item["family_id"], allowed_folders)
        snapshot, permalink = None, None  # adopted card has no last-synced baseline

    if task_id is None:
        # New item: it must be created in the existing folder matching its prefix. If no
        # folder matches the prefix, queue the item for the next sync (don't create/error).
        folder_id, _is_fallback = resolve_folder(item["prefix"], folder_map)
        if not folder_id:
            return "deferred"
        task = client.create_task(folder_id, build_create_payload(item, item_type_id))
        task_id, permalink = task["id"], task.get("permalink")
        summary["created"] += 1
        status = "created"
    else:
        # Safety gate: only ever write to a card that lives in a configured folder. The
        # map or the account-wide search can point at a card from an earlier run in
        # another (e.g. production) space; refuse it instead of updating it.
        task = client.get_task(task_id)
        _assert_card_in_scope(task, task_id, allowed_folders)
        # An adopted card (found by search) has no baseline snapshot: refresh every
        # PLM-owned section but do not emit change/alert comments.
        refresh_all = snapshot is None
        section_changes = {} if refresh_all else detect_section_changes(snapshot, item)
        alerts = {} if refresh_all else detect_alerts(snapshot, item)
        # Validate incoming PLM values against what is currently on the live card and
        # send only the fields that actually differ — no redundant writes / version
        # -history noise (client requirement). Wrike returns customFields by default.
        payload = build_update_payload(item, _current_custom_fields(task))
        if refresh_all or section_changes:
            existing = task.get("description") or ""
            payload["description"] = merge_description(
                existing,
                material_codes=item.get("material_codes")
                if refresh_all or "material_codes" in section_changes else None,
                contents=item.get("contents")
                if refresh_all or "contents" in section_changes else None,
            )
        # Skip the PUT entirely when nothing changed vs the live card (no changed
        # custom fields and no description refresh). Comments/alerts below still fire
        # off the snapshot diff regardless, since they report PLM-side changes.
        if payload.get("customFields") or "description" in payload:
            client.update_task(task_id, payload)
            summary["updated"] += 1
            status = "updated"
        else:
            summary["unchanged"] += 1
            status = "unchanged"
        if section_changes:
            client.add_comment(task_id, format_change_comment(section_changes, now))
            summary["comments"] += 1
        if alerts:
            client.add_comment(task_id, format_alert_comment(alerts, now))
            summary["alerts"] += 1

    upsert_task_map(
        conn, family_id=item["family_id"], plm_internal_id=item["plm_internal_id"],
        wrike_task_id=task_id, wrike_permalink=permalink, snapshot=_snapshot(item), now=now,
    )
    return status


def load_sync_context(conn) -> dict:
    """The per-run context process_family needs beyond the family item: the static
    prefix -> Wrike folder map (WRIKE_FOLDER_* settings, no live discovery; an unmapped
    prefix defers), the category -> author-token map, and the Custom Item Type stamped on
    every new card (PRD: Item Type = "Retail Item"). run_sync builds it once per batch;
    the Service Bus consumer once per message."""
    return {
        "folder_map": load_folder_map(),
        "author_map": load_category_author_map(conn),
        "item_type_id": os.environ.get("WRIKE_RETAIL_ITEM_TYPE_ID"),
    }


def sync_one_family(conn, make_client, item, ctx, now) -> str:
    """Create/update one family's Wrike card and stamp its outcome (Stage 5). The single
    -message seam for the Service Bus consumer; run_sync drives the same process_family in
    a loop with shared batch counters, so it calls process_family directly, not this."""
    status = process_family(conn, make_client, item, ctx["folder_map"],
                            ctx["author_map"], now, _new_summary(), ctx["item_type_id"])
    record_sync_result(conn, item["family_id"], status)
    return status


def run_sync(conn, make_client, *, now, since=None) -> dict:
    watermark = since or get_watermark(conn) or EPOCH
    ctx = load_sync_context(conn)

    families = sorted_eligible_families(conn, watermark)
    summary = _new_summary()
    ok_mods, held_mods = [], []  # held = deferred or failed: watermark must not pass them

    for item in families:
        try:
            status = process_family(
                conn, make_client, item, ctx["folder_map"], ctx["author_map"], now,
                summary, ctx["item_type_id"])
            record_sync_result(conn, item["family_id"], status)  # Stage 5 outcome stamp
            if status == "deferred":  # e.g. folder not configured yet -> retry next sync
                summary["deferred"] += 1
                held_mods.append(item["modified_at"])
            else:
                ok_mods.append(item["modified_at"])
        except Exception as exc:  # isolate one row's failure; dead-letter and continue
            dead_letter(
                conn, family_id=item["family_id"], plm_internal_id=item["plm_internal_id"],
                reason=f"{type(exc).__name__}: {exc}",
                payload={"item_number": item.get("item_number")},
            )
            record_sync_result(conn, item["family_id"], "failed",
                               error=f"{type(exc).__name__}: {exc}")
            summary["failed"] += 1
            held_mods.append(item["modified_at"])

    # Never advance the watermark past the earliest held (deferred/failed) row (ties
    # included), so it is retried next run. Re-processing successes is a safe no-op.
    if held_mods:
        boundary = min(held_mods)
        earlier_ok = [m for m in ok_mods if m < boundary]
        new_watermark = max(earlier_ok) if earlier_ok else watermark
    else:
        new_watermark = max(ok_mods) if ok_mods else watermark

    set_watermark(conn, new_watermark, rows_in_delta=len(families),
                  rows_succeeded=len(ok_mods), rows_failed=summary["failed"])
    return summary
