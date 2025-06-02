import time
from io import BytesIO

from discord.ext import commands
from discord.commands import slash_command
import discord

from Helpers.functions import create_progress_bar
from Helpers.variables import guilds


class ProgressBar(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(guilds=[guilds[1]])
    async def progress_bar(self, message, width: discord.Option(int, require=True),
                           colour: discord.Option(str, require=True),
                           percentage: discord.Option(int, min_value=0, max_value=100, require=True)):
        img = create_progress_bar(width, percentage, colour)

        if not img:
            await message.respond('Please specify valid width and percentage.')
            return

        with BytesIO() as file:
            img.save(file, format="PNG")
            file.seek(0)
            t = int(time.time())
            bar = discord.File(file, filename=f"bar{t}.png")

        await message.respond(file=bar)

    @commands.Cog.listener()
    async def on_ready(self):
        print('ProgressBar command loaded')


def setup(client):
    client.add_cog(ProgressBar(client))
