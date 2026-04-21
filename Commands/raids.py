import asyncio
import colorsys
import os
import time
from io import BytesIO
from urllib.parse import quote
from typing import Dict, List, Tuple

import requests
import discord
from discord.ext import commands
from discord.commands import slash_command, Option
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageColor

from Helpers.functions import (
    round_corners,
    addLine,
    generate_rank_badge,
    generate_banner,
    getData,
    generate_badge,
)
from Helpers.classes import PlayerStats
from Helpers.variables import discord_ranks, minecraft_banner_colors
from Helpers.rate_limiter import external_rate_limit
from Helpers.storage import get_background, get_cached_avatar, save_cached_avatar

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
    Generates a Raid Stats Card
    """

    CARD_W = 1300
    CARD_H = 720
    FONT_CACHE: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}

    # (abbr, display name, API count aliases, possible ranking keys, fallback ranking fragments)
    RAIDS: List[Tuple[str, str, Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]] = [
        ("NOTG", "Nest of the Grootslangs", ("Nest of the Grootslangs",), ("grootslangSrPlayers",), ("grootslang",)),
        ("NOL",  "Orphion's Nexus of Light", ("Orphion's Nexus of Light",), ("orphionSrPlayers",), ("orphion",)),
        ("TCC",  "The Canyon Colossus", ("The Canyon Colossus",), ("colossusSrPlayers",), ("colossus",)),
        ("TNA",  "The Nameless Anomaly", ("The Nameless Anomaly",), ("namelessSrPlayers",), ("nameless", "anomaly")),
        ("WTP",  "The Queen's Wartorn Palace", ("The Wartorn Palace", "Wartorn Palace"), ("wartornSrPlayers", "wartornPalaceSrPlayers", "palaceSrPlayers"), ("wartorn", "palace")),
    ]
    # Temporary API shim: until Wynn exposes Queen's Wartorn Palace by name,
    # treat the generic "unknown" bucket as WTP, but only if no explicit WTP key exists.
    TEMP_RAID_COUNT_FALLBACKS: Dict[str, Tuple[str, ...]] = {
        "WTP": ("unknown",),
    }

    def __init__(self, client: commands.Bot):
        self.client = client

    # ------------------------- Slash Command -------------------------------
    @slash_command(
        name="raids",
        description="Show raid rankings and counts for a player",
        integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install},
        contexts={discord.InteractionContextType.guild, discord.InteractionContextType.bot_dm, discord.InteractionContextType.private_channel},
    )
    @external_rate_limit()
    async def raids(self,
                    ctx: discord.ApplicationContext,
                    name: Option(str, "Minecraft username", required=True)):
        try:
            await ctx.defer()
        except discord.NotFound:
            # Discord invalidates interactions that are not acknowledged within ~3 seconds.
            # Once that happens, there is no valid token left to respond with.
            return

        # 1) Fetch player data using PlayerStats (like profile.py) ----------------
        try:
            player_stats = await asyncio.to_thread(PlayerStats, name, 7)  # 7 days for compatibility
            if player_stats.error:
                embed = discord.Embed(
                    title=':no_entry: Oops! Something did not go as intended.',
                    description=f'Could not retrieve information of `{name}`.\nPlease check your spelling or try again later.',
                    color=0xe33232
                )
                await ctx.followup.send(embed=embed, ephemeral=True)
                return
        except Exception:
            embed = discord.Embed(
                title=':no_entry: Error',
                description=f'Could not retrieve information of `{name}`.',
                color=0xe33232
            )
            await ctx.followup.send(embed=embed, ephemeral=True)
            return

        # 2) Fetch raw player data for raid stats ----------------------------------
        player = await self._fetch_player(name)
        if player is None:
            embed = discord.Embed(
                title=':no_entry: Player not found',
                description=f'Could not find player `{name}`.',
                color=0xe33232
            )
            await ctx.followup.send(embed=embed, ephemeral=True)
            return

        # 3) Use PlayerStats for customization and formatting ----------------------
        tag_color = player_stats.tag_color
        grad_start, grad_end = player_stats.gradient
        bg_index = player_stats.background

        # 4) Extract raid stats ---------------------------------------------------
        stats = self._extract_raid_stats(player)
        summary = self._summarize_raid_stats(stats)

        # 5) Build the card image -------------------------------------------------
        card = await asyncio.to_thread(
            self._render_card,
            player,
            player_stats,
            stats,
            summary,
            tag_color,
            grad_start,
            grad_end,
            bg_index,
        )

        # 6) Send -----------------------------------------------------------------
        buf = BytesIO()
        card.save(buf, format="PNG")
        buf.seek(0)
        filename = f"raids_{player.get('username', name)}_{int(time.time())}.png"
        await ctx.followup.send(file=discord.File(buf, filename=filename))

    # ------------------------- Helpers -------------------------------------
    async def _fetch_player(self, name: str) -> Dict:
        """Fetch player JSON or return None on error."""
        return await asyncio.to_thread(self._fetch_player_sync, name)

    def _fetch_player_sync(self, name: str) -> Dict:
        """Fetch player JSON or return None on error."""
        safe_name = quote(name)
        url = f"https://api.wynncraft.com/v3/player/{safe_name}"
        try:
            res = requests.get(url, timeout=10, headers={"Authorization": f"Bearer {os.getenv('WYNN_TOKEN')}"})
        except requests.RequestException:
            return None
        if res.status_code != 200:
            return None
        payload = res.json()
        data = payload.get("data") or ([payload] if payload.get("username") else [])
        return data[0] if data else None

    def _render_card(self, player: Dict, player_stats: PlayerStats, stats: List[Dict],
                     summary: Dict[str, str], tag_color: str, grad_start: str,
                     grad_end: str, bg_index: int) -> Image.Image:
        """Render the PIL card in a worker thread to avoid blocking Discord ACKs."""
        card = self._build_base_canvas(tag_color, grad_start, grad_end)
        draw = ImageDraw.Draw(card)

        self._draw_background(card, tag_color, bg_index)
        self._draw_player_header(card, draw, player, tag_color)
        self._draw_rank_badge(card, player_stats, tag_color)
        self._draw_guild_elements(card, player, player_stats)
        self._draw_raid_panel(card, draw, stats, summary, tag_color)
        return card

    def _extract_raid_stats(self, player: Dict) -> List[Dict]:
        """Return raid stats in display order."""
        ranking = player.get("ranking", {})
        raids_list = player.get("globalData", {}).get("raids", {}).get("list", {})
        stats: List[Dict] = []
        for abbr, full, aliases, rank_keys, rank_fragments in self.RAIDS:
            rank_val = self._lookup_rank(ranking, rank_keys, rank_fragments)
            count = self._lookup_raid_count(
                raids_list,
                aliases,
                self.TEMP_RAID_COUNT_FALLBACKS.get(abbr, ()),
            )
            stats.append({
                "abbr": abbr,
                "name": full,
                "rank": rank_val,
                "count": count,
            })
        return stats

    @staticmethod
    def _lookup_raid_count(
        raids_list: Dict,
        aliases: Tuple[str, ...],
        fallback_aliases: Tuple[str, ...] = (),
    ) -> int:
        for name in aliases:
            if name in raids_list:
                return raids_list.get(name, 0) or 0

        normalized = {
            key.lower().replace("the ", "").strip(): value
            for key, value in raids_list.items()
        }
        for name in aliases:
            key = name.lower().replace("the ", "").strip()
            if key in normalized:
                return normalized.get(key, 0) or 0

        for name in fallback_aliases:
            if name in raids_list:
                return raids_list.get(name, 0) or 0

        for name in fallback_aliases:
            key = name.lower().replace("the ", "").strip()
            if key in normalized:
                return normalized.get(key, 0) or 0
        return 0

    @staticmethod
    def _lookup_rank(ranking: Dict, rank_keys: Tuple[str, ...], fragments: Tuple[str, ...]) -> int:
        for key in rank_keys:
            value = ranking.get(key)
            if value:
                return value

        for key, value in ranking.items():
            key_l = key.lower()
            if "srplayers" not in key_l:
                continue
            if any(fragment in key_l for fragment in fragments):
                return value or 0
        return 0

    @staticmethod
    def _summarize_raid_stats(stats: List[Dict]) -> Dict[str, str]:
        total = sum(item["count"] for item in stats)
        counted = [item for item in stats if item["count"] > 0]
        ranked = [item for item in stats if item["rank"]]

        favorite = max(counted, key=lambda item: item["count"]) if counted else None
        best = min(ranked, key=lambda item: item["rank"]) if ranked else None
        top_100 = sum(1 for item in ranked if item["rank"] <= 100)

        return {
            "total": str(total),
            "favorite": favorite["abbr"] if favorite else "N/A",
            "best_rank": f'#{best["rank"]} {best["abbr"]}' if best else "N/A",
            "top_100": str(top_100),
        }

    # ------------------------- Drawing pieces ---------------------------------
    def _build_base_canvas(self, tag_color: str, grad_start: str, grad_end: str) -> Image.Image:
        """Create outer border + inner gradient panel with rounded corners."""
        card = self._fast_vertical_gradient(width=self.CARD_W, height=self.CARD_H, main_color=tag_color)
        card = round_corners(card)

        # 25px border matches profile card style -- no separate round_corners on overlay
        overlay = self._fast_vertical_gradient(width=self.CARD_W - 50, height=self.CARD_H - 50,
                                               main_color=grad_start,
                                               secondary_color=grad_end)
        card.paste(overlay, (25, 25), overlay)
        return card

    def _draw_background(self, card: Image.Image, tag_color: str, bg_index: int) -> None:
        """Outline rect + user-selected background PNG."""
        # Default radius=25 matches profile's bg_outline style
        outline = self._fast_vertical_gradient(width=438, height=545, main_color=tag_color, reverse=True)
        outline = round_corners(outline)
        card.paste(outline, (41, 100), outline)

        bg_img = get_background(bg_index)
        bg_img = self._cover_resize(bg_img, 418, 525)
        bg_img = round_corners(bg_img, radius=20)
        card.paste(bg_img, (50, 110), bg_img)

    def _draw_player_header(self, card: Image.Image, draw: ImageDraw.ImageDraw, player: Dict, tag_color: str) -> None:
        """Name text + avatar bust."""
        username = player.get("username") or player.get("displayName") or "Unknown"
        name_font = self._fit_font(username, draw, 'images/profile/game.ttf', 50, 430, min_size=28)
        addLine(text=username, draw=draw, font=name_font, x=50, y=40, drop_x=7, drop_y=7)

        # Avatar
        uuid = player.get("uuid", "")
        try:
            if not uuid:
                raise ValueError("Missing player UUID")

            cached = get_cached_avatar(uuid)
            if cached:
                skin = Image.open(BytesIO(cached)).convert('RGBA')
            else:
                headers = {'User-Agent': os.getenv("visage_UA", "")}
                av_url = f"https://visage.surgeplay.com/bust/500/{uuid}"
                resp = requests.get(av_url, headers=headers, timeout=6)
                resp.raise_for_status()
                try:
                    save_cached_avatar(uuid, resp.content)
                except Exception:
                    pass
                skin = Image.open(BytesIO(resp.content)).convert('RGBA')
        except Exception:
            skin = Image.open('images/profile/x-steve500.png').convert('RGBA')
        skin.thumbnail((480, 480))
        card.paste(skin, (20, 156), skin)

    def _draw_rank_badge(self, card: Image.Image, player_stats: PlayerStats, tag_color: str) -> None:
        """Centered support/rank badge like profile card."""
        rank_badge = generate_rank_badge(player_stats.tag_display, tag_color)
        rank_badge = self._fit_badge_width(rank_badge, 380)
        w, h = rank_badge.size
        # Center on portrait area (x=41 to x=479, center=260) to match profile centering logic
        card.paste(rank_badge, (260 - w // 2, 96), rank_badge)

    def _draw_guild_elements(self, card: Image.Image, player: Dict, player_stats: PlayerStats) -> None:
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
        g_bbox = g_badge.getbbox()
        if g_bbox:
            g_badge = g_badge.crop(g_bbox)
        g_badge = self._fit_badge_width(g_badge, 360)

        # Decide which rank system to use for the badge
        use_taq = gname.lower() == "the aquarium" or guild_info.get('prefix', '').lower() == 'taq'

        gr_text = guild_info.get('rank') or getattr(player_stats, 'guild_rank', '') or ''
        gr_color = '#a0aeb0'

        if use_taq:
            disc_rank = getattr(player_stats, 'rank', None)
            if getattr(player_stats, 'linked', False) and disc_rank in discord_ranks:
                gr_text = disc_rank
                gr_color = discord_ranks[disc_rank]['color']
            elif getattr(player_stats, 'guild_rank', None):
                gr_text = player_stats.guild_rank
        else:
            # Fallback: use Wynn guild rank color mapping (discord_ranks if available)
            gr_key = str(gr_text).lower()
            gr_color = discord_ranks.get(gr_key, {}).get('color', '#a0aeb0')

        gr_badge = generate_badge(text=str(gr_text).upper(), base_color=gr_color, scale=3)
        gr_bbox = gr_badge.getbbox()
        if gr_bbox:
            gr_badge = gr_badge.crop(gr_bbox)
        gr_badge = self._scale_badge(gr_badge, 0.82, 320)

        # Minecraft-style banner icon
        try:
            bn = generate_banner(gname, 15, "2")
            bn.thumbnail((157, 157))
            bn = bn.convert('RGBA')
        except Exception:
            bn = None

        guild_badge_x = 108
        guild_badge_y = 620
        card.paste(g_badge, (guild_badge_x, guild_badge_y), g_badge)
        card.paste(gr_badge, (guild_badge_x, guild_badge_y + g_badge.height), gr_badge)
        if bn is not None:
            card.paste(bn, (41, 538), bn)

    def _draw_raid_panel(self, card: Image.Image, draw: ImageDraw.ImageDraw, stats: List[Dict],
                         summary: Dict[str, str], tag_color: str) -> None:
        """Draw the wide raid panel and all five raid cards."""
        panel_x = 520
        panel_y = 42
        panel_w = self.CARD_W - panel_x - 38
        accent = "#fad51e"
        sep = tag_color if tag_color.startswith("#") else f"#{tag_color}"

        f_title = self._font('images/profile/5x5.ttf', 38)
        divider_y = panel_y + 48
        summary_y = divider_y + 48

        draw.text((panel_x, panel_y), "Raids", font=f_title, fill=accent)
        draw.line([(panel_x, divider_y), (panel_x + panel_w, divider_y)], fill=sep, width=2)

        summary_entries = [
            ("Total", summary["total"]),
            ("Best", summary["best_rank"]),
            ("Favorite", summary["favorite"]),
            ("Top 100", summary["top_100"]),
        ]
        box_gap = 12
        box_w = (panel_w - box_gap * 3) // 4
        for idx, (label, value) in enumerate(summary_entries):
            x = panel_x + idx * (box_w + box_gap)
            self._draw_summary_box(card, draw, x, summary_y, box_w, 75, label, value)

        raid_y = 235
        raid_gap = 10
        raid_w = (panel_w - raid_gap * (len(stats) - 1)) // len(stats)
        raid_h = 385

        for idx, item in enumerate(stats):
            x = panel_x + idx * (raid_w + raid_gap)
            self._draw_single_raid_card(card, x, raid_y, raid_w, raid_h, item, sep)

    def _draw_summary_box(self, card: Image.Image, draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int,
                          label: str, value: str) -> None:
        f_label = self._font('images/profile/5x5.ttf', 27)
        f_value = self._fit_font(value, draw, 'images/profile/game.ttf', 31, w - 18, min_size=18)

        box = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        bdraw = ImageDraw.Draw(box)
        bdraw.rounded_rectangle((0, 0, w - 1, h - 1), radius=10, fill=(0, 0, 0, 30))
        card.paste(box, (x, y), box)

        draw.text((x + (w // 2), y - 16), label, font=f_label, fill="#fad51e", anchor="mm")
        addLine(value, draw, f_value, x + (w // 2), y + (h // 2) + 2, drop_x=3, drop_y=3, anchor="mm")

    def _draw_single_raid_card(self, card: Image.Image, x: int, y: int, w: int, h: int,
                               item: Dict, accent_color: str) -> None:
        abbr = item["abbr"]
        rank_val = item["rank"]
        count = item["count"]
        outline_color = self._rank_outline_color(rank_val)
        stripe_color = outline_color or accent_color

        box = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        bdraw = ImageDraw.Draw(box)
        bdraw.rounded_rectangle((0, 0, w - 1, h - 1), radius=10, fill=(0, 0, 0, 30))
        if outline_color:
            bdraw.rounded_rectangle((0, 0, w - 1, h - 1), radius=10, outline=outline_color, width=2)

        icon = self._load_raid_icon(abbr, stripe_color)
        icon.thumbnail((104, 104))
        box.paste(icon, ((w - icon.width) // 2, 22), icon)

        abbr_font = self._fit_font(abbr, bdraw, 'images/profile/5x5.ttf', 36, w - 12, min_size=26)
        bdraw.text((w // 2, 138), abbr, font=abbr_font, fill="#fad51e", anchor="mm")

        name_font = self._font('images/profile/5x5.ttf', 16)
        for line_idx, line in enumerate(self._wrap_lines(item["name"], bdraw, name_font, w - 20, max_lines=3)):
            bdraw.text((w // 2, 172 + line_idx * 21), line, font=name_font, fill="#ffffff", anchor="mm")

        label_font = self._font('images/profile/5x5.ttf', 22)
        value_font = self._font('images/profile/game.ttf', 34)
        small_value_font = self._font('images/profile/game.ttf', 28)
        rank_text = f"#{rank_val}" if rank_val else "N/A"

        bdraw.text((w // 2, 238), "Rank", font=label_font, fill="#fad51e", anchor="mm")
        rank_font = self._fit_font(rank_text, bdraw, 'images/profile/game.ttf', 34, w - 26, min_size=20)
        self._draw_shadow_text(bdraw, (w // 2, 270), rank_text, rank_font, outline_color or "#ffffff", anchor="mm")

        bdraw.text((w // 2, 314), "Clears", font=label_font, fill="#fad51e", anchor="mm")
        count_text = str(count)
        count_font = small_value_font if len(count_text) > 4 else value_font
        count_font = self._fit_font(count_text, bdraw, 'images/profile/game.ttf', count_font.size, w - 26, min_size=20)
        self._draw_shadow_text(bdraw, (w // 2, 350), count_text, count_font, "#ffffff", anchor="mm")

        card.paste(box, (x, y), box)

    @staticmethod
    def _rank_outline_color(rank_val: int):
        if not rank_val:
            return None
        if rank_val <= 10:
            return "#ffd700"
        if rank_val <= 50:
            return "#c0c0c0"
        if rank_val <= 100:
            return "#cd7f32"
        return None

    @staticmethod
    def _fit_badge_width(badge: Image.Image, max_w: int) -> Image.Image:
        if badge.width <= max_w:
            return badge
        ratio = max_w / badge.width
        return badge.resize((max_w, max(1, int(badge.height * ratio))), Image.Resampling.NEAREST)

    @staticmethod
    def _scale_badge(badge: Image.Image, scale: float, max_w: int) -> Image.Image:
        new_w = max(1, int(badge.width * scale))
        new_h = max(1, int(badge.height * scale))
        badge = badge.resize((new_w, new_h), Image.Resampling.NEAREST)
        if badge.width <= max_w:
            return badge
        ratio = max_w / badge.width
        return badge.resize((max_w, max(1, int(badge.height * ratio))), Image.Resampling.NEAREST)

    @classmethod
    def _font(cls, path: str, size: int) -> ImageFont.FreeTypeFont:
        key = (path, size)
        font = cls.FONT_CACHE.get(key)
        if font is None:
            font = ImageFont.truetype(path, size)
            cls.FONT_CACHE[key] = font
        return font

    @staticmethod
    def _normalize_hex(color: str) -> str:
        return color if color.startswith("#") else f"#{color}"

    @classmethod
    def _gradient_endpoints(cls, main_color: str, secondary_color=False, reverse: bool = False):
        main_color = cls._normalize_hex(main_color)
        if secondary_color is not False:
            return ImageColor.getrgb(main_color), ImageColor.getrgb(cls._normalize_hex(secondary_color))

        r, g, b = [channel / 255 for channel in ImageColor.getrgb(main_color)]
        h, s, v = colorsys.rgb_to_hsv(r, g, b)

        shadow_rgb = colorsys.hsv_to_rgb((h - 0.03) % 1, s, max(v - 0.1, 0))
        light_rgb = colorsys.hsv_to_rgb((h + 0.03) % 1, s, min(v + 0.15, 1))
        shadow = tuple(int(channel * 255) for channel in shadow_rgb)
        light = tuple(int(channel * 255) for channel in light_rgb)
        return (shadow, light) if reverse else (light, shadow)

    @classmethod
    def _fast_vertical_gradient(cls, width=900, height=1180, main_color='#66ccff',
                                secondary_color=False, reverse=False) -> Image.Image:
        top_color, bottom_color = cls._gradient_endpoints(main_color, secondary_color, reverse)
        strip = Image.new('RGBA', (1, height), (0, 0, 0, 0))
        denom = max(height - 1, 1)
        for y in range(height):
            ratio = y / denom
            r = int(top_color[0] * (1 - ratio) + bottom_color[0] * ratio)
            g = int(top_color[1] * (1 - ratio) + bottom_color[1] * ratio)
            b = int(top_color[2] * (1 - ratio) + bottom_color[2] * ratio)
            strip.putpixel((0, y), (r, g, b, 255))
        return strip.resize((width, height), Image.Resampling.NEAREST)

    @staticmethod
    def _draw_shadow_text(draw: ImageDraw.ImageDraw, xy: Tuple[int, int], text: str, font: ImageFont.FreeTypeFont,
                          fill: str, anchor: str = None) -> None:
        x, y = xy
        draw.text((x + 3, y + 3), text, font=font, fill="#151515", anchor=anchor)
        draw.text((x, y), text, font=font, fill=fill, anchor=anchor)

    @staticmethod
    def _cover_resize(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
        img = img.convert("RGBA")
        scale = max(target_w / img.width, target_h / img.height)
        new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
        left = (img.width - target_w) // 2
        top = (img.height - target_h) // 2
        return img.crop((left, top, left + target_w, top + target_h))

    @staticmethod
    def _fit_font(text: str, draw: ImageDraw.ImageDraw, path: str, max_size: int, max_w: int,
                  min_size: int = 12) -> ImageFont.FreeTypeFont:
        sample = text or "N/A"
        for size in range(max_size, min_size - 1, -1):
            font = Raids._font(path, size)
            if draw.textbbox((0, 0), sample, font=font)[2] <= max_w:
                return font
        return Raids._font(path, min_size)

    @staticmethod
    def _wrap_lines(text: str, draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont,
                    max_w: int, max_lines: int) -> List[str]:
        words = text.split()
        if not words:
            return ["N/A"]
        lines = []
        current = words[0]
        for word in words[1:]:
            test = f"{current} {word}"
            if draw.textbbox((0, 0), test, font=font)[2] <= max_w:
                current = test
            else:
                lines.append(current)
                current = word
        lines.append(current)
        if len(lines) <= max_lines:
            return lines
        clipped = lines[:max_lines]
        clipped[-1] = clipped[-1].rstrip(". ") + "..."
        return clipped

    def _load_raid_icon(self, abbr: str, color: str) -> Image.Image:
        paths = [f"images/raids/{abbr}.png"]
        for path in paths:
            try:
                return Image.open(path).convert("RGBA")
            except FileNotFoundError:
                continue
        return self._fallback_raid_icon(abbr, color)

    @staticmethod
    def _fallback_raid_icon(abbr: str, color: str) -> Image.Image:
        img = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        glow = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow)
        rgb = ImageColor.getrgb(color)
        gdraw.ellipse((16, 16, 112, 112), fill=rgb + (120,))
        glow = glow.filter(ImageFilter.GaussianBlur(12))
        img.paste(glow, (0, 0), glow)

        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((25, 20, 103, 108), radius=8, fill=(12, 12, 12, 220), outline=rgb + (255,), width=4)
        draw.polygon([(64, 8), (88, 24), (40, 24)], fill=rgb + (230,))
        font = Raids._font('images/profile/5x5.ttf', 26)
        draw.text((64, 64), abbr, font=font, fill="#ffffff", anchor="mm")
        return img

    # ------------------------- Cog setup ------------------------------------
    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client: commands.Bot):
    client.add_cog(Raids(client))
