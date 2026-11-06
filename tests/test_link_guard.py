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
6. registration_refusal: /new_member and Member | Register add two rules, and
   both paths refuse before they change any role or row.
"""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers.links import (
    LinkConflictError,
    assert_uuid_free,
    find_linked_uuid_conflict,
    link_change_refusal,
    lookup_registration_refusal,
    registration_refusal,
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


OTHER_UUID = "11111111-2222-3333-4444-555555555555"


def test_registration_allowed_for_a_new_member():
    assert registration_refusal("Hammerhead", None, None, UUID, "Starfish") is None


def test_registration_allowed_for_a_rejoin_on_the_same_account():
    assert registration_refusal("Hammerhead", None, UUID, UUID, "Starfish") is None
    assert registration_refusal("Hammerhead", "Starfish", UUID.upper().replace("-", ""), UUID, "Starfish") is None


def test_registration_refused_for_an_unlinked_initiator():
    assert registration_refusal(None, None, None, UUID, "Starfish") is not None


def test_registration_refused_on_a_superior():
    assert "below your own rank" in registration_refusal("Hammerhead", "Narwhal", UUID, UUID, "Starfish")


def test_registration_refused_when_it_would_repoint_a_link():
    assert "/manage link" in registration_refusal("Narwhal", "Starfish", OTHER_UUID, UUID, "Starfish")


def test_registration_refused_when_it_would_lower_a_rank():
    assert "/manage rank" in registration_refusal("Narwhal", "Angler", UUID, UUID, "Starfish")


def test_registration_keeps_an_honorific_starting_rank():
    assert registration_refusal("Narwhal", "Piranha", UUID, UUID, "Piranha") is None


def test_registration_ignores_ranks_outside_the_guild_list():
    assert registration_refusal("Hammerhead", "Navigator", UUID, UUID, "Starfish") is None


def test_lookup_reads_both_accounts():
    cursor = FakeCursor([("Hammerhead",), ("Narwhal", UUID)])
    assert lookup_registration_refusal(cursor, 1, 2, UUID, "Starfish") is not None
    assert [q[1] for q in cursor.queries] == [(1,), (2,)]


def _fake_db(rows):
    db = MagicMock()
    db.cursor = FakeCursor(rows)
    return db


def _target():
    return MagicMock(id=2, roles=[], add_roles=AsyncMock(), remove_roles=AsyncMock(), edit=AsyncMock())


def _pdata():
    return SimpleNamespace(error=False, UUID=UUID, username="Name")


def test_slash_command_refuses_before_changing_anything():
    import Commands.new_member as cmd

    user = _target()
    message = MagicMock(defer=AsyncMock(), respond=AsyncMock())
    message.interaction.user.id = 1
    message.interaction.user.guild_permissions.manage_roles = True
    # assert_uuid_free reads nothing back here, then initiator, then target.
    db = _fake_db([None, ("Hammerhead",), ("Narwhal", UUID)])
    with patch.object(cmd, "BasicPlayerStats", return_value=_pdata()), \
            patch.object(cmd, "DB", return_value=db), \
            patch.object(cmd, "record_registration") as record:
        cog = cmd.NewMember(MagicMock())
        asyncio.run(cog.new_member.callback(cog, message, user, "Name"))
    assert "below your own rank" in message.respond.await_args.args[0]
    user.add_roles.assert_not_awaited()
    user.remove_roles.assert_not_awaited()
    user.edit.assert_not_awaited()
    record.assert_not_called()


def test_register_modal_refuses_before_changing_anything():
    import Helpers.classes as classes

    user = _target()
    msg = MagicMock(edit=AsyncMock())
    interaction = MagicMock(
        response=MagicMock(send_message=AsyncMock()),
        original_response=AsyncMock(return_value=msg),
    )
    interaction.user.id = 1
    db = _fake_db([None, ("Hammerhead",), ("Narwhal", UUID)])

    async def run():
        modal = classes.NewMember(title="New Member", user=user)
        modal.children[0].value = "Name"
        await modal.callback(interaction)

    with patch.object(classes, "BasicPlayerStats", return_value=_pdata()), \
            patch.object(classes, "DB", return_value=db), \
            patch.object(classes, "record_registration") as record:
        asyncio.run(run())
    assert "below your own rank" in msg.edit.await_args.kwargs["content"]
    user.add_roles.assert_not_awaited()
    user.remove_roles.assert_not_awaited()
    user.edit.assert_not_awaited()
    record.assert_not_called()
