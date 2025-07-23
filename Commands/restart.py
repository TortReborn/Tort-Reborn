import json
import os
import sys
import time

from discord.ext import commands
from discord import slash_command

from Helpers.variables import guilds


class Restart(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(guild_ids=[guilds[1]])
    async def restart(self, message):
        crash = {"type": 'Restart', "value": str(message.user) + ' ran the restart command', "timestamp": int(time.time())}
        with open('last_online.json', 'w') as f:
            json.dump(crash, f)
        await message.respond('Restarting...', ephemeral=True)
        os.execv(sys.executable, ['python'] + sys.argv)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Restart(client))
