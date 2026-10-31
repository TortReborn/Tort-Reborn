"""
Tome requests (TAQ-116): the rules, a member's standing, and making a request.

A tome is earned one of two ways, and a request is for one of them:

- **War tomes** cost nothing beyond the milestone: lifetime wars (the API's
  `globalData.wars`, every guild) of 10, 25, 50 and 100 for the first four,
  then every additional 50.
- **Raid tomes** are bought with shells: 10 each for the first four raid
  tomes, 20 each after. Only raid tomes count towards that price, and only
  war tomes towards the milestones.

At most MAX_QUEUED tomes wait in a member's queue at once; a request that
would go past it puts the rest in 'give when available'. Tomes given before
they were earned are recorded as debt and count as war tomes received, so
later wars pay them off.

Keep in step with TAq-Website/lib/tomes.ts, which shows the same numbers on
the exec Tome Queue page. Nothing here commits: callers do, so a request and
its shell spend land together or not at all.
"""

from dataclasses import dataclass

from Helpers.database import apply_shell_delta

KINDS = ("war", "raid")
KIND_LABELS = {"war": "War tomes", "raid": "Raid tomes"}

# The priorities members choose from, as the old pinned post described them.
PRIORITIES = {
    "low": ("Low", "💚", "Want a tome but have no current use for it"),
    "medium": ("Medium", "💛", "Need it for a non-war build or class"),
    "high": ("High", "🧡", "Need it for a war build, to boost DPS or general stats"),
    "very_high": ("Very High", "❤️", "Need it for a war build to actually work"),
}

MAX_QUEUED = 100
WAR_MILESTONES = (10, 25, 50, 100)  # then every additional WAR_STEP
WAR_STEP = 50
RAID_PRICES = (10, 10, 10, 10)      # then RAID_PRICE_AFTER each
RAID_PRICE_AFTER = 20


class TomeError(ValueError):
    """A request that can't be made; the message is for the member."""


def wars_needed(k: int) -> int:
    """Lifetime wars needed for the k-th war tome (1-based)."""
    if k <= len(WAR_MILESTONES):
        return WAR_MILESTONES[k - 1]
    return WAR_MILESTONES[-1] + WAR_STEP * (k - len(WAR_MILESTONES))


def war_tomes_earned(wars: int) -> int:
    """How many war tomes `wars` lifetime wars have earned."""
    earned = sum(1 for m in WAR_MILESTONES if wars >= m)
    if earned == len(WAR_MILESTONES):
        earned += (wars - WAR_MILESTONES[-1]) // WAR_STEP
    return earned


def raid_tome_price(k: int) -> int:
    """Shells for the k-th raid tome (1-based)."""
    return RAID_PRICES[k - 1] if k <= len(RAID_PRICES) else RAID_PRICE_AFTER


def raid_tomes_cost(already: int, n: int) -> int:
    """Shells for n more raid tomes when `already` are received or queued."""
    return sum(raid_tome_price(k) for k in range(already + 1, already + n + 1))


@dataclass
class Standing:
    """Where a member stands, per kind. `wars` is None when their war count
    is hidden and the bot has no snapshot of it either."""
    wars: int | None
    war_received: int
    war_queued: int
    raid_received: int
    raid_queued: int
    shells: int
    queued_total: int  # tomes waiting in the 'queue' bucket, both kinds

    @property
    def war_earned(self) -> int:
        return war_tomes_earned(self.wars or 0)

    @property
    def war_available(self) -> int:
        return max(self.war_earned - self.war_received - self.war_queued, 0)

    @property
    def next_war_tome_at(self) -> int:
        """Lifetime wars needed for the next war tome they could request."""
        return wars_needed(max(self.war_earned, self.war_received + self.war_queued) + 1)

    @property
    def raid_bought(self) -> int:
        return self.raid_received + self.raid_queued

    @property
    def next_raid_price(self) -> int:
        return raid_tome_price(self.raid_bought + 1)

    @property
    def raid_affordable(self) -> int:
        n = 0
        while raid_tomes_cost(self.raid_bought, n + 1) <= self.shells:
            n += 1
        return n

    def available(self, kind: str) -> int:
        return self.war_available if kind == "war" else self.raid_affordable


