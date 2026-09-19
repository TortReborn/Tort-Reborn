import datetime
import json
import os
import sys
import threading
import time
from datetime import timedelta
from typing import ClassVar

import psycopg2
import psycopg2.pool
from psycopg2 import OperationalError, extensions

from Helpers import telemetry
from Helpers.logger import ERROR, WARN, log


class _TimedCursor:
    """Times execute/executemany into db.query; delegates everything else.

    Delegation is via __getattr__ rather than an explicit method list so that
    fetchone/fetchall/close and anything else keep working untouched.
    """

    def __init__(self, cursor):
        self.__dict__["_cursor"] = cursor

    def execute(self, *args, **kwargs):
        with telemetry.track("db.query"):
            return self.__dict__["_cursor"].execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        with telemetry.track("db.query"):
            return self.__dict__["_cursor"].executemany(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.__dict__["_cursor"], name)

    def __setattr__(self, name, value):
        setattr(self.__dict__["_cursor"], name, value)

    def __iter__(self):
        return iter(self.__dict__["_cursor"])

    def __enter__(self):
        return self.__dict__["_cursor"].__enter__()

    def __exit__(self, *exc):
        return self.__dict__["_cursor"].__exit__(*exc)


class _TimedConnection:
    """Times commit into db.commit; delegates everything else."""

    def __init__(self, connection):
        self.__dict__["_connection"] = connection

    def commit(self, *args, **kwargs):
        with telemetry.track("db.commit"):
            return self.__dict__["_connection"].commit(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.__dict__["_connection"], name)

    def __setattr__(self, name, value):
        setattr(self.__dict__["_connection"], name, value)


class DB:
    _pool_lock: ClassVar = threading.Lock()
    _pools: ClassVar[dict[tuple, psycopg2.pool.ThreadedConnectionPool]] = {}

    # Bounded retry on pool exhaustion: getconn() is non-blocking, so a burst
    # would otherwise turn into instant user-facing failures. Two short waits
    # ride out transient contention; real exhaustion still fails, loudly.
    # The first retry is expected (every loop fires at once on boot, and the
    # 10-min and hourly loops re-align after) and always recovered in 28 days
    # of prod logs, so only a second miss is worth a line.
    _POOL_RETRY_DELAYS: ClassVar[tuple] = (0.2, 0.4)

    def __init__(self, *, use_pool: bool = True, pool_min: int = 1, pool_max: int | None = None):
        self.connection = None
        self.cursor = None
        self._raw_connection = None
        self._pool = None
        self._use_pool = use_pool
        self._pool_min = max(1, int(pool_min))
        if pool_max is None:
            pool_max = int(os.getenv("DB_POOL_MAX", "10"))
        self._pool_max = max(self._pool_min, int(pool_max))

    @staticmethod
    def _connection_kwargs() -> tuple[str, dict]:
        # One DB_* set per environment: the local .env carries the dev
        # database, the Railway service variables carry prod. Which world we
        # are in is the config profile's job (Helpers/variables.py), not the
        # connection layer's.
        host = os.getenv("DB_HOST")
        if not host:
            log(ERROR, "DB_HOST is not set — check your .env", context="database")
            sys.exit(-1)

        return "default", {
            "user": os.getenv("DB_LOGIN"),
            "password": os.getenv("DB_PASS"),
            "host": host,
            "port": int(os.getenv("DB_PORT", "5432")),
            "database": os.getenv("DB_DATABASE", "postgres"),
            "sslmode": os.getenv("DB_SSLMODE"),
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        }

    @classmethod
    def _pool_for(
        cls,
        mode: str,
        kwargs: dict,
        *,
        minconn: int,
        maxconn: int,
    ) -> psycopg2.pool.ThreadedConnectionPool:
        key = (
            mode,
            minconn,
            maxconn,
            tuple(sorted(kwargs.items())),
        )
        with cls._pool_lock:
            pool = cls._pools.get(key)
            if pool is None:
                pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn,
                    maxconn,
                    **kwargs,
                )
                cls._pools[key] = pool
            return pool

    def connect(self):
        try:
            mode, kwargs = self._connection_kwargs()
            if self._use_pool:
                with telemetry.track("db.connect"):
                    self._pool = self._pool_for(
                        mode,
                        kwargs,
                        minconn=self._pool_min,
                        maxconn=self._pool_max,
                    )
                    raw_connection = self._checkout_with_retry()
            else:
                with telemetry.track("db.connect"):
                    raw_connection = psycopg2.connect(**kwargs)
        except (OperationalError, psycopg2.pool.PoolError) as e:
            log(ERROR, f"Connection failed: {e}", context="database")
            raise
        try:
            self._raw_connection = raw_connection
            self.connection = _TimedConnection(raw_connection)
            self.cursor = _TimedCursor(raw_connection.cursor())
        except Exception:
            # A failure here would otherwise leak the checked-out connection
            # from the pool's tracking for the life of the process.
            try:
                if self._use_pool and self._pool is not None:
                    self._pool.putconn(raw_connection, close=True)
                else:
                    raw_connection.close()
            except Exception:
                pass
            self._raw_connection = None
            self.connection = None
            self.cursor = None
            raise

    def _checkout_with_retry(self):
        attempt = 0
        while True:
            try:
                return self._pool.getconn()
            except psycopg2.pool.PoolError:
                if attempt >= len(self._POOL_RETRY_DELAYS):
                    raise
                if attempt >= 1:
                    log(WARN, f"DB pool exhausted, retrying (attempt {attempt + 1})", context="database")
                time.sleep(self._POOL_RETRY_DELAYS[attempt])
                attempt += 1

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        """Close cursor and release or close connection."""
        if self.cursor:
            self.cursor.close()
        if self._raw_connection:
            if self._pool:
                close_connection = bool(self._raw_connection.closed)
                if not close_connection:
                    try:
                        if self._raw_connection.status != extensions.STATUS_READY:
                            self._raw_connection.rollback()
                    except Exception as e:
                        log(
                            WARN,
                            f"Discarding pooled DB connection after rollback failed: {e}",
                            context="database",
                        )
                        close_connection = True
                self._pool.putconn(self._raw_connection, close=close_connection)
            else:
                self._raw_connection.close()
        self.cursor = None
        self.connection = None
        self._raw_connection = None
        self._pool = None

    @classmethod
    def _reset_pools_for_tests(cls):
        """Close and drop every pool. Test hook only."""
        with cls._pool_lock:
            for pool in cls._pools.values():
                try:
                    pool.closeall()
                except Exception:
                    pass
            cls._pools.clear()


