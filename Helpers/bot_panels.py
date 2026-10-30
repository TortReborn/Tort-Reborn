"""
Helpers/bot_panels.py
Turns a bot panel revision (website-edited JSON, see bot_panels in
schema.sql) into the content/embeds/files/view the publisher sends.

Content shape -- keep in sync with TAq-Website/lib/bot-panels.ts:

    {"messages": [{
        "content": str,
        "images":  ["asset://<name>", ...],      # attached above the embeds (banners)
        "embeds":  [{"title", "url", "description", "color": int | None,
                     "author": {"name", "url", "icon_url"},
                     "thumbnail": url, "image": url,
                     "footer": {"text", "icon_url"},
                     "fields": [{"name", "value", "inline"}]}],
        "rows":    [[{"kind": "link", "label", "url", "emoji"} |
                     {"kind": "action", "action", "label", "emoji", "style"}]],
        "dynamic": "shell_exchange_panels" | None,
    }]}

Image urls are https:// or asset://<name>; assets live in the bucket under
ASSET_PREFIX and are attached, so an embed points at attachment://<name>.
Action buttons only ever carry a custom_id from the registry the bot hands
in, so the website can label and place a button but never invent one.

Everything here is pure (no Discord or DB calls) apart from fetch_asset,
which the caller supplies.
"""

import io
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import discord

ASSET_PREFIX = "bot_panels/"
ASSET_SCHEME = "asset://"
ASSET_NAME_RE = re.compile(r"^[0-9a-f]{16}\.(png|jpg|jpeg|gif|webp)$")
VALUE_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")

DYNAMIC_SHELL_EXCHANGE = "shell_exchange_panels"
DYNAMIC_BLOCKS = (DYNAMIC_SHELL_EXCHANGE,)

# Discord's own limits
MAX_MESSAGES = 10
MAX_CONTENT = 2000
MAX_EMBEDS = 10
MAX_EMBED_TOTAL = 6000
MAX_TITLE = 256
MAX_DESCRIPTION = 4096
MAX_FIELDS = 25
MAX_FIELD_NAME = 256
MAX_FIELD_VALUE = 1024
MAX_FOOTER = 2048
MAX_AUTHOR = 256
MAX_ROWS = 5
MAX_BUTTONS_PER_ROW = 5
MAX_BUTTON_LABEL = 80
MAX_IMAGES = 10
MAX_BOX_TEXT = 4000  # all text in a Components V2 message
LAYOUT_BOX = "box"   # message["layout"]: one Components V2 container instead of embeds

BUTTON_STYLES = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}


class PanelError(ValueError):
    """The revision can't be sent as written; the message says where."""


@dataclass(frozen=True)
class PanelAction:
    """A bot-handled button a panel may place. custom_id is what the
    persistent view registered in on_ready answers to."""
    custom_id: str
    label: str
    group: str
    emoji: str | None = None
    style: str = "secondary"


@dataclass
class RenderedMessage:
    content: str | None
    embeds: list[discord.Embed]
    files: list[discord.File]
    view: discord.ui.View | None
    # Components V2 (the view is the whole message; no content or embeds)
    box: bool = False
    # A box's text and colour as a plain embed, for copies that can't take V2
    # (the shell exchange legacy webhook)
    copy_embed: discord.Embed | None = None
    # Raw bytes behind files, so a retry can rebuild them (a File is single-use)
    file_bytes: dict[str, bytes] = field(default_factory=dict)

    def fresh_files(self) -> list[discord.File]:
        return [discord.File(io.BytesIO(data), filename=name) for name, data in self.file_bytes.items()]


def asset_name(url: str) -> str | None:
    if not url.startswith(ASSET_SCHEME):
        return None
    return url[len(ASSET_SCHEME):]


def _check_len(where: str, label: str, value: str, limit: int):
    if len(value) > limit:
        raise PanelError(f"{where}: {label} is {len(value)} characters (Discord allows {limit}).")


