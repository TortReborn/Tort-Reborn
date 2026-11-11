"""
Tome requests (TAQ-116): the rules, a member's standing, and making a request.

A tome is earned one of two ways, and a request is for one of them:

- **War tomes** cost nothing beyond the milestone: wars done in TAq (across
  every stint, Helpers/roster.taq_wars; wars in other guilds don't count) of
  10, 25, 50 and 100 for the first four, then every additional 50.
- **Shell tomes** are bought with shells: 10 each for the first four, 20 each
  after. Only shell tomes count towards that price, and only war tomes towards
  the milestones. Stored as kind 'raid'; members only ever see "shell".

Every request joins the queue; only exec moves one to 'give when available'
or 'give when full', on the website. A member who leaves the guild still has
their tomes owed: sync_membership moves their requests to 'dormant', and back
where they were if they return. Tomes given before they were earned are recorded as debt and count as war tomes received, so
later wars pay them off.

Keep in step with TAq-Website/lib/tomes.ts, which shows the same numbers on
the exec Distribution page. Nothing here commits: callers do, so a request and
its shell spend land together or not at all.
"""

from dataclasses import dataclass

from Helpers import roster
from Helpers.database import DB, apply_shell_delta

KINDS = ("war", "raid")
KIND_LABELS = {"war": "War tomes", "raid": "Shell tomes"}
KIND_WORDS = {"war": "war", "raid": "shell"}
BUCKET_LABELS = {"give_when_available": "Give when available", "give_when_full": "Give when full"}

# The priorities members choose from, as the old pinned post described them.
PRIORITIES = {
    "very_low": ("Very Low", "\U0001FA76", "Only if there are tomes to spare"),
    "low": ("Low", "💚", "Want a tome but have no current use for it"),
    "medium": ("Medium", "💛", "Need it for a non-war build or class"),
    "high": ("High", "🧡", "Need it for a war build, to boost DPS or general stats"),
    "very_high": ("Very High", "❤️", "Need it for a war build to actually work"),
}

WAR_MILESTONES = (10, 25, 50, 100)  # then every additional WAR_STEP
WAR_STEP = 50
RAID_PRICES = (10, 10, 10, 10)      # then RAID_PRICE_AFTER each
RAID_PRICE_AFTER = 20
QUEUE_TEXT_LIMIT = 3900  # leaves an embed description room for the panel's own words


class TomeError(ValueError):
    """A request that can't be made; the message is for the member."""


def wars_needed(k: int) -> int:
    """TAq wars needed for the k-th war tome (1-based)."""
    if k <= len(WAR_MILESTONES):
        return WAR_MILESTONES[k - 1]
    return WAR_MILESTONES[-1] + WAR_STEP * (k - len(WAR_MILESTONES))


