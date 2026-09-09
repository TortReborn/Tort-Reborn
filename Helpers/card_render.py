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

from Helpers.logger import WARN, log

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_GAME = os.path.join(BASE, "images", "profile", "game.ttf")
FONT_UI = os.path.join(BASE, "images", "shell_exchange", "resources",
                       "Inter-VariableFont_opsz,wght.ttf")
ART_CACHE = os.path.join(BASE, "images", "cards")

W, H = 340, 500
PAD = 13
ART_H = 320
RADIUS = 16

BG_TOP = (26, 29, 36)
BG_BOT = (18, 20, 26)
PANEL = (35, 39, 48)

TIERS = {
    "common": {"accent": (156, 163, 175), "glow": (110, 118, 132)},
    "uncommon": {"accent": (52, 211, 153), "glow": (16, 140, 100)},
    "rare": {"accent": (96, 165, 250), "glow": (30, 90, 190)},
    "epic": {"accent": (192, 132, 252), "glow": (110, 50, 180)},
    "legendary": {"accent": (251, 191, 36), "glow": (170, 110, 10)},
    # The five raid bosses, above legendary. Red, which is what Wynncraft
    # itself uses for Fabled, and well clear of legendary gold.
    "fabled": {"accent": (255, 85, 85), "glow": (150, 25, 25)},
}

# 1/1 member cards are tiered by the holder's guild rank rather than by
# rarity, so a Hydra card reads differently from a Swordfish at a glance.
RANK_TIERS = {
    "Swordfish": {"accent": (24, 186, 241), "glow": (10, 120, 165)},
    "Hammerhead": {"accent": (57, 106, 255), "glow": (25, 55, 180)},
    "Sailfish": {"accent": (158, 107, 255), "glow": (85, 45, 175)},
    "Dolphin": {"accent": (230, 107, 255), "glow": (150, 40, 180)},
    "Narwhal": {"accent": (235, 34, 121), "glow": (165, 15, 80)},
    "Hydra": {"accent": (176, 20, 68), "glow": (125, 10, 45)},
}
TIERS.update(RANK_TIERS)

# Rarity and fusion are separate facts, so they get separate places. The outer
# edge always carries the tier colour; fusion shows as an inner ring that
# climbs bronze, silver, gold, and finally a prismatic edge at the top.
#
# The ladder is read as progress toward *this tier's* ceiling rather than as an
# absolute star count, because the ceilings differ: a legendary maxes at 2★ and
# an epic at 3★, and both should look every bit as finished as a 5★ common.
FUSION_RING = [
    (0.34, (205, 127, 50)),        # bronze
    (0.67, (214, 216, 222)),       # silver
    (0.99, (255, 196, 61)),        # gold
    (1.00, (247, 251, 255)),       # maxed, paired with the prismatic edge
]
PRISMATIC = [(255, 120, 200), (150, 200, 255), (140, 255, 210), (255, 225, 140)]

# How far a card can be fused, by tier. Copies triple each step, so these are
# 81, 9 and 3 base copies respectively.
# Stars count merges, so 0 is an unfused card. Copies behind a maxed card:
# 81 for the common half, 9 for an epic, 3 for a legendary or a fabled.
MAX_STARS_BY_TIER = {
    "common": 4, "uncommon": 4, "rare": 4, "epic": 2,
    "legendary": 1, "fabled": 1,
}


def max_stars_for(tier: str) -> int:
    """A member 1/1 has no ladder; everything else has its tier's ceiling."""
    return MAX_STARS_BY_TIER.get(tier, 0)


def _fusion_progress(stars: int, max_stars: int) -> float:
    """0 for unfused, 1 at this tier's ceiling."""
    if max_stars <= 0 or stars <= 0:
        return 0.0
    return min(1.0, stars / max_stars)


def _ring_colour(progress: float):
    for cutoff, colour in FUSION_RING:
        if progress <= cutoff:
            return colour
    return FUSION_RING[-1][1]

_FONT_CACHE = {}


def _font(path, size):
    key = (path, size)
    if key not in _FONT_CACHE:
        try:
            _FONT_CACHE[key] = ImageFont.truetype(path, size)
        except OSError:
            _FONT_CACHE[key] = ImageFont.load_default()
    return _FONT_CACHE[key]


def _readable(rgb, floor=0.42):
    """Lift dark accents (Hydra red) so small label text stays legible."""
    lum = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255
    if lum >= floor:
        return rgb
    t = min(0.72, (floor - lum) * 1.5)
    return tuple(int(c + (255 - c) * t) for c in rgb)


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


def get_art(slug: str, image_url: str) -> Image.Image | None:
    """Cached card art. Downloads once, then reads from disk."""
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


