import datetime
from datetime import timedelta
import json
import os
import time

import psycopg2
from psycopg2 import OperationalError

from Helpers.logger import log, ERROR


class DB:
    def __init__(self):
        self.connection = None
        self.cursor = None

    def connect(self):
        try:
            if os.getenv("TEST_MODE").lower() == "true":
                self.connection = psycopg2.connect(
                    user=os.getenv("TEST_DB_LOGIN"),
                    password=os.getenv("TEST_DB_PASS"),
                    host=os.getenv("TEST_DB_HOST"),
                    port=int(os.getenv("TEST_DB_PORT")),
                    database=os.getenv("TEST_DB_DATABASE", "postgres"),
                    sslmode=os.getenv("TEST_DB_SSLMODE"),
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=5
                )
                self.cursor = self.connection.cursor()
            elif os.getenv("TEST_MODE").lower() == "false":
                self.connection = psycopg2.connect(
                    user=os.getenv("DB_LOGIN"),
                    password=os.getenv("DB_PASS"),
                    host=os.getenv("DB_HOST"),
                    port=int(os.getenv("DB_PORT")),
                    database=os.getenv("DB_DATABASE", "postgres"),
                    sslmode=os.getenv("DB_SSLMODE"),
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=5
                )
                self.cursor = self.connection.cursor()
            else:
                log(ERROR, "Problem logging into db", context="database")
                exit(-1)
        except OperationalError as e:
            log(ERROR, f"Connection failed: {e}", context="database")
            raise

    def close(self):
        """Close cursor and connection."""
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()


def get_current_guild_data() -> dict:
    """Load current guild member data from cache_entries table.
    Returns dict with 'time' and 'members' keys, or empty dict on error.
    Replaces: current_activity.json
    """
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "SELECT data FROM cache_entries WHERE cache_key = 'guildData'"
        )
        row = db.cursor.fetchone()
        if row and row[0]:
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return {}
    except Exception as e:
        log(ERROR, f"Error: {e}", context="database")
        return {}
    finally:
        db.close()


def get_player_activity_baseline(uuid: str, key: str, days: int) -> tuple:
    """Get baseline value for a player from N calendar days ago.

    Uses date-based lookup (not OFFSET) for accuracy even with missing snapshot days.
    Handles corrupted 0-value entries from private profiles / API failures by walking
    forward to find the first non-zero value when zeros are followed by real data.

    Returns (value, warn_flag) tuple.
    """
    db = DB()
    db.connect()
    try:
        return _get_baseline_from_db(db, uuid, key, days)
    except Exception as e:
        log(ERROR, f"Error for {uuid}/{key}: {e}", context="database")
        return (0, True)
    finally:
        db.close()


def get_player_activity_baseline_with_db(db: DB, uuid: str, key: str, days: int) -> tuple:
    """Same as get_player_activity_baseline but uses an existing DB connection."""
    try:
        return _get_baseline_from_db(db, uuid, key, days)
    except Exception as e:
        log(ERROR, f"Error for {uuid}/{key}: {e}", context="database")
        return (0, True)


