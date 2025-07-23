import time
from io import BytesIO

import discord
from PIL import Image, ImageFont, ImageDraw
from discord.ext import commands
from discord.ext import pages
from discord.commands import slash_command
import json

from Helpers.classes import PlaceTemplate, Page
from Helpers.functions import isInCurrDay, addLine, expand_image, generate_rank_badge
from Helpers.variables import rank_map
import math


class Xp(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(description='Displays an XP leaderboard for specified time period (Default 7 days)')
    async def xp(self, message, days: discord.Option(int, min_value=1, max_value=30, default=7)):
        await message.defer()
        book = []
        with open('player_activity.json', 'r') as f:
            old_data = json.loads(f.read())
        with open('current_activity.json', 'r') as f:
            new_data = json.loads(f.read())

        if days > len(old_data):
            days = len(old_data)
        elif days < 1:
            days = 1
        bg1 = PlaceTemplate('images/profile/first.png')
        bg2 = PlaceTemplate('images/profile/second.png')
        bg3 = PlaceTemplate('images/profile/third.png')
        bg = PlaceTemplate('images/profile/other.png')
        xp_img = Image.open('images/profile/xp.png')
        xp_img.thumbnail((16, 16))
        warning_icon = Image.open('images/profile/time_warning.png')
        warning_icon.thumbnail((16, 16))
        gameFont = ImageFont.truetype('images/profile/game.ttf', 19)
        legendFont = ImageFont.truetype('images/profile/5x5.ttf', 20)
        widest = 0
        playerdata = []
        for member in new_data:
            uuid = member['uuid']
            cont = member['contributed']
            day = days
            while not isInCurrDay(old_data[day - 1]['members'], uuid) and day - 1 != 0:
                day -= 1
            else:
                for user in old_data[day - 1]['members']:
                    if uuid == user['uuid']:
                        real_cont = cont - user['contributed']
                        if day != days:
                            playerdata.append(
                                {'name': member['name'], 'uuid': uuid, 'contributed': real_cont, 'rank': user['rank'],
                                 'warning': True})
                        else:
                            playerdata.append(
                                {'name': member['name'], 'uuid': uuid, 'contributed': real_cont, 'rank': user['rank'],
                                 'warning': False})
        if playerdata:
            i = 1
            playerdata.sort(key=lambda x: x['contributed'], reverse=True)
            page_num = int(math.ceil(len(playerdata) / 10))
            for page in range(page_num):
                img = Image.new('RGBA', (560, 0), color='#00000000')
                d = ImageDraw.Draw(img)
                d.fontmode = '1'
                page_playerdata = playerdata[(10 * page):10 + (10 * page)]
                for p, player in enumerate(page_playerdata):
                    img, d = expand_image(img, border=(0, 0, 0, 36), fill='#00000000')
                    match i:
                        case 1:
                            bg_color = bg1
                        case 2:
                            bg_color = bg2
                        case 3:
                            bg_color = bg3
                        case _:
                            bg_color = bg
                    if player['warning']:
                        img.paste(warning_icon, (img.width - 24, p * 36 + 11), warning_icon)
                    bg_color.add(img, 530, (0, p * 36 + 3))
                    img.paste(bg_color.divider, (55, p * 36 + 3), bg_color.divider)
                    pos = f'{i}.'
                    addLine(f'&f{pos}', d, gameFont, 10, p * 36 + 9)
                    addLine(f'&f{player["name"]}', d, gameFont, 65, p * 36 + 9)
                    _, _, w, h = d.textbbox((0, 0), "{:,}".format(player["contributed"]), font=gameFont)
                    if i == 1:
                        widest = w
                    addLine(f'&f{"{:,}".format(player["contributed"])}', d, gameFont, img.width - 40 - w, p * 36 + 9)
                    img.paste(xp_img, (img.width - 65 - widest, p * 36 + 11), xp_img)
                    img.paste(bg_color.divider, (img.width - 75 - widest, p * 36 + 3), bg_color.divider)
                    i += 1

                img, d = expand_image(img, border=(0, 120, 0, 20), fill='#00000000')
                title = Image.open('images/profile/guxp_title.png')
                img.paste(title, (int(img.width / 2) - int(title.width / 2), 10), title)
                badge = generate_rank_badge(f"{days} days", "#0477c9", scale=1)
                img.paste(badge, (int(img.width / 2) - int(badge.width / 2), 98), badge)

                img.paste(warning_icon, (10, img.height - 18), warning_icon)
                d.text((36, img.height - 23), f"Member for less than {days} days", font=legendFont)

                background = Image.new('RGBA', (img.width, img.height), color='#00000000')
                bg_img = Image.open('images/profile/leaderboard_bg.png')
                background.paste(bg_img,
                                 (int(img.width / 2) - int(bg_img.width / 2),
                                  int(img.height / 2) - int(bg_img.height / 2)))
                background.paste(img, (0, 0), img)

                with BytesIO() as file:
                    background.save(file, format="PNG")
                    file.seek(0)
                    t = int(time.time())
                    leaderboard_img = discord.File(file, filename=f"leaderboard{t}_{page}.png")
                book.append(Page(content='', files=[leaderboard_img]))

        final_book = pages.Paginator(pages=book)
        final_book.add_button(
            pages.PaginatorButton("prev", emoji="<:left_arrow:1198703157501509682>", style=discord.ButtonStyle.red))
        final_book.add_button(
            pages.PaginatorButton("next", emoji="<:right_arrow:1198703156088021112>", style=discord.ButtonStyle.green))
        final_book.add_button(pages.PaginatorButton("first", emoji="<:first_arrows:1198703152204103760>",
                                                    style=discord.ButtonStyle.blurple))
        final_book.add_button(pages.PaginatorButton("last", emoji="<:last_arrows:1198703153726627880>",
                                                    style=discord.ButtonStyle.blurple))
        await final_book.respond(message.interaction)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Xp(client))
