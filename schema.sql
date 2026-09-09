-- =============================================================================
-- Member Management
-- =============================================================================

CREATE TABLE IF NOT EXISTS discord_links (
  discord_id        BIGINT       PRIMARY KEY,
  ign               VARCHAR(64)  NOT NULL,
  uuid              UUID,
  linked            BOOLEAN      NOT NULL DEFAULT FALSE,
  rank              VARCHAR(32)  NOT NULL,
  wars_on_join      INT,
  -- Honorific roles held at (re)registration, recorded before the bot strips
  -- them, so member removal can hand them back (TAQ-51). On restore, Retired
  -- Chief also grants Honored Fish (TAQ-67).
  was_honored_fish  BOOLEAN      NOT NULL DEFAULT FALSE,
  was_retired_chief BOOLEAN      NOT NULL DEFAULT FALSE
);

-- Migration: honorific tracking for member removal (TAQ-51 / TAQ-67)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'discord_links' AND column_name = 'was_honored_fish') THEN
    ALTER TABLE discord_links ADD COLUMN was_honored_fish BOOLEAN NOT NULL DEFAULT FALSE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'discord_links' AND column_name = 'was_retired_chief') THEN
    ALTER TABLE discord_links ADD COLUMN was_retired_chief BOOLEAN NOT NULL DEFAULT FALSE;
  END IF;
END $$;

-- A Minecraft account may be linked to at most one Discord account at a time.
-- Duplicate linked rows fan out every uuid join (bot and website), duplicating
-- leaderboard rows and double-counting raid points. Unlinked historical rows
-- may still share a uuid (e.g. a member who left and relinked elsewhere).
CREATE UNIQUE INDEX IF NOT EXISTS discord_links_linked_uuid_uq
  ON discord_links (uuid) WHERE linked AND uuid IS NOT NULL;

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
  poll_message_id      BIGINT,
  app_complete         BOOLEAN      DEFAULT FALSE,
  app_message_id       BIGINT
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
  "user"                 BIGINT      PRIMARY KEY,
  shells                 INT         NOT NULL DEFAULT 0,
  balance                INT         NOT NULL DEFAULT 0,
  ign                    VARCHAR(64),
  last_aspect_convert_at TIMESTAMPTZ
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
  bonus_threshold    INT,
  bonus_amount       INT,
  min_points          INT         NOT NULL DEFAULT 0,
  le_per_point        INT         NOT NULL DEFAULT 1,
  created_by_discord BIGINT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_graid_events_active ON graid_events(active);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'graid_events' AND column_name = 'min_points') THEN
    ALTER TABLE graid_events ADD COLUMN min_points INT NOT NULL DEFAULT 0;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'graid_events' AND column_name = 'le_per_point') THEN
    ALTER TABLE graid_events ADD COLUMN le_per_point INT NOT NULL DEFAULT 1;
  END IF;
END$$;

CREATE TABLE IF NOT EXISTS graid_event_totals (
  event_id     BIGINT      NOT NULL REFERENCES graid_events(id) ON DELETE CASCADE,
  uuid         UUID        NOT NULL,
  total        INT         NOT NULL DEFAULT 0,
  last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (event_id, uuid)
);

