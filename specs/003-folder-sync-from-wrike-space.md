# 003 Folder Sync from a Wrike Space

**Status:** complete (implemented 2026-06-15; pending live-sandbox validation)
**Created:** 2026-06-15
**Last updated:** 2026-06-15

> Builds on **001** (the as-built PLM → Wrike engine) and **002** (Centric as the PLM
> source). This spec changes only **how `wrike_folder_map` gets populated**. Everything
> downstream of the map — the producer's enqueue, the Service Bus consumer, `process_family`,
> `resolve_folder`, reconciliation — is unchanged.

---

## 1. Overview

Today `wrike_folder_map` is **human-maintained** and the schema calls it the *"sole
authority — the app never discovers folders by searching Wrike."* This spec **reverses
that**: a new **folder-sync stage** runs at the start of each producer run, reads a
configured Wrike **space id**, pulls that space's **top-level folders**, and rebuilds
`wrike_folder_map` from them — deriving each row's `prefix` and a new `brand` column from
the folder title (`"<PREFIX> - <Brand>"`). It also guarantees a `_PENDING_REVIEW` staging
folder exists (creating it in Wrike if missing). After this stage the table is current and
the existing engine routes cards exactly as before: **by prefix only** — a prefix hit goes
to that folder, a miss goes to `_PENDING_REVIEW`.

## 2. Requirements

**Folder-sync stage (`folder_sync.py`, new)**
- WHEN the stage runs, THEN list the **top-level** folders of `WRIKE_SPACE_ID` and, for
  each whose title contains `" - "`, split on the first `" - "` into `prefix` (left) and
  `brand` (right) and upsert a `wrike_folder_map` row
  `(prefix, wrike_folder_id, full_folder_name, brand, space_id)`. The Wrike folder `id`
  (already a v4 id) is stored directly.
- WHEN a top-level folder's title does **not** contain `" - "` (no derivable prefix),
  THEN skip it and log a WARNING (it cannot be keyed) — do not guess a prefix.
- WHEN the stage runs, THEN **erase the whole table** (`TRUNCATE wrike_folder_map`) before
  repopulating, so the map is always a faithful mirror of the current space — folders renamed
  or removed in Wrike never leave stale rows, and a changed `WRIKE_SPACE_ID` is handled for
  free (every row is simply replaced).
- WHEN no top-level folder titled `_PENDING_REVIEW` exists in the space, THEN create it in
  Wrike (`POST /folders/{rootFolderId}/folders`) during this stage. EITHER way, store the
  `_PENDING_REVIEW` folder as the `'*'` staging row so the existing `resolve_folder`
  fallback is untouched.
- WHEN the stage finishes, THEN return a summary (folders found / upserted / skipped,
  whether the table was wiped, whether `_PENDING_REVIEW` was created) for logging.

**Wrike client (`wrike_client.py`)** *(mechanics verified live 2026-06-15 — see §4)*
- WHEN listing a space's **top-level** folders, THEN `GET /spaces/{spaceId}/folders` (which
  returns the **whole flattened subtree**, including the root folder whose `id == spaceId`),
  find the root entry by `id == spaceId`, and return only the folders whose `id` is in the
  root's `childIds` (each dict carries `id`, `title`).
- WHEN creating a folder at the space's top level, THEN `POST /folders/{spaceId}/folders` with
  `title` as a **query** parameter (not JSON) and return the new folder dict (carries its
  `id`). The space id **is** its root folder id — there is no `rootFolderId` to look up
  (`GET /spaces/{spaceId}?fields=["rootFolderId"]` returns HTTP 400).

**Producer wiring (`function_app.py`)**
- WHEN the producer timer fires, THEN run the folder-sync stage (using the catch-all author
  identity) **after** the Centric refresh and **before** reading eligible items. The rest of
  the producer is unchanged.

**Schema (`db/schema.sql`)**
- WHEN the schema is applied, THEN `wrike_folder_map` has a nullable `brand text` column
  (idempotent `ADD COLUMN IF NOT EXISTS`).

### Out of Scope

