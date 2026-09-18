"""
Test suite for the discord_links uuid guard (Helpers/links.py).

A Minecraft uuid may be linked to at most one Discord account; these helpers
back the unique index discord_links_uuid_uq with up-front
detection so commands can report the conflict instead of failing on the
constraint.

1. find_linked_uuid_conflict returns the other account's row when the uuid is
   linked elsewhere, and None when it is free / owned by the same account
2. A falsy uuid never queries and never conflicts
3. assert_uuid_free raises LinkConflictError carrying the conflicting account
4. user_message mentions the conflicting Discord account
5. link_change_refusal: the one rule /manage link and /manage unlink share --
   the initiator needs a recognised rank and may only touch members strictly
   below it. /manage link used to have no in-code check at all.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers.links import (
    LinkConflictError,
    assert_uuid_free,
    find_linked_uuid_conflict,
    link_change_refusal,
)

UUID = "065fc385-f9c1-4e7f-96b3-8674a65c509f"


class FakeCursor:
    """Replays queued fetchone results and records executed queries."""

    def __init__(self, results):
        self.results = list(results)
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))

    def fetchone(self):
        return self.results.pop(0)


def test_conflict_found():
    cursor = FakeCursor([(500, "Kenji121")])
    assert find_linked_uuid_conflict(cursor, UUID, 751) == (500, "Kenji121")
    (sql, params), = cursor.queries
    assert params == (UUID, 751)
    # Identity is unique outright now (TAQ-76): no partial "linked" filter,
    # and the row's own account is excluded so a relink to itself is fine.
    assert "linked" not in sql
    assert "discord_id <> %s" in sql


def test_no_conflict():
    cursor = FakeCursor([None])
    assert find_linked_uuid_conflict(cursor, UUID, 751) is None


def test_falsy_uuid_skips_query():
    cursor = FakeCursor([])
    assert find_linked_uuid_conflict(cursor, None, 751) is None
    assert find_linked_uuid_conflict(cursor, "", 751) is None
    assert cursor.queries == []


def test_assert_uuid_free_raises_with_conflict_details():
    cursor = FakeCursor([(500, "Kenji121")])
    with pytest.raises(LinkConflictError) as excinfo:
        assert_uuid_free(cursor, UUID, 751)
    err = excinfo.value
    assert err.uuid == UUID
    assert err.other_discord_id == 500
    assert err.other_ign == "Kenji121"
    assert "<@500>" in err.user_message()


def test_assert_uuid_free_passes_when_free():
    assert_uuid_free(FakeCursor([None]), UUID, 751)


@pytest.mark.parametrize("initiator", [None, "", "Guest"])
def test_link_change_refused_without_a_recognised_rank(initiator):
    assert link_change_refusal(initiator, None) is not None


def test_link_change_allowed_on_unranked_target():
    assert link_change_refusal("Hammerhead", None) is None
    assert link_change_refusal("Starfish", "") is None


def test_link_change_allowed_strictly_below_own_rank():
    assert link_change_refusal("Narwhal", "Hammerhead") is None


@pytest.mark.parametrize("initiator,target", [
    ("Hammerhead", "Hammerhead"),  # equal
    ("Hammerhead", "Narwhal"),     # above
    ("Starfish", "Manatee"),
])
def test_link_change_refused_at_or_above_own_rank(initiator, target):
    assert link_change_refusal(initiator, target) is not None
