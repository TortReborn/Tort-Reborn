"""Tank list pages, drawn rather than written. One row per stack.

Blocking; call through asyncio.to_thread like the other card renders.
"""

import time
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageDraw

from Helpers import card_render, cards as cardlib
from Helpers.card_copy import LIMITED
from Helpers.functions import round_corners, vertical_gradient
from Helpers.player_canvas import PlayerCanvas

FONT_NAME = "images/profile/game.ttf"
FONT_LABEL = "images/profile/5x5.ttf"

WIDTH = 1000
ROW_H = 40
ROWS_PER_PAGE = 20
HEAD_H = 128
FOOT_H = 50

BADGE_X = 38
NAME_X = 200
NAME_MAX_W = 500
NAME_END = NAME_X + NAME_MAX_W
ROW_END = WIDTH - 28
STARS_CENTER = NAME_END + (ROW_END - NAME_END) // 4
COPIES_CENTER = NAME_END + 3 * (ROW_END - NAME_END) // 4

ACCENT = "#fad51e"
FRAME = "#3474eb"
WHITE = "#ffffff"
ROW_FILL = (255, 255, 255, 15)

NAME_SIZE = 24
TEXT_DY = 4

STAR_SCALE = 3
STAR_GAP = 3
STAR_SLOTS = 3
_STAR_DY = None
_STARS_X = None

_BADGE_CACHE: dict[str, Image.Image] = {}


@dataclass(frozen=True)
class Stack:
    card: dict
    star: int
    count: int


def display_tier(card: dict) -> str:
    return card_render.LIMITED if card.get("member") else card["tier"]


def _tier_index(card: dict) -> int:
    tier = "member" if card.get("member") else card.get("tier", "")
    return (cardlib.TIER_ORDER.index(tier) if tier in cardlib.TIER_ORDER
            else len(cardlib.TIER_ORDER))


def _sort_key(sort: str):
    if sort == "copies":
        return lambda s: (-s.count, _tier_index(s.card), -s.star,
                          s.card["name"].lower())
    if sort == "name":
        return lambda s: (s.card["name"].lower(), -s.star)
    return lambda s: (_tier_index(s.card), -s.star, s.card["name"].lower())


def build_stacks(entries: list, sort: str = "tier") -> list[Stack]:
    stacks = [Stack(card, star, count)
              for card, entry in entries
              for star, count in entry["levels"].items()]
    stacks.sort(key=_sort_key(sort))
    return stacks