- Any change to `mapping.py` (`resolve_folder`, `load_folder_map`), `sync.py`,
  `plm_reader.py`, `state.py`, `changes.py`, or the consumer/reconcile flow. Routing stays
  **prefix-only**; `brand` is stored for audit/identification, **not** a routing key, so
  `load_folder_map` (which returns `{prefix -> folder_id}`) is untouched.
- Using `brand` as a second lookup before `_PENDING_REVIEW` (explicitly dropped — prefix
  miss routes straight to staging).
- Re-pulling folders mid-sync per unmapped card (no per-card Wrike re-trigger — the one
  upfront stage is authoritative for the run).
- Deleting Wrike folders, renaming them, moving cards, or nested/subfolder routing (only
  top-level folders are mapped).

## 3. Design

### Flow (producer)

```
Centric refresh (002)  →  [NEW] folder sync  →  read eligible items  →  enqueue  →  advance watermark
                              │
                              ├─ list top-level folders of WRIKE_SPACE_ID
                              ├─ TRUNCATE wrike_folder_map  (always — full rebuild)
                              ├─ insert (prefix, folder_id, full_folder_name, brand, space_id)
                              └─ ensure _PENDING_REVIEW folder (create if missing) → store as '*' row
```

The consumer later reads the freshly-populated table via the unchanged
`load_folder_map` / `resolve_folder` path.

### `wrike_folder_map` row, before → after

| Column | Before (human-seeded) | After (folder-sync owned) |
|---|---|---|
| `prefix` | hand-entered, e.g. `WP` | title left of `" - "`, e.g. `WP` |
| `wrike_folder_id` | numeric permalink or v4 | the folder's **v4 `id`** from the API |
| `full_folder_name` | hand-entered | the folder `title` verbatim |
| **`brand`** *(new)* | — | title right of `" - "`, e.g. `Winnie-the-Pooh` |
| `space_id` | hand-entered | `WRIKE_SPACE_ID` |
| `'*'` row | `Pending (staging)` | the `_PENDING_REVIEW` folder (created if absent) |

### Affected Files

| Layer | File | Change |
|-------|------|--------|
| Core logic | `folder_sync.py` | **New.** `sync_folders(conn, client, *, space_id) -> dict`: list top-level folders, wipe-on-space-change, parse `"<PREFIX> - <Brand>"`, upsert rows, ensure `_PENDING_REVIEW` (`'*'` row) |
| Core logic | `wrike_client.py` | Add `list_top_level_folders(space_id)` (lists the space subtree, filters to the root's direct children) and `create_folder(parent_folder_id, title)` (top-level ⇒ parent is the `space_id`) |
| Entry point | `function_app.py` | `plm_wrike_producer`: call `sync_folders` after the Centric refresh, before `sorted_eligible_items`, with the catch-all author's Wrike client |
| Data | `db/schema.sql` | `ALTER TABLE wrike_folder_map ADD COLUMN IF NOT EXISTS brand text`; note in comments that folder sync now owns/repopulates the table; refresh the POC seed to carry a `brand` value |
| Config | `local.settings.json.example` | Add `WRIKE_SPACE_ID` |
| Tests | `tests/test_folder_sync.py` (new), `tests/test_wrike_client.py` | See Tasks 5–6 |
| Unchanged | `mapping.py`, `sync.py`, `plm_reader.py`, `state.py`, `changes.py`, `loader.py`, `centric_client.py` | Engine + routing from 001/002 untouched |

### Key Decisions

- **Folder sync runs in the producer, once per run.** It *writes* the table; the consumer
  *reads* it later in the same run — no concurrency. One Wrike pull per run (the listing is
  unpaginated and a space has only a handful of top-level folders), mirroring how the Centric
  refresh sits at the front of the producer.
- **Identity = the catch-all author.** Listing and *creating* folders is a write whose author
  matters, so the stage uses the `category_author_map` `'*'` row's token (the same default
  identity staging cards are already created under). **Requirement:** that token must be a
  member of `WRIKE_SPACE_ID` with folder-create permission (else `403`).
- **Top-level only, via the root's `childIds`.** `GET /spaces/{spaceId}/folders` returns the
  whole flattened subtree *including the root* (`id == spaceId`) — verified live (113 folders
  vs 93 true top-level). So the client finds the root entry and keeps only folders in its
  `childIds`. (The "not referenced as anyone's child" heuristic is wrong here — the root is in
  the list and claims all top-level folders as children.)
