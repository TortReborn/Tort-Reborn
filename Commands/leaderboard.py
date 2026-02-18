import json
import math
import time
from datetime import date, timedelta
from io import BytesIO
from typing import Dict, List, Any

import discord
from PIL import Image, ImageFont, ImageDraw
from discord import SlashCommandGroup
from discord.ext import commands, pages

from Helpers.classes import PlaceTemplate, Page
from Helpers.database import DB, get_current_guild_data
from Helpers.functions import addLine, expand_image, generate_rank_badge
from Helpers.logger import log, ERROR
from Helpers.variables import rank_map, discord_ranks, ALL_GUILD_IDS

# ============================
# Core leaderboard generator
# ============================

def create_leaderboard(order_key: str, key_icon: str, header: str, days: int = 7) -> pages.Paginator:
    """
    Build a paginator filled with leaderboard images for a given metric.

    Uses current guild data (live) minus baseline from player_activity database table.

    Args:
        order_key: key in each member record (e.g. 'contributed', 'wars', 'playtime', 'shells', 'raids')
        key_icon: path to the 16x16-ish icon image used next to the numeric stat
        header:   path to the title image pasted at the top of the page
        days:     last N calendar days to sum (<=0 => all-time, i.e., current totals)
    Returns:
        discord.ext.pages.Paginator instance ready to respond.
    """
    # Keys that are cumulative in our snapshots and should use (current - baseline)
    CUMULATIVE_KEYS = {"contributed", "wars", "playtime", "shells", "raids"}

    # ---------------------------
    # Load current data from database
    # ---------------------------
    current_data = get_current_guild_data()
    if not current_data:
        return pages.Paginator(pages=[Page(content='No current activity available.')])

    if not isinstance(current_data, dict) or not current_data.get('members'):
        return pages.Paginator(pages=[Page(content='No current activity members found.')])

    # Current/live membership is source of truth for "who to list" and "names"
    current_members = current_data.get('members', [])
    current_by_uuid = {m['uuid']: m for m in current_members}

    # ---------------------------
    # DB connection for baseline lookups and discord ranks
    # ---------------------------
    db = DB()
    db.connect()

    try:
        db.cursor.execute("SELECT uuid, rank FROM discord_links")
        uuid_to_discord_rank: Dict[str, str] = {row[0]: row[1] for row in db.cursor.fetchall()}

        # ---------------------------
        # Assets
        # ---------------------------
        bg1 = PlaceTemplate('images/profile/first.png')
        bg2 = PlaceTemplate('images/profile/second.png')
        bg3 = PlaceTemplate('images/profile/third.png')
        bg_other = PlaceTemplate('images/profile/other.png')
        bg_private = PlaceTemplate('images/profile/warning.png')  # Red background for private profiles
        warning_icon = Image.open('images/profile/time_warning.png')
        rank_star = Image.open('images/profile/rank_star.png')
        warning_icon.thumbnail((16, 16))
        icon = Image.open(key_icon)
        icon.thumbnail((16, 16))
        game_font = ImageFont.truetype('images/profile/game.ttf', 19)

        # ---------------------------
        # Helpers
        # ---------------------------
        def get_current_value(uuid: str, key: str) -> tuple[int, bool]:
            """
            Return the current live cumulative value for the uuid/key from database cache.
            Returns: (value, is_null) where is_null=True if the data is actually None/private.
            """
            m = current_by_uuid.get(uuid)
            if not m:
                return 0, False
            v = m.get(key)
            # Check if the value is actually None (private profile)
            if v is None:
                return 0, True
            try:
                return int(v) if isinstance(v, (int, float)) else int(v or 0), False
            except Exception:
                return 0, False

        def find_baseline_value_from_db(uuid: str, key: str, window_days: int) -> tuple[int, bool]:
            """
            Get baseline value from player_activity database table.
            Uses index-based lookup (window_days-th most recent snapshot) to match
            the original JSON-based behavior.
            Returns (baseline_value, warn_flag).
            """
            if window_days <= 0:
                return 0, False

            try:
                # Get the window_days-th most recent snapshot date (0-indexed)
                # This matches the original JSON index-based lookup
                db.cursor.execute("""
                    SELECT DISTINCT snapshot_date FROM player_activity
                    ORDER BY snapshot_date DESC
                    OFFSET %s LIMIT 1
                """, (window_days,))
                date_row = db.cursor.fetchone()

                if not date_row:
                    # Not enough snapshots - use oldest available
                    db.cursor.execute("""
                        SELECT DISTINCT snapshot_date FROM player_activity
                        ORDER BY snapshot_date ASC
                        LIMIT 1
                    """)
                    date_row = db.cursor.fetchone()
                    if not date_row:
                        current_val, _ = get_current_value(uuid, key)
                        return current_val, True

                target_date = date_row[0]

                db.cursor.execute(f"""
                    SELECT {key} FROM player_activity
                    WHERE uuid = %s AND snapshot_date = %s
                """, (uuid, target_date))
                row = db.cursor.fetchone()

                if row and row[0] is not None:
                    return int(row[0]), False

                # Player not found at target date - try walking toward present
                db.cursor.execute(f"""
                    SELECT {key}, snapshot_date FROM player_activity
                    WHERE uuid = %s AND snapshot_date > %s
                    ORDER BY snapshot_date ASC
                    LIMIT 1
                """, (uuid, target_date))
                fallback = db.cursor.fetchone()
                if fallback and fallback[0] is not None:
                    return int(fallback[0]), True  # warn flag since we used fallback

                # Never found - use current value (delta = 0)
                current_val, _ = get_current_value(uuid, key)
                return current_val, True
            except Exception as e:
                log(ERROR, f"Error getting baseline for {uuid}/{key}: {e}", context="leaderboard")
                current_val, _ = get_current_value(uuid, key)
                return current_val, True

        # ---------------------------
        # Build leaderboard rows using CURRENT membership
        # ---------------------------
        player_rows: List[Dict[str, Any]] = []

        for m in current_members:
            uuid = m['uuid']
            name = m.get('name') or m.get('username') or 'Unknown'
            api_rank = m.get('rank', 'unknown')
            rank = uuid_to_discord_rank.get(uuid, api_rank)

            is_private = False  # Track if the relevant metric is private/null

            if order_key in CUMULATIVE_KEYS:
                curr_val, is_null = get_current_value(uuid, order_key)
                base_val, warn_flag = find_baseline_value_from_db(uuid, order_key, days)
                contributed = curr_val - base_val
                if contributed < 0:
                    # In case of data resets or rollbacks, never show negatives
                    contributed = 0
                    warn_flag = True
                is_private = is_null  # Mark as private if current value is null
            else:
                # For non-cumulative keys, just use current value
                contributed, is_null = get_current_value(uuid, order_key)
                warn_flag = False
                is_private = is_null

            player_rows.append({
                'name': name,
                'uuid': uuid,
                'contributed': int(contributed),
                'rank': rank,
                'warning': bool(warn_flag),
                'is_private': is_private,
            })
    finally:
        db.close()

    # Nothing to show?
    if not player_rows:
        return pages.Paginator(pages=[Page(content='No data available.')])

    # ---------------------------
    # Sort & paginate
    # ---------------------------
    # Sort by: private profiles last, then by contributed (descending)
    player_rows.sort(key=lambda x: (x['is_private'], -x['contributed']))
    total_pages = math.ceil(len(player_rows) / 10)
    rank_counter = 1
    widest = 0
    book: List[Page] = []

    for page_index in range(total_pages):
        img = Image.new('RGBA', (560, 0), color='#00000000')
        draw = ImageDraw.Draw(img)
        draw.fontmode = '1'

        page_chunk = player_rows[page_index * 10:(page_index + 1) * 10]
        for row_idx, player in enumerate(page_chunk):
            img, draw = expand_image(img, border=(0, 0, 0, 36), fill='#00000000')

            # Choose background color: red for private, ranked colors for top 3, blue for others
            if player['is_private']:
                bg_color = bg_private
            elif rank_counter <= 3:
                bg_color = [bg1, bg2, bg3][rank_counter - 1]
            else:
                bg_color = bg_other

            # Warning icon for partial window/late join/missing baseline
            if player['warning']:
                img.paste(warning_icon, (img.width - 24, row_idx * 36 + 11), warning_icon)

            # Slot background & dividers
            bg_color.add(img, 530, (0, row_idx * 36 + 3))
            img.paste(bg_color.divider, (55, row_idx * 36 + 3), bg_color.divider)
            addLine(f'&f{rank_counter}.', draw, game_font, 10, row_idx * 36 + 9)

            # Rank stars (based on discord rank mapping)
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

    leaderboard_group = SlashCommandGroup('leaderboard', 'Leaderboard commands', guild_ids=ALL_GUILD_IDS)

    # ---- XP ----
    @leaderboard_group.command(description='Display the XP leaderboard')
    async def xp(self, message: discord.ApplicationContext, period: discord.Option(str, choices=list(PERIOD_TO_DAYS.keys()))):
        await message.defer()
        try:
            days = PERIOD_TO_DAYS.get(period, 7)
            book = create_leaderboard('contributed', 'images/profile/xp.png', 'images/profile/guxp_title.png', days=days)
            await book.respond(message.interaction)
        except Exception as e:
            await message.respond("Something went wrong generating the XP leaderboard.", ephemeral=True)
            log(ERROR, f"Error in /xp: {e}", context="leaderboard")

    # ---- Wars ----
    @leaderboard_group.command(description='Display the Wars leaderboard')
    async def wars(self, message: discord.ApplicationContext, period: discord.Option(str, choices=list(PERIOD_TO_DAYS.keys()))):
        await message.defer()
        try:
            days = PERIOD_TO_DAYS.get(period, 7)
            book = create_leaderboard('wars', 'images/profile/wars.png', 'images/profile/wars_title.png', days=days)
            await book.respond(message.interaction)
        except Exception as e:
            await message.respond("Something went wrong generating the wars leaderboard.", ephemeral=True)
            log(ERROR, f"Error in /wars: {e}", context="leaderboard")

    # ---- Playtime ----
    @leaderboard_group.command(description='Display the Playtime leaderboard')
    async def playtime(self, message: discord.ApplicationContext, period: discord.Option(str, choices=list(PERIOD_TO_DAYS.keys()))):
        await message.defer()
        try:
            days = PERIOD_TO_DAYS.get(period, 7)
            book = create_leaderboard('playtime', 'images/profile/playtime.png', 'images/profile/playtime_title.png', days=days)
            await book.respond(message.interaction)
        except Exception as e:
            await message.respond("Something went wrong generating the playtime leaderboard.", ephemeral=True)
            log(ERROR, f"Error in /playtime: {e}", context="leaderboard")

    # ---- Shells (now time-gated like others) ----
    @leaderboard_group.command(description='Display the Shells leaderboard')
    async def shells(self, message: discord.ApplicationContext, period: discord.Option(str, choices=list(PERIOD_TO_DAYS.keys()))):
        await message.defer()
        try:
            days = PERIOD_TO_DAYS.get(period, 7)
            book = create_leaderboard('shells', 'images/profile/shells.png', 'images/profile/shell_leaderboard.png', days=days)
            await book.respond(message.interaction)
        except Exception as e:
            await message.respond("Something went wrong generating the shells leaderboard.", ephemeral=True)
            log(ERROR, f"Error in /shells: {e}", context="leaderboard")

    # ---- NEW: Raids ----
    @leaderboard_group.command(description='Display the Raids leaderboard')
    async def raids(self, message: discord.ApplicationContext, period: discord.Option(str, choices=list(PERIOD_TO_DAYS.keys()))):
        """Leaderboard for raid clears (value stored under 'raids' in player_activity table)."""
        await message.defer()
        try:
            days = PERIOD_TO_DAYS.get(period, 7)
            book = create_leaderboard('raids', 'images/profile/raid_icon.png', 'images/profile/raids_title.png', days=days)
            await book.respond(message.interaction)
        except Exception as e:
            await message.respond("Something went wrong generating the raids leaderboard.", ephemeral=True)
            log(ERROR, f"Error in /raids: {e}", context="leaderboard")

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client: discord.Client):
    client.add_cog(Leaderboard(client))
