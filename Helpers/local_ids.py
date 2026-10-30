"""
Maincord ids written into code and content, translated for the test servers.

Panels, role buttons and help texts name maincord roles, channels and emojis
by id. Tort Test and the test exec server mirror the real servers by name
(TAQ-110), so under the dev bot each of those ids has a same-named test
counterpart; data/test_id_map.json holds the pairs, written by
`scripts/server_parity.py idmap`.

Under the prod bot every function here returns its input unchanged. An id
with no test counterpart is left as it is.
"""

import json
import re
from pathlib import Path

import discord

from Helpers.variables import IS_TEST_PROFILE

MAP_FILE = Path(__file__).parent.parent / "data" / "test_id_map.json"

_EMOJI = re.compile(r"<(a?):(\w+):(\d+)>")
_MENTION = re.compile(r"<(@&|#)(\d+)>")
_LINK = re.compile(r"discord\.com/channels/(\d+)/(\d+)")


def _load() -> dict:
    if not IS_TEST_PROFILE:
        return {}
    try:
        with open(MAP_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


_MAP = _load()


def _get(kind: str, i):
    if i is None:
        return None
    return int(_MAP.get(kind, {}).get(str(i), i))


def role(i):
    return _get("roles", i)


def channel(i):
    return _get("channels", i)


def emoji(i):
    return _get("emojis", i)


def partial_emoji(name: str, emoji_id: int) -> discord.PartialEmoji:
    return discord.PartialEmoji(name=name, id=emoji(emoji_id))


def text(s):
    """Role and channel mentions, custom emojis and channel links in `s`."""
    if not _MAP or not isinstance(s, str):
        return s
    s = _EMOJI.sub(lambda m: f"<{m[1]}:{m[2]}:{emoji(m[3])}>", s)
    s = _MENTION.sub(lambda m: f"<{m[1]}{role(m[2]) if m[1] == '@&' else channel(m[2])}>", s)
    return _LINK.sub(lambda m: f"discord.com/channels/{_get('guilds', m[1])}/{channel(m[2])}", s)


def data(v):
    """`text` applied to every string in a JSON-like value (embed dicts)."""
    if isinstance(v, str):
        return text(v)
    if isinstance(v, list):
        return [data(x) for x in v]
    if isinstance(v, dict):
        return {k: data(x) for k, x in v.items()}
    return v


def embed(e: discord.Embed) -> discord.Embed:
    return discord.Embed.from_dict(data(e.to_dict())) if _MAP else e


def embeds(es):
    return [embed(e) for e in es]
