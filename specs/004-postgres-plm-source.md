# 004 Postgres PLM Database as the Source

**Status:** draft
**Created:** 2026-06-15
**Last updated:** 2026-06-15

> Supersedes **002** (Centric 8 *API* as the source). The mirror architecture from 002 is
> unchanged — `plm_item` is still a refreshed-then-read cache and the producer/Service Bus/
> consumer/reconcile engine from 001 is untouched. This spec only swaps *where the refresh
> reads from*: from the Centric REST API to a **Postgres PLM database** (the `centric_8_plm`
> schema, populated by Airbyte from Centric). Read 002 first for the source seam this replaces.

---

## 1. Overview

The producer's refresh currently pulls ready-for-Wrike *styles* from the live Centric 8 REST
API (`centric_client.py`) and upserts them into the `plm_item` mirror. This spec replaces that
with a **direct read of the upstream Postgres PLM database**, where the Centric data already
lands (schema `centric_8_plm`, tables `styles` + reference tables `category1s`, `category2s`,
`collections`, `seasons`). One SQL query — with the reference JOINs that the API client used to
do via per-id `resolve_ref` calls — returns the ready set; the refresh maps each row into a
`plm_item` record and upserts it exactly as before. **This source database has its own
credentials, separate from the app/state Postgres**, so it needs a second connection.

## 2. Requirements

**Source reader (`plm_source.py`, new)**
- WHEN `connect_source()` is called, THEN open a psycopg connection to the **source** PLM
  Postgres using `PLM_SOURCE_PG_CONN` (or the `PLM_SOURCE_PG_*` component vars) — distinct
  from `db.connect()`, which stays pointed at the app/state database.
- WHEN `read_ready_styles(source_conn, since)` runs, THEN execute the JOIN query (§3) against
  `centric_8_plm`, returning one dict per ready style with the reference fields already
  resolved to their `node_name`. WHEN `since` is at/<= EPOCH, THEN return the full ready set
  (first-run backfill); otherwise add `AND s._modified_at > since` (the delta).

**Refresh / mapping (`loader.py`, refocused API → Postgres)**
- WHEN the refresh runs, THEN map each source row to a `plm_item` record via the §3 field map
  and upsert (idempotent, keyed on `plm_internal_id`) via the existing `load_plm_items`.
- WHEN mapping a row, THEN the reference fields (`customer` ← `category_2`, `product_category`
  ← `category_1`, `brand` ← `collection`, `season` ← `parent_season`) come straight from the
  JOINed `node_name` columns — **no `resolve_ref` step** (the SQL does the resolution).
- WHEN deriving `family_id`/`prefix`/`code`/`title`/`description`/`contents`, THEN reuse the
  existing logic unchanged (`item_prefix`, `item_number[:8]`, `strip_html`, `build_description`).

**Producer wiring (`function_app.py`)**
- WHEN the producer timer fires, THEN open the source connection, refresh the `plm_item` mirror
  from it (`since` = current watermark), and close it — *before* the folder sync + delta read.
  Everything downstream (folder sync, enqueue one message per canonical item, advance watermark)
  is unchanged.

**Configuration / secrets**
- WHEN deployed, THEN the source DB credentials come from a new Key Vault secret
  (`PLM-SOURCE-PG-CONN`) referenced by the `PLM_SOURCE_PG_CONN` app setting; the obsolete
  `CENTRIC-*` secrets and `CENTRIC_*` app settings are removed. See §3 Key Vault changes.

### Out of Scope

- Any change to `plm_reader.py`, `sync.py`, `state.py`, `mapping.py`, `wrike_client.py`,
  `changes.py`, `folder_sync.py`, the Service Bus producer/consumer flow, or `db/schema.sql` —
  the engine and schema from 001 are untouched (`plm_item` already has every needed column).
