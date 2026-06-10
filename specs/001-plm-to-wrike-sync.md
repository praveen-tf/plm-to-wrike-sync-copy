# 001 PLM to Wrike Sync

**Status:** as-built (reference) — incl. mapping + reconciliation (implemented 2026-06-10; live smoke test pending)
**Created:** 2026-06-08
**Last updated:** 2026-06-10

> This spec documents the **existing, implemented** PLM → Wrike sync feature as the
> contract for future changes. The Implementation Checklist (section 5) reflects the
> as-built state; use the Acceptance Criteria (section 6) to verify the system still
> meets its contract before merging changes.
>
> **2026-06-10 — planned change (mapping + reconciliation).** Client-clarified change: a 1:1 live
> map (`plm_internal_id` ↔ `wrike_task_id`, both unique); first-sight existence via the `item_number`
> custom field with an **exact, raw `customer` match**; ambiguous / non-identical / hand-made cards
> are logged to a **review table** (a full-folder reconciliation feeds an Excel data-quality export);
> folder routing moves to a human-maintained `wrike_folder_map` table with a **staging-folder**
> fallback. Planned items are marked **(planned)** and unchecked in sections 5–6; the rest of the doc
> remains as-built. Full decision log: project memory `project_mapping_tables_design`.

---

## 1. Overview

One-way synchronisation of product data from a PLM PostgreSQL source into Wrike work items.
The pipeline runs as two Azure Functions decoupled by an Azure Service Bus queue (`plm-sync`),
so each product family is processed and retried independently and a single bad record never
stops the batch. Wrike is never treated as a source of truth — it is only read back to confirm
a card's current state before an update.

## 2. Requirements

**Producer (timer trigger `plm_wrike_producer`)**
- WHEN the timer fires (`0 0 12,0 * * *` UTC = 05:00 / 17:00 Pacific), THEN read the watermark,
  select `plm_item` rows with `modified_at` later than the watermark and the eligibility flag set,
  reduce each family to its canonical record (CORE preferred over CUSTOM), and enqueue one Service
  Bus message per family.
- WHEN messages are enqueued, THEN advance the watermark to the max `modified_at` processed; the
  queue owns delivery and retry from that point. The producer never calls Wrike.
- WHEN nothing changed since the watermark, THEN log and exit without enqueuing.
- The message carries identifiers + `modified_at` only (not a data snapshot), and `message_id =
  family_id:modified_at` so Service Bus duplicate detection drops re-enqueues of the same family.

**Consumer (Service Bus queue trigger `plm_wrike_consumer`)**
- WHEN a message arrives, THEN re-read the canonical family by id (current DB state, not the
  enqueued snapshot). WHEN the family is no longer eligible, THEN ack and skip.
- WHEN the family has no card (no `wrike_task_map` entry and no scoped title-prefix match), THEN
  create a Wrike card under the author token + the folder matching the item prefix, stamped with
  Item Type "Retail Item".
- WHEN no configured folder matches the item prefix, THEN defer the family to the next run
  (status `deferred`) rather than misfile or error.
- WHEN the family has a card, THEN PUT only the update-eligible custom fields whose incoming PLM
  value differs from the live card; section-merge only the PLM-owned description sections; skip the
  PUT entirely when nothing differs (status `unchanged`).
- WHEN a tracked content field (material codes, contents) changed, THEN write it and add a summary
  comment. WHEN a protected field (title, brand category) changed, THEN do NOT write it and post a
  comment-only alert instead.
- WHEN a mapped/adopted card lives outside the configured `WRIKE_FOLDER_*` folders, THEN refuse the
  write and dead-letter it (`OutOfScopeError`) — never update a card in another space.
- WHEN processing fails with an unhandled error, THEN abandon the lock so Service Bus redelivers,
  and after `maxDeliveryCount` the message dead-letters; record failures in `sync_dlq`.
- WHEN any family is processed, THEN record its outcome in `wrike_task_map` (status + snapshot).