-- Individual raid completion logs (one row per detected raid group)
CREATE TABLE IF NOT EXISTS graid_logs (
  id           SERIAL      PRIMARY KEY,
  event_id     BIGINT      REFERENCES graid_events(id) ON DELETE CASCADE,  -- NULL = raid outside any event
  raid_type    VARCHAR(40),            -- Full raid name or NULL for unknown/xp-only
  completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_graid_logs_event_id     ON graid_logs(event_id);
CREATE INDEX IF NOT EXISTS idx_graid_logs_completed_at ON graid_logs(completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_graid_logs_raid_type    ON graid_logs(raid_type);

-- Participants in each logged raid
CREATE TABLE IF NOT EXISTS graid_log_participants (
  log_id INT         NOT NULL REFERENCES graid_logs(id) ON DELETE CASCADE,
  uuid   UUID,
  ign    VARCHAR(64),
  UNIQUE (log_id, uuid, ign)
);

CREATE INDEX IF NOT EXISTS idx_graid_log_participants_uuid ON graid_log_participants(uuid);
CREATE INDEX IF NOT EXISTS idx_graid_log_participants_ign  ON graid_log_participants(ign);

-- Legacy per-raid-type reward overrides for old graid events.
CREATE TABLE IF NOT EXISTS graid_event_raid_rewards (
  event_id         BIGINT      NOT NULL REFERENCES graid_events(id) ON DELETE CASCADE,
  raid_type        VARCHAR(40) NOT NULL,
  low_rank_reward  INT         NOT NULL,
  high_rank_reward INT         NOT NULL,
  PRIMARY KEY (event_id, raid_type)
);

-- Per-event point values for each raid type.
CREATE TABLE IF NOT EXISTS graid_event_raid_points (
  event_id  BIGINT      NOT NULL REFERENCES graid_events(id) ON DELETE CASCADE,
  raid_type VARCHAR(40) NOT NULL,
  points    NUMERIC     NOT NULL DEFAULT 0 CONSTRAINT graid_event_raid_points_points_decimal_check CHECK (points >= 0 AND points = ROUND(points, 2)),
  PRIMARY KEY (event_id, raid_type)
);

DO $$
BEGIN
  ALTER TABLE graid_event_raid_points
    ALTER COLUMN points TYPE NUMERIC USING points::numeric,
    ALTER COLUMN points SET DEFAULT 0;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'graid_event_raid_points_points_decimal_check'
      AND conrelid = 'graid_event_raid_points'::regclass
  ) THEN
    ALTER TABLE graid_event_raid_points
      ADD CONSTRAINT graid_event_raid_points_points_decimal_check
      CHECK (points >= 0 AND points = ROUND(points, 2));
  END IF;
END$$;

-- Cumulative reward point bonuses unlocked by reaching ranking point thresholds.
CREATE TABLE IF NOT EXISTS graid_event_milestones (
  event_id         BIGINT NOT NULL REFERENCES graid_events(id) ON DELETE CASCADE,
  threshold_points INT    NOT NULL,
  bonus_points     INT    NOT NULL DEFAULT 0,
  PRIMARY KEY (event_id, threshold_points)
);

-- Final placement reward point bonuses. These do not affect ranking.
CREATE TABLE IF NOT EXISTS graid_event_placement_bonuses (
  event_id     BIGINT NOT NULL REFERENCES graid_events(id) ON DELETE CASCADE,
  placement    INT    NOT NULL,
  bonus_points INT    NOT NULL DEFAULT 0,
  PRIMARY KEY (event_id, placement)
);

-- Per-UUID raid offsets for raids missed during bot downtime
CREATE TABLE IF NOT EXISTS graid_raid_offsets (
  uuid         UUID PRIMARY KEY,
  raid_offset  INT NOT NULL DEFAULT 0
);

-- Queue for manually-logged guild raids submitted via the website.
-- The bot's update_member_data loop drains this queue and runs each entry
-- through the same flow as auto-detected raids (DB writes + Discord embed
-- with the guild level progress image), so the website doesn't have to
-- duplicate that logic.
CREATE TABLE IF NOT EXISTS graid_log_queue (
  id               SERIAL      PRIMARY KEY,
  raid_type        VARCHAR(40),                 -- Full raid name, NULL = unknown
  announce         BOOLEAN     NOT NULL DEFAULT TRUE,  -- post to Discord (only honored for known raid types)
  participants     JSONB       NOT NULL,        -- [{uuid: "..."|null, ign: "..."}, ...]
  submitted_by     BIGINT,                      -- Discord ID of the submitter
  submitted_by_ign VARCHAR(64),
  status           VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending | done | error
  error_message    TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  processed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_graid_log_queue_pending
  ON graid_log_queue(created_at) WHERE status = 'pending';

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

-- ── Presence sampling ────────────────────────────────────────────────────────
-- player_activity is one cumulative snapshot per day, so it can answer "how
-- long did they play on the 4th" but never "at what hour". These two tables
-- record who is online at each update_member_data tick (every 3 minutes),
-- accumulated into 15-minute buckets.
--
-- presence_ticks is what keeps the derived charts honest. An empty bucket
-- means "nobody was online" only if ticks were actually recorded for it;
-- without the tick log, bot downtime is indistinguishable from a dead
-- timezone. Consumers normalise a bucket by the ticks observed inside it
-- (5 expected at a 3-minute cadence) and render unobserved spans as gaps.

CREATE TABLE IF NOT EXISTS presence_buckets (
  uuid         UUID        NOT NULL,
  bucket_start TIMESTAMPTZ NOT NULL,           -- floored to 15 minutes, UTC
  samples      SMALLINT    NOT NULL DEFAULT 0, -- ticks seen online in this bucket
  PRIMARY KEY (uuid, bucket_start)
);

CREATE INDEX IF NOT EXISTS idx_presence_buckets_bucket
  ON presence_buckets(bucket_start DESC);

-- online_count is the guild endpoint's own top-level count; attributed_count is
-- how many of those we could pin to a specific member.
--
-- They differ, and the difference is the point. A member with
-- restrictions.online_status = true is counted in the guild's total but has
-- their per-member `online` flag reported false, so presence_buckets can never
-- see them. Measured on the live endpoint: 10 of 150 members restrict their
-- status, and across repeated samples not one of them ever reported
-- online = true while the top-level count stayed exactly that much higher.
-- Guild-wide charts therefore read online_count; only per-member views are
-- limited to attributed_count, and the gap between the two is reportable
-- rather than silent.
CREATE TABLE IF NOT EXISTS presence_ticks (
  tick_at          TIMESTAMPTZ PRIMARY KEY,
  online_count     SMALLINT    NOT NULL,  -- authoritative: includes hidden members
  member_count     SMALLINT    NOT NULL,
  gap_seconds      INT,                   -- since the previous tick; NULL on the first tick ever
  attributed_count SMALLINT    NOT NULL DEFAULT 0  -- of online_count, how many we can name
);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name = 'presence_ticks' AND column_name = 'attributed_count') THEN
    ALTER TABLE presence_ticks ADD COLUMN attributed_count SMALLINT NOT NULL DEFAULT 0;
  END IF;
END $$;

-- ── Hourly counter samples ───────────────────────────────────────────────────
-- The last cumulative reading seen for each member in each hour.
--
-- player_activity records these same counters once a day, which can say how
-- much someone played on the 4th but never at what hour. The 3-minute loop
-- already fetches all three to build the guildData cache, so keeping the last
-- reading per hour costs nothing extra and makes hour-of-day derivable from
-- the difference between consecutive hours.
--
-- Values are cumulative and monotonic, so the write uses GREATEST: a stale or
-- out-of-order read can never walk a counter backwards. Deltas are computed at
-- query time and only trusted where consecutive samples are exactly one hour
-- apart — a wider span means the bot was down, and attributing a whole gap to
-- one hour would invent activity that could have happened at any point in it.
--
-- This only ever describes time after it was deployed. The daily history in
-- playtime_daily is not re-derivable at this grain and remains the long-range
-- source.
CREATE TABLE IF NOT EXISTS member_counters_hourly (
  uuid     UUID        NOT NULL,
  hour     TIMESTAMPTZ NOT NULL,
  playtime REAL        NOT NULL,
  wars     INT         NOT NULL,
  raids    INT         NOT NULL,
  PRIMARY KEY (uuid, hour)
);

CREATE INDEX IF NOT EXISTS idx_member_counters_hour
  ON member_counters_hourly(hour DESC);

-- ── Derived activity series ──────────────────────────────────────────────────
-- Rollups the charts read. Everything here is recomputable from the raw
-- sources (player_activity, presence_buckets, presence_ticks), so any of it
-- can be dropped and rebuilt.

-- Per-player playtime attributed to a calendar day, derived from consecutive
-- player_activity snapshots. source records how trustworthy the row is:
--   exact        – the two snapshots were one day apart
--   interpolated – they spanned span_days > 1 (missed snapshot, or a member
--                  who left and rejoined); hours are that span's delta split
--                  evenly, so the shape within the span is invented
--   clamped      – the delta ran negative (uuid reuse or an API correction)
--                  and was floored to zero
--   capped       – the delta implied more than 24 hours in a day, which is
--                  impossible; it was clipped to 24 and the excess discarded
CREATE TABLE IF NOT EXISTS playtime_daily (
  uuid      UUID        NOT NULL,
  day       DATE        NOT NULL,
  hours     REAL        NOT NULL,
  wars      INT         NOT NULL DEFAULT 0,
  raids     INT         NOT NULL DEFAULT 0,
  span_days SMALLINT    NOT NULL DEFAULT 1,
  source    VARCHAR(16) NOT NULL CHECK (source IN ('exact', 'interpolated', 'clamped', 'capped')),
  PRIMARY KEY (uuid, day)
);

-- Widen the source constraint on databases created before 'capped' existed.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.constraint_column_usage
             WHERE table_name = 'playtime_daily' AND constraint_name = 'playtime_daily_source_check') THEN
    ALTER TABLE playtime_daily DROP CONSTRAINT playtime_daily_source_check;
  END IF;
  ALTER TABLE playtime_daily ADD CONSTRAINT playtime_daily_source_check
    CHECK (source IN ('exact', 'interpolated', 'clamped', 'capped'));
