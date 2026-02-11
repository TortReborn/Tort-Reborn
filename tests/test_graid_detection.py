"""
Test suite for guild raid detection logic in update_member_data.py

Tests steps 7-11b of the update_member_data loop:
  7   – Detect unvalidated raid count increases
  8   – Detect XP threshold jumps
  9   – Validate via XP jump (raid count + XP)
  9b  – Cross-validate (xp_only pool + later raid count)
  10  – Validate via contrib diff against baseline
  10b – XP-only pool (XP jump, no raid type)
  11  – Announce raids with xp_only backfill
  11b – All-private group with grace period
"""

import asyncio
import datetime
from collections import deque
from datetime import timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

# The contribution threshold must match the value in update_member_data.py
CONTRIBUTION_THRESHOLD = 2_500_000_000

# ---------------------------------------------------------------------------
# Constants for tests
# ---------------------------------------------------------------------------
RAID_NAMES = [
    "Nest of the Grootslangs",
    "The Canyon Colossus",
    "The Nameless Anomaly",
    "Orphion's Nexus of Light",
]

T0 = datetime.datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(minutes=3)   # next tick
T2 = T0 + timedelta(minutes=6)   # tick after that

XP_BASE = 10_000_000_000
XP_AFTER_RAID = XP_BASE + CONTRIBUTION_THRESHOLD  # exactly meets threshold


def _uuid(n: int) -> str:
    """Return a deterministic fake UUID string for player n."""
    return f"00000000-0000-0000-0000-{n:012d}"


def _make_player_result(uid, username, raid_counts=None, contributed=None):
    """Build a fake player API result dict."""
    counts = raid_counts or {}
    return {
        'uuid': uid,
        'username': username,
        'globalData': {
            'raids': {
                'list': {r: counts.get(r, 0) for r in RAID_NAMES}
            },
            'wars': 0,
        },
        'playtime': 100,
        'online': True,
    }


def _make_guild(members):
    """Build a fake Guild object with .all_members, .name, .level, .xpPercent, .online."""
    guild = MagicMock()
    guild.all_members = members
    guild.name = "The Aquarium"
    guild.level = 90
    guild.xpPercent = 50
    guild.online = len(members)
    return guild


class _FakeCog:
    """Lightweight stand-in for UpdateMemberData with only the fields steps 7-11b need."""
    pass


def _make_cog():
    """Create a fake cog with the same state shape as UpdateMemberData."""
    cog = _FakeCog()
    cog.RAID_NAMES = list(RAID_NAMES)
    cog.cold_start = False
    cog.previous_data = {}
    cog.raid_participants = {r: {"unvalidated": {}, "validated": {}} for r in RAID_NAMES}
    cog.xp_only_validated = {}
    cog._announce_raid = AsyncMock()
    return cog


