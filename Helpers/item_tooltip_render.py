import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont

Color = tuple[int, int, int]
WHITE: Color = (255, 255, 255)
GREEN: Color = (85, 255, 85)
RED: Color = (255, 85, 85)
BACKGROUND: Color = (26, 10, 46)
TIER_COLORS: dict[str, Color] = {
    "mythic": (170, 0, 170),
    "fabled": (255, 85, 85),
    "legendary": (85, 255, 255),
    "rare": (255, 85, 255),
    "unique": (255, 255, 85),
    "common": WHITE,
}

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_PATH = os.path.join(BASE, "images", "profile", "game.ttf")
STAT_NAMES_PATH = os.path.join(BASE, "data", "stat-names.json")
_FONT_CACHE: dict[int, ImageFont.FreeTypeFont] = {}


def _font(size: int) -> ImageFont.FreeTypeFont:
    if size not in _FONT_CACHE:
        _FONT_CACHE[size] = ImageFont.truetype(FONT_PATH, size)
    return _FONT_CACHE[size]


@lru_cache(maxsize=1)
def stat_names() -> dict[str, str]:
    with open(STAT_NAMES_PATH, encoding="utf-8-sig") as file:
        mapping = json.load(file)
    if not isinstance(mapping, dict) or not mapping or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in mapping.items()
    ):
        raise ValueError("Invalid stat-names.json")
    return mapping


def _number(value: Any, label: str) -> float:
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+(?:\.\d+)?%?", value.strip()):
        value = float(value.strip().removesuffix("%"))
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Missing or invalid number for {label}: {value!r}")
    return float(value)


def _rate(value: Any, label: str) -> float:
    rate = _number(value, label)
    if not 0 <= rate <= 100:
        raise ValueError(f"Roll percentage outside 0..100 for {label}")
    return rate


ROLL_COLOR_STOPS: tuple[tuple[float, Color], ...] = (
    (0, (255, 85, 85)),
    (40, (255, 170, 0)),
    (70, (255, 255, 85)),
    (90, (85, 255, 85)),
    (100, (85, 255, 255)),
)


def roll_color(value: Any) -> Color:
    rate = max(0.0, min(100.0, _number(value, "roll color")))
    for (start_rate, start), (end_rate, end) in zip(ROLL_COLOR_STOPS, ROLL_COLOR_STOPS[1:]):
        if rate <= end_rate:
            fraction = (rate - start_rate) / (end_rate - start_rate)
            channels = [round(a + (b - a) * fraction) for a, b in zip(start, end)]
            return channels[0], channels[1], channels[2]
    return ROLL_COLOR_STOPS[-1][1]


def stat_value_color(name: str, value: Any) -> Color:
    number = _number(value, name)
    if number == 0:
        return WHITE
    lower_is_better = re.search(r"\bspell cost\b", name, re.IGNORECASE) is not None
    return GREEN if (number < 0 if lower_is_better else number > 0) else RED


@dataclass(frozen=True)
class Scale:
    name: str
    score: float


def calculate_custom_scales(item: Mapping[str, Any]) -> list[Scale]:
    weights = item.get("wynnpoolWeights")
    if weights is None:
        return []
    if not isinstance(weights, list):
        raise ValueError("Invalid Wynnpool weight response")
    mapping = stat_names()
    scales = []
    for scale in weights:
        if not isinstance(scale, dict) or not isinstance(scale.get("weight_name"), str):
            raise ValueError("Invalid Wynnpool scale definition")
        name = scale["weight_name"]
        identifications = scale.get("identifications")
        if not name or not isinstance(identifications, dict):
            raise ValueError("Invalid Wynnpool scale definition")
        weighted_rolls = []
        total_weights = []
        for key, weight in identifications.items():
            if isinstance(weight, bool) or not isinstance(weight, (int, float)):
                raise ValueError(f"Invalid scale weight: {key}")
            weight = _number(weight, key)
            if weight < 0:
                raise ValueError(f"Negative scale weight: {key}")
            if weight == 0:
                continue
            rate = _rate(item["rate"].get(mapping.get(key, key)), f"{name}: {key}")
            weighted_rolls.append(rate * weight)
            total_weights.append(weight)
        total = math.fsum(total_weights)
        if total <= 0:
            raise ValueError(f"Empty scale: {name}")
        scales.append(Scale(name, math.fsum(weighted_rolls) / total))
    return scales


