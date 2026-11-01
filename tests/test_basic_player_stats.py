"""
Test suite for BasicPlayerStats error paths (Helpers/classes.py).

Tests:
1. A failed UUID lookup sets error=True (pre-existing behaviour)
2. A failed player-data fetch sets error=True instead of raising
   AttributeError on the False that getPlayerDatav3 returns
"""

import os
import sys
from unittest.mock import patch


# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers.classes import BasicPlayerStats


def test_failed_uuid_lookup_sets_error():
    with patch("Helpers.classes.getPlayerUUID", return_value=None):
        stats = BasicPlayerStats("NoSuchPlayer")
    assert stats.error is True


def test_failed_player_data_fetch_sets_error_not_attributeerror():
    with patch("Helpers.classes.getPlayerUUID", return_value=("Name", "some-uuid")), \
         patch("Helpers.classes.getPlayerDatav3", return_value=False):
        stats = BasicPlayerStats("Name")
    assert stats.error is True
