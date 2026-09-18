import asyncio
import datetime
import re
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import timezone
from io import BytesIO
from urllib.parse import urlsplit

import aiohttp
import discord
import emoji
from discord.ext import commands, tasks

from Helpers.database import DB
from Helpers.item_tooltip import ItemTooltipBridge
from Helpers.logger import ERROR, INFO, WARN, log
from Helpers.variables import (
    GUILD_CHAT_BRIDGE_TOKEN,
    TAQ_GUILD_ID,
    discord_ranks,
)

CHANNEL_MANAGEMENT_ENABLED = True

TAQ_GUILD_TAG = "TAq"
BRIDGE_WORKER_URL = "wss://verge-raid-tracker.wavelink.workers.dev/v1/bridge/ws"
BRIDGE_CHANNEL_NAME = "🌊｜sea-coast"
BRIDGE_PERMISSION_ANCHOR_CHANNEL_ID = 736920151081091122  # build-discussions
BRIDGE_POSITION_ANCHOR_CHANNEL_ID = 748900470575071293  # guild-general
BRIDGE_ROTATION_HOURS = 24
BRIDGE_IDLE_MINUTES = 5
MAX_MESSAGE_LENGTH = 4000
RECENT_DISCORD_MESSAGES = 256
MAX_MEDIA_ITEMS = 3
MAX_MEDIA_URL_LENGTH = 768
MAX_REPLY_EXCERPT_LENGTH = 160
BRIDGE_WEBHOOK_NAME = "Tort Guild Bridge"
WEBHOOK_NAME_FORBIDDEN = ("discord", "clyde")
CUSTOM_EMOJI_PATTERN = re.compile(r"<a?:(\w+):\d+>")
IGN_MENTION_PATTERN = re.compile(r"(?<![\w@])@(\w{3,16})(?!\w)")
RANK_TAG_PATTERN = re.compile(r"^(?:" + "|".join(re.escape(rank) for rank in discord_ranks) + r")\s+")
URL_PATTERN = re.compile(r"https://[^\s<>]+", re.IGNORECASE)
EMBED_REFRESH_DELAYS = (0.25, 0.5, 0.75)


@dataclass(frozen=True)
class LinkedBridgeMember:
    discord_id: int
    ign: str
    color: int | None


@dataclass(frozen=True)
class BridgePoster:
    name: str
    avatar_url: str


@dataclass(frozen=True)
class BridgeReply:
    username: str
    excerpt: str

    def payload(self) -> dict:
        data = {"username": self.username}
        if self.excerpt:
            data["excerpt"] = self.excerpt
        return data


@dataclass(frozen=True)
class BridgeMedia:
    kind: str
    url: str
    label: str
    preview_url: str = ""
    title: str = ""
    description: str = ""
    provider: str = ""
    inline: bool = False
    spoiler: bool = False

    def payload(self) -> dict:
        data = {
            "kind": self.kind,
            "url": self.url,
            "label": self.label,
            "inline": self.inline,
            "spoiler": self.spoiler,
        }
        if self.preview_url:
            data["previewUrl"] = self.preview_url
        if self.title:
            data["title"] = self.title
        if self.description:
            data["description"] = self.description
        if self.provider:
            data["provider"] = self.provider
        return data


@dataclass(frozen=True)
class DiscordBridgeMessage:
    message: str
    content: str
    reply: BridgeReply | None
    media: tuple[BridgeMedia, ...]

    def payload(self, member: LinkedBridgeMember, message_id: int) -> dict:
        data = {
            "guildTag": TAQ_GUILD_TAG,
            "username": member.ign,
            "message": self.message,
            "color": member.color if member.color is not None else 0xFFFFFF,
            "discordId": str(member.discord_id),
            "messageId": str(message_id),
        }
        if self.reply is not None or any(not item.inline for item in self.media):
            data["content"] = self.content
        if self.reply is not None:
            data["reply"] = self.reply.payload()
        if self.media:
            data["media"] = [item.payload() for item in self.media]
        return data