END $$;

CREATE INDEX IF NOT EXISTS idx_playtime_daily_day
  ON playtime_daily(day DESC);

-- Per-player minutes online in an hour, folded up from presence_buckets. A
-- sample is worth 15 / (ticks observed in its bucket) minutes, so a lagging
-- loop stretches its surviving samples instead of under-reporting the hour.
CREATE TABLE IF NOT EXISTS presence_hourly (
  uuid    UUID        NOT NULL,
  hour    TIMESTAMPTZ NOT NULL,
  minutes REAL        NOT NULL,
  PRIMARY KEY (uuid, hour)
);

CREATE INDEX IF NOT EXISTS idx_presence_hourly_hour
  ON presence_hourly(hour DESC);

-- Guild-wide shape of the same hour, plus how much of it was actually
-- observed. ticks_observed is the honesty column: 20 is full coverage at a
-- 3-minute cadence, 0 means the sampler was down and the hour is a gap
-- rather than a quiet period.
CREATE TABLE IF NOT EXISTS presence_coverage_hourly (
  hour             TIMESTAMPTZ PRIMARY KEY,
  ticks_observed   SMALLINT    NOT NULL,
  online_avg       REAL        NOT NULL,  -- from the guild's own count
  online_peak      SMALLINT    NOT NULL,
  distinct_members SMALLINT    NOT NULL,
  attributed_avg   REAL        NOT NULL DEFAULT 0  -- the nameable subset of online_avg
);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name = 'presence_coverage_hourly' AND column_name = 'attributed_avg') THEN
    ALTER TABLE presence_coverage_hourly ADD COLUMN attributed_avg REAL NOT NULL DEFAULT 0;
  END IF;
