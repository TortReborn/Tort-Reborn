import asyncio
import traceback

import discord
from discord import Embed
from discord.ext import commands

from Helpers.database import DB
from Helpers.variables import member_app_channel, manual_review_role_id, error_channel

STATUS_COLOURS = {
    ":green_circle: Opened": 0x3ED63E,
    ":hourglass: In Queue":  0xFFE019,
    ":hourglass: Invited":   0xFFE019,
}

def _db_fetch_row(channel_id: int):
    """Blocking DB: fetch row for channel."""
    db = DB()
    try:
        db.connect()
        db.cursor.execute(
            """
            SELECT channel, ticket, status, thread_id
              FROM new_app
             WHERE channel = %s
            """,
            (channel_id,)
        )
        return db.cursor.fetchone()
    finally:
        db.close()

def _db_update_status(channel_id: int, new_status: str):
    """Blocking DB: update status."""
    db = DB()
    try:
        db.connect()
        db.cursor.execute(
            "UPDATE new_app SET status = %s WHERE channel = %s",
            (new_status, channel_id)
        )
        db.connection.commit()
    finally:
        db.close()


class OnGuildChannelUpdate(commands.Cog):
    def __init__(self, client: discord.Client):
        self.client = client
        self._queue = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

    # -------------- event -> enqueue fast --------------
    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.TextChannel, after: discord.TextChannel):
        # Only trigger if the name changed
        if before.name == after.name:
            return

        # Compute status quickly on loop (pure CPU, trivial)
        if after.category and after.category.name == "Guild Queue":
            new_status = ":hourglass: In Queue"
        elif after.category and after.category.name == "Invited":
            new_status = ":hourglass: Invited"
        else:
            prefix = after.name.split("-", 1)[0].lower()
            match prefix:
                case "closed":
                    new_status = ":lock: Closed"
                case "ticket":
                    new_status = ":green_circle: Opened"
                case "accepted":
                    new_status = ":white_check_mark: Accepted"
                case "denied":
                    new_status = ":x: Denied"
                case "na":
                    new_status = ":grey_question: N/A"
                case other:
                    new_status = other.capitalize()

        colour = STATUS_COLOURS.get(
            new_status,
            0xD93232 if new_status != ":green_circle: Opened" else 0x3ED63E
        )

        # Enqueue work so we return ASAP (avoids heartbeat starvation)
        await self._queue.put((before.id, new_status, colour))

    # -------------- background worker --------------
    async def _worker(self):
        while True:
            channel_id, new_status, colour = await self._queue.get()
            try:
                # BLOCKING DB fetch in a thread
                row = await asyncio.to_thread(_db_fetch_row, channel_id)
                ticket = None

                if row:
                    _, ticket, _, thread_id = row
                    if not thread_id:
                        print(f"🚨 No thread_id stored for channel {channel_id}; skipping post.")
                    else:
                        # BLOCKING DB update in a thread
                        await asyncio.to_thread(_db_update_status, channel_id, new_status)

                        # Build embed
                        ticket_str = ticket.replace("ticket-", "") if ticket else "?"
                        embed = Embed(
                            title=f"Application {ticket_str}",
                            description="Status updated — please review below:",
                            colour=colour,
                        )
                        embed.add_field(name="Channel", value=f"<#{channel_id}>", inline=True)
                        embed.add_field(name="Status",  value=new_status, inline=True)

                        # Send into the associated thread (fetch if not cached)
                        thread = self.client.get_channel(thread_id)
                        if thread is None:
                            try:
                                thread = await self.client.fetch_channel(thread_id)
                            except Exception as e:
                                print(f"🚨 Could not fetch thread {thread_id}: {e}")
                                thread = None

                        if thread and isinstance(thread, discord.Thread):
                            try:
                                if getattr(thread, "archived", False):
                                    async with asyncio.timeout(8):
                                        await thread.edit(archived=False)
                                async with asyncio.timeout(8):
                                    await thread.send(embed=embed)
                            except asyncio.TimeoutError:
                                print(f"⚠️ Timed out sending to thread {thread_id}")
                            except Exception as e:
                                print(f"🚨 Failed to send update to thread {thread_id}: {e}")
                        elif thread:
                            print(f"🚨 Channel {thread_id} is not a thread; got {type(thread)}")

                # Process accepted tickets for recruiter tracking
                # Works with or without a new_app DB row (for testing)
                if new_status == ":white_check_mark: Accepted":
                    # Use DB ticket name if available, otherwise derive from channel
                    if not ticket:
                        ch = self.client.get_channel(channel_id)
                        ticket = ch.name if ch else f"channel-{channel_id}"
                    try:
                        await self._process_accepted_ticket(channel_id, ticket)
                    except Exception as e:
                        tb = ''.join(traceback.format_exception(e))[:800]
                        err_ch = self.client.get_channel(error_channel)
                        if err_ch:
                            await err_ch.send(
                                f"## Recruiter Tracker Error\n"
                                f"**Source:** accepted ticket `{ticket}`\n"
                                f"```\n{tb}\n```"
                            )
            finally:
                self._queue.task_done()

    async def _process_accepted_ticket(self, channel_id: int, ticket: str):
        import re
        from Helpers.openai_helper import parse_application
        from Helpers.sheets import add_row

        # Extract just the ticket number (e.g. "ticket-3650" -> "3650")
        num_match = re.search(r'(\d+)', ticket)
        ticket_num = num_match.group(1) if num_match else ticket

        channel = self.client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.client.fetch_channel(channel_id)
            except Exception:
                return

        # Collect message text from the ticket channel
        message_text = ""
        async for msg in channel.history(limit=50, oldest_first=True):
            if msg.embeds:
                for embed in msg.embeds:
                    for field in embed.fields:
                        message_text += f"{field.name}: {field.value}\n"
                    if embed.description:
                        message_text += f"{embed.description}\n"
            elif not msg.author.bot:
                message_text += f"{msg.content}\n"

        if not message_text.strip():
            return

        result = await asyncio.to_thread(parse_application, message_text)

        if result.get("error"):
            err_ch = self.client.get_channel(error_channel)
            if err_ch:
                await err_ch.send(
                    f"## Recruiter Tracker - OpenAI Error\n"
                    f"**Ticket:** `{ticket}`\n"
                    f"```\n{result['error'][:500]}\n```"
                )
            return

        ign = result.get("ign", "")
        recruiter = result.get("recruiter", "")
        certainty = result.get("certainty", 0.0)

        if certainty >= 0.90 and ign:
            sheet_result = await asyncio.to_thread(add_row, ticket_num, ign, recruiter)

            if not sheet_result.get("success"):
                err_ch = self.client.get_channel(error_channel)
                if err_ch:
                    await err_ch.send(
                        f"## Recruiter Tracker - Sheets Error\n"
                        f"**Ticket:** `{ticket}` | **IGN:** `{ign}`\n"
                        f"```\n{sheet_result.get('error', 'Unknown')[:500]}\n```"
                    )
        else:
            review_ch = self.client.get_channel(member_app_channel)
            if review_ch:
                await review_ch.send(
                    f"<@&{manual_review_role_id}> **Recruiter tracking needs manual review**\n"
                    f"**Ticket:** `{ticket}` | **Parsed IGN:** `{ign}` | "
                    f"**Parsed Recruiter:** `{recruiter}` | **Certainty:** `{certainty:.0%}`\n"
                    f"Please update the recruiter sheet manually."
                )

    @commands.Cog.listener()
    async def on_ready(self):
        # Start worker once
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())

    def cog_unload(self):
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()


def setup(client):
    client.add_cog(OnGuildChannelUpdate(client))