class GuildChatBridge(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.channel_id = 0
        self.webhook_id = 0
        self.webhook = None
        self.session = None
        self.ws = None
        self.socket_task = None
        self.recent_discord_messages = deque(maxlen=RECENT_DISCORD_MESSAGES)
        self.bridge_message_authors = OrderedDict()
        self.item_bridge = ItemTooltipBridge()
        self.rotate_bridge_channel.start()

    def cog_unload(self):
        self.rotate_bridge_channel.cancel()
        if self.socket_task:
            self.socket_task.cancel()
        if self.session:
            asyncio.create_task(self.session.close())
        asyncio.create_task(self.item_bridge.close())

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._configured():
            log(WARN, "Guild chat bridge disabled; set GUILD_CHAT_BRIDGE_TOKEN.", context="guild_chat_bridge")
            return
        if self.channel_id == 0:
            try:
                self.channel_id, self.webhook_id = await asyncio.to_thread(self._load_state)
            except Exception as exc:
                log(ERROR, f"Could not load guild chat bridge channel: {exc}", context="guild_chat_bridge")
                return
        channel = await self._ensure_bridge_channel()
        if channel is None:
            log(WARN, "Guild chat bridge disabled; bridge channel could not be found or created.", context="guild_chat_bridge")
            return
        self.channel_id = channel.id
        await asyncio.to_thread(self._save_channel_id, channel.id)
        self.webhook = await self._ensure_webhook(channel)
        if not self.socket_task or self.socket_task.done():
            self.socket_task = asyncio.create_task(self._socket_loop())

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not self._configured() or message.author.bot or message.guild is None:
            return
        if message.guild.id != TAQ_GUILD_ID or message.channel.id != self.channel_id:
            return
        if message.id in self.recent_discord_messages:
            return
        self.recent_discord_messages.append(message.id)

        member = await asyncio.to_thread(_linked_member, message.author.id)
        if member is None:
            return
        prepared = await self._prepare_discord_message(message)
        if prepared is None:
            return
        self._remember_bridge_author(message.id, member.ign)
        if not self.ws or self.ws.closed:
            log(WARN, "Dropped Discord bridge message; Worker socket is offline.", context="guild_chat_bridge")
            return

        try:
            await self.ws.send_json({
                "type": "discord chat message",
                "data": prepared.payload(member, message.id),
            })
        except Exception as exc:
            log(ERROR, f"Could not relay Discord message to Worker: {exc}", context="guild_chat_bridge")

    async def _socket_loop(self):
        delay = 5
        while not self.client.is_closed():
            try:
                self.session = aiohttp.ClientSession()
                async with self.session.ws_connect(
                    BRIDGE_WORKER_URL,
                    headers={"Authorization": f"Bearer {GUILD_CHAT_BRIDGE_TOKEN}"},
                    heartbeat=30,
                    max_msg_size=16384,
                ) as ws:
                    self.ws = ws
                    delay = 5
                    log(INFO, "Guild chat bridge socket connected.", context="guild_chat_bridge")
                    async for packet in ws:
                        if packet.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_worker_packet(packet.json())
                        elif packet.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(WARN, f"Guild chat bridge socket disconnected: {exc}", context="guild_chat_bridge")
            finally:
                self.ws = None
                if self.session:
                    await self.session.close()
                    self.session = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def _handle_worker_packet(self, packet: dict):
        if packet.get("type") == "pong":
            return
        if packet.get("type") != "minecraft_chat_message":
            return
        data = packet.get("data") or {}
        if data.get("guildTag") != TAQ_GUILD_TAG:
            return

        username = str(data.get("username") or "")[:16]
        message = str(data.get("message") or "").strip()
        if not username or not message:
            return

        channel = self._channel()
        if channel is None:
            log(WARN, "Dropped Minecraft bridge message; bridge channel is missing.", context="guild_chat_bridge")
            return

        webhook = self.webhook or await self._ensure_webhook(channel)
        if webhook is None:
            log(WARN, "Dropped Minecraft bridge message; bridge webhook is unavailable.", context="guild_chat_bridge")
            return
        self.webhook = webhook

        # Sending through the bridge requires Verge login verification, which only succeeds
        # for a linked TAq member, so a poster should always resolve here.
        poster = await self._resolve_poster(channel.guild, username)
        if poster is None:
            log(WARN, f"Dropped Minecraft bridge message; {username} did not resolve to a linked member.", context="guild_chat_bridge")
            return

        try:
            prepared = await self.item_bridge.prepare(message)
        except ValueError as exc:
            log(WARN, f"Item tooltip preparation failed: {exc}", context="guild_chat_bridge")
            prepared = None
        for error in prepared.errors if prepared else ():
            log(WARN, error, context="guild_chat_bridge")

        content = _discord_safe_text(prepared.content if prepared else message)
        content = await _resolve_ign_mentions(content)
        attachments = prepared.attachments if prepared else ()

        async def post(hook: discord.Webhook):
            sent = await hook.send(
                content[:2000],
                username=poster.name,
                avatar_url=poster.avatar_url,
                files=[discord.File(BytesIO(attachment.png), filename=attachment.filename)
                       for attachment in attachments],
                allowed_mentions=discord.AllowedMentions(everyone=False, users=True, roles=False),
                wait=True,
            )
            if sent is not None:
                self._remember_bridge_author(sent.id, username)

        try:
            await post(webhook)
        except discord.NotFound:
            log(WARN, "Guild chat bridge webhook was deleted; recreating.", context="guild_chat_bridge")
            self.webhook = None
            webhook = await self._create_webhook(channel)
            if webhook is None:
                return
            self.webhook = webhook
            try:
                await post(webhook)
            except discord.HTTPException as exc:
                log(ERROR, f"Could not resend Minecraft bridge message: {exc}", context="guild_chat_bridge")
        except discord.HTTPException as exc:
            log(ERROR, f"Could not relay Minecraft message to Discord: {exc}", context="guild_chat_bridge")

    async def _prepare_discord_message(self, message: discord.Message) -> DiscordBridgeMessage | None:
        content = _message_text(message)
        reply = await self._reply_context(message)
        embeds = message.embeds
        if content and URL_PATTERN.search(message.content) and not message.attachments:
            for delay in EMBED_REFRESH_DELAYS:
                if _embed_has_preview(embeds):
                    break
                await asyncio.sleep(delay)
                try:
                    embeds = (await message.channel.fetch_message(message.id)).embeds
                except discord.HTTPException:
                    break
        media = _bridge_media(message.attachments, embeds)
        fallback = _fallback_message(content, reply, media)
        if not fallback:
            return None
        return DiscordBridgeMessage(fallback[:MAX_MESSAGE_LENGTH], content, reply, media)

    async def _reply_context(self, message: discord.Message) -> BridgeReply | None:
        reference = message.reference
        if reference is None or reference.message_id is None:
            return None

        username = self.bridge_message_authors.get(reference.message_id)
        target = reference.resolved if isinstance(reference.resolved, discord.Message) else reference.cached_message
        if target is None:
            try:
                target = await message.channel.fetch_message(reference.message_id)
            except discord.HTTPException:
                target = None
        if target is not None and username is None:
            username = await self._reply_username(message.guild, target)
        if username is None:
            username = "unknown"
        return BridgeReply(_clip(username, 64), _reply_excerpt(target))

    async def _reply_username(self, guild: discord.Guild, target: discord.Message) -> str:
        if target.webhook_id is None and not target.author.bot:
            linked = await asyncio.to_thread(_linked_member, target.author.id)
            if linked is not None:
                return linked.ign

        display_name = _strip_rank_prefix(target.author.display_name)
        matches = [member for member in guild.members if member.display_name == target.author.display_name]
        if len(matches) == 1:
            linked = await asyncio.to_thread(_linked_member, matches[0].id)
            if linked is not None:
                return linked.ign
        return display_name or "unknown"

    def _remember_bridge_author(self, message_id: int, ign: str):
        self.bridge_message_authors[message_id] = ign
        self.bridge_message_authors.move_to_end(message_id)
        while len(self.bridge_message_authors) > RECENT_DISCORD_MESSAGES:
            self.bridge_message_authors.popitem(last=False)

    async def _resolve_poster(self, guild: discord.Guild, ign: str) -> BridgePoster | None:
        discord_id = await asyncio.to_thread(_linked_discord_id, ign)
        if discord_id is None:
            return None
        member = guild.get_member(discord_id)
        if member is None:
            try:
                member = await guild.fetch_member(discord_id)
            except discord.HTTPException:
                member = None
        if member is None:
            return None
        return BridgePoster(
            name=_sanitize_webhook_username(member.display_name),
            avatar_url=member.display_avatar.url,
        )

    async def _ensure_webhook(self, channel: discord.TextChannel) -> discord.Webhook | None:
        if self.webhook_id:
            try:
                hooks = await channel.webhooks()
            except discord.Forbidden:
                log(ERROR, "Missing Manage Webhooks permission for the guild chat bridge channel.", context="guild_chat_bridge")
                return None
            except discord.HTTPException as exc:
                log(WARN, f"Could not list guild chat bridge webhooks: {exc}", context="guild_chat_bridge")
                hooks = []
            existing = discord.utils.get(hooks, id=self.webhook_id)
            if existing is not None:
                return existing
        return await self._create_webhook(channel)

    async def _create_webhook(self, channel: discord.TextChannel) -> discord.Webhook | None:
        try:
            webhook = await channel.create_webhook(name=BRIDGE_WEBHOOK_NAME, reason="Guild chat bridge")
        except discord.Forbidden:
            log(ERROR, "Missing Manage Webhooks permission for the guild chat bridge channel.", context="guild_chat_bridge")
            return None
        except discord.HTTPException as exc:
            log(ERROR, f"Could not create guild chat bridge webhook: {exc}", context="guild_chat_bridge")
            return None
        self.webhook_id = webhook.id
        await asyncio.to_thread(self._save_webhook_id, webhook.id)
        return webhook

    @tasks.loop(minutes=1)
    async def rotate_bridge_channel(self):
        if not CHANNEL_MANAGEMENT_ENABLED:
            return
        if not self._configured() or self.channel_id == 0:
            return

        channel = self._channel()
        if channel is None:
            return

        now = datetime.datetime.now(timezone.utc)
        if now - channel.created_at < datetime.timedelta(hours=BRIDGE_ROTATION_HOURS):
            return

        last_activity = channel.created_at
        if channel.last_message_id:
            last_activity = discord.utils.snowflake_time(channel.last_message_id)
        if now - last_activity < datetime.timedelta(minutes=BRIDGE_IDLE_MINUTES):
            return

        try:
            replacement = await channel.clone(name=BRIDGE_CHANNEL_NAME, reason="Guild chat bridge daily reset")
            await self._place_bridge_channel(replacement)
            # Cloning a channel does not carry its webhooks over, so the old one dies with it.
            self.webhook = await self._create_webhook(replacement)
            await asyncio.to_thread(self._save_channel_id, replacement.id)
            self.channel_id = replacement.id
            await channel.delete(reason="Guild chat bridge daily reset")
            log(INFO, f"Rotated guild chat bridge channel to {replacement.id}.", context="guild_chat_bridge")
        except Exception as exc:
            log(ERROR, f"Could not rotate guild chat bridge channel: {exc}", context="guild_chat_bridge")

    @rotate_bridge_channel.before_loop
    async def before_rotate_bridge_channel(self):
        await self.client.wait_until_ready()

    def _configured(self) -> bool:
        return bool(GUILD_CHAT_BRIDGE_TOKEN)

    def _channel(self):
        if self.channel_id == 0:
            return None
        channel = self.client.get_channel(self.channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    def _load_state(self) -> tuple[int, int]:
        _ensure_state_table()
        with DB() as db:
            db.cursor.execute("SELECT channel_id, webhook_id FROM guild_chat_bridge_state WHERE id = TRUE")
            row = db.cursor.fetchone()
        if not row:
            return 0, 0
        channel_id, webhook_id = row
        return int(channel_id or 0), int(webhook_id or 0)

    def _save_channel_id(self, channel_id: int):
        _ensure_state_table()
        with DB() as db:
            db.cursor.execute(
                """
                INSERT INTO guild_chat_bridge_state (id, channel_id)
                VALUES (TRUE, %s)
                ON CONFLICT (id) DO UPDATE SET channel_id = EXCLUDED.channel_id, updated_at = NOW()
                """,
                (channel_id,),
            )
            db.connection.commit()

    def _save_webhook_id(self, webhook_id: int):
        with DB() as db:
            db.cursor.execute(
                "UPDATE guild_chat_bridge_state SET webhook_id = %s, updated_at = NOW() WHERE id = TRUE",
                (webhook_id,),
            )
            db.connection.commit()

    async def _ensure_bridge_channel(self) -> discord.TextChannel | None:
        guild = self.client.get_guild(TAQ_GUILD_ID)
        if guild is None:
            return None
        channel = self._channel()
        if channel is None:
            channel = self._named_channel()
        if channel is None:
            anchor = self._permission_anchor_channel()
            if anchor is None:
                return None
            channel = await anchor.clone(name=BRIDGE_CHANNEL_NAME, reason="Create guild chat bridge channel")
        if CHANNEL_MANAGEMENT_ENABLED:
            if channel.name != BRIDGE_CHANNEL_NAME:
                await channel.edit(name=BRIDGE_CHANNEL_NAME, reason="Sync guild chat bridge channel name")
            await self._place_bridge_channel(channel)
        return channel

    async def _place_bridge_channel(self, channel: discord.TextChannel):
        permission_anchor = self._permission_anchor_channel()
        position_anchor = self._position_anchor_channel()

        edit = {}
        if position_anchor is not None and channel.category_id != position_anchor.category_id:
            edit["category"] = position_anchor.category
        if permission_anchor is not None:
            anchor_overwrites = dict(permission_anchor.overwrites)
            if channel.overwrites != anchor_overwrites:
                edit["overwrites"] = anchor_overwrites
        if edit:
            await channel.edit(**edit, reason="Sync guild chat bridge channel with build-discussions")

        if position_anchor is not None:
            await channel.edit(position=position_anchor.position + 1, reason="Place guild chat bridge channel below guild-general")

    def _permission_anchor_channel(self) -> discord.TextChannel | None:
        channel = self.client.get_channel(BRIDGE_PERMISSION_ANCHOR_CHANNEL_ID)
        return channel if isinstance(channel, discord.TextChannel) else None

    def _position_anchor_channel(self) -> discord.TextChannel | None:
        channel = self.client.get_channel(BRIDGE_POSITION_ANCHOR_CHANNEL_ID)
        return channel if isinstance(channel, discord.TextChannel) else None

    def _named_channel(self) -> discord.TextChannel | None:
        guild = self.client.get_guild(TAQ_GUILD_ID)
        if guild is None:
            return None
        matches = sorted(
            (
                channel
                for channel in guild.text_channels
                if channel.name == BRIDGE_CHANNEL_NAME
            ),
            key=lambda channel: (channel.category_id or 0, channel.position, channel.id),
        )
        return matches[0] if matches else None


def _ensure_state_table():
    with DB() as db:
        db.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_chat_bridge_state (
                id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
                channel_id BIGINT NOT NULL,
                webhook_id BIGINT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        db.cursor.execute("ALTER TABLE guild_chat_bridge_state ADD COLUMN IF NOT EXISTS webhook_id BIGINT")
        db.connection.commit()


def _linked_member(discord_id: int) -> LinkedBridgeMember | None:
    with DB() as db:
        db.cursor.execute(
            """
            SELECT dl.discord_id, dl.ign, dl.rank, dl.color_primary
            FROM discord_links dl
            JOIN guild_roster gr ON gr.uuid = dl.uuid
            WHERE dl.discord_id = %s
              AND dl.rank IS NOT NULL
            LIMIT 1
            """,
            (discord_id,),
        )
        row = db.cursor.fetchone()
    if not row:
        return None

    _, ign, rank, color = row
    if rank not in discord_ranks:
        return None
    return LinkedBridgeMember(
        discord_id=discord_id,
        ign=str(ign),
        color=_color_value(color),
    )


def _linked_discord_id(ign: str) -> int | None:
    with DB() as db:
        db.cursor.execute(
            """
            SELECT dl.discord_id
            FROM discord_links dl
            JOIN guild_roster gr ON gr.uuid = dl.uuid
            WHERE LOWER(dl.ign) = LOWER(%s)
            LIMIT 1
            """,
            (ign,),
        )
        row = db.cursor.fetchone()
    return int(row[0]) if row else None


async def _resolve_ign_mentions(text: str) -> str:
    tokens = {match.group(1) for match in IGN_MENTION_PATTERN.finditer(text)}
    if not tokens:
        return text

    resolved: dict[str, int] = {}
    for token in tokens:
        discord_id = await asyncio.to_thread(_linked_discord_id, token)
        if discord_id is not None:
            resolved[token.lower()] = discord_id
    if not resolved:
        return text

    def replace(match: re.Match) -> str:
        discord_id = resolved.get(match.group(1).lower())
        return f"<@{discord_id}>" if discord_id is not None else match.group(0)

    return IGN_MENTION_PATTERN.sub(replace, text)


def _sanitize_webhook_username(name: str) -> str:
    cleaned = name.strip()
    for forbidden in WEBHOOK_NAME_FORBIDDEN:
        cleaned = re.sub(re.escape(forbidden), "*" * len(forbidden), cleaned, flags=re.IGNORECASE)
    cleaned = cleaned[:80].strip()
    return cleaned or "Player"


def _bridge_media(attachments, embeds) -> tuple[BridgeMedia, ...]:
    media = []
    attachment_urls = set()
    for attachment in attachments:
        if len(media) >= MAX_MEDIA_ITEMS:
            break
        content_type = (attachment.content_type or "").lower()
        filename = attachment.filename
        extension = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        if content_type.startswith("video/"):
            kind = "video"
        elif content_type == "image/gif" or extension == "gif":
            kind = "gif"
        elif content_type.startswith("image/") or extension in {"png", "jpg", "jpeg", "webp"}:
            kind = "image"
        else:
            continue
        url = _media_url(attachment.url)
        if not url:
            continue
        spoiler = attachment.is_spoiler()
        preview_url = "" if spoiler or kind == "video" else _media_url(attachment.proxy_url)
        media.append(BridgeMedia(
            kind=kind,
            url=url,
            label=_clip(filename, 96),
            preview_url=preview_url,
            description=_clip(attachment.description or "", 160),
            spoiler=spoiler,
        ))
        attachment_urls.add(url)

    for embed in embeds:
        if len(media) >= MAX_MEDIA_ITEMS:
            break
        url = _media_url(embed.url)
        if not url or url in attachment_urls or any(item.url == url for item in media):
            continue
        image = embed.image or embed.thumbnail
        preview_url = _embed_preview_url(image)
        provider = _clip(getattr(embed.provider, "name", "") or "", 64)
        kind = "gif" if embed.type == "gifv" or url.lower().split("?", 1)[0].endswith(".gif") else "link"
        media.append(BridgeMedia(
            kind=kind,
            url=url,
            label=_clip(embed.title or provider or "link", 96),
            preview_url=preview_url,
            title=_clip(embed.title or "", 96),
            description=_clip(embed.description or "", 160),
            provider=provider,
            inline=True,
        ))
    return tuple(media)


def _embed_has_preview(embeds) -> bool:
    for embed in embeds:
        image = embed.image or embed.thumbnail
        if _embed_preview_url(image):
            return True
    return False


def _fallback_message(content: str, reply: BridgeReply | None, media: tuple[BridgeMedia, ...]) -> str:
    parts = []
    if reply is not None:
        parts.append(f"replied to {reply.username}:")
    for item in media:
        if item.inline:
            continue
        if item.kind == "video":
            parts.append("[sent a video]")
        elif item.kind == "gif":
            parts.append(f"[GIF: {item.label}]")
        else:
            parts.append("[spoiler image]" if item.spoiler else "[image]")
    if content:
        parts.append(content)
    return " ".join(parts).strip()


def _reply_excerpt(message: discord.Message | None) -> str:
    if message is None:
        return ""
    text = " ".join(_message_text(message).split())
    if not text:
        media = _bridge_media(message.attachments, message.embeds)
        text = _fallback_message("", None, media)
    return _clip(text, MAX_REPLY_EXCERPT_LENGTH)


def _media_url(value) -> str:
    text = str(value or "").strip()
    return text if text.startswith("https://") and len(text) <= MAX_MEDIA_URL_LENGTH else ""


def _embed_preview_url(image) -> str:
    for attribute in ("url", "proxy_url"):
        url = _media_url(getattr(image, attribute, ""))
        if not url:
            continue
        try:
            host = urlsplit(url).hostname
        except ValueError:
            continue
        if host and (
            host in {"cdn.discordapp.com", "media.discordapp.net"}
            or host.endswith(".discordapp.net")
        ):
            return url
    return ""


def _clip(value: str, limit: int) -> str:
    return value.strip()[:limit]


def _message_text(message: discord.Message) -> str:
    text = _resolve_mention_text(message)
    text = _normalize_emoji(text)
    text = text.strip()
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH]
    return text


def _resolve_mention_text(message: discord.Message) -> str:
    # message.clean_content resolves mentions to display names, but that keeps the TAq
    # rank-tag prefix from the member's server nickname (e.g. "Swordfish lamelemon"); resolve
    # mentions ourselves so we can strip that prefix before it reaches Minecraft chat.
    text = message.content
    for member in message.mentions:
        name = f"@{_strip_rank_prefix(member.display_name)}"
        text = text.replace(f"<@{member.id}>", name).replace(f"<@!{member.id}>", name)
    for role in message.role_mentions:
        text = text.replace(f"<@&{role.id}>", f"@{role.name}")
    for channel in message.channel_mentions:
        text = text.replace(f"<#{channel.id}>", f"#{channel.name}")
    return text


def _strip_rank_prefix(name: str) -> str:
    return RANK_TAG_PATTERN.sub("", name, count=1)


def _normalize_emoji(text: str) -> str:
    text = CUSTOM_EMOJI_PATTERN.sub(r":\1:", text)
    return emoji.demojize(text, language="en")


def _discord_safe_text(text: str) -> str:
    safe = discord.utils.escape_mentions(text)
    return discord.utils.escape_markdown(safe, as_needed=True)


def _color_value(value) -> int | None:
    if value is None:
        return None
    try:
        color = int(value)
    except (TypeError, ValueError):
        return None
    return color if 0 <= color <= 0xFFFFFF else None


def setup(client):
    client.add_cog(GuildChatBridge(client))
