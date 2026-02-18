import os
import sys
import time

import discord
from discord.ext import commands
from discord import slash_command

from Helpers.database import set_last_online
from Helpers.variables import EXEC_GUILD_IDS


class Restart(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(
        guild_ids=EXEC_GUILD_IDS,
        default_member_permissions=discord.Permissions(administrator=True)
    )
    async def restart(self, message):
        crash = {"type": 'Restart', "value": str(message.user) + ' ran the restart command', "timestamp": int(time.time())}
        set_last_online(crash)
        await message.respond('Restarting...', ephemeral=True)
        os.execv(sys.executable, ['python'] + sys.argv)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Restart(client))