async def _run_tick(cog, guild, contrib_map, results, now):
    """
    Execute steps 7-11b of the update loop with the given state.
    This extracts the core detection/validation/announcement logic
    so we can test it without mocking the full loop (Guild fetch, API calls, etc).
    """
    prev = cog.previous_data

    # --- 7: Build new snapshot & detect unvalidated ---
    new_data = dict(prev)
    fresh = set()
    name_map = {}

    for m in results:
        if not isinstance(m, dict):
            continue
        uid, uname = m['uuid'], m['username']
        fresh.add(uid)
        name_map[uid] = uname

        raids = m.get('globalData', {}).get('raids', {}).get('list', {})
        counts = {r: raids.get(r, 0) for r in RAID_NAMES}

        carried_contrib = new_data.get(uid, {}).get('contributed', 0)
        contributed = contrib_map.get(uid, carried_contrib)

        new_data[uid] = {'raids': counts, 'contributed': contributed}

        if not cog.cold_start and uid in prev:
            old_counts = prev.get(uid, {}).get('raids', {r: 0 for r in RAID_NAMES})
            for raid in RAID_NAMES:
                diff = counts[raid] - old_counts.get(raid, 0)
                if (
                    0 < diff < 3
                    and uid not in cog.raid_participants[raid]['unvalidated']
                    and uid not in cog.raid_participants[raid]['validated']
                ):
                    cog.raid_participants[raid]['unvalidated'][uid] = {
                        'name': uname,
                        'first_seen': now,
                        'baseline_contrib': prev.get(uid, {}).get('contributed', 0),
                    }

    # --- 8: XP jumps ---
    xp_jumps = set()
    for uid in fresh:
        if uid not in prev:
            continue
        old_c = prev[uid].get('contributed', 0)
        new_c = new_data[uid].get('contributed', 0)
        if new_c - old_c >= CONTRIBUTION_THRESHOLD:
            xp_jumps.add(uid)

    # --- 9: Validate via XP jump ---
    for raid, queues in cog.raid_participants.items():
        for uid in list(queues['unvalidated']):
            if uid in xp_jumps:
                info = queues['unvalidated'].pop(uid)
                queues['validated'][uid] = info

    # --- 9b: Cross-validate ---
    for raid, queues in cog.raid_participants.items():
        for uid in list(queues['unvalidated']):
            if uid in cog.xp_only_validated:
                info = queues['unvalidated'].pop(uid)
                queues['validated'][uid] = info
                cog.xp_only_validated.pop(uid)

    # --- 10: Validate via contrib diff ---
    for raid, queues in cog.raid_participants.items():
        for uid, info in list(queues['unvalidated'].items()):
            if uid not in fresh:
                continue
            base = info['baseline_contrib']
            curr = new_data.get(uid, {}).get('contributed', 0)
            if curr - base >= CONTRIBUTION_THRESHOLD:
                queues['validated'][uid] = info
                queues['unvalidated'].pop(uid)

    # --- 10b: xp_only pool ---
    for uid in xp_jumps:
        in_any_raid_queue = any(
            uid in cog.raid_participants[r]['unvalidated'] or uid in cog.raid_participants[r]['validated']
            for r in RAID_NAMES
        )
        if not in_any_raid_queue and uid not in cog.xp_only_validated:
            uname = name_map.get(uid, uid)
            cog.xp_only_validated[uid] = {"name": uname, "first_seen": now}

    # --- 11: Announce raids (with xp_only backfill) ---
    for raid in RAID_NAMES:
        vals = cog.raid_participants[raid]['validated']
        raid_validated_count = len(vals)
        if raid_validated_count == 0:
            continue
        needed = 4 - raid_validated_count
        if needed <= 0:
            group = set(list(vals)[:4])
            await cog._announce_raid(raid, group, guild)
            for uid in group:
                vals.pop(uid)
        elif len(cog.xp_only_validated) >= needed:
            xp_only_uids = list(cog.xp_only_validated.keys())[:needed]
            for uid in xp_only_uids:
                info = cog.xp_only_validated.pop(uid)
                vals[uid] = info
            group = set(list(vals)[:4])
            await cog._announce_raid(raid, group, guild)
            for uid in group:
                vals.pop(uid)

    # --- 11b: All-private group with grace period ---
    eligible_xp_only = {
        uid: info for uid, info in cog.xp_only_validated.items()
        if info['first_seen'] < now
    }
    while len(eligible_xp_only) >= 4:
        xp_only_uids = list(eligible_xp_only.keys())[:4]
        group = set(xp_only_uids)
        participant_names = {uid: cog.xp_only_validated[uid]["name"] for uid in xp_only_uids}
        for uid in xp_only_uids:
            cog.xp_only_validated.pop(uid)
            eligible_xp_only.pop(uid)
        await cog._announce_raid(None, group, guild, participant_names=participant_names)

    # --- 12: Persist ---
    cog.previous_data = new_data

    return {
        'new_data': new_data,
        'xp_jumps': xp_jumps,
        'fresh': fresh,
        'name_map': name_map,
    }


# ===========================================================================
# TESTS
# ===========================================================================

