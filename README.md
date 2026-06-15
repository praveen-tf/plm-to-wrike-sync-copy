# PLM to Wrike Sync

One-way synchronisation of product data from a PLM source database into Wrike work items.
The pipeline runs as three Azure Functions (two core sync functions plus a reconciliation function)
decoupled by an Azure Service Bus queue, so each record is processed and retried independently
and a single bad record never stops the batch.

## What it does

- Reads product records that changed since the last run from a PostgreSQL source table.
- Selects one canonical record per product family (CORE preferred over CUSTOM).
- Creates a Wrike card for a new family, or updates the existing card for a known family via a
  1:1 live map (PLM record ↔ Wrike card keyed by id).
- On update, writes only the fields that actually changed, merges the PLM-owned description
  sections, comments on tracked content changes, and raises a comment-only alert on fields
  that must not be auto-overwritten.
- Routes unmapped or ambiguous cards to a human-reviewed log table, exported to Excel for
  data-quality review.
- Records the outcome of every record in a sync-state table and routes repeated failures to
  a dead-letter queue.

The sync is one directional, from PLM to Wrike. Wrike is never read back as a source of truth
beyond confirming the current state of a card before an update.

## Architecture

```
Centric 8 PLM API
   -> Producer Function (timer)        refreshes plm_item mirror, syncs folder map, reads change delta, enqueues messages
   -> PostgreSQL (plm_item mirror)     local cache of active Centric styles (read by producer/consumer/reconcile)
   -> Service Bus queue (plm-sync)     decouples and buffers, with a dead-letter queue
   -> Consumer Function (queue trigger) transforms and writes to Wrike, records sync state
   -> Wrike API                        creates or updates the card
   -> PostgreSQL (sync state)          family to card map, watermark, dead-letter, unmapped log
   -> Reconciliation Function (HTTP)   on-demand full-folder audit of unmapped cards
```

| Azure resource | Role |
|---|---|
| Function App (Python) | Hosts three functions: producer (timer), consumer (queue trigger), reconciliation (HTTP) |
| Service Bus namespace and queue | Decoupling, retry, and dead-lettering between producer and consumer |
| PostgreSQL | Centric mirror (`plm_item`), sync state, live map, folder routing, and unmapped-card log |
| Key Vault | Wrike tokens, Centric credentials, database connection string, Service Bus connection string |
| Application Insights | Execution logs, failures, and queue metrics |

## How it works

### Producer (timer trigger)

Runs on a schedule (`0 0 12,0 * * *`, which is 12:00 and 00:00 UTC, or 05:00 and 17:00
Pacific). On each run it:

1. Reads the watermark (last run timestamp).
2. Refreshes the `plm_item` mirror from Centric: fetches the ready-for-Wrike styles
   (`mgf_ready_for_wrike=true`) modified since the watermark, maps each to a `plm_item` record
   (resolving references, joining lists, stripping HTML), and upserts them. On the first run,
   this is a full backfill of the ready set.
3. Syncs the folder map from Wrike: reads the top-level folders of `WRIKE_SPACE_ID`, rebuilds
   `wrike_folder_map` from them (parsing folder titles for `"<PREFIX> - <Brand>"` pairs), and
   guarantees a `_PENDING_REVIEW` staging folder exists (creating it in Wrike if absent). This
   stage runs once per producer run under the catch-all author's Wrike token.
4. Selects records where `modified_at` is later than the watermark and the eligibility flag
   (`ready_for_wrike`) is set.
5. Reduces each family to its canonical record.
6. Sends one Service Bus message per family. The message carries identifiers only, not a data
   snapshot, so the consumer always acts on the current database state.
7. Advances the watermark once the messages are enqueued. Delivery and retry are then owned by
   the queue.

The producer does not call Wrike except to sync the folder map (step 3).

### Consumer (Service Bus queue trigger)

Triggers once per message. For each message it:

1. Re-reads the canonical family by identifier. If the family is no longer eligible, the
   message is acknowledged and skipped.
2. Decides create or update using a three-step resolution:
   - If the family has a live map row, update that card directly by id (no Wrike search).
   - Otherwise, search Wrike by the "PLM - Item #" custom field within the prefix's mapped folder.
   - Branch based on the search result: nothing → create; one exact match → update; extras or
     non-exact → log to the review table.
3. Builds the Wrike payload and writes the card (if the branch is create or update).
4. Records the result in the sync-state table and the unmapped-log table (if applicable).

An unhandled error abandons the message lock, so Service Bus redelivers it and, after the
maximum delivery count, moves it to the dead-letter queue.

### Reconciliation (HTTP trigger, on demand)

The `plm_wrike_reconcile` function (route `/api/reconcile`, auth level FUNCTION) performs a
full-folder audit: it walks every card in each managed folder (including the staging folder)
and attempts to match each card to a PLM record by item number + raw customer string. Cards
that do not match—including hand-made cards—are logged to the unmapped-log table for the
client's data-quality review. The function is read-only against Wrike and run on demand
(e.g., after a cold-start or to audit the folder state).

