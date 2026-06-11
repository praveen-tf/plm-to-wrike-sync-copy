# 002 Centric 8 API as the PLM Source

**Status:** implemented 2026-06-11 (Option B — Excel-removal follow-up deferred)
**Created:** 2026-06-11
**Last updated:** 2026-06-11

> Builds on **001** (the as-built PLM → Wrike sync). 001 is unchanged by this spec
> except for *where `plm_item` rows come from*. Read 001 first for the engine this
> feeds.

---

## 1. Overview

Today `plm_item` (the sync source) is loaded from an Excel `Wrike` sheet / synthetic
CSV by `loader.py`. This spec replaces that source with the **live Centric 8 PLM REST
API**: a new Centric client fetches product *styles* and a refresh step maps each into
a `plm_item` row and upserts it into Postgres. Postgres stays as both the **state**
store (watermark, task map, folder/author maps, dead-letter, review log — all 001) and
a **mirror** of the Centric source. The producer/Service Bus/consumer/reconcile engine
from 001 reads that mirror exactly as before — none of it changes.

## 2. Requirements

**Centric client (`centric_client.py`)**
- WHEN the client is created, THEN authenticate via `POST /session` with the configured
  username/password and send the returned token as the `Cookie` header on every later
  request.
- WHEN listing styles, THEN page with `skip`/`limit` (page size 200) until a page returns
  fewer than `limit` rows, and accept an optional `modified_after` filter for delta pulls.
- WHEN a response returns HTTP 500 but carries a valid JSON body, THEN trust the body
  (do not treat it as a failure). WHEN a request times out or returns 503, THEN retry
  with capped exponential backoff. (Sandbox quirks — see `project_docs/centric_8_api.md`.)
- WHEN a reference id (`category_1`, `category_2`, `collection`) is resolved, THEN fetch
  the entity (`/category1s/{id}`, `/category2s/{id}`, `/collections/{id}`) and return its
  `node_name` (fallback `code`), cached per run. WHEN the id is blank or starts with
  `centric%3A`, THEN resolve to empty string without a request.

**Refresh / mapping (`loader.py`, refocused Centric → `plm_item`)**
- WHEN the refresh runs with `since`, THEN fetch the **ready-for-Wrike** styles
  (`mgf_ready_for_wrike=true`) modified at/after `since` (re-checking `active` client-side),
  map each to a `plm_item` record, and upsert them (idempotent, keyed on `plm_internal_id`).
  The fetch is ready-only — the fast path (≈11s for the ready set vs >9min for the whole
  active catalogue on the sandbox), so the mirror is ready-only (see §3 Key Decisions for the
  reconciliation tradeoff).
- WHEN mapping a style, THEN apply the field map in §3 (incl. `customer` ← `category_2`
  resolved, `title` ← `item_number[:8] + " " + node_name`, `modified_at` ← `_modified_at`,
  a formula-built `description`), reusing the existing `item_number`-derived `family_id`/
  `prefix`/`code` logic.

**Producer wiring (`function_app.py`)**
- WHEN the producer timer fires, THEN refresh the `plm_item` mirror from Centric
  (`since` = current watermark) *before* reading the delta; the rest of the producer
  (delta → enqueue one message per canonical family → advance watermark) is unchanged.
- WHEN the first run sees no watermark (EPOCH), THEN the refresh pulls the full ready set
  (one-time backfill); subsequent runs pull only the `modified_after` delta.

### Out of Scope

- Any change to `plm_reader.py`, `sync.py`, `state.py`, `mapping.py`, `wrike_client.py`,
  `changes.py`, the Service Bus producer/consumer flow, or `db/schema.sql` — the engine
  and schema from 001 are untouched (`plm_item` already has every needed column).
