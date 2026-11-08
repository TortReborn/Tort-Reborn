"""
Tests for rank_style (Helpers/classes.py): the rank badge colour and text
used by /profile and the other player cards.
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers.classes import BasicPlayerStats, rank_style


def test_player_gets_default_badge():
    assert rank_style('Player') == ('#66ccff', 'PLAYER')


def test_support_rank_lookup():
    assert rank_style('CHAMPION') == ('#ffa214', 'CHAMPION')


def test_art_staff_rank_matches_api_value():
    # The API returns rank "Art" for artists (shortenedRank "Artist").
    assert rank_style('Art') == ('#00aaaa', 'ARTIST')


def test_unknown_rank_falls_back_instead_of_raising():
    assert rank_style('Lore') == ('#66ccff', 'LORE')


def test_basic_player_stats_with_unlisted_staff_rank():
    pdata = {'rank': 'Lore', 'globalData': {}}
    with patch("Helpers.classes.getPlayerUUID", return_value=("Name", "some-uuid")), \
         patch("Helpers.classes.getPlayerDatav3", return_value=pdata):
        stats = BasicPlayerStats("Name")
    assert stats.tag_color == '#66ccff'
