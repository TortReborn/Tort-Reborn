import os
import time
from io import BytesIO
from urllib.parse import quote
from typing import Dict, List, Tuple, Optional

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
    create_progress_bar,
)
from Helpers.database import DB
from Helpers.variables import (
    discord_ranks,
    minecraft_banner_colors,
    guilds,
    wynn_ranks,
)

# Tunable elo weights
ELO_WEIGHTS = {
    "level": 5,          # totalLevel
    "war": 5,            # wars
    "dungeon": 5,        # dungeons total
    "raid": 13,          # raids total
    "quest": 5,          # completedQuests
    "discovery": 1,      # discoveries (if available)
    "death": -10,        # deaths
}

# Elo tiers (generated: 6 ranks * 5 divisions each)
# We build them from anchor ranges so it’s easy to tune later.
TIER_ANCHORS: List[Tuple[str, int, int]] = [
    ("Bronze",   0,     5000),    # 0  - 4,999
    ("Silver",   5000,  20000),   # 5k - 19,999
    ("Gold",     20000, 60000),   # 20k - 59,999
    ("Diamond",  60000, 120000),  # 60k - 119,999
    ("Emerald",  120000, 240000), # 120k - 239,999
    ("Dernic",   240000, 480000), # 240k - 479,999
]

def _build_tiers() -> List[Tuple[int, str]]:
    tiers: List[Tuple[int, str]] = []
    roman = ["I", "II", "III", "IV", "V"]
    for name, low, high in TIER_ANCHORS:
        span = (high - low) / 5
        for i in range(5):
            lower = int(low + span * i)
            label = f"{name} {roman[i]}"  # 1 = lowest division for that rank, 5 = highest
            tiers.append((lower, label))
    return tiers

ELO_TIERS: List[Tuple[int, str]] = _build_tiers()

# Rank color accents (border & title text)
RANK_COLORS = {
    "Bronze":  "#cd7f32",
    "Silver":  "#c0c0c0",
    "Gold":    "#ffd700",
    "Diamond": "#5ad0ff",
    "Emerald": "#55ff55",
    "Dernic":  "#c08cff",
}