**Mapping + reconciliation (planned change — 2026-06-10)**

*These supersede the consumer's title-prefix lookup and defer-on-miss behaviours above.*

**Live map (`wrike_task_map`, re-keyed) is 1:1** — one row per canonical PLM record ↔ one Wrike card.
Keys: `plm_internal_id` UNIQUE NOT NULL ↔ `wrike_task_id` UNIQUE NOT NULL. `item_number`, `family_id`,
and `customer` are non-key columns (verification, lost-map recovery, audit). All per-customer /
ambiguous cases go to the review table, never the live map.

*Resolution / create / update / log* (per eligible item; canonical chosen by `pick_canonical`):
- WHEN the canonical record already has a live map row, THEN update that card directly by
  `wrike_task_id` (id→id) — no Wrike search.
- WHEN it has no map row, THEN search Wrike by `item_number` (the "PLM - Item #" custom field) within
  the prefix's mapped folder, and — only when `ready_for_wrike = true`:
  - WHEN nothing is returned, THEN **create** the canonical card (folder from `wrike_folder_map`, or
    the staging folder if the prefix is unmapped) and write the 1:1 map row.
  - WHEN exactly one card is returned and it is an EXACT (`item_number` + `customer`, raw string)
    match, THEN **update** it and write the map row.
  - WHEN an exact match is returned alongside other non-identical cards, THEN **update** the exact
    match (write the map row) AND **log** the extra cards to the review table (note the core card was
    updated).
  - WHEN only non-identical cards are returned (no exact match), THEN **log** them to the review table
    and create/update nothing (creation happens only when nothing is returned).
- `customer` matching is RAW exact string (no normalization) — deliberately surfaces Wrike-side data
  quality (HTML-polluted / inconsistent `customer` values fail to match and land in the review table).
- `customer` is create-only: set at creation, never overwritten by later syncs (already as-built).
- WHEN `ready_for_wrike` is false, THEN skip entirely (no create/update) — unchanged.

*Folder mapping (`wrike_folder_map`, new — replaces `WRIKE_FOLDER_*`)*:
- WHEN routing a new card, THEN look up the item prefix in `wrike_folder_map` → folder id. The table
  is human-maintained and the **sole authority for ALL folder ids — no folder id lives in app
  settings, and we never search Wrike for folders by name**.
- WHEN the prefix is not in the table, THEN create the card in the **staging folder** (the table's
  special `prefix = '*'` fallback row, client-provided id 4483642519) and **log the missed prefix** to
  the review table. New folder rows are added to the table only after client/business **approval**
  (manual). With no `'*'` row either, the item defers to a later run.

*Reconciliation + review (new)*:
- A **full-folder reconciliation scan** walks every card in each mapped folder and attempts to map it
  to a PG record by (`item_number` + `customer`, raw exact); every card that does not map — including
  hand-made ones — is written to the **review/log table**, which is exported to Excel for the client's
  data-quality review. No active alerting.

### Out of Scope

- Two-way sync or reading Wrike as a source of truth.
- Deleting Wrike cards when a PLM record is removed/deeligible.
- Real-time / event-driven sync (the trigger is a batch timer).
- Schema/source migration from the Excel/local-Postgres POC to the live `centric_8_plm` source
  (tracked separately — see project memory `project_servicebus_rearchitecture`).
- Changing the Wrike write target away from the test/Personal space.
- Auto-discovery of folders (searching Wrike by name): the `wrike_folder_map` table is the sole
  authority; new rows are added manually after client/business approval, not by the app.
- Auto-creating Wrike folders, and any built approval-workflow UI (approval is a manual human step).
- Auto-resolving the review table (cleaning duplicates, merging hand-made variant cards): the review
  export is for human/client action, not automated remediation.
- Normalizing the Wrike `customer` field before matching (raw exact match is intentional).

## 3. Design

### Affected Files (as-built layer map)

