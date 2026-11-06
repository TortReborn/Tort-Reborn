"""
The promotion queue applies rows automatically, so it enforces the same limit as
the website: a queuer can only promote someone to a rank below their own.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import Tasks.promotion_queue_processor as pq_mod

UUID = "065fc385-f9c1-4e7f-96b3-8674a65c509f"


def _processor(monkeypatch, queuer_rank="Hammerhead", target_rank="Starfish"):
    proc = pq_mod.PromotionQueueProcessor(client=None)
    monkeypatch.setattr(proc, "_lookup_queuer_rank", lambda _id: queuer_rank)
    monkeypatch.setattr(proc, "_lookup_member_data", lambda _uuid: (2, target_rank))
    proc._do_promote = AsyncMock()
    proc._do_demote = AsyncMock()
    return proc


def _entry(new_rank, action="promote", current_rank="Starfish"):
    return {
        "id": 1,
        "uuid": UUID,
        "ign": "Target",
        "current_rank": current_rank,
        "new_rank": new_rank,
        "action_type": action,
        "queued_by_discord_id": 1,
        "queued_by_ign": "Queuer",
    }


def _guild():
    guild = MagicMock()
    guild.get_member.return_value = MagicMock()
    return guild


@pytest.mark.parametrize("new_rank", ["Hammerhead", "Sailfish", "Hydra"])
def test_promotion_at_or_above_the_queuers_rank_is_refused(monkeypatch, new_rank):
    proc = _processor(monkeypatch)
    with pytest.raises(ValueError, match="below their own"):
        asyncio.run(proc._process_entry(_entry(new_rank), _guild()))
    proc._do_promote.assert_not_awaited()


def test_promotion_below_the_queuers_rank_goes_through(monkeypatch):
    proc = _processor(monkeypatch)
    asyncio.run(proc._process_entry(_entry("Swordfish"), _guild()))
    proc._do_promote.assert_awaited_once()


def test_a_demotion_is_not_limited(monkeypatch):
    proc = _processor(monkeypatch, target_rank="Angler")
    asyncio.run(proc._process_entry(_entry("Starfish", "demote", "Angler"), _guild()))
    proc._do_demote.assert_awaited_once()
