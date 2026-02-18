import time
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
from discord.ext import commands
from discord.commands import slash_command
import discord

from Helpers.functions import generate_rank_badge, addLine
from Helpers.variables import EXEC_GUILD_IDS


class RenderText(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(guild_ids=EXEC_GUILD_IDS)
    async def render_text(self, message, text: discord.Option(str, require=True)):

        img = Image.new('RGBA', (1000, 20), (255, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.fontmode = '1'

        gameFont = ImageFont.truetype('images/profile/game.ttf', 19)

        r_text = addLine(text, draw, gameFont, 0, 0)
        img = img.crop((0, 0, r_text + 2, 20))

        with BytesIO() as file:
            img.save(file, format="PNG")
            file.seek(0)
            t = int(time.time())
            rendered_text = discord.File(file, filename=f"text{t}.png")

        await message.respond(file=rendered_text)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(RenderText(client))
