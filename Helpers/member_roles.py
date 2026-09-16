"""Single source of truth for the Discord role names tied to TAq guild membership.

Before this module, the registration flows (/new_member, the NewMember modal,
auto-registration) and the removal flows (/reset_roles, the "Member | Remove"
user command, the website promotion queue) each carried their own copy of the
role lists, and they drifted: renamed roles kept their old spelling, new roles
never got added (TAQ-67). Every flow now plans its role changes here.

Several names carry invisible U+2800 (braille blank) padding that fakes section
headers in the sidebar, so those are built from escapes with an explicit count
rather than pasted literals. All names were verified character-for-character
against the prod guild's role list on 2026-08-20 (TAQ-51 / TAQ-67).
"""

from Helpers.variables import discord_ranks, discord_rank_roles

# --- Membership -------------------------------------------------------------
MEMBER_ROLE = 'Member'
TAQ_TAG_ROLE = 'The Aquarium [TAq]'
LAND_CRAB_ROLE = 'Land Crab'
EX_MEMBER_ROLE = 'Ex-Member'

# Honorifics an ex-member may keep. Retired Chief is the higher honor and
# implies Honored Fish: restoring it grants both (TAQ-67). Honored Fish on
# its own implies nothing further.
HONORED_FISH_ROLE = 'Honored Fish'
RETIRED_CHIEF_ROLE = 'Retired Chief'

# --- Section header roles ---------------------------------------------------
RANKS_HEADER = '\U0001F947 RANKS' + '⠀' * 26
PROFESSIONS_HEADER = '\U0001F6E0️ PROFESSIONS' + '⠀' * 20
COSMETIC_HEADER = '✨ COSMETIC ROLES' + '⠀' * 18
# The guild role lost its leading trophy emoji at some point; the old
# '🏆 CONTRIBUTION ROLES' spelling no longer matches anything.
CONTRIBUTION_HEADER = 'CONTRIBUTION ROLES' + '⠀' * 16
MILITARY_HEADER = '\U0001F396️MILITARY' + '⠀' * 24

# --- Military block (everything under the 🎖️MILITARY header) ----------------
MILITARY_ROLES = [
    '⚬ Shelf',
    '⚬ ⚬ Slope',
    '⚬ ⚬ ⚬ Rise',
    '⚬ ⚬ ⚬ ⚬ Abyss',
    'War Trainer',
    'Territory Munching',
    'Soloer',
    'EcoFish',
    'HQ Team',
    'DPS',
    'Healer',
    'Tank',
]

# --- Guild staff positions --------------------------------------------------
STAFF_ROLES = [
    'Event Team Manager',
    'Event Team',
    'Giveaway Manager',
    'Shell Manager',
    'Build Manager',
]

# --- Contribution awards (bi-weekly tiers and standing #1 honors) -----------
CONTRIBUTION_ROLES = [
    'Goblin Shark - Tier 7 Bi-Weekly War Contribution',
    'Giant Phantom Jelly - Tier 7 Bi-Weekly Raid Contribution',
    'Great White Shark - Tier 3 Bi-Weekly War Contribution',
    'Megalodon - Tier 3 Bi-Weekly Raid Contribution',
    'Orca - Tier 2 Bi-Weekly War Contribution',
    'Mosasaurus - Tier 2 Bi-Weekly Raid Contribution',
    'Mako Shark - Tier 1 Bi-Weekly War Contribution',
    'Liopleurodon - Tier 1 Bi-Weekly Raid Contribution',
    'Noobwhal - #1 XP contributed',
    '#1 - Shells',
    'Raidfish (Graid Event Top #5)',
]

# Headers every registered member gets so the sidebar sections show up.
REGISTRATION_HEADER_ROLES = [
    RANKS_HEADER,
    PROFESSIONS_HEADER,
    COSMETIC_HEADER,
    CONTRIBUTION_HEADER,
]

