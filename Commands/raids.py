import os
import time
from io import BytesIO
from urllib.parse import quote
from typing import Dict, List, Tuple

import requests
import discord
from discord.ext import commands
from discord.commands import slash_command, Option
from PIL import Image, ImageDraw, ImageFont

from Helpers.functions import (
    vertical_gradient,
    round_corners,
    addLine,
    generate_rank_badge,
    generate_banner,
    getData,
    generate_badge,
)
from Helpers.database import DB
from Helpers.variables import discord_ranks, minecraft_banner_colors, guilds, wynn_ranks

# ---------------------------------------------------------------------------
# Raids Command
#   Visual style intentionally mirrors the /profile card:
#   - Outer tag-colored gradient border w/ rounded corners
#   - Inner panel gradient from user-chosen start/end colors
#   - Background outline + PNG background image
#   - Centered rank badge, avatar bust, guild badges
#   - Stat boxes with semi-transparent rounded rectangles
# ---------------------------------------------------------------------------

class Raids(commands.Cog):
    """
    /raids <username>
    Generates a profile-style card showing the player's four guild raid rankings & counts.
    Incorporates the same customization hooks (background, gradients, tag colors) used in /profile.
    """

    # (abbr, full API name, ranking key)
    RAIDS: List[Tuple[str, str, str]] = [
        ("NOTG", "Nest of the Grootslangs", "grootslangSrPlayers"),
        ("NOL",  "Orphion's Nexus of Light", "orphionSrPlayers"),
        ("TCC",  "The Canyon Colossus", "colossusSrPlayers"),
        ("TNA",  "The Nameless Anomaly", "namelessSrPlayers"),
    ]

    def __init__(self, client: commands.Bot):
        self.client = client

    # ------------------------- Slash Command -------------------------------
    @slash_command(
        name="raids",
        description="Show raid rankings and counts for a player",
        guild_ids=guilds,
    )
    async def raids(self,
                    ctx: discord.ApplicationContext,
                    name: Option(str, "Minecraft username", required=True)):
        await ctx.defer()

        # 1) Fetch player data from Wynncraft API --------------------------------
        player = await self._fetch_player(name)
        if player is None:
            return await ctx.followup.send(f"❌ Could not fetch data for `{name}`.", ephemeral=True)

        # 2) Resolve customization (background, gradients, tag_color) -------------
        tag_color, (grad_start, grad_end), bg_index = self._resolve_customization(ctx, player)

        # 3) Extract raid stats ---------------------------------------------------
        stats = self._extract_raid_stats(player)

        # 4) Build the card image -------------------------------------------------
        card = self._build_base_canvas(tag_color, grad_start, grad_end)
        draw = ImageDraw.Draw(card)

        # Background outline + PNG
        self._draw_background(card, tag_color, bg_index)

        # Player name & avatar
        self._draw_player_header(card, draw, player, tag_color)

        # Rank badge (supportRank or rank)
        self._draw_rank_badge(card, player, tag_color)

        # Guild badges & banner
        self._draw_guild_elements(card, player, tag_color)

        # Raid boxes
        self._draw_raid_boxes(card, draw, stats)

        # 5) Send -----------------------------------------------------------------
        buf = BytesIO()
        card.save(buf, format="PNG")
        buf.seek(0)
        filename = f"raids_{player.get('username', name)}_{int(time.time())}.png"
        await ctx.followup.send(file=discord.File(buf, filename=filename))

    # ------------------------- Helpers -------------------------------------
    async def _fetch_player(self, name: str) -> Dict:
        """Fetch player JSON or return None on error."""
        safe_name = quote(name)
        url = f"https://api.wynncraft.com/v3/player/{safe_name}"
        try:
            res = requests.get(url, timeout=10, headers={"Authorization": f"Bearer {os.getenv("WYNN_TOKEN")}"})
        except requests.RequestException:
            return None
        if res.status_code != 200:
            return None
        payload = res.json()
        data = payload.get("data") or ([payload] if payload.get("username") else [])
        return data[0] if data else None

    def _resolve_customization(self, ctx: discord.ApplicationContext, player: Dict):
        """
        Determine tag_color (outer border), gradient start/end, and background index.
        If the player is **not** in our backend (discord_links/profile_customization), use
        the hard-coded defaults (blue gradient + background 1.png).
        We no longer fall back to the command author's customization.
        """
        # Defaults
        default_grad = ("#293786", "#1d275e")
        default_bg = 1

        # Wynn rank colour (preferred for border)
        w_rank_key = (player.get("supportRank") or player.get("rank") or "").lower()
        tag_color = wynn_ranks.get(w_rank_key, {}).get("color", "#293786")

        grad_start, grad_end = default_grad
        bg_index = default_bg

        uuid = player.get("uuid")
        if not uuid:
            return tag_color, (grad_start, grad_end), bg_index

        db = DB(); db.connect()
        try:
            # Only use customization if we find this player in discord_links
            db.cursor.execute("SELECT discord_id FROM discord_links WHERE uuid = %s", (uuid,))
            row = db.cursor.fetchone()
            if row:
                target_discord_id = row[0]
                db.cursor.execute('SELECT background, gradient FROM profile_customization WHERE "user" = %s',
                                  (target_discord_id,))
                prow = db.cursor.fetchone()
                if prow:
                    if prow[0] is not None:
                        bg_index = prow[0]
                    if isinstance(prow[1], list) and len(prow[1]) == 2:
                        grad_start, grad_end = prow[1]
        finally:
            db.close()

        if not bg_index:
            bg_index = 1
        return tag_color, (grad_start, grad_end), bg_index

    def _extract_raid_stats(self, player: Dict) -> List[Tuple[str, int, int]]:
        """Return list of (abbr, rank, count)."""
        ranking = player.get("ranking", {})
        raids_list = player.get("globalData", {}).get("raids", {}).get("list", {})
        stats: List[Tuple[str, int, int]] = []
        for abbr, full, rank_key in self.RAIDS:
            rank_val = ranking.get(rank_key, 0)
            count = raids_list.get(full, 0)
            stats.append((abbr, rank_val, count))
        return stats

    # ------------------------- Drawing pieces ---------------------------------
    def _build_base_canvas(self, tag_color: str, grad_start: str, grad_end: str) -> Image.Image:
        """Create outer border + inner gradient panel with rounded corners."""
        # Outer border (full-size default from vertical_gradient)
        card = vertical_gradient(main_color=tag_color)
        card = round_corners(card)

        # Inner panel
        overlay = vertical_gradient(width=850, height=1130,
                                    main_color=grad_start,
                                    secondary_color=grad_end)
        card.paste(overlay, (25, 25), overlay)
        return card

    def _draw_background(self, card: Image.Image, tag_color: str, bg_index: int) -> None:
        """Outline rect + user-selected background PNG."""
        outline = vertical_gradient(width=818, height=545, main_color=tag_color, reverse=True)
        outline = round_corners(outline)
        card.paste(outline, (41, 100), outline)

        bg_dir = "images/profile_backgrounds"
        bg_path = f"{bg_dir}/{bg_index}.png"
        try:
            bg_img = Image.open(bg_path).convert("RGBA")
        except FileNotFoundError:
            # Fallback to default background 1.png
            try:
                bg_img = Image.open(f"{bg_dir}/1.png").convert("RGBA")
            except Exception:
                bg_img = Image.new("RGBA", (818, 545), (0, 0, 0, 100))  # ultimate fallback
        bg_img = round_corners(bg_img, radius=20)
        card.paste(bg_img, (50, 110), bg_img)

    def _draw_player_header(self, card: Image.Image, draw: ImageDraw.ImageDraw, player: Dict, tag_color: str) -> None:
        """Name text + avatar bust."""
        # Name
        name_font = ImageFont.truetype('images/profile/game.ttf', 50)
        username = player.get("username") or player.get("displayName") or "Unknown"
        addLine(text=username, draw=draw, font=name_font, x=50, y=40, drop_x=7, drop_y=7)

        # Avatar
        uuid = player.get("uuid", "")
        try:
            headers = {'User-Agent': os.getenv("visage_UA", "")}
            av_url = f"https://visage.surgeplay.com/bust/500/{uuid}"
            resp = requests.get(av_url, headers=headers, timeout=6)
            skin = Image.open(BytesIO(resp.content)).convert('RGBA')
        except Exception:
            skin = Image.open('images/profile/x-steve500.png').convert('RGBA')
        skin.thumbnail((480, 480))
        card.paste(skin, (200, 156), skin)

    def _draw_rank_badge(self, card: Image.Image, player: Dict, tag_color: str) -> None:
        """Centered support/rank badge like profile card."""
        rank_tag = player.get("supportRank") or player.get("rank") or "Player"
        rank_badge = generate_rank_badge(rank_tag, tag_color)
        w, h = rank_badge.size
        card.paste(rank_badge, (450 - w // 2, 96), rank_badge)

    def _draw_guild_elements(self, card: Image.Image, player: Dict, fallback_color: str) -> None:
        guild_info = player.get("guild")
        if not guild_info:
            return

        gname = guild_info.get('name', '')
        # Derive a readable color from banner
        try:
            banner = getData(gname)['banner']
            base = banner.get('base')
            if base in ['BLACK', 'GRAY', 'BROWN']:
                colour = next(layer['colour'] for layer in banner['layers'] if layer['colour'] not in ['BLACK', 'GRAY', 'BROWN'])
            else:
                colour = base
        except Exception:
            colour = 'WHITE'

        # Guild name badge
        rgb = minecraft_banner_colors.get(colour, (255, 255, 255))
        g_badge = generate_badge(text=gname,
                                 base_color='#{:02x}{:02x}{:02x}'.format(*rgb),
                                 scale=3)
        g_badge.crop(g_badge.getbbox())
        card.paste(g_badge, (108, 615), g_badge)

        # Decide which rank system to use for the badge
        use_taq = gname.lower() == "the aquarium" or guild_info.get('prefix', '').lower() == 'taq'

        gr_text = guild_info.get('rank', '').upper()
        gr_color = '#a0aeb0'

        if use_taq:
            # Pull TAq-specific discord rank from backend
            disc_rank = None
            try:
                db = DB(); db.connect()
                db.cursor.execute("SELECT rank FROM discord_links WHERE uuid = %s", (player.get('uuid'),))
                row = db.cursor.fetchone()
                if row:
                    disc_rank = row[0]
            finally:
                try:
                    db.close()
                except Exception:
                    pass

            if disc_rank and disc_rank in discord_ranks:
                gr_text = disc_rank.upper()
                gr_color = discord_ranks[disc_rank]['color']
        else:
            # Fallback: use Wynn guild rank color mapping (discord_ranks if available)
            gr_key = gr_text.lower()
            gr_color = discord_ranks.get(gr_key, {}).get('color', '#a0aeb0')

        gr_badge = generate_badge(text=gr_text, base_color=gr_color, scale=3)
        gr_badge.crop(gr_badge.getbbox())
        card.paste(gr_badge, (108, 667), gr_badge)

        # Minecraft-style banner icon
        try:
            bn = generate_banner(gname, 15, "2")
            bn.thumbnail((157, 157))
            bn = bn.convert('RGBA')
            card.paste(bn, (41, 562), bn)
        except Exception:
            pass

    def _draw_raid_boxes(self, card: Image.Image, draw: ImageDraw.ImageDraw, stats: List[Tuple[str, int, int]]) -> None:
        """Draw the 4 raid stat boxes in a 2x2 grid with padding, full-height icon column, and colored outlines by rank."""
        title_font = ImageFont.truetype('images/profile/5x5.ttf', 54)
        data_font = ImageFont.truetype('images/profile/game.ttf', 68)

        # Dimensions
        box_w, box_h = 390, 200
        row_pad = 12  # vertical gap between rows
        left_col_w = 200  # width reserved for the icon column
        corner_radius = 20

        # Outline colors (hex)
        GOLD = '#ffd700'
        SILVER = '#c0c0c0'
        BRONZE = '#cd7f32'

        # Load icons once, will resize per-box to fit height
        icons: Dict[str, Image.Image] = {}
        for abbr, _, _ in self.RAIDS:
            try:
                icon_img = Image.open(f"images/raids/{abbr}.png").convert('RGBA')
            except FileNotFoundError:
                icon_img = Image.new('RGBA', (60, 60), (0, 0, 0, 0))
            icons[abbr] = icon_img

        start_x, start_y = 50, 730
        x_gap = 410
        y_gap = box_h + row_pad

        for i, (abbr, rank_val, count) in enumerate(stats):
            x = start_x + (i % 2) * x_gap
            y = start_y + (i // 2) * y_gap

            # Determine outline color
            outline_color = None
            if rank_val and rank_val > 0:
                if rank_val <= 10:
                    outline_color = GOLD
                elif rank_val <= 50:
                    outline_color = SILVER
                elif rank_val <= 100:
                    outline_color = BRONZE

            # Build box
            box = Image.new('RGBA', (box_w, box_h), (0, 0, 0, 0))
            bdraw = ImageDraw.Draw(box)
            if outline_color:
                bdraw.rounded_rectangle((0, 0, box_w, box_h), radius=corner_radius, fill=(0, 0, 0, 30), outline=outline_color, width=6)
            else:
                bdraw.rounded_rectangle((0, 0, box_w, box_h), radius=corner_radius, fill=(0, 0, 0, 30))

            # Paste icon filling left column
            icon = icons[abbr].copy()
            # Fit icon to (left_col_w - margin) x (box_h - margin)
            margin = 10
            icon.thumbnail((left_col_w - margin*2, box_h - margin*2))
            ic_x = margin
            ic_y = (box_h - icon.height) // 2
            box.paste(icon, (ic_x, ic_y), icon)

            # Text on right half
            right_x0 = left_col_w  # start of right area
            right_cx = right_x0 + (box_w - right_x0) // 2

            box_draw = ImageDraw.Draw(box)
            if not rank_val or rank_val == 0:
                rank_text = 'N/A'
            else:
                rank_text = f"#{rank_val}"
            count_text = str(count)

            # Measure text heights to center the block and keep equal top/bottom padding
            r_bbox = box_draw.textbbox((0, 0), rank_text, font=title_font)
            c_bbox = box_draw.textbbox((0, 0), count_text, font=data_font)
            rank_h = r_bbox[3] - r_bbox[1]
            count_h = c_bbox[3] - c_bbox[1]

            gap_between = 20
            group_h = rank_h + gap_between + count_h
            top_y = (box_h - group_h) // 2

            rank_center_y = top_y + rank_h / 2
            count_center_y = top_y + rank_h + gap_between + count_h / 2

            box_draw.text((right_cx, rank_center_y), rank_text, fill='#fad51e', font=title_font, anchor='mm')
            box_draw.text((right_cx, count_center_y), count_text, fill='white', font=data_font, anchor='mm')

            # Composite box onto card
            card.paste(box, (x, y), box)

        
        # ------------------------- Cog setup ------------------------------------
    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client: commands.Bot):
    client.add_cog(Raids(client))
