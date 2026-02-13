-- =============================================================================
-- Member Management
-- =============================================================================

CREATE TABLE IF NOT EXISTS discord_links (
  discord_id   BIGINT       PRIMARY KEY,
  ign          VARCHAR(64)  NOT NULL,
  uuid         UUID,
  linked       BOOLEAN      NOT NULL DEFAULT FALSE,
  rank         VARCHAR(32)  NOT NULL,
  wars_on_join INT
);

CREATE TABLE IF NOT EXISTS new_app (
  id                   SERIAL       PRIMARY KEY,
  channel              BIGINT       NOT NULL UNIQUE,
  ticket               VARCHAR(100) NOT NULL,
  webhook              TEXT         NOT NULL,
  posted               BOOLEAN      NOT NULL DEFAULT FALSE,
  reminder             BOOLEAN      NOT NULL DEFAULT FALSE,
  status               TEXT         NOT NULL DEFAULT ':green_circle: Opened',
  created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  thread_id            BIGINT,
  applicant_discord_id BIGINT,
  app_type             VARCHAR(20),
  decision             VARCHAR(20),
  decision_at          TIMESTAMPTZ,
  ign                  VARCHAR(64),
  poll_message_id      BIGINT
);

-- =============================================================================
-- Profile System
-- =============================================================================

CREATE TABLE IF NOT EXISTS profile_backgrounds (
  id          SERIAL       PRIMARY KEY,
  name        VARCHAR(100) UNIQUE NOT NULL,
  public      BOOLEAN      DEFAULT FALSE,
  price       INT          DEFAULT 0,
  description TEXT         DEFAULT ''
);

CREATE TABLE IF NOT EXISTS profile_customization (
  "user"     BIGINT  PRIMARY KEY,
  background INT     NOT NULL DEFAULT 0 REFERENCES profile_backgrounds(id),
  owned      JSONB   NOT NULL,
  gradient   JSONB
);

CREATE TABLE IF NOT EXISTS shells (
  "user"  BIGINT      PRIMARY KEY,
  shells  INT         NOT NULL DEFAULT 0,
  balance INT         NOT NULL DEFAULT 0,
  ign     VARCHAR(64)
);

-- =============================================================================
-- Aspect Distribution System
-- =============================================================================

CREATE TABLE IF NOT EXISTS aspect_queue (
  id         INT         PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  queue      JSONB       NOT NULL DEFAULT '[]'::jsonb,
  marker     INT         NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS aspect_blacklist (
  uuid     UUID        PRIMARY KEY,
  added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  added_by BIGINT
);

CREATE TABLE IF NOT EXISTS uncollected_raids (
  uuid                UUID PRIMARY KEY,
  uncollected_raids   INT  NOT NULL DEFAULT 0,
  collected_raids     INT  NOT NULL DEFAULT 0,
  uncollected_aspects INT  NOT NULL DEFAULT 0,
  ign                 VARCHAR(64)
);

CREATE INDEX IF NOT EXISTS idx_uncollected_aspects
  ON uncollected_raids(uuid) WHERE uncollected_aspects > 0;

CREATE TABLE IF NOT EXISTS distribution_log (
  id                 SERIAL      PRIMARY KEY,
  distributed_by     BIGINT,
  distributions      JSONB       NOT NULL,
  total_aspects      INT         NOT NULL,
  total_emeralds     INT         DEFAULT 0,
  created_at         TIMESTAMPTZ DEFAULT NOW(),
  distributed_by_ign TEXT,
  api_key_name       TEXT
);

-- =============================================================================
-- Guild Raid Events
-- =============================================================================

CREATE TABLE IF NOT EXISTS graid_events (
  id                 BIGSERIAL   PRIMARY KEY,
  title              TEXT        UNIQUE NOT NULL,
  start_ts           TIMESTAMPTZ NOT NULL,
  end_ts             TIMESTAMPTZ,
  active             BOOLEAN     NOT NULL DEFAULT TRUE,
  low_rank_reward    INT         NOT NULL,
  high_rank_reward   INT         NOT NULL,
  min_completions    INT         NOT NULL,
  created_by_discord BIGINT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_graid_events_active ON graid_events(active);

CREATE TABLE IF NOT EXISTS graid_event_totals (
  event_id     BIGINT      NOT NULL REFERENCES graid_events(id) ON DELETE CASCADE,
  uuid         UUID        NOT NULL,
  total        INT         NOT NULL DEFAULT 0,
  last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (event_id, uuid)
);

-- =============================================================================
-- Activity Tracking
-- =============================================================================

CREATE TABLE IF NOT EXISTS player_activity (
  uuid          UUID   NOT NULL,
  playtime      FLOAT  NOT NULL,
  contributed   BIGINT DEFAULT 0,
  wars          INTEGER DEFAULT 0,
  raids         INTEGER DEFAULT 0,
  shells        INTEGER DEFAULT 0,
  snapshot_date DATE   NOT NULL,
  created_at    TIMESTAMP DEFAULT NOW(),
  PRIMARY KEY (uuid, snapshot_date)
);

-- Migration: Add columns if they don't exist (for existing tables)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'player_activity' AND column_name = 'contributed') THEN
    ALTER TABLE player_activity ADD COLUMN contributed BIGINT DEFAULT 0;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'player_activity' AND column_name = 'wars') THEN
    ALTER TABLE player_activity ADD COLUMN wars INTEGER DEFAULT 0;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'player_activity' AND column_name = 'raids') THEN
    ALTER TABLE player_activity ADD COLUMN raids INTEGER DEFAULT 0;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'player_activity' AND column_name = 'shells') THEN
    ALTER TABLE player_activity ADD COLUMN shells INTEGER DEFAULT 0;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_player_activity_date
  ON player_activity(snapshot_date DESC);

