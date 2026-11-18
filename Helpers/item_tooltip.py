import asyncio
import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from urllib.parse import quote

import aiohttp

from Helpers.artemis_item import (
    CRAFTED_CONSUMABLE_ITEM_TYPE,
    CRAFTED_GEAR_ITEM_TYPE,
    GEAR_ITEM_TYPE,
    MOUNT_ITEM_TYPE,
    decode_crafted_consumable,
    decode_crafted_gear,
    decode_gear,
    decode_mount,
    item_type,
)
from Helpers.item_tooltip_render import (
    item_from_api,
    item_from_gear,
    render_crafted_consumable_tooltip,
    render_crafted_tooltip,
    render_item_tooltip,
    render_mount_tooltip,
)
from Helpers.logger import WARN, log
from Helpers.wynn_items import (
    DATABASE_TTL_SECONDS,
    MAX_DATABASE_RESPONSE,
    WYNN_ITEM_DATABASE_URL,
    WYNNTILS_GEAR_URL,
    WYNNTILS_RETRY_SECONDS,
    WynnItemIndex,
)

PUA_RUN = re.compile(r"[\U000F0000-\U000FFFFD\U00100000-\U0010FFFD]+")
START = re.compile(r"[\U000F0000-\U000F0003][\U000F0100-\U000F0105]")
UNREADABLE_ITEM = "[shared item]"
NAME_SUFFIX = re.compile(r'\s?"([^"\n]*)"')
MAX_RESPONSE = 2 * 1024 * 1024
MAX_ITEMS = 4
WYNNPOOL_DECODE_URL = "https://api.wynnpool.com/item/full-decode"
WYNNPOOL_WEIGHT_URL = "https://api.wynnpool.com/item/{}/weight"
MOUNT_COLORS_URL = "https://cdn.wynntils.com/static/Data-Storage/mount_colors.json"
MAX_TOOLTIP_BYTES = 8 * 1024 * 1024
WEIGHT_CACHE_TTL_SECONDS = 300
WEIGHT_CACHE_MAX_ENTRIES = 128
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
DATABASE_TIMEOUT = aiohttp.ClientTimeout(total=120)


@dataclass(frozen=True)
class Attachment:
    filename: str
    png: bytes


@dataclass(frozen=True)
class PreparedMessage:
    content: str
    attachments: tuple[Attachment, ...]
    errors: tuple[str, ...]


def find_item_codes(text: str) -> list[tuple[int, int, str, str | None]]:
    if not isinstance(text, str) or len(text.encode("utf-16-le")) // 2 > 4000:
        raise ValueError("Expected a chat message of at most 4000 UTF-16 characters")
    matches = []
    # Wynntils reads one unbroken run as one item; item data can contain a header-like pair.
    for run in PUA_RUN.finditer(text):
        if not START.match(run.group()):
            continue
        suffix = NAME_SUFFIX.match(text, run.end())
        name_hint = suffix.group(1).strip() if suffix else None
        matches.append((run.start(), suffix.end() if suffix else run.end(), run.group(), name_hint or None))
    return matches


def without_item_codes(text: str) -> str:
    return PUA_RUN.sub(UNREADABLE_ITEM, text)


def unrecognised_encodings(text: str) -> tuple[str, ...]:
    errors = []
    for run in PUA_RUN.finditer(text):
        high, low = divmod(ord(run.group()[0]) - 0xF0000, 256)
        version = f"version byte {low}" if high == 0 else "no start block"
        errors.append(f"Unrecognised Wynntils item encoding ({version})")
    return tuple(errors)


def _describe(code: str, name_hint: str | None) -> str:
    try:
        kind = f"type {item_type(code)}"
    except ValueError:
        kind = "unreadable header"
    return f"{kind}, {name_hint!r}" if name_hint else kind


async def _draw(render, item: dict) -> bytes:
    png = await asyncio.to_thread(render, item)
    if len(png) > MAX_TOOLTIP_BYTES:
        raise ValueError("Tooltip PNG exceeds 8 MiB")
    return png