class BatchBaselineQueryError(RuntimeError):
    """Raised when a multi-member baseline query fails."""


def get_current_guild_data() -> dict:
    """Load current guild member data from cache_entries table.
    Returns dict with 'time' and 'members' keys, or empty dict on error.
    Replaces: current_activity.json
    """
    db = DB()
    db.connect()
    try:
        return get_current_guild_data_with_db(db)
    except Exception as e:
        log(ERROR, f"Error: {e}", context="database")
        return {}
    finally:
        db.close()


def get_current_guild_data_with_db(db: DB) -> dict:
    """Load current guild member data using an existing DB connection."""
    db.cursor.execute(
        "SELECT data FROM cache_entries WHERE cache_key = 'guildData'"
    )
    row = db.cursor.fetchone()
    if row and row[0]:
        return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return {}


def get_current_guild_data_and_snapshot_count_with_db(db: DB) -> tuple[dict, int]:
    """Load current guild data and the number of activity snapshots in one round trip."""
    db.cursor.execute("""
        SELECT
            (SELECT data FROM cache_entries WHERE cache_key = 'guildData') AS guild_data,
            (SELECT COUNT(DISTINCT snapshot_date) FROM player_activity) AS snapshot_count
    """)
    row = db.cursor.fetchone()
    if not row:
        return {}, 0

    raw_guild_data, snapshot_count = row
    if raw_guild_data:
        guild_data = raw_guild_data if isinstance(raw_guild_data, dict) else json.loads(raw_guild_data)
    else:
        guild_data = {}
    return guild_data, int(snapshot_count or 0)


