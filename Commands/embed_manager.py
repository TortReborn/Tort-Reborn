"""
/embeds admin commands for the in-house embed management system.

Replaces Discohook by letting execs import existing channel messages into the
`managed_messages` table. The sync task (Tasks/sync_managed_messages.py) picks
up changes made through the website and pushes them back to Discord.
"""

import asyncio
import json
from urllib.parse import quote

import aiohttp
import discord
from discord import Permissions
from discord.commands import SlashCommandGroup
from discord.ext import commands

from Helpers.database import DB
from Helpers.logger import log, INFO, ERROR, WARN
from Helpers.storage import storage
from Helpers.variables import HOME_GUILD_IDS, WEBSITE_URL


def _public_image_url(s3_key: str) -> str:
    """Build a public URL for an S3 object that Discord can fetch.

    The website exposes /api/embeds/image?key=... as an unauthenticated proxy
    for objects under the `embeds/` prefix.
    """
    base = WEBSITE_URL.rstrip("/")
    return f"{base}/api/embeds/image?key={quote(s3_key, safe='/')}"


async def _download(url: str) -> bytes | None:
    """Download an image/file from a URL, returning bytes or None on failure."""
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url) as resp:
                if resp.status != 200:
                    return None
                return await resp.read()
    except Exception as e:
        log(WARN, f"download failed for {url}: {e}", context="embed_manager")
        return None


async def _rehost_attachment(channel_id: int, message_id: int, att: discord.Attachment) -> dict | None:
    """Download a Discord attachment and re-upload it to S3.

    Returns a dict {url, filename, content_type, s3_key} on success, or None on failure.
    """
    data = await _download(att.url)
    if not data:
        return None

    safe_name = att.filename.replace("/", "_").replace("\\", "_")
    s3_key = f"embeds/{channel_id}/{message_id}_{safe_name}"
    content_type = att.content_type or "application/octet-stream"

    try:
        await asyncio.to_thread(storage.put_bytes, s3_key, data, content_type)
    except Exception as e:
        log(ERROR, f"S3 upload failed for {s3_key}: {e}", context="embed_manager")
        return None

    return {
        "url": _public_image_url(s3_key),
        "filename": att.filename,
        "content_type": content_type,
        "s3_key": s3_key,
    }


def _rewrite_embed_cdn_urls(embed_dict: dict, attachments_meta: list[dict]) -> dict:
    """Replace any embed image/thumbnail/author/footer icon URLs that reference
    a Discord CDN attachment with the matching rehosted S3 URL.

    Discord CDN URLs now expire; rewriting them ensures the embed still renders
    after the message is re-edited by the sync task.
    """
    if not attachments_meta:
        return embed_dict

    # Map original filename -> new rehosted url
    by_filename = {a["filename"]: a["url"] for a in attachments_meta}

    def _swap(url: str | None) -> str | None:
        if not url:
            return url
        for filename, new_url in by_filename.items():
            if filename in url:
                return new_url
        return url

    for key in ("image", "thumbnail"):
        obj = embed_dict.get(key)
        if isinstance(obj, dict) and obj.get("url"):
            obj["url"] = _swap(obj["url"])

    author = embed_dict.get("author")
    if isinstance(author, dict) and author.get("icon_url"):
        author["icon_url"] = _swap(author["icon_url"])

    footer = embed_dict.get("footer")
    if isinstance(footer, dict) and footer.get("icon_url"):
        footer["icon_url"] = _swap(footer["icon_url"])

    return embed_dict


def _upsert_message(
    channel_id: int,
    message_id: int,
    position: int,
    content: str | None,
    embeds: list[dict],
    attachments: list[dict],
):
    db = DB()
    try:
        db.connect()
        db.cursor.execute(
            """
            INSERT INTO managed_messages (
                channel_id, message_id, position, content, embeds, attachments,
                dirty, is_new, last_synced_at, updated_at, created_at
            )
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, FALSE, FALSE, NOW(), NOW(), NOW())
            ON CONFLICT (message_id) DO UPDATE SET
                channel_id    = EXCLUDED.channel_id,
                position      = EXCLUDED.position,
                content       = EXCLUDED.content,
                embeds        = EXCLUDED.embeds,
                attachments   = EXCLUDED.attachments,
                dirty         = FALSE,
                is_new        = FALSE,
                last_synced_at = NOW(),
                updated_at    = NOW()
            """,
            (
                channel_id,
                message_id,
                position,
                content or None,
                json.dumps(embeds),
                json.dumps(attachments),
            ),
        )
        db.connection.commit()
    finally:
        db.close()


def _fetch_managed_channels() -> list[dict]:
    db = DB()
    try:
        db.connect()
        db.cursor.execute(
            "SELECT channel_id, guild_id, label FROM managed_channels ORDER BY label"
        )
        return [
            {"channel_id": row[0], "guild_id": row[1], "label": row[2]}
            for row in db.cursor.fetchall()
        ]
    finally:
        db.close()


def _clear_channel_messages(channel_id: int):
    db = DB()
    try:
        db.connect()
        db.cursor.execute(
            "DELETE FROM managed_messages WHERE channel_id = %s", (channel_id,)
        )
        db.connection.commit()
    finally:
        db.close()


