import asyncio
import math
import os
import time
from io import BytesIO
from urllib.parse import quote

import discord
import requests
from discord.ext import commands
from discord.commands import slash_command
from PIL import Image, ImageDraw, ImageFont


DEBUG_SKILLCARD = True

FONT_PATH = "images/profile/game.ttf"
TITLE_FONT_PATH = "images/profile/5x5.ttf"
PROFILE_ACCENT = (250, 213, 30)
PROFILE_EDGE_TOP = (28, 36, 64)
PROFILE_EDGE_BOTTOM = (10, 14, 28)
PROFILE_INNER_TOP = (30, 52, 96)
PROFILE_INNER_BOTTOM = (14, 24, 52)
PROFILE_OVERLAY = (0, 0, 0, 90)
PROFILE_OVERLAY_DARK = (0, 0, 0, 140)
SKIN_CACHE = {}
CACHE_DIR = os.path.join("images", "cache", "skins")


def _debug(message: str) -> None:
    if DEBUG_SKILLCARD:
        print(f"[skillcard] {message}")


def _get_nested(data, *keys):
    cur = data
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def fetch_wynn_player(username: str):
    safe_name = quote(username)
    url = f"https://api.wynncraft.com/v3/player/{safe_name}?fullResult"
    headers = {}
    token = os.getenv("WYNN_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    _debug(f"Fetching player data: {url}")
    try:
        resp = requests.get(url, headers=headers, timeout=12)
    except requests.RequestException as exc:
        _debug(f"Wynn API request failed: {exc}")
        return None, f"API request failed: {exc}"
    _debug(f"Wynn API status: {resp.status_code}")
    if resp.status_code != 200:
        return None, f"API status {resp.status_code}"
    try:
        payload = resp.json()
    except ValueError as exc:
        _debug(f"Wynn API JSON parse failed: {exc}")
        return None, "Invalid API response"
    data = payload.get("data") or ([payload] if payload.get("username") else [])
    player = data[0] if data else None
    if player is None:
        return None, "Player not found"
    return player, None


def extract_wynn_stats(player: dict):
    stats = {}

    playtime = player.get("playtime")
    stats["playtime"] = playtime if isinstance(playtime, (int, float)) else None

    characters = player.get("characters")
    discoveries = None
    if isinstance(characters, dict):
        total = 0
        found = False
        for ch in characters.values():
            if isinstance(ch, dict) and "discoveries" in ch:
                val = ch.get("discoveries")
                if isinstance(val, (int, float)):
                    total += int(val)
                    found = True
        if found:
            discoveries = total
    stats["discoveries"] = discoveries

    quests = _get_nested(player, "globalData", "completedQuests")
    stats["quests"] = quests if isinstance(quests, (int, float)) else None

    total_levels = _get_nested(player, "globalData", "totalLevel")
    stats["total_levels"] = total_levels if isinstance(total_levels, (int, float)) else None

    raids = _get_nested(player, "globalData", "raids", "total")
    stats["raids"] = raids if isinstance(raids, (int, float)) else None

    wars = _get_nested(player, "globalData", "wars")
    stats["wars"] = wars if isinstance(wars, (int, float)) else None

    private_fields = [key for key, val in stats.items() if val is None]
    return stats, private_fields


def calculate_elo(stats: dict):
    playtime = stats["playtime"] if stats["playtime"] is not None else 0
    discoveries = stats["discoveries"] if stats["discoveries"] is not None else 0
    quests = stats["quests"] if stats["quests"] is not None else 0
    total_levels = stats["total_levels"] if stats["total_levels"] is not None else 0
    raids = stats["raids"] if stats["raids"] is not None else 0
    wars = stats["wars"] if stats["wars"] is not None else 0

    playtime_elo = 15000 * (1 - math.exp(-playtime / 1000))
    discoveries_elo = 3534 * math.log(0.01 * discoveries ** 0.9 + 1)
    quests_elo = 8987 * math.log(0.008 * quests + 1)
    total_levels_elo = 10417 * math.log(0.0016 * total_levels + 1)
    raids_elo = 51918 * math.log(0.002 * raids + 1) / math.log(35)
    wars_elo = 48162 * math.log(0.00017 * wars + 1) / math.log(5.25)
    total_elo = (
        playtime_elo + discoveries_elo + quests_elo +
        total_levels_elo + raids_elo + wars_elo
    )
    return {
        "playtime_elo": playtime_elo,
        "discoveries_elo": discoveries_elo,
        "quests_elo": quests_elo,
        "total_levels_elo": total_levels_elo,
        "raids_elo": raids_elo,
        "wars_elo": wars_elo,
        "total_elo": total_elo
    }


def get_tier(total_elo: float) -> str:
    tiers = [
        ("Copper I", 0, 300), ("Copper II", 300, 600), ("Copper III", 600, 1000),
        ("Bronze I", 1000, 2000), ("Bronze II", 2000, 3000), ("Bronze III", 3000, 4000),
        ("Iron I", 4000, 5000), ("Iron II", 5000, 7000), ("Iron III", 7000, 9000),
        ("Silver I", 9000, 11000), ("Silver II", 11000, 14000), ("Silver III", 14000, 16000),
        ("Gold I", 16000, 19000), ("Gold II", 19000, 22000), ("Gold III", 22000, 25000),
        ("Cobalt I", 25000, 29000), ("Cobalt II", 29000, 32000), ("Cobalt III", 32000, 36000),
        ("Diamond I", 36000, 40000), ("Diamond II", 40000, 45000), ("Diamond III", 45000, 49000),
        ("Molten I", 49000, 56000), ("Molten II", 56000, 64000), ("Molten III", 64000, 71000),
        ("Void I", 71000, 80000), ("Void II", 80000, 91000), ("Void III", 91000, 100000),
    ]
    for name, lower, upper in tiers:
        if lower <= total_elo < upper:
            return name
    if total_elo >= 100000:
        return "Dernic"
    return "Unranked"


def create_gradient(size, start_color, end_color):
    base = Image.new("RGB", size, start_color)
    top = Image.new("RGB", size, end_color)
    mask = Image.new("L", size)
    for y in range(size[1]):
        ratio = y / size[1]
        value = int(255 * ratio)
        ImageDraw.Draw(mask).line([(0, y), (size[0], y)], fill=value)
    return Image.composite(top, base, mask)


def round_corners(image, radius=75):
    width, height = image.size
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, width, height], radius=radius, fill=255)
    rounded = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    rounded.paste(image.convert("RGBA"), (0, 0), mask=mask)
    return rounded