def get_player_activity_baseline(uuid: str, key: str, days: int, joined_date: datetime.date = None) -> tuple:
    """Get baseline value for a player from N calendar days ago.

    Uses date-based lookup (not OFFSET) for accuracy even with missing snapshot days.
    Handles corrupted 0-value entries from private profiles / API failures by walking
    forward to find the first non-zero value when zeros are followed by real data.

    If joined_date is provided, only snapshots from the current membership period
    (snapshot_date >= joined_date) are considered. This prevents returning members
    from having inflated stats based on old data from a previous membership.

    Returns (value, warn_flag) tuple.
    """
    db = DB()
    db.connect()
    try:
        return _get_baseline_from_db(db, uuid, key, days, joined_date=joined_date)
    except Exception as e:
        log(ERROR, f"Error for {uuid}/{key}: {e}", context="database")
        return (0, True)
    finally:
        db.close()


def get_player_activity_baseline_with_db(db: DB, uuid: str, key: str, days: int, joined_date: datetime.date = None) -> tuple:
    """Same as get_player_activity_baseline but uses an existing DB connection."""
    try:
        return _get_baseline_from_db(db, uuid, key, days, joined_date=joined_date)
    except Exception as e:
        log(ERROR, f"Error for {uuid}/{key}: {e}", context="database")
        return (0, True)


def get_player_activity_baselines_with_db(db: DB, uuid: str, keys: list[str], days: int, joined_date: datetime.date = None) -> dict:
    """Get multiple baseline values with shared snapshot lookup work.

    Returns {key: (value, warn_flag)} and preserves the single-key helper's
    zero-value correction behavior per key.
    """
    try:
        return _get_baselines_from_db(db, uuid, keys, days, joined_date=joined_date)
    except Exception as e:
        log(ERROR, f"Error for {uuid}/{keys}: {e}", context="database")
        return {key: (0, True) for key in keys}


def get_player_activity_baselines_for_members_with_db(db: DB, key: str, days: int, joined_dates_by_uuid: dict[str, datetime.date | None]) -> dict[str, tuple[int, bool]]:
    """Get one baseline key for many members in a single DB round trip.

    Returns {uuid: (value, warn_flag)} and preserves the single-member helper's
    zero-value correction behavior.
    """
    try:
        return _get_member_baselines_from_db(db, key, days, joined_dates_by_uuid)
    except Exception as e:
        log(ERROR, f"Error for member baselines {key}: {e}", context="database")
        raise BatchBaselineQueryError(f"Failed to load batched baselines for key={key}") from e


def get_members_with_baseline_history_with_db(db: DB, joined_dates_by_uuid: dict[str, datetime.date | None]) -> set[str]:
    """Return the uuids that have at least one snapshot in their current membership period.

    The baseline helpers above return (0, True) both for "no snapshot at all" and for
    a legitimate zero. Callers that turn a baseline into a delta on a lifetime-cumulative
    column (wars) must tell those apart: a 0 baseline for a member with no history yet
    credits them their entire lifetime total. Use this to drop those members instead.
    """
    if not joined_dates_by_uuid:
        return set()

    values_sql = []
    params = []
    for uuid, joined_date in joined_dates_by_uuid.items():
        values_sql.append("(%s::uuid, %s::date)")
        params.extend((uuid, joined_date))

    db.cursor.execute(f"""
        WITH input(uuid, joined_date) AS (
            VALUES {", ".join(values_sql)}
        )
        SELECT i.uuid FROM input i
        WHERE EXISTS (
            SELECT 1 FROM player_activity pa
            WHERE pa.uuid = i.uuid
              AND (i.joined_date IS NULL OR pa.snapshot_date >= i.joined_date)
        )
    """, tuple(params))
    return {str(row[0]) for row in db.cursor.fetchall()}


