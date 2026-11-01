"""Membership: guild_roster (now) and membership_stints (history).

discord_links says who a Discord account *is*. This module says whether that
player is *in the guild* and when they were (TAQ-76 linking overhaul; see
docs/specs/taq-76-linking-audit.md). Before it, `discord_links.linked` was
read as "is a member" by some callers and as "has ever been registered" by
the data, and every consumer that needed the former re-derived it from the
guild API roster by hand.

- ``guild_roster`` mirrors the in-game member list. ``sync_roster`` is called
  by Tasks/update_member_data.py every cycle from the same API payload that
  drives the join/leave embeds; it is the diff baseline, not a cache.
- ``membership_stints`` keeps one row per stint: opened when a uuid appears on
  the roster, closed when it disappears. The open stint's ``discord_id`` is
  filled in at registration, and ``rank_at_leave`` is stamped whenever
  removal clears the Discord rank.
- Wars in TAq (TAQ-133). The API only reports lifetime wars across every
  guild, so TAq's share comes from the bot's own samples: ``wars_on_join`` is
  the first sample at or after the stint opened, ``wars_on_leave`` the last
  one before it closed, and a member's TAq wars is the sum over their stints.

All functions take a psycopg2 cursor and are blocking; callers run them via
asyncio.to_thread inside their own DB checkout and commit.
"""

from collections import namedtuple
from datetime import date, datetime, timezone


def uuid_key(value):
    """Dashless lower-case form of a uuid, or None. The API and older rows
    disagree on dashes; comparisons in Python go through this."""
    if not value:
        return None
    return str(value).replace('-', '').lower()


# --- guild_roster -----------------------------------------------------------

def roster_uuids(cursor):
    """{uuid_key: uuid text} for every current roster member."""
    cursor.execute("SELECT uuid::text FROM guild_roster")
    return {uuid_key(row[0]): row[0] for row in cursor.fetchall()}


def is_member(cursor, *, uuid=None, discord_id=None):
    """Is this player (by uuid, or by the Discord account linked to them) on
    the roster right now?"""
    if uuid is not None:
        cursor.execute("SELECT 1 FROM guild_roster WHERE uuid = %s::uuid", (str(uuid),))
    elif discord_id is not None:
        cursor.execute(
            "SELECT 1 FROM discord_links dl JOIN guild_roster gr ON gr.uuid = dl.uuid"
            " WHERE dl.discord_id = %s",
            (int(discord_id),),
        )
    else:
        return False
    return cursor.fetchone() is not None


def diff_roster(previous_keys, members):
    """(joined, left) as sets of uuid_key, comparing a previous key set with
    an iterable of API member dicts. Pure; unit-tested."""
    current = {uuid_key(m.get('uuid')) for m in members if uuid_key(m.get('uuid'))}
    previous = set(previous_keys)
    return current - previous, previous - current


def sync_roster(cursor, members, now=None):
    """Reconcile guild_roster with the API member list.

    ``members`` is Guild.all_members: dicts with uuid, name, rank, joined.
    Upserts every current member, deletes the rest, then opens a stint for
    each newcomer and closes one for each leaver. Returns
    ``(joined_uuids, left_uuids)`` as dashless keys so the caller can post
    embeds from the same diff.
    """
    now = now or datetime.now(timezone.utc)
    previous = roster_uuids(cursor)
    joined, left = diff_roster(previous.keys(), members)

    by_key = {uuid_key(m.get('uuid')): m for m in members if uuid_key(m.get('uuid'))}
    for key, m in by_key.items():
        cursor.execute(
            """INSERT INTO guild_roster (uuid, ign, in_game_rank, joined_at, first_seen, last_seen)
               VALUES (%s::uuid, %s, %s, %s, %s, %s)
               ON CONFLICT (uuid) DO UPDATE
               SET ign = EXCLUDED.ign,
                   in_game_rank = EXCLUDED.in_game_rank,
                   joined_at = COALESCE(EXCLUDED.joined_at, guild_roster.joined_at),
                   last_seen = EXCLUDED.last_seen""",
            (m['uuid'], m['name'], (m.get('rank') or 'unknown'), m.get('joined'), now, now),
        )

    for key in left:
        cursor.execute("DELETE FROM guild_roster WHERE uuid = %s::uuid", (previous[key],))
        close_stint(cursor, previous[key], now, left_via='api_diff')

    for key in joined:
        m = by_key[key]
        open_stint(cursor, m['uuid'], joined_at=_parse_joined(m.get('joined')) or now)

    return joined, left