- **Routing stays prefix-only.** `brand` is parsed and stored but never read for routing, so
  `mapping.py` is untouched and "downstream stays the same" holds literally. (Adding a column
  the app only writes is intentional here — it is audit/identification data the client asked
  for, not dead code.)
- **`_PENDING_REVIEW` reuses the `'*'` convention.** Storing the staging folder under
  `prefix = '*'` means `resolve_folder` / `_managed_folder_ids` / reconcile keep working with
  zero changes; only the staging folder's *source* changes (discovered/created vs hand-seeded).
- **Always wipe + repopulate.** Each run `TRUNCATE`s and rebuilds the table from the live
  listing, so it is always a faithful mirror — no stale rows from renamed/removed folders, and
  a changed `WRIKE_SPACE_ID` is just the case where every row is replaced. The listing is
  unpaginated and small, so the full rebuild is cheap.

## 4. Reference Documents

> **Rule:** read these before coding; do not duplicate them here.

| Document | Location | What to look for |
|----------|----------|------------------|
| Wrike folders & spaces | `project_docs/wrike_api.md` → "Folders & Spaces" | **Verified-live** mechanics: `GET /spaces/{id}/folders` returns the **whole subtree** incl. the root (`id == spaceId`) → filter to the root's `childIds`; **space id IS the root folder id** (no `rootFolderId`; `fields=["rootFolderId"]` ⇒ 400); `POST /folders/{spaceId}/folders?title=…` (query param, **not** JSON; not idempotent — check existence first) |
| Wrike client pattern | `wrike_client.py` | `_request` + capped backoff; `resolve_folder_id` (v4 ids pass through); `bearer` auth |
| As-built engine | `specs/001-plm-to-wrike-sync.md` | `resolve_folder` / `load_folder_map` / `_managed_folder_ids` / the `'*'` staging convention this preserves |
| Centric source | `specs/002-centric-8-api-source.md` | Where the producer's refresh sits — folder sync slots in right after it |
| Testing / style / logging | `agent_docs/*` | Fixture + HTTP-mock patterns, naming, log levels |

## 5. Implementation Checklist