def _get_baseline_from_db(db: DB, uuid: str, key: str, days: int) -> tuple:
    """Core baseline lookup logic.

    Algorithm:
    1. Find the player's snapshot closest to (but not after) N calendar days ago.
    2. If the value is 0 but later non-zero snapshots exist, walk forward to the
       first non-zero value (handles corrupted data from private profiles).
    3. If no snapshot exists at or before the target date (new player), use their
       earliest available snapshot as baseline.
    4. If no snapshots exist at all, return (0, True).
    """
    # Whitelist of allowed column names to prevent SQL injection
    ALLOWED_KEYS = {"playtime", "contributed", "wars", "raids", "shells"}
    if key not in ALLOWED_KEYS:
        return (0, True)

    if days <= 0:
        return (0, False)

    target_date = datetime.date.today() - timedelta(days=days)

    # 1. Find the player's snapshot closest to (but not after) N days ago
    db.cursor.execute(f"""
        SELECT {key}, snapshot_date FROM player_activity
        WHERE uuid = %s AND snapshot_date <= %s
        ORDER BY snapshot_date DESC LIMIT 1
    """, (uuid, target_date))
    row = db.cursor.fetchone()

    if row and row[0] is not None:
        value = row[0]
        # 2. If the value is 0, check for corrupted data:
        #    walk forward to find first non-zero value after this date
        if value == 0:
            db.cursor.execute(f"""
                SELECT {key}, snapshot_date FROM player_activity
                WHERE uuid = %s AND snapshot_date > %s AND {key} > 0
                ORDER BY snapshot_date ASC LIMIT 1
            """, (uuid, row[1]))
            nonzero = db.cursor.fetchone()
            if nonzero:
                return (int(nonzero[0]), True)
        return (int(value), False)

    # 3. No snapshot at or before target date (new player):
    #    use their earliest available snapshot
    db.cursor.execute(f"""
        SELECT {key}, snapshot_date FROM player_activity
        WHERE uuid = %s
        ORDER BY snapshot_date ASC LIMIT 1
    """, (uuid,))
    earliest = db.cursor.fetchone()
    if earliest and earliest[0] is not None:
        value = earliest[0]
        # Same 0-value check for earliest snapshot
        if value == 0:
            db.cursor.execute(f"""
                SELECT {key} FROM player_activity
                WHERE uuid = %s AND {key} > 0
                ORDER BY snapshot_date ASC LIMIT 1
            """, (uuid,))
            nonzero = db.cursor.fetchone()
            if nonzero:
                return (int(nonzero[0]), True)
        return (int(value), True)

    # 4. No snapshots at all
    return (0, True)


def get_last_online() -> dict:
    """Load last online/crash data from cache_entries."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute("SELECT data FROM cache_entries WHERE cache_key = 'lastOnline'")
        row = db.cursor.fetchone()
        if row and row[0]:
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return {"type": "Online", "timestamp": int(time.time())}
    except Exception:
        return {"type": "Online", "timestamp": int(time.time())}
    finally:
        db.close()


def set_last_online(data: dict):
    """Save last online/crash data to cache_entries."""
    db = DB()
    db.connect()
    try:
        epoch = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
        db.cursor.execute("""
            INSERT INTO cache_entries (cache_key, data, expires_at)
            VALUES ('lastOnline', %s, %s)
            ON CONFLICT (cache_key) DO UPDATE SET
                data = EXCLUDED.data, created_at = NOW()
        """, (json.dumps(data), epoch))
        db.connection.commit()
    except Exception:
        pass
    finally:
        db.close()


def get_territory_data() -> dict:
    """Load territory data from cache_entries.
    Replaces: territories.json reads
    """
    db = DB()
    db.connect()
    try:
        db.cursor.execute("SELECT data FROM cache_entries WHERE cache_key = 'territories'")
        row = db.cursor.fetchone()
        if row and row[0]:
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return {}
    except Exception as e:
        log(ERROR, f"Error: {e}", context="database")
        return {}
    finally:
        db.close()


def get_blacklist() -> list:
    """Load blacklist from cache_entries."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute("SELECT data FROM cache_entries WHERE cache_key = 'blacklist'")
        row = db.cursor.fetchone()
        if row and row[0]:
            data = row[0] if isinstance(row[0], list) else json.loads(row[0])
            return data
        return []
    except Exception:
        return []
    finally:
        db.close()


def save_blacklist(data: list):
    """Save blacklist to cache_entries."""
    db = DB()
    db.connect()
    try:
        epoch = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
        db.cursor.execute("""
            INSERT INTO cache_entries (cache_key, data, expires_at)
            VALUES ('blacklist', %s, %s)
            ON CONFLICT (cache_key) DO UPDATE SET
                data = EXCLUDED.data, created_at = NOW()
        """, (json.dumps(data), epoch))
        db.connection.commit()
    except Exception:
        pass
    finally:
        db.close()