def _get_baseline_from_db(db: DB, uuid: str, key: str, days: int, joined_date: datetime.date = None) -> tuple:
    """Core baseline lookup logic.

    Algorithm:
    1. Find the player's snapshot closest to (but not after) N calendar days ago.
    2. If the value is 0 but later non-zero snapshots exist, walk forward to the
       first non-zero value (handles corrupted data from private profiles).
    3. If no snapshot exists at or before the target date (new player), use their
       earliest available snapshot as baseline.
    4. If no snapshots exist at all, return (0, True).

    If joined_date is provided, all queries are filtered to only consider snapshots
    from the current membership period (snapshot_date >= joined_date).
    """
    # Whitelist of allowed column names to prevent SQL injection
    ALLOWED_KEYS = {"playtime", "contributed", "wars", "raids", "shells"}
    if key not in ALLOWED_KEYS:
        return (0, True)

    if days <= 0:
        return (0, False)

    target_date = datetime.date.today() - timedelta(days=days)

    # Build conditional filter for membership period
    join_filter = ""
    join_params = ()
    if joined_date is not None:
        join_filter = "AND snapshot_date >= %s"
        join_params = (joined_date,)
        # Clamp target_date so baseline never reaches before current membership
        if target_date < joined_date:
            target_date = joined_date

    # 1. Find the player's snapshot closest to (but not after) N days ago
    db.cursor.execute(f"""
        SELECT {key}, snapshot_date FROM player_activity
        WHERE uuid = %s AND snapshot_date <= %s {join_filter}
        ORDER BY snapshot_date DESC LIMIT 1
    """, (uuid, target_date) + join_params)
    row = db.cursor.fetchone()

    if row and row[0] is not None:
        value = row[0]
        # 2. If the value is 0, check for corrupted data:
        #    walk forward to find first non-zero value after this date
        if value == 0:
            db.cursor.execute(f"""
                SELECT {key}, snapshot_date FROM player_activity
                WHERE uuid = %s AND snapshot_date > %s AND {key} > 0 {join_filter}
                ORDER BY snapshot_date ASC LIMIT 1
            """, (uuid, row[1]) + join_params)
            nonzero = db.cursor.fetchone()
            if nonzero:
                return (int(nonzero[0]), True)
        return (int(value), False)

    # 3. No snapshot at or before target date (new player):
    #    use their earliest available snapshot
    db.cursor.execute(f"""
        SELECT {key}, snapshot_date FROM player_activity
        WHERE uuid = %s {join_filter}
        ORDER BY snapshot_date ASC LIMIT 1
    """, (uuid,) + join_params)
    earliest = db.cursor.fetchone()
    if earliest and earliest[0] is not None:
        value = earliest[0]
        # Same 0-value check for earliest snapshot
        if value == 0:
            db.cursor.execute(f"""
                SELECT {key} FROM player_activity
                WHERE uuid = %s AND {key} > 0 {join_filter}
                ORDER BY snapshot_date ASC LIMIT 1
            """, (uuid,) + join_params)
            nonzero = db.cursor.fetchone()
            if nonzero:
                return (int(nonzero[0]), True)
        return (int(value), True)

    # 4. No snapshots at all (or none in current membership period)
    return (0, True)


def _get_baselines_from_db(db: DB, uuid: str, keys: list[str], days: int, joined_date: datetime.date = None) -> dict:
    ALLOWED_KEYS = {"playtime", "contributed", "wars", "raids", "shells"}
    keys = [key for key in keys if key in ALLOWED_KEYS]
    if not keys:
        return {}

    if days <= 0:
        return {key: (0, False) for key in keys}

    target_date = datetime.date.today() - timedelta(days=days)
    join_filter = ""
    join_params = ()
    if joined_date is not None:
        join_filter = "AND snapshot_date >= %s"
        join_params = (joined_date,)
        if target_date < joined_date:
            target_date = joined_date

    columns = ", ".join(keys)
    db.cursor.execute(f"""
        SELECT {columns}, snapshot_date FROM player_activity
        WHERE uuid = %s AND snapshot_date <= %s {join_filter}
        ORDER BY snapshot_date DESC LIMIT 1
    """, (uuid, target_date) + join_params)
    row = db.cursor.fetchone()

    if row:
        values = dict(zip(keys, row[:-1]))
        snapshot_date = row[-1]
        return _baseline_results_from_row(db, uuid, keys, values, snapshot_date, join_filter, join_params, warn_default=False)

    db.cursor.execute(f"""
        SELECT {columns}, snapshot_date FROM player_activity
        WHERE uuid = %s {join_filter}
        ORDER BY snapshot_date ASC LIMIT 1
    """, (uuid,) + join_params)
    earliest = db.cursor.fetchone()
    if earliest:
        values = dict(zip(keys, earliest[:-1]))
        snapshot_date = earliest[-1]
        return _baseline_results_from_row(db, uuid, keys, values, snapshot_date, join_filter, join_params, warn_default=True)

    return {key: (0, True) for key in keys}