END $$;

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

  -- Secondary and tertiary are set only for gradient and holographic roles.
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'discord_links' AND column_name = 'color_primary') THEN
    ALTER TABLE discord_links ADD COLUMN color_primary INT;
    ALTER TABLE discord_links ADD COLUMN color_secondary INT;
    ALTER TABLE discord_links ADD COLUMN color_tertiary INT;
    ALTER TABLE discord_links ADD COLUMN color_role_name VARCHAR(100);
    ALTER TABLE discord_links ADD COLUMN colors_synced_at TIMESTAMPTZ;
  END IF;

  -- guild leave tracking for accepted applicants currently in another guild
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'new_app' AND column_name = 'guild_leave_pending') THEN
    ALTER TABLE new_app ADD COLUMN guild_leave_pending BOOLEAN DEFAULT FALSE;
  END IF;

  -- Application completeness validation columns
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'new_app' AND column_name = 'app_complete') THEN
    ALTER TABLE new_app ADD COLUMN app_complete BOOLEAN DEFAULT FALSE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'new_app' AND column_name = 'app_message_id') THEN
    ALTER TABLE new_app ADD COLUMN app_message_id BIGINT;
  END IF;
  -- applications: app_number column for persistent counter-based naming
  -- (table-existence guard: on a fresh database the table is created later
  -- in this file, with the column already present)
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'applications')
     AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'applications' AND column_name = 'app_number') THEN
    ALTER TABLE applications ADD COLUMN app_number INT;
  END IF;

  -- snipe_participants: uuid column for stable player identity (ign is a
  -- display snapshot that goes stale on rename). Backfill from linked
  -- discord_links rows by current name; unmatched rows stay NULL and keep
  -- ign-keyed fallback identity. The table-existence guard matters: this DO
  -- block runs before the CREATE TABLE further down, so on a fresh database
  -- there is nothing to migrate (the CREATE already includes the column).
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'snipe_participants')
     AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'snipe_participants' AND column_name = 'uuid') THEN
    ALTER TABLE snipe_participants ADD COLUMN uuid UUID;
    UPDATE snipe_participants sp
    SET uuid = dl.uuid
    FROM discord_links dl
    WHERE sp.uuid IS NULL
      AND dl.linked = TRUE
      AND dl.uuid IS NOT NULL
      AND LOWER(dl.ign) = LOWER(sp.ign);
  END IF;

  -- graid_log_queue: the old mode column ('group'/'individual') is replaced by
  -- the announce flag — party size no longer changes how a raid is processed.
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'graid_log_queue' AND column_name = 'announce') THEN
    ALTER TABLE graid_log_queue ADD COLUMN announce BOOLEAN NOT NULL DEFAULT TRUE;
    UPDATE graid_log_queue SET announce = COALESCE(mode = 'group', TRUE);
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'graid_log_queue' AND column_name = 'mode') THEN
    ALTER TABLE graid_log_queue DROP COLUMN mode;
  END IF;
END $$;

