import datetime
import json
import os
import time

import psycopg2
from psycopg2 import OperationalError


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
                print("Problem logging into db")
                exit(-1)
        except OperationalError as e:
            print(f"[DB] Connection failed: {e}")
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
        print(f"[get_current_guild_data] Error: {e}")
        return {}
    finally:
        db.close()


def get_player_activity_baseline(uuid: str, key: str, days: int) -> tuple:
    """Get baseline value from player_activity table.
    Returns (value, warn_flag) tuple.
    Replaces: player_activity.json lookups
    """
    db = DB()
    db.connect()
    try:
        # Get the days-th most recent snapshot date
        db.cursor.execute("""
            SELECT DISTINCT snapshot_date FROM player_activity
            ORDER BY snapshot_date DESC
            OFFSET %s LIMIT 1
        """, (days,))
        date_row = db.cursor.fetchone()

        if not date_row:
            return (0, True)  # No snapshots available

        target_date = date_row[0]

        db.cursor.execute(f"""
            SELECT {key} FROM player_activity
            WHERE uuid = %s AND snapshot_date = %s
        """, (uuid, target_date))
        row = db.cursor.fetchone()

        if row and row[0] is not None:
            return (int(row[0]), False)

        # Player not found - try walking toward present
        db.cursor.execute(f"""
            SELECT {key} FROM player_activity
            WHERE uuid = %s AND snapshot_date > %s
            ORDER BY snapshot_date ASC LIMIT 1
        """, (uuid, target_date))
        fallback = db.cursor.fetchone()

        if fallback and fallback[0] is not None:
            return (int(fallback[0]), True)  # warn flag

        return (0, True)
    except Exception as e:
        print(f"[get_player_activity_baseline] Error for {uuid}/{key}: {e}")
        return (0, True)
    finally:
        db.close()


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
        print(f"[get_territory_data] Error: {e}")
        return {}
    finally:
        db.close()