| Layer | File | Role |
|-------|------|------|
| Entry point | `function_app.py` | `plm_wrike_producer` (timer) + `plm_wrike_consumer` (SB trigger) |
| Core logic | `sync.py` | `process_family` create/update orchestration; `run_sync` batch loop; `OutOfScopeError` folder gate |
| Core logic | `plm_reader.py` | Canonical-family selection (`sorted_eligible_families`, `read_one_family`) |
| Core logic | `mapping.py` | Field mapping, create/update payloads, author + folder resolution |
| Core logic | `wrike_client.py` | Wrike REST client (create/get/update task, comments, folder resolve, title search) |
| Core logic | `state.py` | Watermark, `wrike_task_map`, `sync_dlq`, sync-result stamping |
| Helpers | `changes.py` | Snapshot diff: section changes, protected-field alerts, comment formatting |
| Helpers | `description.py` | Section-merge of the PLM-owned description |
| Helpers | `db.py` | Postgres connection helper |
| Config | `settings.py` | Mirror `local.settings.json` into `os.environ` for local runs |
| Data | `db/schema.sql` | `plm_item`, `category_author_map`, `sync_watermark`, `wrike_task_map`, `sync_dlq` |
| Tests | `tests/test_*.py` | One test file per module |

**Planned changes (mapping + reconciliation, 2026-06-10)**

| Layer | File | Change |
|-------|------|--------|
| Data | `db/schema.sql` | Re-key `wrike_task_map`: PK `plm_internal_id`, UNIQUE `wrike_task_id`, add `item_number` + `customer` columns, `family_id` → non-key (guarded migration). New `wrike_folder_map` (`prefix` PK, `wrike_folder_id`, `full_folder_name`, `space_id`, timestamps). New `wrike_unmapped_log` review table (`item_number`, `customer`, `wrike_task_id`, `prefix`, `reason`, `details`, `created_at`) |
| Core logic | `wrike_client.py` | New `find_tasks_by_custom_field` (filter `customFields=[{id,value}]`, folder-scoped — see project_docs; the field id is passed by the caller so the client stays domain-agnostic); helper to list all tasks in a folder for reconciliation. Title-prefix search retired |
| Core logic | `sync.py` | New flow in `process_family`: map short-circuit (id→id) → `item_number` search → 4 branches (create / update / update+log / log) → review-table writes; folder lookup via table with staging fallback. New `reconcile_folder()` full-folder scan |
| Core logic | `state.py` | Map getters by `plm_internal_id` and `item_number`; 1:1 upsert (both unique); `wrike_folder_map` reader; `wrike_unmapped_log` writer |
| Core logic | `mapping.py` | `load_folder_map(conn)` reads `wrike_folder_map`; raw exact `customer` match helper; `resolve_folder` returns staging-folder fallback |
| Entry point | `function_app.py` | Reconciliation entry point (one-time/periodic full-folder scan), separate from the per-message consumer |
| Config | `local.settings.json(.example)`, `README.md` | Drop `WRIKE_FOLDER_*` (no folder id remains in app settings — staging is the `'*'` table row); document the three tables |
| Reference | `project_docs/wrike_api.md` | Custom-field task filtering syntax (already created) |
| Tests | `tests/test_state.py`, `test_sync.py`, `test_mapping.py`, `test_wrike_client.py` | 1:1 map keys; item# search; the 4 branches; raw customer match; folder lookup + staging; reconciliation + review log |

### Key Decisions

- **Message = pointer, not snapshot.** The consumer re-reads current DB state by id, so an edit
  between enqueue and processing syncs the latest values.
- **Watermark advances at enqueue (producer).** Enqueue is the producer's unit of "done"; the
  queue owns delivery/retry. The batch `run_sync` path instead holds the watermark behind the
  earliest deferred/failed row so those retry next run.
- **Diff against the live card, not just the snapshot.** Update payload sends only fields that
  differ from Wrike's current values, avoiding redundant writes / version-history noise (client req).