- The consumer or reconciliation reading the source DB directly — the source is touched in
  exactly one place (the producer's refresh). The consumer still re-reads `plm_item` by
  `item_number` from the app database.
- Keeping the Centric API client. `centric_client.py` becomes dead code and is deleted (git
  keeps history); the Centric API project doc is retained for historical reference only.
- Delete/deactivate propagation when a style leaves the ready set in the source (no delete
  propagation — consistent with 001/002).
- Two-way sync, change-data-capture/event triggers, or reading any schema other than
  `centric_8_plm`.

## 3. Design

### Field map: source row → `plm_item`

The query aliases its columns to near-final names, so the mapper reads the **query alias**
(the row-dict key), not the raw source column. Deltas from 002 marked **⚠**.

| `plm_item` field | Source (query alias) | Notes |
|---|---|---|
| `plm_internal_id` | `id` | upsert key — **⚠ must add `s.id` to the SELECT** (the query omits it) |
| `item_number` | `item_number` | |
| `item_name` | `item_name` | |
| `family_id` / `prefix` / `code` | derived from `item_number` | reuse `item_prefix` + `item_number[:8]` |
| `print_method` | `mgf_print_method` | **⚠** JSON-array text, e.g. `["MATTE & SPOT FOIL"]` → `json.loads` then `", ".join` |
| `previous_item_no` | `previous_item_number` | |
| `contents` | `contents` | strip HTML (unchanged) |
| `material_codes` | `material_codes` | source col `mgf_material_code_item` — same as 002 |
| `brand_category` | `brand_category` | |
| `customer` | `customer` | **⚠** pre-resolved by JOIN (`category2s.node_name`); was `resolve_ref('/category2s')` |
| `product_category` | `product_category` | **⚠** pre-resolved by JOIN (`category1s.node_name`); drives author mapping |
| `brand` | `brand` | **⚠** pre-resolved by JOIN (`collections.node_name`) |
| `season` | `season` | **⚠** pre-resolved by JOIN (`seasons.node_name` via `parent_season`) |
| `design_request` | `design_request` | |
| `image_link` | `mgf_image_link` | |
| `description` | **formula** from `material_codes` + `contents` | `build_description()` — unchanged |
| `title` | `item_number[:8] + " " + item_name` | unchanged |
| `ready_for_wrike` | `mgf_ready_for_wrike` | **⚠** text `'true'`/`'false'` via `#>> '{}'` → compare `== 'true'` (**not** `bool()`, which is truthy for any non-empty string); SQL `WHERE` gates it, so always `true` in the mirror |
| `modified_at` | `_modified_at` | the watermark — **⚠** ISO 8601 **string** (`2026-06-12T11:22:55.697Z`), parse with `_parse_centric_timestamp` (handles `Z` + ms); SQL casts `::timestamptz` for the delta |
| `created_at` | `_modified_at` | source has no creation date; reuse `_modified_at` (only feeds `pick_canonical`'s rare tiebreak) |
| `design_brief`, `status`, `priority`, `end_date`, `folder`, `workflow`, `custom_status` | — | blank / `None`, unchanged |

### The source query (`plm_source.read_ready_styles`)

The user's corrected query, plus `s.id` (the upsert key) and the delta filter, and with the
unused `original_season`/`os` JOIN dropped (it was never selected). The `#>> '{}'` operator
extracts a JSONB value as text at the root path — the source stores Centric fields as JSONB.

```sql
SELECT
  s.id,
  s.mgf_item_identifier      #>> '{}' AS item_number,
  s.node_name                         AS item_name,
  s.mgf_print_method         #>> '{}' AS mgf_print_method,
  s.mgf_previous_item_number #>> '{}' AS previous_item_number,
  s.mgf_test_material        #>> '{}' AS contents,
  s.mgf_material_code_item   #>> '{}' AS material_codes,
  s.mgf_brand_category_2     #>> '{}' AS brand_category,
  c2.node_name                        AS customer,
  c1.node_name                        AS product_category,
  col.node_name                       AS brand,
  ps.node_name                        AS season,
  s.mgf_item_description     #>> '{}' AS design_request,
  s.mgf_image_link           #>> '{}' AS mgf_image_link,
  s.mgf_ready_for_wrike      #>> '{}' AS mgf_ready_for_wrike,
  s._modified_at
FROM centric_8_plm.styles s
LEFT JOIN centric_8_plm.category2s  c2  ON c2.id  = s.category_2
LEFT JOIN centric_8_plm.category1s  c1  ON c1.id  = s.category_1
LEFT JOIN centric_8_plm.collections col ON col.id = s.collection
LEFT JOIN centric_8_plm.seasons     ps  ON ps.id  = s.parent_season
WHERE s.mgf_ready_for_wrike #>> '{}' = 'true'
  AND s._modified_at::timestamptz > %(since)s   -- delta: appended only when since > EPOCH
ORDER BY s._modified_at::timestamptz, item_number;
```

`_modified_at` is stored as an ISO 8601 **string** (`2026-06-12T11:22:55.697Z` — the `T`/`Z`
form a native `timestamptz` never renders), so the delta filter casts it with `::timestamptz`
to compare against the `datetime` watermark param (a bare `string > datetime` comparison would
error). If the column turns out to be `jsonb` rather than `text`, the cast becomes
`(s._modified_at #>> '{}')::timestamptz`.

### Affected Files

| Layer | File | Change |
|-------|------|--------|
| Core logic | `plm_source.py` | **New.** `connect_source()` (psycopg, `PLM_SOURCE_PG_*`, mirrors `db.py`) + `read_ready_styles(source_conn, since)` running the §3 query with `dict_row`. The only module that names the source schema. |
| Core logic | `loader.py` | **Refocus.** `style_to_plm_item(row, *, now)` — drop the `resolve` param, take pre-resolved (aliased) fields, `json.loads`+join the `print_method` array text, compare `ready_for_wrike == 'true'`. `refresh_plm_items(conn, source_conn, *, since, now)` — call `read_ready_styles`, map, upsert. Drop `_format_centric_timestamp` (the delta now casts in SQL); keep `_parse_centric_timestamp` (still gets a `str`), `item_prefix`, `PLM_ITEM_COLUMNS`, `load_plm_items`, `strip_html`. |
| Entry point | `function_app.py` | `plm_wrike_producer`: replace `make_centric_client()` + `refresh_plm_items(conn, client, …)` with `with connect_source() as src: refresh_plm_items(conn, src, since=watermark, now=now)`. No other function changes. |
| Delete | `centric_client.py`, `tests/test_centric_client.py` | Dead once the source is Postgres. |
| Config | `local.settings.json(.example)` | Add `PLM_SOURCE_PG_CONN` (+ optional `PLM_SOURCE_PG_*`); remove `CENTRIC_*`. |
| Tests | `tests/test_centric_loader.py` (rename → `test_loader.py`), `tests/fixtures_mapping.py`, `tests/test_reader.py` | Update the style-dict shape to pre-resolved fields; drop the resolver. See Task 5. |
| Docs | `project_docs/centric_8_plm_source.md` (new), `README.md` | Document the source schema/query/connection; update README source section. |
| Unchanged | `plm_reader.py`, `sync.py`, `state.py`, `mapping.py`, `wrike_client.py`, `changes.py`, `folder_sync.py`, `description.py`, `db.py`, `db/schema.sql` | 001/002 engine + schema untouched. |

### Key Decisions

- **Keep the mirror; swap only the refresh source.** `plm_item` stays a refreshed-then-read
  cache (002's decision). The alternative — repointing `read_changed_items`/`read_one_item`
  directly at the source schema (the original deployment-guide §8 idea) — is **rejected**: it
  would put the source DB on the per-message consumer path and in reconciliation, undoing 002's
  isolation of source flakiness to one refresh step. Surgical change: one new module + a
  refocused `loader` + a two-line producer swap.
- **Separate connection, not a second schema on the app DB.** The source has its own
  credentials, so `plm_source.connect_source()` is independent of `db.connect()`. Mirroring
  `db.py`'s env-var pattern (`PLM_SOURCE_PG_CONN` or components) keeps the two connections
  symmetrical and readable.
- **Resolution moves from the client to SQL.** The API client resolved `category_1/2`,
  `collection`, `parent_season` with cached per-id GETs; the source query does it with JOINs.
  This deletes `resolve_ref` and the entire `centric_client.py`.
- **`since` filter lives in the query**, not client-side — Postgres filters the delta directly
  (`s._modified_at::timestamptz > since`; the column is an ISO string, so it casts), replacing
  the API's `modified_after` string formatting (so `_format_centric_timestamp` is deleted).

### Key Vault / app-settings changes (answers "what changes in Key Vault")

Secret names use hyphens; app-setting/env names use underscores (existing convention — see
`project_docs/client_deployment_runbook_2026-06-04.md`).

**Add** one secret + one reference:
```bash
az keyvault secret set --vault-name $KV -n PLM-SOURCE-PG-CONN \
  --value "host=<src-host> port=5432 dbname=<src-db> user=<src-user> password=<src-pw> sslmode=require"
# then add the app setting (Key Vault reference):
#   PLM_SOURCE_PG_CONN = @Microsoft.KeyVault(SecretUri=https://$KV.vault.azure.net/secrets/PLM-SOURCE-PG-CONN/)
```
**Remove** the now-unused Centric secrets + app settings: `CENTRIC-BASE-URL`,
`CENTRIC-USERNAME`, `CENTRIC-PASSWORD` (secrets) and `CENTRIC_BASE_URL`, `CENTRIC_USERNAME`,
`CENTRIC_PASSWORD`, `CENTRIC_API_VERSION` (app settings).
**Unchanged:** `PG-CONN` (the app/state DB), the Wrike tokens, and the Service Bus secrets.
> The Function App's managed identity already has Key Vault *Get/List*; the new reference
> resolves with no further grant. If the source DB is private (VNet), confirm the Function App
> has network reachability to it (same consideration as `PG_CONN`).

## 4. Reference Documents

> **Rule:** read these before coding; do not duplicate them in the spec.

| Document | Location | What to look for |
|----------|----------|------------------|
| Centric API source (superseded) | `specs/002-centric-8-api-source.md` | The refresh/mirror seam being replaced; the field map this evolves from |
| As-built engine | `specs/001-plm-to-wrike-sync.md` | Producer/consumer/reconcile flow; `plm_item` schema + canonical rule |
| App DB connection pattern | `db.py` | `pg_conninfo()`/`connect()` env-var shape to mirror in `plm_source.py` |
| Source schema (to create) | `project_docs/centric_8_plm_source.md` | `centric_8_plm` tables, the JSONB `#>> '{}'` quirk, `_modified_at` type, `print_method` shape |
| Key Vault / app settings | `project_docs/client_deployment_runbook_2026-06-04.md` | Secret-vs-env naming, `@Microsoft.KeyVault(...)` reference form |
| Testing / style / logging | `agent_docs/testing_guidelines.md`, `agent_docs/style_guidelines.md`, `agent_docs/logging_guidelines.md` | Fixture patterns, naming, log levels |

## 5. Implementation Checklist

> **RESOLVED — `mgf_print_method` shape.** Stored as a JSON array; `#>> '{}'` returns its
> serialized text (e.g. `["MATTE & SPOT FOIL"]`, `["MATTE"]`). The mapper `json.loads` it and
> `", ".join`s the elements — so a multi-element array becomes `"MATTE, FOIL"` and a single
> element passes through unchanged.

> **RESOLVED — material codes.** The corrected query uses `mgf_material_code_item` (aliased
> `material_codes`) — the same column as 002. No change.

> **RESOLVED — `original_season`.** Dropped from the SELECT (and its dead `os` JOIN removed).
> `plm_item` / `db/schema.sql` / `mapping.py` stay untouched.

> **RESOLVED — `_modified_at`.** An ISO 8601 *string* (`2026-06-12T11:22:55.697Z`, shown
> without JSON quotes → a `text` column), not a native `timestamptz` — so
> `_parse_centric_timestamp` stays (psycopg returns a `str`) and the SQL delta casts
> `s._modified_at::timestamptz`.

> **DECISION — `active` gate dropped for now.** `styles` has an `active` column, but the query
> does **not** gate on it (per the user, 2026-06-15). The mirror may therefore include
> inactive-but-ready styles — a deliberate widening vs. 002, which re-checked `active`
> client-side. Re-add `AND s.active … = 'true'` to the `WHERE` if inactive rows become noise.

- [x] **Task 0: Write the source-DB doc.**
      Files: `project_docs/centric_8_plm_source.md`
      Details: Document the `centric_8_plm` schema, the §3 query, the `#>> '{}'` JSONB quirk
      (incl. the `print_method` array text and the ISO-string `_modified_at`), and the
      connection. (`active` is intentionally not gated — see the decision note.)

- [x] **Task 1: Source reader.** *(depends on: Task 0)*
      Files: `plm_source.py`
      Details: `connect_source()` (psycopg, `PLM_SOURCE_PG_CONN` or `PLM_SOURCE_PG_*`, mirroring
      `db.pg_conninfo`/`connect`); `read_ready_styles(source_conn, since)` running the §3 query
      with `dict_row`, appending the `_modified_at > since` delta only when `since > EPOCH`.
      Test: query construction (delta appended vs omitted at EPOCH) against a small seeded
      `centric_8_plm`-shaped fixture, or a unit test of the SQL-builder branch.

- [x] **Task 2: Mapper + refresh refocus.** *(depends on: Tasks 0, 1)*
      Files: `loader.py`
      Details: `style_to_plm_item(row, *, now)` — drop `resolve`; pre-resolved (aliased) fields;
      `json.loads`+join `print_method`; `ready_for_wrike == 'true'`.
      `refresh_plm_items(conn, source_conn, *, since, now)` — `read_ready_styles` → map → upsert.
      Delete `_format_centric_timestamp`; simplify `_parse_centric_timestamp` per the type decision.
      Test: each field maps (incl. pre-resolved refs, HTML strip, customer, title, blanks).

- [x] **Task 3: Wire into the producer.** *(depends on: Tasks 1, 2)*
      Files: `function_app.py`
      Details: Replace the Centric client build + refresh with
      `with connect_source() as src: refresh_plm_items(conn, src, since=watermark, now=now)`.
      Drop the `centric_client` import. Everything downstream unchanged.

- [x] **Task 4: Delete dead Centric code + config.** *(depends on: Task 3)*
      Files: delete `centric_client.py`, `tests/test_centric_client.py`; edit
      `local.settings.json.example` (add `PLM_SOURCE_PG_CONN`, remove `CENTRIC_*`).

- [x] **Task 5: Tests.** *(depends on: Tasks 1, 2)*
      Files: `tests/test_centric_loader.py` → `tests/test_loader.py`, `tests/fixtures_mapping.py`,
      `tests/test_reader.py`
      Details: Update the style-dict fixtures to the pre-resolved shape (drop `_identity_resolve`
      / `_resolve`); keep the 11-item topology. New `plm_source` test (Task 1). No live DB in
      unit tests (mapper tested with plain dicts; source read tested against a seeded fixture or
      mocked).

- [x] **Task 6: Docs + Key Vault runbook.** *(depends on: Tasks 3, 4)*
      Files: `README.md` (via readme-manager), `project_docs/centric_8_plm_source.md`, the Key
      Vault note in the deployment runbook.
      Details: README source section → Postgres source; document the `PLM_SOURCE_PG_CONN` secret
      and the `CENTRIC_*` removal.

## 6. Acceptance Criteria

- [ ] All tests pass (`python -m pytest`) against the isolated `plm_test` DB, with **no test
      reaching a live source/app DB or the Centric API**.
- [ ] `read_ready_styles` runs the §3 query: full ready set at EPOCH, `_modified_at > since`
      delta otherwise; reference fields arrive pre-resolved.
- [ ] `style_to_plm_item` produces the §3 field map from the pre-resolved row shape (no
      `resolve_ref`); `family_id`/`prefix`/`code`/`title`/`description`/`contents` unchanged.
- [ ] The producer refreshes `plm_item` from the **source Postgres** before the delta read;
      `centric_client` is deleted and imported nowhere.
- [ ] **Live validation:** a real refresh against the source DB pulls the ready set into
      `plm_item`; the producer enqueues and the consumer creates/updates cards unchanged.
- [ ] `PLM-SOURCE-PG-CONN` Key Vault secret + `PLM_SOURCE_PG_CONN` reference added; `CENTRIC_*`
      secrets/settings removed; `PG-CONN` and Wrike/Service Bus secrets unchanged.
- [ ] README + `project_docs/centric_8_plm_source.md` reflect the Postgres source.
```