def war_tomes_earned(wars: int) -> int:
    """How many war tomes `wars` TAq wars have earned."""
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

    @property
    def war_earned(self) -> int:
        return war_tomes_earned(self.wars or 0)

    @property
    def war_available(self) -> int:
        return max(self.war_earned - self.war_received - self.war_queued, 0)

    @property
    def next_war_tome_at(self) -> int:
        """TAq wars needed for the next war tome they could request."""
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
    """The latest lifetime war count the bot's daily snapshots hold, for
    members whose count is hidden in the API right now."""
    cursor.execute(
        "SELECT wars FROM player_activity WHERE uuid = %s AND wars > 0 ORDER BY snapshot_date DESC LIMIT 1",
        (str(uuid),),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def taq_wars_for(cursor, uuid, live_wars: int | None) -> int | None:
    """Wars done in TAq, which is what war tomes reward. The open stint runs
    to ``live_wars``, the API's lifetime count, or while that is hidden to the
    bot's last reading. None when nothing can be counted: no stint, or wars
    hidden since joining (no baseline yet)."""
    current = live_wars if live_wars is not None else snapshot_wars(cursor, uuid)
    result = roster.taq_wars(cursor, uuid, current)
    return result.total if result else None


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
        SELECT kind, COALESCE(SUM(count - given), 0) FROM tome_requests
        WHERE uuid = %s AND status = 'open' AND bucket <> 'dormant'
        GROUP BY kind
        """,
        (str(uuid),),
    )
    queued = dict(cursor.fetchall())
    cursor.execute('SELECT balance FROM shells WHERE "user" = %s', (str(discord_id),))
    row = cursor.fetchone()
    return Standing(
        wars=wars,
        war_received=received.get("war", 0), war_queued=queued.get("war", 0),
        raid_received=received.get("raid", 0), raid_queued=queued.get("raid", 0),
        shells=row[0] if row else 0,
    )


def request_tomes(db, *, uuid, discord_id: int, ign: str, kind: str, count: int, priority: str,
                  wars: int | None) -> dict:
    """Queue `count` tomes of `kind` for a member: check they can have them and
    take the shells for raid tomes. Returns the request made ({id, count,
    shells}). Raises TomeError with a message for the member."""
    if kind not in KINDS:
        raise TomeError("Pick war tomes or shell tomes.")
    if priority not in PRIORITIES:
        raise TomeError("Pick a priority.")
    if count < 1:
        raise TomeError("Ask for at least one tome.")

    c = db.cursor
    c.execute("SELECT 1 FROM guild_roster WHERE uuid = %s", (str(uuid),))
    if c.fetchone() is None:
        raise TomeError("You need to be in the guild to request tomes.")
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
                f"The next one needs {s.next_war_tome_at} TAq wars (you have {s.wars})."
            )
    else:
        shells = raid_tomes_cost(s.raid_bought, count)
        if shells > s.shells:
            raise TomeError(
                f"{count} shell tome(s) cost {shells} shells and you have {s.shells}. "
                f"You can afford {s.raid_affordable}."
            )

    c.execute(
        """
        INSERT INTO tome_requests (uuid, discord_id, ign, kind, count, priority, bucket, shells_spent)
        VALUES (%s, %s, %s, %s, %s, %s, 'queue', %s) RETURNING id
        """,
        (str(uuid), discord_id, ign, kind, count, priority, shells),
    )
    rid = c.fetchone()[0]
    c.execute(
        """
        INSERT INTO tome_events (uuid, request_id, action, kind, count, actor_id, actor_name, shells)
        VALUES (%s, %s, 'requested', %s, %s, %s, %s, %s)
        """,
        (str(uuid), rid, kind, count, discord_id, ign, shells),
    )
    if shells:
        apply_shell_delta(db, discord_id, -shells, source="tome_request", ign=ign,
                          reason=f"{count} shell tome(s), request #{rid}")
    c.execute(
        "INSERT INTO audit_log (log_type, actor_name, actor_id, action) VALUES ('tome', %s, %s, %s)",
        (ign, discord_id, f"Requested {count} {KIND_WORDS[kind]} tome(s) at {priority} priority"
                          + (f" for {shells} shells" if shells else "")),
    )
    return {"id": rid, "count": count, "shells": shells}


def open_queue(cursor) -> list[tuple]:
    """(ign, tomes left, priority, bucket, discord id) in the order tomes are given
    out. Those a give would skip, dormant requests and members not in the
    guild, aren't here. Keep in step with servingOrder in TAq-Website/lib/tomes.ts."""
    cursor.execute(
        """
        SELECT r.ign, r.count - r.given, r.priority, r.bucket,
               COALESCE(r.discord_id, (SELECT l.discord_id FROM discord_links l WHERE l.uuid = r.uuid LIMIT 1))
        FROM tome_requests r
        WHERE r.status = 'open' AND r.bucket <> 'dormant' AND r.count > r.given
          AND EXISTS (SELECT 1 FROM guild_roster g WHERE g.uuid = r.uuid)
        ORDER BY array_position(ARRAY['queue', 'give_when_available', 'give_when_full']::varchar[], r.bucket::varchar),
                 array_position(ARRAY['very_high', 'high', 'medium', 'low', 'very_low']::varchar[], r.priority::varchar),
                 r.created_at, r.id
        """
    )
    return cursor.fetchall()


def queue_text(rows) -> str:
    """The queue as the panels' {{tome_queue}} shows it: one line a member per
    section, with what would not fit counted at the end. Members are mentioned
    (a mention in an embed never pings), or named if they have no linked Discord
    account. Keep in step with
    queueText in TAq-Website/lib/tomes.ts."""
    if not rows:
        return "Nothing is waiting."
    sections: dict[tuple, dict[str, int]] = {}
    for ign, left, priority, bucket, discord_id in rows:
        members = sections.setdefault((bucket, priority if bucket == "queue" else None), {})
        who = f"<@{discord_id}>" if discord_id else f"`{ign}`"
        members[who] = members.get(who, 0) + left

    lines: list[tuple[str, str | None, int]] = []
    for (bucket, priority), members in sections.items():
        if bucket == "queue":
            label, emoji, _ = PRIORITIES[priority]
            lines.append((f"\n{emoji} **{label}**", None, 0))
        else:
            lines.append((f"\n**{BUCKET_LABELS[bucket]}**", None, 0))
        for who, n in members.items():
            lines.append((f"{who}: {n}", who, n))

    text = ""
    for i, (line, _, _) in enumerate(lines):
        if len(text) + len(line) + 1 > QUEUE_TEXT_LIMIT - 60:  # room for the line below
            rest = [(ign, n) for _, ign, n in lines[i:] if ign]
            who = len({ign for ign, _ in rest})
            return f"{text.lstrip()}\n…and {who} more member{'s' if who != 1 else ''} ({sum(n for _, n in rest)} tomes)"
        text += "\n" + line
    return text.lstrip()


def load_queue_text() -> str:
    """The current queue text (reads the database; blocking, run in a thread)."""
    db = DB(); db.connect()
    try:
        return queue_text(open_queue(db.cursor))
    finally:
        db.close()


def sync_membership(db) -> tuple[int, int]:
    """Move open requests of members who left the guild to 'dormant', and those
    of members who returned back where they were. Returns (moved out, moved
    back). An empty roster means the roster failed, not that everyone left.
    Requests exec made dormant by hand stay put. Nothing here commits."""
    c = db.cursor
    c.execute(
        """
        UPDATE tome_requests r SET left_from = r.bucket, bucket = 'dormant', updated_at = NOW()
        WHERE r.status = 'open' AND r.bucket <> 'dormant'
          AND NOT EXISTS (SELECT 1 FROM guild_roster g WHERE g.uuid = r.uuid)
          AND EXISTS (SELECT 1 FROM guild_roster)
        RETURNING r.id, r.uuid::text, r.ign, r.left_from
        """
    )
    out = c.fetchall()
    c.execute(
        """
        UPDATE tome_requests r SET bucket = r.left_from, left_from = NULL, updated_at = NOW()
        WHERE r.status = 'open' AND r.bucket = 'dormant' AND r.left_from IS NOT NULL
          AND EXISTS (SELECT 1 FROM guild_roster g WHERE g.uuid = r.uuid)
        RETURNING r.id, r.uuid::text, r.ign, r.bucket
        """
    )
    back = c.fetchall()
    for rows, describe in ((out, lambda b: f"{b} -> dormant (left the guild)"),
                           (back, lambda b: f"dormant -> {b} (back in the guild)")):
        for rid, uuid, ign, bucket in rows:
            note = describe(bucket)
            c.execute("INSERT INTO tome_events (uuid, request_id, action, note) VALUES (%s, %s, 'moved', %s)", (uuid, rid, note))
            c.execute("INSERT INTO audit_log (log_type, actor_name, action) VALUES ('tome', 'Bot', %s)",
                      (f"Moved {ign}'s request #{rid}: {note}",))
    return len(out), len(back)
