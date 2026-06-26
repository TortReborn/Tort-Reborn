import discord
from discord.ext import commands

from Helpers.variables import GENERAL_CHANNEL_ID, RULES_CHANNEL_ID, TAQ_GUILD_ID
from Helpers.logger import log, INFO

WELCOME_COLOR = 0x94C1FF


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
            f"Welcome to TAq {member.mention}! <:TAq:744256840254226553>\n"
            f"If you want to apply, head to <#1476866917854609408> and choose your application type.\n"
            f"Read <#{RULES_CHANNEL_ID}> for any immediate questions or concerns (like ally raiding) and have a wonderful stay within The Aquarium! <:partytort:975138500150165594>"
        )


def setup(client):
    client.add_cog(OnMemberJoin(client))