def fill_values(where: str, data, values: dict[str, str]):
    """Replace {{key}} everywhere in a message with the database value (see
    Helpers/panel_values.py). An unknown key stops the publish rather than
    posting raw braces."""
    if isinstance(data, str):
        def one(m):
            if m.group(1) not in values:
                raise PanelError(f"{where}: {m.group(0)} isn't a value panels can use.")
            return values[m.group(1)]
        return VALUE_RE.sub(one, data)
    if isinstance(data, list):
        return [fill_values(where, v, values) for v in data]
    if isinstance(data, dict):
        return {k: fill_values(where, v, values) for k, v in data.items()}
    return data


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


class _AssetCollector:
    def __init__(self, fetch_asset: Callable[[str], bytes | None]):
        self.fetch_asset = fetch_asset
        self.files: dict[str, bytes] = {}

    def resolve(self, where: str, url) -> str | None:
        """An image url as Discord should see it: assets become attachments."""
        if not isinstance(url, str) or not url.strip():
            return None
        url = url.strip()
        name = asset_name(url)
        if name is None:
            if not url.startswith("https://"):
                raise PanelError(f"{where}: image links must start with https:// ({url[:60]}).")
            return url
        if not ASSET_NAME_RE.match(name):
            raise PanelError(f"{where}: {url} is not an uploaded image.")
        if name not in self.files:
            data = self.fetch_asset(name)
            if data is None:
                raise PanelError(f"{where}: uploaded image {name} is missing from storage.")
            self.files[name] = data
        return f"attachment://{name}"


def _build_embed(where: str, data: dict, assets: _AssetCollector) -> discord.Embed:
    title = _text(data.get("title"))
    description = _text(data.get("description"))
    _check_len(where, "title", title, MAX_TITLE)
    _check_len(where, "description", description, MAX_DESCRIPTION)

    color = data.get("color")
    embed = discord.Embed(
        title=title or None,
        description=description or None,
        url=(data.get("url") or None) if title else None,
        color=color if isinstance(color, int) and 0 <= color <= 0xFFFFFF else None,
    )

    author = data.get("author") or {}
    author_name = _text(author.get("name"))
    if author_name:
        _check_len(where, "author name", author_name, MAX_AUTHOR)
        embed.set_author(
            name=author_name,
            url=author.get("url") or None,
            icon_url=assets.resolve(f"{where} author icon", author.get("icon_url")),
        )

    footer = data.get("footer") or {}
    footer_text = _text(footer.get("text"))
    if footer_text:
        _check_len(where, "footer", footer_text, MAX_FOOTER)
        embed.set_footer(
            text=footer_text,
            icon_url=assets.resolve(f"{where} footer icon", footer.get("icon_url")),
        )

    thumbnail = assets.resolve(f"{where} thumbnail", data.get("thumbnail"))
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    image = assets.resolve(f"{where} image", data.get("image"))
    if image:
        embed.set_image(url=image)

    fields = data.get("fields") or []
    if len(fields) > MAX_FIELDS:
        raise PanelError(f"{where}: {len(fields)} fields (Discord allows {MAX_FIELDS}).")
    for i, f in enumerate(fields, start=1):
        name = _text(f.get("name"))
        value = _text(f.get("value"))
        if not name or not value:
            raise PanelError(f"{where}, field {i}: a field needs both a name and a value.")
        _check_len(f"{where}, field {i}", "name", name, MAX_FIELD_NAME)
        _check_len(f"{where}, field {i}", "value", value, MAX_FIELD_VALUE)
        embed.add_field(name=name, value=value, inline=bool(f.get("inline")))

    if not (embed.title or embed.description or embed.fields or embed.image or embed.thumbnail or author_name or footer_text):
        raise PanelError(f"{where}: the embed is empty.")
    return embed


def _parse_emoji(where: str, raw) -> discord.PartialEmoji | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    return discord.PartialEmoji.from_str(raw.strip())


