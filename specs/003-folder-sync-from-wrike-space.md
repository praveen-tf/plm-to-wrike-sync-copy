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
the engine routes cards **prefix-first with brand as the tiebreaker**: a prefix with one
folder routes by prefix alone; a prefix shared by several brands (e.g. `MB - MR BEAST` vs
`MB - MEAT BOARDS`) is disambiguated by an exact brand match; an unmapped prefix — or a
multi-brand prefix with no brand match — goes to `_PENDING_REVIEW`.

> **Design evolved during execution (2026-06-15):** this started prefix-only with `brand`
> as audit-only. Live validation found two real folders sharing prefix `MB` for different
> brands, so brand became part of the routing key — the table key is now `(prefix, brand)`
> and `resolve_folder`/`load_folder_map` take brand into account (see §7).

## 2. Requirements

**Folder-sync stage (`folder_sync.py`, new)**
- WHEN the stage runs, THEN list the **top-level** folders of `WRIKE_SPACE_ID` and, for
  each whose title contains `" - "`, split on the first `" - "` into `prefix` (left) and
  `brand` (right) and upsert a `wrike_folder_map` row
  `(prefix, wrike_folder_id, full_folder_name, brand, space_id)`. The Wrike folder's v4 `id`
  is stored as its **numeric permalink id** (`to_numeric_id`), consistent with `space_id`.
- WHEN a top-level folder's title does **not** contain `" - "` (no derivable prefix),
  THEN skip it and log a WARNING (it cannot be keyed) — do not guess a prefix.
- WHEN the stage runs, THEN **erase the whole table** (`TRUNCATE wrike_folder_map`) before
  repopulating, so the map is always a faithful mirror of the current space — folders renamed
  or removed in Wrike never leave stale rows, and a changed `WRIKE_SPACE_ID` is handled for
  free (every row is simply replaced).
- WHEN two top-level folders share a `prefix` but differ in `brand` (e.g. `MB - MR BEAST`
  and `MB - MEAT BOARDS`), THEN keep **both** as distinct `(prefix, brand)` rows. Only a true
  duplicate (same prefix **and** brand) is skipped+warned.
- WHEN no top-level folder titled `_PENDING_REVIEW` exists in the space, THEN create it in
  Wrike (`POST /folders/{spaceId}/folders`) during this stage. EITHER way, store the
  `_PENDING_REVIEW` folder as the `'*'` staging row (brand `''`) so the `resolve_folder`
  fallback works unchanged.
- WHEN the stage finishes, THEN return a summary (folders found / inserted / skipped,
  whether `_PENDING_REVIEW` was created) for logging.

**Routing (`mapping.py`, `sync.py`)**
- WHEN resolving a card's folder, THEN `resolve_folder(prefix, brand, folder_map)` is
  **prefix-first with brand as a tiebreaker**: one folder for the prefix → route by prefix
  (brand ignored); several → exact brand match; none / no match → the `'*'` staging folder.
- WHEN loading the map, THEN `load_folder_map` returns `{prefix -> {brand -> folder_id}}`.

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
- WHEN the schema is applied, THEN `wrike_folder_map` has a `brand text NOT NULL DEFAULT ''`
  column and its primary key is the composite `(prefix, brand)` (idempotent migration: add
  the column, then swap the single-column `prefix` key for the composite one).

### Out of Scope

- Any change to `plm_reader.py`, `state.py`, `changes.py`, or the Service Bus
  producer/consumer message flow. (`mapping.py`/`sync.py` *do* change — for brand-aware
  routing — but only at the `resolve_folder`/`load_folder_map`/`_managed_folder_ids` seam.)
- Fuzzy/normalized brand matching — the brand tiebreaker is a **raw exact** string match
  (item brand from Centric `collection` vs the folder-title brand), same philosophy as
  `customer_matches`.
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

The consumer later reads the freshly-populated table via `load_folder_map` /
`resolve_folder` (prefix-first, brand as tiebreaker).

### `wrike_folder_map` row, before → after

