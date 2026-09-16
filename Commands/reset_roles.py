import asyncio

import discord
from discord.ext import commands
from discord.commands import slash_command
from discord import default_permissions

from Helpers.database import DB
from Helpers.member_removal import check_reset_permission, remove_member
from Helpers.variables import HOME_GUILD_IDS


def _lookup_ranks(initiator_id, target_id):
    """(initiator_rank, target_row) -- target_row is (rank,) or None. Blocking."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute('SELECT rank FROM discord_links WHERE discord_id = %s', (initiator_id,))
        initiator_row = db.cursor.fetchone()
        db.cursor.execute('SELECT rank FROM discord_links WHERE discord_id = %s', (target_id,))
        target_row = db.cursor.fetchone()
        return (initiator_row[0] if initiator_row else None), target_row
    finally:
        db.close()


def removal_embed(user_id, result):
    description = f'Roles were reset for <@{user_id}>'
    if result.restored:
        description += '\nRestored: ' + ', '.join(f'`{r}`' for r in result.restored)
    return discord.Embed(title=':white_check_mark: Roles reset', description=description, color=0x3ed63e)


class ResetRolesCommand(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(guild_ids=HOME_GUILD_IDS, description="HR: Reset a user's roles")
    @default_permissions(manage_roles=True)
    async def reset_roles(self, message, user: discord.Member):
        if not message.interaction.user.guild_permissions.manage_roles:
            await message.respond('You are missing Manage Roles permission(s) to run this command.', ephemeral=True)
            return
        # Discord resolves a user id that has left the server to a bare User,
        # which has no roles to reset.
        if not hasattr(user, 'roles'):
            await message.respond(f'<@{user.id}> is not in this server.', ephemeral=True)
            return

        await message.defer(ephemeral=True)
        initiator_rank, target_row = await asyncio.to_thread(
            _lookup_ranks, message.interaction.user.id, user.id
        )
        refusal = check_reset_permission(initiator_rank, target_row)
        if refusal:
            title, description = refusal
            await message.respond(embed=discord.Embed(title=title, description=description, color=0xe33232),
                                  ephemeral=True)
            return

        result = await remove_member(
            user, message.interaction.guild,
            actor_id=message.interaction.user.id,
            reason=f'Roles reset (ran by {message.author.name})',
        )
        await message.respond(embed=removal_embed(user.id, result))

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(ResetRolesCommand(client))
