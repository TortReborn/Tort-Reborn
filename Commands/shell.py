import math
import time
from io import BytesIO

import discord
from PIL import Image, ImageDraw, ImageFont
from discord import SlashCommandGroup, ApplicationContext
from discord.ext import commands, pages

from Helpers.classes import PlaceTemplate, Page, Guild
from Helpers.database import DB
from Helpers.functions import addLine, expand_image, generate_rank_badge
from Helpers.logger import log, INFO
from Helpers.variables import HOME_GUILD_IDS

from Helpers.pagination import add_paginator_buttons


def darken_color(color, iterations):
    if color.startswith('#'):
        color = color[1:]

    # Convert the hexadecimal color to RGB
    red = int(color[0:2], 16)
    green = int(color[2:4], 16)
    blue = int(color[4:6], 16)

    step = 10  # Adjust this value to control the darkness increment

    for _ in range(iterations):
        red = max(0, red - step)
        green = max(0, green - step)
        blue = max(0, blue - step)

        updated_color = f"#{red:02x}{green:02x}{blue:02x}"

        log(INFO, f"Darkened color: {updated_color}", context="shell")

    return updated_color


class Shell(commands.Cog):
    def __init__(self, client):
        self.client = client

    shell_group = SlashCommandGroup('shell', 'Shells related commands', guild_ids=HOME_GUILD_IDS)

    @shell_group.command(description='Displays the top shell balances')
    async def baltop(self, message: ApplicationContext):
        await message.response.defer()
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                'SELECT shells.user, discord_links.uuid, shells.balance '
                'FROM shells JOIN discord_links ON shells.user = discord_links.discord_id;'
            )
            row_headers = [x[0] for x in db.cursor.description]
            rows = db.cursor.fetchall()
            data = Guild('The Aquarium').all_members
            playerdata = []
            shelldata = []
            bg1 = PlaceTemplate('images/profile/first.png')
            bg2 = PlaceTemplate('images/profile/second.png')
            bg3 = PlaceTemplate('images/profile/third.png')
            bg = PlaceTemplate('images/profile/other.png')
            shells_img = Image.open('images/profile/shells.png')
            shells_img.thumbnail((16, 16))
            gameFont = ImageFont.truetype('images/profile/game.ttf', 19)
            widest = 0
            book = []

            for result in rows:
                playerdata.append(dict(zip(row_headers, result)))
            for member in data:
                found = False
                for player in playerdata:
                    if member['uuid'] == player['uuid']:
                        found = True
                        shelldata.append({
                            'name': member['name'],
                            'rank': member['rank'],
                            'shells': player['balance']
                        })
                if not found:
                    shelldata.append({
                        'name': member['name'],
                        'rank': member['rank'],
                        'shells': 0
                    })

            shelldata.sort(key=lambda x: x['shells'], reverse=True)
            page_num = int(math.ceil(len(shelldata) / 10))
            i = 1

            # Row geometry, mirroring Commands/leaderboard.py: the bar is centered in the
            # canvas and every element is placed relative to SIDE_PAD (bar's left edge) or
            # BAR_R (right edge) so the two insets stay symmetric. The gutter is narrower
            # than the leaderboard's 30px because nothing overhangs the bar here -- widen
            # it if an icon is ever added outside the bar.
            BAR_W = 380
            SIDE_PAD = 15
            BAR_R = SIDE_PAD + BAR_W
            CANVAS_W = BAR_R + SIDE_PAD

            for page in range(page_num):
                img = Image.new('RGBA', (CANVAS_W, 0), color='#00000000')
                d = ImageDraw.Draw(img)
                d.fontmode = '1'
                page_playerdata = shelldata[(10 * page):(10 * page + 10)]

                for p, player in enumerate(page_playerdata):
                    img, d = expand_image(img, border=(0, 0, 0, 36), fill='#00000000')
                    bg_color = {1: bg1, 2: bg2, 3: bg3}.get(i, bg)
                    # start=True draws the mirrored left cap; without it the bar has a raw
                    # cut edge, which only looked right while it was flush against x=0.
                    bg_color.add(img, BAR_W, (SIDE_PAD, p * 36 + 3), start=True)
                    img.paste(bg_color.divider, (SIDE_PAD + 55, p * 36 + 3), bg_color.divider)
                    pos = f'{i}.'
                    addLine(f'&f{pos}', d, gameFont, SIDE_PAD + 10, p * 36 + 9)
                    addLine(f"&f{player['name']}", d, gameFont, SIDE_PAD + 65, p * 36 + 9)
                    _, _, w, h = d.textbbox((0, 0), f"{player['shells']:,}", font=gameFont)
                    if i == 1:
                        widest = w
                    addLine(f"&f{player['shells']:,}", d, gameFont, BAR_R - 10 - w, p * 36 + 9)
                    img.paste(shells_img, (BAR_R - 35 - widest, p * 36 + 11), shells_img)
                    img.paste(bg_color.divider, (BAR_R - 45 - widest, p * 36 + 3), bg_color.divider)
                    i += 1

                img, d = expand_image(img, border=(0, 120, 0, 10), fill='#00000000')
                title = Image.open('images/profile/shell_leaderboard.png')
                img.paste(title, ((img.width - title.width) // 2, 10), title)
                badge = generate_rank_badge('balance', '#0477c9', scale=1)
                img.paste(badge, ((img.width - badge.width) // 2, 98), badge)

                background = Image.new('RGBA', (img.width, img.height), color='#00000000')
                bg_img = Image.open('images/profile/leaderboard_bg.png')
                background.paste(
                    bg_img,
                    ((img.width - bg_img.width) // 2, (img.height - bg_img.height) // 2),
                    bg_img
                )
                background.paste(img, (0, 0), img)

                buf = BytesIO()
                background.save(buf, format='PNG')
                buf.seek(0)
                t = int(time.time())
                leaderboard_img = discord.File(buf, filename=f"leaderboard{t}_{page}.png")
                book.append(Page(content='', files=[leaderboard_img]))

            final_book = pages.Paginator(pages=book)
            add_paginator_buttons(final_book)

            await final_book.respond(message.interaction)
        finally:
            db.close()

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Shell(client))
