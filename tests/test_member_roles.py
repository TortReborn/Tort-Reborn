"""Tests for Helpers/member_roles.py — the single source of truth for
membership role names (TAQ-51 / TAQ-67).

What matters here:

1. Removal restores the honorifics independently — Honored Fish and Retired
   Chief are separate honors; neither implies the other.
2. The removal list can never strip what removal itself just granted
   (Ex-Member, restored honorifics) — that invariant is what makes the
   restore safe to run in one pass.
3. The list matches the guild as it exists today: the new military block
   (⚬ Shelf … ⚬ ⚬ ⚬ ⚬ Abyss), HQ Team, and the renamed CONTRIBUTION header
   are present; the stale pre-rename spellings are gone.
4. Registration strips exactly the honorifics/visitor roles and records the
   honorific flags first.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import member_roles as mr


class FakeRole:
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"<Role {self.name}>"


class FakeMember:
    def __init__(self, roles):
        self.roles = list(roles)


# ── removal planning ─────────────────────────────────────────────────────

def test_removal_adds_only_ex_member_without_honorifics():
    to_add, _ = mr.removal_role_names()
    assert to_add == [mr.EX_MEMBER_ROLE]


def test_removal_restores_honored_fish_alone():
    to_add, _ = mr.removal_role_names(was_honored_fish=True)
    assert to_add == [mr.EX_MEMBER_ROLE, mr.HONORED_FISH_ROLE]


def test_removal_of_retired_chief_also_grants_honored_fish():
    # TAQ-67: Retired Chief is the higher honor and implies Honored Fish.
    to_add, _ = mr.removal_role_names(was_retired_chief=True)
    assert to_add == [mr.EX_MEMBER_ROLE, mr.HONORED_FISH_ROLE, mr.RETIRED_CHIEF_ROLE]


def test_removal_restores_both_when_both_were_held():
    to_add, _ = mr.removal_role_names(was_honored_fish=True, was_retired_chief=True)
    assert to_add == [mr.EX_MEMBER_ROLE, mr.HONORED_FISH_ROLE, mr.RETIRED_CHIEF_ROLE]


def test_removal_never_strips_what_it_grants():
    to_add, to_remove = mr.removal_role_names(True, True)
    assert not set(to_add) & set(to_remove)
    # And the static list itself can never contain the keeps.
    for keep in (mr.EX_MEMBER_ROLE, mr.HONORED_FISH_ROLE, mr.RETIRED_CHIEF_ROLE):
        assert keep not in mr.MEMBER_REMOVE_ROLES


def test_removal_list_covers_current_military_block():
    for name in ['⚬ Shelf', '⚬ ⚬ Slope', '⚬ ⚬ ⚬ Rise', '⚬ ⚬ ⚬ ⚬ Abyss',
                 'HQ Team', 'DPS', 'Tank', 'Healer', 'EcoFish',
                 'War Trainer', 'Territory Munching', 'Soloer']:
        assert name in mr.MEMBER_REMOVE_ROLES, name


def test_removal_list_uses_renamed_contribution_header():
    assert mr.CONTRIBUTION_HEADER in mr.MEMBER_REMOVE_ROLES
    assert not mr.CONTRIBUTION_HEADER.startswith('🏆')


def test_removal_list_dropped_stale_role_names():
    # Roles that no longer exist in the guild; keeping them would only hide
    # future name-rot behind silent no-ops.
    stale = {'🏹Spearhead', '⚠️Standby', '🗡️FFA', 'Orca', 'War News'}
    assert not stale & set(mr.MEMBER_REMOVE_ROLES)


def test_removal_list_has_no_duplicates():
    assert len(mr.MEMBER_REMOVE_ROLES) == len(set(mr.MEMBER_REMOVE_ROLES))


# ── registration planning ────────────────────────────────────────────────

def test_registration_strips_honorifics_and_visitor_roles():
    _, to_remove = mr.registration_role_names('Starfish')
    assert set(to_remove) == {mr.LAND_CRAB_ROLE, mr.HONORED_FISH_ROLE,
                              mr.RETIRED_CHIEF_ROLE, mr.EX_MEMBER_ROLE}


def test_registration_adds_membership_rank_and_headers():
    to_add, _ = mr.registration_role_names('Piranha')
    assert mr.MEMBER_ROLE in to_add
    assert mr.TAQ_TAG_ROLE in to_add
    assert 'Piranha' in to_add
    for header in mr.REGISTRATION_HEADER_ROLES:
        assert header in to_add
    assert mr.CONTRIBUTION_HEADER in to_add


def test_honorific_flags_read_from_role_names():
    assert mr.honorific_flags(['Member', 'DPS']) == (False, False)
    assert mr.honorific_flags(['Honored Fish']) == (True, False)
    assert mr.honorific_flags(['Retired Chief']) == (False, True)
    assert mr.honorific_flags(['Honored Fish', 'Retired Chief']) == (True, True)


# ── role resolution ──────────────────────────────────────────────────────

def test_resolve_roles_skips_names_the_guild_lacks():
    guild_roles = [FakeRole('Member'), FakeRole('DPS')]
    roles = mr.resolve_roles(guild_roles, ['Member', 'Ghost Role', 'DPS'])
    assert [r.name for r in roles] == ['Member', 'DPS']


def test_resolve_roles_present_filter():
    member_role = FakeRole('Member')
    dps_role = FakeRole('DPS')
    ex_role = FakeRole('Ex-Member')
    guild_roles = [member_role, dps_role, ex_role]
    member = FakeMember([member_role])

    held = mr.resolve_roles(guild_roles, ['Member', 'DPS', 'Ex-Member'],
                            member=member, present=True)
    assert held == [member_role]

    missing = mr.resolve_roles(guild_roles, ['Member', 'DPS', 'Ex-Member'],
                               member=member, present=False)
    assert missing == [dps_role, ex_role]


# ── rank changes (/manage rank, TAQ-86) ──────────────────────────────────

from Helpers.variables import discord_ranks, discord_rank_roles


def test_rank_role_names_adds_own_roles_and_strips_every_other_rank_role():
    for rank in discord_ranks:
        to_add, to_remove = mr.rank_role_names(rank)
        assert to_add == list(discord_ranks[rank]['roles'])
        assert not set(to_add) & set(to_remove)
        assert set(to_add) | set(to_remove) == set(discord_rank_roles)


def test_rank_role_names_touches_nothing_but_rank_roles():
    to_add, to_remove = mr.rank_role_names('Piranha')
    for name in (mr.MEMBER_ROLE, mr.TAQ_TAG_ROLE, mr.HONORED_FISH_ROLE, mr.RETIRED_CHIEF_ROLE,
                 mr.EX_MEMBER_ROLE, *mr.REGISTRATION_HEADER_ROLES):
        assert name not in to_add and name not in to_remove


class RecordingMember(FakeMember):
    """A member whose add_roles / remove_roles mutate .roles and log the call."""

    def __init__(self, roles):
        super().__init__(roles)
        self.calls = []

    async def add_roles(self, *roles, reason=None):
        self.calls.append(('add', [r.name for r in roles], reason))
        self.roles.extend(roles)

    async def remove_roles(self, *roles, reason=None):
        self.calls.append(('remove', [r.name for r in roles], reason))
        self.roles = [r for r in self.roles if r not in roles]


def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


def test_apply_rank_roles_promotes_and_reports_only_what_changed():
    guild_roles = {n: FakeRole(n) for n in discord_rank_roles + ['Member']}
    member = RecordingMember([guild_roles['Member'], guild_roles['Starfish'], guild_roles['☆Reef']])

    added, removed = _run(mr.apply_rank_roles(member, list(guild_roles.values()), 'Piranha', reason='test'))

    assert added == list(discord_ranks['Piranha']['roles'])
    assert removed == ['Starfish', '☆Reef']
    held = {r.name for r in member.roles}
    assert held == {'Member', *discord_ranks['Piranha']['roles']}
    assert [c[0] for c in member.calls] == ['add', 'remove']
    assert all(c[2] == 'test' for c in member.calls)


def test_apply_rank_roles_on_unlinked_member_with_no_rank_roles():
    # The TAQ-86 case: the modal path's target holds no rank roles yet.
    guild_roles = {n: FakeRole(n) for n in discord_rank_roles}
    member = RecordingMember([])

    added, removed = _run(mr.apply_rank_roles(member, list(guild_roles.values()), 'Piranha'))

    assert added == list(discord_ranks['Piranha']['roles'])
    assert removed == []
    assert member.calls == [('add', added, None)]      # no empty remove_roles call


def test_apply_rank_roles_is_a_no_op_when_already_at_rank():
    guild_roles = {n: FakeRole(n) for n in discord_rank_roles}
    member = RecordingMember([guild_roles[n] for n in discord_ranks['Hammerhead']['roles']])

    added, removed = _run(mr.apply_rank_roles(member, list(guild_roles.values()), 'Hammerhead'))

    assert (added, removed) == ([], [])
    assert member.calls == []


def test_apply_rank_roles_skips_roles_the_guild_lacks():
    guild_roles = [FakeRole('Piranha')]        # '★★ Azure Ocean' missing
    member = RecordingMember([])

    added, removed = _run(mr.apply_rank_roles(member, guild_roles, 'Piranha'))

    assert added == ['Piranha'] and removed == []


def test_rank_change_summary_lists_both_sections_even_when_empty():
    assert mr.rank_change_summary([], []) == 'Added Roles:\n\nRemoved Roles:'
    assert mr.rank_change_summary(['Piranha'], ['Starfish', '☆Reef']) == (
        'Added Roles:\n - Piranha\n\nRemoved Roles:\n - Starfish\n - ☆Reef'
    )
