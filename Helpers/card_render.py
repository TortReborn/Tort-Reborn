"""Trading card rendering for the card collection system.

Cards are a tier-tinted frame over the character's wiki art with a name plate
underneath, drawn with the same game.ttf the profile and leaderboard renders
use so cards sit alongside the rest of the bot's imagery.

Art is fetched from the wiki on first use and cached under images/cards, so a
card is only ever downloaded once.
"""

import os
import time
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from Helpers.functions import generate_badge
from Helpers.logger import WARN, log
from Helpers.variables import discord_ranks

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_GAME = os.path.join(BASE, "images", "profile", "game.ttf")
FONT_UI = os.path.join(BASE, "images", "shell_exchange", "resources",
                       "Inter-VariableFont_opsz,wght.ttf")
ART_CACHE = os.path.join(BASE, "images", "cards")
PORTRAIT_DIR = os.path.join(BASE, "images", "card_portraits")

W, H = 340, 500
PAD = 13
ART_H = 320
RADIUS = 16
BORDER = 7

BG_TOP = (26, 29, 36)
BG_BOT = (18, 20, 26)
PANEL = (35, 39, 48)
LIMITED = "limited"

# Wynncraft's own rarity colours, one rung longer than theirs; Limited sits outside the ladder.
TIERS = {
    "normal": {"accent": (255, 255, 255), "glow": (140, 140, 140), "badge": "#ffffff"},
    "unique": {"accent": (255, 255, 85), "glow": (150, 140, 30), "badge": "#ffff55"},
    "rare": {"accent": (255, 85, 255), "glow": (140, 47, 140), "badge": "#ff55ff"},
    "legendary": {"accent": (85, 255, 255), "glow": (51, 153, 153), "badge": "#55ffff"},
    "fabled": {"accent": (255, 85, 85), "glow": (150, 25, 25), "badge": "#ff5555"},
    "mythic": {"accent": (170, 0, 170), "glow": (110, 10, 110), "badge": "#aa00aa"},
}
LIMITED_ENDS = (TIERS["fabled"]["accent"], TIERS["mythic"]["accent"])
LIMITED_MID = tuple((a + b) // 2 for a, b in zip(*LIMITED_ENDS))
TIERS[LIMITED] = {"accent": LIMITED_MID, "glow": tuple(c * 2 // 3 for c in LIMITED_MID),
                  "badge": "#%02x%02x%02x" % LIMITED_MID}

# How far a card can be fused, by tier. Copies triple each step, so these are
# 81, 9 and 3 base copies respectively.
# Stars count merges, so 0 is an unfused card. Copies behind a maxed card:
# 81 for the normal half, 9 for a legendary, 3 for a fabled or a mythic.
MAX_STARS_BY_TIER = {
    "normal": 4, "unique": 4, "rare": 4, "legendary": 2,
    "fabled": 1, "mythic": 1,
}


def max_stars_for(tier: str) -> int:
    """A member card has no ladder; everything else has its tier's ceiling."""
    return MAX_STARS_BY_TIER.get(tier, 0)


def _tier_level(stars: int, max_stars: int) -> int:
    """0-3 readout for the tier indicator; shorter ladders start higher so their own max lands on 3."""
    return min(3, stars + max(0, 3 - max_stars))

_FONT_CACHE = {}


def _font(path, size):
    key = (path, size)
    if key not in _FONT_CACHE:
        try:
            _FONT_CACHE[key] = ImageFont.truetype(path, size)
        except OSError:
            _FONT_CACHE[key] = ImageFont.load_default()
    return _FONT_CACHE[key]


def _vgrad(size, top, bot):
    w, h = size
    strip = Image.new("RGB", (1, h))
    d = ImageDraw.Draw(strip)
    for y in range(h):
        t = y / max(1, h - 1)
        d.point((0, y), tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    return strip.resize((w, h))


def _fit_font(draw, text, path, max_w, start, min_size=13):
    size = start
    while size > min_size:
        f = _font(path, size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 1
    return _font(path, min_size)


def _mix(a, b, t: float):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _badge(text: str, color: str, max_w: int, scale: int = 2) -> Image.Image | None:
    img = generate_badge(text=text.upper(), base_color=color, scale=scale)
    if not img:
        return None
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    if img.width > max_w:
        ratio = max_w / img.width
        img = img.resize((max_w, max(1, round(img.height * ratio))),
                         Image.Resampling.NEAREST)
    return img


def _rank_badge(rank: str, max_w: int) -> Image.Image | None:
    color = discord_ranks.get(rank, {}).get("color", "#a0aeb0")
    return _badge(rank, color, max_w, scale=2)


TIER_STAR_DIR = os.path.join(BASE, "images", "profile", "tiers")
TIER_STAR_SCALE = 4
_TIER_STAR_CACHE: dict[int, Image.Image] = {}


def _tier_star(level: int) -> Image.Image:
    if level not in _TIER_STAR_CACHE:
        img = Image.open(os.path.join(TIER_STAR_DIR, f"tier_{level}.png")).convert("RGBA")
        img = img.resize((img.width * TIER_STAR_SCALE, img.height * TIER_STAR_SCALE),
                         Image.Resampling.NEAREST)
        _TIER_STAR_CACHE[level] = img
    return _TIER_STAR_CACHE[level]


def _draw_tier_indicator(card: Image.Image, level: int, right: int, top: int, gap: int = 3):
    """Three stars, right-aligned; the rightmost `level` of them carry that tier's colour."""
    icons = [_tier_star(0)] * (3 - level) + [_tier_star(level)] * level
    x = right
    for icon in reversed(icons):
        x -= icon.width
        card.paste(icon, (x, top), icon)
        x -= gap


RAINBOW = [(255, 60, 60), (255, 165, 0), (255, 230, 0), (60, 200, 90),
          (60, 160, 255), (140, 90, 255), (255, 90, 220)]


def _rainbow_strip(width: int, height: int) -> Image.Image:
    n = len(RAINBOW)
    strip = Image.new("RGB", (width, 1))
    d = ImageDraw.Draw(strip)
    for x in range(width):
        t = (x / max(1, width - 1)) * (n - 1)
        a, b = RAINBOW[int(t)], RAINBOW[min(int(t) + 1, n - 1)]
        f = t - int(t)
        d.point((x, 0), tuple(int(a[c] + (b[c] - a[c]) * f) for c in range(3)))
    return strip.resize((width, height))


def _draw_rainbow_text(card: Image.Image, text: str, font, x: int, y: int):
    """ImageDraw.text, but rainbow-filled instead of a flat colour."""
    bbox = ImageDraw.Draw(card).textbbox((0, 0), text, font=font)
    mask = Image.new("L", (bbox[2], bbox[3]), 0)
    ImageDraw.Draw(mask).text((0, 0), text, font=font, fill=255)
    fill = _rainbow_strip(bbox[2], bbox[3]).convert("RGBA")
    fill.putalpha(mask)
    card.paste(fill, (x, y), fill)


def get_art(slug: str, image_url: str) -> Image.Image | None:
    """Cached card art. Downloads once, then reads from disk."""
    portrait = os.path.join(PORTRAIT_DIR, f"{slug}.png")
    if os.path.exists(portrait):
        try:
            return Image.open(portrait).convert("RGBA")
        except Exception as e:
            log(WARN, f"Card portrait unreadable for {slug}: {e}", context="cards")

    os.makedirs(ART_CACHE, exist_ok=True)
    path = os.path.join(ART_CACHE, f"{slug}.png")

    if not os.path.exists(path):
        if not image_url:
            return None
        try:
            r = requests.get(
                image_url, timeout=20,
                headers={"User-Agent": "TortRebornCards/1.0 (TAq guild bot)"})
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
        except Exception as e:
            log(WARN, f"Card art download failed for {slug}: {e}", context="cards")
            return None

    try:
        return Image.open(path).convert("RGBA")
    except Exception as e:
        log(WARN, f"Card art unreadable for {slug}: {e}", context="cards")
        return None


def render_card(name: str, tier: str, slug: str = "", image_url: str = "",
                badge: str | None = None, stars: int = 0,
                max_stars: int | None = None) -> Image.Image:
    """Draw a single card. Falls back to a '?' panel when art is missing."""
    display_tier = LIMITED if badge else tier
    style = TIERS.get(display_tier, TIERS["normal"])
    accent, glow = style["accent"], style["glow"]
    if max_stars is None:
        max_stars = max_stars_for(tier)
    tier_level = _tier_level(stars, max_stars)
    maxed = max_stars > 0 and stars >= max_stars

    top_hue, bot_hue = LIMITED_ENDS if display_tier == LIMITED else (accent, accent)

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    outer = _vgrad((W, H), _mix(top_hue, (255, 255, 255), 0.2),
                   _mix(bot_hue, (0, 0, 0), 0.62)).convert("RGBA")
    outer_mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(outer_mask).rounded_rectangle(
        [0, 0, W - 1, H - 1], RADIUS, fill=255)
    card.paste(outer, (0, 0), outer_mask)

    body = _vgrad((W - BORDER * 2, H - BORDER * 2),
                  _mix(BG_TOP, top_hue, 0.17),
                  _mix(BG_BOT, bot_hue, 0.1)).convert("RGBA")
    body_mask = Image.new("L", body.size, 0)
    ImageDraw.Draw(body_mask).rounded_rectangle(
        [0, 0, body.width - 1, body.height - 1], RADIUS - 5, fill=255)
    card.paste(body, (BORDER, BORDER), body_mask)

    # art panel with the tier glow pooled behind the character
    ax0, ay0, ax1, ay1 = PAD, PAD, W - PAD, PAD + ART_H
    panel = Image.new("RGBA", (ax1 - ax0, ay1 - ay0), PANEL + (255,))
    pm = Image.new("L", panel.size, 0)
    ImageDraw.Draw(pm).rounded_rectangle(
        [0, 0, panel.size[0] - 1, panel.size[1] - 1], RADIUS - 5, fill=255)

    halo = Image.new("RGBA", panel.size, (0, 0, 0, 0))
    cx, cy = panel.size[0] // 2, int(panel.size[1] * 0.62)
    ImageDraw.Draw(halo).ellipse(
        [cx - 108, cy - 78, cx + 108, cy + 78], fill=glow + (95,))
    panel = Image.alpha_composite(panel, halo.filter(ImageFilter.GaussianBlur(34)))

    art = get_art(slug, image_url) if (slug or image_url) else None
    if art:
        art.thumbnail((panel.size[0] - 22, panel.size[1] - 22), Image.LANCZOS)
        panel.paste(art, ((panel.size[0] - art.width) // 2,
                          (panel.size[1] - art.height) // 2 + 4), art)
    else:
        d = ImageDraw.Draw(panel)
        qf = _font(FONT_GAME, 74)
        d.text(((panel.size[0] - d.textlength("?", font=qf)) / 2,
                panel.size[1] / 2 - 52), "?", font=qf, fill=(90, 98, 112))
    card.paste(panel, (ax0, ay0), pm)
    _draw_tier_indicator(card, tier_level, right=ax1 - 10, top=ay0 + 10)

    d = ImageDraw.Draw(card)

    ny = ay1 + 16

    nf = _fit_font(d, name, FONT_GAME, W - 2 * PAD - 14, 29)
    rarity_badge = _badge(display_tier, style["badge"], W - 2 * PAD - 18)
    rank_badge = _rank_badge(badge, W - 2 * PAD - 52) if badge else None

    name_h, gap, badge_gap = 30, 13, 6
    rarity_h = rarity_badge.height if rarity_badge else 0
    rank_h = rank_badge.height if rank_badge else 0
    top = ny + 8

    name_x = int((W - d.textlength(name, font=nf)) / 2)
    if maxed:
        _draw_rainbow_text(card, name, nf, name_x, top)
    else:
        d.text((name_x, top), name, font=nf, fill=(240, 243, 248))

    badge_y = top + name_h + gap
    if rarity_badge:
        card.paste(rarity_badge, ((W - rarity_badge.width) // 2, badge_y),
                   rarity_badge)
    cursor_y = badge_y + rarity_h
    if rank_badge:
        cursor_y += badge_gap
        card.paste(rank_badge, ((W - rank_badge.width) // 2, cursor_y),
                   rank_badge)
        cursor_y += rank_h

    from Helpers.foil import apply_foil, foil_tier_for
    foil_tier = foil_tier_for(stars, max_stars)
    if foil_tier:
        card = apply_foil(card, foil_tier, seed=f"{slug}:{stars}")

    return card


SPREAD_COLS = 5
SPREAD_SCALE = 0.5
SPREAD_GAP = 10


def render_spread(cards: list) -> Image.Image:
    """Several plain cards on one sheet, five to a row at half size.

    A discard can turn one card into ten, and ten attachments is both
    Discord's ceiling and a wall of embeds. One sheet reads as one event.
    """
    cw, ch = int(W * SPREAD_SCALE), int(H * SPREAD_SCALE)
    cols = min(SPREAD_COLS, max(1, len(cards)))
    rows = (len(cards) + cols - 1) // cols
    sheet = Image.new("RGBA", (cols * cw + (cols + 1) * SPREAD_GAP,
                               rows * ch + (rows + 1) * SPREAD_GAP),
                      BG_BOT + (255,))
    for i, card in enumerate(cards):
        badge = card.get("rank") if card.get("member") else None
        img = render_card(card["name"], card["tier"], card.get("slug", ""),
                          card.get("image_url", ""), badge=badge)
        img = img.resize((cw, ch), Image.LANCZOS)
        x = SPREAD_GAP + (i % cols) * (cw + SPREAD_GAP)
        y = SPREAD_GAP + (i // cols) * (ch + SPREAD_GAP)
        sheet.paste(img, (x, y), img)
    return sheet


def spread_file(cards: list):
    """render_spread as a discord.File."""
    import discord

    buf = BytesIO()
    render_spread(cards).convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return discord.File(buf, filename=f"spread_{int(time.time())}.png")


def card_file(card: dict, badge: str | None = None, stars: int = 0,
              max_stars: int | None = None):
    """Render a card from a card-set entry into a discord.File.

    Stars default to none: a card out of a reel is unfused, and stars count
    merges behind it rather than the card itself.

    Member cards carry their own badge and are framed by guild rank, so the
    badge defaults to MEMBER for them unless the caller says otherwise.
    """
    import discord

    if card.get("member") and badge is None:
        badge = card.get("rank")

    img = render_card(card["name"], card["tier"], card.get("slug", ""),
                      card.get("image_url", ""), badge=badge, stars=stars,
                      max_stars=max_stars)
    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return discord.File(buf, filename=f"card_{card.get('slug', 'x')}_{int(time.time())}.png")