| Column | Before (human-seeded) | After (folder-sync owned) |
|---|---|---|
| `prefix` | hand-entered, e.g. `WP` | title left of `" - "`, e.g. `WP` |
| `wrike_folder_id` | numeric permalink or v4 | the folder's **numeric permalink id**, decoded from the API's v4 `id` (`to_numeric_id`), consistent with `space_id` |
| `full_folder_name` | hand-entered | the folder `title` verbatim |
| **`brand`** *(new)* | — | title right of `" - "`, e.g. `Winnie-the-Pooh` |
| `space_id` | hand-entered | `WRIKE_SPACE_ID` |
| `'*'` row | `Pending (staging)` | the `_PENDING_REVIEW` folder (created if absent) |

### Affected Files

| Layer | File | Change |
|-------|------|--------|
| Core logic | `folder_sync.py` | **New.** `sync_folders(conn, client, *, space_id) -> dict`: list top-level folders, wipe-on-space-change, parse `"<PREFIX> - <Brand>"`, upsert rows, ensure `_PENDING_REVIEW` (`'*'` row) |
| Core logic | `wrike_client.py` | Add `list_top_level_folders(space_id)` (resolves a numeric space id → v4 root via `resolve_folder_id`, lists `/folders/{root}/folders`, filters to the root's direct children), `create_folder(parent_folder_id, title)` (resolves parent; top-level ⇒ parent is the `space_id`), and `to_numeric_id(v4_id)` (decodes a v4 id → its numeric permalink id for storage) |
| Core logic | `mapping.py` | `resolve_folder(prefix, brand, folder_map)` prefix-first/brand-tiebreak; `load_folder_map` returns `{prefix -> {brand -> folder_id}}` |
| Core logic | `sync.py` | `process_family` passes `item["brand"]`; `_managed_folder_ids` + `reconcile_folders` flatten the nested map |
| Entry point | `function_app.py` | `plm_wrike_producer`: call `sync_folders` after the Centric refresh, before `sorted_eligible_items`, with the catch-all author's Wrike client |
| Data | `db/schema.sql` | `brand text NOT NULL DEFAULT ''` + composite PK `(prefix, brand)` (idempotent migration: add column, swap the single-column key for the composite); folder sync now owns/repopulates the table; POC seed refreshed |
| Config | `local.settings.json.example` | `WRIKE_SPACE_ID` (already present — comment enriched) |
| Tests | `tests/test_folder_sync.py` (new), `tests/test_wrike_client.py`, `tests/test_mapping.py` | See Tasks 2–6 |
| Unchanged | `plm_reader.py`, `state.py`, `changes.py`, `loader.py`, `centric_client.py` | Engine from 001/002 untouched |

### Key Decisions

- **Folder sync runs in the producer, once per run.** It *writes* the table; the consumer
  *reads* it later in the same run — no concurrency. One Wrike pull per run (the listing is
  unpaginated and a space has only a handful of top-level folders), mirroring how the Centric
  refresh sits at the front of the producer.
- **Identity = the catch-all author.** Listing and *creating* folders is a write whose author
  matters, so the stage uses the `category_author_map` `'*'` row's token (the same default
  identity staging cards are already created under). **Requirement:** that token must be a
  member of `WRIKE_SPACE_ID` with folder-create permission (else `403`).
- **Top-level only, via the root's `childIds`.** The numeric `WRIKE_SPACE_ID` resolves (via
  the existing `resolve_folder_id`) to the v4 root folder; `GET /folders/{root}/folders` returns
  the whole flattened subtree *including the root* (`id == root`). The client keeps only folders
  in the root's `childIds`. (The "not referenced as anyone's child" heuristic is wrong here —
  the root is in the list and claims all top-level folders as children.) Verified live: 88
  top-level folders.
- **Routing: prefix-first, brand as tiebreaker.** A prefix with one folder routes by prefix
  alone (robust — no dependence on brand strings matching); a prefix shared by several brands
  is disambiguated by an **exact** brand match (item brand from Centric `collection` vs the
  folder-title brand). This is why `brand` is part of the key, not audit-only — forced by the
  real `MB - MR BEAST` / `MB - MEAT BOARDS` collision found in live validation.
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

- [x] All checklist tasks done; `python -m pytest` passes (**123 passed**), with **no test
      reaching the live Wrike API** (HTTP mocked).
- [x] `wrike_folder_map` has a `brand text NOT NULL DEFAULT ''` column and a composite
      `(prefix, brand)` primary key; existing `seed_folder_map` inserts (which omit `brand`,
      defaulting to `''`) still work.