class EmbedManager(commands.Cog):
    embeds_group = SlashCommandGroup(
        "embeds",
        "ADMIN: Manage the in-house embed system",
        guild_ids=HOME_GUILD_IDS,
        default_member_permissions=Permissions(administrator=True),
    )

    def __init__(self, client):
        self.client = client

    async def _import_channel(self, channel: discord.TextChannel, wipe: bool = False) -> tuple[int, int]:
        """Import all messages from a channel into managed_messages.

        Returns (imported_count, skipped_count).
        """
        if wipe:
            await asyncio.to_thread(_clear_channel_messages, channel.id)

        imported = 0
        skipped = 0
        position = 0

        async for msg in channel.history(limit=500, oldest_first=True):
            # Skip messages with interactive components -- those are owned by
            # other cogs (generate.py, etc.) and must not be edited from the
            # embed UI.
            if msg.components:
                skipped += 1
                continue
            # Skip messages that aren't authored by our bot -- we can't edit
            # those anyway.
            if msg.author.id != self.client.user.id:
                skipped += 1
                continue

            # Rehost attachments first so we can rewrite CDN URLs in embeds.
            attachments_meta: list[dict] = []
            for att in msg.attachments:
                meta = await _rehost_attachment(channel.id, msg.id, att)
                if meta:
                    attachments_meta.append(meta)

            embed_dicts = []
            for e in msg.embeds:
                ed = e.to_dict()
                ed = _rewrite_embed_cdn_urls(ed, attachments_meta)
                embed_dicts.append(ed)

            await asyncio.to_thread(
                _upsert_message,
                channel.id,
                msg.id,
                position,
                msg.content,
                embed_dicts,
                attachments_meta,
            )
            imported += 1
            position += 1

        return imported, skipped

    @embeds_group.command(
        name="import",
        description="ADMIN: Import all messages from a managed channel into the DB",
    )
    async def import_cmd(
        self,
        ctx: discord.ApplicationContext,
        channel: discord.Option(
            discord.TextChannel,
            "Channel to import (must be in managed_channels). Omit to import all.",
            required=False,
            default=None,
        ),
        wipe: discord.Option(
            bool,
            "Delete existing DB rows for this channel first",
            required=False,
            default=False,
        ),
    ):
        await ctx.defer(ephemeral=True)

        managed = await asyncio.to_thread(_fetch_managed_channels)
        managed_ids = {m["channel_id"] for m in managed}

        if channel is not None:
            if channel.id not in managed_ids:
                return await ctx.followup.send(
                    f"❌ <#{channel.id}> is not in `managed_channels`. Register it first.",
                    ephemeral=True,
                )
            targets = [channel]
        else:
            targets = []
            for m in managed:
                ch = self.client.get_channel(m["channel_id"])
                if ch:
                    targets.append(ch)

        if not targets:
            return await ctx.followup.send(
                "❌ No managed channels found.", ephemeral=True
            )

        lines: list[str] = []
        for ch in targets:
            try:
                imp, skip = await self._import_channel(ch, wipe=wipe)
                lines.append(f"<#{ch.id}> — imported **{imp}**, skipped **{skip}**")
                log(INFO, f"Imported {imp} messages from {ch.name} (skipped {skip})",
                    context="embed_manager")
            except Exception as e:
                log(ERROR, f"Import failed for {ch.id}: {e}", context="embed_manager")
                lines.append(f"<#{ch.id}> — ❌ failed: `{e}`")

        await ctx.followup.send(
            "**Embed import complete**\n" + "\n".join(lines),
            ephemeral=True,
        )

    @embeds_group.command(
        name="reimport",
        description="ADMIN: Wipe + re-import a managed channel (use after manual Discord edits)",
    )
    async def reimport_cmd(
        self,
        ctx: discord.ApplicationContext,
        channel: discord.Option(
            discord.TextChannel,
            "Channel to re-import",
            required=True,
        ),
    ):
        # Thin wrapper delegating to import with wipe=True.
        await ctx.defer(ephemeral=True)

        managed = await asyncio.to_thread(_fetch_managed_channels)
        if channel.id not in {m["channel_id"] for m in managed}:
            return await ctx.followup.send(
                f"❌ <#{channel.id}> is not in `managed_channels`.", ephemeral=True
            )

        try:
            imp, skip = await self._import_channel(channel, wipe=True)
        except Exception as e:
            return await ctx.followup.send(
                f"❌ Reimport failed: `{e}`", ephemeral=True
            )

        await ctx.followup.send(
            f"✅ Reimported <#{channel.id}> — **{imp}** imported, **{skip}** skipped.",
            ephemeral=True,
        )

    @embeds_group.command(
        name="register_channel",
        description="ADMIN: Register a new channel to be managed through the embed editor",
    )
    async def register_channel(
        self,
        ctx: discord.ApplicationContext,
        channel: discord.Option(discord.TextChannel, "Channel to manage", required=True),
        label: discord.Option(str, "Display label for the dashboard", required=True),
    ):
        await ctx.defer(ephemeral=True)

        def _insert():
            db = DB()
            try:
                db.connect()
                db.cursor.execute(
                    """
                    INSERT INTO managed_channels (channel_id, guild_id, label)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (channel_id) DO UPDATE SET label = EXCLUDED.label
                    """,
                    (channel.id, channel.guild.id, label),
                )
                db.connection.commit()
            finally:
                db.close()

        await asyncio.to_thread(_insert)
        await ctx.followup.send(
            f"✅ Registered <#{channel.id}> as **{label}**. Run `/embeds import channel:#{channel.name}` next.",
            ephemeral=True,
        )


def setup(client):
    client.add_cog(EmbedManager(client))
