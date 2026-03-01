import asyncio
import datetime

import discord
from discord.ext import tasks, commands

from Helpers.database import DB
from Helpers.logger import log, ERROR, INFO
from Helpers.variables import KICK_LIST_CHANNEL_ID


# ---------------------------------------------------------------------------
# DB helpers (all synchronous — run via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _fetch_kick_list_sync() -> list[tuple]:
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "SELECT uuid, ign, tier, added_by, created_at "
            "FROM kick_list ORDER BY tier, created_at"
        )
        return db.cursor.fetchall()
    finally:
        db.close()


def _get_setting_sync(key: str) -> str | None:
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "SELECT value FROM bot_settings WHERE key = %s", (key,)
        )
        row = db.cursor.fetchone()
        return row[0] if row else None
    finally:
        db.close()


def _set_setting_sync(key: str, value: str):
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "INSERT INTO bot_settings (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, value),
        )
        db.connection.commit()
    finally:
        db.close()


def _add_to_kick_list_sync(uuid: str, ign: str, tier: int, added_by: str):
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "INSERT INTO kick_list (uuid, ign, tier, added_by, created_at) "
            "VALUES (%s, %s, %s, %s, NOW()) "
            "ON CONFLICT (uuid) DO UPDATE SET ign = EXCLUDED.ign, tier = EXCLUDED.tier, "
            "added_by = EXCLUDED.added_by, created_at = NOW()",
            (uuid, ign, tier, added_by),
        )
        db.connection.commit()
    finally:
        db.close()


def _remove_from_kick_list_sync(ign: str) -> bool:
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "DELETE FROM kick_list WHERE LOWER(ign) = LOWER(%s)", (ign,)
        )
        removed = db.cursor.rowcount > 0
        db.connection.commit()
        return removed
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Embed builder
# ---------------------------------------------------------------------------

TIER_LABELS = {1: "High Priority", 2: "Medium Priority", 3: "Low Priority"}


def build_kick_list_embed(rows: list[tuple]) -> discord.Embed:
    embed = discord.Embed(
        title="Kick List",
        color=0xFF0000,
    )

    if not rows:
        embed.description = "No players on the kick list."
        return embed

    # Group by tier
    tiers: dict[int, list[tuple]] = {}
    for uuid, ign, tier, added_by, created_at in rows:
        tiers.setdefault(tier, []).append((ign, added_by))

    for tier_num in sorted(tiers.keys()):
        label = TIER_LABELS.get(tier_num, f"Tier {tier_num}")
        lines = []
        for ign, added_by in tiers[tier_num]:
            lines.append(f"`{ign}` — added by **{added_by}**")
        embed.add_field(name=label, value="\n".join(lines), inline=False)

    return embed


# ---------------------------------------------------------------------------
# Refresh helper (called by commands for immediate updates)
# ---------------------------------------------------------------------------

_tracker_instance: "KickListTracker | None" = None


async def refresh_kick_list_message(client: discord.Bot):
    """Force an immediate refresh of the kick list perma message."""
    if _tracker_instance is not None:
        await _tracker_instance._update_message()


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class KickListTracker(commands.Cog):
    def __init__(self, client: discord.Bot):
        self.client = client
        self._last_rows: list[tuple] | None = None

        global _tracker_instance
        _tracker_instance = self

    # -- background loop -----------------------------------------------------

    @tasks.loop(minutes=2)
    async def kick_list_loop(self):
        try:
            await self._update_message()
        except Exception as e:
            log(ERROR, f"error: {e!r}", context="kick_list_tracker")

    async def _update_message(self):
        channel = self.client.get_channel(KICK_LIST_CHANNEL_ID)
        if channel is None:
            return

        rows = await asyncio.to_thread(_fetch_kick_list_sync)

        # Skip edit if nothing changed
        if rows == self._last_rows:
            return
        self._last_rows = rows

        embed = build_kick_list_embed(rows)
        raw_id = await asyncio.to_thread(_get_setting_sync, "kick_list_message_id")
        message_id = int(raw_id) if raw_id else None

        if message_id:
            try:
                msg = await channel.fetch_message(message_id)
                await msg.edit(embed=embed)
                return
            except discord.NotFound:
                pass  # Message was deleted — send a new one
            except Exception as e:
                log(ERROR, f"Failed to edit kick list message: {e!r}", context="kick_list_tracker")
                return

        # Send new message, store its ID, and create a discussion thread
        msg = await channel.send(embed=embed)
        await asyncio.to_thread(_set_setting_sync, "kick_list_message_id", str(msg.id))

        try:
            thread = await msg.create_thread(name="Kick List Discussion")
            await asyncio.to_thread(_set_setting_sync, "kick_list_thread_id", str(thread.id))
        except Exception as e:
            log(ERROR, f"Failed to create kick list thread: {e!r}", context="kick_list_tracker")

    # -- lifecycle -----------------------------------------------------------

    @kick_list_loop.before_loop
    async def before_loop(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.kick_list_loop.is_running():
            self.kick_list_loop.start()


def setup(client):
    client.add_cog(KickListTracker(client))
