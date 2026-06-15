-- PLM -> Wrike sync POC schema.
-- Apply with: psql -U plm -d plm -f db/schema.sql
-- Re-runnable: every object uses IF NOT EXISTS / idempotent seeds / guarded migrations.

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
-- Config: item-prefix + brand -> Wrike folder routing.
-- Populated by the folder-sync stage (folder_sync.py), which runs at the start of
-- every producer run: it pulls the top-level folders of WRIKE_SPACE_ID and rebuilds
-- this table from them (TRUNCATE + repopulate), so it is always a faithful mirror of
-- the space. Each folder titled "<PREFIX> - <Brand>" becomes a row.
-- The key is (prefix, brand): a prefix usually has one folder, but two brands can share a
-- prefix (e.g. "MB - MR BEAST" vs "MB - MEAT BOARDS"). Routing is prefix-first with brand
-- as the tiebreaker - a single-folder prefix routes by prefix alone; a multi-folder prefix
-- is disambiguated by an exact brand match (see mapping.resolve_folder).
-- The special row prefix = '*' (brand '') is the staging/pending fallback (the
-- "_PENDING_REVIEW" folder, created in Wrike by folder sync if absent): an item whose
-- prefix has no row - or a multi-folder prefix with no brand match - is created there and
-- the miss is logged to wrike_unmapped_log for follow-up. With no '*' row, the item defers.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wrike_folder_map (
    prefix            text NOT NULL,              -- e.g. WP, LT; '*' = staging fallback
    wrike_folder_id   text NOT NULL,              -- numeric permalink id or v4 API id
    full_folder_name  text,
    brand             text NOT NULL DEFAULT '',   -- folder title right of " - "; '' for '*'
    space_id          text,                       -- the Wrike space the folder lives in
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (prefix, brand)
);

-- Migration for DBs created before brand existed / when prefix alone was the key.
-- Idempotent: add the column, default empty (never NULL), then swap the single-column
-- (prefix) primary key for the composite (prefix, brand) one only while the old key stands.
ALTER TABLE wrike_folder_map ADD COLUMN IF NOT EXISTS brand text;
UPDATE wrike_folder_map SET brand = '' WHERE brand IS NULL;
ALTER TABLE wrike_folder_map ALTER COLUMN brand SET DEFAULT '';
ALTER TABLE wrike_folder_map ALTER COLUMN brand SET NOT NULL;
DO $$
DECLARE pk_cols int;
BEGIN
    SELECT count(*) INTO pk_cols
    FROM pg_index i
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
    WHERE i.indrelid = 'wrike_folder_map'::regclass AND i.indisprimary;
    IF pk_cols = 1 THEN  -- still the old single-column (prefix) key
        ALTER TABLE wrike_folder_map DROP CONSTRAINT wrike_folder_map_pkey;
        ALTER TABLE wrike_folder_map ADD PRIMARY KEY (prefix, brand);
    END IF;
END $$;

-- POC seed: a couple of example rows. Folder sync OVERWRITES this table on the first
-- real producer run (the stored space_id no longer matches WRIKE_SPACE_ID), so these
-- are illustrative only and harmless to leave.
INSERT INTO wrike_folder_map (prefix, wrike_folder_id, full_folder_name, brand, space_id) VALUES
    ('WP', '4459532498', 'WP - Winnie-the-Pooh', 'Winnie-the-Pooh', '4450208096'),
    ('LT', '4469574468', 'LT - Lindt',           'Lindt',           '4450208096'),
    ('*',  '4483642519', '_PENDING_REVIEW',      '',                '4450208096')
ON CONFLICT (prefix, brand) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Config: product-category -> author identity (Wrike Author = token owner).
-- token_ref names the app-setting holding that identity's Wrike API token.
-- The special row product_category = '*' is the catch-all: any category without
-- its own row resolves to it, so only the exceptions need explicit rows.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS category_author_map (
    product_category text PRIMARY KEY,           -- e.g. CONFECTION; '*' = catch-all
    author           text,
    token_ref        text
);

INSERT INTO category_author_map (product_category, author, token_ref) VALUES
    ('CONFECTION', 'Jesse',   'WRIKE_TOKEN_JESSE'),
    ('*',          'Praveen', 'WRIKE_TOKEN_PRAVEEN')
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

-- For DBs created before the outcome columns existed (CREATE TABLE IF NOT EXISTS
-- won't add columns to an existing table) - idempotent backfill:
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