- [x] `list_top_level_folders` resolves a numeric space id → v4 root and filters the subtree to
      the root's direct children (root entry + deep descendants excluded), and `create_folder`
      posts the title with the space id as parent; both parse the responses
      (`tests/test_wrike_client.py`).
- [x] `sync_folders` derives `prefix`/`brand` by splitting the title on `" - "`, skips
      non-conforming titles with a warning, **keeps two brands that share a prefix** and skips
      only true `(prefix, brand)` duplicates, rebuilds the table each run, creates
      `_PENDING_REVIEW` only when absent (stored as the `'*'` row), and is idempotent
      (`tests/test_folder_sync.py`).
- [x] `resolve_folder` routes prefix-first with brand tiebreaker; the producer runs folder sync
      after the Centric refresh and before reading items (`tests/test_mapping.py`).
- [x] **Live-sandbox validation:** against the real `WRIKE_SPACE_ID` (numeric `4435490633` →
      v4 root `MQAAAAEIYDdJ`), folder sync listed **88** top-level folders and wrote **88 rows**
      (87 prefix/brand + the `'*'`/`_PENDING_REVIEW` staging row), **0 skipped**, creating no
      Wrike folder (the staging folder already existed). Both `MB` brands stored
      (`MR BEAST`/`MEAT BOARDS`); brand parsing confirmed (e.g. `AA → Auntie Anne's`); folder ids
      stored as **numeric permalink ids** (e.g. `DS → 4487260788`).
- [x] README + `local.settings.json.example` reflect the folder-sync stage and `WRIKE_SPACE_ID`.

## 7. Execution Notes (2026-06-15)

- **Numeric space id resolution (found in live validation):** `WRIKE_SPACE_ID` is a numeric
  permalink id (`4435490633`); the Wrike API rejects numeric ids (400 "Invalid Space ID"). The
  space id IS its root folder id, so `list_top_level_folders`/`create_folder` run it through the
  existing `resolve_folder_id` (numeric → v4 via `/ids?type=ApiV2Folder`; `ApiV2Space` is not a
  valid type) and use `GET /folders/{root}/folders`. Documented in `project_docs/wrike_api.md`.
- **Folder ids stored numeric, not v4 (client request):** the table holds the numeric
  permalink id (like `space_id`), decoded from the API's v4 id by `WrikeClient.to_numeric_id`
  (URL-safe base64 → `[type byte][big-endian numeric]`; Wrike has no v4→numeric API). Verified
  by round-tripping all 89 folders back through `/ids`. `resolve_folder_id` converts the stored
  numeric back to v4 on use (and raises loudly if one is ever unresolvable), so this also
  restores 001's original convention (the POC seed was numeric; `resolve_folder_id` exists for it).
- **Brand became a routing key (mid-execution design change):** live validation found
  `MB - MR BEAST` and `MB - MEAT BOARDS` — two real, distinct brands sharing prefix `MB`. The
  original prefix-only model skipped one (wrong). Per the client, **both must map**, so the key
  became `(prefix, brand)` and routing became prefix-first with brand as the exact-match
  tiebreaker (`resolve_folder(prefix, brand, …)`, `load_folder_map` → `{prefix -> {brand ->
  id}}`). `sync_folders` now keeps both and only skips a true `(prefix, brand)` duplicate.
- **Migration applied to the running DBs:** the idempotent `brand` column + the single-column →
  composite `(prefix, brand)` primary-key swap were run against both `plm` and `plm_test`; the
  suite is green and re-running `db/schema.sql` elsewhere picks it up the same way.
- **Test isolation fixed (incident during execution):** the suite was running against the real
  `plm` DB (conftest used the default `PG_DB`), so `pytest` `TRUNCATE`d live sync state. `conftest`
  now defaults `PG_DB=plm_test` and **hard-fails** if it ever lands on `plm`. The polluted `plm`
  state (synthetic fixture rows) was cleared so a fresh Centric producer run is the source of
  truth (`wrike_folder_map` + `category_author_map` kept).
- **Files touched:** `db/schema.sql`, `wrike_client.py`, `mapping.py`, `sync.py`,
  `folder_sync.py` (new), `function_app.py`, `local.settings.json.example`,
  `project_docs/wrike_api.md` (verified-live corrections), `README.md`, plus
  `tests/conftest.py`, `tests/test_wrike_client.py`, `tests/test_folder_sync.py`,
  `tests/test_mapping.py`.