class TestStep7_RaidDetection:
    """Step 7: Detect unvalidated players when raid count increases."""

    @pytest.mark.asyncio
    async def test_raid_count_increase_adds_to_unvalidated(self):
        cog = _make_cog()
        uid = _uuid(1)
        # Previous data: 0 completions
        cog.previous_data = {uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}}
        # Current: 1 NotG completion, no XP jump
        results = [_make_player_result(uid, "Player1", {"Nest of the Grootslangs": 1})]
        contrib_map = {uid: XP_BASE}  # no XP change
        guild = _make_guild([{'uuid': uid, 'name': 'Player1', 'rank': 'Starfish', 'contributed': XP_BASE}])

        await _run_tick(cog, guild, contrib_map, results, T0)

        assert uid in cog.raid_participants["Nest of the Grootslangs"]['unvalidated']
        assert uid not in cog.raid_participants["Nest of the Grootslangs"]['validated']

    @pytest.mark.asyncio
    async def test_no_detection_on_cold_start(self):
        cog = _make_cog()
        cog.cold_start = True
        uid = _uuid(1)
        cog.previous_data = {uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}}
        results = [_make_player_result(uid, "Player1", {"Nest of the Grootslangs": 1})]
        contrib_map = {uid: XP_BASE}
        guild = _make_guild([{'uuid': uid, 'name': 'Player1', 'rank': 'Starfish', 'contributed': XP_BASE}])

        await _run_tick(cog, guild, contrib_map, results, T0)

        assert uid not in cog.raid_participants["Nest of the Grootslangs"]['unvalidated']

    @pytest.mark.asyncio
    async def test_no_detection_without_baseline(self):
        """Player not in previous_data (brand new) should not be detected."""
        cog = _make_cog()
        uid = _uuid(1)
        cog.previous_data = {}  # no baseline
        results = [_make_player_result(uid, "Player1", {"Nest of the Grootslangs": 5})]
        contrib_map = {uid: XP_BASE}
        guild = _make_guild([{'uuid': uid, 'name': 'Player1', 'rank': 'Starfish', 'contributed': XP_BASE}])

        await _run_tick(cog, guild, contrib_map, results, T0)

        assert uid not in cog.raid_participants["Nest of the Grootslangs"]['unvalidated']

    @pytest.mark.asyncio
    async def test_large_raid_diff_ignored(self):
        """Diff >= 3 should be ignored (suspicious data)."""
        cog = _make_cog()
        uid = _uuid(1)
        cog.previous_data = {uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}}
        results = [_make_player_result(uid, "Player1", {"Nest of the Grootslangs": 5})]
        contrib_map = {uid: XP_BASE}
        guild = _make_guild([{'uuid': uid, 'name': 'Player1', 'rank': 'Starfish', 'contributed': XP_BASE}])

        await _run_tick(cog, guild, contrib_map, results, T0)

        assert uid not in cog.raid_participants["Nest of the Grootslangs"]['unvalidated']

    @pytest.mark.asyncio
    async def test_multiple_raids_detected_independently(self):
        """Two different raid types increasing should both be detected."""
        cog = _make_cog()
        uid = _uuid(1)
        cog.previous_data = {uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}}
        results = [_make_player_result(uid, "Player1", {
            "Nest of the Grootslangs": 1,
            "The Canyon Colossus": 1,
        })]
        contrib_map = {uid: XP_BASE}
        guild = _make_guild([{'uuid': uid, 'name': 'Player1', 'rank': 'Starfish', 'contributed': XP_BASE}])

        await _run_tick(cog, guild, contrib_map, results, T0)

        assert uid in cog.raid_participants["Nest of the Grootslangs"]['unvalidated']
        assert uid in cog.raid_participants["The Canyon Colossus"]['unvalidated']


class TestStep8_XPThreshold:
    """Step 8: XP jump detection."""

    @pytest.mark.asyncio
    async def test_xp_jump_detected(self):
        cog = _make_cog()
        uid = _uuid(1)
        cog.previous_data = {uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}}
        results = [_make_player_result(uid, "Player1")]
        contrib_map = {uid: XP_AFTER_RAID}
        guild = _make_guild([{'uuid': uid, 'name': 'Player1', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}])

        info = await _run_tick(cog, guild, contrib_map, results, T0)

        assert uid in info['xp_jumps']

    @pytest.mark.asyncio
    async def test_xp_below_threshold_not_detected(self):
        cog = _make_cog()
        uid = _uuid(1)
        cog.previous_data = {uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}}
        small_bump = XP_BASE + CONTRIBUTION_THRESHOLD - 1
        results = [_make_player_result(uid, "Player1")]
        contrib_map = {uid: small_bump}
        guild = _make_guild([{'uuid': uid, 'name': 'Player1', 'rank': 'Starfish', 'contributed': small_bump}])

        info = await _run_tick(cog, guild, contrib_map, results, T0)

        assert uid not in info['xp_jumps']

    @pytest.mark.asyncio
    async def test_xp_jump_no_baseline_skipped(self):
        """New player with no previous_data should not trigger XP jump."""
        cog = _make_cog()
        uid = _uuid(1)
        cog.previous_data = {}
        results = [_make_player_result(uid, "Player1")]
        contrib_map = {uid: XP_AFTER_RAID}
        guild = _make_guild([{'uuid': uid, 'name': 'Player1', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}])

        info = await _run_tick(cog, guild, contrib_map, results, T0)

        assert uid not in info['xp_jumps']


