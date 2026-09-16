"""
Test suite for guild-leave monitoring (Helpers/guild_leave.py).

An accepted applicant who is in another guild is flagged guild_leave_pending
until the Wynncraft API shows them guildless. The flag must never outlive
the join: witherfry's app kept it for four months after they joined and
registered, so the day they left TAq the bot un-archived their old exec
thread and announced they had "left their guild" (TAQ-81).

1. The poll's SELECT skips applicants with a live discord_links row
2. Stale flags on joined applicants are cleared (and returned for logging)
3. A guildless player is 'left', a TAq member is 'joined', another guild is
   'waiting', and a broken API payload is 'unknown' -- never 'left'
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers.guild_leave import (
    CLEAR_STALE_LEAVE_SQL,
    PENDING_LEAVE_SQL,
    classify_pending_leave,
    clear_stale_pending_leaves,
    current_guild_name,
    fetch_pending_leaves,
)


class FakeCursor:
    """Records executed queries and replays configured rows."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))

    def fetchall(self):
        return self.rows


def test_poll_skips_applicants_who_already_joined():
    assert "guild_leave_pending = TRUE" in PENDING_LEAVE_SQL
    assert "NOT EXISTS" in PENDING_LEAVE_SQL
    assert "JOIN membership_stints ms ON ms.uuid = dl.uuid" in PENDING_LEAVE_SQL
    assert "linked" not in PENDING_LEAVE_SQL
    cursor = FakeCursor(rows=[(96, 1, 2, "547898736337092639", "witherfry")])
    assert fetch_pending_leaves(cursor) == [(96, 1, 2, "547898736337092639", "witherfry")]
    assert cursor.queries == [(PENDING_LEAVE_SQL, None)]


def test_stale_flags_are_cleared_and_reported():
    assert "SET guild_leave_pending = FALSE" in CLEAR_STALE_LEAVE_SQL
    assert "EXISTS" in CLEAR_STALE_LEAVE_SQL and "NOT EXISTS" not in CLEAR_STALE_LEAVE_SQL
    assert "JOIN membership_stints ms ON ms.uuid = dl.uuid" in CLEAR_STALE_LEAVE_SQL
    assert "linked" not in CLEAR_STALE_LEAVE_SQL
    assert "RETURNING" in CLEAR_STALE_LEAVE_SQL
    cursor = FakeCursor(rows=[(96, "witherfry"), (21, "kioabc1")])
    assert clear_stale_pending_leaves(cursor) == [(96, "witherfry"), (21, "kioabc1")]
    assert cursor.queries == [(CLEAR_STALE_LEAVE_SQL, None)]


def test_guildless_player_has_left():
    assert classify_pending_leave({"username": "x", "guild": None}) == "left"
    assert classify_pending_leave({"username": "x"}) == "left"
    assert classify_pending_leave({"guild": {"name": ""}}) == "left"


def test_taq_member_counts_as_joined_not_left():
    payload = {"guild": {"name": "The Aquarium", "prefix": "TAq", "rank": "RECRUIT"}}
    assert classify_pending_leave(payload) == "joined"


def test_other_guild_keeps_waiting():
    payload = {"guild": {"name": "Some Other Guild", "prefix": "SOG"}}
    assert classify_pending_leave(payload) == "waiting"
    assert current_guild_name(payload) == "Some Other Guild"


def test_broken_payload_is_unknown_never_left():
    assert classify_pending_leave(None) == "unknown"
    assert classify_pending_leave("rate limited") == "unknown"
    assert classify_pending_leave(429) == "unknown"
    assert classify_pending_leave({"guild": "TAq"}) == "unknown"
