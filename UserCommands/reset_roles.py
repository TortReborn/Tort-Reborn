import asyncio

import discord
from discord.ext import commands
from discord.commands import user_command

from Commands.reset_roles import _lookup_ranks, removal_embed
from Helpers.member_removal import check_reset_permission, remove_member
from Helpers.variables import HOME_GUILD_IDS


class ResetRoles(commands.Cog):
    def __init__(self, client):
        self.client = client

    @user_command(
        name='Member | Remove',
        default_member_permissions=discord.Permissions(manage_roles=True),
        guild_ids=HOME_GUILD_IDS
    )
    async def reset_roles(self, interaction: discord.Interaction, user: discord.Member):
        # Ensure the invoker has the Manage Roles permission
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message(
                'You are missing Manage Roles permission(s) to run this command.',
                ephemeral=True
            )
            return

        await interaction.defer(ephemeral=True)
        initiator_rank, target_row = await asyncio.to_thread(_lookup_ranks, interaction.user.id, user.id)
        refusal = check_reset_permission(initiator_rank, target_row)
        if refusal:
            title, description = refusal
            await interaction.respond(embed=discord.Embed(title=title, description=description, color=0xe33232),
                                      ephemeral=True)
            return

        result = await remove_member(
            user, interaction.guild,
            actor_id=interaction.user.id,
            reason=f'Roles reset (ran by {interaction.user.name})',
        )
        await interaction.respond(embed=removal_embed(user.id, result))

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(ResetRoles(client))