def draw_rounded_overlay(image, box, radius, fill):
    overlay = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle([0, 0, box[2] - box[0], box[3] - box[1]], radius=radius, fill=fill)
    image.paste(overlay, (box[0], box[1]), overlay)


def draw_top_overlay(image, box, height_ratio=0.25, fill=(30, 45, 65), radius=25):
    width = box[2] - box[0]
    height = int((box[3] - box[1]) * height_ratio)
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle([0, 0, width, height * 2], radius=radius, fill=fill)
    image.paste(overlay, (box[0], box[1]), overlay)


def _format_stat_parts(label: str, value, suffix: str = ""):
    if value is None:
        return label, "Private"
    if suffix:
        return label, f"{value}{suffix}"
    return label, f"{value}"


def _draw_stat_line(draw, x, y, label, value, font, label_color, value_color):
    label_text = f"{label}: "
    draw.text((x, y), label_text, fill=label_color, font=font)
    label_bbox = draw.textbbox((x, y), label_text, font=font)
    draw.text((label_bbox[2], y), value, fill=value_color, font=font)


def _skin_cache_path(cache_key: str) -> str:
    safe_key = "".join(ch for ch in cache_key.lower() if ch.isalnum())
    if not safe_key:
        safe_key = "unknown"
    return os.path.join(CACHE_DIR, f"{safe_key}.bin")


def _load_skin_cache(cache_key: str):
    cached = SKIN_CACHE.get(cache_key)
    if cached:
        try:
            return Image.open(BytesIO(cached)).convert("RGBA"), cached
        except Exception:
            return None, None

    path = _skin_cache_path(cache_key)
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        return Image.open(BytesIO(data)).convert("RGBA"), data
    except Exception:
        return None, None


def _save_skin_cache(cache_key: str, data: bytes) -> None:
    if not data:
        return
    SKIN_CACHE[cache_key] = data
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = _skin_cache_path(cache_key)
        with open(path, "wb") as handle:
            handle.write(data)
    except Exception:
        pass