def _baseline_results_from_row(db: DB, uuid: str, keys: list[str], values: dict, snapshot_date, join_filter: str, join_params: tuple, warn_default: bool) -> dict:
    results = {}
    for key in keys:
        value = values.get(key)
        if value is None:
            results[key] = (0, True)
            continue

        if value == 0:
            db.cursor.execute(f"""
                SELECT {key} FROM player_activity
                WHERE uuid = %s AND snapshot_date > %s AND {key} > 0 {join_filter}
                ORDER BY snapshot_date ASC LIMIT 1
            """, (uuid, snapshot_date) + join_params)
            nonzero = db.cursor.fetchone()
            if nonzero:
                results[key] = (int(nonzero[0]), True)
                continue

        results[key] = (int(value), warn_default)
    return results


def _get_member_baselines_from_db(db: DB, key: str, days: int, joined_dates_by_uuid: dict[str, datetime.date | None]) -> dict[str, tuple[int, bool]]:
    ALLOWED_KEYS = {"playtime", "contributed", "wars", "raids", "shells"}
    if key not in ALLOWED_KEYS:
        return {uuid: (0, True) for uuid in joined_dates_by_uuid}

    if not joined_dates_by_uuid:
        return {}

    if days <= 0:
        return {uuid: (0, False) for uuid in joined_dates_by_uuid}

    target_date = datetime.date.today() - timedelta(days=days)
    values_sql = []
    params = []
    for uuid, joined_date in joined_dates_by_uuid.items():
        effective_target = joined_date if joined_date is not None and joined_date > target_date else target_date
        values_sql.append("(%s::uuid, %s::date, %s::date)")
        params.extend((uuid, joined_date, effective_target))

    db.cursor.execute(f"""
        WITH input(uuid, joined_date, target_date) AS (
            VALUES {", ".join(values_sql)}
        ),
        selected AS (
            SELECT
                i.uuid,
                i.joined_date,
                COALESCE(nearest.value, earliest.value) AS value,
                COALESCE(nearest.snapshot_date, earliest.snapshot_date) AS snapshot_date,
                CASE
                    WHEN nearest.snapshot_date IS NULL AND earliest.snapshot_date IS NOT NULL THEN TRUE
                    ELSE FALSE
                END AS warn_default
            FROM input i
            LEFT JOIN LATERAL (
                SELECT pa.{key} AS value, pa.snapshot_date
                FROM player_activity pa
                WHERE pa.uuid = i.uuid
                  AND pa.snapshot_date <= i.target_date
                  AND (i.joined_date IS NULL OR pa.snapshot_date >= i.joined_date)
                ORDER BY pa.snapshot_date DESC
                LIMIT 1
            ) nearest ON TRUE
            LEFT JOIN LATERAL (
                SELECT pa.{key} AS value, pa.snapshot_date
                FROM player_activity pa
                WHERE pa.uuid = i.uuid
                  AND (i.joined_date IS NULL OR pa.snapshot_date >= i.joined_date)
                ORDER BY pa.snapshot_date ASC
                LIMIT 1
            ) earliest ON nearest.snapshot_date IS NULL
        )
        SELECT
            selected.uuid,
            selected.value,
            selected.snapshot_date,
            selected.warn_default,
            nonzero.value AS nonzero_value
        FROM selected
        LEFT JOIN LATERAL (
            SELECT pa.{key} AS value
            FROM player_activity pa
            WHERE pa.uuid = selected.uuid
              AND selected.snapshot_date IS NOT NULL
              AND pa.snapshot_date > selected.snapshot_date
              AND pa.{key} > 0
              AND (selected.joined_date IS NULL OR pa.snapshot_date >= selected.joined_date)
            ORDER BY pa.snapshot_date ASC
            LIMIT 1
        ) nonzero ON selected.value = 0
    """, tuple(params))

    results = {uuid: (0, True) for uuid in joined_dates_by_uuid}
    for uuid, value, snapshot_date, warn_default, nonzero_value in db.cursor.fetchall():
        uuid_str = str(uuid)
        if value is None or snapshot_date is None:
            results[uuid_str] = (0, True)
            continue
        if value == 0 and nonzero_value is not None:
            results[uuid_str] = (int(nonzero_value), True)
            continue
        results[uuid_str] = (int(value), bool(warn_default))
    return results