def _parse_joined(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


# --- membership_stints ------------------------------------------------------

def open_stint(cursor, uuid, *, joined_at, discord_id=None, wars_on_join=None):
    """Open a stint for ``uuid`` unless one is already open. The linked
    Discord account is looked up when not given so a stint opened by the
    roster sync still carries identity for players who registered before
    joining in-game."""
    if discord_id is None:
        cursor.execute("SELECT discord_id FROM discord_links WHERE uuid = %s::uuid", (str(uuid),))
        row = cursor.fetchone()
        discord_id = row[0] if row else None
    cursor.execute(
        """INSERT INTO membership_stints (uuid, discord_id, joined_at, wars_on_join, source)
           VALUES (%s::uuid, %s, %s, %s, 'live')
           ON CONFLICT (uuid) WHERE left_at IS NULL DO UPDATE
           SET discord_id = COALESCE(membership_stints.discord_id, EXCLUDED.discord_id),
               wars_on_join = COALESCE(membership_stints.wars_on_join, EXCLUDED.wars_on_join)
           RETURNING id""",
        (str(uuid), discord_id, joined_at, wars_on_join),
    )
    return cursor.fetchone()[0]


def close_stint(cursor, uuid, now=None, *, left_via='api_diff'):
    """Close the open stint for ``uuid``; the Discord rank at that moment is
    kept as rank_at_leave unless removal already stamped it, and the last war
    sample as wars_on_leave. Returns True if a stint was closed."""
    now = now or datetime.now(timezone.utc)
    cursor.execute(
        f"""UPDATE membership_stints ms
               SET left_at = %s,
                   left_via = %s,
                   rank_at_leave = COALESCE(ms.rank_at_leave, dl.rank),
                   wars_on_leave = COALESCE(ms.wars_on_leave, {_wars_before('ms.uuid', '%s::timestamptz')})
              FROM (SELECT %s::uuid AS uuid) target
              LEFT JOIN discord_links dl ON dl.uuid = target.uuid
             WHERE ms.uuid = target.uuid AND ms.left_at IS NULL""",
        (now, left_via, now, str(uuid)),
    )
    return cursor.rowcount > 0


def attach_identity(cursor, uuid, discord_id):
    """Registration: put the Discord account on the open stint. No-op when
    the player is not on the roster yet -- the roster sync will open the
    stint with the link looked up. Wars on join come from the samples
    (fill_join_baselines), not from here: registration can come long after
    the in-game join, or never."""
    cursor.execute(
        """UPDATE membership_stints
              SET discord_id = %s
            WHERE uuid = %s::uuid AND left_at IS NULL""",
        (int(discord_id), str(uuid)),
    )
    return cursor.rowcount > 0


def stamp_rank_at_leave(cursor, uuid, rank):
    """Removal: remember the rank being cleared on the player's latest stint
    (open or not) if nothing is recorded there yet."""
    if not rank:
        return False
    cursor.execute(
        """UPDATE membership_stints
              SET rank_at_leave = %s
            WHERE id = (SELECT id FROM membership_stints
                         WHERE uuid = %s::uuid
                         ORDER BY left_at IS NULL DESC, joined_at DESC
                         LIMIT 1)
              AND rank_at_leave IS NULL""",
        (rank, str(uuid)),
    )
    return cursor.rowcount > 0


def has_prior_stint(cursor, uuid):
    """Has this player been in the guild before (any closed stint)?"""
    cursor.execute(
        "SELECT 1 FROM membership_stints WHERE uuid = %s::uuid AND left_at IS NOT NULL LIMIT 1",
        (str(uuid),),
    )
    return cursor.fetchone() is not None


def latest_stint(cursor, uuid):
    """The most recent stint as a dict, or None."""
    cursor.execute(
        """SELECT id, uuid::text, discord_id, joined_at, left_at, wars_on_join, wars_on_leave,
                  rank_at_leave, left_via, source
             FROM membership_stints
            WHERE uuid = %s::uuid
            ORDER BY left_at IS NULL DESC, joined_at DESC
            LIMIT 1""",
        (str(uuid),),
    )
    row = cursor.fetchone()
    if not row:
        return None
    keys = ['id', 'uuid', 'discord_id', 'joined_at', 'left_at', 'wars_on_join', 'wars_on_leave',
            'rank_at_leave', 'left_via', 'source']
    return dict(zip(keys, row))


# --- wars in TAq (TAQ-133) ---------------------------------------------------
#
# Two sample sources, both of lifetime wars: member_counters_hourly (every
# update tick, kept as the hour's max, since 2026-08-13) and player_activity
# (one row per member per day at 00:01 UTC, since 2025-06-03). Whichever is
# nearer in time wins.

# player_activity began on this day. A stint that opened on or before it got
# its baseline from that first snapshot, not from the real join, so its count
# is a floor: "tracked since June 2025".
SNAPSHOTS_BEGAN = date(2025, 6, 3)

# NULL is a private reading (TAQ-134): unknown, never a baseline.
_SAMPLES = """(
       SELECT c.hour AS at, c.wars FROM member_counters_hourly c
        WHERE c.uuid = {uuid} AND c.wars IS NOT NULL
       UNION ALL
       SELECT pa.snapshot_date::timestamp AT TIME ZONE 'UTC', pa.wars
         FROM player_activity pa WHERE pa.uuid = {uuid} AND pa.wars IS NOT NULL
     ) s"""


def _wars_after(uuid_expr, at_expr, until_expr):
    """SQL scalar: the first wars sample at or after ``at_expr`` (an hourly
    bucket counts if it contains that moment) and, when ``until_expr`` is
    not NULL, no later than it."""
    return f"""(SELECT s.wars FROM {_SAMPLES.format(uuid=uuid_expr)}
       WHERE s.at >= date_trunc('hour', {at_expr})
         AND s.at <= COALESCE({until_expr}, 'infinity'::timestamptz)
       ORDER BY s.at LIMIT 1)"""


def _wars_before(uuid_expr, at_expr):
    """SQL scalar: the last wars sample at or before ``at_expr``."""
    return f"""(SELECT s.wars FROM {_SAMPLES.format(uuid=uuid_expr)}
       WHERE s.at <= {at_expr}
       ORDER BY s.at DESC LIMIT 1)"""


def fill_join_baselines(cursor):
    """Set wars_on_join on every stint that lacks one, from the first sample
    after it opened. Runs after each counter write, so a new member has a
    baseline within one update tick of joining in-game. Returns the number
    of stints filled."""
    first = _wars_after('ms.uuid', 'ms.joined_at', 'ms.left_at')
    cursor.execute(
        f"""UPDATE membership_stints ms
               SET wars_on_join = {first}
             WHERE ms.wars_on_join IS NULL AND {first} IS NOT NULL"""
    )
    return cursor.rowcount


def fill_leave_counts(cursor):
    """Set wars_on_leave on every closed stint that lacks one, from the last
    sample before it closed. close_stint stamps it live; this covers stints
    closed before the column existed. Returns the number filled."""
    last = _wars_before('ms.uuid', 'ms.left_at')
    cursor.execute(
        f"""UPDATE membership_stints ms
               SET wars_on_leave = {last}
             WHERE ms.left_at IS NOT NULL AND ms.wars_on_leave IS NULL AND {last} IS NOT NULL"""
    )
    return cursor.rowcount


TaqWars = namedtuple('TaqWars', 'total partial')


def count_taq_wars(stints, current_wars):
    """Wars done in TAq across ``stints`` (dicts with joined_at, left_at,
    wars_on_join, wars_on_leave). The open stint runs to ``current_wars``,
    the live lifetime count. A stint missing either end is skipped; a
    negative delta (an API correction) counts as zero. ``partial`` is set
    when a counted stint began on or before SNAPSHOTS_BEGAN. Returns None
    when no stint could be counted. Pure; unit-tested."""
    total, counted, partial = 0, False, False
    for s in stints:
        start = s['wars_on_join']
        end = current_wars if s['left_at'] is None else s['wars_on_leave']
        if start is None or end is None:
            continue
        total += max(int(end) - int(start), 0)
        counted = True
        if s['joined_at'].astimezone(timezone.utc).date() <= SNAPSHOTS_BEGAN:
            partial = True
    return TaqWars(total, partial) if counted else None


def taq_wars(cursor, uuid, current_wars):
    """count_taq_wars over every stint ``uuid`` has had."""
    cursor.execute(
        """SELECT joined_at, left_at, wars_on_join, wars_on_leave
             FROM membership_stints WHERE uuid = %s::uuid ORDER BY joined_at""",
        (str(uuid),),
    )
    keys = ['joined_at', 'left_at', 'wars_on_join', 'wars_on_leave']
    return count_taq_wars([dict(zip(keys, r)) for r in cursor.fetchall()], current_wars)


# Fragments for queries over ``applications a`` (its discord_id is text).
#
# "Is on the roster right now":
APPLICANT_IS_MEMBER_SQL = """EXISTS (
     SELECT 1 FROM discord_links dl
     JOIN guild_roster gr ON gr.uuid = dl.uuid
     WHERE dl.discord_id = CAST(a.discord_id AS BIGINT)
   )"""

# "Has joined for this application": a stint that was still active when the
# application came in, or later -- open (a current member; the roster sync
# opens the stint in the same transaction as the roster row, so this never
# disagrees with guild_roster), or closed after the application date. Covers
# the new joiner, the member who applied and later left, and the player who
# joined in-game just before applying; a returning applicant whose previous
# stay ended before they applied is pending, which is right. The old
# ``linked = TRUE`` was a sticky has-joined marker; the roster alone is not.
# app_expiry, guild_leave, check_apps and the website's pending-joins count
# all ask this question.
APPLICANT_HAS_JOINED_SQL = """EXISTS (
     SELECT 1 FROM discord_links dl
     JOIN membership_stints ms ON ms.uuid = dl.uuid
     WHERE dl.discord_id = CAST(a.discord_id AS BIGINT)
       AND (ms.left_at IS NULL
            OR ms.left_at >= COALESCE(a.submitted_at, a.reviewed_at))
   )"""
