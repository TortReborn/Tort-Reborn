import asyncio
import os
import time
from io import BytesIO
from urllib.parse import quote
from typing import Dict, List, Tuple

import requests
import discord
from discord.ext import pages
from PIL import Image, ImageDraw, ImageFilter, ImageColor

from Helpers.classes import PlayerStats, Page
from Helpers.functions import timed_get
from Helpers.pagination import add_paginator_buttons, respond_paginator
from Helpers.player_canvas import PlayerCanvas


class RaidCardError(Exception):
    pass


class RaidCardBase(PlayerCanvas):
    NAME_LINES: Dict[str, Tuple[str, str, str]] = {
        "NOTG": ("Nest", "of the", "Grootslangs"),
        "NOL":  ("Orphion's", "Nexus", "of Light"),
        "TCC":  ("The", "Canyon", "Colossus"),
        "TNA":  ("The", "Nameless", "Anomaly"),
        "WTP":  ("The Queen's", "Wartorn", "Palace"),
    }

    TITLE = "Raids"
    COUNT_LABEL = "Clears"
    FILE_PREFIX = "raids"
    STAT_KEY = "raids"
    RANK_QUALIFIERS: Tuple[str, ...] = ("completion",)
    TEMP_RAID_COUNT_FALLBACKS: Dict[str, Tuple[str, ...]] = {}

    RAIDS: List[Tuple[str, str, Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]] = []

    def __init__(self, client):
        self.client = client

    async def _run(self, ctx: discord.ApplicationContext, name: str):
        try:
            await ctx.defer()
        except discord.NotFound:
            return

        try:
            player_stats = await asyncio.to_thread(PlayerStats, name, 7, False)
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

        player = getattr(player_stats, "player_data", None)
        if not isinstance(player, dict):
            player = await self._fetch_player(name)
        if player is None:
            embed = discord.Embed(
                title=':no_entry: Player not found',
                description=f'Could not find player `{name}`.',
                color=0xe33232
            )
            await ctx.followup.send(embed=embed, ephemeral=True)
            return

        tag_color = player_stats.tag_color
        grad_start, grad_end = player_stats.gradient
        bg_index = player_stats.background

        try:
            raids_list, ranking = await self._collect_stat_source(player)
        except RaidCardError as exc:
            embed = discord.Embed(
                title=':no_entry: Oops! Something did not go as intended.',
                description=str(exc),
                color=0xe33232
            )
            await ctx.followup.send(embed=embed, ephemeral=True)
            return

        stats = self._extract_raid_stats(raids_list, ranking)
        summary = self._summarize_raid_stats(stats)

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

        extra = await self._extra_cards(ctx, player, player_stats)
        username = player.get('username', name)
        stamp = int(time.time())

        if not extra:
            buf = BytesIO()
            card.save(buf, format="PNG")
            buf.seek(0)
            filename = f"{self.FILE_PREFIX}_{username}_{stamp}.png"
            await ctx.followup.send(file=discord.File(buf, filename=filename))
            return

        book = []
        for index, image in enumerate([card, *extra]):
            buf = BytesIO()
            image.save(buf, format="PNG")
            buf.seek(0)
            filename = f"{self.FILE_PREFIX}_{username}_{stamp}_{index}.png"
            book.append(Page(content='', files=[discord.File(buf, filename=filename)]))

        paginator = pages.Paginator(pages=book)
        add_paginator_buttons(paginator)
        await respond_paginator(paginator, ctx.interaction)

    async def _extra_cards(self, ctx: discord.ApplicationContext, player: Dict,
                           player_stats: PlayerStats) -> List[Image.Image]:
        """Pages appended after the API card. Empty here; /graids fills it."""
        return []

    async def _collect_stat_source(self, player: Dict) -> Tuple[Dict, Dict]:
        raids_list = player.get("globalData", {}).get(self.STAT_KEY, {}).get("list", {})
        ranking = player.get("ranking", {})
        return raids_list, ranking

    async def _fetch_player(self, name: str) -> Dict:
        return await asyncio.to_thread(self._fetch_player_sync, name)

    def _fetch_player_sync(self, name: str) -> Dict:
        safe_name = quote(name)
        url = f"https://api.wynncraft.com/v3/player/{safe_name}"
        try:
            res = timed_get(url, timeout=10, headers={"Authorization": f"Bearer {os.getenv('WYNN_TOKEN')}"})
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
        card = self._build_base_canvas(tag_color, grad_start, grad_end)
        draw = ImageDraw.Draw(card)

        self._draw_background(card, tag_color, bg_index)
        self._draw_player_header(card, draw, player, tag_color)
        self._draw_rank_badge(card, player_stats, tag_color)
        self._draw_guild_elements(card, player, player_stats)
        self._draw_raid_panel(card, draw, stats, summary, tag_color)
        return card

    def _extract_raid_stats(self, raids_list: Dict, ranking: Dict) -> List[Dict]:
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

    def _lookup_rank(self, ranking: Dict, rank_keys: Tuple[str, ...], fragments: Tuple[str, ...]) -> int:
        for key in rank_keys:
            value = ranking.get(key)
            if value:
                return value

        for key, value in ranking.items():
            key_l = key.lower()
            if not any(qualifier in key_l for qualifier in self.RANK_QUALIFIERS):
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
        avg_rank = f'#{round(sum(item["rank"] for item in ranked) / len(ranked))}' if ranked else "N/A"

        return {
            "total": str(total),
            "favorite": favorite["abbr"] if favorite else "N/A",
            "best_rank": f'#{best["rank"]} {best["abbr"]}' if best else "N/A",
            "avg_rank": avg_rank,
        }

    def _draw_raid_panel(self, card: Image.Image, draw: ImageDraw.ImageDraw, stats: List[Dict],
                         summary: Dict[str, str], tag_color: str) -> None:
        panel_x = 492
        panel_w = self.CARD_W - panel_x - 38
        accent = "#fad51e"
        sep = tag_color if tag_color.startswith("#") else f"#{tag_color}"

        f_title = self._font('images/profile/5x5.ttf', 54)
        divider_y = 100
        title_y = 25 + (75 - 54) // 2
        summary_y = divider_y + 48

        draw.text((panel_x, title_y), self.TITLE, font=f_title, fill=accent)
        draw.line([(panel_x, divider_y), (panel_x + panel_w, divider_y)], fill=sep, width=2)

        summary_entries = [
            ("Total", summary["total"]),
            ("Best", summary["best_rank"]),
            ("Favorite", summary["favorite"]),
            ("Avg. Rank", summary["avg_rank"]),
        ]
        box_gap = 12
        box_w = (panel_w - box_gap * 3) // 4
        for idx, (label, value) in enumerate(summary_entries):
            x = panel_x + idx * (box_w + box_gap)
            self._draw_stat_box(card, draw, x, summary_y, box_w, 75, label, value)

        raid_y = 235
        raid_gap = 10
        raid_w = (panel_w - raid_gap * (len(stats) - 1)) // len(stats)
        raid_h = 405

        for idx, item in enumerate(stats):
            x = panel_x + idx * (raid_w + raid_gap)
            self._draw_single_raid_card(card, x, raid_y, raid_w, raid_h, item, sep)

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

        abbr_font = self._fit_font(abbr, bdraw, 'images/profile/5x5.ttf', 44, w - 12, min_size=28)
        bdraw.text((w // 2, 138), abbr, font=abbr_font, fill="#fad51e", anchor="mm")

        name_font = self._font('images/profile/5x5.ttf', 16)
        fixed_lines = self.NAME_LINES.get(abbr)
        name_lines = list(fixed_lines) if fixed_lines else self._wrap_lines(item["name"], bdraw, name_font, w - 20, max_lines=3)
        for line_idx, line in enumerate(name_lines):
            bdraw.text((w // 2, 172 + line_idx * 21), line, font=name_font, fill="#ffffff", anchor="mm")

        label_font = self._font('images/profile/5x5.ttf', 27)
        rank_text = f"#{rank_val}" if rank_val else "N/A"

        bdraw.text((w // 2, 250), "Rank", font=label_font, fill="#fad51e", anchor="mm")
        rank_font = self._fit_font(rank_text, bdraw, 'images/profile/game.ttf', 42, w - 14, min_size=20)
        self._draw_shadow_text(bdraw, (w // 2, 288), rank_text, rank_font, outline_color or "#ffffff", anchor="mm")

        bdraw.text((w // 2, 330), self.COUNT_LABEL, font=label_font, fill="#fad51e", anchor="mm")
        count_text = str(count)
        count_font = self._fit_font(count_text, bdraw, 'images/profile/game.ttf', 42, w - 8, min_size=20)
        self._draw_shadow_text(bdraw, (w // 2, 370), count_text, count_font, "#ffffff", anchor="mm")

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

    def _load_raid_icon(self, abbr: str, color: str) -> Image.Image:
        try:
            return Image.open(f"images/raids/{abbr}.png").convert("RGBA")
        except FileNotFoundError:
            return self._fallback_raid_icon(abbr, color)

    @classmethod
    def _fallback_raid_icon(cls, abbr: str, color: str) -> Image.Image:
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
        font = cls._font('images/profile/5x5.ttf', 26)
        draw.text((64, 64), abbr, font=font, fill="#ffffff", anchor="mm")
        return img
