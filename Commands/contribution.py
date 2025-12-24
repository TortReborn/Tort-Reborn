import datetime
import time
from datetime import date, timedelta
from io import BytesIO

from PIL import Image, ImageFont, ImageDraw
from dateutil import parser
import discord
from discord.ext import commands
from discord.ext import pages
from discord.commands import slash_command

from Helpers.classes import PlaceTemplate, Page, Guild
from Helpers.database import DB, get_current_guild_data
from Helpers.functions import isInCurrDay, expand_image, addLine, generate_rank_badge, format_number
from Helpers.variables import rank_map, te, guilds
import json
import math


def _get_baseline_from_db(db: DB, uuid: str, key: str, days: int):
    """Get baseline value from player_activity table using index-based lookup."""
    try:
        # Get the days-th most recent snapshot date (0-indexed)
        db.cursor.execute("""
            SELECT DISTINCT snapshot_date FROM player_activity
            ORDER BY snapshot_date DESC
            OFFSET %s LIMIT 1
        """, (days,))
        date_row = db.cursor.fetchone()
        if not date_row:
            return 0
        target_date = date_row[0]

        db.cursor.execute(f"""
            SELECT {key} FROM player_activity
            WHERE uuid = %s AND snapshot_date = %s
        """, (uuid, target_date))
        row = db.cursor.fetchone()
        if row and row[0] is not None:
            return row[0]
        return 0
    except Exception:
        return 0


class Contribution(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(description='Displays contribution of members', guild_ids=[te, guilds[1]])
    async def contribution(self, message,
                       dayss: discord.Option(int, name="days", min_value=1, max_value=30, default=7)):
        await message.defer()
        book = []
        current_data = get_current_guild_data()

        # Handle both dict and list formats
        if isinstance(current_data, dict):
            new_data = current_data.get('members', [])
        else:
            new_data = current_data

        taq = Guild('The Aquarium').all_members
        playerdata = []

        bg1 = PlaceTemplate('images/profile/first.png')
        bg = PlaceTemplate('images/profile/other.png')
        rank_star = Image.open('images/profile/rank_star.png')
        playtime_icon = Image.open('images/profile/playtime.png')
        playtime_icon.thumbnail((16, 16))
        inactive_icon = Image.open('images/profile/wars.png')
        inactive_icon.thumbnail((16, 16))
        xp_icon = Image.open('images/profile/xp.png')
        xp_icon.thumbnail((16, 16))
        shells_icon = Image.open('images/profile/shells.png')
        shells_icon.thumbnail((16, 16))
        gameFont = ImageFont.truetype('images/profile/game.ttf', 19)
        legendFont = ImageFont.truetype('images/profile/5x5.ttf', 20)
        db = DB()
        db.connect()

        try:
            for member in new_data:
                uuid = member['uuid']
                playtime = member.get('playtime') or 0
                wars = member.get('wars') or 0
                xp = member.get('contributed') or 0

                db.cursor.execute(
                    f'SELECT discord_links.ign, COALESCE(shells.shells, 0) AS shells, COALESCE((SELECT entries FROM promotion_suggestions WHERE promotion_suggestions.uuid = discord_links.uuid), \'[]\') AS entries FROM discord_links LEFT JOIN shells ON discord_links.discord_id = shells.user WHERE discord_links.uuid = \'{uuid}\';')
                row = db.cursor.fetchone()
                if row:
                    shells = row[1]
                    suggestions = json.loads(row[2])
                else:
                    shells = 0
                    suggestions = []

                # Get baselines from database
                base_pt = _get_baseline_from_db(db, uuid, 'playtime', dayss)
                base_wars = _get_baseline_from_db(db, uuid, 'wars', dayss)
                base_xp = _get_baseline_from_db(db, uuid, 'contributed', dayss)
                base_shells = _get_baseline_from_db(db, uuid, 'shells', dayss)

                real_pt = playtime - base_pt
                real_wars = wars - base_wars
                real_xp = xp - base_xp
                real_shells = shells - base_shells

                contribution_score = (real_wars / 5) + (real_xp / 500000000) + (real_pt / 15) + real_shells + (len(suggestions) * 1000000)
                if real_pt >= 0:
                    playerdata.append(
                            {'name': member['name'], 'uuid': uuid, 'playtime': real_pt, 'wars': real_wars,
                             'rank': member['rank'], 'xp': real_xp, 'shells': real_shells, 'suggestions': len(suggestions), 'score': contribution_score})
        finally:
            db.close()

        if playerdata:
            i = 1
            playerdata.sort(key=lambda x: x['score'], reverse=True)
        page_num = int(math.ceil(len(playerdata) / 15))
        for page in range(page_num):
            img = Image.new('RGBA', (820, 0), color='#00000000')
            d = ImageDraw.Draw(img)
            d.fontmode = '1'
            page_playerdata = playerdata[(15 * page):15 + (15 * page)]
            for p, player in enumerate(page_playerdata):
                img, d = expand_image(img, border=(0, 0, 0, 36), fill='#00000000')
                if player['suggestions'] > 0:
                    bg_color = bg1
                else:
                    bg_color = bg
                bg_color.add(img, 800, (0, p * 36 + 3))
                pos = f'{i}.'
                addLine(f'&f{pos}', d, gameFont, 10, p * 36 + 9)
                img.paste(bg_color.divider, (55, p * 36 + 3), bg_color.divider)

                for s in range(len(rank_map[player['rank'].lower()])):
                    img.paste(rank_star, (65 + (s*12), p * 36 + 14), rank_star)

                img.paste(bg_color.divider, (133, p * 36 + 3), bg_color.divider)
                addLine(f'&f{player["name"]}', d, gameFont, 143, p * 36 + 9)
                img.paste(bg_color.divider, (380, p * 36 + 3), bg_color.divider)
                img.paste(playtime_icon, (390, p * 36 + 11), playtime_icon)
                addLine(f'&f{str(int(player["playtime"])) + (" hrs" if int(player["playtime"]) != 1 else " hr")}', d, gameFont, 416, p * 36 + 9)
                img.paste(bg_color.divider, (510, p * 36 + 3), bg_color.divider)
                img.paste(inactive_icon, (520, p * 36 + 11), inactive_icon)
                addLine(f'&f{str(player["wars"])}', d, gameFont, 546, p * 36 + 9)
                img.paste(bg_color.divider, (600, p * 36 + 3), bg_color.divider)
                img.paste(xp_icon, (608, p * 36 + 11), xp_icon)
                addLine(f'&f{format_number(player["xp"])}', d, gameFont, 634, p * 36 + 9)
                img.paste(bg_color.divider, (700, p * 36 + 3), bg_color.divider)
                img.paste(shells_icon, (708, p * 36 + 11), shells_icon)
                addLine(f'&f{player["shells"]}', d, gameFont, 734, p * 36 + 9)

                i += 1

            img, d = expand_image(img, border=(0, 120, 0, 20), fill='#00000000')
            title = Image.open(f'images/profile/contribution_title.png')
            img.paste(title, (int(img.width / 2) - int(title.width / 2), 10), title)
            badge = generate_rank_badge(f"{dayss} days", "#0477c9", scale=1)
            img.paste(badge, (int(img.width / 2) - int(badge.width / 2), 98), badge)

            background = Image.new('RGBA', (img.width, img.height), color='#00000000')
            bg_img = Image.open('images/profile/leaderboard_bg.png')
            background.paste(bg_img,
                             (int(img.width / 2) - int(bg_img.width / 2), int(img.height / 2) - int(bg_img.height / 2)))
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
    client.add_cog(Contribution(client))
