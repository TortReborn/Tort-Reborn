import html
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from typing import Any, Callable

from PIL import Image, ImageDraw


Color = tuple[int, int, int]
WHITE: Color = (255, 255, 255)
GRAY: Color = (170, 170, 170)
DARK_GRAY: Color = (85, 85, 85)
GREEN: Color = (85, 255, 85)
RED: Color = (255, 85, 85)
BLACK: Color = (0, 0, 0)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSET_ROOT = os.path.join(BASE, "images", "item_tooltip")
GUI_ROOT = os.path.join(ASSET_ROOT, "gui")
FONT_ROOT = os.path.join(ASSET_ROOT, "font")

TIER_COLORS: dict[str, Color] = {
    "normal": WHITE,
    "common": WHITE,
    "unique": (255, 255, 85),
    "rare": (255, 85, 255),
    "legendary": (85, 255, 255),
    "fabled": (255, 85, 85),
    "mythic": (170, 0, 170),
    "crafted": (0, 170, 170),
}
DIVIDER_COLORS: dict[str, Color] = {
    "normal": (224, 224, 224),
    "common": (224, 224, 224),
    "unique": (255, 242, 179),
    "rare": (242, 194, 242),
    "legendary": (194, 242, 242),
    "fabled": (242, 194, 194),
    "mythic": (224, 179, 230),
    "crafted": WHITE,
}
SKIN_BORDERS = {
    "mythic": (14, 20, 14, 20),
    "fabled": (14, 20, 14, 20),
    "legendary": (12, 20, 12, 20),
    "rare": (12, 20, 12, 20),
    "unique": (12, 20, 12, 20),
    "normal": (10, 12, 10, 12),
    "crafted": (10, 12, 10, 12),
}
TIER_FRAME_INDEX = {
    "normal": 0,
    "common": 0,
    "unique": 1,
    "rare": 2,
    "legendary": 3,
    "fabled": 4,
    "mythic": 5,
    "crafted": 6,
}
TYPE_SPRITES = {
    "helmet": 0,
    "chestplate": 1,
    "leggings": 2,
    "boots": 3,
    "bow": 4,
    "dagger": 5,
    "wand": 6,
    "relik": 7,
    "spear": 8,
    "ring": 13,
    "bracelet": 14,
    "necklace": 15,
    "weapon_tome": 26,
    "armor_tome": 26,
    "guild_tome": 26,
    "lootrun_tome": 26,
    "charm": 28,
}
REWARD_EMBLEMS = {
    "tome": ("square", 5),
    "charm": ("circle", 4),
}
RESTRICTION_BANNER_ROWS = {
    "untradable": 2,
    "untradable item": 2,
    "quest": 3,
    "quest item": 3,
}
SKILLS = ("strength", "dexterity", "intelligence", "defence", "agility")
CLASS_NAMES = {
    "warrior": "Warrior/Knight",
    "mage": "Mage/Dark Wizard",
    "assassin": "Assassin/Ninja",
    "archer": "Archer/Hunter",
    "shaman": "Shaman/Skyseer",
}
SPELL_NAMES = {
    "warrior": ("Bash", "Charge", "Uppercut", "War Scream"),
    "mage": ("Heal", "Teleport", "Meteor", "Ice Snake"),
    "assassin": ("Spin Attack", "Dash", "Multihit", "Smoke Bomb"),
    "archer": ("Arrow Storm", "Escape", "Arrow Bomb", "Arrow Shield"),
    "shaman": ("Totem", "Haul", "Aura", "Uproot"),
}
SKILL_STAT_ICONS = {
    "rawStrength": 16,
    "rawDexterity": 17,
    "rawIntelligence": 18,
    "rawDefence": 19,
    "rawAgility": 20,
}
ATTACK_SPEEDS = {
    "superSlow": ("Super Slow", "0.51"),
    "verySlow": ("Very Slow", "0.83"),
    "slow": ("Slow", "1.5"),
    "normal": ("Normal", "2.05"),
    "fast": ("Fast", "2.5"),
    "veryFast": ("Very Fast", "3.1"),
    "superFast": ("Super Fast", "4.3"),
}
ELEMENTS = ("earth", "thunder", "water", "fire", "air")
ELEMENT_INDEX = {element: index for index, element in enumerate(ELEMENTS)}

STAT_GROUPS = (
    ("rawStrength", "rawDexterity", "rawIntelligence", "rawDefence", "rawAgility"),
    (
        "rawHealth", "healthRegenRaw", "healthRegen", "healingEfficiency", "reflection",
        "thorns", "lifeSteal", "manaRegen", "manaSteal", "sprint", "sprintRegen",
    ),
    (
        "elementalDefence", "elementalDefense", "earthDefence", "thunderDefence",
        "waterDefence", "fireDefence", "airDefence",
    ),
    (
        "rawElementalSpellDamage", "elementalSpellDamage", "spellDamage", "rawSpellDamage",
        "elementalDamage", "damage", "mainAttackDamage", "rawMainAttackDamage",
        "earthDamage", "thunderDamage", "waterDamage", "fireDamage", "airDamage",
    ),
    (
        "1stSpellCost", "raw1stSpellCost", "2ndSpellCost", "raw2ndSpellCost",
        "3rdSpellCost", "raw3rdSpellCost", "4thSpellCost", "raw4thSpellCost",
    ),
)


