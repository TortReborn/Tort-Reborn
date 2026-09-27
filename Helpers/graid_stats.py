"""Aggregation over the guild raids we track ourselves.

Source of truth is graid_logs / graid_log_participants, not the Wynncraft API.
Players are keyed by uuid so a rename does not split them; graid_raid_offsets
are historical corrections and only apply to an all-time range.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

RAID_NAMES = [
    "Nest of the Grootslangs",
    "The Canyon Colossus",
    "The Nameless Anomaly",
    "Orphion's Nexus of Light",
    "The Wartorn Palace",
]

RAID_SHORT = {
    "Nest of the Grootslangs": "NOTG",
    "The Canyon Colossus": "TCC",
    "The Nameless Anomaly": "TNA",
    "Orphion's Nexus of Light": "NOL",
    "The Wartorn Palace": "WTP",
}

RAID_SHORT_TO_FULL = {v: k for k, v in RAID_SHORT.items()}

RAID_ORDER = ("NOTG", "TCC", "TNA", "NOL", "WTP")
UNKNOWN = "Unknown"

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

RANGE_CHOICES = ["All-Time", "30 Days", "7 Days", "Active Event"]

# The full board runs to hundreds of players; past this it is all paginator.
LEADERBOARD_DEFAULT_LIMIT = 60

_IDENT = "COALESCE(glp.uuid::text, 'ign:' || LOWER(glp.ign))"

# uuid is unique in discord_links (discord_links_uuid_uq), but LATERAL keeps
# the display-name join single-row regardless of what the table grows into.
_DISPLAY_JOIN = """
    LEFT JOIN LATERAL (
        SELECT ign FROM discord_links WHERE uuid = glp.uuid LIMIT 1
    ) dl ON TRUE
"""


def raid_short(raid_type):
    if not raid_type:
        return UNKNOWN
    return RAID_SHORT.get(raid_type, UNKNOWN)


def empty_counts():
    return {short: 0 for short in RAID_ORDER + (UNKNOWN,)}


@dataclass(frozen=True)
class RaidRange:
    label: str
    since: datetime | None = None
    until: datetime | None = None

    @property
    def all_time(self) -> bool:
        return self.since is None and self.until is None

    def where(self, alias: str = "gl") -> tuple[str, list]:
        clauses, params = [], []
        if self.since is not None:
            clauses.append(f"{alias}.completed_at >= %s")
            params.append(self.since)
        if self.until is not None:
            clauses.append(f"{alias}.completed_at <= %s")
            params.append(self.until)
        return (" AND ".join(clauses), params)


def resolve_range(cursor, choice: str | None) -> RaidRange:
    choice = choice or "All-Time"
    now = datetime.now(timezone.utc)

    if choice == "Active Event":
        cursor.execute(
            "SELECT title, start_ts, end_ts FROM graid_events WHERE active = TRUE"
            " ORDER BY start_ts DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if row:
            return RaidRange(label=row[0], since=row[1], until=row[2])
        # No event running, so the command shows all-time rather than nothing.
        return RaidRange(label="All-Time")

    days = {"30 Days": 30, "7 Days": 7}.get(choice)
    if days:
        return RaidRange(label=choice, since=now - timedelta(days=days))
    return RaidRange(label="All-Time")


def _where(rng: RaidRange, extra: list[str] | None = None, params: list | None = None):
    clause, rng_params = rng.where()
    clauses = [c for c in ([clause] if clause else []) + (extra or [])]
    sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return sql, rng_params + (params or [])


def compute_streaks(days: list) -> tuple[int, int]:
    """Best and current run of consecutive raid days. A current streak only
    counts when it reaches today or yesterday; a finished one reads as zero."""
    if not days:
        return 0, 0
    day_set = set(days)
    ordered = sorted(day_set)
    best = run = 1
    for prev, day in zip(ordered, ordered[1:]):
        run = run + 1 if (day - prev).days == 1 else 1
        best = max(best, run)

    today = datetime.now(timezone.utc).date()
    start = today if today in day_set else (
        today - timedelta(days=1) if today - timedelta(days=1) in day_set else None
    )
    current = 0
    while start in day_set:
        current += 1
        start -= timedelta(days=1)
    return best, current


@dataclass
class Board:
    """Every tracked player's per-raid-type counts for one range."""

    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)

    def rank_of(self, ident: str, short: str | None = None) -> tuple[int, int]:
        """(placement, field size) by total, or by one raid type. A player with
        none of that raid is not in the field."""
        if short is None:
            scores = self.totals
        else:
            scores = {k: c[short] for k, c in self.counts.items() if c.get(short)}
        if ident not in scores:
            return 0, len(scores)
        mine = scores[ident]
        return sum(1 for value in scores.values() if value > mine) + 1, len(scores)

    def ordered(self, short: str | None = None) -> list[tuple[str, int]]:
        scores = self.totals if short is None else {
            k: c.get(short, 0) for k, c in self.counts.items()
        }
        return sorted(scores.items(), key=lambda kv: (-kv[1], self.names.get(kv[0], "").lower()))