class TestStep9_ValidateViaXPJump:
    """Step 9: Unvalidated + XP jump = validated."""

    @pytest.mark.asyncio
    async def test_raid_count_plus_xp_jump_validates(self):
        cog = _make_cog()
        uid = _uuid(1)
        cog.previous_data = {uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}}
        # Raid count +1 AND XP jump in the same tick
        results = [_make_player_result(uid, "Player1", {"Nest of the Grootslangs": 1})]
        contrib_map = {uid: XP_AFTER_RAID}
        guild = _make_guild([{'uuid': uid, 'name': 'Player1', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}])

        await _run_tick(cog, guild, contrib_map, results, T0)

        assert uid in cog.raid_participants["Nest of the Grootslangs"]['validated']
        assert uid not in cog.raid_participants["Nest of the Grootslangs"]['unvalidated']

    @pytest.mark.asyncio
    async def test_four_players_same_raid_announces(self):
        """4 players with raid count + XP jump should trigger announcement."""
        cog = _make_cog()
        uids = [_uuid(i) for i in range(1, 5)]
        cog.previous_data = {
            uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}
            for uid in uids
        }
        results = [_make_player_result(uid, f"Player{i}", {"The Canyon Colossus": 1})
                    for i, uid in enumerate(uids, 1)]
        contrib_map = {uid: XP_AFTER_RAID for uid in uids}
        members = [{'uuid': uid, 'name': f'Player{i}', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}
                    for i, uid in enumerate(uids, 1)]
        guild = _make_guild(members)

        await _run_tick(cog, guild, contrib_map, results, T0)

        # _announce_raid should have been called with "The Canyon Colossus"
        cog._announce_raid.assert_called_once()
        call_args = cog._announce_raid.call_args
        assert call_args[0][0] == "The Canyon Colossus"
        assert call_args[0][1] == set(uids)


class TestStep9b_CrossValidation:
    """Step 9b: Player in xp_only pool gets raid count later → cross-validated."""

    @pytest.mark.asyncio
    async def test_xp_only_then_raid_count_cross_validates(self):
        """
        THE KEY BUG FIX TEST:
        Tick 1: XP jump detected, no raid count change → goes to xp_only
        Tick 2: Raid count updates → should be cross-validated, not left in xp_only
        """
        cog = _make_cog()
        uid = _uuid(1)

        # --- Tick 1: XP jumps, no raid count change ---
        cog.previous_data = {uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}}
        results_t1 = [_make_player_result(uid, "Player1", {r: 0 for r in RAID_NAMES})]
        contrib_map_t1 = {uid: XP_AFTER_RAID}
        guild = _make_guild([{'uuid': uid, 'name': 'Player1', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}])

        await _run_tick(cog, guild, contrib_map_t1, results_t1, T0)

        # Player should be in xp_only, NOT in any raid queue
        assert uid in cog.xp_only_validated
        for r in RAID_NAMES:
            assert uid not in cog.raid_participants[r]['unvalidated']
            assert uid not in cog.raid_participants[r]['validated']

        # --- Tick 2: Raid count now updates ---
        results_t2 = [_make_player_result(uid, "Player1", {"Nest of the Grootslangs": 1})]
        contrib_map_t2 = {uid: XP_AFTER_RAID}  # no further XP change

        await _run_tick(cog, guild, contrib_map_t2, results_t2, T1)

        # Should be cross-validated into the specific raid
        assert uid in cog.raid_participants["Nest of the Grootslangs"]['validated']
        assert uid not in cog.xp_only_validated

    @pytest.mark.asyncio
    async def test_cross_validation_removes_from_xp_only(self):
        """Cross-validated player must be removed from xp_only_validated."""
        cog = _make_cog()
        uid = _uuid(1)

        # Manually place player in xp_only pool (simulating a previous tick)
        cog.xp_only_validated[uid] = {"name": "Player1", "first_seen": T0}
        cog.previous_data = {uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_AFTER_RAID}}

        # Raid count now increases
        results = [_make_player_result(uid, "Player1", {"The Nameless Anomaly": 1})]
        contrib_map = {uid: XP_AFTER_RAID}
        guild = _make_guild([{'uuid': uid, 'name': 'Player1', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}])

        await _run_tick(cog, guild, contrib_map, results, T1)

        assert uid not in cog.xp_only_validated
        assert uid in cog.raid_participants["The Nameless Anomaly"]['validated']


