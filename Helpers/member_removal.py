"""The one way to turn a member into an ex-member (TAQ-76).

Before this module, /reset_roles, the "Member | Remove" user command,
/stale-roles and the website promotion queue each carried their own copy of
the removal steps, and they disagreed about the discord_links row: the queue
deleted it, the others left it untouched with the rank intact. Every surface
now calls ``remove_member``; the permission checks stay with the callers
because they differ per surface.

What removal does:

1. Optionally records an honorific grant (Honored Fish / Retired Chief) in
   the ledger -- before touching Discord, so a role failure never loses the
   decision.
2. Reads the honorifics on record and plans the role changes through
   Helpers/member_roles.removal_role_names.
3. Applies the role changes and clears the nickname.
4. Clears the Discord rank on discord_links and stamps rank_at_leave on the
   player's latest stint. The identity row itself is left alone: who the
   account is does not change because they left.

It does not close the membership stint -- that is the in-game roster's job
(Helpers/roster.sync_roster), and a website removal usually runs before the
in-game kick.
"""

import asyncio
from dataclasses import dataclass, field

from Helpers import honorifics as hon
from Helpers import roster
from Helpers.database import DB
from Helpers.member_roles import EX_MEMBER_ROLE, removal_role_names, resolve_roles
from Helpers.variables import discord_ranks


@dataclass
class RemovalPlan:
    discord_id: int
    uuid: str | None
    ign: str | None
    rank: str | None
    honored_fish: bool
    retired_chief: bool
    to_add: list = field(default_factory=list)
    to_remove: list = field(default_factory=list)
    granted: str | None = None


@dataclass
class RemovalResult:
    plan: RemovalPlan
    roles_added: list
    roles_removed: list
    already_ex_member: bool

    @property
    def restored(self):
        """Honorific role names handed back, for the confirmation embed."""
        return [r.name for r in self.roles_added if r.name != EX_MEMBER_ROLE]


def load_identity(cursor, discord_id):
    """(uuid, ign, rank) for a Discord account, or None when unlinked."""
    cursor.execute(
        "SELECT uuid::text, ign, rank FROM discord_links WHERE discord_id = %s",
        (int(discord_id),),
    )
    row = cursor.fetchone()
    return {'uuid': row[0], 'ign': row[1], 'rank': row[2]} if row else None


def plan_removal(cursor, discord_id, *, grant=None, actor_id=None, ign_hint=None, note=None):
    """Record the optional grant, read the honorifics on record and decide
    the role changes. Blocking; the caller commits."""
    identity = load_identity(cursor, discord_id) or {}
    uuid = identity.get('uuid')
    ign = identity.get('ign') or ign_hint
    granted = None
    if grant:
        if not uuid:
            raise ValueError("cannot grant an honorific to an account with no Minecraft link")
        hon.grant(cursor, uuid=uuid, ign=ign or '?', honorific=grant, granted_by=actor_id or 0,
                  discord_id=int(discord_id), note=note)
        granted = grant

    if uuid:
        hf, rc = hon.active_honorifics(cursor, uuid=uuid)
    else:
        hf, rc = hon.active_honorifics(cursor, discord_id=discord_id)

    to_add, to_remove = removal_role_names(hf, rc)
    return RemovalPlan(int(discord_id), uuid, ign, identity.get('rank'), hf, rc, to_add, to_remove, granted)


def record_removal(cursor, plan):
    """Clear the rank and remember it on the stint. Blocking; caller commits."""
    if plan.uuid and plan.rank:
        roster.stamp_rank_at_leave(cursor, plan.uuid, plan.rank)
    cursor.execute(
        "UPDATE discord_links SET rank = NULL WHERE discord_id = %s AND rank IS NOT NULL",
        (plan.discord_id,),
    )
    return cursor.rowcount > 0


def _with_db(fn, *args, **kwargs):
    db = DB()
    db.connect()
    try:
        result = fn(db.cursor, *args, **kwargs)
        db.connection.commit()
        return result
    finally:
        db.close()


async def remove_member(member, guild, *, actor_id, reason, grant=None, note=None):
    """Remove ``member`` (a discord.Member) from the guild's Discord side.

    ``grant`` is None, 'honored_fish' or 'retired_chief'. Returns a
    RemovalResult; raises whatever discord.py raises if the role edits fail
    (after the grant, if any, has been recorded).
    """
    plan = await asyncio.to_thread(
        _with_db, plan_removal, member.id,
        grant=grant, actor_id=actor_id, ign_hint=getattr(member, 'display_name', None), note=note,
    )

    all_roles = guild.roles
    held = {r.name for r in member.roles}
    already_ex_member = EX_MEMBER_ROLE in held and not any(n in held for n in plan.to_remove)

    roles_to_add = resolve_roles(all_roles, plan.to_add, member=member, present=False)
    roles_to_remove = resolve_roles(all_roles, plan.to_remove, member=member, present=True)
    if roles_to_remove:
        await member.remove_roles(*roles_to_remove, reason=reason, atomic=True)
    if roles_to_add:
        await member.add_roles(*roles_to_add, reason=reason, atomic=True)
    try:
        await member.edit(nick='')
    except Exception:
        pass

    await asyncio.to_thread(_with_db, record_removal, plan)
    return RemovalResult(plan, roles_to_add, roles_to_remove, already_ex_member)


def record_grant_only(cursor, *, discord_id=None, uuid=None, ign, grant, actor_id, note=None):
    """The leave-button case where the member is no longer in Discord: write
    the ledger row and nothing else. Returns the honorific key recorded."""
    if uuid is None and discord_id is not None:
        identity = load_identity(cursor, discord_id)
        uuid = identity['uuid'] if identity else None
        ign = (identity or {}).get('ign') or ign
    if not uuid:
        raise ValueError("no Minecraft account to record the honorific against")
    hon.grant(cursor, uuid=uuid, ign=ign, honorific=grant, granted_by=actor_id,
              discord_id=discord_id, note=note)
    return grant


def check_reset_permission(initiator_rank, target_row):
    """None when allowed, else the (title, description) of the refusal.

    Shared by the slash command, the user command and the leave-message
    buttons so the rule lives in one place: the initiator needs a linked
    account with a recognised rank, and may only reset members ranked
    strictly below them. A target with no rank on record is always allowed.
    """
    if initiator_rank is None:
        return (':no_entry: Oops!',
                'You do not have a linked account.\nPlease use the `/manage link` command first.')
    if initiator_rank not in discord_ranks:
        return (':no_entry: Error',
                f'Your rank `{initiator_rank}` is not recognized. Please contact an admin.')
    if target_row and target_row[0]:
        target_rank = target_row[0]
        if target_rank not in discord_ranks:
            return (':no_entry: Error',
                    f'Target\'s rank `{target_rank}` is not recognized. Please contact an admin.')
        ranks = list(discord_ranks)
        if ranks.index(target_rank) >= ranks.index(initiator_rank):
            return (':no_entry: Permission denied',
                    'You can only reset roles for members with a lower rank than your own.')
    return None
