import asyncio
import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from urllib.parse import quote

import aiohttp

from Helpers.item_tooltip_render import item_from_api, render_item_tooltip

PUA_RUN = re.compile(r"[\U000F0000-\U000FFFFD\U00100000-\U0010FFFD]+")
START = re.compile(r"[\U000F0000\U000F0002]\U000F0100")
NAME_SUFFIX = re.compile(r'\s?"([^"\n]*)"')
MAX_RESPONSE = 2 * 1024 * 1024
MAX_ITEMS = 4
WYNNPOOL_DECODE_URL = "https://api.wynnpool.com/item/full-decode"
WYNNPOOL_WEIGHT_URL = "https://api.wynnpool.com/item/{}/weight"
WEIGHT_CACHE_TTL_SECONDS = 300
WEIGHT_CACHE_MAX_ENTRIES = 128
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)


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
    for run in PUA_RUN.finditer(text):
        starts = list(START.finditer(run.group()))
        for index, start in enumerate(starts):
            left = run.start() + start.start()
            right = run.start() + starts[index + 1].start() if index + 1 < len(starts) else run.end()
            code = text[left:right]
            suffix = NAME_SUFFIX.match(text, right)
            name_hint = suffix.group(1).strip() if suffix else None
            matches.append((left, suffix.end() if suffix else right, code, name_hint or None))
    return matches


class ItemTooltipBridge:
    def __init__(self):
        self._slots = asyncio.Semaphore(2)
        self._session: aiohttp.ClientSession | None = None
        self._weights: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()

    async def close(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def prepare(self, text: str) -> PreparedMessage:
        matches = find_item_codes(text)
        if not matches:
            if len(text.encode("utf-16-le")) // 2 > 2000 or not text:
                raise ValueError("Forwarded content must contain 1..2000 UTF-16 characters")
            return PreparedMessage(text, (), ())
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
            parts.append(text[position:left])
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
                    errors.append(f"Item {attempts}: {error}")
                rendered[key] = replacement
            parts.append(replacement)
            position = right
        parts.append(text[position:])
        content = "".join(parts)
        if len(content.encode("utf-16-le")) // 2 > 2000:
            raise ValueError("Rendered message exceeds Discord's 2000-character content limit")
        return PreparedMessage(content, tuple(attachments), tuple(errors))

    async def _render(self, code: str, name_hint: str | None) -> tuple[str, bytes]:
        if len(code) > 1024:
            raise ValueError("Item code exceeds 1024 characters")
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
        png = await asyncio.to_thread(render_item_tooltip, item)
        if len(png) > 8 * 1024 * 1024:
            raise ValueError("Tooltip PNG exceeds 8 MiB")
        return item["itemName"], png

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

    async def _json_request(self, method: str, url: str, payload: dict[str, str] | None = None):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=REQUEST_TIMEOUT)
        async with self._session.request(
            method, url, json=payload,
            headers={"Accept": "application/json"},
        ) as response:
            if response.status not in (200, 201):
                raise ValueError(f"Item API returned HTTP {response.status}")
            body = await response.content.read(MAX_RESPONSE + 1)
            if len(body) > MAX_RESPONSE:
                raise ValueError("Item API response exceeds 2 MiB")
        return json.loads(body.decode("utf-8-sig"))