def load_board(cursor, rng: RaidRange) -> Board:
    where, params = _where(rng)
    cursor.execute(
        f"""SELECT {_IDENT} AS ident, gl.raid_type, COUNT(*)
            FROM graid_log_participants glp
            JOIN graid_logs gl ON glp.log_id = gl.id
            {where}
            GROUP BY ident, gl.raid_type""",
        params,
    )
    board = Board()
    for ident, raid_type, count in cursor.fetchall():
        counts = board.counts.setdefault(ident, empty_counts())
        counts[raid_short(raid_type)] += count
        board.totals[ident] = board.totals.get(ident, 0) + count

    if rng.all_time:
        cursor.execute("SELECT uuid::text, raid_offset FROM graid_raid_offsets")
        for ident, offset in cursor.fetchall():
            board.counts.setdefault(ident, empty_counts())
            board.totals[ident] = board.totals.get(ident, 0) + offset

    cursor.execute(
        f"""SELECT {_IDENT} AS ident,
                   COALESCE(MAX(dl.ign),
                            (array_agg(glp.ign ORDER BY glp.log_id DESC)
                             FILTER (WHERE glp.ign IS NOT NULL))[1]) AS display_name
            FROM graid_log_participants glp
            {_DISPLAY_JOIN}
            GROUP BY ident"""
    )
    board.names = {ident: name for ident, name in cursor.fetchall() if name}
    for ident in board.totals:
        board.names.setdefault(ident, ident)
    return board


@dataclass
class PlayerRaidStats:
    ign: str
    ident: str
    total: int
    counts: dict[str, int]
    type_ranks: dict[str, tuple[int, int]]
    overall_rank: tuple[int, int]
    guild_share: float
    best_streak: int
    current_streak: int
    best_day: tuple[object, int] | None
    first_raid: datetime | None
    latest_raid: datetime | None
    weekdays: dict[str, int]
    partners: list[tuple[str, int]]
    recent: list[tuple[datetime, str, list[str]]]


def has_tracked_raids(cursor, uuid) -> bool:
    cursor.execute(
        "SELECT 1 FROM graid_log_participants WHERE uuid = %s::uuid LIMIT 1", (str(uuid),)
    )
    return cursor.fetchone() is not None


def player_raid_stats(cursor, uuid, ign: str, rng: RaidRange) -> PlayerRaidStats | None:
    ident = str(uuid)
    where, params = _where(rng, ["glp.uuid = %s::uuid"], [ident])
    cursor.execute(
        f"""SELECT gl.id, gl.raid_type, gl.completed_at
            FROM graid_log_participants glp
            JOIN graid_logs gl ON glp.log_id = gl.id
            {where}
            ORDER BY gl.completed_at DESC""",
        params,
    )
    rows = cursor.fetchall()
    if not rows:
        return None

    board = load_board(cursor, rng)
    counts = board.counts.get(ident, empty_counts())
    # graid_raid_offsets credits raids from before tracking: it belongs in the
    # leaderboard standing, not in a count of what we logged.
    total = len(rows)

    guild_where, guild_params = _where(rng)
    cursor.execute(f"SELECT COUNT(*) FROM graid_logs gl {guild_where}", guild_params)
    guild_total = cursor.fetchone()[0]

    days = [completed.date() for _, _, completed in rows]
    best_streak, current_streak = compute_streaks(days)
    day_counts = Counter(days)
    weekdays = {name: 0 for name in WEEKDAYS}
    for day in days:
        weekdays[WEEKDAYS[day.weekday()]] += 1

    log_ids = [row[0] for row in rows]
    partners = _partners(cursor, log_ids, ident)
    recent = _recent(cursor, log_ids[:5])

    return PlayerRaidStats(
        ign=ign,
        ident=ident,
        total=total,
        counts=counts,
        type_ranks={short: board.rank_of(ident, short) for short in RAID_ORDER},
        overall_rank=board.rank_of(ident),
        guild_share=(total / guild_total) if guild_total else 0.0,
        best_streak=best_streak,
        current_streak=current_streak,
        best_day=day_counts.most_common(1)[0] if day_counts else None,
        first_raid=rows[-1][2],
        latest_raid=rows[0][2],
        weekdays=weekdays,
        partners=partners,
        recent=recent,
    )