class Rank(commands.Cog):
    """/rank <username>   —   Elo & stats card."""

    def __init__(self, client: commands.Bot):
        self.client = client

    # ------------------------------------------------------------------
    @slash_command(
        name="rank",
        description="Show Elo and key stats for a player",
        guild_ids=guilds,
    )
    async def rank(self,
                   ctx: discord.ApplicationContext,
                   name: Option(str, "Minecraft username", required=True)):
        await ctx.defer()

        # 1) Fetch player data
        player = await self._fetch_player(name)
        if player is None:
            return await ctx.followup.send(f"❌ Could not fetch data for `{name}`.", ephemeral=True)

        # 2) Customization (same logic as raids)
        tag_color, (grad_start, grad_end), bg_index = self._resolve_customization(player)

        # 3) Compute Elo & tier
        elo = self._compute_elo(player)
        tier = self._tier_from_elo(elo)

        # 4) Collect stats for grid
        stats_dict = self._collect_stats(player)

        # 5) Build the card
        card = self._build_base_canvas(tag_color, grad_start, grad_end)
        draw = ImageDraw.Draw(card)

        # Background block
        self._draw_background(card, tag_color, bg_index)

        # Header (name + avatar)
        self._draw_player_header(card, draw, player)

        # Wynn rank badge (supportRank/rank)
        self._draw_rank_badge(card, player, tag_color)

        # Guild badges & banner (with TAq override)
        self._draw_guild_elements(card, player)

        # Info boxes: Elo big box + 2x3 grid
        self._draw_info_boxes(card, draw, elo, tier, stats_dict)

        # Send
        buf = BytesIO(); card.save(buf, format='PNG'); buf.seek(0)
        fn = f"rank_{player.get('username', name)}_{int(time.time())}.png"
        await ctx.followup.send(file=discord.File(buf, filename=fn))

    # ------------------------------------------------------------------
    async def _fetch_player(self, name: str) -> Optional[Dict]:
        safe = quote(name)
        url = f"https://api.wynncraft.com/v3/player/{safe}?fullResult"
        try:
            r = requests.get(url, timeout=10, headers={"Authorization": f"Bearer {os.getenv("WYNN_TOKEN")}"})
        except requests.RequestException:
            return None
        if r.status_code != 200:
            return None
        payload = r.json()
        data = payload.get("data") or ([payload] if payload.get("username") else [])
        return data[0] if data else None

    def _resolve_customization(self, player: Dict):
        """
        Use same rules as /raids: if the player is not present in our backend, use defaults.
        """
        default_grad = ("#293786", "#1d275e")
        default_bg = 1

        # Border color from Wynn ranks
        w_rank_key = (player.get("supportRank") or player.get("rank") or "").lower()
        tag_color = wynn_ranks.get(w_rank_key, {}).get("color", "#293786")

        grad_start, grad_end = default_grad
        bg_index = default_bg

        uuid = player.get("uuid")
        if not uuid:
            return tag_color, (grad_start, grad_end), bg_index

        db = DB(); db.connect()
        try:
            db.cursor.execute("SELECT discord_id FROM discord_links WHERE uuid = %s", (uuid,))
            row = db.cursor.fetchone()
            if row:
                target = row[0]
                db.cursor.execute('SELECT background, gradient FROM profile_customization WHERE "user" = %s', (target,))
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

    def _compute_elo(self, player: Dict) -> int:
        g = player.get("globalData", {}) or {}
        # safe ints (None -> 0)
        total_level = int(g.get("totalLevel", g.get("totalLevels", 0)) or 0)
        wars        = int(g.get("wars", 0) or 0)
        dungeons    = int((g.get("dungeons", {}) or {}).get("total", 0) or 0)
        raids       = int((g.get("raids", {}) or {}).get("total", 0) or 0)
        quests      = int(g.get("completedQuests", 0) or 0)

        # Per-character pulls (fullResult)
        chars = player.get("characters") or {}
        char_deaths = 0
        discoveries  = 0
        for ch in chars.values():
            if isinstance(ch, dict):
                char_deaths += int(ch.get("deaths") or 0)
                discoveries  += int(ch.get("discoveries") or 0)
        if char_deaths == 0:
            char_deaths = int((g.get("pvp", {}) or {}).get("deaths", 0) or 0)

        elo = (
            total_level * ELO_WEIGHTS["level"] +
            wars        * ELO_WEIGHTS["war"] +
            dungeons    * ELO_WEIGHTS["dungeon"] +
            raids       * ELO_WEIGHTS["raid"] +
            quests      * ELO_WEIGHTS["quest"] +
            discoveries * ELO_WEIGHTS["discovery"] +
            char_deaths * ELO_WEIGHTS["death"]
        )
        return int(elo)

    def _tier_from_elo(self, elo: int) -> str:
        name = ELO_TIERS[0][1]
        for threshold, tier_name in ELO_TIERS:
            if elo >= threshold:
                name = tier_name
            else:
                break
        return name

    def _collect_stats(self, player: Dict) -> Dict[str, int]:
        g = player.get("globalData", {}) or {}
        chars = player.get("characters") or {}
        char_deaths = sum(int(ch.get("deaths") or 0) for ch in chars.values() if isinstance(ch, dict))
        if char_deaths == 0:
            char_deaths = g.get("pvp", {}).get("deaths", 0)
        return {
            "Total Level": g.get("totalLevel", g.get("totalLevels", 0)),
            "Wars": g.get("wars", 0),
            "Dungeons": g.get("dungeons", {}).get("total", 0),
            "Raids": g.get("raids", {}).get("total", 0),
            "Quests": g.get("completedQuests", 0),
            "Deaths": char_deaths,
        }

    # ------------------------------------------------------------------ Drawing
    def _build_base_canvas(self, tag_color: str, grad_start: str, grad_end: str) -> Image.Image:
        card = vertical_gradient(main_color=tag_color)
        card = round_corners(card)

        overlay = vertical_gradient(width=850, height=1130, main_color=grad_start, secondary_color=grad_end)
        card.paste(overlay, (25, 25), overlay)
        return card

    def _draw_background(self, card: Image.Image, tag_color: str, bg_index: int) -> None:
        outline = vertical_gradient(width=818, height=545, main_color=tag_color, reverse=True)
        outline = round_corners(outline)
        card.paste(outline, (41, 100), outline)

        bg_dir = "images/profile_backgrounds"
        bg_path = f"{bg_dir}/{bg_index}.png"
        try:
            bg_img = Image.open(bg_path).convert('RGBA')
        except FileNotFoundError:
            try:
                bg_img = Image.open(f"{bg_dir}/1.png").convert('RGBA')
            except Exception:
                bg_img = Image.new('RGBA', (818, 545), (0, 0, 0, 100))
        bg_img = round_corners(bg_img, radius=20)
        card.paste(bg_img, (50, 110), bg_img)

    def _draw_player_header(self, card: Image.Image, draw: ImageDraw.ImageDraw, player: Dict) -> None:
        name_font = ImageFont.truetype('images/profile/game.ttf', 50)
        username = player.get('username') or player.get('displayName') or 'Unknown'
        addLine(text=username, draw=draw, font=name_font, x=50, y=40, drop_x=7, drop_y=7)

        uuid = player.get('uuid', '')
        try:
            headers = {'User-Agent': os.getenv('visage_UA', '')}
            av_url = f"https://visage.surgeplay.com/bust/500/{uuid}"
            resp = requests.get(av_url, headers=headers, timeout=6)
            skin = Image.open(BytesIO(resp.content)).convert('RGBA')
        except Exception:
            skin = Image.open('images/profile/x-steve500.png').convert('RGBA')
        skin.thumbnail((480, 480))
        card.paste(skin, (200, 156), skin)

    def _draw_rank_badge(self, card: Image.Image, player: Dict, tag_color: str) -> None:
        rank_tag = player.get('supportRank') or player.get('rank') or 'Player'
        badge = generate_rank_badge(rank_tag, tag_color)
        w, h = badge.size
        card.paste(badge, (450 - w // 2, 96), badge)

    def _draw_guild_elements(self, card: Image.Image, player: Dict) -> None:
        guild_info = player.get('guild')
        if not guild_info:
            return
        gname = guild_info.get('name', '')
        # Derive readable color from banner
        try:
            banner = getData(gname)['banner']
            base = banner.get('base')
            if base in ['BLACK', 'GRAY', 'BROWN']:
                colour = next(l['colour'] for l in banner['layers'] if l['colour'] not in ['BLACK', 'GRAY', 'BROWN'])
            else:
                colour = base
        except Exception:
            colour = 'WHITE'

        rgb = minecraft_banner_colors.get(colour, (255, 255, 255))
        g_badge = generate_badge(text=gname,
                                 base_color='#{:02x}{:02x}{:02x}'.format(*rgb),
                                 scale=3)
        g_badge.crop(g_badge.getbbox())
        card.paste(g_badge, (108, 615), g_badge)

        # TAq override
        use_taq = gname.lower() == "the aquarium" or guild_info.get('prefix', '').lower() == 'taq'
        gr_text = guild_info.get('rank', '').upper()
        gr_color = '#a0aeb0'

        if use_taq:
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
            gr_key = gr_text.lower()
            gr_color = discord_ranks.get(gr_key, {}).get('color', '#a0aeb0')

        gr_badge = generate_badge(text=gr_text, base_color=gr_color, scale=3)
        gr_badge.crop(gr_badge.getbbox())
        card.paste(gr_badge, (108, 667), gr_badge)

        try:
            bn = generate_banner(gname, 15, "2")
            bn.thumbnail((157, 157))
            bn = bn.convert('RGBA')
            card.paste(bn, (41, 562), bn)
        except Exception:
            pass

    def _draw_info_boxes(self, card: Image.Image, draw: ImageDraw.ImageDraw, elo: int, tier: str, stats: Dict[str, int]) -> None:
        """
        Top wide box: tier GUI with badges + Minecraft‑styled progress bar.
        - Left: big badge of current rank.
        - Right: smaller badge of next rank.
        - Between: rectangular orange bar with black outline and darker 10% ticks.
          The fill has 5 vertical components (black outline already counted, then light orange, orange, dark orange, and the opposite side light again)
          to give a chunky MC look. Ticks remain visible on top of the fill.
        - Current Elo (small) is shown left of the bar. Threshold numbers (prev & next) are shown BELOW the bar edges.
        - Tier name uses the rank color and a drop shadow.
        Below: 2x3 stat grid; "Deaths" value red.
        """
        # Fonts
        tier_font = ImageFont.truetype('images/profile/5x5.ttf', 64)
        small_number_font = ImageFont.truetype('images/profile/5x5.ttf', 26)
        threshold_font = ImageFont.truetype('images/profile/5x5.ttf', 52)
        title_font = ImageFont.truetype('images/profile/5x5.ttf', 32)
        value_font = ImageFont.truetype('images/profile/game.ttf', 34)

        # Geometry constants
        info_start_y = 730
        total_width = 818
        left_x = 50
        box_gap_x = 20
        box_gap_y = 8

        # Elo/Tier box geometry (taller for margins balance)
        elo_box_h = 160
        elo_box = Image.new('RGBA', (total_width, elo_box_h), (0, 0, 0, 0))
        edraw = ImageDraw.Draw(elo_box)

        # Determine tier base & colors
        tier_base = tier.split()[0]
        rank_color = RANK_COLORS.get(tier_base, '#fad51e')
        # Border
        edraw.rounded_rectangle((0, 0, total_width, elo_box_h), radius=20, fill=(0, 0, 0, 30), outline=rank_color, width=6)

        # --- Determine thresholds & progress ---
        cur_idx = 0
        for i, (thr, name_lbl) in enumerate(ELO_TIERS):
            if tier == name_lbl:
                cur_idx = i
                break
        prev_thr = ELO_TIERS[cur_idx][0]
        if cur_idx + 1 < len(ELO_TIERS):
            next_thr = ELO_TIERS[cur_idx + 1][0]
            next_label = ELO_TIERS[cur_idx + 1][1]
        else:
            next_thr = max(prev_thr + 1, elo)
            next_label = tier
        span = max(next_thr - prev_thr, 1)
        progress = max(0.0, min(1.0, (elo - prev_thr) / span))

        # Badges
        def load_badge(base: str, size: int) -> Image.Image:
            path = f"images/elo_badges/{base.lower()}.png"
            try:
                im = Image.open(path).convert('RGBA')
            except Exception:
                im = Image.new('RGBA', (16, 16), (0, 0, 0, 0))
            return im.resize((size, size), Image.NEAREST)

        big_badge_size = 64
        small_badge_size = 42
        big_badge = load_badge(tier_base, big_badge_size)
        small_badge = load_badge(next_label.split()[0], small_badge_size) if next_label else Image.new('RGBA', (16, 16), (0,0,0,0))

        margin = 20
        big_x = margin
        big_y = (elo_box_h - big_badge_size) // 2
        small_x = total_width - margin - small_badge_size
        small_y = (elo_box_h - small_badge_size) // 2

        # Progress bar 
        avail_left = big_x + big_badge_size + 24
        avail_right = small_x - 24
        avail_width = max(10, avail_right - avail_left)
        bar_width = int(avail_width * 0.85)
        bar_x0 = avail_left + (avail_width - bar_width) // 2
        bar_x1 = bar_x0 + bar_width
        perc = int(progress * 100)
        # ensure valid hex colour for helper
        prog_color = '#cc7700'
        try:
            bar_img = create_progress_bar(bar_width, perc, prog_color, scale=1)
        except Exception:
            # fallback: 0% bar
            bar_img = create_progress_bar(bar_width, 0, prog_color, scale=2)
        bar_h = bar_img.height
        bar_y = (elo_box_h - bar_h) // 2
        elo_box.paste(bar_img, (bar_x0, bar_y), bar_img)

        # Tier text with shadow (centered over bar)
        cx = (bar_x0 + bar_x1) // 2
        tier_text = tier
        edraw.text((cx + 2, bar_y - 48 + 2), tier_text, font=tier_font, anchor='mm', fill='black')
        edraw.text((cx,     bar_y - 48),      tier_text, font=tier_font, anchor='mm', fill=rank_color)

        # Threshold numbers BELOW bar (left = current Elo, right = next threshold)
        below_y = bar_y + bar_h + 28
        edraw.text((bar_x0, below_y), str(elo), font=threshold_font, fill='#ffff55', anchor='ls')
        edraw.text((bar_x1, below_y), str(next_thr), font=threshold_font, fill='white', anchor='rs')

        # Paste badges on top
        elo_box.paste(big_badge, (big_x, big_y), big_badge)
        elo_box.paste(small_badge, (small_x, small_y), small_badge)

        card.paste(elo_box, (left_x, info_start_y), elo_box)

        # --- Grid boxes ---
        grid_y = info_start_y + elo_box_h + box_gap_y
        col_w = (total_width - box_gap_x) // 2
        row_h = 80
        base_box = Image.new('RGBA', (col_w, row_h), (0, 0, 0, 0))
        bdraw = ImageDraw.Draw(base_box)
        bdraw.rounded_rectangle((0, 0, col_w, row_h), radius=15, fill=(0, 0, 0, 30))

        keys = list(stats.keys())
        for idx, key in enumerate(keys):
            r = idx // 2
            c = idx % 2
            bx = left_x + c * (col_w + box_gap_x)
            by = grid_y + r * (row_h + box_gap_y)

            box = base_box.copy()
            b2 = ImageDraw.Draw(box)

            title_y = 12
            value_y = row_h // 2 + 10

            # Stat title
            b2.text((20, title_y), key.upper(), fill='#fad51e', font=title_font)
            # Value
            val_str = str(stats[key])
            val_color = '#ff5555' if key == 'Deaths' else 'white'
            b2.text((col_w - 20, value_y), val_str, fill=val_color, font=value_font, anchor='rt')

            card.paste(box, (bx, by), box)

    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client: commands.Bot):
    client.add_cog(Rank(client))