> **Verified on sandbox (2026-06-15):** `GET /spaces/{spaceId}/folders` returns the **full
> flattened subtree** (113 folders vs 93 true top-level), including the root (`id == spaceId`);
> filter to the root's `childIds` for top-level. The **space id is its own root folder id** —
> there is no `rootFolderId` field (`fields=["rootFolderId"]` ⇒ 400), and `POST
> /folders/{spaceId}/folders` creates at the top level. (Both facts are documented in §4's doc.)

- [x] **Task 1: Schema — `brand` column.**
      Files: `db/schema.sql`
      Details: `ALTER TABLE wrike_folder_map ADD COLUMN IF NOT EXISTS brand text` (nullable).
      Update the table's header comment (folder sync now owns/repopulates it) and add a
      `brand` value to the POC seed rows so the example stays valid.

- [x] **Task 2: Wrike client — space/folder methods.** *(independent)*
      Files: `wrike_client.py`, `tests/test_wrike_client.py`
      Details: `list_top_level_folders(space_id)` → `GET /spaces/{spaceId}/folders` (the full
      subtree); find the root entry (`id == space_id`), return only the `data` folders whose
      `id` is in the root's `childIds`. `create_folder(parent_folder_id, title)` → `POST
      /folders/{parent}/folders` with `params={"title": title}`, return `data[0]` (top-level
      callers pass `space_id` as the parent — it is the root folder id).
      Ref: `project_docs/wrike_api.md` "Folders & Spaces".
      Test (mocked transport): list filters to the root's direct children (the root entry and a
      deep descendant are excluded); create posts the title and returns the new folder; never
      hits the live API.

- [x] **Task 3: Folder-sync stage.** *(depends on: Tasks 1, 2)*
      Files: `folder_sync.py`, `tests/test_folder_sync.py`
      Details: `sync_folders(conn, client, *, space_id) -> dict`:
      (a) `folders = client.list_top_level_folders(space_id)`;
      (b) `TRUNCATE wrike_folder_map` (always — full rebuild each run);
      (c) for each top-level folder, `prefix, sep, brand = title.partition(" - ")` — skip +
      WARNING when `sep` is empty; otherwise insert `(prefix, id, title, brand, space_id)`;
      (d) scan `folders` for one titled `_PENDING_REVIEW`; if none, create it via
      `client.create_folder(space_id, "_PENDING_REVIEW")`; insert it as the `'*'` row;
      (e) return the summary.
      Test: parses prefix/brand; skips non-`" - "` titles; rebuilds the table each run (stale
      rows gone); creates `_PENDING_REVIEW` only when absent from the listing and stores it as
      `'*'`; idempotent across two runs.

- [x] **Task 4: Wire into the producer.** *(depends on: Tasks 2, 3)*
      Files: `function_app.py`
      Details: In `plm_wrike_producer`, after `refresh_plm_items`, resolve the catch-all
      author's `token_ref` (from `load_category_author_map`), build a Wrike client, and call
      `sync_folders(conn, client, space_id=os.environ["WRIKE_SPACE_ID"])`; log the summary.
      Everything after (`sorted_eligible_items` → enqueue → watermark) is unchanged.

- [x] **Task 5: Config.** *(depends on: Task 4)*
      Files: `local.settings.json.example`
      Details: `WRIKE_SPACE_ID` was already present — enhanced its placeholder to note the
      catch-all author token must be a member of that space with folder-create permission.

- [x] **Task 6: README.** *(depends on: Tasks 4, 5)*
      Files: `README.md` (via readme-manager)
      Details: Document the folder-sync stage (app now owns `wrike_folder_map`, prefix/brand
      from folder titles, `_PENDING_REVIEW`, `WRIKE_SPACE_ID`). Correct the "app never
      discovers folders" wording carried from 001.

## 6. Acceptance Criteria

- [x] All checklist tasks done; `python -m pytest` passes (**118 passed**), with **no test
      reaching the live Wrike API** (HTTP mocked).
- [x] `wrike_folder_map` has a nullable `brand` column; existing `seed_folder_map` inserts
      (which omit `brand`) still work.
- [x] `list_top_level_folders` filters the space subtree to the root's direct children (root
      entry + deep descendants excluded), and `create_folder` posts the title with the space id
      as parent; both parse the responses (`tests/test_wrike_client.py`).
- [x] `sync_folders` derives `prefix`/`brand` by splitting the title on `" - "`, skips
      non-conforming titles with a warning, rebuilds the table each run (truncate +
      repopulate), creates `_PENDING_REVIEW` only when absent and stores it as the `'*'` row,
      and is idempotent (`tests/test_folder_sync.py`).
- [x] The producer runs folder sync after the Centric refresh and before reading items; the
      consumer/`resolve_folder` path is unchanged and routes prefix-only (prefix miss →
      `_PENDING_REVIEW`).
- [ ] **Live-sandbox validation (pending):** against the real `WRIKE_SPACE_ID`, folder sync
      lists the top-level folders, populates prefix/brand rows, and creates `_PENDING_REVIEW` if
      it was absent; a card with an unmapped prefix lands in `_PENDING_REVIEW`.
- [x] README + `local.settings.json.example` reflect the folder-sync stage and `WRIKE_SPACE_ID`.

## 7. Execution Notes (2026-06-15)

- **Hardening beyond the literal checklist:** `sync_folders` also skips a **duplicate prefix**
  (two top-level folders sharing a prefix) with a WARNING, keeping the first — a plain insert
  would hit the `prefix` primary key and abort the whole producer run. Same skip+warn shape as
  the no-`" - "` and `_PENDING_REVIEW` paths.
- **Migration applied to the running DB:** the idempotent `ADD COLUMN IF NOT EXISTS brand` was
  run against the dev/test `plm` Postgres so the suite is green; re-running `db/schema.sql`
  elsewhere picks it up the same way.
- **Files touched:** `db/schema.sql`, `wrike_client.py`, `folder_sync.py` (new),
  `function_app.py`, `local.settings.json.example`, `project_docs/wrike_api.md` (verified-live
  corrections), `README.md`, plus `tests/test_wrike_client.py`, `tests/test_folder_sync.py`.