- **Folder mapping gates both create and update.** A card is only writable if a parent folder is a
  configured `WRIKE_FOLDER_*` folder; otherwise dead-letter — protects real production cards.
- **Author = API-token owner.** `category_author_map` maps product category → `token_ref`; the card
  is created/updated under that author's token.
- **Item Type via Custom Item Type id**, not the dropdown field (`WRIKE_RETAIL_ITEM_TYPE_ID`,
  create-only).
- **(planned) Live map is 1:1, keyed `plm_internal_id` ↔ `wrike_task_id` (both unique).** Future
  syncs go id→id with no search. `pick_canonical` (one canonical record per family) and `customer`
  create-only are already built and unchanged. `item_number` / `family_id` / `customer` are non-key:
  `item_number` is the only PLM id also on the card, so it drives verification and lost-map recovery;
  `plm_internal_id` never leaves PG. All per-customer / ambiguous cases go to the review table, so the
  live map stays clean 1:1.
- **(planned) First-sight existence via `item_number` custom-field search + exact raw `customer`
  match.** Replaces the title-prefix search, which is ambiguous against the hand-made look-alikes in
  the real folder (a `*CORE` card and a `THERANGE`/`---INTL` variant share an item number). Multiple /
  non-identical / no-exact-match never auto-pick — they go to the review table — so the sync never
  duplicates a card or overwrites a hand-made variant. Raw (un-normalized) matching is intentional: it
  routes the messy `customer` values into the review export for the client to clean up.
- **(planned) Folder map = human-maintained DB table, sole authority for ALL folder ids.** `prefix →
  folder_id` (+ full name, space id, timestamps); no folder id lives in app settings — the staging
  fallback is the table's `'*'` row — and the app never searches Wrike for folders. Miss → create in
  the staging folder + log the prefix; new rows are added only after client/business approval.
  Replaces `WRIKE_FOLDER_*` and the defer-on-miss behaviour (a card is now always created somewhere).
- **(planned) Review/log table + full-folder reconciliation scan** produce the comprehensive Excel
  data-quality export — the agreed bridge from a messy hand-made folder to a clean id→id map.

## 4. Reference Documents

> **Note:** Read `project_docs/wrike_api.md` before implementing the item-number search (Task B) — it
> documents the exact folder-scoped custom-field filter syntax. Other Wrike topics (Custom Item Types,
> permalink resolution, comments) remain in the codebase/`README.md`; extend the doc as needed.

| Document | Location | What to look for |
|----------|----------|------------------|
| Wrike custom-field search | `project_docs/wrike_api.md` | `GET /folders/{id}/tasks?customFields=[{id,value}]&descendants=true` syntax, exact-match + field-type caveats |
| Feature README | `README.md` | End-to-end behaviour, config settings, deployment, data model |
| Data schema | `db/schema.sql` | Existing table definitions; the re-key + new tables land here |
| Mapping decision log | project memory `project_mapping_tables_design` | Full client-clarified rationale behind the planned change |
| Style guidelines | `agent_docs/style_guidelines.md` | Naming/formatting conventions |
| Testing guidelines | `agent_docs/testing_guidelines.md` | Fixture patterns, pytest conventions |
| Logging guidelines | `agent_docs/logging_guidelines.md` | Log levels and format |

## 5. Implementation Checklist (as-built)

- [x] **Producer**: timer trigger reads delta, reduces to canonical families, batch-sends one
      message per family, advances watermark. Files: `function_app.py`, `plm_reader.py`, `state.py`
- [x] **Consumer**: SB queue trigger re-reads family by id, create-or-update, records outcome.
      Files: `function_app.py`, `sync.py`, `state.py`
- [x] **Field mapping**: create payload (full set) + update payload (changed update-eligible fields
      only); author + folder resolution. Files: `mapping.py`
- [x] **Update rules**: diff vs live card, section-merge description, content-change comments,
      protected-field alerts, `unchanged` skip. Files: `sync.py`, `changes.py`, `description.py`
