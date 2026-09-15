"""
Test suite for the discord_links ign rename sync
(Tasks/update_member_data.py UpdateMemberData._sync_member_igns and
_apply_rename_nicknames).

The guild loop holds the authoritative {uuid: name} map from the Wynncraft API;
the sync writes name changes back to discord_links (otherwise only written at
link time — stale forever) and rebuilds the '{rank} {ign}' Discord nickname of
the linked member.

1. A changed name updates the row and reports {old, new, discord_id, rank}
2. Matching names touch nothing (no UPDATE, no commit)
3. uuid dash-format differences between API and DB still match
4. Members with no discord_links row are ignored
5. Nicknames: linked member gets '{rank} {new name}'; Forbidden is swallowed;
   renames without a linked discord_id are skipped

Rows are (uuid, ign, discord_id, rank): one per uuid since TAQ-76.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Tasks import update_member_data as umd

UUID = "110c11c8-d8b7-478d-8adf-b0f606d5f939"


class FakeCursor:
    def __init__(self, stored_rows):
        self.stored_rows = stored_rows
        self.updates = []

    def execute(self, sql, params=None):
        if not sql.strip().upper().startswith("SELECT"):
            self.updates.append(params)

    def fetchall(self):
        return self.stored_rows


class FakeDB:
    def __init__(self, stored_rows):
        self.cursor = FakeCursor(stored_rows)
        self.connection = self
        self.committed = False
        self.closed = False

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def run_sync(monkeypatch, stored_rows, curr_map):
    db = FakeDB(stored_rows)
    monkeypatch.setattr(umd, "_db_connect_with_retry", lambda: db)
    renames = umd.UpdateMemberData._sync_member_igns(curr_map)
    return db, renames


def test_rename_updates_row(monkeypatch):
    db, renames = run_sync(
        monkeypatch,
        [(UUID, "JohnMadDog", 609, "Angler")],
        {UUID: {"name": "Sedacto", "rank": "captain"}},
    )
    assert renames == [{"old": "JohnMadDog", "new": "Sedacto", "discord_id": 609, "rank": "Angler"}]
    assert db.cursor.updates == [("Sedacto", UUID)]
    assert db.committed and db.closed


def test_matching_name_untouched(monkeypatch):
    db, renames = run_sync(
        monkeypatch,
        [(UUID, "Sedacto", 609, "Angler")],
        {UUID: {"name": "Sedacto", "rank": "captain"}},
    )
    assert renames == []
    assert db.cursor.updates == []
    assert not db.committed


def test_dashless_api_uuid_matches(monkeypatch):
    db, renames = run_sync(
        monkeypatch,
        [(UUID, "OldName", 609, "Angler")],
        {UUID.replace("-", ""): {"name": "NewName", "rank": None}},
    )
    assert renames[0]["old"] == "OldName"
    assert renames[0]["new"] == "NewName"


def test_unknown_member_ignored(monkeypatch):
    db, renames = run_sync(
        monkeypatch,
        [(UUID, "Sedacto", 609, "Angler")],
        {"9aeb062a-f769-49bc-8046-4d9c8cc86e5a": {"name": "guywhyII", "rank": None}},
    )
    assert renames == []
    assert db.cursor.updates == []


def _cog_with_guild(member):
    cog = umd.UpdateMemberData.__new__(umd.UpdateMemberData)
    guild = MagicMock()
    guild.get_member.return_value = member
    cog.client = MagicMock()
    cog.client.get_guild.return_value = guild
    return cog


def test_nickname_rebuilt_for_linked_member():
    member = MagicMock()
    member.edit = AsyncMock()
    cog = _cog_with_guild(member)
    asyncio.run(cog._apply_rename_nicknames(
        [{"old": "JohnMadDog", "new": "Sedacto", "discord_id": 609, "rank": "Angler"}]
    ))
    member.edit.assert_awaited_once_with(nick="Angler Sedacto", reason="Minecraft name change")


def test_nickname_forbidden_swallowed():
    member = MagicMock()
    member.edit = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
    cog = _cog_with_guild(member)
    asyncio.run(cog._apply_rename_nicknames(
        [{"old": "A", "new": "B", "discord_id": 1, "rank": "Angler"}]
    ))
    member.edit.assert_awaited_once()


def test_nickname_skipped_without_discord_id():
    member = MagicMock()
    member.edit = AsyncMock()
    cog = _cog_with_guild(member)
    asyncio.run(cog._apply_rename_nicknames(
        [{"old": "A", "new": "B", "discord_id": None, "rank": None}]
    ))
    member.edit.assert_not_awaited()