def _prismatic_border(card, box, radius, width=3):
    """Paint a gradient through a rounded-rectangle outline mask.

    Arcs would trace an ellipse rather than the card's rounded corners, so the
    gradient is generated full-bleed and then masked down to just the border.
    """
    w, h = card.size
    grad = Image.new("RGB", (w, h))
    gd = ImageDraw.Draw(grad)
    n = len(PRISMATIC)
    for x in range(w):
        t = (x / max(1, w - 1)) * n
        a = PRISMATIC[int(t) % n]
        b = PRISMATIC[(int(t) + 1) % n]
        f = t % 1
        gd.line([(x, 0), (x, h)],
                fill=tuple(int(a[c] + (b[c] - a[c]) * f) for c in range(3)))

    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius, outline=255, width=width)
    card.paste(grad, (0, 0), mask)


def render_card(name: str, tier: str, slug: str = "", image_url: str = "",
                badge: str | None = None, stars: int = 1,
                max_stars: int | None = None) -> Image.Image:
    """Draw a single card. Falls back to a '?' panel when art is missing."""
    style = TIERS.get(tier, TIERS["common"])
    accent, glow = style["accent"], style["glow"]
    if max_stars is None:
        max_stars = max_stars_for(tier)
    progress = _fusion_progress(stars, max_stars)
    maxed = stars > 0 and progress >= 1.0
    ring = _ring_colour(progress) if stars > 0 else None

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    body = _vgrad((W, H), BG_TOP, BG_BOT).convert("RGBA")
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, H - 1], RADIUS, fill=255)
    card.paste(body, (0, 0), mask)

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

    d = ImageDraw.Draw(card)

    # name plate, vertically centred in the space below the art
    ny = ay1 + 16
    d.line([PAD + 6, ny, W - PAD - 6, ny], fill=accent + (70,), width=1)

    nf = _fit_font(d, name, FONT_GAME, W - 2 * PAD - 14, 29)
    tier_font = _font(FONT_UI, 18)
    level_font = _font(FONT_UI, 22)

    name_h, gap, tier_h, level_gap = 30, 15, 18, 9
    level_h = 24 if stars > 0 else 0
    block = name_h + gap + tier_h + (level_gap + level_h if level_h else 0)
    top = ny + max(10, (H - ny - block) // 2)

    d.text(((W - d.textlength(name, font=nf)) / 2, top), name, font=nf,
           fill=(240, 243, 248))

    spaced = " ".join(tier.upper())
    tier_y = top + name_h + gap
    d.text(((W - d.textlength(spaced, font=tier_font)) / 2, tier_y),
           spaced, font=tier_font, fill=_readable(accent))

    # Counting stars stops meaning anything at the ceiling, where the point is
    # that there is nowhere left to go — so it says so.
    if stars > 0:
        row = "M A X" if maxed else "  ".join(["\u2605"] * stars)
        d.text(((W - d.textlength(row, font=level_font)) / 2,
                tier_y + tier_h + level_gap),
               row, font=level_font, fill=_readable(ring if ring else accent))

    # Outer edge: the tier, always — except at the ceiling, where the
    # prismatic band takes over and the tier still reads from the label.
    if maxed:
        _prismatic_border(card, [0, 0, W - 1, H - 1], RADIUS, width=4)
    else:
        d.rounded_rectangle([0, 0, W - 1, H - 1], RADIUS,
                            outline=accent + (235,), width=2)
    d.rounded_rectangle([2, 2, W - 3, H - 3], RADIUS - 2,
                        outline=(255, 255, 255, 16), width=1)

    # Inner ring: how far up this tier's ladder the card has been fused.
    if ring is not None:
        inset = 6
        d.rounded_rectangle([inset, inset, W - 1 - inset, H - 1 - inset],
                            RADIUS - 4, outline=ring + (225,),
                            width=2 if not maxed else 3)

    if badge:
        bf = _font(FONT_UI, 12)
        bw = d.textlength(badge, font=bf)
        d.rounded_rectangle([W - PAD - bw - 18, PAD + 8, W - PAD - 4, PAD + 30],
                            9, fill=(12, 14, 18, 205), outline=accent + (170,))
        d.text((W - PAD - bw - 11, PAD + 12), badge, font=bf, fill=accent)

    return card


def card_file(card: dict, badge: str | None = None, stars: int = 1,
              max_stars: int | None = None):
    """Render a card from a card-set entry into a discord.File.

    Member 1/1s carry their own badge and are framed by guild rank, so the
    badge defaults to 1 / 1 for them unless the caller says otherwise.
    """
    import discord

    if card.get("member") and badge is None:
        badge = "RETIRED" if card.get("retired") else "1 / 1"

    img = render_card(card["name"], card["tier"], card.get("slug", ""),
                      card.get("image_url", ""), badge=badge, stars=stars,
                      max_stars=max_stars)
    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return discord.File(buf, filename=f"card_{card.get('slug', 'x')}_{int(time.time())}.png")