- [x] **Safety**: `OutOfScopeError` folder gate; unmapped-prefix `deferred`; dead-letter on failure.
      Files: `sync.py`, `state.py`
- [x] **Wrike client**: create/get/update task, comments, folder-id + permalink resolution,
      title-prefix search. Files: `wrike_client.py`
- [x] **Item Type**: stamp "Retail Item" Custom Item Type on new cards. Files: `mapping.py`, config
- [x] **Tests**: per-module unit + db-backed tests. Files: `tests/test_*.py`

**Planned (mapping + reconciliation, 2026-06-10)** — implemented 2026-06-10:

- [x] **Task A: Schema — re-key live map + new tables.**
      Files: `db/schema.sql`, `state.py`
      Details: Re-key `wrike_task_map` (PK `plm_internal_id`, UNIQUE `wrike_task_id`, add `item_number`
      + `customer`, `family_id` → non-key) via a guarded migration. Add `wrike_folder_map` (`prefix` PK,
      `wrike_folder_id`, `full_folder_name`, `space_id`, timestamps) and `wrike_unmapped_log`
      (`item_number`, `customer`, `wrike_task_id`, `prefix`, `reason`, `details`, `created_at`). Add
      `state.py` helpers: 1:1 upsert, get-by-`plm_internal_id`, unmapped-log writer.
      Test: 1:1 constraints reject a second card for the same `plm_internal_id` and a second
      `plm_internal_id` for the same `wrike_task_id`; helpers round-trip.
      *Deviations:* the folder-map reader lives in `mapping.py` (next to `load_category_author_map`,
      the existing config-loading pattern), and no get-by-`item_number` getter was needed (nothing
      reads the map by item number — the Wrike search returns full task dicts). The 1:1 upsert
      re-points a card's existing row when a pure-duplicate canonical flip arrives with a new
      `plm_internal_id` ("latest updated wins" is normal data, not an error).

- [x] **Task B: Wrike client — item-number search.** *(depends on: Task A)*
      Files: `wrike_client.py`
      Details: Added `find_tasks_by_custom_field(field_id, value, folder_id)` (returns ALL
      matching task dicts, customFields included) using the custom-field filter
      (`customFields=[{"id": …, "value": …}]&descendants=true`, folder-scoped — see
      `project_docs/wrike_api.md`); the item-number field id is passed by `sync.py`, keeping the
      client domain-agnostic. Added `list_folder_tasks` (paginated) for reconciliation.
      Title-prefix search retired.
      Test: returns 0 / 1 / many matches; stays folder-scoped; exact request shape asserted.

- [x] **Task C: Resolution / create / update / log flow.** *(depends on: Tasks A, B)*
      Files: `sync.py`, `mapping.py`
      Details: In `process_family` — if a map row exists, update by `wrike_task_id` (skip search);
      else search by `item_number` and branch: nothing → create + map row; one exact (`item_number` +
      raw `customer`) match → update + map row; exact + extras → update + map row + log extras; only
      non-identical (or several identical = ambiguous) → log, no write. Raw exact `customer`-match
      helper (`customer_matches`). New summary/status outcome: `logged` (advances the watermark — the
      record is parked in the review log, not retried every run).
      Test: each of the 4 branches; raw match sends HTML/variant-customer cards to the log, not the map.

