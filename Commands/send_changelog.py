import discord
from discord.ext import commands
from discord.commands import slash_command
import random
import json
import re

from Helpers.variables import changelog_channel, guilds


class SendChangelog(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(description='Sends changelog', guild_ids=[guilds[1]])
    async def send_changelog(self, message):
        with open('version.txt', 'r') as f:
            version = f.readline()
            f.close()

        with open('changelog.json', 'r', encoding='utf-8') as f:
            changelog = json.load(f)
            f.close()

        new_version = list(changelog.keys())[0]

        if version != new_version:
            embed = discord.Embed(title='📜 Changelog', description='', color=0x36ff3c)

            for change in changelog[new_version]:
                embed.add_field(name=change['name'], value=change['description'], inline=False)

            ch = self.client.get_channel(changelog_channel)
            await ch.send(embed=embed)
            await message.respond("Sent.")

            with open('version.txt', 'w') as f:
                f.write(new_version)
                f.close()
        else:
            await message.respond("No new changes.")

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(SendChangelog(client))