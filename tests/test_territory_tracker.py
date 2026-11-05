"""Tests for the territory tracker's snapshot persistence.

The snapshot write is the bot's dominant egress cost, so the loop must send
only the territories that differ from what the DB already holds — while still
rewriting when the row is missing (self-heal after external deletion).
"""

from unittest.mock import MagicMock

import pytest

from Tasks import territory_tracker as tt
from Tasks.territory_tracker import TerritoryTracker


def _terr(guild_name, prefix, acquired="2026-01-01T00:00:00Z"):
    return {
        "guild": {"uuid": "u", "name": guild_name, "prefix": prefix},
        "acquired": acquired,
        "location": {"start": [0, 0], "end": [1, 1]},
    }


def _snapshot():
    return {
        "Alpha Plains": _terr("Guild A", "AAA"),
        "Beta Woods": _terr("Guild B", "BBB"),
    }


def _make_cog():
    cog = TerritoryTracker.__new__(TerritoryTracker)
    client = MagicMock()
    client.is_ready.return_value = True
    # Home tracker channel exists (loop returns early without it);
    # global/military channels absent so no embeds are attempted.
    client.get_channel.side_effect = (
        lambda cid: MagicMock() if cid == tt.TERRITORY_TRACKER_CHANNEL_ID else None
    )
    cog.client = client
    return cog


async def _run_tick(monkeypatch, old_data, new_data):
    saves = []
    patches = []
    exchanges = []
    monkeypatch.setattr(tt, "_read_territories_sync", lambda: old_data)
    monkeypatch.setattr(tt, "saveTerritoryData", lambda data: saves.append(data))
    monkeypatch.setattr(tt, "patchTerritoryData", lambda old, new: patches.append((old, new)))
    monkeypatch.setattr(
        tt, "save_territory_exchanges", lambda changes: exchanges.append(changes)
    )

    async def fake_fetch():
        return new_data

    monkeypatch.setattr(tt, "getTerritoryData", fake_fetch)

    cog = _make_cog()
    await TerritoryTracker.territory_tracker.coro(cog)
    return saves, patches, exchanges


@pytest.mark.asyncio
async def test_identical_snapshot_skips_write(monkeypatch):
    saves, patches, exchanges = await _run_tick(monkeypatch, _snapshot(), _snapshot())
    assert saves == []
    assert patches == []
    assert exchanges == []


@pytest.mark.asyncio
async def test_ownership_change_patches_and_records_exchange(monkeypatch):
    old = _snapshot()
    new = _snapshot()
    new["Alpha Plains"] = _terr("Guild C", "CCC", acquired="2026-01-02T00:00:00Z")

    saves, patches, exchanges = await _run_tick(monkeypatch, old, new)

    assert saves == []
    assert patches == [(old, new)]
    assert len(exchanges) == 1
    assert exchanges[0]["Alpha Plains"]["new"]["owner"] == "Guild C"


@pytest.mark.asyncio
async def test_missing_row_rewrites(monkeypatch):
    saves, patches, _ = await _run_tick(monkeypatch, {}, _snapshot())
    assert saves == [_snapshot()]
    assert patches == []


@pytest.mark.asyncio
async def test_failed_fetch_writes_nothing(monkeypatch):
    saves, patches, _ = await _run_tick(monkeypatch, _snapshot(), False)
    assert saves == []
    assert patches == []


def test_strip_stored_keeps_the_rest_of_each_resource():
    data = {
        "Alpha Plains": {"resources": [{"type": "ORE", "stored": 5, "generation": 3600}]},
        "Beta Woods": {"resources": None},
        "Gamma Hills": {},
    }

    assert tt._strip_stored(data) == {
        "Alpha Plains": {"resources": [{"type": "ORE", "generation": 3600}]},
        "Beta Woods": {"resources": None},
        "Gamma Hills": {},
    }


@pytest.fixture
def territory_row(temp_db, monkeypatch):
    temp_db.cursor.execute(
        "CREATE TEMP TABLE IF NOT EXISTS cache_entries (LIKE public.cache_entries INCLUDING ALL)"
    )
    temp_db.cursor.execute("TRUNCATE cache_entries")
    monkeypatch.setattr(tt, "DB", lambda: temp_db)
    tt.saveTerritoryData(_snapshot())
    return temp_db


def _row(db):
    db.cursor.execute(
        "SELECT data, created_at, fetch_count FROM cache_entries WHERE cache_key = 'territories'"
    )
    return db.cursor.fetchone()


def test_patch_applies_changes_additions_and_removals(territory_row):
    old = _snapshot()
    old["Gone Keep"] = _terr("Guild D", "DDD")
    tt.saveTerritoryData(old)
    _, _, count_before = _row(territory_row)

    new = _snapshot()
    new["Alpha Plains"] = _terr("Guild C", "CCC", acquired="2026-01-02T00:00:00Z")
    new["New Harbor"] = _terr("Guild E", "EEE")

    tt.patchTerritoryData(old, new)

    data, _, count_after = _row(territory_row)
    assert data == new
    assert count_after == count_before + 1


def test_patch_sends_only_changed_territories(territory_row, monkeypatch):
    new = _snapshot()
    new["Alpha Plains"] = _terr("Guild C", "CCC")
    sent = []
    real_dumps = tt.json.dumps
    monkeypatch.setattr(tt.json, "dumps", lambda value: sent.append(value) or real_dumps(value))

    tt.patchTerritoryData(_snapshot(), new)

    assert sent == [{"Alpha Plains": new["Alpha Plains"]}]


def test_patch_advances_the_timestamp_the_website_etag_uses(territory_row):
    territory_row.cursor.execute(
        "UPDATE cache_entries SET created_at = NOW() - INTERVAL '1 hour' WHERE cache_key = 'territories'"
    )
    _, before, _ = _row(territory_row)
    new = _snapshot()
    new["Alpha Plains"] = _terr("Guild C", "CCC")

    tt.patchTerritoryData(_snapshot(), new)

    _, after, _ = _row(territory_row)
    assert after > before
