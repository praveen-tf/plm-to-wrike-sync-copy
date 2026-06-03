# PLM to Wrike Sync

One-way synchronisation of product data from a PLM source database into Wrike work items.
The pipeline runs as two Azure Functions decoupled by an Azure Service Bus queue, so each
record is processed and retried independently and a single bad record never stops the batch.

## What it does

- Reads product records that changed since the last run from a PostgreSQL source table.
- Selects one canonical record per product family (CORE preferred over CUSTOM).
- Creates a Wrike card for a new family, or updates the existing card for a known family.
- On update, writes only the fields that actually changed, merges the PLM-owned description
  sections, comments on tracked content changes, and raises a comment-only alert on fields
  that must not be auto-overwritten.
- Records the outcome of every record in a sync-state table and routes repeated failures to
  a dead-letter queue.

The sync is one directional, from PLM to Wrike. Wrike is never read back as a source of truth
beyond confirming the current state of a card before an update.

## Architecture

```
PostgreSQL (source)
   -> Producer Function (timer)        reads the change delta, enqueues one message per family
   -> Service Bus queue (plm-sync)     decouples and buffers, with a dead-letter queue
   -> Consumer Function (queue trigger) transforms and writes to Wrike, records sync state
   -> Wrike API                        creates or updates the card
   -> PostgreSQL (sync state)          family to card map, watermark, dead-letter
```

| Azure resource | Role |
|---|---|
| Function App (Python) | Hosts both functions, the producer and the consumer |
| Service Bus namespace and queue | Decoupling, retry, and dead-lettering between the two functions |
| PostgreSQL | Source records and sync state |
| Key Vault | Wrike tokens, database connection string, Service Bus connection string |
| Application Insights | Execution logs, failures, and queue metrics |

## How it works

### Producer (timer trigger)

Runs on a schedule (`0 0 12,0 * * *`, which is 12:00 and 00:00 UTC, or 05:00 and 17:00
Pacific). On each run it:

1. Reads the watermark, then selects records where `modified_at` is later than the watermark
   and the eligibility flag is set.
2. Reduces each family to its canonical record.
3. Sends one Service Bus message per family. The message carries identifiers only, not a data
   snapshot, so the consumer always acts on the current database state.
4. Advances the watermark once the messages are enqueued. Delivery and retry are then owned by
   the queue.

The producer does not call Wrike.

### Consumer (Service Bus queue trigger)

Triggers once per message. For each message it:

1. Re-reads the canonical family by identifier. If the family is no longer eligible, the
   message is acknowledged and skipped.
2. Decides create or update by looking up the family to card map, falling back to a scoped
   title search inside the configured folders.
3. Builds the Wrike payload and writes the card.
4. Records the result in the sync-state table.

An unhandled error abandons the message lock, so Service Bus redelivers it and, after the
maximum delivery count, moves it to the dead-letter queue.

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

The Wrike author of a card is the owner of the API token that created it. Tokens are mapped to
product categories in the `category_author_map` table. Cards are placed in the existing folder
whose configured prefix matches the item, and a family with no matching folder is deferred to
the next run rather than misfiled.

## Data model

The schema is defined in `db/schema.sql`.

| Table | Purpose |
|---|---|
| `plm_item` | Source records, one row per PLM variant |
| `category_author_map` | Product category to author token mapping |
| `sync_watermark` | Incremental cursor and last-run statistics |
| `wrike_task_map` | Family to Wrike card map, last-synced snapshot, and per-record sync status |
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
| `WRIKE_TOKEN_<NAME>` | Wrike API token per author identity | Key Vault reference |
| `WRIKE_HOST` | Wrike API host, default `www.wrike.com` | Plain value |
| `WRIKE_FOLDER_<PREFIX>` | Target folder id for an item prefix | Plain value |
| `WRIKE_RETAIL_ITEM_TYPE_ID` | Custom Item Type id for new cards | Plain value |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Telemetry | Plain value |

The `WRIKE_TOKEN_<NAME>` setting names must match the `token_ref` values in
`category_author_map`. The `WRIKE_FOLDER_<PREFIX>` names map an item prefix (for example WP)
to a Wrike folder. A numeric folder id from a folder URL is accepted and resolved automatically.

## Deployment using the Azure portal

1. Resource group. Create a resource group to hold all resources.

2. PostgreSQL. Create an Azure Database for PostgreSQL Flexible Server. Apply `db/schema.sql`
   to create the tables. Load the source records into `plm_item`.

3. Service Bus. Create a Service Bus namespace on the Standard tier. Add a queue named
   `plm-sync` and set maximum delivery count to 10, enable dead-lettering on message
   expiration, and enable duplicate detection. The dead-letter queue is created automatically.

4. Key Vault. Create a Key Vault. Add the Wrike tokens, the PostgreSQL connection string, and
   the Service Bus connection string as secrets.

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
   Center. After deployment both functions appear under Functions, named `plm_wrike_producer`
   and `plm_wrike_consumer`.

9. Verify. On the Function App, confirm the host is running and both functions are listed.
   On Configuration, confirm every Key Vault reference shows a resolved status.

## Operations

- Run on demand. The producer runs automatically on its schedule. To run it immediately,
  open the `plm_wrike_producer` function in the portal and use Code and Test, then Run, or
  call the function admin endpoint with the host key. `scripts/trigger_azure.sh` wraps the
  command-line equivalents for triggering, checking queue depth, and tailing logs.

- Monitor. Use Application Insights for execution logs and failures. Use the Service Bus
  metrics for active and dead-lettered message counts.

- Failures. A record that fails all retries lands in the queue dead-letter sub-queue and is
  recorded in `sync_dlq`. Configure an alert on a dead-letter count greater than zero so a
  record that never reached Wrike is noticed.

## Local development

A local environment is provided for running the test suite. It is not required for the Azure
deployment.

```
docker compose up -d        # PostgreSQL on localhost:5433, applies db/schema.sql
python -m pytest            # unit and database-backed tests
```

The application reads the same settings locally from `local.settings.json`, which is excluded
from version control. Use `local.settings.json.example` as a template.
```
