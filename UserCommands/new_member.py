import discord
from discord.ext import commands
from discord.commands import user_command

from Helpers.classes import NewMember
from Helpers.variables import guilds


class GiveMemberRoles(commands.Cog):
    def __init__(self, client):
        self.client = client

    @user_command(
        name='Member | Register',
        default_member_permissions=discord.Permissions(manage_roles=True),
        guild_ids=guilds
    )
    async def give_member_roles(self, interaction: discord.Interaction, user: discord.Member):
        if interaction.user.guild_permissions.manage_roles:
            modal = NewMember(title="New Member", user=user)
            await interaction.response.send_modal(modal)
        else:
            await interaction.response.send_message(
                'You are missing Manage Roles permission(s) to run this command.',
                ephemeral=True
            )


    @commands.Cog.listener()
    async def on_ready(self):
        print('NewMember user command loaded')


def setup(client):
    client.add_cog(GiveMemberRoles(client))