class TestStep10_ContribDiffValidation:
    """Step 10: Validate unvalidated player via contrib diff against baseline."""

    @pytest.mark.asyncio
    async def test_contrib_diff_validates(self):
        """Unvalidated player whose contrib grew >= threshold from baseline gets validated."""
        cog = _make_cog()
        uid = _uuid(1)

        # Tick 1: Raid detected, XP hasn't jumped yet
        cog.previous_data = {uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}}
        results_t1 = [_make_player_result(uid, "Player1", {"Orphion's Nexus of Light": 1})]
        contrib_map_t1 = {uid: XP_BASE}  # no XP change yet
        guild = _make_guild([{'uuid': uid, 'name': 'Player1', 'rank': 'Starfish', 'contributed': XP_BASE}])

        await _run_tick(cog, guild, contrib_map_t1, results_t1, T0)
        assert uid in cog.raid_participants["Orphion's Nexus of Light"]['unvalidated']

        # Tick 2: XP now arrives (but raid count same as last tick = no new raid diff)
        results_t2 = [_make_player_result(uid, "Player1", {"Orphion's Nexus of Light": 1})]
        contrib_map_t2 = {uid: XP_AFTER_RAID}

        await _run_tick(cog, guild, contrib_map_t2, results_t2, T1)

        assert uid in cog.raid_participants["Orphion's Nexus of Light"]['validated']
        assert uid not in cog.raid_participants["Orphion's Nexus of Light"]['unvalidated']


class TestStep10b_XPOnlyPool:
    """Step 10b: XP jump with no raid count change → xp_only_validated."""

    @pytest.mark.asyncio
    async def test_xp_jump_no_raid_goes_to_xp_only(self):
        cog = _make_cog()
        uid = _uuid(1)
        cog.previous_data = {uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}}
        results = [_make_player_result(uid, "Player1")]  # no raid count change
        contrib_map = {uid: XP_AFTER_RAID}
        guild = _make_guild([{'uuid': uid, 'name': 'Player1', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}])

        await _run_tick(cog, guild, contrib_map, results, T0)

        assert uid in cog.xp_only_validated
        assert cog.xp_only_validated[uid]['name'] == "Player1"

    @pytest.mark.asyncio
    async def test_xp_jump_with_raid_does_not_go_to_xp_only(self):
        """If raid count also increased, player should NOT go to xp_only."""
        cog = _make_cog()
        uid = _uuid(1)
        cog.previous_data = {uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}}
        results = [_make_player_result(uid, "Player1", {"Nest of the Grootslangs": 1})]
        contrib_map = {uid: XP_AFTER_RAID}
        guild = _make_guild([{'uuid': uid, 'name': 'Player1', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}])

        await _run_tick(cog, guild, contrib_map, results, T0)

        assert uid not in cog.xp_only_validated
        # Should be validated for the specific raid instead
        assert uid in cog.raid_participants["Nest of the Grootslangs"]['validated']


class TestStep11_BackfillAnnouncement:
    """Step 11: Raid-specific validated + xp_only backfill."""

    @pytest.mark.asyncio
    async def test_three_raid_one_xponly_backfills(self):
        """3 validated for a raid + 1 xp_only = group of 4, announced with raid theme."""
        cog = _make_cog()
        raid = "Nest of the Grootslangs"

        # 3 players already validated for NotG
        for i in range(1, 4):
            uid = _uuid(i)
            cog.raid_participants[raid]['validated'][uid] = {
                'name': f'Player{i}', 'first_seen': T0, 'baseline_contrib': XP_BASE
            }

        # 1 player in xp_only pool
        xp_uid = _uuid(10)
        cog.xp_only_validated[xp_uid] = {"name": "XPOnlyPlayer", "first_seen": T0}

        # Set up previous_data so the tick doesn't cause new detections
        all_uids = [_uuid(i) for i in range(1, 4)] + [xp_uid]
        cog.previous_data = {
            uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_AFTER_RAID}
            for uid in all_uids
        }
        results = [_make_player_result(uid, f"P{i}") for i, uid in enumerate(all_uids)]
        contrib_map = {uid: XP_AFTER_RAID for uid in all_uids}
        guild = _make_guild([{'uuid': uid, 'name': f'P{i}', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}
                              for i, uid in enumerate(all_uids)])

        await _run_tick(cog, guild, contrib_map, results, T1)

        cog._announce_raid.assert_called_once()
        call_args = cog._announce_raid.call_args
        assert call_args[0][0] == raid  # themed announcement
        assert xp_uid in call_args[0][1]  # backfilled player in group
        assert xp_uid not in cog.xp_only_validated  # removed from pool

    @pytest.mark.asyncio
    async def test_not_enough_xponly_no_backfill(self):
        """2 validated + 0 xp_only = no announcement (need 4)."""
        cog = _make_cog()
        raid = "The Canyon Colossus"

        for i in range(1, 3):
            uid = _uuid(i)
            cog.raid_participants[raid]['validated'][uid] = {
                'name': f'Player{i}', 'first_seen': T0, 'baseline_contrib': XP_BASE
            }

        cog.previous_data = {
            _uuid(i): {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_AFTER_RAID}
            for i in range(1, 3)
        }
        results = [_make_player_result(_uuid(i), f"Player{i}") for i in range(1, 3)]
        contrib_map = {_uuid(i): XP_AFTER_RAID for i in range(1, 3)}
        guild = _make_guild([{'uuid': _uuid(i), 'name': f'Player{i}', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}
                              for i in range(1, 3)])

        await _run_tick(cog, guild, contrib_map, results, T1)

        cog._announce_raid.assert_not_called()