## Field mapping and sync rules

- Create payload carries the full field set. Update payload carries only update-eligible
  custom fields whose incoming value differs from the live card.
- Create-only fields (for example brand category, customer, design brief, image link) are set
  once at creation and never overwritten on update.
- The description is section-merged on update, not replaced. Only the PLM-owned sections are
  refreshed.
- A change to a tracked content field (material codes, contents) is written and a summary
  comment is added to the card.
- A change to a protected field (title, brand category) is not written. A comment-only alert
  is posted instead.
- Every new card is stamped with the Wrike Item Type "Retail Item".
- A card with no real change is left untouched.
- Customer matching is raw and exact (no normalization)—deliberately surfaces data-quality
  issues so they land in the Excel export for client review.

The Wrike author of a card is the owner of the API token that created it. Tokens are mapped to
product categories in the `category_author_map` table. Cards are placed in the folder identified
by the `wrike_folder_map` table, routed **prefix-first with brand as a tiebreaker**: a prefix
with one folder routes by prefix alone, while a prefix shared by several brands (e.g.
`MB - MR BEAST` vs `MB - MEAT BOARDS`) is disambiguated by an exact brand match. A family with
an unmapped prefix — or a multi-brand prefix whose brand matches none — is created in the
staging folder (a special `'*'` row in `wrike_folder_map`) and the miss is logged.

## Data model

The schema is defined in `db/schema.sql`.

| Table | Purpose |
|---|---|
| `plm_item` | Local mirror of active Centric styles (refreshed from Centric API on each producer run) |
| `category_author_map` | Product category to author token mapping |
| `sync_watermark` | Incremental cursor and last-run statistics |
| `wrike_task_map` | 1:1 live map (PLM record ↔ Wrike card); keys: `plm_internal_id` (PK) ↔ `wrike_task_id` (UNIQUE); columns `item_number`, `family_id`, `customer` for audit/recovery |
| `wrike_folder_map` | Prefix+brand to folder routing table (app-owned, rebuilt from the Wrike space on each producer run); key: `(prefix, brand)` composite PK (`brand` is the routing tiebreaker when brands share a prefix, `''` for the `'*'` staging row); columns `wrike_folder_id`, `full_folder_name`, `space_id`, timestamps |
| `wrike_unmapped_log` | Review log for unmapped, ambiguous, or hand-made cards; columns: `item_number`, `customer`, `wrike_task_id`, `prefix`, `reason` (e.g., `no_exact_match`, `multiple_exact_matches`, `non_identical_extra`, `unmapped_prefix`, `no_plm_match`), `details`, `created_at` |
| `sync_dlq` | Records that failed after retries |

## Configuration

All configuration is supplied through Function App application settings. Secrets are stored in
Key Vault and referenced from the settings, so no secret value is held in the application
configuration itself.

| Setting | Purpose | Source |
|---|---|---|
| `PG_CONN` | PostgreSQL connection string | Key Vault reference |
| `ServiceBusConnection` | Queue listener connection (consumer) | Key Vault reference |
| `ServiceBusSendConnection` | Queue sender connection (producer) | Key Vault reference |
| `ServiceBusQueue` | Queue name, default `plm-sync` | Plain value |
| `CENTRIC_BASE_URL` | Centric 8 API base URL (e.g., `https://company-sandbox.centricsoftware.com`) | Plain value |
| `CENTRIC_USERNAME` | Centric API user account | Key Vault reference |
| `CENTRIC_PASSWORD` | Centric API user password | Key Vault reference |
| `CENTRIC_API_VERSION` | Centric API version, default `v2` | Plain value |
| `WRIKE_TOKEN_<NAME>` | Wrike API token per author identity | Key Vault reference |
| `WRIKE_HOST` | Wrike API host, default `www.wrike.com` | Plain value |
| `WRIKE_SPACE_ID` | Wrike space id for folder sync; the app discovers and maps the space's top-level folders on each producer run | Plain value |
| `WRIKE_RETAIL_ITEM_TYPE_ID` | Custom Item Type id for new cards | Plain value |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Telemetry | Plain value |

The `WRIKE_TOKEN_<NAME>` setting names must match the `token_ref` values in
`category_author_map`. Folder routing is derived from the `wrike_folder_map` table
(which is rebuilt each producer run from the configured Wrike space). Folder titles
must follow the `"<PREFIX> - <Brand>"` pattern to be mapped (titles without `" - "`
are skipped). A special `'*'` row in `wrike_folder_map` serves as a fallback staging
folder for unmapped prefixes (the `_PENDING_REVIEW` folder, automatically created/synced
from Wrike). If no `'*'` row is present, items with unmapped prefixes defer (safe degradation).