def _build_button_rows(where: str, rows, actions: dict[str, PanelAction]) -> list[list[discord.ui.Button]]:
    """Validated buttons, one list per row. Action buttons carry the registry's
    custom_id, so clicks reach the persistent views registered in on_ready."""
    if not rows:
        return []
    if len(rows) > MAX_ROWS:
        raise PanelError(f"{where}: {len(rows)} button rows (Discord allows {MAX_ROWS}).")

    out: list[list[discord.ui.Button]] = []
    seen_custom_ids = set()
    for r, row in enumerate(rows):
        if not row:
            continue
        built: list[discord.ui.Button] = []
        out.append(built)
        if len(row) > MAX_BUTTONS_PER_ROW:
            raise PanelError(f"{where}, row {r + 1}: {len(row)} buttons (Discord allows {MAX_BUTTONS_PER_ROW}).")
        for b, spec in enumerate(row, start=1):
            at = f"{where}, row {r + 1} button {b}"
            label = _text(spec.get("label"))
            _check_len(at, "label", label, MAX_BUTTON_LABEL)
            emoji = _parse_emoji(at, spec.get("emoji"))

            if spec.get("kind") == "link":
                url = (spec.get("url") or "").strip()
                if not url.startswith(("https://", "http://", "discord://")):
                    raise PanelError(f"{at}: a link button needs an http(s) url.")
                if not label and not emoji:
                    raise PanelError(f"{at}: a link button needs a label or an emoji.")
                built.append(discord.ui.Button(
                    style=discord.ButtonStyle.link, url=url, label=label or None, emoji=emoji,
                ))
                continue

            if spec.get("kind") != "action":
                raise PanelError(f"{at}: unknown button kind {spec.get('kind')!r}.")
            action = actions.get(spec.get("action") or "")
            if action is None:
                raise PanelError(f"{at}: {spec.get('action')!r} is not a bot action.")
            if action.custom_id in seen_custom_ids:
                raise PanelError(f"{at}: {action.label} appears twice in one message.")
            seen_custom_ids.add(action.custom_id)
            built.append(discord.ui.Button(
                style=BUTTON_STYLES.get(spec.get("style") or action.style, discord.ButtonStyle.secondary),
                custom_id=action.custom_id,
                label=label or action.label,
                emoji=emoji if emoji is not None else _parse_emoji(at, action.emoji),
            ))
    return [row for row in out if row]


def _build_view(where: str, rows, actions: dict[str, PanelAction]) -> discord.ui.View | None:
    # store=False: the buttons must reach the persistent views registered by
    # custom_id in on_ready, not a message-bound view with no callbacks.
    button_rows = _build_button_rows(where, rows, actions)
    if not button_rows:
        return None
    view = discord.ui.View(timeout=None, store=False)
    for r, row in enumerate(button_rows):
        for button in row:
            button.row = r
            view.add_item(button)
    return view


def _render_box(
    where: str, message: dict, actions: dict[str, PanelAction], assets: _AssetCollector,
    dynamic_files: dict[str, list[tuple[str, bytes]]] | None, default_color: int | None,
) -> "RenderedMessage":
    """A Components V2 message: one coloured container holding the text and a
    full-width gallery per image, then the button rows. This is how the old
    /shell_exchange generate laid out the shell exchange panel."""
    if message.get("embeds"):
        raise PanelError(f"{where}: a single-box message can't also have embeds.")
    text = _text(message.get("content"))
    _check_len(where, "text", text, MAX_BOX_TEXT)

    color = message.get("color")
    color = color if isinstance(color, int) and 0 <= color <= 0xFFFFFF else default_color
    container = discord.ui.Container(colour=discord.Colour(color) if color is not None else None)
    if text:
        container.add_item(discord.ui.TextDisplay(text))

    images = message.get("images") or []
    if len(images) > MAX_IMAGES:
        raise PanelError(f"{where}: {len(images)} images (Discord allows {MAX_IMAGES}).")
    names = []
    for i, url in enumerate(images, start=1):
        if asset_name(url or "") is None:
            raise PanelError(f"{where}, image {i}: images must be uploaded, not linked.")
        names.append(assets.resolve(f"{where}, image {i}", url))
    dynamic = message.get("dynamic")
    if dynamic:
        if dynamic not in DYNAMIC_BLOCKS:
            raise PanelError(f"{where}: unknown generated block {dynamic!r}.")
        for filename, data in (dynamic_files or {}).get(dynamic, []):
            assets.files[filename] = data
            names.append(f"attachment://{filename}")
    for name in names:  # one gallery each, so every image renders at full width
        container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(name)))

    if not container.items:
        raise PanelError(f"{where}: the message is empty.")
    view = discord.ui.DesignerView(container, timeout=None, store=False)
    for row in _build_button_rows(where, message.get("rows"), actions):
        view.add_item(discord.ui.ActionRow(*row))

    rendered = RenderedMessage(
        content=None, embeds=[], files=[], view=view, file_bytes=assets.files, box=True,
        copy_embed=discord.Embed(description=text or None, color=color),
    )
    rendered.files = rendered.fresh_files()
    return rendered


