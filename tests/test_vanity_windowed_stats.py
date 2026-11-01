"""
Test suite for Tasks/vanity_roles.py::compute_windowed_stats.

Regression cover for members who joined inside the scoring window. The task used to
run its own baseline lookup that returned None whenever a member had no snapshot
from exactly `window_days` ago, and skipped those members outright — so every recent
joiner was silently excluded from vanity roles no matter how much they contributed.

Tests:
1. A member who joined mid-window is scored from their first post-join snapshot
2. An established member's delta is unchanged by the fix
3. A member with no snapshot yet this membership is skipped, not given a 0 baseline
4. A returning member is not credited progress from a previous membership
"""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Tasks import vanity_roles
from Tasks.vanity_roles import compute_windowed_stats, _war_tier_label, _raid_tier_label

WINDOW = 14
NEWBIE = "11111111-1111-1111-1111-111111111111"
VETERAN = "22222222-2222-2222-2222-222222222222"
TODAY_JOIN = "33333333-3333-3333-3333-333333333333"
RETURNEE = "44444444-4444-4444-4444-444444444444"


def _member(uuid, name, joined, wars, raids):
    return {"uuid": uuid, "name": name, "joined": joined, "wars": wars, "raids": raids}


@pytest.fixture
def guild_data():
    return {
        "time": 0,
        "members": [
            # joined 12 days ago, well inside the 14-day window
            _member(NEWBIE, "Newbie", "2026-07-21T11:26:53.830000Z", 2248, 150),
            # long-standing member
            _member(VETERAN, "Veteran", "2026-03-27T00:32:40.234000Z", 1213, 40),
            # joined today, snapshot job has not covered them yet
            _member(TODAY_JOIN, "FreshFish", "2026-08-02T09:00:00.000000Z", 5000, 300),
            # left and rejoined 5 days ago
            _member(RETURNEE, "Returnee", "2026-07-28T00:00:00.000000Z", 900, 60),
        ],
    }


@pytest.fixture
def patched_db(guild_data):
    """Patch the three Helpers.database calls compute_windowed_stats makes."""
    baselines = {
        # first snapshot after joining (2026-07-22) — everything since counts
        "wars": {NEWBIE: (2032, True), VETERAN: (1170, False), RETURNEE: (860, True)},
        "raids": {NEWBIE: (12, True), VETERAN: (40, False), RETURNEE: (55, True)},
    }
    # TODAY_JOIN has no snapshot inside its membership period
    measurable = {NEWBIE, VETERAN, RETURNEE}

    with patch.object(vanity_roles, "DB") as db_cls, \
         patch.object(vanity_roles, "get_current_guild_data_with_db", return_value=guild_data), \
         patch.object(vanity_roles, "get_members_with_baseline_history_with_db", return_value=measurable), \
         patch.object(vanity_roles, "get_player_activity_baselines_for_members_with_db",
                      side_effect=lambda db, key, days, joined: baselines[key]):
        yield db_cls


def test_mid_window_joiner_is_scored_not_skipped(patched_db):
    stats = compute_windowed_stats(WINDOW)
    assert NEWBIE in stats, "member who joined inside the window must still be scored"
    assert stats[NEWBIE].wars == 2248 - 2032
    assert stats[NEWBIE].raids == 150 - 12
    assert _war_tier_label(stats[NEWBIE].wars) == "t3"
    assert _raid_tier_label(stats[NEWBIE].raids) == "t3"


def test_established_member_delta_unchanged(patched_db):
    stats = compute_windowed_stats(WINDOW)
    assert stats[VETERAN].wars == 1213 - 1170
    assert stats[VETERAN].raids == 0
    assert _war_tier_label(stats[VETERAN].wars) == "t1"
    assert _raid_tier_label(stats[VETERAN].raids) is None


def test_member_without_any_snapshot_is_skipped(patched_db):
    """A 0 baseline on `wars` (a lifetime-cumulative counter) would hand someone
    who joined hours ago their entire career total, and with it an instant t3."""
    stats = compute_windowed_stats(WINDOW)
    assert TODAY_JOIN not in stats


def test_returning_member_not_credited_previous_membership(patched_db):
    stats = compute_windowed_stats(WINDOW)
    assert stats[RETURNEE].wars == 900 - 860
    assert stats[RETURNEE].raids == 60 - 55


def test_deltas_never_go_negative(patched_db, guild_data):
    """A baseline above the live value (API blip, carried-forward row) must clamp to 0."""
    guild_data["members"][1]["wars"] = 1000  # below the 1170 baseline
    stats = compute_windowed_stats(WINDOW)
    assert stats[VETERAN].wars == 0


def test_private_profile_null_stats_treated_as_zero(patched_db, guild_data):
    guild_data["members"][0]["wars"] = None
    stats = compute_windowed_stats(WINDOW)
    assert stats[NEWBIE].wars == 0
    assert stats[NEWBIE].raids == 150 - 12  # raids still scored


def test_empty_guild_data_returns_empty(patched_db, guild_data):
    guild_data["members"] = []
    assert compute_windowed_stats(WINDOW) == {}


@pytest.mark.parametrize("wars,expected", [(39, None), (40, "t1"), (79, "t1"), (80, "t2"),
                                           (119, "t2"), (120, "t3"), (500, "t3")])
def test_war_tier_boundaries(wars, expected):
    assert _war_tier_label(wars) == expected


@pytest.mark.parametrize("raids,expected", [(29, None), (30, "t1"), (49, "t1"), (50, "t2"),
                                            (79, "t2"), (80, "t3"), (200, "t3")])
def test_raid_tier_boundaries(raids, expected):
    assert _raid_tier_label(raids) == expected