def get_recruitment_data() -> dict:
    """Load recruitment scan data from cache_entries."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute("SELECT data FROM cache_entries WHERE cache_key = 'recruitmentList'")
        row = db.cursor.fetchone()
        if row and row[0]:
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return {}
    except Exception:
        return {}
    finally:
        db.close()


def save_recruitment_data(data: dict):
    """Save recruitment scan data to cache_entries."""
    db = DB()
    db.connect()
    try:
        epoch = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
        db.cursor.execute("""
            INSERT INTO cache_entries (cache_key, data, expires_at)
            VALUES ('recruitmentList', %s, %s)
            ON CONFLICT (cache_key) DO UPDATE SET
                data = EXCLUDED.data, created_at = NOW()
        """, (json.dumps(data), epoch))
        db.connection.commit()
    except Exception as e:
        log(ERROR, f"Save failed: {e}", context="database")
    finally:
        db.close()


def get_shell_exchange_config() -> dict:
    """Load shell exchange display config from cache_entries."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute("SELECT data FROM cache_entries WHERE cache_key = 'shellExchangeConfig'")
        row = db.cursor.fetchone()
        if row and row[0]:
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return {}
    except Exception as e:
        log(ERROR, f"Error: {e}", context="database")
        return {}
    finally:
        db.close()


def save_shell_exchange_config(data: dict):
    """Save shell exchange display config to cache_entries."""
    db = DB()
    db.connect()
    try:
        epoch = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
        db.cursor.execute("""
            INSERT INTO cache_entries (cache_key, data, expires_at)
            VALUES ('shellExchangeConfig', %s, %s)
            ON CONFLICT (cache_key) DO UPDATE SET
                data = EXCLUDED.data, created_at = NOW()
        """, (json.dumps(data), epoch))
        db.connection.commit()
    except Exception as e:
        log(ERROR, f"Save failed: {e}", context="database")
    finally:
        db.close()


def get_shell_exchange_ings() -> dict:
    """Load shell exchange ingredient data from cache_entries."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute("SELECT data FROM cache_entries WHERE cache_key = 'shellExchangeIngs'")
        row = db.cursor.fetchone()
        if row and row[0]:
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return {}
    except Exception as e:
        log(ERROR, f"Error: {e}", context="database")
        return {}
    finally:
        db.close()


def save_shell_exchange_ings(data: dict):
    """Save shell exchange ingredient data to cache_entries."""
    db = DB()
    db.connect()
    try:
        epoch = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
        db.cursor.execute("""
            INSERT INTO cache_entries (cache_key, data, expires_at)
            VALUES ('shellExchangeIngs', %s, %s)
            ON CONFLICT (cache_key) DO UPDATE SET
                data = EXCLUDED.data, created_at = NOW()
        """, (json.dumps(data), epoch))
        db.connection.commit()
    except Exception as e:
        log(ERROR, f"Save failed: {e}", context="database")
    finally:
        db.close()


def get_shell_exchange_mats() -> dict:
    """Load shell exchange material data from cache_entries."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute("SELECT data FROM cache_entries WHERE cache_key = 'shellExchangeMats'")
        row = db.cursor.fetchone()
        if row and row[0]:
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return {}
    except Exception as e:
        log(ERROR, f"Error: {e}", context="database")
        return {}
    finally:
        db.close()


def save_shell_exchange_mats(data: dict):
    """Save shell exchange material data to cache_entries."""
    db = DB()
    db.connect()
    try:
        epoch = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
        db.cursor.execute("""
            INSERT INTO cache_entries (cache_key, data, expires_at)
            VALUES ('shellExchangeMats', %s, %s)
            ON CONFLICT (cache_key) DO UPDATE SET
                data = EXCLUDED.data, created_at = NOW()
        """, (json.dumps(data), epoch))
        db.connection.commit()
    except Exception as e:
        log(ERROR, f"Save failed: {e}", context="database")
    finally:
        db.close()