-- Re-runnable on every replay: resolve remaining NULL-uuid snipe rows from
-- unambiguous (uuid, name) pairs in the raid logs — a name-history source
-- that covers snapshots older than the player's current name, which the
-- discord_links backfill above can never match. Guarded because this runs
-- before the snipe tables are created on a fresh database.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'snipe_participants') THEN
    UPDATE snipe_participants sp
    SET uuid = h.uuid
    FROM (
      SELECT LOWER(ign) AS name_key, MIN(uuid::text)::uuid AS uuid
      FROM graid_log_participants
      WHERE uuid IS NOT NULL AND ign IS NOT NULL
      GROUP BY LOWER(ign)
      HAVING COUNT(DISTINCT uuid) = 1
    ) h
    WHERE sp.uuid IS NULL AND h.name_key = LOWER(sp.ign);
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
-- Annihilation Party System
-- =============================================================================

CREATE TABLE IF NOT EXISTS annihilation_party_events (
  id                BIGSERIAL   PRIMARY KEY,
  schedule_at       TIMESTAMPTZ NOT NULL UNIQUE,
  closes_at         TIMESTAMPTZ NOT NULL,
  guild_id          BIGINT      NOT NULL,
  channel_id        BIGINT      NOT NULL,
  message_id        BIGINT      UNIQUE,
  thread_id         BIGINT,
  status            VARCHAR(16) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'closed')),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  closed_at         TIMESTAMPTZ,
  discord_closed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_annihilation_party_events_due
  ON annihilation_party_events(closes_at)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_annihilation_party_events_finalize
  ON annihilation_party_events(closed_at)
  WHERE status = 'closed' AND discord_closed_at IS NULL;

CREATE TABLE IF NOT EXISTS annihilation_parties (
  event_id     BIGINT      NOT NULL
               REFERENCES annihilation_party_events(id) ON DELETE CASCADE,
  party_number SMALLINT    NOT NULL CHECK (party_number BETWEEN 1 AND 5),
  world        VARCHAR(8),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (event_id, party_number)
);

CREATE TABLE IF NOT EXISTS annihilation_party_members (
  id                BIGSERIAL   PRIMARY KEY,
  event_id          BIGINT      NOT NULL
                    REFERENCES annihilation_party_events(id) ON DELETE CASCADE,
  discord_id        BIGINT      NOT NULL,
  ign               VARCHAR(16) NOT NULL,
  uuid              UUID,
  party_number      SMALLINT    NOT NULL CHECK (party_number BETWEEN 1 AND 5),
  slot_number       SMALLINT    NOT NULL CHECK (slot_number BETWEEN 1 AND 10),
  build             VARCHAR(50) NOT NULL,
  combat_role       VARCHAR(10)
                    CHECK (combat_role IN ('healer', 'guardian')),
  bringing_scrolls BOOLEAN      NOT NULL DEFAULT FALSE,
  notes             VARCHAR(50),
  party_joined_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (event_id, discord_id),
  UNIQUE (event_id, party_number, slot_number),
  FOREIGN KEY (event_id, party_number)
    REFERENCES annihilation_parties(event_id, party_number) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_annihilation_party_members_ign
  ON annihilation_party_members(event_id, LOWER(ign));

-- The name index alone cannot stop a renamed player from holding two slots;
-- the uuid is the stable identity when resolvable.
CREATE UNIQUE INDEX IF NOT EXISTS idx_annihilation_party_members_uuid
  ON annihilation_party_members(event_id, uuid) WHERE uuid IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_annihilation_party_members_party
  ON annihilation_party_members(event_id, party_number, party_joined_at, id);

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

-- =============================================================================
-- Audit Log (replaces background.log and shell.log)
-- =============================================================================

CREATE TABLE IF NOT EXISTS audit_log (
  id         SERIAL       PRIMARY KEY,
  log_type   VARCHAR(50)  NOT NULL,
  actor_name VARCHAR(100),
  actor_id   BIGINT,
  action     TEXT         NOT NULL,
  created_at TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_type ON audit_log(log_type);
CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at);

-- =============================================================================
-- Website Application System
-- =============================================================================

CREATE TABLE IF NOT EXISTS applications (
  id                SERIAL       PRIMARY KEY,
  application_type  VARCHAR(20)  NOT NULL CHECK (application_type IN ('guild', 'community', 'hammerhead')),
  discord_id        VARCHAR(30)  NOT NULL,
  discord_username  VARCHAR(50)  NOT NULL,
  discord_avatar    VARCHAR(255),
  status            VARCHAR(20)  DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'denied', 'expired')),
  answers           JSONB        NOT NULL,
  submitted_at      TIMESTAMPTZ  DEFAULT NOW(),
  reviewed_at       TIMESTAMPTZ,
  reviewed_by       VARCHAR(50),
  channel_id        BIGINT,
  thread_id         BIGINT,
  poll_message_id   BIGINT,
  guild_leave_pending BOOLEAN    DEFAULT FALSE,
  poll_status       TEXT         DEFAULT ':green_circle: Received',
  bot_processed     BOOLEAN      DEFAULT FALSE,
  invite_image      TEXT,
  app_number        INT,
  message_ids       BIGINT[]     DEFAULT NULL,
  closed_at         TIMESTAMPTZ,
  transcribed_at    TIMESTAMPTZ,
  channel_deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS application_votes (
  id               SERIAL      PRIMARY KEY,
  application_id   INT         NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
  voter_discord_id VARCHAR(30) NOT NULL,
  voter_username   VARCHAR(50) NOT NULL,
  vote             VARCHAR(10) NOT NULL CHECK (vote IN ('accept', 'deny', 'abstain')),
  source           VARCHAR(10) NOT NULL CHECK (source IN ('website', 'discord')),
  voted_at         TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (application_id, voter_discord_id)
);

