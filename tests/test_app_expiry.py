"""
Test suite for application expiry on ticket close (Helpers/app_expiry.py).

An accepted guild application whose ticket is closed before the applicant
ever joins (no live discord_links row) must leave 'accepted', otherwise the
website counts it as a pending join forever (TAQ-77).

1. Accepted guild app runs the guarded UPDATE and reports whether it expired
2. The SQL itself re-checks status/type and requires no live linked row,
   so a race with the join flow cannot expire a joined player
3. Non-guild and non-accepted applications never touch the database
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers.app_expiry import EXPIRE_UNJOINED_SQL, expire_if_never_joined


class FakeCursor:
    """Records executed queries and replays a configured rowcount."""

    def __init__(self, rowcount=0):
        self.rowcount = rowcount
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))


def test_expires_accepted_guild_app_with_no_live_link():
    cursor = FakeCursor(rowcount=1)
    assert expire_if_never_joined(cursor, 203, "guild", "accepted") is True
    (sql, params), = cursor.queries
    assert params == (203,)
    assert sql == EXPIRE_UNJOINED_SQL


def test_leaves_app_alone_when_applicant_already_linked():
    # The UPDATE's NOT EXISTS guard matches no row -> rowcount 0.
    cursor = FakeCursor(rowcount=0)
    assert expire_if_never_joined(cursor, 241, "guild", "accepted") is False
    assert len(cursor.queries) == 1


def test_sql_guards_against_join_race():
    assert "status = 'accepted'" in EXPIRE_UNJOINED_SQL
    assert "application_type = 'guild'" in EXPIRE_UNJOINED_SQL
    assert "NOT EXISTS" in EXPIRE_UNJOINED_SQL
    assert "linked = TRUE" in EXPIRE_UNJOINED_SQL


def test_non_guild_apps_never_query():
    cursor = FakeCursor()
    assert expire_if_never_joined(cursor, 1, "community", "accepted") is False
    assert expire_if_never_joined(cursor, 2, "hammerhead", "accepted") is False
    assert cursor.queries == []


def test_non_accepted_apps_never_query():
    cursor = FakeCursor()
    assert expire_if_never_joined(cursor, 1, "guild", "pending") is False
    assert expire_if_never_joined(cursor, 2, "guild", "denied") is False
    assert expire_if_never_joined(cursor, 3, "guild", "expired") is False
    assert cursor.queries == []
