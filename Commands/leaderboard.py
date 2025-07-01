import json
import math
import time
from io import BytesIO

import discord
from PIL import Image, ImageFont, ImageDraw
from discord import SlashCommandGroup
from discord.ext import commands, pages

from Helpers.classes import PlaceTemplate, Page
from Helpers.database import DB
from Helpers.functions import addLine, expand_image, generate_rank_badge
from Helpers.variables import rank_map, discord_ranks

def create_leaderboard(order_key, key_icon, header, days=7):
    book = []
    with open('player_activity.json', 'r') as f:
        all_days_data = json.load(f)

    all_days_data.sort(key=lambda x: x['time'])  # oldest to newest
    newest_day = all_days_data[-1]['members']
    uuid_to_full_history = {}
    for day in all_days_data:
        for member in day['members']:
            uuid = member['uuid']
            if uuid not in uuid_to_full_history:
                uuid_to_full_history[uuid] = []
            uuid_to_full_history[uuid].append(member)

    bg1 = PlaceTemplate('images/profile/first.png')
    bg2 = PlaceTemplate('images/profile/second.png')
    bg3 = PlaceTemplate('images/profile/third.png')
    bg = PlaceTemplate('images/profile/other.png')
    warning_icon = Image.open('images/profile/time_warning.png')
    rank_star = Image.open('images/profile/rank_star.png')
    warning_icon.thumbnail((16, 16))
    icon = Image.open(key_icon)
    icon.thumbnail((16, 16))
    gameFont = ImageFont.truetype('images/profile/game.ttf', 19)
    widest = 0
    playerdata = []

    if order_key == 'shells':
        db = DB()
        db.connect()
        db.cursor.execute('''
            SELECT COALESCE(shells.ign, discord_links.ign) as ign,
                   discord_links.uuid,
                   COALESCE(shells.shells, 0) as shells,
                   COALESCE(discord_links.rank, 'unknown') as rank
            FROM shells
            LEFT JOIN discord_links ON shells.user = discord_links.discord_id
        ''')
        results = db.cursor.fetchall()
        for ign, uuid, shells, rank in results:
            playerdata.append({
                'name': ign if ign else 'Unknown',
                'uuid': uuid if uuid is not None else '',
                'contributed': shells,
                'rank': rank,
                'warning': False
            })
        db.close()
    else:
        for member in newest_day:
            uuid = member['uuid']
            name = member['name']
            rank = member['rank']
            current_value = member.get(order_key, 0)
            history = uuid_to_full_history.get(uuid, [])

            warning = False
            contributed = 0
            if days > 0:
                filtered_history = [entry for entry in history if order_key in entry]
                if len(filtered_history) >= days:
                    old_value = filtered_history[-days].get(order_key, 0)
                    contributed = current_value - old_value
                elif len(filtered_history) >= 2:
                    old_value = filtered_history[0].get(order_key, 0)
                    contributed = current_value - old_value
                    warning = True
                else:
                    contributed = 0
                    warning = True
            else:
                contributed = current_value

            playerdata.append({
                'name': name,
                'uuid': uuid,
                'contributed': contributed,
                'rank': rank,
                'warning': warning
            })

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
                bg_color = [bg1, bg2, bg3][i - 1] if i <= 3 else bg

                if player['warning']:
                    img.paste(warning_icon, (img.width - 24, p * 36 + 11), warning_icon)
                bg_color.add(img, 530, (0, p * 36 + 3))
                img.paste(bg_color.divider, (55, p * 36 + 3), bg_color.divider)
                addLine(f'&f{i}.', d, gameFont, 10, p * 36 + 9)

                rank_key = (player.get('rank') or '').lower()
                general_rank = None
                for rname, info in discord_ranks.items():
                    if rname.lower() == rank_key:
                        general_rank = info['in_game_rank'].lower()
                        break

                stars = rank_map.get(general_rank, '')
                for s in range(len(stars)):
                    img.paste(rank_star, (65 + (s * 12), p * 36 + 14), rank_star)

                img.paste(bg_color.divider, (133, p * 36 + 3), bg_color.divider)
                addLine(f'&f{player["name"]}', d, gameFont, 143, p * 36 + 9)
                _, _, w, _ = d.textbbox((0, 0), "{:,}".format(int(player["contributed"])), font=gameFont)
                if i == 1:
                    widest = w
                addLine(f'&f{"{:,}".format(int(player["contributed"]))}', d, gameFont, img.width - 40 - w, p * 36 + 9)
                img.paste(icon, (img.width - 65 - widest, p * 36 + 11), icon)
                img.paste(bg_color.divider, (img.width - 75 - widest, p * 36 + 3), bg_color.divider)
                i += 1

            img, d = expand_image(img, border=(0, 120, 0, 20), fill='#00000000')
            title = Image.open(header)
            img.paste(title, (int(img.width / 2) - int(title.width / 2), 10), title)
            badge = generate_rank_badge(f"{days} days" if days > 0 else "All-Time", "#0477c9", scale=1)
            img.paste(badge, (int(img.width / 2) - int(badge.width / 2), 98), badge)

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
        final_book.add_button(pages.PaginatorButton("prev", emoji="<:left_arrow:1198703157501509682>", style=discord.ButtonStyle.red))
        final_book.add_button(pages.PaginatorButton("next", emoji="<:right_arrow:1198703156088021112>", style=discord.ButtonStyle.green))
        final_book.add_button(pages.PaginatorButton("first", emoji="<:first_arrows:1198703152204103760>", style=discord.ButtonStyle.blurple))
        final_book.add_button(pages.PaginatorButton("last", emoji="<:last_arrows:1198703153726627880>", style=discord.ButtonStyle.blurple))

    return final_book


