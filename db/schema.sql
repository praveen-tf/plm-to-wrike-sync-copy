-- PLM -> Wrike sync POC schema.
-- Runs automatically on first container init (see docker-compose.yml).
-- Re-runnable by hand: every object uses IF NOT EXISTS / idempotent seeds.

-- ---------------------------------------------------------------------------
-- Source table: one row per PLM variant (simulated from Excel + synthetic CSV).
-- Values are pre-resolved display strings (Excel is already at Wrike-field grain).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plm_item (
    plm_internal_id   text PRIMARY KEY,         -- stable PLM record id (the "Key" column)
    item_number       text NOT NULL,            -- full 4-part, e.g. WP-71511-006-319
    family_id         text NOT NULL,            -- first 8 chars, e.g. WP-71511
    prefix            text NOT NULL,            -- leading alpha prefix, e.g. WP
    code              text,                     -- 5-digit style code, e.g. 71511
    item_name         text,
    customer          text,                     -- *CORE / *CUSTOM / GM; also the canonical-row discriminator
    title             text,
    folder            text,
    workflow          text,
    status            text,
    custom_status     text,
    priority          text,
    end_date          text,
    description       text,
    print_method      text,
    previous_item_no  text,
    contents          text,                     -- PLM - Contents
    material_codes    text,                     -- PLM - Material Codes
    brand_category    text,
    product_category  text,                     -- drives author mapping
    brand             text,
    season            text,
    design_request    text,
    design_brief      text,
    image_link        text,
    -- synthesized control columns (not in the source Excel):
    ready_for_wrike   boolean NOT NULL DEFAULT true,   -- eligibility gate
    created_at        timestamptz NOT NULL,
    modified_at       timestamptz NOT NULL             -- watermark column
);

CREATE INDEX IF NOT EXISTS ix_plm_item_modified_at ON plm_item (modified_at);
CREATE INDEX IF NOT EXISTS ix_plm_item_family_id   ON plm_item (family_id);

-- ---------------------------------------------------------------------------
-- Config: item-prefix -> Wrike folder routing.
-- ALL folder ids live here - no folder id is environment config. Human-maintained
-- and the SOLE authority: the app never discovers folders by searching Wrike. New
-- rows are added manually after client/business approval.
-- The special row prefix = '*' is the staging/pending fallback: an item whose
-- prefix has no row is created in that folder and the miss is logged to
-- wrike_unmapped_log for follow-up. With no '*' row either, the item defers.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wrike_folder_map (
    prefix            text PRIMARY KEY,           -- e.g. WP, LT; '*' = staging fallback
    wrike_folder_id   text NOT NULL,              -- numeric permalink id or v4 API id
    full_folder_name  text,
    space_id          text,                       -- the Wrike space the folder lives in
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

-- POC seed: the test folders in the Personal space. Folder ids are environment-
-- specific; humans update these rows when the sync points at the client's MGF space.
INSERT INTO wrike_folder_map (prefix, wrike_folder_id, full_folder_name, space_id) VALUES
    ('WP', '4459532498', 'WP - Winnie-the-Pooh', '4450208096'),
    ('LT', '4469574468', 'LT - Lindt',           '4450208096'),
    ('*',  '4483642519', 'Pending (staging)',    '4450208096')
ON CONFLICT (prefix) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Config: product-category -> author identity (Wrike Author = token owner).
-- token_ref names the app-setting holding that identity's Wrike API token.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS category_author_map (
    product_category text PRIMARY KEY,
    author           text,
    token_ref        text
);

INSERT INTO category_author_map (product_category, author, token_ref) VALUES
    ('BAKING',     'Praveen', 'WRIKE_TOKEN_PRAVEEN'),
    ('HOT DRINKS', 'Praveen', 'WRIKE_TOKEN_PRAVEEN'),
    ('CONFECTION', 'Jesse',   'WRIKE_TOKEN_JESSE')
ON CONFLICT (product_category) DO NOTHING;

-- ---------------------------------------------------------------------------
-- State: incremental watermark (single row).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sync_watermark (
    id                int PRIMARY KEY DEFAULT 1,
    last_modified_at  timestamptz,
    run_started_at    timestamptz,
    run_completed_at  timestamptz,
    rows_in_delta     int,
    rows_succeeded    int,
    rows_failed       int,
    CONSTRAINT sync_watermark_singleton CHECK (id = 1)
);