HOURLY_ACTIVITY_KEYS = {"playtime", "contributed", "wars"}
HOURLY_RETENTION_DAYS = 35


def write_hourly_activity_snapshot_with_db(db: 'DB', members: list[dict], snapshot_at: datetime.datetime):
    for m in members:
        uuid = m.get('uuid')
        if not uuid:
            continue
        db.cursor.execute("""
            INSERT INTO player_activity_hourly (uuid, snapshot_at, playtime, contributed, wars)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (uuid, snapshot_at) DO UPDATE SET
                playtime    = EXCLUDED.playtime,
                contributed = EXCLUDED.contributed,
                wars        = EXCLUDED.wars
        """, (uuid, snapshot_at, m.get('playtime'), m.get('contributed'), m.get('wars')))


def prune_hourly_activity_snapshots_with_db(db: 'DB', retention_days: int = HOURLY_RETENTION_DAYS):
    cutoff = datetime.datetime.now(datetime.timezone.utc) - timedelta(days=retention_days)
    db.cursor.execute("DELETE FROM player_activity_hourly WHERE snapshot_at < %s", (cutoff,))


def get_recent_activity_baselines_for_members_with_db(db: 'DB', key: str, days: int, joined_dates_by_uuid: dict) -> dict[str, tuple[int, bool]]:
    """Prefers hourly baselines, falls back to daily."""
    daily_results = _get_member_baselines_from_db(db, key, days, joined_dates_by_uuid)

    if key not in HOURLY_ACTIVITY_KEYS or days <= 0 or days > HOURLY_RETENTION_DAYS:
        return daily_results

    hourly_results = _get_member_hourly_baselines_from_db(db, key, days, joined_dates_by_uuid)
    for uuid, value in hourly_results.items():
        if value is not None:
            daily_results[uuid] = value
    return daily_results


def _get_member_hourly_baselines_from_db(db: 'DB', key: str, days: int, joined_dates_by_uuid: dict) -> dict:
    """Like _get_member_baselines_from_db, against the hourly table. None = no row."""
    if key not in HOURLY_ACTIVITY_KEYS or not joined_dates_by_uuid:
        return {}

    target_at = datetime.datetime.now(datetime.timezone.utc) - timedelta(days=days)
    values_sql = []
    params = []
    for uuid, joined_date in joined_dates_by_uuid.items():
        joined_at = (
            datetime.datetime.combine(joined_date, datetime.time.min, tzinfo=datetime.timezone.utc)
            if joined_date else None
        )
        effective_target = joined_at if joined_at is not None and joined_at > target_at else target_at
        values_sql.append("(%s::uuid, %s::timestamptz, %s::timestamptz)")
        params.extend((uuid, joined_at, effective_target))

    db.cursor.execute(f"""
        WITH input(uuid, joined_at, target_at) AS (
            VALUES {", ".join(values_sql)}
        )
        SELECT i.uuid, nearest.value
        FROM input i
        LEFT JOIN LATERAL (
            SELECT pah.{key} AS value
            FROM player_activity_hourly pah
            WHERE pah.uuid = i.uuid
              AND pah.{key} IS NOT NULL
              AND pah.snapshot_at <= i.target_at
              AND (i.joined_at IS NULL OR pah.snapshot_at >= i.joined_at)
            ORDER BY pah.snapshot_at DESC
            LIMIT 1
        ) nearest ON TRUE
    """, tuple(params))

    results = {}
    for uuid, value in db.cursor.fetchall():
        results[str(uuid)] = (int(value), False) if value is not None else None
    return results


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
    """Load blacklist from the blacklist table."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute("SELECT uuid, ign, reason FROM blacklist ORDER BY ign")
        return [{'UUID': row[0], 'ign': row[1], 'reason': row[2]} for row in db.cursor.fetchall()]
    except Exception:
        return []
    finally:
        db.close()


def add_blacklist_entry(uuid: str, ign: str, reason: str = None):
    """Add or update a player on the blacklist."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "INSERT INTO blacklist (uuid, ign, reason) VALUES (%s, %s, %s) "
            "ON CONFLICT (uuid) DO UPDATE SET ign = EXCLUDED.ign, reason = EXCLUDED.reason",
            (uuid, ign, reason),
        )
        db.connection.commit()
    finally:
        db.close()