class Leaderboard(commands.Cog):
    def __init__(self, client):
        self.client = client

    leaderboard_group = SlashCommandGroup('leaderboard', 'Leaderboard commands')

    @leaderboard_group.command()
    async def xp(self, message, period: discord.Option(str, choices=['All-Time', '7 Days', '14 Days', '30 Days', 'Custom'])):
        await message.defer()
        try:
            period_to_days = {'All-Time': -1, '7 Days': 7, '14 Days': 14, '30 Days': 30, 'Custom': 7}
            days = period_to_days.get(period, 7)
            background_book = create_leaderboard(
                'contributed', 'images/profile/xp.png', 'images/profile/guxp_title.png', days=days
            )
            await background_book.respond(message.interaction)
        except Exception as e:
            await message.respond("Something went wrong generating the XP leaderboard.", ephemeral=True)
            print("Error in /xp:", e)

    @leaderboard_group.command()
    async def wars(self, message, period: discord.Option(str, choices=['All-Time', '7 Days', '14 Days', '30 Days', 'Custom'])):
        await message.defer()
        try:
            period_to_days = {'All-Time': -1, '7 Days': 7, '14 Days': 14, '30 Days': 30, 'Custom': 7}
            days = period_to_days.get(period, 7)
            background_book = create_leaderboard(
                'wars', 'images/profile/wars.png', 'images/profile/wars_title.png', days=days
            )
            await background_book.respond(message.interaction)
        except Exception as e:
            await message.respond("Something went wrong generating the wars leaderboard.", ephemeral=True)
            print("Error in /wars:", e)

    @leaderboard_group.command()
    async def playtime(self, message, period: discord.Option(str, choices=['All-Time', '7 Days', '14 Days', '30 Days', 'Custom'])):
        await message.defer()
        try:
            period_to_days = {'All-Time': -1, '7 Days': 7, '14 Days': 14, '30 Days': 30, 'Custom': 7}
            days = period_to_days.get(period, 7)
            background_book = create_leaderboard(
                'playtime', 'images/profile/playtime.png', 'images/profile/playtime_title.png', days=days
            )
            await background_book.respond(message.interaction)
        except Exception as e:
            await message.respond("Something went wrong generating the playtime leaderboard.", ephemeral=True)
            print("Error in /playtime:", e)

    @leaderboard_group.command()
    async def shells(self, message):
        await message.defer()
        try:
            background_book = create_leaderboard(
                'shells', 'images/profile/shells.png', 'images/profile/shell_leaderboard.png', days=-1
            )
            await background_book.respond(message.interaction)
        except Exception as e:
            await message.respond("Something went wrong generating the shells leaderboard.", ephemeral=True)
            print("Error in /shells:", e)

    @commands.Cog.listener()
    async def on_ready(self):
        print('Leaderboard commands loaded')


def setup(client):
    client.add_cog(Leaderboard(client))
