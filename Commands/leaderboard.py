import json
import math
import time
from io import BytesIO
from typing import Dict, List, Any

import discord
from PIL import Image, ImageFont, ImageDraw
from discord import SlashCommandGroup
from discord.ext import commands, pages

from Helpers.classes import PlaceTemplate, Page
from Helpers.database import DB
from Helpers.functions import addLine, expand_image, generate_rank_badge
from Helpers.variables import rank_map, discord_ranks

# ============================
# Core leaderboard generator
# ============================

def create_leaderboard(order_key: str, key_icon: str, header: str, days: int = 7) -> pages.Paginator:
    """Build a paginator filled with leaderboard images for a given metric.

    Args:
        order_key: key in each member record (e.g. 'contributed', 'wars', 'playtime', 'shells', 'raids')
        key_icon: path to the 16x16-ish icon image used next to the numeric stat
        header:   path to the title image pasted at the top of the page
        days:     number of days to look back to compute "contributed" delta (<=0 => all-time)
    Returns:
        discord.ext.pages.Paginator instance ready to respond.
    """
    book: List[Page] = []

    # Load activity JSON (already sorted oldest->newest later, but we'll sort anyway)
    with open('player_activity.json', 'r') as f:
        all_days_data: List[Dict[str, Any]] = json.load(f)

    # Get discord ranks once (still needed for star overlay)
    db = DB()
    db.connect()
    db.cursor.execute("SELECT uuid, rank FROM discord_links")
    uuid_to_discord_rank: Dict[str, str] = {row[0]: row[1] for row in db.cursor.fetchall()}
    db.close()

    # Ensure chronological order
    all_days_data.sort(key=lambda x: x['time'])
    newest_day_members = all_days_data[-1]['members'] if all_days_data else []

    # Build full history per uuid
    uuid_to_full_history: Dict[str, List[Dict[str, Any]]] = {}
    for day in all_days_data:
        for member in day['members']:
            uuid = member['uuid']
            uuid_to_full_history.setdefault(uuid, []).append(member)

    # Assets
    bg1 = PlaceTemplate('images/profile/first.png')
    bg2 = PlaceTemplate('images/profile/second.png')
    bg3 = PlaceTemplate('images/profile/third.png')
    bg_other = PlaceTemplate('images/profile/other.png')
    warning_icon = Image.open('images/profile/time_warning.png')
    rank_star = Image.open('images/profile/rank_star.png')
    warning_icon.thumbnail((16, 16))
    icon = Image.open(key_icon)
    icon.thumbnail((16, 16))
    game_font = ImageFont.truetype('images/profile/game.ttf', 19)

    playerdata: List[Dict[str, Any]] = []

    # Unified path for *all* metrics (shells/raids/etc.). No DB shells lookup anymore.
    for member in newest_day_members:
        uuid = member['uuid']
        name = member['name']
        api_rank = member.get('rank', 'unknown')  # fallback
        rank = uuid_to_discord_rank.get(uuid, api_rank)
        current_value = member.get(order_key, 0)
        history = uuid_to_full_history.get(uuid, [])

        warning = False
        if days > 0:
            # Filter history entries that actually contain the key
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
            # All-time
            contributed = current_value

        playerdata.append({
            'name': name,
            'uuid': uuid,
            'contributed': contributed,
            'rank': rank,
            'warning': warning
        })

    if not playerdata:
        # Return an empty paginator to avoid crashes
        return pages.Paginator(pages=[Page(content='No data available.')])

    # Sort & paginate
    playerdata.sort(key=lambda x: x['contributed'], reverse=True)
    total_pages = math.ceil(len(playerdata) / 10)
    rank_counter = 1
    widest = 0

    for page_index in range(total_pages):
        img = Image.new('RGBA', (560, 0), color='#00000000')
        draw = ImageDraw.Draw(img)
        draw.fontmode = '1'

        page_chunk = playerdata[page_index * 10:(page_index + 1) * 10]
        for row_idx, player in enumerate(page_chunk):
            img, draw = expand_image(img, border=(0, 0, 0, 36), fill='#00000000')
            bg_color = [bg1, bg2, bg3][rank_counter - 1] if rank_counter <= 3 else bg_other

            # Paste warning icon if data window < days
            if player['warning']:
                img.paste(warning_icon, (img.width - 24, row_idx * 36 + 11), warning_icon)

            # Slot background & dividers
            bg_color.add(img, 530, (0, row_idx * 36 + 3))
            img.paste(bg_color.divider, (55, row_idx * 36 + 3), bg_color.divider)
            addLine(f'&f{rank_counter}.', draw, game_font, 10, row_idx * 36 + 9)

            # Rank stars
            rank_key = (player.get('rank') or '').lower()
            general_rank = None
            for rname, info in discord_ranks.items():
                if rname.lower() == rank_key:
                    general_rank = info['in_game_rank'].lower()
                    break
            stars = rank_map.get(general_rank, '')
            for s in range(len(stars)):
                img.paste(rank_star, (65 + (s * 12), row_idx * 36 + 14), rank_star)

            # Name divider
            img.paste(bg_color.divider, (133, row_idx * 36 + 3), bg_color.divider)
            addLine(f'&f{player["name"]}', draw, game_font, 143, row_idx * 36 + 9)

            # Value text right aligned
            value_str = "{:,}".format(int(player['contributed']))
            _, _, w, _ = draw.textbbox((0, 0), value_str, font=game_font)
            if rank_counter == 1:
                widest = w
            addLine(f'&f{value_str}', draw, game_font, img.width - 40 - w, row_idx * 36 + 9)

            # Icon & divider near value
            img.paste(icon, (img.width - 65 - widest, row_idx * 36 + 11), icon)
            img.paste(bg_color.divider, (img.width - 75 - widest, row_idx * 36 + 3), bg_color.divider)

            rank_counter += 1

        # Footer (title + badge)
        img, draw = expand_image(img, border=(0, 120, 0, 20), fill='#00000000')
        title_img = Image.open(header)
        img.paste(title_img, (img.width // 2 - title_img.width // 2, 10), title_img)

        badge = generate_rank_badge(f"{days} days" if days > 0 else "All-Time", "#0477c9", scale=1)
        img.paste(badge, (img.width // 2 - badge.width // 2, 98), badge)

        # Background
        background = Image.new('RGBA', (img.width, img.height), color='#00000000')
        bg_img = Image.open('images/profile/leaderboard_bg.png')
        background.paste(bg_img, (img.width // 2 - bg_img.width // 2, img.height // 2 - bg_img.height // 2))
        background.paste(img, (0, 0), img)

        with BytesIO() as file:
            background.save(file, format="PNG")
            file.seek(0)
            t = int(time.time())
            leaderboard_img = discord.File(file, filename=f"leaderboard{t}_{page_index}.png")
        book.append(Page(content='', files=[leaderboard_img]))

    paginator = pages.Paginator(pages=book)
    paginator.add_button(pages.PaginatorButton("prev", emoji="<:left_arrow:1198703157501509682>", style=discord.ButtonStyle.red))
    paginator.add_button(pages.PaginatorButton("next", emoji="<:right_arrow:1198703156088021112>", style=discord.ButtonStyle.green))
    paginator.add_button(pages.PaginatorButton("first", emoji="<:first_arrows:1198703152204103760>", style=discord.ButtonStyle.blurple))
    paginator.add_button(pages.PaginatorButton("last", emoji="<:last_arrows:1198703153726627880>", style=discord.ButtonStyle.blurple))

    return paginator


# ============================
# Cog & slash commands
# ============================

PERIOD_TO_DAYS = {
    'All-Time': -1,
    '7 Days': 7,
    '14 Days': 14,
    '30 Days': 30,
    'Custom': 7  # default fallback
}


class Leaderboard(commands.Cog):
    def __init__(self, client: discord.Client):
        self.client = client

    leaderboard_group = SlashCommandGroup('leaderboard', 'Leaderboard commands')

    # ---- XP ----
    @leaderboard_group.command()
    async def xp(self, message: discord.ApplicationContext, period: discord.Option(str, choices=list(PERIOD_TO_DAYS.keys()))):
        await message.defer()
        try:
            days = PERIOD_TO_DAYS.get(period, 7)
            book = create_leaderboard('contributed', 'images/profile/xp.png', 'images/profile/guxp_title.png', days=days)
            await book.respond(message.interaction)
        except Exception as e:
            await message.respond("Something went wrong generating the XP leaderboard.", ephemeral=True)
            print("Error in /xp:", e)

    # ---- Wars ----
    @leaderboard_group.command()
    async def wars(self, message: discord.ApplicationContext, period: discord.Option(str, choices=list(PERIOD_TO_DAYS.keys()))):
        await message.defer()
        try:
            days = PERIOD_TO_DAYS.get(period, 7)
            book = create_leaderboard('wars', 'images/profile/wars.png', 'images/profile/wars_title.png', days=days)
            await book.respond(message.interaction)
        except Exception as e:
            await message.respond("Something went wrong generating the wars leaderboard.", ephemeral=True)
            print("Error in /wars:", e)

    # ---- Playtime ----
    @leaderboard_group.command()
    async def playtime(self, message: discord.ApplicationContext, period: discord.Option(str, choices=list(PERIOD_TO_DAYS.keys()))):
        await message.defer()
        try:
            days = PERIOD_TO_DAYS.get(period, 7)
            book = create_leaderboard('playtime', 'images/profile/playtime.png', 'images/profile/playtime_title.png', days=days)
            await book.respond(message.interaction)
        except Exception as e:
            await message.respond("Something went wrong generating the playtime leaderboard.", ephemeral=True)
            print("Error in /playtime:", e)

    # ---- Shells (now time-gated like others) ----
    @leaderboard_group.command()
    async def shells(self, message: discord.ApplicationContext, period: discord.Option(str, choices=list(PERIOD_TO_DAYS.keys()))):
        await message.defer()
        try:
            days = PERIOD_TO_DAYS.get(period, 7)
            book = create_leaderboard('shells', 'images/profile/shells.png', 'images/profile/shell_leaderboard.png', days=days)
            await book.respond(message.interaction)
        except Exception as e:
            await message.respond("Something went wrong generating the shells leaderboard.", ephemeral=True)
            print("Error in /shells:", e)

    # ---- NEW: Raids ----
    @leaderboard_group.command()
    async def raids(self, message: discord.ApplicationContext, period: discord.Option(str, choices=list(PERIOD_TO_DAYS.keys()))):
        """Leaderboard for raid clears (value stored under 'raids' in player_activity.json)."""
        await message.defer()
        try:
            days = PERIOD_TO_DAYS.get(period, 7)
            book = create_leaderboard('raids', 'images/profile/raid_icon.png', 'images/profile/raids_title.png', days=days)
            await book.respond(message.interaction)
        except Exception as e:
            await message.respond("Something went wrong generating the raids leaderboard.", ephemeral=True)
            print("Error in /raids:", e)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client: discord.Client):
    client.add_cog(Leaderboard(client))