def _partners(cursor, log_ids: list[int], ident: str, limit: int = 5) -> list[tuple[str, int]]:
    if not log_ids:
        return []
    cursor.execute(
        f"""SELECT {_IDENT} AS ident,
                   COALESCE(MAX(dl.ign),
                            (array_agg(glp.ign ORDER BY glp.log_id DESC)
                             FILTER (WHERE glp.ign IS NOT NULL))[1]) AS display_name,
                   COUNT(*)
            FROM graid_log_participants glp
            {_DISPLAY_JOIN}
            WHERE glp.log_id = ANY(%s) AND {_IDENT} <> %s
            GROUP BY ident
            ORDER BY COUNT(*) DESC
            LIMIT %s""",
        (log_ids, ident, limit),
    )
    return [(name or key, count) for key, name, count in cursor.fetchall()]


def _recent(cursor, log_ids: list[int]) -> list[tuple[datetime, str, list[str]]]:
    if not log_ids:
        return []
    cursor.execute(
        f"""SELECT gl.id, gl.raid_type, gl.completed_at,
                   COALESCE(dl.ign, glp.ign) AS display_name
            FROM graid_logs gl
            JOIN graid_log_participants glp ON glp.log_id = gl.id
            {_DISPLAY_JOIN}
            WHERE gl.id = ANY(%s)
            ORDER BY gl.completed_at DESC""",
        (log_ids,),
    )
    grouped: dict[int, list] = defaultdict(list)
    meta: dict[int, tuple] = {}
    for log_id, raid_type, completed, name in cursor.fetchall():
        meta[log_id] = (completed, raid_short(raid_type))
        if name:
            grouped[log_id].append(name)
    ordered = sorted(meta.items(), key=lambda kv: kv[1][0], reverse=True)
    return [(meta[i][0], meta[i][1], sorted(grouped[i])) for i, _ in ordered]


@dataclass
class LeaderboardRow:
    placement: int
    ign: str
    total: int
    counts: dict[str, int]


def leaderboard(cursor, rng: RaidRange, sort: str | None = None,
                limit: int | None = LEADERBOARD_DEFAULT_LIMIT) -> list[LeaderboardRow]:
    board = load_board(cursor, rng)
    short = sort.upper() if sort and sort.upper() in RAID_ORDER else None
    ordered = board.ordered(short)
    if short is not None:
        ordered = [(ident, value) for ident, value in ordered if value]
    if limit:
        ordered = ordered[:limit]
    return [
        LeaderboardRow(
            placement=i,
            ign=board.names.get(ident, ident),
            total=board.totals.get(ident, 0),
            counts=board.counts.get(ident, empty_counts()),
        )
        for i, (ident, _) in enumerate(ordered, 1)
    ]


@dataclass
class GuildRaidStats:
    range_label: str
    total_raids: int
    unique_players: int
    counts: dict[str, int]
    top_players: list[tuple[str, int]]
    weekly: list[tuple[object, int]]
    weekdays: dict[str, int]
    duos: list[tuple[str, str, int]]
    event: dict | None


def guild_stats(cursor, rng: RaidRange, top: int = 5) -> GuildRaidStats:
    where, params = _where(rng)
    cursor.execute(
        f"""SELECT gl.raid_type, COUNT(*) FROM graid_logs gl {where} GROUP BY gl.raid_type""",
        params,
    )
    counts = empty_counts()
    for raid_type, count in cursor.fetchall():
        counts[raid_short(raid_type)] += count
    total_raids = sum(counts.values())

    # Offsets are per player, so they cannot be added to a raid count: four
    # participants in one missed raid carry four rows and would count it four
    # times.
    board = load_board(cursor, rng)
    top_players = [
        (board.names.get(ident, ident), value) for ident, value in board.ordered()[:top]
    ]

    cursor.execute(
        f"""SELECT DATE_TRUNC('week', gl.completed_at) AS week, COUNT(*)
            FROM graid_logs gl {where} GROUP BY week ORDER BY week""",
        params,
    )
    weekly = [(week, count) for week, count in cursor.fetchall()]

    cursor.execute(
        f"""SELECT EXTRACT(ISODOW FROM gl.completed_at)::int, COUNT(*)
            FROM graid_logs gl {where} GROUP BY 1""",
        params,
    )
    weekdays = {name: 0 for name in WEEKDAYS}
    for isodow, count in cursor.fetchall():
        weekdays[WEEKDAYS[isodow - 1]] = count

    return GuildRaidStats(
        range_label=rng.label,
        total_raids=total_raids,
        unique_players=len(board.totals),
        counts=counts,
        top_players=top_players,
        weekly=weekly,
        weekdays=weekdays,
        duos=_duos(cursor, rng),
        event=_active_event(cursor),
    )