def _image(path: str) -> Image.Image:
    return Image.open(path).convert("RGBA")


@lru_cache(maxsize=None)
def _asset(relative: str) -> Image.Image:
    return _image(os.path.join(ASSET_ROOT, relative))


class BitmapFont:
    _rows = (
        "\0" * 16,
        "\0" * 16,
        " !\"#$%&'()*+,-./",
        "0123456789:;<=>?",
        "@ABCDEFGHIJKLMNO",
        "PQRSTUVWXYZ[\\]^_",
        "`abcdefghijklmno",
        "pqrstuvwxyz{|}~\0",
    )

    def __init__(self):
        self.image = _asset("font/wynncraft.png")
        self.positions = {
            char: (column * 8, row * 8)
            for row, chars in enumerate(self._rows)
            for column, char in enumerate(chars)
            if char != "\0"
        }
        self.widths = {char: self._glyph_width(char) for char in self.positions}
        self.widths[" "] = 4

    def _glyph_width(self, char: str) -> int:
        x, y = self.positions[char]
        box = self.image.crop((x, y, x + 8, y + 8)).getchannel("A").getbbox()
        return (box[2] if box else 3) + 1

    def width(self, text: str, scale: float = 1.0) -> int:
        return round(sum(self.widths.get(char, self.widths["?"]) for char in text) * scale)

    def draw(
        self,
        target: Image.Image,
        position: tuple[int, int],
        text: str,
        color: Color = WHITE,
        shadow: bool = True,
        scale: float = 1.0,
    ) -> int:
        x, y = position
        start = x
        for char in text:
            advance = round(self.widths.get(char, self.widths["?"]) * scale)
            if char != " ":
                source = char if char in self.positions else "?"
                sx, sy = self.positions[source]
                mask = self.image.crop((sx, sy, sx + 8, sy + 8)).getchannel("A")
                if scale != 1:
                    size = (max(1, round(8 * scale)), max(1, round(8 * scale)))
                    mask = mask.resize(size, Image.Resampling.NEAREST)
                if shadow:
                    shadow_color = tuple(channel // 4 for channel in color)
                    layer = Image.new("RGBA", mask.size, (*shadow_color, 255))
                    target.paste(layer, (x + max(1, round(scale)), y + max(1, round(scale))), mask)
                layer = Image.new("RGBA", mask.size, (*color, 255))
                target.paste(layer, (x, y), mask)
            x += advance
        return x - start


FONT = BitmapFont()


class QuadFont:
    _rows = (
        "\0!\"#$%&'()*+,-./",
        "0123456789:;<=>?",
        "@ABCDEFGHIJKLMNO",
        "PQRSTUVWXYZ[\\]^_",
        "`abcdefghijklmno",
        "pqrstuvwxyz{|}~\0",
    )

    def __init__(self):
        self.image = _asset("font/wynncraft_quad.png")
        self.positions = {
            char: (column * 32, row * 32)
            for row, chars in enumerate(self._rows)
            for column, char in enumerate(chars)
            if char != "\0"
        }
        self.widths = {char: self._glyph_width(char) for char in self.positions}
        self.widths[" "] = 4

    def _glyph_width(self, char: str) -> int:
        x, y = self.positions[char]
        box = self.image.crop((x, y, x + 32, y + 32)).getchannel("A").getbbox()
        return math.floor((box[2] if box else 8) * 12 / 32) + 1

    def width(self, text: str) -> int:
        return sum(self.widths.get(char, self.widths["?"]) for char in text)

    def draw_final(
        self,
        target: Image.Image,
        position: tuple[int, int],
        text: str,
        color: Color,
    ) -> None:
        x, y = position
        for char in text:
            advance = self.widths.get(char, self.widths["?"]) * 2
            if char != " ":
                source = char if char in self.positions else "?"
                sx, sy = self.positions[source]
                mask = self.image.crop((sx, sy, sx + 32, sy + 32)).getchannel("A")
                mask = mask.resize((24, 24), Image.Resampling.NEAREST)
                shadow = Image.new("RGBA", mask.size, (*(channel // 4 for channel in color), 255))
                target.paste(shadow, (x + 2, y + 2), mask)
                layer = Image.new("RGBA", mask.size, (*color, 255))
                target.paste(layer, (x, y), mask)
            x += advance


QUAD_FONT = QuadFont()


class BannerBoxFont:
    _characters = "abcdefghijklmnopqrstuvwxyz?[]/%&0123456789!()<=>"
    _foreground = {character: index for index, character in enumerate(_characters)}
    _foreground.update({"+": 0x70, "-": 0x71, ".": 0x72})
    _background = {
        character: index + 0x30 for character, index in _foreground.items() if index < 0x30
    }
    _background.update({"+": 0x80, "-": 0x81, ".": 0x82})

    def __init__(self):
        self.image = _asset("font/banner_box.png")

    def _glyph(self, index: int) -> Image.Image:
        x, y = index % 16 * 8, index // 16 * 8
        return self.image.crop((x, y, x + 8, y + 8))

    def _advance(self, index: int) -> int:
        box = self._glyph(index).getchannel("A").getbbox()
        return (box[2] if box else 3) + 1

    def _draw_glyph(
        self,
        target: Image.Image,
        position: tuple[int, int],
        index: int,
        color: Color,
    ) -> None:
        target.alpha_composite(_tinted(self._glyph(index), color), position)

    def render(self, text: str, background: Color, foreground: Color = BLACK) -> Image.Image:
        text = text.lower()
        background_glyphs = []
        foreground_glyphs = []
        for character in text:
            if character == " ":
                background_glyphs.append(0x61)
                foreground_glyphs.append(None)
            else:
                character = character if character in self._foreground else "?"
                background_glyphs.append(self._background[character])
                foreground_glyphs.append(self._foreground[character])

        left_advance = self._advance(0x60) - 1
        background_width = left_advance
        background_width += sum(self._advance(index) - 1 for index in background_glyphs)
        background_width += self._advance(0x62)
        foreground_width = 2 + sum(
            4 if index is None else self._advance(index) for index in foreground_glyphs
        )
        image = Image.new("RGBA", (max(background_width, foreground_width), 8))

        self._draw_glyph(image, (0, 0), 0x60, background)
        x = left_advance
        for index in background_glyphs:
            self._draw_glyph(image, (x, 0), index, background)
            x += self._advance(index) - 1
        self._draw_glyph(image, (x, 0), 0x62, background)

        x = 2
        for index in foreground_glyphs:
            if index is None:
                x += 4
                continue
            self._draw_glyph(image, (x, 0), index, foreground)
            x += self._advance(index)
        return image


BANNER_BOX_FONT = BannerBoxFont()


@dataclass(frozen=True)
class Text:
    value: str
    color: Color = WHITE


@dataclass
class Line:
    left: tuple[Text, ...] = ()
    right: tuple[Text, ...] = ()
    center: tuple[Text, ...] = ()
    draw: Callable[[Image.Image, int, int], None] | None = None
    final_draw: Callable[[Image.Image, int, int], None] | None = None
    minimum_width: int = 0


def _segments_width(segments: tuple[Text, ...]) -> int:
    return sum(FONT.width(segment.value) for segment in segments)


def _draw_segments(image: Image.Image, x: int, y: int, segments: tuple[Text, ...]) -> None:
    for segment in segments:
        x += FONT.draw(image, (x, y), segment.value, segment.color)


def _tinted(source: Image.Image, color: Color) -> Image.Image:
    alpha = source.getchannel("A")
    result = Image.new("RGBA", source.size, (*color, 255))
    result.putalpha(alpha)
    return result


def _multiplied_tint(source: Image.Image, color: Color) -> Image.Image:
    channels = source.convert("RGBA").split()
    result = Image.merge("RGBA", tuple(
        channels[index].point(lambda value, channel=color[index]: value * channel // 255)
        for index in range(3)
    ) + (channels[3],))
    return result


def _nine_slice(source: Image.Image, size: tuple[int, int], borders: tuple[int, int, int, int]) -> Image.Image:
    width, height = size
    left, top, right, bottom = borders
    source_width, source_height = source.size
    target = Image.new("RGBA", size)
    boxes = (
        ((0, 0, left, top), (0, 0, left, top)),
        ((left, 0, source_width - right, top), (left, 0, width - right, top)),
        ((source_width - right, 0, source_width, top), (width - right, 0, width, top)),
        ((0, top, left, source_height - bottom), (0, top, left, height - bottom)),
        ((left, top, source_width - right, source_height - bottom), (left, top, width - right, height - bottom)),
        ((source_width - right, top, source_width, source_height - bottom), (width - right, top, width, height - bottom)),
        ((0, source_height - bottom, left, source_height), (0, height - bottom, left, height)),
        ((left, source_height - bottom, source_width - right, source_height), (left, height - bottom, width - right, height)),
        ((source_width - right, source_height - bottom, source_width, source_height), (width - right, height - bottom, width, height)),
    )
    for source_box, target_box in boxes:
        piece = source.crop(source_box)
        target_size = (target_box[2] - target_box[0], target_box[3] - target_box[1])
        if piece.size != target_size:
            piece = piece.resize(target_size, Image.Resampling.NEAREST)
        target.alpha_composite(piece, (target_box[0], target_box[1]))
    return target


def _skin(tier: str, size: tuple[int, int]) -> Image.Image:
    skin = tier if tier in SKIN_BORDERS else "normal"
    background = _asset(f"gui/{skin}_background.png")
    frame = _asset(f"gui/{skin}_frame.png")
    image = _nine_slice(background, size, SKIN_BORDERS[skin])
    image.alpha_composite(_nine_slice(frame, size, SKIN_BORDERS[skin]))
    return image


def _crop_grid(relative: str, columns: int, rows: int, index: int) -> Image.Image:
    atlas = _asset(relative)
    cell_width, cell_height = atlas.width // columns, atlas.height // rows
    column, row = index % columns, index // columns
    return atlas.crop((
        column * cell_width,
        row * cell_height,
        (column + 1) * cell_width,
        (row + 1) * cell_height,
    ))


def _paste(image: Image.Image, sprite: Image.Image, position: tuple[int, int], size: tuple[int, int]) -> None:
    image.alpha_composite(sprite.resize(size, Image.Resampling.NEAREST), position)


def _draw_attribute_sprite(image: Image.Image, x: int, y: int, index: int) -> None:
    sprite = _crop_grid("font/tooltip/attribute/sprite.png", 16, 2, index)
    x_offset = -1 if index == 0 else 1 if index in {1, 2, 3, 4} else 0
    width = 23 if index in {0, 1, 2, 3, 4, 5} else 24
    image.alpha_composite(
        sprite.resize((width, 24), Image.Resampling.NEAREST),
        (x + x_offset, y - 4),
    )


def _draw_requirement_mark(image: Image.Image, x: int, y: int, index: int) -> None:
    mark = _crop_grid("font/tooltip/requirement/linear.png", 3, 1, index)
    mark = mark.resize((16, 16), Image.Resampling.NEAREST)
    image.alpha_composite(mark, (x, y))


def _roll_color(value: float) -> Color:
    stops = (
        (0, (255, 85, 85)),
        (40, (255, 170, 0)),
        (70, (255, 255, 85)),
        (90, (85, 255, 85)),
        (100, (85, 255, 255)),
    )
    value = max(0.0, min(100.0, value))
    for (start, start_color), (end, end_color) in zip(stops, stops[1:]):
        if value <= end:
            fraction = (value - start) / (end - start)
            return tuple(round(a + (b - a) * fraction) for a, b in zip(start_color, end_color))
    return stops[-1][1]


def _compact(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _average(item: Mapping[str, Any]) -> float:
    rates = item.get("rate") or {}
    return math.fsum(float(value) for value in rates.values()) / len(rates) if rates else 0


def _divider(tier: str) -> Line:
    color = DIVIDER_COLORS.get(tier, WHITE)

    def draw(image: Image.Image, width: int, y: int) -> None:
        divider = _tinted(_asset("font/tooltip/divider/line.png"), color)
        image.alpha_composite(divider, ((width - divider.width) // 2, y + 2))

    return Line(draw=draw)


def _banner(text: str, background: Color, foreground: Color = BLACK) -> Image.Image:
    return BANNER_BOX_FONT.render(text, background, foreground)


def _header(item: Mapping[str, Any], tier: str) -> list[Line]:
    entry = item.get("entry") or {}
    name = str(item.get("itemName") or entry.get("displayName") or "Item")
    average = _average(item)
    has_average = bool(item.get("rate"))
    category = str(entry.get("type") or "").lower()
    item_type = (
        "TOME" if category == "tome"
        else "CHARM" if category == "charm"
        else str(entry.get("subType") or entry.get("type") or "item").upper()
    )
    reward_emblem = REWARD_EMBLEMS.get(category)
    if reward_emblem:
        shape, variant = reward_emblem
    else:
        emblem = str(entry.get("emblem") or "diamond_1").lower().split("_")
        shape = emblem[0]
        try:
            variant = max(1, min(6, int(emblem[1])))
        except (IndexError, ValueError):
            variant = 1
    shape_index = {"diamond": 0, "square": 1, "hexagon": 2, "shield": 3, "sticker": 4, "circle": 5}.get(shape, 0)
    frame = _crop_grid("font/tooltip/emblem/frame.png", 6, 6, (variant - 1) * 6 + shape_index)
    sprite_index = TYPE_SPRITES.get(str(entry.get("subType") or "").lower(), 6)
    sprite = _crop_grid("font/tooltip/emblem/sprite.png", 9, 5, sprite_index)
    tier_color = TIER_COLORS.get(tier, WHITE)
    divider_color = DIVIDER_COLORS.get(tier, WHITE)
    restriction = str(entry.get("restriction") or "none").lower().replace("_", " ")
    restriction_row = RESTRICTION_BANNER_ROWS.get(restriction)

    def name_line(image: Image.Image, width: int, y: int) -> None:
        x = 48
        x += FONT.draw(image, (x, y), name + (" " if has_average else ""), tier_color)
        if has_average:
            FONT.draw(image, (x, y), f"[{average:.1f}%]", _roll_color(average))

    def type_line(image: Image.Image, width: int, y: int) -> None:
        x = 48
        tier_banner = _banner(tier.upper(), tier_color)
        image.alpha_composite(tier_banner, (x, y))
        x += tier_banner.width + 1
        type_banner = _banner(item_type, divider_color)
        image.alpha_composite(type_banner, (x, y))

    def restriction_icon(image: Image.Image, width: int, y: int) -> None:
        if restriction_row is None:
            return
        atlas = _asset("font/tooltip/banner.png")
        background = atlas.crop((0, restriction_row * 12, 24, (restriction_row + 1) * 12))
        overlay = atlas.crop((24, restriction_row * 12, 48, (restriction_row + 1) * 12))
        background = _tinted(background.resize((28, 14), Image.Resampling.NEAREST), (255, 66, 66))
        overlay = overlay.resize((28, 14), Image.Resampling.NEAREST)
        x = (48 + _banner(tier.upper(), tier_color).width + _banner(item_type, divider_color).width + 2) * 2
        image.alpha_composite(background, (x, y))
        image.alpha_composite(overlay, (x, y))

    def tags_line(image: Image.Image, width: int, y: int) -> None:
        elements = sorted(
            {str(value).lower() for value in entry.get("elements") or [] if str(value).lower() in ELEMENT_INDEX},
            key=ELEMENT_INDEX.get,
        )
        if not elements:
            return
        atlas = _asset("font/tooltip/banner.png")
        for position, element in enumerate(elements):
            row = 6 + ELEMENT_INDEX[element]
            background = atlas.crop((0, row * 12, 24, (row + 1) * 12))
            overlay = atlas.crop((24, row * 12, 48, (row + 1) * 12))
            x = 96 + position * 18
            image.alpha_composite(
                _tinted(background.resize((28, 14), Image.Resampling.NEAREST), divider_color),
                (x, y),
            )
            image.alpha_composite(overlay.resize((28, 14), Image.Resampling.NEAREST), (x, y))

    name_width = 42 + FONT.width(name)
    if has_average:
        name_width += FONT.width(" ") + FONT.width(f"[{average:.1f}%]")

    def header_line(image: Image.Image, width: int, y: int) -> None:
        image.alpha_composite(frame, (-6, y - 24))
        image.alpha_composite(sprite, (10, y - 7))
        name_line(image, width, y)

    lines = [
        Line(),
        Line(draw=header_line, minimum_width=name_width),
        Line(draw=type_line, final_draw=restriction_icon if restriction_row is not None else None),
    ]
    if entry.get("elements"):
        lines.append(Line(final_draw=tags_line))
    if category in {"weapon", "armor", "accessory"}:
        lines.append(Line())
    return lines


def _overview(entry: Mapping[str, Any], tier: str) -> list[Line]:
    lines: list[Line] = []
    divider_color = DIVIDER_COLORS.get(tier, WHITE)
    if entry.get("averageDps") is not None:
        dps = int(round(float(entry["averageDps"])))

        def dps_line(image: Image.Image, width: int, y: int) -> None:
            text = f"{dps:,}"
            FONT.draw(image, (10 + QUAD_FONT.width(text) + 4, y), "DPS")

        def dps_digits(image: Image.Image, width: int, y: int) -> None:
            QUAD_FONT.draw_final(image, (20, y - 8), f"{dps:,}", divider_color)

        lines.append(Line(draw=dps_line, final_draw=dps_digits))
    elif (entry.get("base") or {}).get("baseHealth") is not None:
        health = int((entry.get("base") or {})["baseHealth"])
        lines.append(Line(left=(Text(f"{health:+,} Health", divider_color),)))

    durability = entry.get("durability")
    if isinstance(durability, Mapping):
        lines.append(Line(left=(Text(
            f"  Durability {int(durability.get('current', 0))}/{int(durability.get('max', 0))}", GRAY
        ),)))

    speed = ATTACK_SPEEDS.get(str(entry.get("attackSpeed")))
    if speed:
        def speed_line(image: Image.Image, width: int, y: int) -> None:
            x = 24
            x += FONT.draw(image, (x, y), speed[0] + " ", GRAY)
            FONT.draw(image, (x, y), f"({speed[1]} hits/s)", DARK_GRAY)

        def speed_icon(image: Image.Image, width: int, y: int) -> None:
            _draw_attribute_sprite(image, 20, y, 7)

        lines.append(Line(draw=speed_line, final_draw=speed_icon))

    damages = []
    base = entry.get("base") or {}
    damage_keys = (("baseDamage", 5),) + tuple((f"base{element.capitalize()}Damage", index) for element, index in ELEMENT_INDEX.items())
    for key, icon_index in damage_keys:
        value = base.get(key)
        if isinstance(value, Mapping):
            damages.append((icon_index, int(value.get("min", value.get("raw", 0))), int(value.get("max", value.get("raw", 0)))))
    if damages:
        rows: list[list[tuple[int, int, int]]] = [[]]
        for damage in damages:
            if len(rows[-1]) == 3:
                rows.append([])
            rows[-1].append(damage)
        for row in rows:
            def damage_line(image: Image.Image, width: int, y: int, row=row) -> None:
                x = 10
                for icon_index, minimum, maximum in row:
                    x += 14
                    x += FONT.draw(image, (x, y), f"{minimum}-{maximum} ", GRAY)

            def damage_icons(image: Image.Image, width: int, y: int, row=row) -> None:
                x = 10
                for icon_index, minimum, maximum in row:
                    _draw_attribute_sprite(image, x * 2, y, icon_index)
                    x += 14 + FONT.width(f"{minimum}-{maximum} ")

            lines.append(Line(draw=damage_line, final_draw=damage_icons))
    return lines


def _weights(item: Mapping[str, Any]) -> list[Line]:
    scales = item.get("calculatedScales") or []
    if not scales:
        return []
    def source_line(image: Image.Image, width: int, y: int) -> None:
        FONT.draw(image, (23, y), "Wynnpool")

    def source_icon(image: Image.Image, width: int, y: int) -> None:
        icon = _crop_grid("font/tooltip/wynntils_sprite.png", 2, 1, 1)
        icon = icon.resize((18, 18), Image.Resampling.NEAREST)
        shadow_color = tuple(channel // 4 for channel in (250, 198, 172))
        image.alpha_composite(_multiplied_tint(icon, shadow_color), (22, y + 1))
        image.alpha_composite(_multiplied_tint(icon, (250, 198, 172)), (20, y - 1))

    lines = [Line(draw=source_line, final_draw=source_icon)]
    for scale in scales:
        score = float(scale["score"])
        lines.append(Line(
            left=(Text(f"{scale['name']} Scale"),),
            right=(Text(f"[{score:.1f}%]", _roll_color(score)),),
        ))
    return lines


def _requirements(entry: Mapping[str, Any], tier: str) -> list[Line]:
    requirements = entry.get("requirements") or {}
    if not isinstance(requirements, Mapping):
        return []
    skill_values = [int(requirements.get(skill, 0) or 0) for skill in SKILLS]
    lines: list[Line] = []
    if any(skill_values):
        lines.append(Line())

        def skill_icons(image: Image.Image, width: int, y: int) -> None:
            start = (width - 135) // 2
            frame_index = TIER_FRAME_INDEX.get(tier, 0)
            for index, value in enumerate(skill_values):
                x = start + index * 27
                frame = _crop_grid("font/tooltip/requirement/frame.png", 8, 1, frame_index if value else 7)
                sprite = _crop_grid("font/tooltip/requirement/sprite.png", 5, 2, index if value else 5 + index)
                _paste(image, frame, (x, y - 7), (24, 24))
                _paste(image, sprite, (x, y - 12), (24, 24))

        lines.extend((Line(draw=skill_icons), Line()))

        def skill_numbers(image: Image.Image, width: int, y: int) -> None:
            start = (width - 135) // 2
            for index, value in enumerate(skill_values):
                text = str(value)
                content_width = 7 + FONT.width(text)
                x = start + index * 27 + (24 - content_width) // 2
                FONT.draw(image, (x + 11, y + 1), text, (172, 250, 198) if value else DARK_GRAY)

        def skill_number_icons(image: Image.Image, width: int, y: int) -> None:
            start = (width // 2 - 135) // 2
            for index, value in enumerate(skill_values):
                text = str(value)
                content_width = 7 + FONT.width(text)
                x = start + index * 27 + (24 - content_width) // 2
                _draw_requirement_mark(image, x * 2 + 2, y + 1, 1 if value else 0)

        lines.extend((Line(draw=skill_numbers, final_draw=skill_number_icons), Line()))
    class_requirement = requirements.get("classRequirement")
    if class_requirement:
        class_name = CLASS_NAMES.get(str(class_requirement).lower(), str(class_requirement))

        def class_line(image: Image.Image, width: int, y: int) -> None:
            FONT.draw(image, (18, y), "Class Type")
            FONT.draw(image, (width - 10 - FONT.width(class_name), y), class_name, GRAY)

        def class_icon(image: Image.Image, width: int, y: int) -> None:
            _draw_requirement_mark(image, 20, y - 1, 1)

        lines.append(Line(draw=class_line, final_draw=class_icon))
    level = requirements.get("level")
    if isinstance(level, (int, float)) and level > 0:
        level_text = str(int(level))

        def level_line(image: Image.Image, width: int, y: int) -> None:
            FONT.draw(image, (18, y), "Combat Level")
            FONT.draw(image, (width - 10 - FONT.width(level_text), y), level_text, GRAY)

        def level_icon(image: Image.Image, width: int, y: int) -> None:
            _draw_requirement_mark(image, 20, y - 1, 1)

        lines.append(Line(draw=level_line, final_draw=level_icon))
    return lines


def _stat_groups(item: Mapping[str, Any]) -> list[list[Mapping[str, Any]]]:
    stats = list(item.get("renderStats") or [])
    by_key = {str(stat.get("key")): stat for stat in stats}
    groups: list[list[Mapping[str, Any]]] = []
    used: set[str] = set()
    for group_keys in STAT_GROUPS:
        group = [by_key[key] for key in group_keys if key in by_key]
        if group:
            groups.append(group)
            used.update(str(stat["key"]) for stat in group)
    remaining = [stat for stat in stats if str(stat.get("key")) not in used]
    if remaining:
        groups.append(remaining)
    return groups


def _spell_label(key: str, label: str, entry: Mapping[str, Any]) -> str:
    match = re.search(r"([1-4])(?:st|nd|rd|th)SpellCost", key)
    class_name = str((entry.get("requirements") or {}).get("classRequirement") or "").lower()
    if match and class_name in SPELL_NAMES:
        return f"{SPELL_NAMES[class_name][int(match.group(1)) - 1]} Cost"
    return label.removesuffix(" %")


def _stats(item: Mapping[str, Any], entry: Mapping[str, Any], include_reroll: bool) -> list[Line]:
    lines: list[Line] = []
    rerolls = int(item.get("reroll") or 0)

    def reroll_line(image: Image.Image, width: int, y: int) -> None:
        color = DIVIDER_COLORS.get(str(item.get("tier")), WHITE)
        badge = _banner(str(rerolls), color)
        x = width - 10 - badge.width - 9
        image.alpha_composite(badge, (x, y))

    def reroll_icon(image: Image.Image, width: int, y: int) -> None:
        color = DIVIDER_COLORS.get(str(item.get("tier")), WHITE)
        badge = _banner(str(rerolls), color)
        atlas = _asset("font/tooltip/banner.png")
        background = atlas.crop((0, 60, 24, 72)).resize((28, 14), Image.Resampling.NEAREST)
        overlay = atlas.crop((24, 60, 48, 72)).resize((28, 14), Image.Resampling.NEAREST)
        overlay_alpha = overlay.getchannel("A")
        ImageDraw.Draw(overlay_alpha).line((0, 0, overlay_alpha.width - 1, 0), fill=0)
        overlay.putalpha(overlay_alpha)
        icon = Image.new("RGBA", (28, 14))
        icon.alpha_composite(_tinted(background, color))
        icon.alpha_composite(overlay)
        x = width - 20 - badge.width * 2 - 18
        image.alpha_composite(icon, (x + badge.width * 2 - 2, y))

    if include_reroll and rerolls > 0:
        lines.append(Line(draw=reroll_line, final_draw=reroll_icon))
    for group_index, group in enumerate(_stat_groups(item)):
        if group_index:
            lines.append(Line())
        for stat in group:
            key = str(stat.get("key"))
            label = _spell_label(key, str(stat.get("label") or key), entry)
            value = float(stat.get("value", 0))
            percent_unit = str(stat.get("label") or "").endswith(" %") and "Spell Cost" not in str(stat.get("label"))
            displayed = ("+" if value >= 0 else "") + _compact(value) + ("%" if percent_unit else "")
            positive = value < 0 if "SpellCost" in key else value > 0
            right = [Text(displayed, GREEN if positive else RED)]
            rate = stat.get("rate")
            if rate is not None:
                rate_value = float(rate)
                right.extend((Text(" "), Text(f"[{rate_value:.1f}%]", _roll_color(rate_value))))
            icon_index = SKILL_STAT_ICONS.get(key) if value < 0 else None
            if icon_index is None:
                lines.append(Line(left=(Text(label),), right=tuple(right)))
                continue

            def skill_stat_label(image: Image.Image, width: int, y: int, label=label) -> None:
                FONT.draw(image, (25, y), label)

            def skill_stat_icon(image: Image.Image, width: int, y: int, icon_index=icon_index) -> None:
                _draw_attribute_sprite(image, 18, y, icon_index)

            lines.append(Line(draw=skill_stat_label, right=tuple(right), final_draw=skill_stat_icon))
    return lines


def _plain_major_ids(entry: Mapping[str, Any]) -> list[tuple[str, str]]:
    major_ids = entry.get("majorIds") or {}
    if not isinstance(major_ids, Mapping):
        return []
    result = []
    for name, description in major_ids.items():
        text = re.sub(r"<[^>]+>", "", str(description))
        result.append((str(name), html.unescape(text)))
    return result


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines = [words[0]]
    for word in words[1:]:
        candidate = lines[-1] + " " + word
        if FONT.width(candidate) <= width:
            lines[-1] = candidate
        else:
            lines.append(word)
    return lines


def _major_ids(entry: Mapping[str, Any], tier: str) -> list[Line]:
    lines: list[Line] = []
    color = DIVIDER_COLORS.get(tier, WHITE)
    for name, description in _plain_major_ids(entry):
        if lines:
            lines.append(Line())
        prefix = f"{name}: "
        wrapped = _wrap(prefix + description, 138)
        if not wrapped:
            continue

        def first_line(image: Image.Image, width: int, y: int, text=wrapped[0], prefix=prefix) -> None:
            diamond = Image.new("RGBA", (5, 5))
            diamond.putpixel((2, 0), (*color, 255))
            for x in (1, 2, 3):
                diamond.putpixel((x, 1), (*color, 255))
                diamond.putpixel((x, 3), (*color, 255))
            for x in range(5):
                diamond.putpixel((x, 2), (*color, 255))
            diamond.putpixel((2, 4), (*color, 255))
            image.alpha_composite(diamond, (10, y + 1))
            x = 17
            if text.startswith(prefix):
                x += FONT.draw(image, (x, y), prefix, color)
                FONT.draw(image, (x, y), text[len(prefix):], GRAY)
            else:
                FONT.draw(image, (x, y), text, GRAY)

        lines.append(Line(draw=first_line))
        lines.extend(Line(left=(Text(text, GRAY),)) for text in wrapped[1:])
    return lines


def _content_width(lines: list[Line]) -> int:
    width = 163
    for line in lines:
        width = max(width, line.minimum_width)
        if line.left or line.right:
            width = max(width, _segments_width(line.left) + _segments_width(line.right) + (4 if line.right else 0))
        if line.center:
            width = max(width, _segments_width(line.center))
    return min(300, width)


def _render(lines: list[Line], tier: str) -> bytes:
    content_width = _content_width(lines)
    width = content_width + 20
    height = 16 + (2 if len(lines) > 1 else 0) + max(0, len(lines) - 1) * 10 + 12
    if width > 640 or height > 2048:
        raise ValueError("Tooltip exceeds image size limit")
    image = _skin(tier, (width, height))
    y = 10
    positions: list[tuple[Line, int]] = []
    for index, line in enumerate(lines):
        positions.append((line, y))
        if line.draw:
            line.draw(image, width, y)
            if line.right:
                _draw_segments(image, width - 10 - _segments_width(line.right), y, line.right)
        elif line.center:
            line_width = _segments_width(line.center)
            _draw_segments(image, (width - line_width) // 2, y, line.center)
        else:
            _draw_segments(image, 10, y, line.left)
            if line.right:
                _draw_segments(image, width - 10 - _segments_width(line.right), y, line.right)
        y += 12 if index == 0 and len(lines) > 1 else 10
    image = image.resize((width * 2, height * 2), Image.Resampling.NEAREST)
    for line, line_y in positions:
        if line.final_draw:
            line.final_draw(image, width * 2, line_y * 2)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def render_item(item: Mapping[str, Any]) -> bytes:
    tier = str(item.get("tier") or "normal").lower()
    entry = item.get("entry") or {}
    category = str(entry.get("type") or "").lower()
    is_gear = category in {"weapon", "armor", "accessory"}
    lines = _header(item, tier)
    lines.extend(_overview(entry, tier))
    weight_lines = _weights(item)
    if weight_lines:
        lines.append(_divider(tier))
        lines.extend(weight_lines)
    lines.append(_divider(tier))
    lines.extend(_requirements(entry, tier))
    lines.append(_divider(tier))
    lines.extend(_stats(item, entry, is_gear))
    major_lines = _major_ids(entry, tier)
    if major_lines:
        lines.append(Line())
        lines.extend(major_lines)
    return _render(lines, tier)


def render_crafted(item: Mapping[str, Any], fallback_lines: list[Any]) -> bytes:
    gear_type = str(item.get("gearType") or "item").lower()
    if gear_type in {"spear", "wand", "dagger", "bow", "relik"}:
        category, shape = "weapon", "diamond"
    elif gear_type in {"helmet", "chestplate", "leggings", "boots"}:
        category, shape = "armor", "shield"
    else:
        category, shape = "accessory", "circle"
    requirements = item.get("requirements") or {}
    entry_requirements = {
        "level": requirements.get("level", 0),
        "classRequirement": str(requirements.get("class") or "").lower() or None,
    }
    crafted_skills = {
        "earth": "strength",
        "thunder": "dexterity",
        "water": "intelligence",
        "fire": "defence",
        "air": "agility",
    }
    for key, value in (requirements.get("skills") or {}).items():
        skill = crafted_skills.get(str(key).lower(), str(key).lower())
        entry_requirements[skill] = value
    entry: dict[str, Any] = {
        "type": category,
        "subType": gear_type,
        "tier": "crafted",
        "emblem": f"{shape}_4",
        "requirements": entry_requirements,
        "durability": item.get("durability"),
        "base": {},
    }
    damage = item.get("damage")
    if isinstance(damage, Mapping):
        entry["averageDps"] = damage.get("dps")
        speed = str(damage.get("attackSpeed") or "").replace(" ", "")
        entry["attackSpeed"] = speed[:1].lower() + speed[1:]
        for damage_type, minimum, maximum in damage.get("damages") or []:
            key = "baseDamage" if str(damage_type).lower() == "neutral" else f"base{damage_type}Damage"
            entry["base"][key] = {"min": minimum, "max": maximum}
    defence = item.get("defense")
    if isinstance(defence, Mapping):
        entry["base"]["baseHealth"] = defence.get("health", 0)
    crafted = dict(item)
    crafted.update({"tier": "crafted", "entry": entry, "rate": {}})
    lines = _header(crafted, "crafted")
    lines.extend(_overview(entry, "crafted"))
    lines.append(_divider("crafted"))
    lines.extend(_requirements(entry, "crafted"))
    lines.append(_divider("crafted"))
    lines.extend(_stats(crafted, entry, False))
    return _render(lines, "crafted")