The catch-all author token (`WRIKE_TOKEN_PRAVEEN` by default) must be a member of `WRIKE_SPACE_ID`
with folder-create permission, as folder sync uses this identity to list folders and create
`_PENDING_REVIEW` if it is absent.

For details on Centric authentication and the field mapping from Centric to `plm_item`, see
`project_docs/centric_8_api.md` and `specs/002-centric-8-api-source.md`.

## Deployment using the Azure portal

1. Resource group. Create a resource group to hold all resources.

2. PostgreSQL. Create an Azure Database for PostgreSQL Flexible Server. Apply `db/schema.sql`
   to create the tables.

3. Service Bus. Create a Service Bus namespace on the Standard tier. Add a queue named
   `plm-sync` and set maximum delivery count to 10, enable dead-lettering on message
   expiration, and enable duplicate detection. The dead-letter queue is created automatically.

4. Key Vault. Create a Key Vault. Add the Centric credentials, Wrike tokens, the PostgreSQL
   connection string, and the Service Bus connection string as secrets.

5. Function App. Create a Function App with the Python runtime. Open Identity and enable the
   system-assigned managed identity. In the Key Vault access policies, grant that identity Get
   and List on secrets.

6. Application settings. Open Configuration on the Function App and add the settings in the
   table above. For each secret, use a Key Vault reference of the form
   `@Microsoft.KeyVault(SecretUri=https://<vault>.vault.azure.net/secrets/<name>/)`.

7. Networking, if the database is private. Add VNet integration on the Function App and place
   the database behind a network rule that allows access from the function subnet only.

8. Deploy the code. Publish this project to the Function App using the Azure Functions
   extension for Visual Studio Code, the Azure Functions Core Tools, or the portal Deployment
   Center. After deployment, three functions appear under Functions: `plm_wrike_producer`
   (timer), `plm_wrike_consumer` (queue trigger), and `plm_wrike_reconcile` (HTTP).

9. Verify. On the Function App, confirm the host is running and all three functions are listed.
   On Configuration, confirm every Key Vault reference shows a resolved status. Test the
   producer and consumer by running the producer manually (via Code and Test or admin endpoint)
   and checking the queue depth and logs. The folder map is automatically synced from Wrike
   on the first producer run.

10. Monitor. Use Application Insights for execution logs and failures. Use the Service Bus
    metrics for active and dead-lettered message counts. The folder-sync stage logs a summary
    of discovered folders on each producer run.

## Operations

- Run on demand. The producer runs automatically on its schedule. To run it immediately,
  open the `plm_wrike_producer` function in the portal and use Code and Test, then Run, or
  call the function admin endpoint with the host key. `scripts/trigger_azure.sh` wraps the
  command-line equivalents for triggering, checking queue depth, and tailing logs.

- Reconciliation audit. To audit the managed folders for unmapped or hand-made cards,
  call the `plm_wrike_reconcile` HTTP function (route `/api/reconcile`). The function is
  read-only and logs every unmapped card to the review table for export.

- Monitor. Use Application Insights for execution logs and failures. Use the Service Bus
  metrics for active and dead-lettered message counts.

- Failures. A record that fails all retries lands in the queue dead-letter sub-queue and is
  recorded in `sync_dlq`. Configure an alert on a dead-letter count greater than zero so a
  record that never reached Wrike is noticed.

- Data-quality review. Export the `wrike_unmapped_log` table to Excel to review unmapped,
  ambiguous, and hand-made cards. The table includes the item number, customer, Wrike task id,
  prefix, reason code, and details for each unmapped card.

## Local development

A local environment is provided for running the test suite. It is not required for the Azure
deployment.

1. Set up a local PostgreSQL instance with a `plm` database and `plm` role. The defaults
   are `localhost:5432`, `db plm`, `user plm`, `password plm_local_pw`. To override, set
   any of `PG_HOST`, `PG_PORT`, `PG_DB`, `PG_USER`, `PG_PASSWORD`, or use a single `PG_CONN`
   connection string. The test suite uses a **separate `plm_test` database** (owned by `plm`,
   same schema) so it never touches live sync state — create it alongside `plm`.

2. Apply the schema (idempotent, includes guarded migrations):

```
psql -h localhost -U plm -d plm -f db/schema.sql
```

3. Optionally load the sample data:

```
psql -h localhost -U plm -d plm -f seed_plm_item.sql
```

4. Run the test suite:

```
python -m pytest
```

The suite defaults to the isolated `plm_test` database (via `PG_DB`) and **hard-fails if it is
ever pointed at the production `plm` database**, because it `TRUNCATE`s the state tables — so it
never disturbs live sync state. Database-backed cases skip automatically if PostgreSQL is
unreachable.

The application reads settings locally from `local.settings.json`, which is excluded from
version control. Use `local.settings.json.example` as a template. To run the producer locally
(e.g., to test a Centric refresh or folder sync), populate the Centric and Wrike settings in
`local.settings.json`.