def page_count(stacks: list[Stack]) -> int:
    return max(1, (len(stacks) + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)


def _badge(tier: str) -> Image.Image | None:
    if tier not in _BADGE_CACHE:
        text = LIMITED if tier == card_render.LIMITED else tier
        style = card_render.TIERS.get(tier, card_render.TIERS["normal"])
        made = card_render._badge(text, style["badge"], WIDTH, scale=1)
        if made is None:
            return None
        _BADGE_CACHE[tier] = made
    return _BADGE_CACHE[tier]


def _panel(height: int):
    card = vertical_gradient(WIDTH, height, FRAME)
    card = round_corners(card, radius=20)
    inner = vertical_gradient(WIDTH - 40, height - 40, "#0e1e3f", "#05101f")
    card.paste(inner, (20, 20), inner)
    return card, ImageDraw.Draw(card)


def _row_stripe(sheet: Image.Image, y: int):
    block = Image.new("RGBA", (WIDTH - 56, ROW_H - 2), (0, 0, 0, 0))
    ImageDraw.Draw(block).rounded_rectangle(
        [0, 0, block.width - 1, block.height - 1], 6, fill=ROW_FILL)
    sheet.paste(block, (28, y - 2), block)


def _star_dy(draw: ImageDraw.ImageDraw) -> int:
    """Centred on the name's ink, which sits below the row box's middle."""
    global _STAR_DY
    if _STAR_DY is None:
        font = PlayerCanvas._font(FONT_NAME, NAME_SIZE)
        top, bottom = draw.textbbox((0, TEXT_DY), "Xa", font=font)[1::2]
        height = card_render._tier_star(0, STAR_SCALE).height
        _STAR_DY = round((top + bottom - height) / 2)
    return _STAR_DY


def _stars_x() -> int:
    global _STARS_X
    if _STARS_X is None:
        width = card_render._tier_star(0, STAR_SCALE).width
        _STARS_X = STARS_CENTER - (STAR_SLOTS * width
                                   + (STAR_SLOTS - 1) * STAR_GAP) // 2
    return _STARS_X


def _draw_stars(sheet: Image.Image, draw: ImageDraw.ImageDraw, level: int,
                y: int):
    icons = [card_render._tier_star(0, STAR_SCALE)] * (STAR_SLOTS - level) \
        + [card_render._tier_star(level, STAR_SCALE)] * level
    x = _stars_x()
    for icon in icons:
        sheet.paste(icon, (x, y + _star_dy(draw)), icon)
        x += icon.width + STAR_GAP


def _draw_row(sheet: Image.Image, draw: ImageDraw.ImageDraw, stack: Stack,
              y: int, striped: bool):
    card = stack.card
    tier = display_tier(card)
    style = card_render.TIERS.get(tier, card_render.TIERS["normal"])
    if striped:
        _row_stripe(sheet, y)

    badge = _badge(tier)
    if badge:
        sheet.paste(badge, (BADGE_X, y + (ROW_H - 2 - badge.height) // 2 - 2),
                    badge)

    name = card["name"]
    font = PlayerCanvas._fit_font(name, draw, FONT_NAME, NAME_SIZE, NAME_MAX_W,
                                  min_size=14)
    text_y = y + TEXT_DY
    max_stars = cardlib.tier_max_stars(card)
    if max_stars and stack.star >= max_stars:
        card_render._draw_rainbow_text(sheet, name, font, NAME_X, text_y)
    else:
        draw.text((NAME_X, text_y), name, font=font, fill=style["badge"])

    if max_stars and stack.star > 0:
        _draw_stars(sheet, draw, card_render._tier_level(stack.star, max_stars),
                    y)

    draw.text((COPIES_CENTER, text_y), str(stack.count),
              font=PlayerCanvas._font(FONT_NAME, NAME_SIZE),
              fill=WHITE, anchor="ma")


STAT_GAP = 18


def _runs(draw: ImageDraw.ImageDraw, runs: list[tuple[str, str, int]], x: int,
          y: int, font):
    for text, color, gap in runs:
        draw.text((x, y), text, font=font, fill=color)
        x += draw.textlength(text, font=font) + gap


COLUMNS = [(BADGE_X, "RARITY", "la"), (NAME_X, "CARD", "la"),
           (STARS_CENTER, "STARS", "ma"), (COPIES_CENTER, "COPIES", "ma")]


def _draw_head(sheet: Image.Image, draw: ImageDraw.ImageDraw, title: str,
               stats: list[tuple[str, str]]):
    draw.text((38, 26), title.upper(),
              font=PlayerCanvas._font(FONT_LABEL, 28), fill=ACCENT)

    runs = []
    for value, label in stats:
        runs += [(f"{label}: ", ACCENT, 0), (value, WHITE, STAT_GAP)]
    _runs(draw, runs, 38, 62, PlayerCanvas._font(FONT_LABEL, 20))

    for x, label, anchor in COLUMNS:
        draw.text((x, 100), label, font=PlayerCanvas._font(FONT_LABEL, 20),
                  fill=ACCENT, anchor=anchor)


def render_page(stacks: list[Stack], index: int, title: str,
                stats: list[tuple[str, str]]) -> Image.Image:
    chunk = stacks[index * ROWS_PER_PAGE:(index + 1) * ROWS_PER_PAGE]
    total = page_count(stacks)
    rows = ROWS_PER_PAGE if total > 1 else max(1, len(chunk))
    height = HEAD_H + rows * ROW_H + FOOT_H
    sheet, draw = _panel(height)
    _draw_head(sheet, draw, title, stats)

    for i, stack in enumerate(chunk):
        _draw_row(sheet, draw, stack, HEAD_H + i * ROW_H, striped=i % 2 == 0)

    page = f"Page {index + 1}" if total == 1 else f"Page {index + 1} of {total}"
    font = PlayerCanvas._font(FONT_LABEL, 20)
    box = draw.textbbox((0, 0), page, font=font)
    draw.text((WIDTH - 22 - box[2], height - 22 - box[3]), page, font=font,
              fill=ACCENT)
    return sheet


def page_file(stacks: list[Stack], index: int, title: str,
              stats: list[tuple[str, str]]):
    import discord

    # RGBA, not RGB: the rounded corners are alpha and flattening squares them.
    buf = BytesIO()
    render_page(stacks, index, title, stats).save(buf, format="PNG")
    buf.seek(0)
    return discord.File(buf, filename=f"tank_{index}_{int(time.time())}.png",
                        description=alt_text(stacks, index, title))


def alt_text(stacks: list[Stack], index: int, title: str) -> str:
    chunk = stacks[index * ROWS_PER_PAGE:(index + 1) * ROWS_PER_PAGE]
    lines = []
    for s in chunk:
        tier = LIMITED if s.card.get("member") else s.card["tier"].capitalize()
        stars = f" {s.star}★" if s.star else ""
        lines.append(f"{tier} {s.card['name']}{stars} ×{s.count}")
    return f"{title}: " + ", ".join(lines)[:950]