# Everything guild-related a member loses when they stop being a member.
# discord_rank_roles covers all rank pairs plus the MODERATOR / SR. MODERATOR
# headers and the Hydra leader role.
MEMBER_REMOVE_ROLES = [
    MEMBER_ROLE,
    TAQ_TAG_ROLE,
    *discord_rank_roles,
    *REGISTRATION_HEADER_ROLES,
    MILITARY_HEADER,
    *MILITARY_ROLES,
    *STAFF_ROLES,
    *CONTRIBUTION_ROLES,
]


def honorific_flags(role_names):
    """(had_honored_fish, had_retired_chief) from an iterable of role names.

    Registration flows call this *before* stripping the honorifics so the
    status can be recorded in discord_links and given back when the member is
    later removed (TAQ-51).
    """
    names = set(role_names)
    return HONORED_FISH_ROLE in names, RETIRED_CHIEF_ROLE in names


def registration_role_names(starting_rank):
    """(to_add, to_remove) role names for registering a (re)joining member."""
    to_add = [
        MEMBER_ROLE,
        TAQ_TAG_ROLE,
        *discord_ranks[starting_rank]['roles'],
        *REGISTRATION_HEADER_ROLES,
    ]
    to_remove = [LAND_CRAB_ROLE, HONORED_FISH_ROLE, RETIRED_CHIEF_ROLE, EX_MEMBER_ROLE]
    return to_add, to_remove


def rank_role_names(rank):
    """(to_add, to_remove) role names for setting a member's rank to ``rank``.

    Adds the rank's own roles and strips every other rank role (including
    the moderator headers and the leader role). Membership, headers and
    honorifics are not touched — this is a rank change, not a registration.
    """
    to_add = list(discord_ranks[rank]['roles'])
    to_remove = [r for r in discord_rank_roles if r not in to_add]
    return to_add, to_remove


async def apply_rank_roles(member, all_roles, rank, *, reason=None):
    """Give ``member`` the Discord roles for ``rank`` and take away the other
    rank roles. Returns (added_names, removed_names) of what actually changed.

    The one place /manage rank's two branches (linked target, and the
    link-first modal for an unlinked target) apply roles, so they cannot
    drift again (TAQ-86: the modal path recorded the rank but never touched
    the roles).
    """
    to_add, to_remove = rank_role_names(rank)
    roles_to_add = resolve_roles(all_roles, to_add, member, present=False)
    roles_to_remove = resolve_roles(all_roles, to_remove, member, present=True)
    if roles_to_add:
        await member.add_roles(*roles_to_add, reason=reason)
    if roles_to_remove:
        await member.remove_roles(*roles_to_remove, reason=reason)
    return [r.name for r in roles_to_add], [r.name for r in roles_to_remove]


def rank_change_summary(added, removed):
    """The ephemeral reply both /manage rank branches send."""
    lines = ['Added Roles:'] + [f' - {n}' for n in added] + ['', 'Removed Roles:'] + [f' - {n}' for n in removed]
    return '\n'.join(lines)


def removal_role_names(was_honored_fish=False, was_retired_chief=False):
    """(to_add, to_remove) role names for turning a member into an ex-member.

    Honorifics held before the member (re)joined are given back (TAQ-51).
    Retired Chief also grants Honored Fish (TAQ-67).
    """
    to_add = [EX_MEMBER_ROLE]
    if was_honored_fish or was_retired_chief:
        to_add.append(HONORED_FISH_ROLE)
    if was_retired_chief:
        to_add.append(RETIRED_CHIEF_ROLE)
    return to_add, list(MEMBER_REMOVE_ROLES)


def resolve_roles(all_roles, names, member=None, present=None):
    """Map role names to Role objects from ``all_roles``, dropping names the
    guild doesn't have.

    ``member`` (or ``present``, an explicit bool) filters by current holding:
    ``present=False`` keeps only roles the member is missing (for add),
    ``present=True`` only roles they hold (for remove).
    """
    by_name = {r.name: r for r in all_roles}
    roles = [by_name[n] for n in names if n in by_name]
    if member is not None and present is not None:
        held = set(member.roles)
        roles = [r for r in roles if (r in held) == present]
    return roles