class TestStep11b_GracePeriod:
    """Step 11b: All-private group must wait 1 tick (grace period)."""

    @pytest.mark.asyncio
    async def test_four_xponly_same_tick_not_announced(self):
        """4 players enter xp_only on the SAME tick → should NOT be announced yet."""
        cog = _make_cog()
        uids = [_uuid(i) for i in range(1, 5)]
        cog.previous_data = {
            uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}
            for uid in uids
        }
        # All get XP jump, no raid count change
        results = [_make_player_result(uid, f"Player{i}") for i, uid in enumerate(uids, 1)]
        contrib_map = {uid: XP_AFTER_RAID for uid in uids}
        members = [{'uuid': uid, 'name': f'Player{i}', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}
                    for i, uid in enumerate(uids, 1)]
        guild = _make_guild(members)

        await _run_tick(cog, guild, contrib_map, results, T0)

        # All should be in xp_only but NOT announced
        assert len(cog.xp_only_validated) == 4
        cog._announce_raid.assert_not_called()

    @pytest.mark.asyncio
    async def test_four_xponly_announced_next_tick(self):
        """4 players from a previous tick should be announced on the next tick."""
        cog = _make_cog()
        uids = [_uuid(i) for i in range(1, 5)]

        # Pre-populate xp_only with first_seen = T0 (previous tick)
        for i, uid in enumerate(uids, 1):
            cog.xp_only_validated[uid] = {"name": f"Player{i}", "first_seen": T0}

        # Previous data so no new detections fire
        cog.previous_data = {
            uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_AFTER_RAID}
            for uid in uids
        }
        results = [_make_player_result(uid, f"Player{i}") for i, uid in enumerate(uids, 1)]
        contrib_map = {uid: XP_AFTER_RAID for uid in uids}
        members = [{'uuid': uid, 'name': f'Player{i}', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}
                    for i, uid in enumerate(uids, 1)]
        guild = _make_guild(members)

        await _run_tick(cog, guild, contrib_map, results, T1)  # T1 > T0

        cog._announce_raid.assert_called_once()
        call_args = cog._announce_raid.call_args
        assert call_args[0][0] is None  # unthemed
        assert len(cog.xp_only_validated) == 0  # all consumed

    @pytest.mark.asyncio
    async def test_grace_period_allows_cross_validation(self):
        """
        END-TO-END: The grace period gives raid detection time to catch up.

        Tick 1: 4 players get XP jump, no raid counts → all enter xp_only, NOT announced
        Tick 2: Raid counts update for all 4 → cross-validated into NotG → themed announcement
        """
        cog = _make_cog()
        uids = [_uuid(i) for i in range(1, 5)]

        # --- Tick 1: XP jumps, no raid counts ---
        cog.previous_data = {
            uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}
            for uid in uids
        }
        results_t1 = [_make_player_result(uid, f"Player{i}") for i, uid in enumerate(uids, 1)]
        contrib_map_t1 = {uid: XP_AFTER_RAID for uid in uids}
        members = [{'uuid': uid, 'name': f'Player{i}', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}
                    for i, uid in enumerate(uids, 1)]
        guild = _make_guild(members)

        await _run_tick(cog, guild, contrib_map_t1, results_t1, T0)

        assert len(cog.xp_only_validated) == 4
        cog._announce_raid.assert_not_called()

        # --- Tick 2: Raid counts now update ---
        results_t2 = [_make_player_result(uid, f"Player{i}", {"Nest of the Grootslangs": 1})
                       for i, uid in enumerate(uids, 1)]
        contrib_map_t2 = {uid: XP_AFTER_RAID for uid in uids}

        await _run_tick(cog, guild, contrib_map_t2, results_t2, T1)

        # All should have been cross-validated into NotG and announced with theme
        assert len(cog.xp_only_validated) == 0
        cog._announce_raid.assert_called_once()
        call_args = cog._announce_raid.call_args
        assert call_args[0][0] == "Nest of the Grootslangs"
        assert call_args[0][1] == set(uids)

    @pytest.mark.asyncio
    async def test_grace_period_falls_through_to_private_if_no_raid(self):
        """
        If raid counts NEVER update, the all-private announcement fires on tick 2.

        Tick 1: 4 XP jumps, no raid → xp_only, not announced
        Tick 2: Still no raid → now eligible (first_seen < now) → announced as private
        """
        cog = _make_cog()
        uids = [_uuid(i) for i in range(1, 5)]

        # --- Tick 1 ---
        cog.previous_data = {
            uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}
            for uid in uids
        }
        results = [_make_player_result(uid, f"Player{i}") for i, uid in enumerate(uids, 1)]
        contrib_map = {uid: XP_AFTER_RAID for uid in uids}
        members = [{'uuid': uid, 'name': f'Player{i}', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}
                    for i, uid in enumerate(uids, 1)]
        guild = _make_guild(members)

        await _run_tick(cog, guild, contrib_map, results, T0)
        assert len(cog.xp_only_validated) == 4
        cog._announce_raid.assert_not_called()

        # --- Tick 2: still no raid counts ---
        results_t2 = [_make_player_result(uid, f"Player{i}") for i, uid in enumerate(uids, 1)]
        contrib_map_t2 = {uid: XP_AFTER_RAID for uid in uids}

        await _run_tick(cog, guild, contrib_map_t2, results_t2, T1)

        cog._announce_raid.assert_called_once()
        call_args = cog._announce_raid.call_args
        assert call_args[0][0] is None  # unthemed / private


