import time
from io import BytesIO

from discord.ext import commands
from discord.commands import slash_command
import discord

from Helpers.functions import generate_rank_badge
from Helpers.variables import guilds


class RankBadge(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(guild_ids=[guilds[1]])
    async def rank_badge(self, message, text: discord.Option(str, require=True),
                         colour: discord.Option(str, require=True),
                         scale: discord.Option(int, require=False, default=4, min_value=1, max_value=6)):
        img = generate_rank_badge(text, colour, scale)

        if not img:
            await message.respond('Please specify valid colour (Format: **#rrggbb**)')
            return

        with BytesIO() as file:
            img.save(file, format="PNG")
            file.seek(0)
            t = int(time.time())
            rankbadge = discord.File(file, filename=f"rank{t}.png")

        await message.respond(file=rankbadge)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(RankBadge(client))
