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
    """
    Build a paginator filled with leaderboard images for a given metric.

    Inclusive window rule (most-recent-first snapshots):
      - For a W-day window, baseline is the (W+1)-th most recent snapshot => index = W.
      - Value = current_activity (live) - baseline_snapshot (with fallback toward newer indices).
      - If a member never appears in any snapshot up to index 0, use baseline=current to yield 0 and warn.

    Args:
        order_key: key in each member record (e.g. 'contributed', 'wars', 'playtime', 'shells', 'raids')
        key_icon: path to the 16x16-ish icon image used next to the numeric stat
        header:   path to the title image pasted at the top of the page
        days:     last N calendar days to sum (<=0 => all-time, i.e., current totals)
    Returns:
        discord.ext.pages.Paginator instance ready to respond.
    """
    from collections import defaultdict

    # Keys that are cumulative in our snapshots and should use (current - baseline)
    CUMULATIVE_KEYS = {"contributed", "wars", "playtime", "shells", "raids"}

    # ---------------------------
    # Load data files
    # ---------------------------
    try:
        with open('current_activity.json', 'r', encoding='utf-8') as f:
            current_data = json.load(f)
    except Exception:
        return pages.Paginator(pages=[Page(content='No current activity available.')])

    try:
        with open('player_activity.json', 'r', encoding='utf-8') as f:
            activity_days_mrf: List[Dict[str, Any]] = json.load(f)  # most recent first
    except Exception:
        # If we can't read historical snapshots, we can still render a "live now" leaderboard
        activity_days_mrf = []

    if not isinstance(current_data, dict) or not current_data.get('members'):
        return pages.Paginator(pages=[Page(content='No current activity members found.')])

    # Most-recent-first count
    num_snapshots = len(activity_days_mrf)

    # Fast index for historical snapshots: at each snapshot index (most recent first),
    # map uuid -> the member dict in that snapshot
    hist_by_uuid_at: List[Dict[str, Dict[str, Any]]] = []
    for day in activity_days_mrf:
        idx_map: Dict[str, Dict[str, Any]] = {}
        for m in day.get('members', []):
            idx_map[m['uuid']] = m
        hist_by_uuid_at.append(idx_map)

    # Current/live membership is source of truth for "who to list" and "names"
    current_members = current_data.get('members', [])
    current_by_uuid = {m['uuid']: m for m in current_members}

    # ---------------------------
    # DB: discord ranks overlay (stars)
    # ---------------------------
    db = DB()
    db.connect()
    db.cursor.execute("SELECT uuid, rank FROM discord_links")
    uuid_to_discord_rank: Dict[str, str] = {row[0]: row[1] for row in db.cursor.fetchall()}
    db.close()

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
        Return the current live cumulative value for the uuid/key from current_activity.json.
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

    def find_baseline_value(uuid: str, key: str, window_days: int) -> (int, bool):
        """
        For cumulative keys:
          - If window_days <= 0: all-time -> baseline = 0, warn=False
          - Else baseline_idx = window_days (W), i.e., the (W+1)-th most recent snapshot.
          - If uuid missing at baseline_idx, walk toward newer snapshots (W-1, W-2, ..., 0).
          - If never appears, use baseline=current to force delta 0 and warn=True.
        Returns (baseline_value, warn_flag).
        """
        if window_days <= 0 or num_snapshots == 0:
            return 0, False

        baseline_idx = min(window_days, num_snapshots - 1)  # clamp in case we have fewer than W+1 snapshots
        warn = False

        # Try baseline at W
        hist_map = hist_by_uuid_at[baseline_idx]
        entry = hist_map.get(uuid)
        if entry is not None:
            try:
                return int(entry.get(key) or 0), False
            except Exception:
                return 0, True  # malformed value -> treat as 0 and warn

        # Fallback: walk toward the present (W-1 ... 0)
        for i in range(baseline_idx - 1, -1, -1):
            e = hist_by_uuid_at[i].get(uuid)
            if e is not None:
                warn = True
                try:
                    return int(e.get(key) or 0), True
                except Exception:
                    return 0, True

        # Never seen in any of the snapshots -> baseline=current (delta=0) and warn
        current_val, _ = get_current_value(uuid, key)
        return current_val, True

    def perday_sum_inclusive(uuid: str, key: str, window_days: int) -> (int, bool):
        """
        For genuinely per-day keys (not in CUMULATIVE_KEYS), sum the most-recent-first
        slice [0 .. window_days] inclusive of today-so-far semantics:
          - If window_days <= 0: sum all available per-day entries across snapshots.
          - If a member is missing in parts of the span, treat missing as 0 and warn.
        """
        if num_snapshots == 0:
            # No history -> try current only
            current_val, is_null = get_current_value(uuid, key)
            return current_val, True

        if window_days <= 0:
            end_idx = num_snapshots - 1
        else:
            end_idx = min(window_days, num_snapshots - 1)

        total = 0
        warn = False
        for i in range(0, end_idx + 1):
            entry = hist_by_uuid_at[i].get(uuid)
            if entry is None:
                warn = True
                continue
            try:
                total += int(entry.get(key) or 0)
            except Exception:
                warn = True
        return total, warn

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
            base_val, warn_flag = find_baseline_value(uuid, order_key, days)
            contributed = curr_val - base_val
            if contributed < 0:
                # In case of data resets or rollbacks, never show negatives
                contributed = 0
                warn_flag = True
            is_private = is_null  # Mark as private if current value is null
        else:
            # Per-day style metrics (if you ever add them)
            contributed, warn_flag = perday_sum_inclusive(uuid, order_key, days)

        player_rows.append({
            'name': name,
            'uuid': uuid,
            'contributed': int(contributed),
            'rank': rank,
            'warning': bool(warn_flag),
            'is_private': is_private,
        })

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

    leaderboard_group = SlashCommandGroup('leaderboard', 'Leaderboard commands')

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
            print("Error in /xp:", e)

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
            print("Error in /wars:", e)

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
            print("Error in /playtime:", e)

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
            print("Error in /shells:", e)

    # ---- NEW: Raids ----
    @leaderboard_group.command(description='Display the Raids leaderboard')
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
