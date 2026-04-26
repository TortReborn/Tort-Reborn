"""
Background task that syncs edits made in the website's embed editor back to
Discord.

Polls `managed_messages` for rows where `dirty = TRUE` and:
  - If `pending_delete`: deletes the Discord message, then removes the row.
  - If `is_new` (no message_id yet): sends a new message, stores the ID.
  - Otherwise: edits the existing message with the new content/embeds.
"""

import asyncio
import json

import discord
from discord.ext import tasks, commands

from Helpers.database import DB
from Helpers.logger import log, ERROR, INFO, WARN


BATCH_LIMIT = 10


def _fetch_dirty() -> list[dict]:
    db = DB()
    try:
        db.connect()
        db.cursor.execute(
            """
            SELECT id, channel_id, message_id, content, embeds,
                   is_new, pending_delete
            FROM managed_messages
            WHERE dirty = TRUE
            ORDER BY updated_at ASC
            LIMIT %s
            """,
            (BATCH_LIMIT,),
        )
        rows = db.cursor.fetchall()
        return [
            {
                "id": r[0],
                "channel_id": r[1],
                "message_id": r[2],
                "content": r[3],
                "embeds": r[4] if isinstance(r[4], list) else (json.loads(r[4]) if r[4] else []),
                "is_new": r[5],
                "pending_delete": r[6],
            }
            for r in rows
        ]
    finally:
        db.close()


def _mark_synced(row_id: int, message_id: int | None = None):
    db = DB()
    try:
        db.connect()
        if message_id is not None:
            db.cursor.execute(
                """
                UPDATE managed_messages
                SET dirty = FALSE, is_new = FALSE, message_id = %s,
                    last_synced_at = NOW()
                WHERE id = %s
                """,
                (message_id, row_id),
            )
        else:
            db.cursor.execute(
                """
                UPDATE managed_messages
                SET dirty = FALSE, last_synced_at = NOW()
                WHERE id = %s
                """,
                (row_id,),
            )
        db.connection.commit()
    finally:
        db.close()


def _delete_row(row_id: int):
    db = DB()
    try:
        db.connect()
        db.cursor.execute("DELETE FROM managed_messages WHERE id = %s", (row_id,))
        db.connection.commit()
    finally:
        db.close()


def _clear_message_id(row_id: int):
    """Message was deleted externally on Discord — drop our reference so the
    next save from the UI will re-send it as a new message."""
    db = DB()
    try:
        db.connect()
        db.cursor.execute(
            """
            UPDATE managed_messages
            SET message_id = NULL, is_new = TRUE, dirty = FALSE,
                last_synced_at = NOW()
            WHERE id = %s
            """,
            (row_id,),
        )
        db.connection.commit()
    finally:
        db.close()


def _build_embeds(embed_dicts: list[dict]) -> list[discord.Embed]:
    embeds: list[discord.Embed] = []
    for ed in embed_dicts or []:
        if not isinstance(ed, dict):
            continue
        try:
            embeds.append(discord.Embed.from_dict(ed))
        except Exception as e:
            log(WARN, f"Skipping invalid embed dict: {e}", context="sync_managed_messages")
    return embeds


class SyncManagedMessages(commands.Cog):
    def __init__(self, client):
        self.client = client

    @tasks.loop(seconds=10)
    async def sync_messages(self):
        rows = await asyncio.to_thread(_fetch_dirty)
        if not rows:
            return

        for row in rows:
            try:
                await self._sync_one(row)
            except Exception as e:
                log(ERROR,
                    f"Unexpected error syncing managed message {row['id']}: {e}",
                    context="sync_managed_messages")
            # Small delay so a burst of edits doesn't blow through Discord's
            # per-channel edit rate limits.
            await asyncio.sleep(0.5)

    async def _sync_one(self, row: dict):
        channel = self.client.get_channel(row["channel_id"])
        if channel is None:
            log(WARN,
                f"Channel {row['channel_id']} not found for managed message {row['id']}",
                context="sync_managed_messages")
            # Leave dirty=TRUE so it retries once the channel is available.
            return

        # --- Deletion ---
        if row["pending_delete"]:
            if row["message_id"]:
                try:
                    msg = await channel.fetch_message(row["message_id"])
                    await msg.delete()
                except discord.NotFound:
                    pass
                except Exception as e:
                    log(ERROR,
                        f"Failed to delete Discord message {row['message_id']}: {e}",
                        context="sync_managed_messages")
                    # Leave the row for retry.
                    return
            await asyncio.to_thread(_delete_row, row["id"])
            log(INFO, f"Deleted managed message {row['id']}", context="sync_managed_messages")
            return

        embeds = _build_embeds(row["embeds"])
        content = row["content"] or None

        # --- New message ---
        if row["is_new"] or not row["message_id"]:
            try:
                sent = await channel.send(content=content, embeds=embeds)
            except Exception as e:
                log(ERROR,
                    f"Failed to send managed message {row['id']}: {e}",
                    context="sync_managed_messages")
                return
            await asyncio.to_thread(_mark_synced, row["id"], sent.id)
            log(INFO,
                f"Sent new managed message {row['id']} -> {sent.id}",
                context="sync_managed_messages")
            return

        # --- Edit existing ---
        try:
            msg = await channel.fetch_message(row["message_id"])
        except discord.NotFound:
            log(WARN,
                f"Discord message {row['message_id']} missing; clearing reference "
                f"on managed_messages row {row['id']}",
                context="sync_managed_messages")
            await asyncio.to_thread(_clear_message_id, row["id"])
            return
        except Exception as e:
            log(ERROR,
                f"Failed to fetch {row['message_id']}: {e}",
                context="sync_managed_messages")
            return

        try:
            await msg.edit(content=content, embeds=embeds)
        except Exception as e:
            log(ERROR,
                f"Failed to edit managed message {row['message_id']}: {e}",
                context="sync_managed_messages")
            return

        await asyncio.to_thread(_mark_synced, row["id"])

    @sync_messages.before_loop
    async def before_sync(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.sync_messages.is_running():
            self.sync_messages.start()


def setup(client):
    client.add_cog(SyncManagedMessages(client))