def snapshot_wars(cursor, uuid) -> int | None:
    """The latest war count the bot's daily snapshots hold, for members
    whose count is hidden in the API right now."""
    cursor.execute(
        "SELECT wars FROM player_activity WHERE uuid = %s AND wars > 0 ORDER BY snapshot_date DESC LIMIT 1",
        (str(uuid),),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def standing(cursor, uuid, discord_id, wars: int | None) -> Standing:
    cursor.execute(
        """
        SELECT kind, COALESCE(SUM(count), 0) FROM tome_events
        WHERE uuid = %s AND action IN ('given', 'debt', 'adjusted') AND kind IS NOT NULL
        GROUP BY kind
        """,
        (str(uuid),),
    )
    received = dict(cursor.fetchall())
    cursor.execute(
        """
        SELECT kind, bucket, COALESCE(SUM(count - given), 0) FROM tome_requests
        WHERE uuid = %s AND status = 'open' AND bucket <> 'dormant'
        GROUP BY kind, bucket
        """,
        (str(uuid),),
    )
    queued, in_queue = {}, 0
    for kind, bucket, n in cursor.fetchall():
        queued[kind] = queued.get(kind, 0) + n
        if bucket == "queue":
            in_queue += n
    cursor.execute('SELECT balance FROM shells WHERE "user" = %s', (str(discord_id),))
    row = cursor.fetchone()
    return Standing(
        wars=wars,
        war_received=received.get("war", 0), war_queued=queued.get("war", 0),
        raid_received=received.get("raid", 0), raid_queued=queued.get("raid", 0),
        shells=row[0] if row else 0, queued_total=in_queue,
    )


def request_tomes(db, *, uuid, discord_id: int, ign: str, kind: str, count: int, priority: str,
                  wars: int | None) -> list[dict]:
    """Queue `count` tomes of `kind` for a member: check they can have them,
    take the shells for raid tomes, and put anything past MAX_QUEUED in
    'give when available'. Returns the requests made ({id, bucket, count,
    shells}). Raises TomeError with a message for the member."""
    if kind not in KINDS:
        raise TomeError("Pick war tomes or raid tomes.")
    if priority not in PRIORITIES:
        raise TomeError("Pick a priority.")
    if count < 1:
        raise TomeError("Ask for at least one tome.")

    c = db.cursor
    # One request at a time per member: two clicks can't spend the same
    # milestones or shells twice.
    c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 116))", (str(uuid),))
    if kind == "raid":
        c.execute('SELECT 1 FROM shells WHERE "user" = %s FOR UPDATE', (str(discord_id),))
    s = standing(c, uuid, discord_id, wars)

    shells = 0
    if kind == "war":
        if s.wars is None:
            raise TomeError("Your war count is hidden. Make your stats public in Wynncraft's privacy settings and try again.")
        if count > s.war_available:
            raise TomeError(
                f"You can request {s.war_available} war tome(s) right now. "
                f"The next one needs {s.next_war_tome_at} lifetime wars (you have {s.wars})."
            )
    else:
        shells = raid_tomes_cost(s.raid_bought, count)
        if shells > s.shells:
            raise TomeError(
                f"{count} raid tome(s) cost {shells} shells and you have {s.shells}. "
                f"You can afford {s.raid_affordable}."
            )

    room = max(MAX_QUEUED - s.queued_total, 0)
    parts = [(b, n) for b, n in (("queue", min(count, room)), ("give_when_available", count - min(count, room))) if n]
    made = []
    for i, (bucket, n) in enumerate(parts):
        spent = shells if i == 0 else 0  # the shells ride on the first part
        c.execute(
            """
            INSERT INTO tome_requests (uuid, discord_id, ign, kind, count, priority, bucket, shells_spent)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (str(uuid), discord_id, ign, kind, n, priority, bucket, spent),
        )
        rid = c.fetchone()[0]
        c.execute(
            """
            INSERT INTO tome_events (uuid, request_id, action, kind, count, actor_id, actor_name, note)
            VALUES (%s, %s, 'requested', %s, %s, %s, %s, %s)
            """,
            (str(uuid), rid, kind, n, discord_id, ign, None if bucket == "queue" else "past the queue limit"),
        )
        made.append({"id": rid, "bucket": bucket, "count": n, "shells": spent})

    if shells:
        apply_shell_delta(db, discord_id, -shells, source="tome_request", ign=ign,
                          reason=f"{count} raid tome(s), request #{made[0]['id']}")
    c.execute(
        "INSERT INTO audit_log (log_type, actor_name, actor_id, action) VALUES ('tome', %s, %s, %s)",
        (ign, discord_id, f"Requested {count} {kind} tome(s) at {priority} priority"
                          + (f" for {shells} shells" if shells else "")),
    )
    return made