- [x] **Task D: Folder mapping table + staging fallback.** *(depends on: Task A)*
      Files: `mapping.py`, `sync.py`, `db/schema.sql`, `local.settings.json(.example)`, `README.md`
      Details: `load_folder_map(conn)` reads `wrike_folder_map`; `resolve_folder` returns the
      staging folder — the table's `'*'` fallback row (seeded with the client-provided id
      4483642519) — on a miss and the caller review-logs the missed prefix (`unmapped_prefix`).
      `WRIKE_FOLDER_*` removed; **no folder id remains in app settings** (an earlier cut used a
      `WRIKE_STAGING_FOLDER_ID` env var; it was folded into the table on 2026-06-10 — all folder ids
      live in `wrike_folder_map`, per the migration's rationale). No status/approval column; no Wrike
      folder search; the folder table is never self-populated by the app.
      *Deviation:* with no `'*'` row either, an unmapped prefix still defers (safe degradation
      instead of a crash). `settings.py` needed no change (it only mirrors the json).
      Test: known prefix routes from the table; missing prefix → staging folder + logged prefix.

- [x] **Task E: Full-folder reconciliation + review export.** *(depends on: Tasks A, B)*
      Files: `sync.py`, `function_app.py`
      Details: `reconcile_folders()` walks every card in each managed folder (incl. staging), matches
      by (`item_number` + raw `customer`) against `plm_item`, and logs every unmapped card (incl.
      hand-made) to `wrike_unmapped_log` (reason `no_plm_match`). Exposed as the HTTP-triggered
      `plm_wrike_reconcile` function (auth level FUNCTION), separate from the per-message consumer.
      Test: a folder with mapped + hand-made cards logs exactly the unmapped ones.

## 6. Acceptance Criteria

- [x] All tests pass (`python -m pytest`) against the docker-compose Postgres (78 passed, 2026-06-10).
- [ ] Producer enqueues exactly one message per changed canonical family and advances the watermark;
      a second run with no changes enqueues nothing.
- [ ] Consumer creates a new family's card (Item Type "Retail Item", correct author, correct folder)
      and updates a known family with only the changed fields.
- [ ] An unchanged family results in status `unchanged` with no PUT.
- [ ] A family whose prefix has no folder row is created in the staging folder and the prefix is
      review-logged (`deferred` only when no staging folder is configured either).
- [ ] A protected-field change posts an alert comment and does NOT overwrite the field.
- [ ] A card outside the managed `wrike_folder_map`/staging folders is refused (`OutOfScopeError`) and
      dead-lettered, never updated.
- [ ] A record that fails all retries lands in the Service Bus dead-letter sub-queue and `sync_dlq`.

**Planned (mapping + reconciliation, 2026-06-10)** — verified 2026-06-10, 78 tests green:

- [x] The live map is 1:1: the DB rejects a second card for one `plm_internal_id` and a second
      `plm_internal_id` for one `wrike_task_id`. A mapped item updates by id with no Wrike search.
      (`test_state.py`; `test_mapped_record_updates_by_id_without_searching`)
- [x] An unmapped, ready item: `item_number` search returning nothing → creates the canonical card;
      one exact (`item_number` + raw `customer`) match → updates it; exact + extras → updates the exact
      match and logs the extras; only non-identical → logs and creates/updates nothing.
      (`test_sync.py` branch tests)
- [x] Customer matching is raw exact: an HTML-polluted or variant `customer` card is logged to
      `wrike_unmapped_log`, never silently mapped or overwritten. `customer` is never sent on update.
      (`test_html_polluted_customer_fails_raw_match_and_is_logged`; `test_mapping.py`)
- [x] Folder routing reads `wrike_folder_map`; a missing prefix routes the card to the staging folder
      and logs the prefix; no `WRIKE_FOLDER_*` env vars remain; the app never searches Wrike for folders.
      (`test_unmapped_prefix_creates_in_staging_and_logs`)
- [x] `reconcile_folders()` over a folder of mapped + hand-made cards logs exactly the unmapped ones to
      `wrike_unmapped_log` (the Excel export source).
      (`test_reconcile_logs_unmapped_cards_including_hand_made`)
- [x] `ready_for_wrike = false` skips create and update entirely (not-ready rows are excluded from
      the delta; `test_reader.py`).
- [x] All tests pass (`python -m pytest`) including the 1:1-constraint, item#-search, 4-branch,
      raw-match, staging-fallback, and reconciliation cases — and the guarded migration was applied
      to a pre-existing (old-schema) docker volume, twice (idempotent), before the green run.