class TestStep11b_MixedScenarios:
    """Mixed scenarios across multiple ticks."""

    @pytest.mark.asyncio
    async def test_partial_cross_validation_and_backfill(self):
        """
        Tick 1: 4 players XP jump, no raid counts
        Tick 2: 2 get raid counts (NotG), 2 remain xp_only
        → 2 cross-validated + 2 xp_only backfill = themed announcement
        """
        cog = _make_cog()
        uids = [_uuid(i) for i in range(1, 5)]

        # --- Tick 1: all XP jump ---
        cog.previous_data = {
            uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}
            for uid in uids
        }
        results_t1 = [_make_player_result(uid, f"Player{i}") for i, uid in enumerate(uids, 1)]
        contrib_map = {uid: XP_AFTER_RAID for uid in uids}
        members = [{'uuid': uid, 'name': f'Player{i}', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}
                    for i, uid in enumerate(uids, 1)]
        guild = _make_guild(members)

        await _run_tick(cog, guild, contrib_map, results_t1, T0)
        assert len(cog.xp_only_validated) == 4

        # --- Tick 2: first 2 get raid counts, last 2 don't ---
        results_t2 = []
        for i, uid in enumerate(uids, 1):
            if i <= 2:
                results_t2.append(_make_player_result(uid, f"Player{i}", {"Nest of the Grootslangs": 1}))
            else:
                results_t2.append(_make_player_result(uid, f"Player{i}"))

        await _run_tick(cog, guild, contrib_map, results_t2, T1)

        # Should announce themed (NotG) with 2 cross-validated + 2 backfilled
        cog._announce_raid.assert_called_once()
        call_args = cog._announce_raid.call_args
        assert call_args[0][0] == "Nest of the Grootslangs"
        assert call_args[0][1] == set(uids)
        assert len(cog.xp_only_validated) == 0

    @pytest.mark.asyncio
    async def test_five_players_two_ticks(self):
        """
        5 players XP jump tick 1, all get NotG tick 2.
        Should announce group of 4, leave 1 validated.
        """
        cog = _make_cog()
        uids = [_uuid(i) for i in range(1, 6)]

        # Tick 1
        cog.previous_data = {
            uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}
            for uid in uids
        }
        results_t1 = [_make_player_result(uid, f"Player{i}") for i, uid in enumerate(uids, 1)]
        contrib_map = {uid: XP_AFTER_RAID for uid in uids}
        members = [{'uuid': uid, 'name': f'Player{i}', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}
                    for i, uid in enumerate(uids, 1)]
        guild = _make_guild(members)

        await _run_tick(cog, guild, contrib_map, results_t1, T0)
        assert len(cog.xp_only_validated) == 5

        # Tick 2: all get raid counts
        results_t2 = [_make_player_result(uid, f"Player{i}", {"Nest of the Grootslangs": 1})
                       for i, uid in enumerate(uids, 1)]

        await _run_tick(cog, guild, contrib_map, results_t2, T1)

        cog._announce_raid.assert_called_once()
        call_args = cog._announce_raid.call_args
        assert call_args[0][0] == "Nest of the Grootslangs"
        assert len(call_args[0][1]) == 4  # group of 4

        # 1 should remain validated but not yet announced
        remaining_validated = cog.raid_participants["Nest of the Grootslangs"]['validated']
        assert len(remaining_validated) == 1

    @pytest.mark.asyncio
    async def test_no_double_announcement(self):
        """A player should never trigger two announcements."""
        cog = _make_cog()
        uids = [_uuid(i) for i in range(1, 5)]

        # Tick 1: all 4 get raid + XP → validated & announced
        cog.previous_data = {
            uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}
            for uid in uids
        }
        results = [_make_player_result(uid, f"Player{i}", {"Nest of the Grootslangs": 1})
                    for i, uid in enumerate(uids, 1)]
        contrib_map = {uid: XP_AFTER_RAID for uid in uids}
        members = [{'uuid': uid, 'name': f'Player{i}', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}
                    for i, uid in enumerate(uids, 1)]
        guild = _make_guild(members)

        await _run_tick(cog, guild, contrib_map, results, T0)
        assert cog._announce_raid.call_count == 1

        # Tick 2: same data, no changes → no new announcement
        cog._announce_raid.reset_mock()
        await _run_tick(cog, guild, contrib_map, results, T1)
        cog._announce_raid.assert_not_called()

    @pytest.mark.asyncio
    async def test_simultaneous_different_raids(self):
        """Two separate raid groups detected in the same tick."""
        cog = _make_cog()
        notg_uids = [_uuid(i) for i in range(1, 5)]
        tcc_uids = [_uuid(i) for i in range(5, 9)]
        all_uids = notg_uids + tcc_uids

        cog.previous_data = {
            uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}
            for uid in all_uids
        }

        results = []
        for i, uid in enumerate(notg_uids, 1):
            results.append(_make_player_result(uid, f"NP{i}", {"Nest of the Grootslangs": 1}))
        for i, uid in enumerate(tcc_uids, 1):
            results.append(_make_player_result(uid, f"TP{i}", {"The Canyon Colossus": 1}))

        contrib_map = {uid: XP_AFTER_RAID for uid in all_uids}
        members = [{'uuid': uid, 'name': f'P{i}', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}
                    for i, uid in enumerate(all_uids, 1)]
        guild = _make_guild(members)

        await _run_tick(cog, guild, contrib_map, results, T0)

        assert cog._announce_raid.call_count == 2
        raid_names_announced = {call.args[0] for call in cog._announce_raid.call_args_list}
        assert raid_names_announced == {"Nest of the Grootslangs", "The Canyon Colossus"}


