import discord
from discord.ext import commands

from Helpers.variables import GENERAL_CHANNEL_ID, MEMBER_APP_CHANNEL_ID, RULES_CHANNEL_ID, TAQ_GUILD_ID
from Helpers.logger import log, INFO


class OnMemberJoin(commands.Cog):
    def __init__(self, client):
        self.client = client

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.guild.id != TAQ_GUILD_ID:
            return

        log(INFO, f'{member.name} joined {member.guild.name}', context='on_member_join')

        ch = self.client.get_channel(GENERAL_CHANNEL_ID)
        if not ch:
            return

        await ch.send(
            f"Welcome {member.mention}! If you want to apply, head to <#{MEMBER_APP_CHANNEL_ID}> and choose your application type. "
            f"Read <#{RULES_CHANNEL_ID}> for any questions regarding our rules or procedures and have a wonderful stay within The Aquarium!"
        )


def setup(client):
    client.add_cog(OnMemberJoin(client))
