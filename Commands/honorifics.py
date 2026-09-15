"""/honorifics -- look up, grant and revoke Honored Fish / Retired Chief (TAQ-76).

The ledger (member_honorifics) is the source of truth; the Discord roles
follow it. ``lookup`` answers the ticket's "what were they before?" for
anyone, including people who have left Discord. ``grant`` and ``revoke``
cover honors decided outside a leave event.
"""

import asyncio

import discord
from discord import ApplicationContext, SlashCommandGroup
from discord.ext import commands

from Helpers import honorifics as hon
from Helpers.database import DB
from Helpers.functions import getPlayerUUID
from Helpers.member_roles import removal_role_names, resolve_roles, HONORED_FISH_ROLE, RETIRED_CHIEF_ROLE
from Helpers.variables import HOME_GUILD_IDS, discord_ranks

CHOICES = {hon.LABELS[hon.HONORED_FISH]: hon.HONORED_FISH, hon.LABELS[hon.RETIRED_CHIEF]: hon.RETIRED_CHIEF}


def _resolve_target(discord_id=None, ign=None, display_name=None):
    """Blocking. Returns (uuid, ign, discord_id) or None. The bot's link table
    is tried first (it is authoritative for current names), then Mojang. A
    Discord account with no link resolves to (None, display_name, id) so
    unlinked holders can still be recorded."""
    db = DB()
    db.connect()
    try:
        if discord_id is not None:
            db.cursor.execute("SELECT uuid::text, ign FROM discord_links WHERE discord_id = %s", (int(discord_id),))
            row = db.cursor.fetchone()
            return (row[0], row[1], int(discord_id)) if row else (None, display_name or str(discord_id), int(discord_id))
        if ign:
            db.cursor.execute("SELECT uuid::text, ign, discord_id FROM discord_links WHERE LOWER(ign) = LOWER(%s)", (ign,))
            row = db.cursor.fetchone()
            if row:
                return row[0], row[1], row[2]
    finally:
        db.close()
    if ign:
        player = getPlayerUUID(ign)
        if player:
            return player[1], player[0], None
    return None


def _actor_rank(discord_id):
    db = DB()
    db.connect()
    try:
        db.cursor.execute("SELECT rank FROM discord_links WHERE discord_id = %s", (int(discord_id),))
        row = db.cursor.fetchone()
        return row[0] if row else None
    finally:
        db.close()


def _history(uuid, discord_id):
    db = DB()
    db.connect()
    try:
        return hon.history(db.cursor, uuid=uuid, discord_id=discord_id)
    finally:
        db.close()


def _grant(uuid, ign, discord_id, honorific, actor_id, note):
    db = DB()
    db.connect()
    try:
        _, created = hon.grant(db.cursor, uuid=uuid, ign=ign, honorific=honorific, granted_by=actor_id,
                               discord_id=discord_id, note=note)
        db.connection.commit()
        return created
    finally:
        db.close()


def _revoke(uuid, discord_id, honorific, actor_id, note):
    db = DB()
    db.connect()
    try:
        closed = hon.revoke(db.cursor, uuid=uuid, discord_id=discord_id, honorific=honorific,
                            revoked_by=actor_id, note=note)
        db.connection.commit()
        return closed
    finally:
        db.close()


def can_manage(actor_rank, honorific):
    """Honored Fish: anyone with a linked rank (manage_roles is checked by
    Discord). Retired Chief: Narwhal or higher."""
    if actor_rank not in discord_ranks:
        return False
    if honorific == hon.RETIRED_CHIEF:
        return list(discord_ranks).index(actor_rank) >= list(discord_ranks).index('Narwhal')
    return True