-- =============================================================================
-- Recruiter Tracking & Payouts
-- =============================================================================
-- Pending on accept, becomes payable once the recruit reaches Piranha
-- (posted_at set, task-board embed sent) — see Helpers/recruiting.py.

CREATE TABLE IF NOT EXISTS recruit_credits (
  id                    SERIAL       PRIMARY KEY,
  app_id                INT          NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
  recruiter_ign         VARCHAR(50)  NOT NULL,
  recruit_ign           VARCHAR(50)  NOT NULL,
  recruit_discord_id    VARCHAR(30),
  excluded              BOOLEAN      DEFAULT FALSE,  -- recruiter was owner/chief: counted, never paid
  recruit_number        INT,                          -- recruiter's Nth paid credit, set when finalized
  payout_le             INT,                          -- set when finalized
  eligible_at           TIMESTAMPTZ,                  -- when the recruit reached Piranha
  posted_at             TIMESTAMPTZ,                  -- when the task-board embed was sent (claim marker)
  task_board_message_id BIGINT,
  voided_at             TIMESTAMPTZ,                  -- recruit left the guild before reaching Piranha
  paid                  BOOLEAN      DEFAULT FALSE,
  paid_at               TIMESTAMPTZ,
  paid_by               VARCHAR(50),
  created_at            TIMESTAMPTZ  DEFAULT NOW(),
  UNIQUE (app_id)
);

CREATE INDEX IF NOT EXISTS idx_recruit_credits_recruiter ON recruit_credits(recruiter_ign);
CREATE INDEX IF NOT EXISTS idx_recruit_credits_recruit_ign ON recruit_credits(recruit_ign);

-- =============================================================================
-- Blacklist
-- =============================================================================

CREATE TABLE IF NOT EXISTS blacklist (
  uuid       VARCHAR(36)   PRIMARY KEY,
  ign        VARCHAR(16)   NOT NULL,
  reason     VARCHAR(1000),
  created_at TIMESTAMPTZ   DEFAULT NOW()
);

-- =============================================================================
-- Kick List
-- =============================================================================

CREATE TABLE IF NOT EXISTS kick_list (
  uuid       VARCHAR(36)  PRIMARY KEY,
  ign        VARCHAR(64)  NOT NULL,
  tier       INT          NOT NULL CHECK (tier IN (1, 2, 3)),
  added_by   VARCHAR(50)  NOT NULL,
  created_at TIMESTAMPTZ  DEFAULT NOW()
);

-- =============================================================================
-- Bot Settings (key-value store for persistent config)
-- =============================================================================