def create_stats_image_with_bigger_top(stats, elo, username, uuid=None):
    width, height = 1024, 1536
    outer_start = PROFILE_EDGE_TOP
    outer_end = PROFILE_EDGE_BOTTOM
    image = create_gradient((width, height), outer_start, outer_end)
    draw = ImageDraw.Draw(image)

    margin = 38
    inner_box = [margin, margin, width - margin, height - margin]
    inner_width = inner_box[2] - inner_box[0]
    inner_height = inner_box[3] - inner_box[1]

    inner_start = PROFILE_INNER_TOP
    inner_end = PROFILE_INNER_BOTTOM
    inner_gradient = create_gradient((inner_width, inner_height), inner_start, inner_end)
    image.paste(inner_gradient, (inner_box[0], inner_box[1]))

    overlay_width = int(inner_width * 0.45)
    overlay_height = int(height * 0.3 * 0.8)
    spacing = (inner_width - 2 * overlay_width) // 3
    bottom_y = inner_box[3] - overlay_height - spacing // 2 - int(spacing * 0.5)

    rect1 = [inner_box[0] + spacing, bottom_y, inner_box[0] + spacing + overlay_width, bottom_y + overlay_height]
    rect2 = [inner_box[2] - spacing - overlay_width, bottom_y, inner_box[2] - spacing, bottom_y + overlay_height]

    middle_height = int(overlay_height * 0.44)
    middle_y = rect1[1] - middle_height - spacing
    middle_rect = [rect1[0], middle_y, rect2[2], middle_y + middle_height]

    original_top_height = int(middle_y - inner_box[1]) // 2
    top_height = int(original_top_height * 1.3)
    top_y = inner_box[1] + ((middle_y - inner_box[1]) - top_height) // 2
    top_rect = [rect1[0], top_y, rect2[2], top_y + top_height]

    background_padding = 8
    backgrounds = [
        [rect1[0] - background_padding, rect1[1] - background_padding, rect1[2] + background_padding, rect1[3] + background_padding],
        [rect2[0] - background_padding, rect2[1] - background_padding, rect2[2] + background_padding, rect2[3] + background_padding],
        [middle_rect[0] - background_padding, middle_rect[1] - background_padding, middle_rect[2] + background_padding, middle_rect[3] + background_padding],
        [top_rect[0] - background_padding, top_rect[1] - background_padding, top_rect[2] + background_padding, top_rect[3] + background_padding],
    ]
    for bg in backgrounds:
        bg_img = round_corners(create_gradient((bg[2] - bg[0], bg[3] - bg[1]), outer_start, outer_end), 32)
        image.paste(bg_img, (bg[0], bg[1]), bg_img)

    overlay_color = PROFILE_OVERLAY
    draw_rounded_overlay(image, rect1, 25, overlay_color)
    draw_rounded_overlay(image, rect2, 25, overlay_color)
    draw_rounded_overlay(image, middle_rect, 25, overlay_color)
    draw_rounded_overlay(image, top_rect, 25, overlay_color)

    username_font = ImageFont.truetype(FONT_PATH, 80)
    username_text = f"{username}"
    username_x = top_rect[0]
    username_y = inner_box[1] + 23
    draw.text((username_x, username_y), username_text, fill="white", font=username_font)

    try:
        uuid_no_hyphens = None
        if uuid:
            uuid_no_hyphens = uuid.replace("-", "")
        if not uuid_no_hyphens:
            uuid_url = f"https://api.mojang.com/users/profiles/minecraft/{username}"
            uuid_response = requests.get(uuid_url, timeout=8)
            uuid_response.raise_for_status()
            uuid_data = uuid_response.json()
            uuid_no_hyphens = uuid_data.get("id")

        if not uuid_no_hyphens:
            raise Exception("No UUID available for skin lookup")

        cache_key = uuid_no_hyphens or username.lower()
        skin_img, cached_bytes = _load_skin_cache(cache_key)

        skin_services = [
            f"https://visage.surgeplay.com/bust/{uuid_no_hyphens}?no=3d&width=600",
            f"https://mc-heads.net/body/{uuid_no_hyphens}/400",
            f"https://crafatar.com/renders/body/{uuid_no_hyphens}?scale=8&overlay",
            f"https://minotar.net/armor/body/{username}/400.png",
            f"https://mc-heads.net/avatar/{username}/200"
        ]

        if skin_img is None:
            for service_url in skin_services:
                try:
                    _debug(f"Fetching skin: {service_url}")
                    skin_response = requests.get(service_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
                    if skin_response.status_code == 200:
                        skin_img = Image.open(BytesIO(skin_response.content)).convert("RGBA")
                        data = skin_img.getdata()
                        new_data = []
                        for item in data:
                            if item[0] < 15 and item[1] < 15 and item[2] < 15:
                                new_data.append((255, 255, 255, 0))
                            else:
                                new_data.append(item)
                        skin_img.putdata(new_data)
                        _save_skin_cache(cache_key, skin_response.content)
                        _debug(f"Skin service succeeded: {service_url}")
                        break
                    _debug(f"Skin service status {skin_response.status_code}: {service_url}")
                except Exception as exc:
                    _debug(f"Skin service failed {service_url}: {exc}")
                    continue

        if skin_img is None:
            raise Exception("All skin services failed")

        max_width = int((top_rect[2] - top_rect[0]) * 0.85)
        max_height = int(top_height * 0.9)

        width, height = skin_img.size
        aspect_ratio = width / height

        if (max_width / max_height) > aspect_ratio:
            new_height = max_height
            new_width = int(new_height * aspect_ratio)
        else:
            new_width = max_width
            new_height = int(new_width / aspect_ratio)

        skin_img = skin_img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        skin_x = top_rect[0] + (top_rect[2] - top_rect[0] - new_width) // 2
        skin_y = top_rect[3] - new_height - 10

        image.paste(skin_img, (skin_x, skin_y), skin_img)

    except Exception as exc:
        _debug(f"Couldn't fetch skin: {exc}")
        try:
            avatar_size = min(400, int(top_height * 0.8))
            avatar_img = Image.new('RGBA', (avatar_size, avatar_size), (0, 0, 0, 0))
            draw_avatar = ImageDraw.Draw(avatar_img)

            head_size = int(avatar_size * 0.5)
            head_x = (avatar_size - head_size) // 2
            head_y = int(avatar_size * 0.1)

            draw_avatar.ellipse([head_x, head_y, head_x + head_size, head_y + head_size],
                                fill=(200, 150, 100))

            body_width = int(head_size * 0.8)
            body_height = int(avatar_size * 0.5)
            body_x = (avatar_size - body_width) // 2
            body_y = head_y + head_size - int(head_size * 0.2)
            draw_avatar.rectangle([body_x, body_y, body_x + body_width, body_y + body_height],
                                  fill=(50, 50, 200))

            initial_font = ImageFont.truetype(FONT_PATH, int(avatar_size * 0.4))
            initial = username[0].upper() if username else "?"
            initial_bbox = draw_avatar.textbbox((0, 0), initial, font=initial_font)
            initial_width = initial_bbox[2] - initial_bbox[0]
            initial_height = initial_bbox[3] - initial_bbox[1]
            draw_avatar.text(
                ((avatar_size - initial_width) // 2,
                 (head_size - initial_height) // 2 + head_y - 5),
                initial,
                fill="white",
                font=initial_font
            )

            avatar_x = top_rect[0] + (top_rect[2] - top_rect[0] - avatar_size) // 2
            avatar_y = top_rect[3] - avatar_size - 10

            image.paste(avatar_img, (avatar_x, avatar_y), avatar_img)
        except Exception as exc:
            _debug(f"Couldn't generate fallback avatar: {exc}")

    title_font = ImageFont.truetype(TITLE_FONT_PATH, 38)
    lighter_overlay_color = PROFILE_OVERLAY_DARK

    draw_top_overlay(image, rect1, height_ratio=0.25, fill=lighter_overlay_color, radius=25)
    title_left = "COMPLETION STATS"
    title_left_x = rect1[0] + 20
    title_left_bbox = draw.textbbox((0, 0), title_left, font=title_font)
    title_left_height = title_left_bbox[3] - title_left_bbox[1]
    title_left_y = rect1[1] + (int(overlay_height * 0.25) - title_left_height) // 2 - 5
    draw.text((title_left_x, title_left_y), title_left, fill=PROFILE_ACCENT, font=title_font)

    draw_top_overlay(image, rect2, height_ratio=0.25, fill=lighter_overlay_color, radius=25)
    title_right = "ACTIVITY STATS"
    title_right_x = rect2[0] + 20
    title_right_bbox = draw.textbbox((0, 0), title_right, font=title_font)
    title_right_height = title_right_bbox[3] - title_right_bbox[1]
    title_right_y = rect2[1] + (int(overlay_height * 0.25) - title_right_height) // 2 - 5
    draw.text((title_right_x, title_right_y), title_right, fill=PROFILE_ACCENT, font=title_font)

    font = ImageFont.truetype(FONT_PATH, 38)

    left_text = [
        _format_stat_parts("Discoveries", stats.get("discoveries")),
        _format_stat_parts("Total Levels", stats.get("total_levels")),
        _format_stat_parts("Quests", stats.get("quests")),
    ]
    left_text_area_height = rect1[3] - (rect1[1] + int(overlay_height * 0.25))
    left_line_spacing = left_text_area_height // (len(left_text) + 1)
    y_left = rect1[1] + int(overlay_height * 0.25) + left_line_spacing // 2
    for label, value in left_text:
        _draw_stat_line(
            draw,
            rect1[0] + 20,
            y_left,
            label,
            value,
            font,
            PROFILE_ACCENT,
            "white",
        )
        y_left += left_line_spacing

    playtime_val = stats.get("playtime")
    if playtime_val is not None:
        playtime_val = int(playtime_val)
    right_text = [
        _format_stat_parts("Raids", stats.get("raids")),
        _format_stat_parts("Playtime", playtime_val, " hrs" if playtime_val is not None else ""),
        _format_stat_parts("Wars", stats.get("wars")),
    ]
    right_text_area_height = rect2[3] - (rect2[1] + int(overlay_height * 0.25))
    right_line_spacing = right_text_area_height // (len(right_text) + 1)
    y_right = rect2[1] + int(overlay_height * 0.25) + right_line_spacing // 2
    for label, value in right_text:
        _draw_stat_line(
            draw,
            rect2[0] + 20,
            y_right,
            label,
            value,
            font,
            PROFILE_ACCENT,
            "white",
        )
        y_right += right_line_spacing

    middle_text = [
        f"ELO: {elo['total_elo']:.1f}",
        f"Tier: {get_tier(elo['total_elo'])}",
    ]
    middle_text_area_height = middle_rect[3] - middle_rect[1]
    middle_line_spacing = middle_text_area_height // (len(middle_text) + 1)
    y_middle = middle_rect[1] + middle_line_spacing // 2
    for idx, line in enumerate(middle_text):
        line_color = PROFILE_ACCENT if idx == 0 else "white"
        draw.text((middle_rect[0] + 20, y_middle), line, fill=line_color, font=font)
        y_middle += middle_line_spacing

    return round_corners(image, radius=38)


class Skillcard(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(description='Displays a Wynncraft skill card for a player')
    async def skillcard(self, ctx: discord.ApplicationContext, name: discord.Option(str, required=True)):
        await ctx.defer()
        try:
            buf, filename, private_fields = await asyncio.to_thread(self._build_skillcard, name)
        except Exception as exc:
            _debug(f"Unhandled skillcard error: {exc}")
            embed = discord.Embed(
                title=':no_entry: Oops! Something did not go as intended.',
                description=f'Could not retrieve information of `{name}`.\nPlease try again later.',
                color=0xe33232
            )
            await ctx.followup.send(embed=embed, ephemeral=True)
            return

        if buf is None:
            embed = discord.Embed(
                title=':no_entry: Oops! Something did not go as intended.',
                description=f'Could not retrieve information of `{name}`.\nPlease check your spelling or try again later.',
                color=0xe33232
            )
            await ctx.followup.send(embed=embed, ephemeral=True)
            return

        _debug(f"Private/missing fields: {private_fields}")
        await ctx.followup.send(file=discord.File(buf, filename=filename))

    def _build_skillcard(self, username: str):
        player, err = fetch_wynn_player(username)
        if err:
            _debug(f"Failed to fetch player: {err}")
            return None, None, []

        display_name = player.get("username") or player.get("displayName") or username
        uuid = player.get("uuid") or player.get("UUID")

        stats, private_fields = extract_wynn_stats(player)
        _debug(f"Stats extracted for {display_name}: {stats}")

        elo = calculate_elo(stats)
        _debug(f"Total ELO: {elo['total_elo']:.2f}")

        img = create_stats_image_with_bigger_top(stats, elo, display_name, uuid=uuid)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        filename = f"skillcard_{display_name}_{int(time.time())}.png"
        return buf, filename, private_fields

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Skillcard(client))