class ItemTooltipBridge:
    def __init__(self):
        self._slots = asyncio.Semaphore(2)
        self._session: aiohttp.ClientSession | None = None
        self._weights: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()
        self._items = WynnItemIndex()
        self._items_lock = asyncio.Lock()
        self._wynntils_retry_at = 0.0
        self._mount_colors: dict[int, str] = {}
        self._mount_colors_due_at = 0.0

    async def close(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def prepare(self, text: str) -> PreparedMessage:
        matches = find_item_codes(text)
        if not matches:
            if len(text.encode("utf-16-le")) // 2 > 2000 or not text:
                raise ValueError("Forwarded content must contain 1..2000 UTF-16 characters")
            return PreparedMessage(without_item_codes(text), (), unrecognised_encodings(text))
        if self._slots.locked():
            raise ValueError("Item renderer is busy; retry through Tort's existing queue")
        async with self._slots:
            return await self._prepare_locked(text, matches)

    async def _prepare_locked(
        self, text: str, matches: list[tuple[int, int, str, str | None]]
    ) -> PreparedMessage:
        parts, attachments, errors = [], [], []
        rendered: dict[tuple[str, str | None], str] = {}
        position = 0
        attempts = 0
        for left, right, code, name_hint in matches:
            parts.append(without_item_codes(text[position:left]))
            errors.extend(unrecognised_encodings(text[position:left]))
            key = (code, name_hint)
            if key in rendered:
                replacement = rendered[key]
            elif attempts >= MAX_ITEMS:
                replacement = "[item preview limit reached]"
                errors.append("At most four distinct items can be rendered per message")
            else:
                attempts += 1
                try:
                    name, png = await self._render(code, name_hint)
                    replacement = name
                    attachments.append(Attachment(f"item_{len(attachments) + 1}.png", png))
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
                    replacement = "[item preview unavailable]"
                    errors.append(f"Item {attempts} ({_describe(code, name_hint)}): {error}")
                rendered[key] = replacement
            parts.append(replacement)
            position = right
        parts.append(without_item_codes(text[position:]))
        errors.extend(unrecognised_encodings(text[position:]))
        content = "".join(parts)
        if len(content.encode("utf-16-le")) // 2 > 2000:
            raise ValueError("Rendered message exceeds Discord's 2000-character content limit")
        return PreparedMessage(content, tuple(attachments), tuple(errors))

    async def _render(self, code: str, name_hint: str | None) -> tuple[str, bytes]:
        if len(code) > 1024:
            raise ValueError("Item code exceeds 1024 characters")
        kind = item_type(code)
        if kind == CRAFTED_GEAR_ITEM_TYPE:
            item = decode_crafted_gear(code, name_hint)
            return item["itemName"], await _draw(render_crafted_tooltip, item)
        # Wynntils leaves consumable and mount names out of the code, so Wynnpool can't look them up.
        if kind == CRAFTED_CONSUMABLE_ITEM_TYPE:
            item = decode_crafted_consumable(code, name_hint)
            return item["itemName"], await _draw(render_crafted_consumable_tooltip, item)
        if kind == MOUNT_ITEM_TYPE:
            item = decode_mount(code, name_hint)
            item["colorNames"] = await self._mount_color_names(item["colorIds"])
            return item["itemName"], await _draw(render_mount_tooltip, item)
        if kind == GEAR_ITEM_TYPE:
            return await self._render_gear(code, name_hint)
        decoded = await self._json_request("POST", WYNNPOOL_DECODE_URL, {"item": code})
        if not isinstance(decoded, dict) or not isinstance(decoded.get("original"), dict):
            raise ValueError("Invalid Wynnpool decode response")
        name = decoded["original"].get("displayName")
        if not isinstance(name, str) or not name or len(name) > 200:
            raise ValueError("Missing or invalid decoded item name")
        weights = await self._get_weights(name)
        item = item_from_api(decoded, weights)
        if name_hint:
            item["itemName"] = name_hint
        return item["itemName"], await _draw(render_item_tooltip, item)

    async def _render_gear(self, code: str, name_hint: str | None) -> tuple[str, bytes]:
        decoded = decode_gear(code)
        entry = (await self._item_index()).resolve(decoded["itemName"], decoded["identifications"])
        weights = await self._get_weights(entry["displayName"])
        item = item_from_gear(decoded, entry, weights)
        if name_hint:
            item["itemName"] = name_hint
        return item["itemName"], await _draw(render_item_tooltip, item)

    async def _mount_color_names(self, color_ids: tuple[int, int]) -> tuple[str, str]:
        if monotonic() >= self._mount_colors_due_at:
            try:
                entries = await self._json_request("GET", MOUNT_COLORS_URL)
                if not isinstance(entries, list) or not all(
                    isinstance(entry, dict) and isinstance(entry.get("id"), int)
                    and isinstance(entry.get("displayName"), str) for entry in entries
                ):
                    raise ValueError("Invalid Wynntils mount colour list")
                self._mount_colors = {entry["id"]: entry["displayName"] for entry in entries}
                self._mount_colors_due_at = monotonic() + DATABASE_TTL_SECONDS
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
                self._mount_colors_due_at = monotonic() + WYNNTILS_RETRY_SECONDS
                log(WARN, f"Wynntils mount colours unavailable, showing them as Unknown: {error}",
                    context="item_tooltip")
        primary, secondary = (self._mount_colors.get(color_id, "Unknown") for color_id in color_ids)
        return primary, secondary

    async def _item_index(self) -> WynnItemIndex:
        if self._items.fresh and not self._wynntils_names_due():
            return self._items
        async with self._items_lock:
            if not self._items.fresh:
                entries = await self._json_request(
                    "GET", WYNN_ITEM_DATABASE_URL,
                    max_bytes=MAX_DATABASE_RESPONSE, timeout=DATABASE_TIMEOUT,
                )
                self._items.load(entries)
                await self._load_wynntils_names()
            elif self._wynntils_names_due():
                await self._load_wynntils_names()
        return self._items

    def _wynntils_names_due(self) -> bool:
        return not self._items.has_wynntils_names and monotonic() >= self._wynntils_retry_at

    async def _load_wynntils_names(self) -> None:
        try:
            gear = await self._json_request(
                "GET", WYNNTILS_GEAR_URL,
                max_bytes=MAX_DATABASE_RESPONSE, timeout=DATABASE_TIMEOUT,
            )
            self._items.load_wynntils_names(gear)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            self._wynntils_retry_at = monotonic() + WYNNTILS_RETRY_SECONDS
            log(WARN, f"Wynntils gear list unavailable, keeping the previous one: {error}",
                context="item_tooltip")

    async def _get_weights(self, name: str) -> list[dict]:
        cached = self._weights.get(name)
        if cached and cached[0] > monotonic():
            self._weights.move_to_end(name)
            return cached[1]
        weights = await self._json_request("GET", WYNNPOOL_WEIGHT_URL.format(quote(name, safe="")))
        if not isinstance(weights, list) or not all(
            isinstance(scale, dict) and scale.get("item_id") == name for scale in weights
        ):
            raise ValueError("Invalid Wynnpool scale response")
        self._weights[name] = (monotonic() + WEIGHT_CACHE_TTL_SECONDS, weights)
        self._weights.move_to_end(name)
        while len(self._weights) > WEIGHT_CACHE_MAX_ENTRIES:
            self._weights.popitem(last=False)
        return weights

    async def _json_request(
        self, method: str, url: str, payload: dict[str, str] | None = None,
        max_bytes: int = MAX_RESPONSE, timeout: aiohttp.ClientTimeout = REQUEST_TIMEOUT,
    ):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=REQUEST_TIMEOUT)
        async with self._session.request(
            method, url, json=payload, timeout=timeout,
            headers={"Accept": "application/json"},
        ) as response:
            if response.status not in (200, 201):
                detail = (await response.text(errors="replace"))[:200].strip()
                raise ValueError(
                    f"{method} {url} returned HTTP {response.status}" + (f": {detail}" if detail else "")
                )
            body = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ValueError("Item API response exceeds its size limit")
        return json.loads(body.decode("utf-8-sig"))