CREATE TABLE IF NOT EXISTS bot_settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Seed app_counter if missing (won't overwrite existing value)
INSERT INTO bot_settings (key, value) VALUES ('app_counter', '3725') ON CONFLICT DO NOTHING;

-- =============================================================================
-- Promotion Queue
-- =============================================================================

CREATE TABLE IF NOT EXISTS promotion_queue (
  id                   SERIAL       PRIMARY KEY,
  uuid                 UUID         NOT NULL,
  ign                  VARCHAR(64)  NOT NULL,
  current_rank         VARCHAR(32)  NOT NULL,
  new_rank             VARCHAR(32),
  action_type          VARCHAR(10)  NOT NULL CHECK (action_type IN ('promote', 'demote', 'remove')),
  queued_by_discord_id BIGINT       NOT NULL,
  queued_by_ign        VARCHAR(64)  NOT NULL,
  created_at           TIMESTAMPTZ  DEFAULT NOW(),
  status               VARCHAR(20)  NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
  completed_at         TIMESTAMPTZ,
  error_message        TEXT
);

-- =============================================================================
-- Guild Colors & Prefixes (territory map)
-- =============================================================================

CREATE TABLE IF NOT EXISTS guild_generated_colors (
  guild_name VARCHAR(100) PRIMARY KEY,
  color      VARCHAR(7)   NOT NULL
);

CREATE TABLE IF NOT EXISTS guild_prefixes (
  guild_name   VARCHAR(100) PRIMARY KEY,
  guild_prefix VARCHAR(10)  NOT NULL
);

-- =============================================================================
-- Territory Exchanges
-- =============================================================================

CREATE TABLE IF NOT EXISTS territory_exchanges (
  exchange_time TIMESTAMPTZ  NOT NULL,
  territory     VARCHAR(100) NOT NULL,
  attacker_name VARCHAR(100) NOT NULL,
  defender_name VARCHAR(100)
);

-- =============================================================================
-- Snipe Tracker
-- =============================================================================

CREATE TABLE IF NOT EXISTS snipe_logs (
  id          SERIAL      PRIMARY KEY,
  hq          VARCHAR(64) NOT NULL,
  difficulty  INT         NOT NULL,
  sniped_at   TIMESTAMPTZ NOT NULL,
  guild_tag   VARCHAR(10) NOT NULL,
  conns       SMALLINT    NOT NULL DEFAULT 0 CHECK (conns BETWEEN 0 AND 6),
  logged_by   BIGINT      NOT NULL,
  season      INT         NOT NULL DEFAULT 1
);

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'snipe_logs' AND column_name = 'conns' AND data_type = 'character varying'
  ) THEN
    ALTER TABLE snipe_logs DROP CONSTRAINT IF EXISTS snipe_logs_conns_check;
    ALTER TABLE snipe_logs ALTER COLUMN conns DROP DEFAULT;
    ALTER TABLE snipe_logs ALTER COLUMN conns TYPE SMALLINT USING conns::SMALLINT;
    ALTER TABLE snipe_logs ALTER COLUMN conns SET DEFAULT 0;
    ALTER TABLE snipe_logs ADD CONSTRAINT snipe_logs_conns_check CHECK (conns BETWEEN 0 AND 6);
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS snipe_participants (
  snipe_id    INT         NOT NULL REFERENCES snipe_logs(id) ON DELETE CASCADE,
  ign         VARCHAR(64) NOT NULL,   -- display snapshot at log time
  uuid        UUID,                   -- player identity; ign-keyed fallback when NULL
  role        VARCHAR(10) NOT NULL,
  PRIMARY KEY (snipe_id, ign)
);

CREATE INDEX IF NOT EXISTS idx_snipe_participants_ign
  ON snipe_participants(ign);
CREATE INDEX IF NOT EXISTS idx_snipe_participants_uuid
  ON snipe_participants(uuid);

CREATE INDEX IF NOT EXISTS idx_snipe_logs_sniped_at
  ON snipe_logs(sniped_at DESC);

CREATE INDEX IF NOT EXISTS idx_snipe_logs_season
  ON snipe_logs(season);

CREATE INDEX IF NOT EXISTS idx_snipe_logs_hq
  ON snipe_logs(hq);

CREATE INDEX IF NOT EXISTS idx_snipe_logs_guild_tag
  ON snipe_logs(guild_tag);

-- ── Snipe settings (shared between bot and web) ──────────────────────────────

CREATE TABLE IF NOT EXISTS snipe_settings (
  key   VARCHAR(64) PRIMARY KEY,
  value TEXT        NOT NULL
);

INSERT INTO snipe_settings (key, value)
VALUES ('current_season', '30')
ON CONFLICT DO NOTHING;

-- ── Rank role colours (mirrored from Discord for the Verge mod) ─────────────