def _duos(cursor, rng: RaidRange, limit: int = 5) -> list[tuple[str, str, int]]:
    """Pairs who raid together most. The `<` on uuid counts each pair once."""
    where, params = _where(rng)
    cursor.execute(
        f"""SELECT COALESCE(MAX(a_name.ign), MAX(a.ign)) AS left_name,
                   COALESCE(MAX(b_name.ign), MAX(b.ign)) AS right_name,
                   COUNT(*)
            FROM graid_log_participants a
            JOIN graid_log_participants b
              ON a.log_id = b.log_id AND a.uuid < b.uuid
            JOIN graid_logs gl ON gl.id = a.log_id
            LEFT JOIN LATERAL (
                SELECT ign FROM discord_links WHERE uuid = a.uuid LIMIT 1
            ) a_name ON TRUE
            LEFT JOIN LATERAL (
                SELECT ign FROM discord_links WHERE uuid = b.uuid LIMIT 1
            ) b_name ON TRUE
            {where}
            GROUP BY a.uuid, b.uuid
            ORDER BY COUNT(*) DESC
            LIMIT %s""",
        params + [limit],
    )
    return [(left, right, count) for left, right, count in cursor.fetchall()]


def _active_event(cursor) -> dict | None:
    cursor.execute(
        "SELECT id, title, start_ts, end_ts FROM graid_events WHERE active = TRUE"
        " ORDER BY start_ts DESC LIMIT 1"
    )
    row = cursor.fetchone()
    if not row:
        return None
    event_id, title, start_ts, end_ts = row
    cursor.execute(
        """SELECT COALESCE(dl.ign,
                           (SELECT ign FROM graid_log_participants
                            WHERE uuid = t.uuid ORDER BY log_id DESC LIMIT 1)) AS display_name,
                  t.total
           FROM graid_event_totals t
           LEFT JOIN LATERAL (
               SELECT ign FROM discord_links WHERE uuid = t.uuid LIMIT 1
           ) dl ON TRUE
           WHERE t.event_id = %s
           ORDER BY t.total DESC
           LIMIT 3""",
        (event_id,),
    )
    return {
        "title": title,
        "start": start_ts,
        "end": end_ts,
        "top": [(name, total) for name, total in cursor.fetchall()],
    }


@dataclass
class LogEntry:
    ordinal: int
    completed_at: datetime
    short: str
    participants: list[str]


def log_entries(cursor, rng: RaidRange, ign: str | None = None,
                raid_type: str | None = None, limit: int = 100) -> list[LogEntry]:
    extra, params = [], []
    if ign:
        extra.append(
            "gl.id IN (SELECT log_id FROM graid_log_participants WHERE LOWER(ign) = LOWER(%s))"
        )
        params.append(ign)
    if raid_type:
        short = raid_type.upper()
        if short == UNKNOWN.upper():
            extra.append("gl.raid_type IS NULL")
        elif short in RAID_SHORT_TO_FULL:
            extra.append("gl.raid_type = %s")
            params.append(RAID_SHORT_TO_FULL[short])

    where, all_params = _where(rng, extra, params)
    cursor.execute(
        f"""SELECT gl.id, gl.raid_type, gl.completed_at
            FROM graid_logs gl
            {where}
            ORDER BY gl.completed_at DESC
            LIMIT %s""",
        all_params + [limit],
    )
    rows = cursor.fetchall()
    if not rows:
        return []

    # Numbered from the oldest match, so a raid keeps its number as newer ones
    # are logged. The page only holds the newest slice, hence the count.
    cursor.execute(f"SELECT COUNT(*) FROM graid_logs gl {where}", all_params)
    matched = cursor.fetchone()[0]

    cursor.execute(
        f"""SELECT glp.log_id, COALESCE(dl.ign, glp.ign) AS display_name
            FROM graid_log_participants glp
            {_DISPLAY_JOIN}
            WHERE glp.log_id = ANY(%s)""",
        ([row[0] for row in rows],),
    )
    names: dict[int, list[str]] = defaultdict(list)
    for log_id, name in cursor.fetchall():
        if name:
            names[log_id].append(name)

    return [
        LogEntry(ordinal=matched - index, completed_at=completed,
                 short=raid_short(raid_type), participants=sorted(names[log_id]))
        for index, (log_id, raid_type, completed) in enumerate(rows)
    ]