- The consumer or reconciliation calling Centric directly — Centric is touched in exactly
  one place (the producer's refresh). The consumer still re-reads `plm_item` by id.
- Deleting / deactivating mirror rows when a style goes inactive or is removed in Centric
  (no delete propagation — consistent with 001's no-card-deletion stance).
- Season resolution (`/seasons` is broken on the sandbox — `season` stays blank).
- Two-way sync, webhooks/event-driven pulls, or season-scoped fetching.

## 3. Design

### Field map: Centric style → `plm_item`

| `plm_item` field | Source | Notes |
|---|---|---|
| `plm_internal_id` | `id` | Centric style id (upsert key) |
| `item_number` | `mgf_item_identifier` | |
| `item_name` | `node_name` | |
| `family_id` / `prefix` / `code` | derived from `item_number` | reuse `item_prefix` + `item_number[:8]` |
| `print_method` | `mgf_print_method` | list → join `", "` |
| `previous_item_no` | `mgf_previous_item_number` | |
| `contents` | `mgf_test_material` | strip HTML |
| `material_codes` | `mgf_material_code_item` | |
| `brand_category` | `mgf_brand_category_2` | |
| `customer` | `category_2` → resolve `/category2s` | e.g. `*CORE` / `*CUSTOM`; drives `pick_canonical` + the create-only "Customer" Wrike field |
| `product_category` | `category_1` → resolve `/category1s` | drives author mapping |
| `brand` | `collection` → resolve `/collections` | |
| `design_request` | `mgf_item_description` | |
| `image_link` | `mgf_image_link` | |
| `description` | **formula**: section-built from `material_codes` + `contents` | new `build_description()` (§ below) |
| `title` | `item_number[:8] + " " + node_name` | |
| `ready_for_wrike` | `mgf_ready_for_wrike` | eligibility gate (column, not a fetch filter) |
| `modified_at` | `_modified_at` (parsed to timestamptz) | the watermark — Centric's *only* timestamp |
| `created_at` | `modified_at` (the parsed `_modified_at`) | the API has **no** creation timestamp (confirmed: only `_modified_at`); reuse the real watermark value, not a dummy. Only feeds `pick_canonical`'s rare no-CORE/no-CUSTOM tiebreak |
| `design_brief`, `season`, `status`, `priority`, `end_date`, `folder`, `workflow`, `custom_status` | blank / `None` | not sourced; `None` → omitted from the Wrike payload / Wrike defaults |

### Affected Files

| Layer | File | Change |
|-------|------|--------|
| Core logic | `centric_client.py` | **New.** `CentricClient` (session auth, `list_styles(modified_after)`, cached `resolve_ref`) + `make_centric_client()` factory. `requests`-based, mirroring `wrike_client.py`, plus Centric quirks (trust-500-body, retry-on-timeout, `Cookie` auth) |
| Core logic | `loader.py` | **Refocus.** Remove the Excel half (`read_source_rows`, `_read_excel_wrike_sheet`, `_read_csv`, `COLUMN_MAP`, `to_plm_item`). Add `style_to_plm_item(style, resolve, *, now)` and `refresh_plm_items(conn, client, *, since, now)`. Keep `item_prefix`, `PLM_ITEM_COLUMNS`, `load_plm_items` (the generic upsert) |
| Helpers | `description.py` | Add `build_description(material_codes, contents)` — builds the initial Material Codes / Contents `<h5>` sections for a new card (`merge_description` only *replaces* sections, it can't build them) |
| Entry point | `function_app.py` | `plm_wrike_producer`: refresh the mirror from Centric (`since` = watermark) before `sorted_eligible_families`. No other function changes |
| Config | `local.settings.json(.example)` | `CENTRIC_*` already present; add optional `CENTRIC_API_VERSION` (default `v2`) |
| Deps | `requirements.txt` | Drop `openpyxl` (Excel loader retired). No new runtime dep — use `requests` (already present), **not** the notebook's `httpx`/`pandas`/`dotenv` |
| Data | `seed_plm_item.sql`, `data/input/*` | Obsolete once Centric feeds `plm_item`; remove (git keeps history) |
| Tests | `tests/test_centric_client.py` (new), `tests/test_loader.py` (rewrite), `tests/fixtures_mapping.py`, `tests/test_reader.py`, `tests/test_description.py` | See Task 6 |
| Unchanged | `plm_reader.py`, `sync.py`, `state.py`, `mapping.py`, `wrike_client.py`, `changes.py`, `db/schema.sql` | Engine + schema from 001 untouched |

### Key Decisions

- **Mirror, not direct read.** `plm_item` stays as a Centric-fed cache. This keeps 001's
  "message = pointer, consumer re-reads by id" design intact and isolates Centric's flakiness
  (slow spells, spurious 500s) to a single refresh step instead of the per-message path.
- **Refresh inside the producer.** One Centric pull per producer run, `since` = the
  current watermark, so the refresh and the existing `modified_at > watermark` delta select
  the same rows. EPOCH on the first run ⇒ automatic full backfill.
- **Fetch ready-only (`mgf_ready_for_wrike=true`), not all-active.** Validated on the live
  sandbox: the ready set (68 → 24 families) refreshes in **~11s**; the full active+inactive
  catalogue took **>9min** (dominated by per-id reference resolution over thousands of
  styles). Ready-only is the sync's actual need and avoids any function-timeout risk.
  *Tradeoff (accepted, option a):* the mirror is therefore ready-only, so `reconcile_folders`
  — which matches Wrike cards against `read_item_customer_pairs` *including not-ready records*
  — may over-report a not-ready card as unmapped (noise in the human-reviewed log). Revisit
  if reconciliation needs not-ready coverage (it would then do its own broader read).
- **`requests`, mirroring `wrike_client`.** Same retry/backoff shape; Centric-specific
  additions are trust-JSON-body-over-500 and retry-on-timeout. No new dependency.
- **`customer` ← resolved `category_2`.** Centric's "Collection" reference holds the
  `*CORE`/`*CUSTOM` value, so `pick_canonical` (CORE-over-CUSTOM, latest-updated) is still
  meaningful when variants share a `family_id`.
- **Timestamps.** Centric exposes only `_modified_at` — no creation date (confirmed against
  the spec + a live record). `modified_at` ← `_modified_at` (the watermark); `created_at` ←
  the same `_modified_at` (a real timestamp, not a dummy), since it only feeds
  `pick_canonical`'s rare fallback. Keeps the engine's column names and leaves
  `load_plm_items` + the schema untouched — no rename to `centric_modified_at`/`first_seen_at`,
  which would ripple through the otherwise-untouched 001 engine and tests.

## 4. Reference Documents

> **Rule:** read these before coding; do not duplicate them in the spec.

| Document | Location | What to look for |
|----------|----------|------------------|
| Centric 8 API | `project_docs/centric_8_api.md` | `POST /session` auth + `Cookie` token, `GET /styles` skip/limit, `modified_after` format `yyyy/mm/ddThh:mm:ss` (assume UTC — confirm on sandbox), ref-resolution endpoints, the 500-with-body / slow-server quirks |
| As-built engine | `specs/001-plm-to-wrike-sync.md` | The producer/consumer/reconcile flow this feeds; the `plm_item` schema + canonical rule |
| Wrike client pattern | `wrike_client.py` | Session + capped-backoff `_request` shape to mirror in `centric_client.py` |
| Testing guidelines | `agent_docs/testing_guidelines.md` | Fixture patterns; mocking HTTP without hitting the live sandbox |
| Style / logging | `agent_docs/style_guidelines.md`, `agent_docs/logging_guidelines.md` | Naming, log levels |

> **Note (confirm on sandbox during Task 1):** format `modified_after` as the standard
> `yyyy/mm/ddThh:mm:ss` (year/month/day) — the project doc's `yyyy/dd/mm` was a typo from
> the 2017 guide, now corrected. Assume UTC; confirm the timezone against the live server
> when wiring the watermark and update the project doc if it differs.

## 5. Implementation Checklist

> **DECISION NEEDED (2026-06-11, during execution):** The Excel readers are imported by
> **5 test files** (`test_sync`, `test_reader`, `test_loader`, `test_db_load`,
> `fixtures_mapping`); `test_sync`/`test_reader` build their 11-family scenario by reading
> the real `.xlsx`/`.csv` directly, not via `plm_row`. So Task 6's "tests pass unchanged" is
> wrong — deleting the Excel half forces rewriting all five. Two sequencings:
> **(A) Full removal now** — rewrite the five test files onto Centric `plm_row` fixtures
> (reproduce the 11-family topology), delete Excel readers + `data/input` + `openpyxl`.
> **(B) Defer deletion (recommended)** — ship the Centric client + refresh + producer wiring
> + new tests now; keep the Excel readers/data as *test-only* scaffolding so the 78 existing
> tests stay green; delete them in a follow-up once the Centric path is validated on the live
> sandbox (where `_modified_at`/`modified_after` format is still unverified).
> **Chosen: B (2026-06-11).** Tasks 3/5/6 below are amended for B: nothing is deleted this
> round; the Excel half of `loader.py`, `data/input/*`, and `openpyxl` stay. A follow-up spec
> handles their removal after live validation.

- [x] **Task 1: Centric client.**
      Files: `centric_client.py`, `tests/test_centric_client.py`
      Details: `CentricClient` — `POST /session` auth (token as `Cookie`); `list_styles(modified_after=None, **filters)`
      paginating `skip`/`limit`=200 over `GET /styles` (filters passed through, e.g.
      `mgf_ready_for_wrike="true"`) until a short page; cached
      `resolve_ref(endpoint, id)` returning `node_name`/`code` (blank/`centric%3A` → `""`). `requests`
      session with capped backoff like `wrike_client`, plus: parse the JSON body even on 500, retry on
      timeout/503, ~90s timeout. `make_centric_client()` reads `CENTRIC_BASE_URL/USERNAME/PASSWORD`
      (+ optional `CENTRIC_API_VERSION`).
      Ref: `project_docs/centric_8_api.md`.
      Test: auth sets the `Cookie`; pagination loops then stops on a short page; `resolve_ref` caches
      and short-circuits placeholders; a 500-with-body is used, a timeout retries — all against a mocked
      transport, never the live API.

- [x] **Task 2: Description builder.** *(independent)*
      Files: `description.py`, `tests/test_description.py`
      Details: `build_description(material_codes, contents)` emits the initial
      `<h5>Material Codes:     </h5>…<br><h5>Contents:     </h5>…` sections in the exact header format
      `merge_description` matches (see the live-card format in `tests/fixtures_mapping.py`).
      Test: output round-trips through `merge_description` (a later update replaces the same sections).

- [x] **Task 3: Centric → `plm_item` mapper + refresh.** *(depends on: Tasks 1, 2)*
      Files: `loader.py`, `tests/test_centric_loader.py` (new)
      Details: `style_to_plm_item(style, resolve, *, now)` applies the §3 field map (resolving
      `category_1/2`, `collection`; joining `print_method`; stripping HTML from `contents`; deriving
      `family_id`/`prefix`/`code`; `customer` ← resolved `category_2`; `title` ← `item_number[:8] + " " +
      node_name`; `description` ← `build_description(...)`; `modified_at`/`created_at` ← parsed
      `_modified_at`; `ready_for_wrike` ← `mgf_ready_for_wrike`). `refresh_plm_items(conn, client, *,
      since, now)` lists ready styles (`mgf_ready_for_wrike="true"`, `modified_after=since`),
      re-checks `active` client-side, maps, and upserts via `load_plm_items`.
      **(Option B)** KEEP the Excel readers + `COLUMN_MAP` + `to_plm_item` as test-only scaffolding; add
      the new functions alongside them.
      Test (`tests/test_centric_loader.py`): each field maps correctly (incl. ref resolution, HTML
      strip, customer, title, blanks); refresh requests the ready filter, skips inactive, upserts, and is
      idempotent.

- [x] **Task 4: Wire refresh into the producer.** *(depends on: Tasks 1, 3)*
      Files: `function_app.py`
      Details: In `plm_wrike_producer`, after reading the watermark, build a Centric client and call
      `refresh_plm_items(conn, client, since=watermark, now=now)` before `sorted_eligible_families`.
      Everything downstream (enqueue, watermark advance) unchanged.
      Test: covered end-to-end via the reader/sync suites against the refreshed mirror (no live API).

- [x] **Task 5: Config.** *(depends on: Task 1)*
      Files: `local.settings.json.example`
      Details: Document `CENTRIC_*` (+ optional `CENTRIC_API_VERSION`) in the example settings.
      **(Option B)** `openpyxl` stays in `requirements.txt`; `seed_plm_item.sql` + `data/input/*` are
      kept (test scaffolding) — their removal is deferred to the follow-up.

- [x] **Task 6: Tests.** *(depends on: Tasks 1, 2, 3)*
      **(Option B)** No rewrite of the existing suite — the Excel scaffolding stays, so `test_sync`,
      `test_reader`, `test_loader`, `test_db_load`, and `fixtures_mapping` are untouched and stay green.
      New coverage lives in `tests/test_centric_client.py` (Task 1), `tests/test_centric_loader.py`
      (Task 3), and a `build_description` case in `tests/test_description.py` (Task 2).

- [x] **Task 7: README.** *(depends on: Tasks 4, 5)*
      Files: `README.md` (via readme-manager)
      Details: Documented the Centric source + refresh-in-producer flow and the `CENTRIC_*` settings.
      Wording corrected to **ready-for-Wrike** styles (not all-active) after the ready-only decision.

## 6. Acceptance Criteria

- [x] All tests pass (`python -m pytest`) — **110 passed** (78 existing + new Centric/mapper/desc),
      run against an isolated `plm_test` DB, with **no test reaching the live Centric API** (HTTP mocked).
- [x] `CentricClient` authenticates (token → `Cookie`), `list_styles` paginates skip/limit until a
      short page, passes `modified_after` for the delta, resolves+caches references, trusts a 500 body,
      and retries timeouts. (`tests/test_centric_client.py`)
- [x] `style_to_plm_item` produces the §3 field map — ref resolution (`customer` ← resolved
      `category_2`, `product_category`, `brand`), `print_method` join, `contents` HTML strip, `title`
      formula, the formula `description`, `modified_at`/`created_at` ← `_modified_at`, blank
      `design_brief`/`season`. (`tests/test_centric_loader.py`)
- [x] The producer refreshes the mirror from Centric before reading the delta (wired in
      `function_app.py`; `refresh_plm_items` tested incl. full-pull-at-EPOCH vs `modified_after` delta).
      *Note:* the producer timer function itself has no unit test (pre-existing gap — the enqueue/
      watermark logic is unchanged 001 behavior covered via `run_sync`).
- [x] The refresh fetches **ready-only** (`test_refresh_requests_only_ready_styles`) and skips inactive
      styles; the consumer and reconcile import nothing from `centric_client` — they make **no** Centric
      calls (by construction). *Tradeoff:* reconciliation may over-report not-ready cards (accepted — §3).
- [x] **Live-sandbox validation:** ready-only refresh pulled **68 styles → 24 families in ~11s**
      (vs >9min all-active); auth, `_modified_at` parse (`…917Z`), and `customer`/`product_category`/
      `brand` resolution all confirmed against `mgf-test.centricsoftware.com`.
- [x] `pick_canonical` still collapses `*CORE`/`*CUSTOM` siblings sharing a `family_id` (existing
      `test_reader.py` cases pass unchanged; `customer` now comes from resolved `category_2`).
- [ ] **(Option B — deferred)** The Excel loader, `openpyxl` dep, and `data/input` seed are intentionally
      retained this round; their removal moves to a follow-up after live-sandbox validation.
- [x] README reflects the Centric source — architecture, producer, data-model, configuration, and
      deployment sections updated (the remaining "Excel" mentions are the unrelated unmapped-log export).