-- Migration: Add application overhaul columns
DO $$
BEGIN
  -- new_app columns
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'new_app' AND column_name = 'applicant_discord_id') THEN
    ALTER TABLE new_app ADD COLUMN applicant_discord_id BIGINT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'new_app' AND column_name = 'app_type') THEN
    ALTER TABLE new_app ADD COLUMN app_type VARCHAR(20);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'new_app' AND column_name = 'decision') THEN
    ALTER TABLE new_app ADD COLUMN decision VARCHAR(20);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'new_app' AND column_name = 'decision_at') THEN
    ALTER TABLE new_app ADD COLUMN decision_at TIMESTAMPTZ;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'new_app' AND column_name = 'ign') THEN
    ALTER TABLE new_app ADD COLUMN ign VARCHAR(64);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'new_app' AND column_name = 'poll_message_id') THEN
    ALTER TABLE new_app ADD COLUMN poll_message_id BIGINT;
  END IF;

  -- discord_links column
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'discord_links' AND column_name = 'app_channel') THEN
    ALTER TABLE discord_links ADD COLUMN app_channel BIGINT;
  END IF;

  -- guild leave tracking for accepted applicants currently in another guild
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'new_app' AND column_name = 'guild_leave_pending') THEN
    ALTER TABLE new_app ADD COLUMN guild_leave_pending BOOLEAN DEFAULT FALSE;
  END IF;
END $$;

-- =============================================================================
-- Guild Bank Transactions
-- =============================================================================

CREATE TABLE IF NOT EXISTS guild_bank_transactions (
  id             SERIAL       PRIMARY KEY,
  content_hash   VARCHAR      NOT NULL,
  sequence_num   INTEGER      NOT NULL DEFAULT 0,
  player_name    VARCHAR      NOT NULL,
  action         VARCHAR      NOT NULL CHECK (action IN ('deposited', 'withdrew')),
  item_count     INTEGER      NOT NULL,
  item_name      VARCHAR      NOT NULL,
  bank_type      VARCHAR      NOT NULL DEFAULT 'High Ranked',
  first_reported TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  report_count   INTEGER      NOT NULL DEFAULT 1,
  reporters      TEXT[]       NOT NULL DEFAULT '{}',
  created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  dedup_key      TEXT         NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS guild_bank_dedup_idx
  ON guild_bank_transactions(dedup_key);

CREATE INDEX IF NOT EXISTS idx_gb_dedup
  ON guild_bank_transactions(content_hash, sequence_num, first_reported DESC);

CREATE INDEX IF NOT EXISTS idx_gb_recent
  ON guild_bank_transactions(created_at DESC);

-- =============================================================================
-- System / Config
-- =============================================================================

CREATE TABLE IF NOT EXISTS guild_settings (
  guild_id      BIGINT      NOT NULL,
  setting_key   VARCHAR(64) NOT NULL,
  setting_value BOOLEAN     NOT NULL DEFAULT TRUE,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (guild_id, setting_key)
);

CREATE TABLE IF NOT EXISTS api_keys (
  key_hash     TEXT        PRIMARY KEY,
  discord_id   BIGINT,
  name         TEXT        NOT NULL,
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  last_used_at TIMESTAMPTZ,
  is_active    BOOLEAN     DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS cache_entries (
  cache_key   VARCHAR(255) PRIMARY KEY,
  data        JSONB        NOT NULL,
  created_at  TIMESTAMPTZ  DEFAULT NOW(),
  expires_at  TIMESTAMPTZ  NOT NULL,
  fetch_count INT          DEFAULT 1,
  last_error  TEXT,
  error_count INT          DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_cache_expires_at ON cache_entries(expires_at);
CREATE INDEX IF NOT EXISTS idx_cache_created_at ON cache_entries(created_at);

-- =============================================================================
-- Territory History (Map Snapshots)
-- =============================================================================

CREATE TABLE IF NOT EXISTS territory_snapshots (
  id            SERIAL       PRIMARY KEY,
  snapshot_time TIMESTAMPTZ  NOT NULL,
  territories   JSONB        NOT NULL,
  created_at    TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_snapshots_time ON territory_snapshots(snapshot_time DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_time_range ON territory_snapshots(snapshot_time);

-- =============================================================================
-- Liquid Emerald Balance Tracking
-- =============================================================================

CREATE TABLE IF NOT EXISTS le_balance_log (
  id               SERIAL       PRIMARY KEY,
  balance          INTEGER      NOT NULL,
  previous_balance INTEGER,
  action           TEXT         NOT NULL,
  reason           TEXT         DEFAULT 'N/A',
  updated_by       TEXT         NOT NULL,
  created_at       TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_le_balance_log_created_at
  ON le_balance_log(created_at DESC);

-- =============================================================================
-- Meeting Agenda
-- =============================================================================

CREATE TABLE IF NOT EXISTS agenda_bau_topics (
  id          SERIAL       PRIMARY KEY,
  topic       VARCHAR(100) NOT NULL UNIQUE,
  description TEXT
);

CREATE TABLE IF NOT EXISTS agenda_requested_topics (
  id           SERIAL       PRIMARY KEY,
  topic        VARCHAR(100) NOT NULL,
  description  TEXT,
  submitted_by BIGINT       NOT NULL,
  created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
