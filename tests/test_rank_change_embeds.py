"""
Test suite for rank-change embed chunking (Tasks/update_member_data.py).

A 30-change diff (promotion wave / cold start) previously produced one
31-field embed, which Discord rejects (50035) — and the send failure killed
the update loop. Embeds must chunk at 25 fields.
"""

import datetime
import os
import sys


# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Tasks.update_member_data import EMBED_FIELD_CAP, _build_rank_change_embeds

NOW = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)


def _changes(n):
    return [(f"uuid{i}", f"Player{i}", "Piranha", "Angler") for i in range(n)]


def test_30_changes_chunk_into_two_embeds():
    embeds = _build_rank_change_embeds(_changes(30), NOW)
    assert len(embeds) == 2
    assert len(embeds[0].fields) == EMBED_FIELD_CAP
    assert len(embeds[1].fields) == 5


def test_no_embed_exceeds_discord_field_cap():
    for n in (1, 24, 25, 26, 50, 51):
        for er in _build_rank_change_embeds(_changes(n), NOW):
            assert len(er.fields) <= 25


def test_all_changes_present_and_ordered():
    embeds = _build_rank_change_embeds(_changes(27), NOW)
    names = [f.name for er in embeds for f in er.fields]
    assert names == [f"Player{i}" for i in range(27)]