def remove_blacklist_entry(uuid: str) -> bool:
    """Remove a player from the blacklist. Returns True if removed."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute("DELETE FROM blacklist WHERE uuid = %s", (uuid,))
        removed = db.cursor.rowcount > 0
        db.connection.commit()
        return removed
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


def apply_shell_delta(db: 'DB', user_id, delta_balance: int, delta_shells: int = 0, *, source: str,
                       ign: str = None, actor_id=None, actor_name: str = None, reason: str = None) -> tuple[int, int]:
    """Only place that should write to `shells`. Returns (shells_after, balance_after)."""
    if delta_shells < 0:
        raise ValueError("delta_shells must not be negative")

    user_id = str(user_id)
    db.cursor.execute("""
        INSERT INTO shells AS sh ("user", shells, balance, ign)
             VALUES (%s, %s, %s, %s)
        ON CONFLICT ("user") DO UPDATE SET
            shells  = sh.shells + EXCLUDED.shells,
            balance = sh.balance + EXCLUDED.balance,
            ign     = COALESCE(EXCLUDED.ign, sh.ign)
        RETURNING sh.shells, sh.balance
    """, (user_id, delta_shells, delta_balance, ign))
    shells_after, balance_after = db.cursor.fetchone()

    record_shell_transaction(
        db, user_id, delta_balance, delta_shells, balance_after, shells_after,
        source=source, actor_id=actor_id, actor_name=actor_name, reason=reason
    )
    return shells_after, balance_after


def record_shell_transaction(db: 'DB', user_id, delta_balance: int, delta_shells: int,
                              balance_after: int, shells_after: int, *, source: str,
                              actor_id=None, actor_name: str = None, reason: str = None):
    """Use directly when balance/shells were already updated via custom SQL."""
    db.cursor.execute("""
        INSERT INTO shell_transactions
            ("user", delta_balance, delta_shells, balance_after, shells_after, source, reason, actor_id, actor_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (str(user_id), delta_balance, delta_shells, balance_after, shells_after,
          source, reason, str(actor_id) if actor_id is not None else None, actor_name))


def get_shell_gains_for_members_with_db(db: 'DB', days: int) -> dict[str, int]:
    """Returns {uuid: gained} for the last N days; absent if no gain."""
    if days <= 0:
        return {}

    since = datetime.datetime.now(datetime.timezone.utc) - timedelta(days=days)
    db.cursor.execute("""
        SELECT dl.uuid, SUM(st.delta_shells) AS gained
        FROM shell_transactions st
        JOIN discord_links dl ON dl.discord_id = st."user"
        WHERE st.created_at >= %s AND st.delta_shells > 0
        GROUP BY dl.uuid
    """, (since,))
    return {str(row[0]): int(row[1]) for row in db.cursor.fetchall()}


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


def get_next_app_number() -> int:
    """Atomically increment and return the next application number.

    Requires a bot_settings table:
        CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO bot_settings (key, value) VALUES ('app_counter', '3725') ON CONFLICT DO NOTHING;
    """
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "UPDATE bot_settings SET value = (value::int + 1)::text "
            "WHERE key = 'app_counter' RETURNING value::int"
        )
        row = db.cursor.fetchone()
        db.connection.commit()
        return row[0]
    finally:
        db.close()


def get_next_hh_app_number() -> int:
    """Atomically increment and return the next hammerhead application number."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "UPDATE bot_settings SET value = (value::int + 1)::text "
            "WHERE key = 'hh_app_counter' RETURNING value::int"
        )
        row = db.cursor.fetchone()
        db.connection.commit()
        return row[0]
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