def item_from_api(decode_response: Mapping[str, Any], wynnpool_weights: list[dict[str, Any]]) -> dict[str, Any]:
    original = decode_response.get("original")
    rolled = decode_response.get("input")
    if not isinstance(original, dict) or not isinstance(rolled, dict):
        raise ValueError("Incomplete Wynnpool decode response")
    internal_name = original.get("displayName")
    full_name = original.get("id")
    tier = original.get("tier")
    if (
        not isinstance(internal_name, str) or not internal_name
        or not isinstance(full_name, str) or not full_name
        or not isinstance(tier, str) or not tier
    ):
        raise ValueError("Missing item name or rarity")
    rolled_stats = rolled.get("identifications")
    ranges = original.get("identifications")
    if not isinstance(rolled_stats, dict) or not rolled_stats or not isinstance(ranges, dict):
        raise ValueError("Incomplete item identification data")
    if not isinstance(wynnpool_weights, list) or not all(
        isinstance(scale, dict) and scale.get("item_id") == internal_name for scale in wynnpool_weights
    ):
        raise ValueError("Invalid weights or scales belonging to another item")
    mapping = stat_names()
    stats, mapped_rates = {}, {}
    for key, percent_of_nominal in rolled_stats.items():
        stat_range = ranges.get(key)
        if not isinstance(stat_range, dict):
            raise ValueError(f"Missing roll range for {key}")
        min_value = _number(stat_range.get("min"), key)
        max_value = _number(stat_range.get("max"), key)
        raw_value = _number(stat_range.get("raw"), key)
        if max_value == min_value:
            raise ValueError(f"Degenerate roll range for {key}")
        actual_value = _number(percent_of_nominal, key) / 100 * raw_value
        rate = (actual_value - min_value) / (max_value - min_value) * 100
        label = mapping.get(key, key)
        if label in stats:
            raise ValueError(f"Duplicate mapped stat: {label}")
        stats[label] = actual_value
        mapped_rates[label] = _rate(rate, key)
    item = {
        "itemName": full_name,
        "internalName": internal_name,
        "tier": tier,
        "stats": stats,
        "rate": mapped_rates,
        "reroll": rolled.get("rerollCount"),
        "shiny": None,
        "wynnpoolWeights": sorted(wynnpool_weights, key=lambda scale: scale.get("weight_name") != "Main"),
    }
    calculate_custom_scales(item)
    return item


@dataclass(frozen=True)
class Segment:
    text: str
    color: Color = WHITE


@dataclass(frozen=True)
class Line:
    segments: tuple[Segment, ...] = ()
    size: int = 15

    @property
    def text(self) -> str:
        return "".join(segment.text for segment in self.segments)


def _compact(number: float) -> str:
    return f"{number:.2f}".rstrip("0").rstrip(".")