def render_message(
    index: int,
    message: dict,
    *,
    actions: dict[str, PanelAction],
    values: dict[str, str],
    fetch_asset: Callable[[str], bytes | None],
    dynamic_files: dict[str, list[tuple[str, bytes]]] | None = None,
    default_color: int | None = None,
) -> RenderedMessage:
    """One panel message, validated against Discord's limits. dynamic_files
    maps a dynamic block to the (filename, png bytes) the bot generated."""
    where = f"Message {index + 1}"
    message = fill_values(where, message, values)
    assets = _AssetCollector(fetch_asset)
    if message.get("layout") == LAYOUT_BOX:
        return _render_box(where, message, actions, assets, dynamic_files, default_color)

    content = _text(message.get("content"))
    _check_len(where, "content", content, MAX_CONTENT)

    images = message.get("images") or []
    if len(images) > MAX_IMAGES:
        raise PanelError(f"{where}: {len(images)} images (Discord allows {MAX_IMAGES}).")
    for i, url in enumerate(images, start=1):
        if asset_name(url or "") is None:
            raise PanelError(f"{where}, image {i}: message images must be uploaded, not linked.")
        assets.resolve(f"{where}, image {i}", url)

    embeds = [
        _build_embed(f"{where}, embed {i}", e, assets)
        for i, e in enumerate(message.get("embeds") or [], start=1)
    ]

    dynamic = message.get("dynamic")
    if dynamic:
        if dynamic not in DYNAMIC_BLOCKS:
            raise PanelError(f"{where}: unknown generated block {dynamic!r}.")
        color = embeds[-1].color.value if embeds and embeds[-1].color else default_color
        for filename, data in (dynamic_files or {}).get(dynamic, []):
            assets.files[filename] = data
            embeds.append(discord.Embed(color=color).set_image(url=f"attachment://{filename}"))

    if len(embeds) > MAX_EMBEDS:
        raise PanelError(f"{where}: {len(embeds)} embeds (Discord allows {MAX_EMBEDS}).")
    total = sum(len(e) for e in embeds)
    if total > MAX_EMBED_TOTAL:
        raise PanelError(f"{where}: the embeds hold {total} characters in all (Discord allows {MAX_EMBED_TOTAL}).")

    view = _build_view(where, message.get("rows"), actions)
    if not (content or embeds or assets.files):
        raise PanelError(f"{where}: the message is empty.")

    rendered = RenderedMessage(content=content or None, embeds=embeds, files=[], view=view, file_bytes=assets.files)
    rendered.files = rendered.fresh_files()
    return rendered


def panel_messages(content: dict) -> list[dict]:
    messages = (content or {}).get("messages")
    if not isinstance(messages, list) or not messages:
        raise PanelError("The panel has no messages.")
    if len(messages) > MAX_MESSAGES:
        raise PanelError(f"The panel has {len(messages)} messages (at most {MAX_MESSAGES}).")
    return messages


def uses_dynamic(content: dict, block: str) -> bool:
    return any(m.get("dynamic") == block for m in (content or {}).get("messages") or [])