class Honorifics(commands.Cog):
    def __init__(self, client):
        self.client = client

    group = SlashCommandGroup(
        'honorifics', 'HR: Honored Fish / Retired Chief records',
        guild_ids=HOME_GUILD_IDS,
        default_member_permissions=discord.Permissions(manage_roles=True),
    )

    @group.command(name='lookup', description='HR: What honorifics does a player have on record?')
    async def lookup(self, ctx: ApplicationContext, user: discord.Member = None, ign: str = None):
        if not ctx.user.guild_permissions.manage_roles:
            await ctx.respond('You are missing Manage Roles permission(s) to run this command.', ephemeral=True)
            return
        await ctx.defer(ephemeral=True)
        target = await asyncio.to_thread(_resolve_target, user.id if user else None, ign, user.display_name if user else None)
        if not target:
            await ctx.followup.send(':no_entry: Could not find that player (no link and no Mojang match).', ephemeral=True)
            return
        uuid, name, discord_id = target
        rows = await asyncio.to_thread(_history, uuid, discord_id)
        active = [r for r in rows if r['revoked_at'] is None]
        past = [r for r in rows if r['revoked_at'] is not None]

        embed = discord.Embed(title=f'Honorifics — {discord.utils.escape_markdown(name)}', color=0x94C1FF)
        embed.add_field(name='Discord', value=f'<@{discord_id}>' if discord_id else 'not linked', inline=True)
        embed.add_field(name='UUID', value=f'`{uuid}`' if uuid else 'not linked', inline=True)
        if active:
            embed.add_field(name='On record', value='\n'.join(
                f"**{hon.LABELS[r['honorific']]}** — since {r['granted_at']:%Y-%m-%d}"
                + (f" by <@{r['granted_by']}>" if r['granted_by'] else '')
                + (f"\n-# {r['note']}" if r['note'] else '')
                for r in active), inline=False)
        else:
            embed.add_field(name='On record', value='none', inline=False)
        if past:
            embed.add_field(name='Revoked', value='\n'.join(
                f"{hon.LABELS[r['honorific']]} — {r['granted_at']:%Y-%m-%d} to {r['revoked_at']:%Y-%m-%d}"
                + (f" by <@{r['revoked_by']}>" if r['revoked_by'] else '')
                for r in past)[:1024], inline=False)
        await ctx.followup.send(embed=embed, ephemeral=True)

    @group.command(name='grant', description='HR: Record an honorific (and give the role if they are here)')
    async def grant(self, ctx: ApplicationContext,
                    honorific: discord.Option(str, choices=list(CHOICES)),
                    user: discord.Member = None, ign: str = None, note: str = None):
        if not ctx.user.guild_permissions.manage_roles:
            await ctx.respond('You are missing Manage Roles permission(s) to run this command.', ephemeral=True)
            return
        await ctx.defer(ephemeral=True)
        key = CHOICES[honorific]
        actor_rank = await asyncio.to_thread(_actor_rank, ctx.user.id)
        if not can_manage(actor_rank, key):
            await ctx.followup.send(f':no_entry: {honorific} can only be granted by Narwhal or higher.' if key == hon.RETIRED_CHIEF
                                    else ':no_entry: Link your account first.', ephemeral=True)
            return
        target = await asyncio.to_thread(_resolve_target, user.id if user else None, ign, user.display_name if user else None)
        if not target:
            await ctx.followup.send(':no_entry: Could not find that player.', ephemeral=True)
            return
        uuid, name, discord_id = target
        created = await asyncio.to_thread(_grant, uuid, name, discord_id, key, ctx.user.id, note)

        applied = ''
        member = user or (ctx.guild.get_member(discord_id) if discord_id else None)
        if member is not None:
            to_add, _ = removal_role_names(key == hon.HONORED_FISH or key == hon.RETIRED_CHIEF, key == hon.RETIRED_CHIEF)
            roles = resolve_roles(ctx.guild.roles, [n for n in to_add if n in (HONORED_FISH_ROLE, RETIRED_CHIEF_ROLE)],
                                  member=member, present=False)
            if roles:
                try:
                    await member.add_roles(*roles, reason=f'Honorific granted by {ctx.user.name}')
                    applied = ' Role applied.'
                except (discord.Forbidden, discord.HTTPException):
                    applied = ' (Could not apply the role — do it manually.)'
        verb = 'Recorded' if created else 'Already on record:'
        await ctx.followup.send(f':white_check_mark: {verb} **{honorific}** for **{discord.utils.escape_markdown(name)}**.{applied}', ephemeral=True)

    @group.command(name='revoke', description='HR: Close an honorific on record (and remove the role)')
    async def revoke(self, ctx: ApplicationContext,
                     honorific: discord.Option(str, choices=list(CHOICES)),
                     user: discord.Member = None, ign: str = None, note: str = None):
        if not ctx.user.guild_permissions.manage_roles:
            await ctx.respond('You are missing Manage Roles permission(s) to run this command.', ephemeral=True)
            return
        await ctx.defer(ephemeral=True)
        key = CHOICES[honorific]
        actor_rank = await asyncio.to_thread(_actor_rank, ctx.user.id)
        if not can_manage(actor_rank, hon.RETIRED_CHIEF):  # revoking either needs Narwhal+
            await ctx.followup.send(':no_entry: Revoking an honorific needs Narwhal or higher.', ephemeral=True)
            return
        target = await asyncio.to_thread(_resolve_target, user.id if user else None, ign, user.display_name if user else None)
        if not target:
            await ctx.followup.send(':no_entry: Could not find that player.', ephemeral=True)
            return
        uuid, name, discord_id = target
        closed = await asyncio.to_thread(_revoke, uuid, discord_id, key, ctx.user.id, note)
        if not closed:
            await ctx.followup.send(f'Nothing to revoke: **{discord.utils.escape_markdown(name)}** has no open {honorific} record.', ephemeral=True)
            return
        member = user or (ctx.guild.get_member(discord_id) if discord_id else None)
        applied = ''
        if member is not None:
            names = [RETIRED_CHIEF_ROLE] if key == hon.RETIRED_CHIEF else [HONORED_FISH_ROLE]
            roles = resolve_roles(ctx.guild.roles, names, member=member, present=True)
            if roles:
                try:
                    await member.remove_roles(*roles, reason=f'Honorific revoked by {ctx.user.name}')
                    applied = ' Role removed.'
                except (discord.Forbidden, discord.HTTPException):
                    applied = ' (Could not remove the role — do it manually.)'
        await ctx.followup.send(f':white_check_mark: Revoked **{honorific}** for **{discord.utils.escape_markdown(name)}**.{applied}', ephemeral=True)


def setup(client):
    client.add_cog(Honorifics(client))