-- ---------------------------------------------------------------------------
-- State: the 1:1 live map - one canonical PLM record <-> one Wrike card - plus the
-- last-synced snapshot for change detection. Both ids are unique: a PLM record can
-- never map to two cards, and two PLM records can never share one card. item_number,
-- customer and family_id are descriptive (verification / recovery / audit), NOT keys.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wrike_task_map (
    plm_internal_id      text PRIMARY KEY,        -- canonical PLM record (stable PLM key)
    wrike_task_id        text NOT NULL,           -- that record's one Wrike card (unique index below)
    family_id            text,                    -- first 8 chars of the item number
    item_number          text,                    -- also on the card ("PLM - Item #")
    customer             text,                    -- canonical's customer at last sync
    wrike_permalink      text,
    snap_material_codes  text,
    snap_contents        text,
    snap_title           text,
    snap_brand_category  text,
    created_at           timestamptz,
    last_synced_at       timestamptz,
    -- Per-record sync outcome (queryable status, set by record_sync_result):
    sync_status          text,                    -- created | updated | unchanged | logged | deferred | failed
    retry_count          int DEFAULT 0,           -- also available live as msg.delivery_count
    error_message        text,
    updated_at           timestamptz              -- last sync attempt (created_at = first)
);

-- For DBs created before the outcome columns existed (the docker volume persists across
-- restarts, so CREATE TABLE IF NOT EXISTS won't add them) - idempotent backfill:
ALTER TABLE wrike_task_map
    ADD COLUMN IF NOT EXISTS sync_status   text,
    ADD COLUMN IF NOT EXISTS retry_count   int DEFAULT 0,
    ADD COLUMN IF NOT EXISTS error_message text,
    ADD COLUMN IF NOT EXISTS updated_at    timestamptz;

-- Migration for DBs created when family_id was the primary key. Idempotent: the DO
-- block only fires while that primary key is still in place. Fails loudly if
-- existing rows have NULL/duplicate plm_internal_id - those need a human, not a guess.
ALTER TABLE wrike_task_map
    ADD COLUMN IF NOT EXISTS item_number text,
    ADD COLUMN IF NOT EXISTS customer    text;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
        WHERE i.indrelid = 'wrike_task_map'::regclass
          AND i.indisprimary AND a.attname = 'family_id'
    ) THEN
        ALTER TABLE wrike_task_map DROP CONSTRAINT wrike_task_map_pkey;
        ALTER TABLE wrike_task_map ALTER COLUMN plm_internal_id SET NOT NULL;
        ALTER TABLE wrike_task_map ADD PRIMARY KEY (plm_internal_id);
        ALTER TABLE wrike_task_map ALTER COLUMN wrike_task_id SET NOT NULL;
    END IF;
END $$;
-- family_id is descriptive; migrated DBs carry a leftover NOT NULL from the old key.
ALTER TABLE wrike_task_map ALTER COLUMN family_id DROP NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS wrike_task_map_wrike_task_id_key
    ON wrike_task_map (wrike_task_id);
CREATE INDEX IF NOT EXISTS ix_wrike_task_map_family_id ON wrike_task_map (family_id);

-- ---------------------------------------------------------------------------
-- Review/log: Wrike cards that do not cleanly map to a PLM record (ambiguous or
-- non-identical search results, unmapped prefixes, reconciliation misses - incl.
-- hand-made cards). Append-only; exported to Excel for the client's data-quality
-- review. Rows are resolved by humans, never auto-remediated.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wrike_unmapped_log (
    id              bigserial PRIMARY KEY,
    item_number     text,
    customer        text,                         -- the card's raw Customer value
    wrike_task_id   text,
    prefix          text,
    reason          text NOT NULL,                -- no_exact_match | multiple_exact_matches |
                                                  -- non_identical_extra | unmapped_prefix | no_plm_match
    details         text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- State: dead-letter for rows that failed after retries.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sync_dlq (
    id               bigserial PRIMARY KEY,
    family_id        text,
    plm_internal_id  text,
    reason           text,
    payload          jsonb,
    created_at       timestamptz NOT NULL DEFAULT now()
);
