"""Orchestrator: reader -> canonical resolver -> per-record create/update in Wrike.

One-way PLM -> Wrike. The live map (wrike_task_map) is 1:1 - one canonical PLM record
<-> one Wrike card - and the id->id lookup is the primary path: a mapped record updates
its card directly with no searching. Only an unmapped record searches Wrike, by the
"PLM - Item #" custom field within its prefix's folder, then branches:

  nothing returned                  -> CREATE the canonical card (+ 1:1 map row)
  one EXACT item#+customer match    -> UPDATE it (+ map row); raw string match only
  exact match + non-identical extras-> UPDATE the exact match, LOG the extras
  only non-identical / ambiguous    -> LOG to wrike_unmapped_log, write NOTHING

Update = PUT only the update-eligible fields whose incoming value differs from the
live card (no redundant writes), section-merge the description for changed sections,
comment on Material Codes/Contents changes, and alert (comment, no write) on Title /
Brand Category changes. A card with nothing changed is left untouched ("unchanged").
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
    CUSTOMER_FIELD_ID,
    ITEM_NUMBER_FIELD_ID,
    build_create_payload,
    build_update_payload,
    customer_matches,
    load_category_author_map,
    load_folder_map,
    resolve_author,
    resolve_folder,
)
from loader import item_prefix
from plm_reader import read_item_customer_pairs, sorted_eligible_items
from state import (
    EPOCH,
    dead_letter,
    get_task_map_entry_by_plm_id,
    get_watermark,
    log_unmapped,
    record_sync_result,
    set_watermark,
    upsert_task_map,
)


class OutOfScopeError(Exception):
    """A mapped/adopted card lives outside the managed folders (wrike_folder_map +
    the staging folder).

    Raised before any write so the card is dead-lettered, never updated. Guards
    against the local map pointing the sync at a card that was moved to another
    space, e.g. a real production card.
    """


def _snapshot(item: dict) -> dict:
    return {f: item.get(f) for f in SNAPSHOT_FIELDS}


def _current_custom_fields(task: dict) -> dict:
    """Map custom-field id -> current value from a live Wrike task. Wrike omits unset
    fields from the list, so a field absent here compares as empty (see _normalize)."""
    return {cf["id"]: cf.get("value") for cf in task.get("customFields") or []}


def _managed_folder_ids(client, folder_map: dict) -> set[str]:
    """The wrike_folder_map folder ids (incl. the '*' staging row) as Wrike v4 ids."""
    return {client.resolve_folder_id(fid) for fid in folder_map.values()}


def _assert_card_in_scope(task: dict, task_id: str, allowed: set[str]) -> None:
    """Refuse to touch a card unless it sits in a managed folder.

    The folder mapping gates updates, not just creates: a card is only writable if at
    least one of its parent folders is one we were told to manage.
    """
    parents = set(task.get("parentIds") or [])
    if not parents & allowed:
        raise OutOfScopeError(
            f"refusing to update {task_id}: its folder(s) {sorted(parents)} are not among "
            f"the managed wrike_folder_map/staging folders {sorted(allowed)}"
        )


def _new_summary() -> dict:
    """Fresh run/message counters. run_sync aggregates across a batch; the Service Bus
    consumer uses a one-message-scoped summary per delivery."""
    return {"created": 0, "updated": 0, "unchanged": 0, "comments": 0,
            "alerts": 0, "failed": 0, "deferred": 0, "logged": 0}


def process_family(conn, make_client, item, folder_map, author_map, now, summary,
                   item_type_id=None) -> str:
    """Create-or-update one canonical PLM record's Wrike card. Shared by the batch loop
    (run_sync) and the Service Bus consumer (one call per message). Returns the outcome
    status: created | updated | unchanged | logged | deferred."""
    author = resolve_author(item["product_category"], author_map)
    token_ref = author[1] if author else None
    client = make_client(token_ref)  # author identity: creates/updates the card in its folder

    # Primary path: the 1:1 live map. A hit goes straight id->id - no Wrike search.
    entry = get_task_map_entry_by_plm_id(conn, item["plm_internal_id"])
    if entry:
        task_id, snapshot, permalink = (
            entry["wrike_task_id"], entry["snapshot"], entry.get("wrike_permalink"))
        extras_to_log = []
    else:
        # Cold path: search the prefix's folder (or staging) by the item number, then
        # branch on what comes back. The search is folder-scoped, never account-wide.
        folder_id, is_staging = resolve_folder(item["prefix"], folder_map)
        if folder_id is None:  # no mapped folder AND no '*' staging row -> retry later
            return "deferred"
        matches = client.find_tasks_by_custom_field(
            ITEM_NUMBER_FIELD_ID, item["item_number"], folder_id)
        exact = [t for t in matches
                 if customer_matches(item["customer"],
                                     _current_custom_fields(t).get(CUSTOMER_FIELD_ID))]
        exact_ids = {t["id"] for t in exact}
        extras_to_log = [t for t in matches if t["id"] not in exact_ids]

        if not matches:
            # Nothing in Wrike for this item number -> create the canonical card.
            task = client.create_task(folder_id, build_create_payload(item, item_type_id))
            task_id, permalink = task["id"], task.get("permalink")
            if is_staging:
                log_unmapped(
                    conn, item_number=item["item_number"], customer=item["customer"],
                    wrike_task_id=task_id, prefix=item["prefix"], reason="unmapped_prefix",
                    details=f"no wrike_folder_map row for prefix {item['prefix']!r}; "
                            f"card created in the staging folder {folder_id}",
                )
            summary["created"] += 1
            upsert_task_map(
                conn, plm_internal_id=item["plm_internal_id"], family_id=item["family_id"],
                item_number=item["item_number"], customer=item["customer"],
                wrike_task_id=task_id, wrike_permalink=permalink,
                snapshot=_snapshot(item), now=now,
            )
            return "created"

        if len(exact) != 1:
            # No exact item#+customer match (or several identical ones): never guess,
            # never overwrite a hand-made card - log everything for human review.
            reason = "no_exact_match" if not exact else "multiple_exact_matches"
            for task in matches:
                card_fields = _current_custom_fields(task)
                log_unmapped(
                    conn, item_number=item["item_number"],
                    customer=card_fields.get(CUSTOMER_FIELD_ID),
                    wrike_task_id=task["id"], prefix=item["prefix"], reason=reason,
                    details=f"PLM record {item['plm_internal_id']} "
                            f"(customer {item['customer']!r}) not synced; "
                            f"card title {task.get('title', '')!r}",
                )
            summary["logged"] += 1
            return "logged"

        # Exactly one exact match -> adopt that card and update it below. The adopted
        # card has no last-synced baseline, so every PLM-owned section is refreshed.
        task_id, snapshot, permalink = exact[0]["id"], None, None

    # Safety gate: only ever write to a card that lives in a managed folder. The map
    # can point at a card that was since moved to another (e.g. production) space;
    # refuse it instead of updating it.
    task = client.get_task(task_id)
    allowed = _managed_folder_ids(client, folder_map)
    _assert_card_in_scope(task, task_id, allowed)

    # An adopted card (found by search) has no baseline snapshot: refresh every
    # PLM-owned section but do not emit change/alert comments.
    refresh_all = snapshot is None
    section_changes = {} if refresh_all else detect_section_changes(snapshot, item)
    alerts = {} if refresh_all else detect_alerts(snapshot, item)
    # Validate incoming PLM values against what is currently on the live card and
    # send only the fields that actually differ - no redundant writes / version
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

    # The non-identical cards that came back alongside the adopted exact match (e.g.
    # a hand-made customer-variant card sharing the item number): review-log them,
    # noting the canonical card was the one updated.
    for extra in extras_to_log:
        log_unmapped(
            conn, item_number=item["item_number"],
            customer=_current_custom_fields(extra).get(CUSTOMER_FIELD_ID),
            wrike_task_id=extra["id"], prefix=item["prefix"],
            reason="non_identical_extra",
            details=f"shares item number with card {task_id}, which was {status}; "
                    f"card title {extra.get('title', '')!r}",
        )

    upsert_task_map(
        conn, plm_internal_id=item["plm_internal_id"], family_id=item["family_id"],
        item_number=item["item_number"], customer=item["customer"],
        wrike_task_id=task_id, wrike_permalink=permalink,
        snapshot=_snapshot(item), now=now,
    )
    return status


def load_sync_context(conn) -> dict:
    """The per-run context process_family needs beyond the family item: the human-
    maintained prefix -> Wrike folder map (wrike_folder_map table, incl. the '*'
    staging row an unmapped prefix routes to), the category -> author-token map, and
    the Custom Item Type stamped on every new card (PRD: Item Type = "Retail Item").
    run_sync builds it once per batch; the Service Bus consumer once per message."""
    return {
        "folder_map": load_folder_map(conn),
        "author_map": load_category_author_map(conn),
        "item_type_id": os.environ.get("WRIKE_RETAIL_ITEM_TYPE_ID"),
    }


def sync_one_family(conn, make_client, item, ctx, now) -> str:
    """Create/update one record's Wrike card and stamp its outcome. The single-message
    seam for the Service Bus consumer; run_sync drives the same process_family in a
    loop with shared batch counters, so it calls process_family directly, not this."""
    status = process_family(conn, make_client, item, ctx["folder_map"],
                            ctx["author_map"], now, _new_summary(), ctx["item_type_id"])
    record_sync_result(conn, item["plm_internal_id"], status)
    return status


def run_sync(conn, make_client, *, now, since=None) -> dict:
    watermark = since or get_watermark(conn) or EPOCH
    ctx = load_sync_context(conn)

    items = sorted_eligible_items(conn, watermark)
    summary = _new_summary()
    ok_mods, held_mods = [], []  # held = deferred or failed: watermark must not pass them

    for item in items:
        try:
            status = process_family(
                conn, make_client, item, ctx["folder_map"], ctx["author_map"], now,
                summary, ctx["item_type_id"])
            record_sync_result(conn, item["plm_internal_id"], status)
            if status == "deferred":  # no folder AND no '*' staging row -> retry next sync
                summary["deferred"] += 1
                held_mods.append(item["modified_at"])
            else:
                # "logged" advances too: the record is parked in wrike_unmapped_log for a
                # human; holding it back would re-log the same card every run.
                ok_mods.append(item["modified_at"])
        except Exception as exc:  # isolate one row's failure; dead-letter and continue
            dead_letter(
                conn, family_id=item["family_id"], plm_internal_id=item["plm_internal_id"],
                reason=f"{type(exc).__name__}: {exc}",
                payload={"item_number": item.get("item_number")},
            )
            record_sync_result(conn, item["plm_internal_id"], "failed",
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

    set_watermark(conn, new_watermark, rows_in_delta=len(items),
                  rows_succeeded=len(ok_mods), rows_failed=summary["failed"])
    return summary


def reconcile_folders(conn, make_client, ctx) -> dict:
    """Walk EVERY card in each managed folder (incl. staging) and review-log the ones
    that do not map to any plm_item row by (item_number + raw customer) - hand-made
    cards included. Read-only against Wrike; feeds the Excel data-quality export the
    client reviews. Run on demand (bootstrap or periodic audit), separate from the sync.
    """
    token_refs = sorted({ref for _author, ref in ctx["author_map"].values()})
    if not token_refs:
        raise RuntimeError("category_author_map is empty: no Wrike token to read folders with")
    client = make_client(token_refs[0])  # reads only - any identity that can see the folders

    # One PLM read up front; each card then matches in Python so the raw exact
    # customer rule has a single implementation (mapping.customer_matches).
    plm_customers_by_item: dict[str, list] = {}
    for plm_item_number, plm_customer in read_item_customer_pairs(conn):
        plm_customers_by_item.setdefault(plm_item_number, []).append(plm_customer)

    summary = {"cards": 0, "mapped": 0, "logged": 0}
    # The folder map includes the '*' staging row, so staged cards are walked too.
    for map_key, folder_id in ctx["folder_map"].items():
        for task in client.list_folder_tasks(folder_id):
            summary["cards"] += 1
            card_fields = _current_custom_fields(task)
            item_number = card_fields.get(ITEM_NUMBER_FIELD_ID) or ""
            card_customer = card_fields.get(CUSTOMER_FIELD_ID)
            # A blank "PLM - Item #" (e.g. a hand-made card) never matches a record.
            if any(customer_matches(plm_customer, card_customer)
                   for plm_customer in plm_customers_by_item.get(item_number, [])):
                summary["mapped"] += 1
            else:
                log_unmapped(
                    conn, item_number=item_number or None, customer=card_customer or None,
                    wrike_task_id=task["id"],
                    prefix=item_prefix(item_number) or None,  # the card's own prefix
                    reason="no_plm_match",
                    details=f"card title {task.get('title', '')!r} "
                            f"in folder {map_key}={folder_id}",
                )
                summary["logged"] += 1
    return summary
