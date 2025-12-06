CREATE TABLE IF NOT EXISTS discord_links (
  discord_id BIGINT        PRIMARY KEY,
  ign        VARCHAR(64)   NOT NULL,
  uuid       UUID,
  linked     BOOLEAN       NOT NULL DEFAULT FALSE,
  rank       VARCHAR(32)   NOT NULL,
  wars_on_join INT
);

CREATE TABLE IF NOT EXISTS profile_backgrounds (
  id   SERIAL    PRIMARY KEY,
  name VARCHAR(100) UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_customization (
  "user"     BIGINT    PRIMARY KEY,
  background INT       NOT NULL DEFAULT 0
                   REFERENCES profile_backgrounds(id),
  owned      JSONB     NOT NULL
);

CREATE TABLE IF NOT EXISTS shells (
  "user"  BIGINT     PRIMARY KEY,
  shells  INT        NOT NULL DEFAULT 0,
  balance INT        NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS new_app (
  id        SERIAL    PRIMARY KEY,
  channel   BIGINT    NOT NULL,
  ticket    VARCHAR(100) NOT NULL,
  webhook   TEXT      NOT NULL,
  posted    BOOLEAN   NOT NULL    DEFAULT FALSE,
  reminder  BOOLEAN   NOT NULL    DEFAULT FALSE,
  status    TEXT      NOT NULL    DEFAULT ':green_circle: Opened',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS guild_settings (
  guild_id      BIGINT       NOT NULL,
  setting_key   VARCHAR(64)  NOT NULL,
  setting_value BOOLEAN      NOT NULL DEFAULT TRUE,
  updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  PRIMARY KEY (guild_id, setting_key)
);