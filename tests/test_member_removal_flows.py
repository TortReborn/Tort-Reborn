"""End-to-end worked examples for the TAQ-51 / TAQ-67 / TAQ-76 member flows.

These drive the *real* code -- registration (Helpers/registration), the
`/reset_roles` slash command callback, the "Member | Remove" user command
callback, the promotion queue's `_do_remove`, `/stale-roles`' reset and the
leave-message buttons -- against fake Discord objects (a role inventory
copied from the prod guild) and the real database schema (session TEMP
tables shadowing discord_links / membership_stints / member_honorifics, see
conftest.py). Each scenario asserts the member's exact final role set, not
just deltas, so an over- or under-strip fails loudly, and the DB side: rank
cleared, honorific recorded, rank_at_leave stamped.

Worked examples:

1. TAQ-51's report verbatim: brenzoned held Honored Fish, rejoined as
   Manatee, was later removed -- and must end up with Honored Fish again,
   with the honorific having been recorded in the ledger at registration.
2. A Retired Chief does the same round trip and gets Retired Chief back,
   which also brings Honored Fish with it (TAQ-67).
3. A veteran holding the full guild stack is stripped down to exactly
   Ex-Member + restored honorific + their non-guild roles, via the website
   queue, whose removal now leaves the identity row alone (TAQ-76).
4. The website queue can attach an honorific to a removal; it lands in the
   ledger before the roles change.
5. No discord_links row -> plain Ex-Member, no restore, no crash.
6. Removal is idempotent.
7. Permission rule shared by every surface.
8. Leave-message buttons: gone from Discord -> grant recorded only;
   already an ex-member -> nothing to reset.
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import honorifics as hon
from Helpers import member_roles as mr
from Helpers import roster
from Helpers.functions import determine_starting_rank
from Helpers.registration import record_registration

UUID_A = '065fc385-f9c1-4e7f-96b3-8674a65c509f'
UUID_B = '9aeb062a-f769-49bc-8046-4d9c8cc86e5a'
UUID_C = '110c11c8-d8b7-478d-8adf-b0f606d5f939'
NARWHAL_ID = 1


# ── fakes ────────────────────────────────────────────────────────────────

class FakeRole:
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"<Role {self.name!r}>"


class FakePermissions:
    manage_roles = True


class FakeMember:
    def __init__(self, member_id, role_names, guild):
        self.id = member_id
        self.guild = guild
        self.roles = [guild.role(n) for n in role_names]
        self.name = f"user-{member_id}"
        self.display_name = self.name
        self.mention = f"<@{member_id}>"
        self.guild_permissions = FakePermissions()
        self.nick_edits = []

    async def add_roles(self, *roles, reason=None, atomic=True):
        for r in roles:
            assert r not in self.roles, f"double-add of {r}"
            self.roles.append(r)

    async def remove_roles(self, *roles, reason=None, atomic=True):
        for r in roles:
            assert r in self.roles, f"removing role not held: {r}"
            self.roles.remove(r)

    async def edit(self, nick=None, reason=None):
        self.nick_edits.append(nick)

    @property
    def role_names(self):
        return {r.name for r in self.roles}


class FakeGuild:
    """Role inventory mirroring the prod guild (relevant subset)."""

    def __init__(self):
        names = [
            '@everyone', mr.MEMBER_ROLE, mr.TAQ_TAG_ROLE, mr.LAND_CRAB_ROLE,
            mr.EX_MEMBER_ROLE, mr.HONORED_FISH_ROLE, mr.RETIRED_CHIEF_ROLE,
            'Starfish', '☆Reef', 'Manatee', '★Coastal Waters', 'Piranha',
            '★★ Azure Ocean', 'Angler', 'Swordfish', '★☆☆ Blue Sea',
            'Hammerhead', '★★☆Deep Sea', 'Sailfish', '★★★Dark Sea',
            'Dolphin', 'Narwhal', '★★★★Abyss Waters',
            mr.RANKS_HEADER, mr.PROFESSIONS_HEADER, mr.COSMETIC_HEADER,
            mr.CONTRIBUTION_HEADER, mr.MILITARY_HEADER,
            *mr.MILITARY_ROLES, *mr.STAFF_ROLES, *mr.CONTRIBUTION_ROLES,
            'Merman', 'Europe', 'Giveaways', 'Sea Pickle - Booster',
            'Tortoise - Community',
        ]
        self.roles = [FakeRole(n) for n in dict.fromkeys(names)]
        self._by_name = {r.name: r for r in self.roles}
        self._members = {}

    def role(self, name):
        return self._by_name[name]

    def add_member(self, member):
        self._members[member.id] = member
        return member

    def get_member(self, member_id):
        return self._members.get(member_id)

    async def fetch_member(self, member_id):
        import discord
        member = self._members.get(member_id)
        if member is None:
            raise discord.NotFound(FakeResponse(), 'Unknown Member')
        return member


class FakeResponse:
    status = 404
    reason = 'Not Found'


class FakeInteractionMessage:
    """Stands in for the ApplicationContext of the /reset_roles slash command
    and the Interaction of the Member | Remove user command."""

    def __init__(self, invoker_id, guild):
        self.guild = guild
        self.user = FakeMember(invoker_id, [], guild)
        self.author = self.user
        self.interaction = self
        self.responses = []

    async def defer(self, ephemeral=False):
        pass

    async def respond(self, *args, **kwargs):
        self.responses.append((args, kwargs))

    def last_embed(self):
        args, kwargs = self.responses[-1]
        return kwargs.get('embed') or args[0]


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── DB seeding ───────────────────────────────────────────────────────────

def seed_link(db, discord_id, uuid, ign, rank):
    db.cursor.execute(
        "INSERT INTO discord_links (discord_id, ign, uuid, rank) VALUES (%s, %s, %s::uuid, %s)",
        (discord_id, ign, uuid, rank),
    )


def seed_member(db, discord_id, uuid, ign, rank, *, on_roster=True):
    seed_link(db, discord_id, uuid, ign, rank)
    if on_roster:
        db.cursor.execute(
            "INSERT INTO guild_roster (uuid, ign, in_game_rank) VALUES (%s::uuid, %s, 'recruit')", (uuid, ign))
        roster.open_stint(db.cursor, uuid, joined_at='2026-01-01T00:00:00Z', discord_id=discord_id)


def link_row(db, discord_id):
    db.cursor.execute("SELECT rank FROM discord_links WHERE discord_id = %s", (discord_id,))
    row = db.cursor.fetchone()
    return row[0] if row else 'NO ROW'


def patch_all_db(monkeypatch, db):
    """Every module that opens its own DB() gets the shared temp-table session."""
    import Commands.reset_roles as cmd_mod
    import Helpers.leave_prompts as lp_mod
    import Helpers.member_removal as rm_mod
    for mod in (cmd_mod, lp_mod, rm_mod):
        monkeypatch.setattr(mod, 'DB', lambda: db)


def register(db, member, ign, uuid, actor_id=NARWHAL_ID, wars=0):
    """The shared registration plan: roles via member_roles, DB via
    Helpers.registration -- what /new_member, the modal and auto-registration
    all do."""
    starting_rank = determine_starting_rank(member)
    held = [r.name for r in member.roles]
    to_add, to_remove = mr.registration_role_names(starting_rank)
    run(member.add_roles(*mr.resolve_roles(member.guild.roles, to_add, member=member, present=False)))
    run(member.remove_roles(*mr.resolve_roles(member.guild.roles, to_remove, member=member, present=True)))
    db.cursor.execute("INSERT INTO guild_roster (uuid, ign, in_game_rank) VALUES (%s::uuid, %s, 'recruit') ON CONFLICT DO NOTHING", (uuid, ign))
    roster.open_stint(db.cursor, uuid, joined_at='2026-01-01T00:00:00Z')
    recorded = record_registration(
        db.cursor, discord_id=member.id, ign=ign, uuid=uuid, rank=starting_rank,
        wars_on_join=wars, held_role_names=held, actor_id=actor_id,
    )
    return starting_rank, recorded


# ── the real removal entry points ────────────────────────────────────────

def remove_via_slash_command(member, invoker, db, monkeypatch):
    import Commands.reset_roles as cmd_mod
    patch_all_db(monkeypatch, db)
    cog = cmd_mod.ResetRolesCommand(client=None)
    ctx = FakeInteractionMessage(invoker.id, member.guild)
    run(cog.reset_roles.callback(cog, ctx, member))
    return ctx


def remove_via_user_command(member, invoker, db, monkeypatch):
    import UserCommands.reset_roles as ucmd_mod
    patch_all_db(monkeypatch, db)
    cog = ucmd_mod.ResetRoles(client=None)
    interaction = FakeInteractionMessage(invoker.id, member.guild)
    run(cog.reset_roles.callback(cog, interaction, member))
    return interaction


def remove_via_promotion_queue(member, db, monkeypatch, grant=None):
    import Tasks.promotion_queue_processor as pq_mod
    patch_all_db(monkeypatch, db)
    proc = pq_mod.PromotionQueueProcessor(client=None)
    entry = {'id': 77, 'queued_by_ign': 'QueuerIGN', 'queued_by_discord_id': NARWHAL_ID, 'grant_honorific': grant}
    run(proc._do_remove(entry, member, member.guild))


# ── worked example 1: TAQ-51's report (brenzoned) ────────────────────────

def test_honored_fish_round_trip_via_slash_command(temp_db, monkeypatch):
    db = temp_db
    guild = FakeGuild()
    seed_link(db, NARWHAL_ID, UUID_C, 'Narwhal1', 'Narwhal')
    brenzoned = FakeMember(101, [mr.EX_MEMBER_ROLE, mr.HONORED_FISH_ROLE, 'Merman', 'Europe'], guild)

    # Rejoins: Honored Fish -> starts as Manatee. Registration strips the
    # honorific role and writes it to the ledger instead of a snapshot flag.
    starting_rank, recorded = register(db, brenzoned, 'brenzoned', UUID_A)
    assert starting_rank == 'Manatee'
    assert recorded == [hon.HONORED_FISH]
    assert brenzoned.role_names == {
        mr.MEMBER_ROLE, mr.TAQ_TAG_ROLE, 'Manatee', '★Coastal Waters',
        *mr.REGISTRATION_HEADER_ROLES, 'Merman', 'Europe',
    }
    assert hon.active_honorifics(db.cursor, uuid=UUID_A) == (True, False)
    assert link_row(db, 101) == 'Manatee'

    # Later removed by an HR (initiator Narwhal, target Manatee).
    ctx = remove_via_slash_command(brenzoned, FakeMember(NARWHAL_ID, [], guild), db, monkeypatch)

    # TAQ-51: Honored Fish is back; every guild role is gone; personal roles kept.
    assert brenzoned.role_names == {mr.EX_MEMBER_ROLE, mr.HONORED_FISH_ROLE, 'Merman', 'Europe'}
    assert brenzoned.nick_edits == ['']
    assert 'Honored Fish' in ctx.last_embed().description
    # TAQ-76: identity stays, rank goes, the stint remembers the rank.
    assert link_row(db, 101) is None
    assert roster.latest_stint(db.cursor, UUID_A)['rank_at_leave'] == 'Manatee'
    assert hon.active_honorifics(db.cursor, uuid=UUID_A) == (True, False)


# ── worked example 2: Retired Chief, restored independently ──────────────

def test_retired_chief_round_trip_via_user_command(temp_db, monkeypatch):
    db = temp_db
    guild = FakeGuild()
    seed_link(db, NARWHAL_ID, UUID_C, 'Narwhal1', 'Narwhal')
    chief = FakeMember(102, [mr.RETIRED_CHIEF_ROLE, 'Tortoise - Community'], guild)

    starting_rank, recorded = register(db, chief, 'chief', UUID_B)
    assert starting_rank == 'Piranha'
    assert recorded == [hon.RETIRED_CHIEF]
    assert chief.role_names == {
        mr.MEMBER_ROLE, mr.TAQ_TAG_ROLE, 'Piranha', '★★ Azure Ocean',
        *mr.REGISTRATION_HEADER_ROLES, 'Tortoise - Community',
    }

    interaction = remove_via_user_command(chief, FakeMember(NARWHAL_ID, [], guild), db, monkeypatch)

    # Retired Chief restored, and it brings Honored Fish with it (TAQ-67).
    assert chief.role_names == {mr.EX_MEMBER_ROLE, mr.RETIRED_CHIEF_ROLE,
                                mr.HONORED_FISH_ROLE, 'Tortoise - Community'}
    assert chief.nick_edits == ['']
    assert 'Retired Chief' in interaction.last_embed().description
    assert 'Honored Fish' in interaction.last_embed().description
    assert link_row(db, 102) is None


# ── worked example 3: full guild stack stripped via the promotion queue ──

def test_full_stack_veteran_removed_via_promotion_queue(temp_db, monkeypatch):
    db = temp_db
    guild = FakeGuild()
    seed_member(db, 103, UUID_A, 'veteran', 'Narwhal')
    hon.grant(db.cursor, uuid=UUID_A, ign='veteran', honorific=hon.HONORED_FISH, granted_by=0)
    veteran = FakeMember(103, [
        mr.MEMBER_ROLE, mr.TAQ_TAG_ROLE,
        'Narwhal', '★★★★Abyss Waters', 'Dolphin', '★★★Dark Sea',
        mr.RANKS_HEADER, mr.PROFESSIONS_HEADER, mr.COSMETIC_HEADER,
        mr.CONTRIBUTION_HEADER, mr.MILITARY_HEADER,
        '⚬ Shelf', '⚬ ⚬ Slope', 'War Trainer', 'Territory Munching',
        'HQ Team', 'DPS', 'Tank', 'Healer', 'EcoFish', 'Soloer',
        'Event Team Manager', 'Shell Manager',
        'Noobwhal - #1 XP contributed', '#1 - Shells',
        'Raidfish (Graid Event Top #5)',
        'Sea Pickle - Booster', 'Merman',       # non-guild: must survive
    ], guild)

    remove_via_promotion_queue(veteran, db, monkeypatch)

    assert veteran.role_names == {mr.EX_MEMBER_ROLE, mr.HONORED_FISH_ROLE, 'Sea Pickle - Booster', 'Merman'}
    assert veteran.nick_edits == ['']
    # The queue used to DELETE the discord_links row; it is identity and stays.
    db.cursor.execute("SELECT uuid::text, rank FROM discord_links WHERE discord_id = 103")
    assert db.cursor.fetchone() == (UUID_A, None)
    # The stint is still open (they have not left in-game yet) but knows the rank.
    stint = roster.latest_stint(db.cursor, UUID_A)
    assert stint['left_at'] is None and stint['rank_at_leave'] == 'Narwhal'


# ── worked example 4: website removal with an honorific attached ─────────

def test_promotion_queue_remove_with_honorific_grant(temp_db, monkeypatch):
    db = temp_db
    guild = FakeGuild()
    seed_member(db, 108, UUID_B, 'leaver', 'Angler')
    member = FakeMember(108, [mr.MEMBER_ROLE, 'Angler', mr.RANKS_HEADER], guild)

    remove_via_promotion_queue(member, db, monkeypatch, grant=hon.HONORED_FISH)

    assert member.role_names == {mr.EX_MEMBER_ROLE, mr.HONORED_FISH_ROLE}
    rows = hon.history(db.cursor, uuid=UUID_B)
    assert [(r['honorific'], r['granted_by'], r['revoked_at']) for r in rows] == [(hon.HONORED_FISH, NARWHAL_ID, None)]
    assert 'website removal queue #77' in rows[0]['note']


# ── edge cases ───────────────────────────────────────────────────────────

def test_promotion_queue_remove_without_link_row(temp_db, monkeypatch):
    db = temp_db
    guild = FakeGuild()
    member = FakeMember(104, [mr.MEMBER_ROLE, 'Starfish', '☆Reef', mr.RANKS_HEADER, 'Giveaways'], guild)

    remove_via_promotion_queue(member, db, monkeypatch)

    # No record -> no restore, just Ex-Member; non-guild role kept.
    assert member.role_names == {mr.EX_MEMBER_ROLE, 'Giveaways'}
    assert link_row(db, 104) == 'NO ROW'


def test_promotion_queue_remove_is_idempotent(temp_db, monkeypatch):
    db = temp_db
    guild = FakeGuild()
    seed_member(db, 105, UUID_A, 'twice', 'Manatee')
    hon.grant(db.cursor, uuid=UUID_A, ign='twice', honorific=hon.HONORED_FISH, granted_by=0)
    member = FakeMember(105, [mr.MEMBER_ROLE, 'Manatee'], guild)

    remove_via_promotion_queue(member, db, monkeypatch)
    first = set(member.role_names)
    remove_via_promotion_queue(member, db, monkeypatch)
    assert member.role_names == first == {mr.EX_MEMBER_ROLE, mr.HONORED_FISH_ROLE}
    assert link_row(db, 105) is None


def test_discord_only_honorific_restored_on_removal(temp_db, monkeypatch):
    # A holder recorded by Discord id only (never linked at backfill time)
    # links later; the grant follows them through registration and removal.
    db = temp_db
    guild = FakeGuild()
    seed_link(db, NARWHAL_ID, UUID_C, 'Narwhal1', 'Narwhal')
    hon.grant(db.cursor, discord_id=109, ign='oldtimer', honorific=hon.HONORED_FISH, granted_by=0,
              note='backfill: role held on 2026-09-14')
    oldtimer = FakeMember(109, [mr.EX_MEMBER_ROLE, mr.HONORED_FISH_ROLE], guild)

    register(db, oldtimer, 'oldtimer', UUID_B)
    db.cursor.execute("SELECT uuid::text FROM member_honorifics WHERE discord_id = 109 AND revoked_at IS NULL")
    assert db.cursor.fetchall() == [(UUID_B,)]  # uuid attached, no duplicate row

    remove_via_slash_command(oldtimer, FakeMember(NARWHAL_ID, [], guild), db, monkeypatch)
    assert oldtimer.role_names == {mr.EX_MEMBER_ROLE, mr.HONORED_FISH_ROLE}


# ── permission rule, shared by every surface ─────────────────────────────

def test_slash_removal_of_unlinked_target_still_strips(temp_db, monkeypatch):
    db = temp_db
    guild = FakeGuild()
    seed_link(db, NARWHAL_ID, UUID_C, 'Narwhal1', 'Narwhal')
    member = FakeMember(106, [mr.MEMBER_ROLE, mr.TAQ_TAG_ROLE, 'Starfish', '☆Reef', 'DPS', 'Europe'], guild)

    remove_via_slash_command(member, FakeMember(NARWHAL_ID, [], guild), db, monkeypatch)

    assert member.role_names == {mr.EX_MEMBER_ROLE, 'Europe'}


def test_slash_removal_blocked_for_equal_rank(temp_db, monkeypatch):
    db = temp_db
    guild = FakeGuild()
    seed_link(db, NARWHAL_ID, UUID_C, 'Narwhal1', 'Narwhal')
    seed_member(db, 107, UUID_A, 'peer', 'Narwhal')
    member = FakeMember(107, [mr.MEMBER_ROLE, 'Narwhal'], guild)

    ctx = remove_via_slash_command(member, FakeMember(NARWHAL_ID, [], guild), db, monkeypatch)

    assert member.role_names == {mr.MEMBER_ROLE, 'Narwhal'}
    assert 'Permission denied' in ctx.last_embed().title
    assert link_row(db, 107) == 'Narwhal'


def test_user_command_blocked_without_initiator_link(temp_db, monkeypatch):
    db = temp_db
    guild = FakeGuild()
    member = FakeMember(110, [mr.MEMBER_ROLE, 'Starfish'], guild)

    interaction = remove_via_user_command(member, FakeMember(999, [], guild), db, monkeypatch)

    assert member.role_names == {mr.MEMBER_ROLE, 'Starfish'}
    assert 'linked account' in interaction.last_embed().description


def test_check_reset_permission_matrix():
    from Helpers.member_removal import check_reset_permission
    assert check_reset_permission(None, None) is not None
    assert check_reset_permission('WeaponMerchant', None) is not None
    assert check_reset_permission('Narwhal', None) is None            # unlinked target
    assert check_reset_permission('Narwhal', (None,)) is None         # linked, no rank
    assert check_reset_permission('Narwhal', ('Manatee',)) is None
    assert check_reset_permission('Narwhal', ('Narwhal',)) is not None
    assert check_reset_permission('Hammerhead', ('Dolphin',)) is not None


# ── leave-message buttons ────────────────────────────────────────────────

class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content)


class FakeMessage:
    def __init__(self, message_id):
        import discord
        self.id = message_id
        self.embeds = [discord.Embed(title='Guild Member Left')]
        self.edits = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


class FakeButtonInteraction:
    def __init__(self, presser, guild, message_id):
        self.user = presser
        self.guild = guild
        self.message = FakeMessage(message_id)
        self.followup = FakeFollowup()
        self.client = None
        self.response = self

    async def defer(self, ephemeral=False):
        pass


def press(db, monkeypatch, presser, guild, message_id, grant):
    import Helpers.leave_prompts as lp_mod
    patch_all_db(monkeypatch, db)
    interaction = FakeButtonInteraction(presser, guild, message_id)

    async def go():
        view = lp_mod.LeavePromptView()   # discord.ui.View needs a running loop
        await view._handle(interaction, grant=grant)

    run(go())
    return interaction


def seed_prompt(db, message_id, uuid, ign, discord_id, last_rank):
    db.cursor.execute(
        "INSERT INTO member_leave_prompts (message_id, uuid, ign, discord_id, last_rank) VALUES (%s, %s::uuid, %s, %s, %s)",
        (message_id, uuid, ign, discord_id, last_rank),
    )


def test_leave_button_grant_when_member_already_left_discord(temp_db, monkeypatch):
    db = temp_db
    guild = FakeGuild()
    seed_link(db, NARWHAL_ID, UUID_C, 'Narwhal1', 'Narwhal')
    seed_link(db, 111, UUID_A, 'ghost', 'Piranha')      # left Discord: not in guild._members
    seed_prompt(db, 5001, UUID_A, 'ghost', 111, 'Piranha')

    interaction = press(db, monkeypatch, FakeMember(NARWHAL_ID, [], guild), guild, 5001, hon.HONORED_FISH)

    # The ticket's central case: nothing to reset, but the decision is kept.
    assert hon.active_honorifics(db.cursor, uuid=UUID_A) == (True, False)
    db.cursor.execute("SELECT resolved_by, resolution FROM member_leave_prompts WHERE message_id = 5001")
    assert db.cursor.fetchone() == (NARWHAL_ID, hon.HONORED_FISH)
    assert 'roles apply on rejoin' in interaction.followup.sent[-1]
    assert interaction.message.edits and interaction.message.edits[0]['view'].children[0].disabled


def test_leave_button_reset_removes_and_resolves(temp_db, monkeypatch):
    db = temp_db
    guild = FakeGuild()
    seed_link(db, NARWHAL_ID, UUID_C, 'Narwhal1', 'Narwhal')
    seed_member(db, 112, UUID_B, 'kicked', 'Angler')
    kicked = guild.add_member(FakeMember(112, [mr.MEMBER_ROLE, 'Angler', mr.RANKS_HEADER, 'Merman'], guild))
    seed_prompt(db, 5002, UUID_B, 'kicked', 112, 'Angler')

    press(db, monkeypatch, FakeMember(NARWHAL_ID, [], guild), guild, 5002, None)

    assert kicked.role_names == {mr.EX_MEMBER_ROLE, 'Merman'}
    assert link_row(db, 112) is None
    db.cursor.execute("SELECT resolution FROM member_leave_prompts WHERE message_id = 5002")
    assert db.cursor.fetchone() == ('reset',)


def test_leave_button_noop_when_website_removed_first(temp_db, monkeypatch):
    db = temp_db
    guild = FakeGuild()
    seed_link(db, NARWHAL_ID, UUID_C, 'Narwhal1', 'Narwhal')
    seed_link(db, 113, UUID_A, 'done', None)
    already = guild.add_member(FakeMember(113, [mr.EX_MEMBER_ROLE, 'Europe'], guild))
    seed_prompt(db, 5003, UUID_A, 'done', 113, 'Piranha')

    interaction = press(db, monkeypatch, FakeMember(NARWHAL_ID, [], guild), guild, 5003, None)

    assert already.role_names == {mr.EX_MEMBER_ROLE, 'Europe'}
    db.cursor.execute("SELECT resolution FROM member_leave_prompts WHERE message_id = 5003")
    assert db.cursor.fetchone() == ('noop',)
    assert 'already an ex-member' in interaction.followup.sent[-1]


def test_leave_button_retired_chief_needs_narwhal(temp_db, monkeypatch):
    db = temp_db
    guild = FakeGuild()
    seed_link(db, 2, UUID_C, 'Hammer1', 'Hammerhead')
    seed_member(db, 114, UUID_A, 'target', 'Piranha')
    guild.add_member(FakeMember(114, [mr.MEMBER_ROLE, 'Piranha'], guild))
    seed_prompt(db, 5004, UUID_A, 'target', 114, 'Piranha')

    interaction = press(db, monkeypatch, FakeMember(2, [], guild), guild, 5004, hon.RETIRED_CHIEF)

    assert 'Narwhal or higher' in interaction.followup.sent[-1]
    assert hon.active_honorifics(db.cursor, uuid=UUID_A) == (False, False)
    db.cursor.execute("SELECT resolved_at FROM member_leave_prompts WHERE message_id = 5004")
    assert db.cursor.fetchone() == (None,)


def test_leave_button_second_press_reports_first_resolver(temp_db, monkeypatch):
    db = temp_db
    guild = FakeGuild()
    seed_link(db, NARWHAL_ID, UUID_C, 'Narwhal1', 'Narwhal')
    seed_link(db, 115, UUID_A, 'gone', 'Piranha')
    seed_prompt(db, 5005, UUID_A, 'gone', 115, 'Piranha')

    press(db, monkeypatch, FakeMember(NARWHAL_ID, [], guild), guild, 5005, None)
    second = press(db, monkeypatch, FakeMember(NARWHAL_ID, [], guild), guild, 5005, hon.HONORED_FISH)

    assert 'Already handled' in second.followup.sent[-1]
    assert hon.active_honorifics(db.cursor, uuid=UUID_A) == (False, False)
