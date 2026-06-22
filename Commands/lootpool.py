import asyncio

import discord
import requests
from discord.ext import commands
from discord.commands import SlashCommandGroup
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from Helpers.variables import mythics, WYNNVENTORY_API_KEY
from Helpers.rate_limiter import external_rate_limit
from Helpers.functions import wrap_text, get_multiline_text_size
from Helpers.database import DB
from Helpers.logger import log, ERROR
import os
import json
import re
from pathlib import Path


# Ward colors (used for their name rendering in lootpool aspects only currently)
WARD_COLORS = {
    "Pink Ward":   (255, 105, 180, 255),
    "Orange Ward": (255, 140,   0, 255),
    "Green Ward":  ( 34, 197,  94, 255),
    "Red Ward":    (220,  38,  38, 255),
    "Blue Ward":   ( 59, 130, 246, 255),
    "Purple Ward": (168,  85, 247, 255),
    "Yellow Ward": (250, 204,  21, 255),
}



class LootPool(commands.Cog):
    lootpool = SlashCommandGroup(
        name="lootpool",
        description="Commands to fetch weekly lootpool data",
        integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install},
        contexts={discord.InteractionContextType.guild, discord.InteractionContextType.bot_dm, discord.InteractionContextType.private_channel},
    )

    def __init__(self, client):
        self.client = client

    RAID_DISPLAY_ORDER = ["TNA", "TCC", "NOL", "NOTG", "WTP"]
    LOOTRUN_REGION_ORDER = ["SICamp1", "CorkusCamp1", "SKYCamp1", "MHCamp1", "SCotlCamp1", "EFrumaCamp1", "WFrumaCamp1"]
    LOOTRUN_REGION_META: dict[str, tuple] = {
        "SICamp1":    ("Silent Expanse Expedition",            (85, 227, 64, 255),  (33, 33, 33, 255)),
        "CorkusCamp1":("The Corkus Traversal",                 (237, 202, 59, 255), (107, 77, 22, 255)),
        "SKYCamp1":   ("Sky Islands Exploration",              (88, 214, 252, 255), (31, 55, 108, 255)),
        "MHCamp1":    ("Molten Heights Hike",                  (189, 30, 30, 255),  (99, 11, 11, 255)),
        "SCotlCamp1": ("Canyon of the Lost Excursion (South)", (52, 64, 235, 255),  (21, 27, 115, 255)),
        "EFrumaCamp1":("Fruma Foray (East)",                   (220, 130, 220, 255),(80, 30, 80, 255)),
        "WFrumaCamp1":("Fruma Foray (West)",                   (130, 220, 220, 255),(30, 80, 80, 255)),
    }
    WARD_ICON_DIR = Path("images/wards")
    MYTHIC_ICON_DIR = Path("images/mythics")
    ASPECT_ICON_DIR = Path("images/raids")

    WYNNVENTORY_BASE_URL = "https://wynnventory.com"
    OFFICIAL_API_BASE_URL = "https://api.wynncraft.com"

    WYNNVENTORY_RAID_MAP = {
        "The Nameless Anomaly":    "TNA",
        "The Canyon Colossus":     "TCC",
        "Orphion's Nexus of Light": "NOL",
        "Nest of the Grootslangs": "NOTG",
        "The Wartorn Palace":      "WTP",
    }

    WYNNVENTORY_REGION_MAP = {
        "Silent Expanse":     "SICamp1",
        "Corkus":             "CorkusCamp1",
        "Sky Islands":        "SKYCamp1",
        "Molten Heights":     "MHCamp1",
        "Canyon of the Lost": "SCotlCamp1",
        "Fruma Foray (East)": "EFrumaCamp1",
        "Fruma Foray (West)": "WFrumaCamp1",
    }

    WYNNVENTORY_ASPECT_CLASS_MAP = {
        "ArcherAspect":   "archer",
        "MageAspect":     "mage",
        "WarriorAspect":  "warrior",
        "AssassinAspect": "assassin",
        "ShamanAspect":   "shaman",
    }

    # Friday resets: loot 19:00 UTC, raids/aspects 18:00 UTC; gambits reset daily at 18:00 UTC
    LOOT_RESET_WEEKDAY = 4
    LOOT_RESET_HOUR_UTC = 19
    RAID_RESET_WEEKDAY = 4
    RAID_RESET_HOUR_UTC = 18
    GAMBIT_RESET_HOUR_UTC = 18

    def _as_mapping(self, value):
        return value if isinstance(value, dict) else {}

    def _as_list(self, value):
        return value if isinstance(value, list) else []

    def _ordered_keys(self, payload: dict, preferred_order: list[str]) -> list[str]:
        payload = self._as_mapping(payload)
        ordered = [key for key in preferred_order if key in payload]
        ordered.extend(key for key in payload.keys() if key not in ordered)
        return ordered

    def _clean_gambit_text(self, value) -> str:
        if not isinstance(value, str):
            return "Unknown"
        text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text or "Unknown"

    def _wrap_gambit_text(self, text: str, font, width: int, draw: ImageDraw.ImageDraw) -> str:
        wrapped_parts = []
        for line in self._clean_gambit_text(text).split("\n"):
            line = line.strip()
            if not line:
                continue
            wrapped_parts.append(wrap_text(line, font, width, draw))
        return "\n".join(wrapped_parts) if wrapped_parts else "Unknown"

    def _multiline_block_height(self, text: str, font, *, spacing: int = 0) -> int:
        base_h = get_multiline_text_size(text, font)[1]
        line_count = text.count("\n") + 1 if text else 1
        return base_h + max(0, line_count - 1) * spacing

    def _resolve_ward_icon_name(self, item_name: str | None) -> str | None:
        if not isinstance(item_name, str):
            return None
        normalized = " ".join(item_name.replace("\u00a0", " ").replace("\u00c0", " ").split()).strip().lower()
        if not normalized:
            return None
        return {
            "yellow ward": "yellow_ward.png",
            "white ward": "white_ward.png",
            "red ward": "red_ward.png",
            "purple ward": "purple_ward.png",
            "pink ward": "pink_ward.png",
            "orange ward": "orange_ward.png",
            "green ward": "green_ward.png",
            "cyan ward": "cyan_ward.png",
            "blue ward": "blue_ward.png",
            "black ward": "black_ward.png",
        }.get(normalized)

    def _load_local_icon(self, icon_path: Path) -> Image.Image | None:
        try:
            with Image.open(icon_path) as local_icon:
                return local_icon.convert("RGBA")
        except Exception as e:
            log(ERROR, f"Failed to load local icon: {icon_path} ({e})", context="lootpool")
            return None

    def _fit_icon(self, icon: Image.Image, max_size: tuple[int, int], *, upscale: bool = False,
                  resample=Image.Resampling.LANCZOS) -> Image.Image:
        """Fit an icon inside max_size, optionally allowing upscale."""
        out = icon.copy()
        if not upscale:
            out.thumbnail(max_size, resample=resample)
            return out

        w, h = out.size
        target_w, target_h = max_size
        if w <= 0 or h <= 0:
            return out

        scale = min(target_w / w, target_h / h)
        if scale <= 0:
            return out

        new_size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
        return out.resize(new_size, resample=resample)

    def _resolve_special_icon_name(self, item_name: str | None) -> str | None:
        if not isinstance(item_name, str):
            return None

        normalized = " ".join(item_name.replace("\u00a0", " ").replace("\u00c0", " ").split()).strip().lower()
        if not normalized:
            return None

        ward_icon = self._resolve_ward_icon_name(item_name)
        if ward_icon:
            return ward_icon

        misc_icon = {
            "liquid emerald": "liquid_emerald.png",
            "emerald block": "emerald_block.png",
            "emerald": "emerald.png",
            "packed crafter bag [1/1]": "crafter_packed.png",
            "stuffed crafter bag [1/1]": "crafter_stuffed.png",
            "varied crafter bag [1/1]": "crafter_varied.png",
            "corkian insulator": "insulator.png",
            "corkian simulator": "simulator.png",
            "tol rune": "tol.png",
            "uth rune": "uth.png",
            "nii rune": "nii.png",
            "az rune": "az.png",
            "ek rune": "ek.png",
        }.get(normalized)
        if misc_icon:
            return misc_icon

        if normalized.endswith(" key"):
            return "dungeon_key.png"
        if normalized.startswith("corkian amplifier"):
            return "corkian_amplifier.png"

        powder_parts = normalized.split(" powder ")
        if len(powder_parts) == 2 and powder_parts[0] in {"earth", "thunder", "water", "fire", "air"}:
            return "powder.png"

        return None

    def _load_local_lootpool_icon(self, item_name: str, max_size: tuple[int, int] = (100, 100)) -> Image.Image | None:
        ward_icon_name = self._resolve_ward_icon_name(item_name)
        if ward_icon_name:
            icon_path = self.WARD_ICON_DIR / ward_icon_name
            if not icon_path.is_file():
                log(ERROR, f"Local ward icon missing: {icon_path}", context="lootpool")
                return None
            upscale = True
            resample = Image.Resampling.NEAREST
        else:
            file_name = mythics.get(item_name) or self._resolve_special_icon_name(item_name)
            if not file_name:
                return None
            icon_path = self.MYTHIC_ICON_DIR / file_name
            if not icon_path.is_file():
                log(ERROR, f"Local lootpool icon missing: {icon_path} (item={item_name!r})", context="lootpool")
                return None
            upscale = False
            resample = Image.Resampling.LANCZOS

        cache_key = (str(icon_path), max_size, upscale)
        if cache_key not in self._icon_cache:
            raw = self._load_local_icon(icon_path)
            if raw is None:
                return None
            self._icon_cache[cache_key] = self._fit_icon(raw, max_size, upscale=upscale, resample=resample)
        return self._icon_cache[cache_key]

    def _load_local_aspect_icon(self, aspect_name: str, aspect_to_class: dict[str, str]) -> Image.Image | None:
        ward_icon_name = self._resolve_ward_icon_name(aspect_name)
        if ward_icon_name:
            ward_path = self.WARD_ICON_DIR / ward_icon_name
            if ward_path.is_file():
                return self._load_local_icon(ward_path)
            log(ERROR, f"Local ward icon missing: {ward_path}", context="lootpool")
            return None

        cls = aspect_to_class.get(aspect_name)
        if not cls:
            return None

        icon_path = self.ASPECT_ICON_DIR / f"aspect_{cls}.png"
        if not icon_path.is_file():
            log(ERROR, f"Local aspect icon missing: {icon_path} (aspect={aspect_name!r})", context="lootpool")
            return None
        return self._load_local_icon(icon_path)

    def _cache_data(self, cache_key: str, data: dict):
        """Cache API data to database"""
        try:
            db = DB()
            db.connect()
            # Set expiration to epoch time (January 1, 1970)
            epoch_time = datetime.fromtimestamp(0, tz=timezone.utc)
            # Use ON CONFLICT to either insert or update the cache entry
            db.cursor.execute("""
                INSERT INTO cache_entries (cache_key, data, expires_at, fetch_count)
                VALUES (%s, %s, %s, 1)
                ON CONFLICT (cache_key)
                DO UPDATE SET
                    data = EXCLUDED.data,
                    created_at = NOW(),
                    expires_at = EXCLUDED.expires_at,
                    fetch_count = cache_entries.fetch_count + 1,
                    last_error = NULL,
                    error_count = 0
            """, (cache_key, json.dumps(data), epoch_time))
            db.connection.commit()
            db.close()
        except Exception as e:
            log(ERROR, f"Failed to save {cache_key} to cache: {e}", context="lootpool")
            # Don't let database errors prevent the command from working
            try:
                if 'db' in locals():
                    db.close()
            except:
                pass

    @property
    def _wynnventory_headers(self) -> dict:
        return {"Authorization": f"Api-Key {WYNNVENTORY_API_KEY}"}

    def _fetch_lootpool_data(self) -> list:
        resp = requests.get(
            f"{self.WYNNVENTORY_BASE_URL}/api/lootpool/items",
            headers=self._wynnventory_headers,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def _fetch_ward_map(self) -> dict[str, list[str]]:
        try:
            resp = requests.get(
                f"{self.OFFICIAL_API_BASE_URL}/v3/map/loot-pools",
                timeout=10,
            )
            resp.raise_for_status()
            ward_map: dict[str, list[str]] = {}
            for pool in resp.json():
                internal_name = pool.get("internalName", "")
                wards = [r["name"] for r in pool.get("rewards", []) if r.get("type") == "WARD"]
                if internal_name and wards:
                    ward_map[internal_name] = wards
            return ward_map
        except Exception as e:
            log(ERROR, f"Failed to fetch ward map from official API: {e}", context="lootpool")
            return {}

    def _next_daily_reset(self, hour: int) -> datetime:
        now = datetime.now(timezone.utc)
        candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def _last_reset(self, weekday: int, hour: int) -> datetime:
        return self._next_reset(weekday, hour) - timedelta(days=7)

    def _last_daily_reset(self, hour: int) -> datetime:
        return self._next_daily_reset(hour) - timedelta(days=1)

    def _next_reset(self, weekday: int, hour: int) -> datetime:
        now = datetime.now(timezone.utc)
        days_ahead = (weekday - now.weekday()) % 7
        candidate = (now + timedelta(days=days_ahead)).replace(
            hour=hour, minute=0, second=0, microsecond=0
        )
        if candidate <= now:
            candidate += timedelta(days=7)
        return candidate

    def _format_countdown(self, target: datetime, prefix: str = "Resets in") -> str:
        delta = target - datetime.now(timezone.utc)
        total_sec = max(0, int(delta.total_seconds()))
        days, rem = divmod(total_sec, 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        if days > 0:
            return f"{prefix} {days}d {hours}h {minutes}m"
        if hours > 0:
            return f"{prefix} {hours}h {minutes}m"
        return f"{prefix} {minutes}m"

    def _extract_wynnventory_lootruns(self, data: list) -> dict:
        if not isinstance(data, list) or not data:
            return {}

        loot: dict = {}

        for entry in data:
            if not isinstance(entry, dict):
                continue
            region_name = entry.get("region", "")
            region_key = self.WYNNVENTORY_REGION_MAP.get(region_name, region_name)

            mythic_items: list[str] = []
            shiny_item: str | None = None
            shiny_tracker: str = ""

            for group_data in self._as_list(entry.get("region_items")):
                if not isinstance(group_data, dict):
                    continue
                group = group_data.get("group", "")
                items = self._as_list(group_data.get("loot_items"))
                if group == "Mythic":
                    mythic_items = [item["name"] for item in items if isinstance(item, dict) and "name" in item]
                elif group == "Shiny" and items:
                    first = items[0] if isinstance(items[0], dict) else {}
                    shiny_item = first.get("name")
                    shiny_stat = first.get("shinyStat")
                    if isinstance(shiny_stat, dict):
                        stat_type = shiny_stat.get("statType")
                        display_name = stat_type.get("displayName", "") if isinstance(stat_type, dict) else ""
                        if display_name:
                            shiny_tracker = display_name

            loot[region_key] = {
                "Mythic": mythic_items,
                "Shiny": {"Item": shiny_item, "Tracker": shiny_tracker} if shiny_item else {},
            }

        return loot

    def _extract_wynnventory_aspects(self, data: list) -> tuple[dict, dict]:
        if not isinstance(data, list) or not data:
            return {}, {}

        aspects: dict = {}
        aspect_to_class: dict[str, str] = {}

        for entry in data:
            if not isinstance(entry, dict):
                continue
            region_name = entry.get("region", "")
            raid_key = self.WYNNVENTORY_RAID_MAP.get(region_name, region_name)

            raid_aspects: dict = {"Mythic": [], "Fabled": [], "Legendary": []}

            for group_data in self._as_list(entry.get("group_items")):
                if not isinstance(group_data, dict) or group_data.get("group") != "Aspects":
                    continue
                for item in self._as_list(group_data.get("loot_items")):
                    if not isinstance(item, dict):
                        continue
                    rarity = item.get("rarity", "")
                    name = item.get("name", "")
                    if not name:
                        continue
                    if rarity in raid_aspects:
                        raid_aspects[rarity].append(name)
                    cls = self.WYNNVENTORY_ASPECT_CLASS_MAP.get(item.get("type", ""))
                    if cls:
                        aspect_to_class[name] = cls

            aspects[raid_key] = raid_aspects

        return aspects, aspect_to_class

    def _extract_wynnventory_gambits(self, data: dict) -> list[dict]:
        if not isinstance(data, dict):
            return []
        strip_codes = lambda s: re.sub(r'§.', '', s)
        result = []
        for gambit in self._as_list(data.get("gambits")):
            if not isinstance(gambit, dict):
                continue
            name = strip_codes(str(gambit.get("name", "Unknown")))
            desc_lines = self._as_list(gambit.get("description"))
            description = strip_codes(" ".join(str(line) for line in desc_lines))
            result.append({"name": name.strip(), "description": description.strip()})
        return result

    _font_cache: dict[int, "ImageFont.FreeTypeFont"] = {}
    _icon_cache: dict[tuple, "Image.Image"] = {}

    @classmethod
    def _get_font(cls, size: int) -> "ImageFont.FreeTypeFont":
        if size not in cls._font_cache:
            cls._font_cache[size] = ImageFont.truetype("images/profile/game.ttf", size)
        return cls._font_cache[size]

    def _build_lootrun_image(self, loot: dict, is_stale: bool = False) -> BytesIO:
        region_order = self._ordered_keys(loot, self.LOOTRUN_REGION_ORDER)
        region_widths = []
        longest = 0
        for region in region_order:
            region_data = self._as_mapping(loot.get(region))
            mythics_in_region = self._as_list(region_data.get("Mythic"))
            shiny_data = self._as_mapping(region_data.get("Shiny"))
            wards_in_region = self._as_list(region_data.get("Wards"))
            length = len(mythics_in_region) + (1 if shiny_data.get("Item") else 0) + len(wards_in_region)
            length = max(length, 1)
            region_widths.append(156 * length)
            longest = max(longest, length)

        w = 156 * longest
        h = 263 * len(region_order)
        lr_lp = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(lr_lp)

        shiny = Image.open("images/mythics/shiny.png").convert("RGBA")
        shiny.thumbnail((36, 36))

        item_font = self._get_font(20)
        tracker_font = self._get_font(18)
        title_font = self._get_font(40)

        count = 0
        for region_name in region_order:
            region_data = self._as_mapping(loot.get(region_name))
            r = region_widths[count]
            x1 = (w - r) / 2
            x2 = w - x1
            y1 = 35 + (255 * count)
            y2 = 250 + (255 * count)

            draw.rounded_rectangle(xy=(x1, y1, x2, y2), radius=3, fill=(0, 0, 0, 200))
            draw.rectangle(xy=(x1 + 4, y1 + 4, x2 - 4, y2 - 4), fill=(36, 0, 89, 255))
            draw.rectangle(xy=(x1 + 8, y1 + 8, x2 - 8, y2 - 8), fill=(0, 0, 0, 200))

            shiny_data = self._as_mapping(region_data.get('Shiny'))
            shiny_item = shiny_data.get('Item')
            mythic_items = self._as_list(region_data.get('Mythic'))
            ward_items = self._as_list(region_data.get('Wards', []))
            items = ([shiny_item] if isinstance(shiny_item, str) and shiny_item else []) + mythic_items + ward_items
            for i, item in enumerate(items):
                item_img = self._load_local_lootpool_icon(item, (100, 100))
                if item_img is None:
                    log(ERROR, f"Missing local icon for lootpool item: {item!r} (region={region_name})", context="lootpool")
                    item_img = self._fit_icon(
                        Image.open("images/mythics/diamond_chestplate.png").convert("RGBA"), (100, 100)
                    )
                x = int(x1 + 28 + i * 156)
                y = int(y1 + 25)
                lr_lp.paste(item_img, (x, y), item_img)
                if item == shiny_item and i < 1:
                    lr_lp.paste(shiny, (x, y), shiny)

                name_text = wrap_text(item, item_font, 156, draw)
                text_w, text_h = get_multiline_text_size(name_text, item_font)
                draw.multiline_text(
                    (x + (100 - text_w) // 2, y + 115),
                    name_text,
                    font=item_font,
                    fill=WARD_COLORS.get(item, (170, 0, 170, 255)),
                    align="center",
                    spacing=0
                )

                if item == shiny_item and i < 1:
                    lines_in_name = name_text.count("\n") + 1
                    tracker_text_raw = str(shiny_data.get('Tracker', ''))
                    wrapped_tracker = wrap_text(tracker_text_raw, tracker_font, 140, draw)
                    tracker_y = y + 115 + (lines_in_name * 20)
                    tracker_w, tracker_h = get_multiline_text_size(wrapped_tracker, tracker_font)
                    draw.multiline_text(
                        (x + (100 - tracker_w) // 2, tracker_y),
                        wrapped_tracker,
                        font=tracker_font,
                        fill=(255, 170, 0, 255),
                        align="center",
                        spacing=0
                    )

            title, fill, stroke = self.LOOTRUN_REGION_META.get(region_name, (region_name, (255, 255, 255, 255), (33, 33, 33, 255)))
            draw.text(
                xy=(w / 2, 16 + 255 * count),
                text=title,
                font=title_font,
                fill=fill,
                stroke_width=3,
                stroke_fill=stroke,
                align="center",
                anchor="mt",
            )

            count += 1

        countdown = self._format_countdown(self._next_reset(self.LOOT_RESET_WEEKDAY, self.LOOT_RESET_HOUR_UTC), prefix="Lootruns reset in")
        pill_font = self._get_font(28)
        warn_font = self._get_font(20)
        pad_x, pad_y = 28, 10
        tb = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), countdown, font=pill_font)
        pill_w, pill_h = tb[2] - tb[0] + pad_x * 2, tb[3] - tb[1] + pad_y * 2
        warn_text = "Wynnventory lootrun data hasn't updated yet" if is_stale else ""
        warn_h = (get_multiline_text_size(warn_text, warn_font)[1] + 8) if is_stale else 0
        strip_h = pad_y + warn_h + pill_h + pad_y
        out = Image.new("RGBA", (lr_lp.width, lr_lp.height + strip_h), (0, 0, 0, 0))
        out.paste(lr_lp, (0, 0))
        fd = ImageDraw.Draw(out)
        if is_stale:
            warn_w = get_multiline_text_size(warn_text, warn_font)[0]
            fd.text(
                ((out.width - warn_w) // 2, lr_lp.height + pad_y),
                warn_text,
                font=warn_font,
                fill=(255, 170, 0, 255),
            )
        pill_x = (out.width - pill_w) // 2
        pill_y = lr_lp.height + pad_y + warn_h
        fd.rounded_rectangle(
            (pill_x, pill_y, pill_x + pill_w, pill_y + pill_h),
            radius=pill_h // 2,
            fill=(14, 10, 25, 255),
            outline=(76, 30, 122, 255),
            width=2,
        )
        fd.text((pill_x + pad_x - tb[0], pill_y + pad_y - tb[1]), countdown, font=pill_font, fill=(255, 215, 90, 255))

        buf = BytesIO()
        out.save(buf, format="PNG")
        buf.seek(0)
        return buf

    def _build_aspects_image(self, loot: dict, aspect_to_class: dict, gambit_entries: list, is_stale_aspects: bool = False, is_stale_gambits: bool = False) -> BytesIO:
        raids = self._ordered_keys(loot, self.RAID_DISPLAY_ORDER)

        cols = len(raids)
        col_w = 300
        padding = 20
        line_spacing = 8
        raid_icon_size = 144
        class_icon_size = 24
        reward_section_inner_padding = 22
        reward_column_gap = 18
        reward_column_text_inset = 10
        reward_icon_text_gap = 12
        gambit_section_gap = 24
        gambit_entry_gap = 12
        gambit_card_padding = 18
        gambit_icon_size = 26
        gambit_text_spacing = 4

        title_font = self._get_font(18)
        gambit_header_font = self._get_font(24)
        gambit_name_font = self._get_font(20)
        gambit_desc_font = self._get_font(18)
        dummy_img = Image.new('RGBA', (1, 1), (0, 0, 0, 0))
        dummy_draw = ImageDraw.Draw(dummy_img)

        img_w = cols * col_w + padding * (cols + 1)
        reward_section_x0 = padding
        reward_section_x1 = img_w - padding
        rewards_content_w = (reward_section_x1 - reward_section_x0) - (reward_section_inner_padding * 2)
        reward_col_w = int((rewards_content_w - (reward_column_gap * max(0, cols - 1))) / max(1, cols))

        max_lines = 0
        max_reward_text_h = 0
        icon_col_offset = class_icon_size + 5
        for raid in raids:
            count = 0
            reward_text_h = 0
            for rarity in ["Mythic", "Fabled", "Legendary"]:
                for aspect in loot.get(raid, {}).get(rarity, []):
                    text = aspect.replace("Aspect of ", "")
                    text = text[:1].upper() + text[1:] if text else text
                    has_icon = aspect in aspect_to_class
                    text_w = reward_col_w - (reward_column_text_inset * 2) - (icon_col_offset if has_icon else 0)
                    wrapped = wrap_text(text, title_font, text_w, dummy_draw)
                    count += wrapped.count("\n") + 1
                    reward_text_h += get_multiline_text_size(wrapped, title_font)[1] + line_spacing
            max_lines = max(max_lines, count)
            max_reward_text_h = max(max_reward_text_h, reward_text_h)

        line_h = get_multiline_text_size("Test", title_font)[1]
        rewards_section_h = (
            reward_section_inner_padding +
            raid_icon_size +
            reward_icon_text_gap +
            max(max_reward_text_h, max_lines * (line_h + line_spacing)) +
            reward_section_inner_padding
        )
        raids_img_h = padding + rewards_section_h + padding

        gambit_icon = self._load_local_icon(self.ASPECT_ICON_DIR / "gambit.png")
        if gambit_icon is not None:
            gambit_icon = self._fit_icon(
                gambit_icon,
                (gambit_icon_size, gambit_icon_size),
                upscale=True,
                resample=Image.Resampling.NEAREST,
            )

        gambit_layout = []
        gambit_section_height = 0
        if gambit_entries:
            section_inner_w = img_w - (padding * 4)
            gambit_cols = max(1, min(4, len(gambit_entries)))
            gambit_col_gap = 12
            card_w = max(
                240,
                int((section_inner_w - (gambit_col_gap * (gambit_cols - 1))) / gambit_cols),
            )
            icon_offset = (gambit_icon.width + 12) if gambit_icon is not None else 0
            text_w = max(150, card_w - (gambit_card_padding * 2) - icon_offset)
            for entry in gambit_entries:
                name = self._wrap_gambit_text(entry.get("name", "Unknown"), gambit_name_font, text_w, dummy_draw)
                description = self._wrap_gambit_text(entry.get("description", "Unknown"), gambit_desc_font, text_w, dummy_draw)
                name_h = self._multiline_block_height(name, gambit_name_font, spacing=gambit_text_spacing)
                desc_h = self._multiline_block_height(description, gambit_desc_font, spacing=gambit_text_spacing)
                text_h = name_h + 8 + desc_h
                icon_h = gambit_icon.height if gambit_icon is not None else 0
                card_h = max(text_h, icon_h) + (gambit_card_padding * 2) + 8
                gambit_layout.append({
                    "name": name,
                    "description": description,
                    "name_h": name_h,
                    "card_w": card_w,
                    "card_h": card_h,
                })

            header_h = get_multiline_text_size("Current Gambits", gambit_header_font)[1]
            row_heights = []
            for row_start in range(0, len(gambit_layout), gambit_cols):
                row_entries = gambit_layout[row_start:row_start + gambit_cols]
                row_heights.append(max(entry["card_h"] for entry in row_entries))
            gambit_section_height = (
                padding +
                header_h +
                16 +
                sum(row_heights) +
                (gambit_entry_gap * max(0, len(row_heights) - 1)) +
                padding + 10
            )

        img_h = raids_img_h + (gambit_section_gap + gambit_section_height if gambit_layout else 0)

        img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        reward_section_y0 = padding
        reward_section_y1 = reward_section_y0 + rewards_section_h
        draw.rounded_rectangle(
            (reward_section_x0, reward_section_y0, reward_section_x1, reward_section_y1),
            radius=14,
            fill=(0, 0, 0, 255),
            outline=(36, 0, 89, 255),
            width=4,
        )

        for i, raid in enumerate(raids):
            x0 = reward_section_x0 + reward_section_inner_padding + i * (reward_col_w + reward_column_gap)
            x1 = x0 + reward_col_w
            column_y0 = reward_section_y0 + reward_section_inner_padding
            column_y1 = reward_section_y1 - reward_section_inner_padding
            draw.rounded_rectangle(
                (x0, column_y0, x1, column_y1),
                radius=12,
                fill=(14, 10, 25, 255),
                outline=(76, 30, 122, 255),
                width=2,
            )

            raid_path = f"images/raids/{raid}.png"
            icon_slot_y = column_y0 + reward_column_text_inset
            if os.path.isfile(raid_path):
                raid_icon = Image.open(raid_path).convert("RGBA")
                raid_icon.thumbnail((raid_icon_size, raid_icon_size))
                ix = x0 + (reward_col_w - raid_icon.width) // 2
                iy = icon_slot_y + (raid_icon_size - raid_icon.height) // 2
                img.paste(raid_icon, (ix, iy), raid_icon)

            ty = icon_slot_y + raid_icon_size + reward_icon_text_gap
            for rarity, color in [("Mythic", (170, 0, 170, 255)), ("Fabled", (255, 85, 85, 255)), ("Legendary", (85, 255, 255, 255))]:
                for aspect in loot.get(raid, {}).get(rarity, []):
                    if "Aspect of a " in aspect:
                        text = aspect.replace("Aspect of a ", "")
                    elif "Aspect of the " in aspect:
                        text = aspect.replace("Aspect of the ", "")
                    elif "Aspect of " in aspect:
                        text = aspect.replace("Aspect of ", "")
                    else:
                        text = aspect

                    text_color = color
                    offset = 0
                    icon_img = self._load_local_aspect_icon(aspect, aspect_to_class)
                    if icon_img is not None:
                        icon_img.thumbnail((class_icon_size, class_icon_size))
                        img.paste(icon_img, (x0 + reward_column_text_inset, ty), icon_img)
                        offset = class_icon_size + 5

                    wrapped = wrap_text(text, title_font, reward_col_w - (reward_column_text_inset * 2) - offset, draw)
                    draw.multiline_text((x0 + reward_column_text_inset + offset, ty), wrapped, font=title_font, fill=text_color)
                    _, h = get_multiline_text_size(wrapped, title_font)
                    ty += h + line_spacing

        if gambit_layout:
            section_x0 = padding
            section_y0 = raids_img_h + gambit_section_gap
            section_x1 = img_w - padding
            section_y1 = img_h - padding

            draw.rounded_rectangle(
                (section_x0, section_y0, section_x1, section_y1),
                radius=14,
                fill=(0, 0, 0, 255),
                outline=(36, 0, 89, 255),
                width=4,
            )

            header_y = section_y0 + padding
            draw.text(
                (section_x0 + padding, header_y),
                "Current Gambits",
                font=gambit_header_font,
                fill=(255, 215, 90, 255),
            )
            gambit_countdown = self._format_countdown(self._next_daily_reset(self.GAMBIT_RESET_HOUR_UTC), prefix="Gambits reset in")
            gc_w, gc_h = get_multiline_text_size(gambit_countdown, gambit_desc_font)
            header_h = get_multiline_text_size("Current Gambits", gambit_header_font)[1]
            draw.text(
                (section_x1 - padding - gc_w, header_y + (header_h - gc_h) // 2),
                gambit_countdown,
                font=gambit_desc_font,
                fill=(180, 180, 200, 255),
            )

            entry_x0 = section_x0 + padding
            entry_x1 = section_x1 - padding
            content_w = entry_x1 - entry_x0
            gambit_cols = max(1, min(4, len(gambit_layout)))
            gambit_col_gap = 12
            card_w = max(
                240,
                int((content_w - (gambit_col_gap * (gambit_cols - 1))) / gambit_cols),
            )
            entry_y = header_y + get_multiline_text_size("Current Gambits", gambit_header_font)[1] + 16

            for row_start in range(0, len(gambit_layout), gambit_cols):
                row_entries = gambit_layout[row_start:row_start + gambit_cols]
                row_h = max(entry["card_h"] for entry in row_entries)

                for col_idx, entry in enumerate(row_entries):
                    card_x0 = entry_x0 + col_idx * (card_w + gambit_col_gap)
                    card_x1 = card_x0 + card_w
                    card_y1 = entry_y + row_h

                    draw.rounded_rectangle(
                        (card_x0, entry_y, card_x1, card_y1),
                        radius=12,
                        fill=(14, 10, 25, 255),
                        outline=(76, 30, 122, 255),
                        width=2,
                    )

                    text_x = card_x0 + gambit_card_padding
                    text_y = entry_y + gambit_card_padding
                    if gambit_icon is not None:
                        icon_y = entry_y + (row_h - gambit_icon.height) // 2
                        img.paste(gambit_icon, (text_x, icon_y), gambit_icon)
                        text_x += gambit_icon.width + 12

                    draw.multiline_text(
                        (text_x, text_y),
                        entry["name"],
                        font=gambit_name_font,
                        fill=(255, 215, 90, 255),
                        spacing=gambit_text_spacing,
                    )
                    draw.multiline_text(
                        (text_x, text_y + entry["name_h"] + 8),
                        entry["description"],
                        font=gambit_desc_font,
                        fill=(230, 230, 240, 255),
                        spacing=gambit_text_spacing,
                    )

                entry_y += row_h + gambit_entry_gap

        countdown = self._format_countdown(self._next_reset(self.RAID_RESET_WEEKDAY, self.RAID_RESET_HOUR_UTC), prefix="Raids reset in")
        pill_font = self._get_font(18)
        warn_font = self._get_font(16)
        pad_x, pad_y = 22, 7
        tb = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), countdown, font=pill_font)
        pill_w, pill_h = tb[2] - tb[0] + pad_x * 2, tb[3] - tb[1] + pad_y * 2
        warn_texts = []
        if is_stale_aspects:
            warn_texts.append("Wynnventory aspect data hasnt updated yet")
        if is_stale_gambits:
            warn_texts.append("Wynnventory gambit data hasnt updated yet")
        warn_line_h = get_multiline_text_size("X", warn_font)[1]
        warn_h = (warn_line_h * len(warn_texts) + 4 * max(0, len(warn_texts) - 1) + 8) if warn_texts else 0
        strip_h = pad_y + warn_h + pill_h + pad_y
        out = Image.new("RGBA", (img.width, img.height + strip_h), (0, 0, 0, 0))
        out.paste(img, (0, 0))
        fd = ImageDraw.Draw(out)
        wy = img.height + pad_y
        for warn_text in warn_texts:
            warn_w = get_multiline_text_size(warn_text, warn_font)[0]
            fd.text(((out.width - warn_w) // 2, wy), warn_text, font=warn_font, fill=(255, 170, 0, 255))
            wy += warn_line_h + 4
        pill_x = (out.width - pill_w) // 2
        pill_y = img.height + pad_y + warn_h
        fd.rounded_rectangle(
            (pill_x, pill_y, pill_x + pill_w, pill_y + pill_h),
            radius=pill_h // 2,
            fill=(14, 10, 25, 255),
            outline=(76, 30, 122, 255),
            width=2,
        )
        fd.text((pill_x + pad_x - tb[0], pill_y + pad_y - tb[1]), countdown, font=pill_font, fill=(255, 215, 90, 255))

        buf = BytesIO()
        out.save(buf, format="PNG")
        buf.seek(0)
        return buf

    @lootpool.command(
        name="aspects",
        description="Shows you the Aspect Raid Pool and current Gambits"
    )
    @external_rate_limit()
    async def aspects(self, ctx: discord.ApplicationContext):
        await ctx.defer()

        raw_resp, gambit_resp = await asyncio.gather(
            asyncio.to_thread(
                requests.get,
                f"{self.WYNNVENTORY_BASE_URL}/api/raidpool/items",
                headers=self._wynnventory_headers,
                timeout=15,
            ),
            asyncio.to_thread(
                requests.get,
                f"{self.WYNNVENTORY_BASE_URL}/api/raidpool/gambits/current",
                headers=self._wynnventory_headers,
                timeout=15,
            ),
            return_exceptions=True,
        )

        if isinstance(raw_resp, Exception):
            log(ERROR, f"Failed to fetch aspects data: {raw_resp}", context="lootpool")
            embed = discord.Embed(
                title=":no_entry: Error",
                description="Failed to fetch aspects data. Please try again later.",
                color=0xe33232
            )
            await ctx.followup.send(embed=embed)
            return

        raw_resp.raise_for_status()
        raw_data = raw_resp.json()
        self._cache_data('aspectData', raw_data if isinstance(raw_data, dict) else {"data": raw_data})

        last_raid_reset = self._last_reset(self.RAID_RESET_WEEKDAY, self.RAID_RESET_HOUR_UTC)
        is_stale_aspects = True
        for entry in (raw_data if isinstance(raw_data, list) else []):
            try:
                if parsedate_to_datetime(entry.get("timestamp", "")) >= last_raid_reset:
                    is_stale_aspects = False
                    break
            except Exception:
                pass

        gambit_entries = []
        is_stale_gambits = False
        if not isinstance(gambit_resp, Exception):
            try:
                gambit_resp.raise_for_status()
                gambit_data = gambit_resp.json()
                gambit_entries = self._extract_wynnventory_gambits(gambit_data)
                try:
                    is_stale_gambits = parsedate_to_datetime(gambit_data.get("timestamp", "")) < self._last_daily_reset(self.GAMBIT_RESET_HOUR_UTC)
                except Exception:
                    pass
            except Exception as e:
                log(ERROR, f"Failed to fetch gambits data: {e}", context="lootpool")
        else:
            log(ERROR, f"Failed to fetch gambits data: {gambit_resp}", context="lootpool")

        loot, api_aspect_to_class = self._extract_wynnventory_aspects(raw_data)
        if not self._ordered_keys(loot, self.RAID_DISPLAY_ORDER):
            embed = discord.Embed(
                title=":no_entry: Error",
                description="Failed to retrieve raid aspect data.",
                color=0xe33232
            )
            await ctx.followup.send(embed=embed)
            return

        try:
            with open('data/aspect_class_map.json', 'r') as f:
                class_map = json.load(f)
        except Exception:
            class_map = {}
        aspect_to_class = {name: cls for cls, names in class_map.items() for name in names}
        aspect_to_class.update(api_aspect_to_class)

        buf = await asyncio.to_thread(self._build_aspects_image, loot, aspect_to_class, gambit_entries, is_stale_aspects, is_stale_gambits)
        await ctx.followup.send(file=discord.File(buf, filename="aspects.png"))

    @lootpool.command(
        name="lootruns",
        description="Provides weekly loot run data (Mythic Only)"
    )
    @external_rate_limit()
    async def lootruns(self, ctx: discord.ApplicationContext):
        await ctx.defer()

        lootpool_raw, ward_map = await asyncio.gather(
            asyncio.to_thread(self._fetch_lootpool_data),
            asyncio.to_thread(self._fetch_ward_map),
            return_exceptions=True,
        )

        if isinstance(lootpool_raw, Exception):
            log(ERROR, f"Failed to fetch loot run data: {lootpool_raw}", context="lootpool")
            embed = discord.Embed(
                title=":no_entry: Error",
                description="Failed to fetch loot run data. Please try again later.",
                color=0xe33232
            )
            await ctx.followup.send(embed=embed)
            return

        self._cache_data('lootpoolData', lootpool_raw if isinstance(lootpool_raw, dict) else {"data": lootpool_raw})
        ward_map = ward_map if isinstance(ward_map, dict) else {}

        last_reset = self._last_reset(self.LOOT_RESET_WEEKDAY, self.LOOT_RESET_HOUR_UTC)
        is_stale = True
        for entry in (lootpool_raw if isinstance(lootpool_raw, list) else []):
            try:
                if parsedate_to_datetime(entry.get("timestamp", "")) >= last_reset:
                    is_stale = False
                    break
            except Exception:
                pass

        loot = self._extract_wynnventory_lootruns(lootpool_raw)
        for internal_name, wards in ward_map.items():
            if internal_name in loot:
                loot[internal_name]["Wards"] = wards
        if not loot:
            embed = discord.Embed(
                title=":no_entry: Error",
                description="Failed to retrieve lootrun pool data.",
                color=0xe33232
            )
            await ctx.followup.send(embed=embed)
            return

        buf = await asyncio.to_thread(self._build_lootrun_image, loot, is_stale)
        await ctx.followup.send(file=discord.File(buf, filename="lootpool.png"))

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(LootPool(client))