class TestDataPersistence:
    """Step 12: Verify previous_data is updated correctly."""

    @pytest.mark.asyncio
    async def test_previous_data_updated_after_tick(self):
        cog = _make_cog()
        uid = _uuid(1)
        cog.previous_data = {uid: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE}}
        results = [_make_player_result(uid, "Player1", {"Nest of the Grootslangs": 1})]
        contrib_map = {uid: XP_AFTER_RAID}
        guild = _make_guild([{'uuid': uid, 'name': 'Player1', 'rank': 'Starfish', 'contributed': XP_AFTER_RAID}])

        await _run_tick(cog, guild, contrib_map, results, T0)

        assert cog.previous_data[uid]['raids']['Nest of the Grootslangs'] == 1
        assert cog.previous_data[uid]['contributed'] == XP_AFTER_RAID

    @pytest.mark.asyncio
    async def test_carry_forward_unfetched_members(self):
        """Members not in results should keep their previous data."""
        cog = _make_cog()
        uid1 = _uuid(1)
        uid2 = _uuid(2)
        cog.previous_data = {
            uid1: {'raids': {r: 0 for r in RAID_NAMES}, 'contributed': XP_BASE},
            uid2: {'raids': {r: 5 for r in RAID_NAMES}, 'contributed': 999},
        }
        # Only uid1 returns fresh data
        results = [_make_player_result(uid1, "Player1")]
        contrib_map = {uid1: XP_BASE}
        guild = _make_guild([{'uuid': uid1, 'name': 'Player1', 'rank': 'Starfish', 'contributed': XP_BASE}])

        await _run_tick(cog, guild, contrib_map, results, T0)

        # uid2 should be carried forward
        assert uid2 in cog.previous_data
        assert cog.previous_data[uid2]['contributed'] == 999
