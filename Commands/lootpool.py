import asyncio

import discord
import requests
from discord.ext import commands
from discord.commands import SlashCommandGroup
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from Helpers.variables import mythics
from Helpers.rate_limiter import external_rate_limit
from Helpers.functions import wrap_text, get_multiline_text_size
from Helpers.database import DB
from Helpers.logger import log, ERROR
import time
import os
import json
import re
from pathlib import Path


# Ward items are raid drops that don't have a real item icon — they're just
# colored "wards" / tokens. We render them as generated colored swatches
# instead of using a misleading chestplate icon.
WARD_COLORS = {
    "Pink Ward":   (255, 105, 180, 255),
    "Orange Ward": (255, 140,   0, 255),
    "Green Ward":  ( 34, 197,  94, 255),
    "Red Ward":    (220,  38,  38, 255),
    "Blue Ward":   ( 59, 130, 246, 255),
    "Purple Ward": (168,  85, 247, 255),
    "Yellow Ward": (250, 204,  21, 255),
}


def make_ward_icon(item_name: str, size: int = 100):
    """Return a PIL RGBA image of a colored ward swatch, or None if not a ward."""
    color = WARD_COLORS.get(item_name)
    if not color:
        return None
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = max(4, size // 7)
    inset = max(2, size // 25)
    # Main colored fill with dark border
    d.rounded_rectangle(
        (inset, inset, size - inset - 1, size - inset - 1),
        radius=radius,
        fill=color,
        outline=(0, 0, 0, 255),
        width=max(1, size // 33),
    )
    # Subtle inner highlight ring for depth
    if size >= 32:
        inner_inset = inset + max(3, size // 14)
        d.rounded_rectangle(
            (inner_inset, inner_inset, size - inner_inset - 1, size - inner_inset - 1),
            radius=max(2, radius - 4),
            outline=(255, 255, 255, 90),
            width=max(1, size // 50),
        )
    return img


class LootPool(commands.Cog):
    lootpool = SlashCommandGroup(
        name="lootpool",
        description="Commands to fetch weekly lootpool data",
        integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install},
        contexts={discord.InteractionContextType.guild, discord.InteractionContextType.bot_dm, discord.InteractionContextType.private_channel},
    )

    def __init__(self, client):
        self.client = client

    RAID_DISPLAY_ORDER = ["TNA", "TCC", "NOL", "NOTG", "TWP"]
    LOOTRUN_REGION_ORDER = ["SE", "Corkus", "Sky", "Molten", "Canyon", "FrumaEast", "FrumaWest"]
    WARD_ICON_DIR = Path("images/wards")
    MYTHIC_ICON_DIR = Path("images/mythics")
    ASPECT_ICON_DIR = Path("images/raids")

    def _format_list(self, items):
        return "\n".join(items) if items else "None"

    def _as_mapping(self, value):
        return value if isinstance(value, dict) else {}

    def _as_list(self, value):
        return value if isinstance(value, list) else []

    def _extract_aspect_payload(self, data: dict) -> tuple[dict, int]:
        data = self._as_mapping(data)
        top_ts = data.get("Timestamp")
        if isinstance(top_ts, int):
            timestamp = top_ts
        else:
            timestamp = int(time.time())

        if isinstance(data.get("Loot"), dict):
            return data["Loot"], timestamp

        aspects = self._as_mapping(data.get("Aspects"))
        if isinstance(aspects.get("Loot"), dict):
            return aspects["Loot"], timestamp
        if aspects:
            nested_ts = aspects.get("Timestamp")
            if isinstance(nested_ts, int):
                timestamp = nested_ts
            return aspects, timestamp

        return {}, timestamp

    def _extract_lootrun_payload(self, data: dict) -> tuple[dict, int]:
        data = self._as_mapping(data)
        timestamp = data.get("Timestamp") if isinstance(data.get("Timestamp"), int) else int(time.time())

        if isinstance(data.get("Loot"), dict):
            return data["Loot"], timestamp

        items = self._as_mapping(data.get("Items"))
        if isinstance(items.get("Loot"), dict):
            return items["Loot"], timestamp
        if items:
            nested_ts = items.get("Timestamp")
            if isinstance(nested_ts, int):
                timestamp = nested_ts
            return items, timestamp

        return {}, timestamp

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

    def _extract_gambits_payload(self, data: dict) -> list[dict]:
        payload = self._as_mapping(data)
        entries = self._as_list(payload.get("Entries"))
        return [self._as_mapping(entry) for entry in entries if isinstance(entry, dict)]

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

    def _load_local_lootpool_icon(self, item_name: str) -> Image.Image | None:
        ward_icon_name = self._resolve_ward_icon_name(item_name)
        if ward_icon_name:
            ward_path = self.WARD_ICON_DIR / ward_icon_name
            if ward_path.is_file():
                return self._load_local_icon(ward_path)
            log(ERROR, f"Local ward icon missing: {ward_path}", context="lootpool")
            return None

        file_name = mythics.get(item_name) or self._resolve_special_icon_name(item_name)
        if not file_name:
            return None

        icon_path = self.MYTHIC_ICON_DIR / file_name
        if not icon_path.is_file():
            log(ERROR, f"Local lootpool icon missing: {icon_path} (item={item_name!r})", context="lootpool")
            return None
        return self._load_local_icon(icon_path)

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

    @lootpool.command(
        name="aspects",
        description="Provides weekly aspects data as an image"
    )
    @external_rate_limit()
    async def aspects(self, ctx: discord.ApplicationContext):
        await ctx.defer()

        data = None
        last_error = None
        gambit_entries = []
        for url in ("https://nori.fish/api/raids", "https://nori.fish/api/aspects"):
            try:
                resp = await asyncio.to_thread(requests.get, url, timeout=15)
                resp.raise_for_status()
                candidate = resp.json()
                loot, timestamp = self._extract_aspect_payload(candidate)
                if loot:
                    data = candidate
                    self._cache_data('aspectData', candidate)
                    break
            except Exception as e:
                last_error = e

        if data is None:
            if last_error:
                log(ERROR, f"Failed to fetch aspects data: {last_error}", context="lootpool")
            embed = discord.Embed(
                title=":no_entry: Error",
                description="Failed to fetch aspects data. Please try again later.",
                color=0xe33232
            )
            await ctx.followup.send(embed=embed)
            return

        try:
            gambits_resp = await asyncio.to_thread(requests.get, "https://nori.fish/api/gambits", timeout=15)
            gambits_resp.raise_for_status()
            gambit_entries = self._extract_gambits_payload(gambits_resp.json())
        except Exception as e:
            log(ERROR, f"Failed to fetch gambits data: {e}", context="lootpool")

        loot, timestamp = self._extract_aspect_payload(data)
        raids = self._ordered_keys(loot, self.RAID_DISPLAY_ORDER)
        if not raids:
            embed = discord.Embed(
                title=":no_entry: Error",
                description="Nori returned no raid aspect data.",
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

        # Layout settings
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

        # Prepare fonts and dummy draw
        title_font = ImageFont.truetype("images/profile/game.ttf", 18)
        gambit_header_font = ImageFont.truetype("images/profile/game.ttf", 24)
        gambit_name_font = ImageFont.truetype("images/profile/game.ttf", 20)
        gambit_desc_font = ImageFont.truetype("images/profile/game.ttf", 18)
        dummy_img = Image.new('RGBA', (1,1), (0,0,0,0))
        dummy_draw = ImageDraw.Draw(dummy_img)

        # Compute max lines to determine canvas height
        img_w = cols * col_w + padding * (cols + 1)
        reward_section_x0 = padding
        reward_section_x1 = img_w - padding
        rewards_content_w = (reward_section_x1 - reward_section_x0) - (reward_section_inner_padding * 2)
        reward_col_w = int((rewards_content_w - (reward_column_gap * max(0, cols - 1))) / max(1, cols))

        max_lines = 0
        max_reward_text_h = 0
        for raid in raids:
            count = 0
            reward_text_h = 0
            for rarity in ["Mythic", "Fabled", "Legendary"]:
                for aspect in loot.get(raid, {}).get(rarity, []):
                    text = aspect.replace("Aspect of ", "")
                    text = text[:1].upper() + text[1:] if text else text
                    wrapped = wrap_text(text, title_font, reward_col_w - (reward_column_text_inset * 2), dummy_draw)
                    count += wrapped.count("\n") + 1
                    reward_text_h += get_multiline_text_size(wrapped, title_font)[1] + line_spacing
            max_lines = max(max_lines, count)
            max_reward_text_h = max(max_reward_text_h, reward_text_h)

        # Canvas size
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

        # Create canvas
        img = Image.new("RGBA", (img_w, img_h), (0,0,0,0))
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

        # Draw each raid inside a shared reward field.
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

            # Raid icon
            raid_path = f"images/raids/{raid}.png"
            icon_slot_y = column_y0 + reward_column_text_inset
            if os.path.isfile(raid_path):
                raid_icon = Image.open(raid_path).convert("RGBA")
                raid_icon.thumbnail((raid_icon_size, raid_icon_size))
                ix = x0 + (reward_col_w - raid_icon.width) // 2
                iy = icon_slot_y + (raid_icon_size - raid_icon.height) // 2
                img.paste(raid_icon, (ix, iy), raid_icon)

            # Draw aspects below icon
            ty = icon_slot_y + raid_icon_size + reward_icon_text_gap
            for rarity, color in [("Mythic",(170,0,170,255)), ("Fabled",(255,85,85,255)), ("Legendary",(85,255,255,255))]:
                for aspect in loot.get(raid, {}).get(rarity, []):
                    # Prepare text
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
                    elif aspect in WARD_COLORS:
                        # Last-resort fallback if the remote ward asset is unavailable.
                        ward_icon = make_ward_icon(aspect, class_icon_size)
                        img.paste(ward_icon, (x0 + reward_column_text_inset, ty), ward_icon)
                        offset = class_icon_size + 5
                        text_color = WARD_COLORS[aspect]

                    # Wrap and draw
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

        # Try to pull a timestamp from the API (fallback to now if missing)
        ts = timestamp
        next_rot = ts + 604800  # one week in seconds

        # Prepare the image file
        with BytesIO() as buf:
            img.save(buf, format="PNG")
            buf.seek(0)
            file = discord.File(buf, filename="aspects.png")

            # Build the embed
            embed = discord.Embed(
                title="Weekly Raid Aspects",
                color=0x7a187a  # match your lootruns color
            )
            embed.add_field(
                name=":arrows_counterclockwise: Next rotation:",
                value=f"<t:{next_rot}:f>"  # full datetime format
            )
            embed.set_image(url="attachment://aspects.png")

            # Send embed + image together
            await ctx.followup.send(embed=embed, file=file)

    @lootpool.command(
        name="lootruns",
        description="Provides weekly loot run data (Mythic Only)"
    )
    @external_rate_limit()
    async def lootruns(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        try:
            resp = await asyncio.to_thread(requests.get, "https://nori.fish/api/lootpool", timeout=15)
            resp.raise_for_status()
            data = resp.json()
            
            # Cache the lootpool data
            self._cache_data('lootpoolData', data)
            
        except Exception:
            embed = discord.Embed(
                title=":no_entry: Error",
                description="Failed to fetch loot run data. Please try again later.",
                color=0xe33232
            )
            await ctx.followup.send(embed=embed)
            return

        embed = discord.Embed(
            title="Weekly Mythic Lootpool",
            color=0x7a187a,
        )
        loot, timestamp = self._extract_lootrun_payload(data)
        if not loot:
            embed = discord.Embed(
                title=":no_entry: Error",
                description="Nori returned no lootrun pool data.",
                color=0xe33232
            )
            await ctx.followup.send(embed=embed)
            return

        embed.add_field(name=":arrows_counterclockwise: Next rotation:", value=f'<t:{timestamp + 604800}:f>')

        region_order = self._ordered_keys(loot, self.LOOTRUN_REGION_ORDER)
        region_widths = []
        longest = 0
        for region in region_order:
            region_data = self._as_mapping(loot.get(region))
            mythics_in_region = self._as_list(region_data.get("Mythic"))
            shiny_data = self._as_mapping(region_data.get("Shiny"))
            length = len(mythics_in_region) + (1 if shiny_data.get("Item") else 0)
            length = max(length, 1)
            region_widths.append(156 * length)
            longest = max(longest, length)

        w = 156 * longest
        h = 263 * len(region_order)  # height based on number of lr regions for future proofing
        lr_lp = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(lr_lp)

        shiny = Image.open("images/mythics/shiny.png").convert("RGBA")
        shiny.thumbnail((36, 36))

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
            items = ([shiny_item] if isinstance(shiny_item, str) and shiny_item else []) + mythic_items
            for i, item in enumerate(items):
                try:
                    item_img = self._load_local_lootpool_icon(item)
                    if item_img is None:
                        log(ERROR, f"Missing local icon for lootpool item: {item!r} (region={region_name})", context="lootpool")
                        ward_icon = make_ward_icon(item, 100)
                        item_img = ward_icon if ward_icon is not None else Image.open("images/mythics/diamond_chestplate.png").convert("RGBA")
                    if self._resolve_ward_icon_name(item):
                        item_img = self._fit_icon(item_img, (100, 100), upscale=True, resample=Image.Resampling.NEAREST)
                    else:
                        item_img = self._fit_icon(item_img, (100, 100))
                    x = int(x1 + 28 + i * 156)
                    y = int(y1 + 25)
                    lr_lp.paste(item_img, (x, y), item_img)
                    if item == shiny_item and i < 1:
                        lr_lp.paste(shiny, (x, y), shiny)

                    # Item name
                    item_font = ImageFont.truetype("images/profile/game.ttf", 20)
                    name_text = wrap_text(item, item_font, 156, draw)
                    text_w, text_h = get_multiline_text_size(name_text, item_font)
                    draw.multiline_text(
                        (x + (100 - text_w) // 2, y + 115),
                        name_text,
                        font=item_font,
                        fill=(170, 0, 170, 255),
                        align="center",
                        spacing=0
                    )

                    # Shiny tracker
                    tracker_font = ImageFont.truetype("images/profile/game.ttf", 18)
                    if item == shiny_item and i < 1:
                        lines_in_name = name_text.count("\n") + 1
                        tracker_text_raw = str(shiny_data.get('Tracker', ''))
                        wrapped_tracker = wrap_text(tracker_text_raw, tracker_font, 140, draw)
                        tracker_lines = wrapped_tracker.count("\n") + 1
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

                except Exception as e:
                    log(ERROR, f"{e}", context="lootpool")
                    embed = discord.Embed(
                        title=":no_entry: Error",
                        description="Could not generate lootpool image. Please try again later.",
                        color=0xe33232
                    )
                    await ctx.followup.send(embed=embed)
                    return

            count += 1

        title_font = ImageFont.truetype('images/profile/game.ttf', 40)
        # (display name, fill RGBA, stroke RGBA) keyed by API region code.
        # Iterated in API order so titles always line up with their item rows.
        region_meta = {
            "SE":        ("Silent Expanse Expedition",            (85, 227, 64, 255),  (33, 33, 33, 255)),
            "Corkus":    ("The Corkus Traversal",                 (237, 202, 59, 255), (107, 77, 22, 255)),
            "Sky":       ("Sky Islands Exploration",              (88, 214, 252, 255), (31, 55, 108, 255)),
            "Molten":    ("Molten Heights Hike",                  (189, 30, 30, 255),  (99, 11, 11, 255)),
            "Canyon":    ("Canyon of the Lost Excursion (South)", (52, 64, 235, 255),  (21, 27, 115, 255)),
            "FrumaEast": ("Fruma East",                           (220, 130, 220, 255),(80, 30, 80, 255)),
            "Fruma East":("Fruma East",                           (220, 130, 220, 255),(80, 30, 80, 255)),
            "FrumaWest": ("Fruma West",                           (130, 220, 220, 255),(30, 80, 80, 255)),
            "Fruma West":("Fruma West",                           (130, 220, 220, 255),(30, 80, 80, 255)),
        }
        for idx, region_key in enumerate(region_order):
            title, fill, stroke = region_meta.get(
                region_key,
                (region_key, (255, 255, 255, 255), (33, 33, 33, 255))
            )
            draw.text(
                xy=(w / 2, 16 + 255 * idx),
                text=title,
                font=title_font,
                fill=fill,
                stroke_width=3,
                stroke_fill=stroke,
                align="center",
                anchor="mt",
            )

        with BytesIO() as file:
            lr_lp.save(file, format="PNG")
            file.seek(0)
            t = int(time.time())
            lr_lootpool = discord.File(file, filename=f"lootpool{t}.png")
            embed.set_image(url=f"attachment://lootpool{t}.png")

        await ctx.followup.send(embed=embed, file=lr_lootpool)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(LootPool(client))
