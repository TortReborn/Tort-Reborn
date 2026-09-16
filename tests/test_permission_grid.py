"""The permission grid from docs/specs/taq-76-surface-matrix.md §G, as tests.

Every gate the overhaul touches is a pure function of (actor rank, target
rank, action), so the whole table is asserted here across all ten ranks
rather than a handful of spot checks. If guild policy changes (say Retired
Chief becomes Hydra-only) this file and the matrix change together.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Commands.honorifics import can_manage
from Helpers import honorifics as hon
from Helpers.member_removal import REMOVAL_FLOOR_RANK, check_reset_permission, meets_floor
from Helpers.variables import discord_ranks

RANKS = list(discord_ranks)          # Starfish .. Hydra, lowest first
NARWHAL = RANKS.index('Narwhal')
HAMMERHEAD = RANKS.index('Hammerhead')


def idx(rank):
    return RANKS.index(rank)


# ── /reset_roles, Member | Remove, /stale-roles, leave buttons ───────────

def test_floor_is_hammerhead():
    assert REMOVAL_FLOOR_RANK == 'Hammerhead'
    assert [r for r in RANKS if meets_floor(r)] == RANKS[HAMMERHEAD:]
    assert not meets_floor(None) and not meets_floor('') and not meets_floor('Navigator')


@pytest.mark.parametrize('actor', RANKS)
@pytest.mark.parametrize('target', RANKS)
def test_reset_needs_floor_and_target_strictly_below(actor, target):
    refusal = check_reset_permission(actor, (target,))
    if idx(actor) < HAMMERHEAD:
        assert refusal is not None and 'Hammerhead or higher' in refusal[1]
    elif idx(target) < idx(actor):
        assert refusal is None
    else:
        assert refusal is not None and 'lower rank' in refusal[1]


@pytest.mark.parametrize('actor', RANKS)
def test_reset_on_unlinked_or_rankless_target_still_needs_floor(actor):
    allowed = idx(actor) >= HAMMERHEAD
    assert (check_reset_permission(actor, None) is None) is allowed
    assert (check_reset_permission(actor, (None,)) is None) is allowed


@pytest.mark.parametrize('actor', [None, '', 'Navigator', 'WeaponMerchant'])
def test_reset_refused_without_a_recognised_rank(actor):
    refusal = check_reset_permission(actor, ('Starfish',))
    assert refusal is not None
    if actor is None:
        assert 'linked account' in refusal[1]
    else:
        assert 'not recognized' in refusal[1]


def test_reset_refuses_unrecognised_target_rank():
    assert check_reset_permission('Hydra', ('Barracuda',)) is not None


# ── /honorifics grant / revoke ───────────────────────────────────────────

@pytest.mark.parametrize('actor', RANKS)
def test_honored_fish_grant_and_lookup_need_hammerhead_plus(actor):
    assert can_manage(actor, hon.HONORED_FISH) is (idx(actor) >= HAMMERHEAD)
    assert can_manage(actor) is (idx(actor) >= HAMMERHEAD)          # lookup


@pytest.mark.parametrize('actor', RANKS)
def test_retired_chief_grant_needs_narwhal_plus(actor):
    assert can_manage(actor, hon.RETIRED_CHIEF) is (idx(actor) >= NARWHAL)


@pytest.mark.parametrize('actor', [None, '', 'Navigator'])
def test_honorific_management_refused_without_member_rank(actor):
    assert can_manage(actor, hon.HONORED_FISH) is False
    assert can_manage(actor, hon.RETIRED_CHIEF) is False


# ── the queue's Retired Chief gate mirrors the same threshold ────────────

def test_narwhal_threshold_is_the_shared_constant():
    # promotion_queue_processor and leave_prompts both compare against
    # ranks_list.index('Narwhal'); pin the position so a rank restructure
    # that moves Narwhal is noticed here rather than in prod.
    assert RANKS[NARWHAL:] == ['Narwhal', 'Hydra']
