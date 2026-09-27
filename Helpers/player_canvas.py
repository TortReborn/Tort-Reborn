"""The player column shared by the raid cards: gradient canvas, portrait,
rank badge and guild banner, plus the font and image helpers around them.

`RaidCardBase` and the tracked-raid cards both inherit it.
"""

import colorsys
import os
from io import BytesIO
from typing import Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageColor, ImageChops

from Helpers.functions import (
    round_corners,
    addLine,
    generate_rank_badge,
    generate_banner,
    getData,
    generate_badge,
    timed_get,
)
from Helpers.variables import discord_ranks, minecraft_banner_colors
from Helpers.storage import get_background


class PlayerCanvas:
    CARD_W = 1300
    CARD_H = 720
    FONT_CACHE: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}

    def _build_base_canvas(self, tag_color: str, grad_start: str, grad_end: str) -> Image.Image:
        card = self._fast_vertical_gradient(width=self.CARD_W, height=self.CARD_H, main_color=tag_color)
        card = round_corners(card)

        overlay = self._fast_vertical_gradient(width=self.CARD_W - 50, height=self.CARD_H - 50,
                                               main_color=grad_start,
                                               secondary_color=grad_end)
        card.paste(overlay, (25, 25), overlay)
        return card

    def _draw_background(self, card: Image.Image, tag_color: str, bg_index: int) -> None:
        outline = self._fast_vertical_gradient(width=438, height=545, main_color=tag_color, reverse=True)
        outline = round_corners(outline)
        card.paste(outline, (41, 100), outline)

        bg_img = get_background(bg_index)
        bg_img = self._cover_resize(bg_img, 418, 525)
        bg_img = round_corners(bg_img, radius=20)
        card.paste(bg_img, (50, 110), bg_img)

    def _draw_player_header(self, card: Image.Image, draw: ImageDraw.ImageDraw, player: Dict, tag_color: str) -> None:
        username = player.get("username") or player.get("displayName") or "Unknown"
        name_font = self._fit_font(username, draw, 'images/profile/game.ttf', 50, 430, min_size=28)
        addLine(text=username, draw=draw, font=name_font, x=50, y=40, drop_x=7, drop_y=7)

        uuid = player.get("uuid", "")
        try:
            if not uuid:
                raise ValueError("Missing player UUID")

            headers = {'User-Agent': os.getenv("visage_UA", "")}
            av_url = f"https://visage.surgeplay.com/bust/500/{uuid}"
            resp = timed_get(av_url, headers=headers, timeout=6)
            resp.raise_for_status()
            skin = Image.open(BytesIO(resp.content)).convert('RGBA')
        except Exception:
            skin = Image.open('images/profile/x-steve500.png').convert('RGBA')
        skin.thumbnail((480, 480))

        portrait_mask = Image.new("L", card.size, 0)
        ImageDraw.Draw(portrait_mask).rounded_rectangle((41, 100, 479, 645), radius=25, fill=255)
        skin_layer = Image.new("RGBA", card.size, (0, 0, 0, 0))
        skin_layer.paste(skin, (20, 156), skin)
        skin_layer.putalpha(ImageChops.multiply(skin_layer.getchannel('A'), portrait_mask))
        card.paste(skin_layer, (0, 0), skin_layer)

    def _draw_rank_badge(self, card: Image.Image, player_stats, tag_color: str) -> None:
        rank_badge = generate_rank_badge(player_stats.tag_display, tag_color)
        rank_badge = self._fit_badge_width(rank_badge, 380)
        w, h = rank_badge.size
        card.paste(rank_badge, (260 - w // 2, 96), rank_badge)

    def _draw_guild_elements(self, card: Image.Image, player: Dict, player_stats) -> None:
        guild_info = player.get("guild")
        if not guild_info:
            return

        gname = guild_info.get('name', '')
        guild_data = getattr(player_stats, 'guild_data', None)
        try:
            banner = guild_data['banner'] if guild_data else getData(gname)['banner']
            base = banner.get('base')
            if base in ['BLACK', 'GRAY', 'BROWN']:
                colour = next(layer['colour'] for layer in banner['layers'] if layer['colour'] not in ['BLACK', 'GRAY', 'BROWN'])
            else:
                colour = base
        except Exception:
            colour = 'WHITE'

        rgb = minecraft_banner_colors.get(colour, (255, 255, 255))
        g_badge = generate_badge(text=gname,
                                 base_color='#{:02x}{:02x}{:02x}'.format(*rgb),
                                 scale=3)
        g_bbox = g_badge.getbbox()
        if g_bbox:
            g_badge = g_badge.crop(g_bbox)
        g_badge = self._fit_badge_width(g_badge, 360)

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
            gr_key = str(gr_text).lower()
            gr_color = discord_ranks.get(gr_key, {}).get('color', '#a0aeb0')

        gr_badge = generate_badge(text=str(gr_text).upper(), base_color=gr_color, scale=3)
        gr_bbox = gr_badge.getbbox()
        if gr_bbox:
            gr_badge = gr_badge.crop(gr_bbox)
        gr_badge = self._scale_badge(gr_badge, 0.82, 320)

        try:
            bn = generate_banner(gname, 15, "2", guild_data=guild_data)
            bn.thumbnail((157, 157))
            bn = bn.convert('RGBA')
        except Exception:
            bn = None

        guild_badge_x = 108
        guild_badge_y = 620 - (12 if not use_taq else 0)
        card.paste(g_badge, (guild_badge_x, guild_badge_y), g_badge)
        card.paste(gr_badge, (guild_badge_x, guild_badge_y + g_badge.height), gr_badge)
        if bn is not None:
            card.paste(bn, (41, 538), bn)

    def _draw_stat_box(self, card: Image.Image, draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int,
                       label: str, value: str) -> None:
        f_label = self._font('images/profile/5x5.ttf', 27)
        f_value = self._fit_font(value, draw, 'images/profile/game.ttf', 31, w - 18, min_size=18)

        box = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        bdraw = ImageDraw.Draw(box)
        bdraw.rounded_rectangle((0, 0, w - 1, h - 1), radius=10, fill=(0, 0, 0, 30))
        card.paste(box, (x, y), box)

        draw.text((x + (w // 2), y - 16), label, font=f_label, fill="#fad51e", anchor="mm")
        addLine(value, draw, f_value, x + (w // 2), y + (h // 2) + 2, drop_x=3, drop_y=3, anchor="mm")

    @staticmethod
    def _fit_badge_width(badge: Image.Image, max_w: int) -> Image.Image:
        if badge.width <= max_w:
            return badge
        ratio = max_w / badge.width
        return badge.resize((max_w, max(1, round(badge.height * ratio))), Image.Resampling.NEAREST)

    @staticmethod
    def _scale_badge(badge: Image.Image, scale: float, max_w: int) -> Image.Image:
        new_w = max(1, int(badge.width * scale))
        new_h = max(1, int(badge.height * scale))
        badge = badge.resize((new_w, new_h), Image.Resampling.NEAREST)
        if badge.width <= max_w:
            return badge
        ratio = max_w / badge.width
        return badge.resize((max_w, max(1, round(badge.height * ratio))), Image.Resampling.NEAREST)

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

    @classmethod
    def _fit_font(cls, text: str, draw: ImageDraw.ImageDraw, path: str, max_size: int, max_w: int,
                  min_size: int = 12) -> ImageFont.FreeTypeFont:
        sample = text or "N/A"
        for size in range(max_size, min_size - 1, -1):
            font = cls._font(path, size)
            if draw.textbbox((0, 0), sample, font=font)[2] <= max_w:
                return font
        return cls._font(path, min_size)

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
