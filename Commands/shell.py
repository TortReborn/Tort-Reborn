import math
import time
from io import BytesIO

import discord
import requests
from PIL import Image, ImageDraw, ImageFont
from discord import SlashCommandGroup, ApplicationContext
from discord.ext import commands, pages

from Helpers.classes import PlaceTemplate, Page, Guild
from Helpers.database import DB
from Helpers.functions import getGuildMembers, getPlayerUUID, addLine, expand_image, generate_rank_badge
from Helpers.logger import log, INFO
from Helpers.variables import rank_map, ALL_GUILD_IDS


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

    shell_group = SlashCommandGroup('shell', 'Shells related commands', guild_ids=ALL_GUILD_IDS)

    @shell_group.command(description='Displays the top shell balances')
    async def baltop(self, message: ApplicationContext):
        await message.response.defer()
        db = DB()
        db.connect()
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

        for page in range(page_num):
            img = Image.new('RGBA', (410, 0), color='#00000000')
            d = ImageDraw.Draw(img)
            d.fontmode = '1'
            page_playerdata = shelldata[(10 * page):(10 * page + 10)]

            for p, player in enumerate(page_playerdata):
                img, d = expand_image(img, border=(0, 0, 0, 36), fill='#00000000')
                bg_color = {1: bg1, 2: bg2, 3: bg3}.get(i, bg)
                bg_color.add(img, 380, (0, p * 36 + 3))
                img.paste(bg_color.divider, (55, p * 36 + 3), bg_color.divider)
                pos = f'{i}.'
                addLine(f'&f{pos}', d, gameFont, 10, p * 36 + 9)
                addLine(f"&f{player['name']}", d, gameFont, 65, p * 36 + 9)
                _, _, w, h = d.textbbox((0, 0), f"{player['shells']:,}", font=gameFont)
                if i == 1:
                    widest = w
                addLine(f"&f{player['shells']:,}", d, gameFont, img.width - 40 - w, p * 36 + 9)
                img.paste(shells_img, (img.width - 65 - widest, p * 36 + 11), shells_img)
                img.paste(bg_color.divider, (img.width - 75 - widest, p * 36 + 3), bg_color.divider)
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

            with BytesIO() as file:
                background.save(file, format='PNG')
                file.seek(0)
                t = int(time.time())
                leaderboard_img = discord.File(file, filename=f"leaderboard{t}_{page}.png")
            book.append(Page(content='', files=[leaderboard_img]))

        final_book = pages.Paginator(pages=book)
        final_book.add_button(pages.PaginatorButton('prev', emoji='<:left_arrow:1198703157501509682>', style=discord.ButtonStyle.red))
        final_book.add_button(pages.PaginatorButton('next', emoji='<:right_arrow:1198703156088021112>', style=discord.ButtonStyle.green))
        final_book.add_button(pages.PaginatorButton('first', emoji='<:first_arrows:1198703152204103760>', style=discord.ButtonStyle.blurple))
        final_book.add_button(pages.PaginatorButton('last', emoji='<:last_arrows:1198703153726627880>', style=discord.ButtonStyle.blurple))

        await final_book.respond(message.interaction)
        db.close()

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Shell(client))