def build_lines(item: Mapping[str, Any]) -> list[Line]:
    name, tier = item.get("itemName"), item.get("tier")
    if not isinstance(name, str) or not name or not isinstance(tier, str) or not tier:
        raise ValueError("Missing item name or rarity")
    stats, rates = item.get("stats"), item.get("rate")
    if not isinstance(stats, dict) or not stats or not isinstance(rates, dict):
        raise ValueError("Missing item stats or roll percentages")
    average = math.fsum(_rate(rates.get(key), key) for key in stats) / len(stats)
    shiny = item.get("shiny")
    is_shiny = isinstance(shiny, dict)
    title = ("✨ Shiny " if is_shiny else "") + name
    lines = [
        Line((Segment(title + " ", TIER_COLORS.get(tier.lower(), WHITE)),
              Segment(f"[{average:.2f}%]", roll_color(average))), 22),
        Line(size=4),
    ]
    for scale in calculate_custom_scales(item):
        lines.append(Line((Segment(f" - {scale.name} Scale "),
                           Segment(f"[{scale.score:.2f}%]", roll_color(scale.score))), 14))
    if item.get("overall") is not None:
        overall = _rate(item["overall"], "mainscale")
        lines.append(Line((Segment("Mainscale: "), Segment(f"{overall:.1f}%", roll_color(overall))), 14))
    lines.append(Line(size=8))
    weights = item.get("weight") or {}
    if not isinstance(weights, dict):
        raise ValueError("Invalid per-stat weights")
    reverse_mapping = {value: key for key, value in stat_names().items()}
    for label, raw_value in stats.items():
        value = _number(raw_value, label)
        rate = _rate(rates.get(label), label)
        percent = label.endswith(" %")
        value_text = ("+" if value >= 0 else "") + _compact(value) + ("%" if percent else "")
        display_name = label[:-2] if percent else label
        segments = [
            Segment(value_text, stat_value_color(label, value)),
            Segment(f" {display_name} "),
            Segment(f"[{_compact(rate)}%]", roll_color(rate)),
        ]
        weight = weights.get(reverse_mapping.get(label, label))
        if weight is not None and _number(weight, label) != 0:
            segments.append(Segment(f" *{_compact(_number(weight, label))}%"))
        lines.append(Line(tuple(segments)))
    lines.append(Line(size=8))
    footer = f"{tier.capitalize()} Item"
    reroll = item.get("reroll")
    if reroll is not None:
        count = _number(reroll, "rerolls")
        if count < 0 or not count.is_integer():
            raise ValueError("Rerolls must be a nonnegative integer")
        if count > 0:
            footer += f" [{int(count)}]"
    if is_shiny and shiny.get("key"):
        footer += f"  •  ✨ {shiny['key']}: {shiny.get('value', 0)}"
    lines.append(Line((Segment(footer),), 13))
    if len(lines) > 160 or any(len(line.text) > 400 for line in lines):
        raise ValueError("Tooltip exceeds rendering limits")
    return lines


def render_item_tooltip(item: Mapping[str, Any]) -> bytes:
    lines = build_lines(item)
    supersample = 3
    fonts = {line.size: _font(line.size * supersample) for line in lines}
    widths = [
        sum(fonts[line.size].getlength(segment.text) for segment in line.segments) / supersample
        for line in lines
    ]
    width = max(270, math.ceil(max(widths) + 48))
    height = 28 + sum(line.size + 4 for line in lines)
    if width > 2048 or height > 4096:
        raise ValueError("Tooltip exceeds image size limit")
    size = (width * supersample, height * supersample)
    image = Image.new("RGBA", size, (*BACKGROUND, 255))
    border = ImageDraw.Draw(image)
    border.rectangle((3, 3, size[0] - 4, size[1] - 4), outline=(85, 0, 170), width=6)
    border.rectangle((9, 9, size[0] - 10, size[1] - 10), outline=(40, 0, 122), width=3)
    layer = Image.new("RGBA", size)
    draw = ImageDraw.Draw(layer)
    y = 14 * supersample
    for line in lines:
        font = fonts[line.size]
        x = 14 * supersample
        for segment in line.segments:
            draw.text((x, y), segment.text, font=font, fill=(*segment.color, 199), anchor="la")
            x += font.getlength(segment.text)
        y += (line.size + 4) * supersample
    rendered = Image.alpha_composite(image, layer).convert("RGB").resize(
        (width, height), Image.Resampling.LANCZOS
    )
    output = BytesIO()
    rendered.save(output, format="PNG")
    return output.getvalue()
