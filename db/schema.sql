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

-- Item-prefix -> Wrike folder routing is configured in app settings
-- (WRIKE_FOLDER_<PREFIX>, e.g. WRIKE_FOLDER_WP / WRIKE_FOLDER_AW), not in the DB,
-- because folder ids are environment-specific. See mapping.load_prefix_folder_map.

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
-- State: PLM family -> Wrike task map + last-synced snapshot for change detection.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wrike_task_map (
    family_id            text PRIMARY KEY,
    plm_internal_id      text,
    wrike_task_id        text,
    wrike_permalink      text,
    snap_material_codes  text,
    snap_contents        text,
    snap_title           text,
    snap_brand_category  text,
    created_at           timestamptz,
    last_synced_at       timestamptz,
    -- Stage 5 per-record sync outcome (queryable status, set by record_sync_result):
    sync_status          text,                    -- created | updated | unchanged | deferred | failed
    retry_count          int DEFAULT 0,           -- also available live as msg.delivery_count
    error_message        text,
    updated_at           timestamptz              -- last sync attempt (created_at = first)
);

-- For DBs created before the Stage 5 columns existed (the docker volume persists across
-- restarts, so CREATE TABLE IF NOT EXISTS won't add them) - idempotent backfill:
ALTER TABLE wrike_task_map
    ADD COLUMN IF NOT EXISTS sync_status   text,
    ADD COLUMN IF NOT EXISTS retry_count   int DEFAULT 0,
    ADD COLUMN IF NOT EXISTS error_message text,
    ADD COLUMN IF NOT EXISTS updated_at    timestamptz;

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