CREATE TABLE IF NOT EXISTS rank_role_colors (
  rank_key        VARCHAR(32)  PRIMARY KEY,
  color_primary   INT          NOT NULL,
  color_secondary INT,
  color_tertiary  INT,
  role_name       VARCHAR(100),
  synced_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- Activity Event Timeline
-- =============================================================================
-- One timestamped shape over the three things the guild does at a specific
-- moment, so the hour-of-day charts have a single source to group. Defined
-- last because it depends on tables created throughout this file.
--
-- Unlike playtime, all three sources carry real timestamps going back through
-- the imported history, so these charts are populated without waiting for
-- presence sampling to accumulate.
--
-- 'capture' is TAq as the attacker only — territory_exchanges also records us
-- losing land, which is a different question than when we choose to fight.
-- Every snipe_logs row is one of ours (guild_tag is the HQ's owner, i.e. the
-- target), so no guild filter applies there.

-- participants is how many people the event took. A guild raid can be one
-- player or a full party of four — 13 pct of logged raids are solo and 72 pct
-- are four-strong, averaging 3.38 — so counting raids as events understates
-- group activity by roughly that factor. Captures have no roster to count and
-- carry 1. GREATEST(..., 1) keeps an event with no participant rows visible
-- rather than weighting it to nothing.
CREATE OR REPLACE VIEW guild_activity_events AS
  SELECT exchange_time AS occurred_at,
         'capture'     AS kind,
         territory     AS label,
         1             AS participants
  FROM territory_exchanges
  WHERE attacker_name = 'The Aquarium'
  UNION ALL
  SELECT g.completed_at,
         'raid',
         COALESCE(g.raid_type, 'Unknown'),
         GREATEST(COUNT(p.log_id), 1)::int
  FROM graid_logs g
  LEFT JOIN graid_log_participants p ON p.log_id = g.id
  GROUP BY g.id, g.completed_at, g.raid_type
  UNION ALL
  SELECT s.sniped_at,
         'snipe',
         s.hq,
         GREATEST(COUNT(sp.snipe_id), 1)::int
  FROM snipe_logs s
  LEFT JOIN snipe_participants sp ON sp.snipe_id = s.id
  GROUP BY s.id, s.sniped_at, s.hq;

-- =============================================================================
-- Card Collection (executive guild only)
-- =============================================================================

-- Reel budget. Refreshed lazily on read rather than by a scheduled task:
-- window_idx is floor(epoch / 21600), so a wallet catches up whenever it is
-- touched. Unused reels bank to twice a window's worth.
-- pearls are the card economy's own currency and never convert to or from
-- shells. last_trickle is advanced in whole hours as passive pearls are paid
-- out, the same lazy catch-up used for reels.
CREATE TABLE IF NOT EXISTS card_wallet (
    "user"       BIGINT      PRIMARY KEY,
    reels        SMALLINT    NOT NULL DEFAULT 3,
    window_idx   BIGINT      NOT NULL DEFAULT 0,
    total_reeled INT         NOT NULL DEFAULT 0,
    pearls       BIGINT      NOT NULL DEFAULT 0,
    tank_tier    SMALLINT    NOT NULL DEFAULT 1,
    streak       INT         NOT NULL DEFAULT 0,
    last_daily   DATE,
    last_trickle TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Reels from /bait, held outside the bank so a full bank cannot swallow
    -- them. Spent before reels do, and never above one day's worth: you
    -- cannot bait again while any are unspent.
    bait_reels   SMALLINT    NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One row per user per card, with a copy count, since duplicates are kept
-- rather than discarded. card references the slug in data/cards.json, or a
-- card_members slug for a 1/1. stars is the fusion level (1-5).
CREATE TABLE IF NOT EXISTS card_collection (
    "user"   BIGINT      NOT NULL,
    card     VARCHAR(64) NOT NULL,
    count    INT         NOT NULL DEFAULT 1,
    stars    SMALLINT    NOT NULL DEFAULT 1,
    first_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY ("user", card)
);

-- Wishes bias which card lands inside a tier, never the tier odds themselves.
-- Only epics and legendaries may be wished for.
CREATE TABLE IF NOT EXISTS card_wishlist (
    "user"   BIGINT      NOT NULL,
    card     VARCHAR(64) NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY ("user", card)
);

-- 1/1 cards of Swordfish+ members, created the moment one is minted. The
-- unique constraint on discord_id is what guarantees "one copy, ever";
-- retired marks a holder who has left or dropped below the rank.
CREATE TABLE IF NOT EXISTS card_members (
    slug       VARCHAR(64) PRIMARY KEY,
    discord_id BIGINT      NOT NULL UNIQUE,
    uuid       UUID,
    ign        VARCHAR(64) NOT NULL,
    rank       VARCHAR(32) NOT NULL,
    owner      BIGINT      NOT NULL,
    retired    BOOLEAN     NOT NULL DEFAULT FALSE,
    minted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One-time milestone payouts, so a completed tier can never pay twice.
CREATE TABLE IF NOT EXISTS card_awards (
    "user"     BIGINT      NOT NULL,
    award      VARCHAR(64) NOT NULL,
    pearls     INT         NOT NULL,
    awarded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY ("user", award)
);
