import asyncio
import json
from datetime import datetime, timezone

import discord
from discord.ext import tasks, commands

from Helpers.logger import log, INFO, ERROR
from Helpers.database import DB
from Helpers.functions import getPlayerDatav3, getPlayerUUID
from Helpers.guild_leave import (
    CLEAR_LEAVE_SQL,
    classify_pending_leave,
    clear_stale_pending_leaves,
    fetch_pending_leaves,
)
from Helpers.app_transcript import (
    classify_transcript_candidate,
    post_transcript,
    stamp_transcribed,
    delete_transcribed_channel,
    TranscriptError,
)
from Helpers.variables import APP_MANAGER_ROLE_MENTION, TAQ_GUILD_ID, CLOSED_CATEGORY_NAME, is_home_guild


class CheckApps(commands.Cog):
    def __init__(self, client):
        self.client = client

    # --- Guild leave monitoring for accepted applicants ---

    @tasks.loop(minutes=3)
    async def check_guild_leave(self):
        """Monitor accepted guild applications where the player needs to leave their current guild."""
        db = DB()
        db.connect()
        # A flag left on an applicant who already joined would fire the
        # "left their guild" ping the day they leave TAq (TAQ-81).
        stale = clear_stale_pending_leaves(db.cursor)
        if stale:
            db.connection.commit()
            for stale_id, stale_ign in stale:
                log(INFO, f"Cleared stale guild_leave_pending on app {stale_id} ({stale_ign}): already joined.", context="check_apps")
        rows = fetch_pending_leaves(db.cursor)
        db.close()

        if not rows:
            return

        for app_id, channel_id, thread_id, discord_id, ign in rows:
            try:
                # Security guard: validate thread belongs to home guild before processing
                if thread_id:
                    thread = self.client.get_channel(thread_id)
                    if thread and hasattr(thread, 'guild') and thread.guild:
                        if not is_home_guild(thread.guild.id):
                            log(ERROR, f"Skipping non-home guild thread {thread_id} for app {app_id}", context="check_apps")
                            continue

                await self._check_pending_leave(app_id, channel_id, thread_id, ign, discord_id)
            except Exception as e:
                log(ERROR, f"Error for app {app_id}: {e}", context="check_apps")

    async def _check_pending_leave(self, app_id, channel_id, thread_id, ign, discord_id):
        """Check if an applicant with pending guild leave has left their guild."""
        if not ign:
            return

        # Get UUID: try discord_links first, then Mojang lookup
        uuid = None
        if discord_id:
            db = DB()
            db.connect()
            db.cursor.execute(
                "SELECT uuid FROM discord_links WHERE discord_id = %s",
                (int(discord_id),)
            )
            link_row = db.cursor.fetchone()
            db.close()
            if link_row and link_row[0]:
                uuid = str(link_row[0])

        if not uuid:
            uuid_data = await asyncio.to_thread(getPlayerUUID, ign)
            uuid = uuid_data[1] if uuid_data else None

        if not uuid:
            return

        player_data = await asyncio.to_thread(getPlayerDatav3, uuid)
        outcome = classify_pending_leave(player_data)
        if outcome in ("unknown", "waiting"):
            return

        # Player has left their guild, or is already in TAq (left and joined
        # between two polls, or registered by hand) -- either way, stop watching.
        db = DB()
        db.connect()
        db.cursor.execute(CLEAR_LEAVE_SQL, (app_id,))
        db.connection.commit()
        db.close()

        if outcome == "joined":
            log(INFO, f"{ign} is already in TAq; cleared guild_leave_pending on app {app_id}.", context="check_apps")
            return

        if thread_id:
            thread = self.client.get_channel(thread_id)
            if thread is None:
                try:
                    thread = await self.client.fetch_channel(thread_id)
                except Exception:
                    thread = None

            if thread:
                if getattr(thread, "archived", False):
                    await thread.edit(archived=False)
                await thread.send(
                    f"{APP_MANAGER_ROLE_MENTION} **{discord.utils.escape_markdown(ign)}** has left their guild! "
                    f"They can now be invited.\n"
                    f"Run `/app invited` in the ticket channel or this thread to send them the invite message."
                )

        log(INFO, f"{ign} has left their guild. Notified exec thread.", context="check_apps")

    @check_guild_leave.before_loop
    async def before_check_guild_leave(self):
        await self.client.wait_until_ready()

    # --- Auto-close for applications ---

    @tasks.loop(minutes=5)
    async def auto_close_web_apps(self):
        """Auto-close denied apps after 24h and accepted apps when user has roles.
        Guild restriction: operates exclusively on TAQ_GUILD_ID (home guild)."""
        guild = self.client.get_guild(TAQ_GUILD_ID)
        if not guild:
            return

        closed_cat = discord.utils.get(guild.categories, name=CLOSED_CATEGORY_NAME)
        if not closed_cat:
            return

        # --- Denied apps: 24 hours after review ---
        denied_rows = await asyncio.to_thread(self._fetch_auto_close_denied)
        for app_id, channel_id, discord_id in denied_rows:
            try:
                await self._auto_close_channel(guild, closed_cat, channel_id, discord_id,
                                               "This application has been automatically closed.")
            except Exception as e:
                log(ERROR, f"Error closing denied app {app_id}: {e}", context="check_apps")

        # --- Accepted apps: user is linked in discord_links (joined + processed) + 1h after review ---
        accepted_guild_rows = await asyncio.to_thread(self._fetch_auto_close_accepted, "guild")
        for app_id, channel_id, discord_id in accepted_guild_rows:
            try:
                await self._auto_close_channel(
                    guild, closed_cat, channel_id, discord_id,
                    "This application has been automatically closed."
                )
            except Exception as e:
                log(ERROR, f"Error closing accepted guild app {app_id}: {e}", context="check_apps")

        accepted_community_rows = await asyncio.to_thread(self._fetch_auto_close_accepted, "community")
        for app_id, channel_id, discord_id in accepted_community_rows:
            try:
                await self._auto_close_channel(
                    guild, closed_cat, channel_id, discord_id,
                    "This application has been automatically closed."
                )
            except Exception as e:
                log(ERROR, f"Error closing accepted community app {app_id}: {e}", context="check_apps")


    async def _auto_close_channel(self, guild, closed_cat, channel_id, discord_id, message):
        """Move an app channel to the closed category (triggers on_guild_channel_update for rename + poll)."""
        channel = self.client.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.client.fetch_channel(channel_id)
            except Exception:
                return

        # Skip if already in closed category
        if getattr(channel, "category", None) == closed_cat:
            return

        # Revoke applicant's access to the channel
        if discord_id:
            member = guild.get_member(int(discord_id))
            if member is None:
                try:
                    member = await guild.fetch_member(int(discord_id))
                except Exception:
                    member = None
            if member:
                try:
                    await channel.set_permissions(member, overwrite=None)
                except discord.Forbidden:
                    pass

        await channel.send(message)
        await channel.edit(category=closed_cat)

    @staticmethod
    def _fetch_auto_close_denied():
        """Fetch denied apps older than 24 hours that aren't closed yet."""
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                """SELECT id, channel_id, discord_id FROM applications
                   WHERE status = 'denied'
                     AND poll_status != ':red_circle: Closed'
                     AND reviewed_at IS NOT NULL
                     AND reviewed_at + interval '24 hours' < NOW()
                     AND channel_id IS NOT NULL AND channel_id > 0"""
            )
            return db.cursor.fetchall()
        finally:
            db.close()

    @staticmethod
    def _fetch_auto_close_accepted(app_type):
        """Fetch accepted apps where the user is linked (joined + processed) and 1h+ since review."""
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                """SELECT a.id, a.channel_id, a.discord_id FROM applications a
                   JOIN discord_links dl ON dl.discord_id = CAST(a.discord_id AS BIGINT)
                   WHERE a.status = 'accepted'
                     AND a.application_type = %s
                     AND a.poll_status != ':red_circle: Closed'
                     AND a.reviewed_at IS NOT NULL
                     AND a.reviewed_at + interval '1 hour' < NOW()
                     AND a.channel_id IS NOT NULL AND a.channel_id > 0
                     AND dl.linked = TRUE""",
                (app_type,)
            )
            return db.cursor.fetchall()
        finally:
            db.close()

    @auto_close_web_apps.before_loop
    async def before_auto_close_web_apps(self):
        await self.client.wait_until_ready()

    # --- Auto-transcribe for closed applications ---

    @staticmethod
    def _fetch_transcript_head():
        """Return the lowest-numbered un-transcribed guild/community ticket, or None.

        This single row is the head of line: a lower un-transcribed ticket always
        gates higher ones (strict order)."""
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                """SELECT id, app_number, application_type, discord_id, discord_username,
                          status, answers, poll_status, channel_id, closed_at,
                          COALESCE(closed_at, reviewed_at) AS effective_closed_at
                     FROM applications
                    WHERE application_type IN ('guild', 'community')
                      AND app_number IS NOT NULL
                      AND transcribed_at IS NULL
                    ORDER BY app_number ASC
                    LIMIT 1"""
            )
            row = db.cursor.fetchone()
        finally:
            db.close()

        if not row:
            return None

        (app_id, app_number, application_type, discord_id, discord_username,
         status, answers, poll_status, channel_id, closed_at, effective_closed_at) = row
        if isinstance(answers, str):
            answers = json.loads(answers)
        return {
            "id": app_id,
            "app_number": app_number,
            "application_type": application_type,
            "discord_id": discord_id,
            "discord_username": discord_username,
            "status": status,
            "answers": answers or {},
            "poll_status": poll_status,
            "channel_id": channel_id,
            "closed_at": closed_at,
            "effective_closed_at": effective_closed_at,
        }

    # Safety bound: max tickets to transcribe in a single tick. The backlog is at
    # most a few dozen; this only guards against a logic bug looping forever.
    AUTO_TRANSCRIBE_MAX_PER_TICK = 100

    @tasks.loop(minutes=5)
    async def auto_transcribe_apps(self):
        """Auto-transcribe closed guild/community apps to the archive channel, then
        delete the transcribed channels. Guild restriction: operates exclusively on
        TAQ_GUILD_ID (home guild)."""
        guild = self.client.get_guild(TAQ_GUILD_ID)
        if not guild:
            return

        await self._drain_transcripts()
        await self._delete_transcribed_channels()

    async def _drain_transcripts(self):
        """Transcribe every currently-eligible ticket this tick, strictly in
        app_number order (sequential sends preserve order); stop at the first ticket
        that is not yet ready."""
        for _ in range(self.AUTO_TRANSCRIBE_MAX_PER_TICK):
            head = await asyncio.to_thread(self._fetch_transcript_head)
            decision = classify_transcript_candidate(head, datetime.now(timezone.utc))

            if decision in ("none", "wait"):
                return  # nothing left, or the next ticket in order isn't ready — stop.

            if decision == "skip":
                await asyncio.to_thread(stamp_transcribed, head["id"])
                log(INFO, f"Skipped app {head['id']} (#{head['app_number']}): no channel to transcribe.",
                    context="check_apps")
                continue

            # decision == "transcribe": resolve the channel; a deleted channel becomes a skip.
            channel = self.client.get_channel(head["channel_id"])
            if channel is None:
                try:
                    channel = await self.client.fetch_channel(head["channel_id"])
                except Exception:
                    await asyncio.to_thread(stamp_transcribed, head["id"])
                    log(INFO, f"Skipped app {head['id']} (#{head['app_number']}): channel "
                              f"{head['channel_id']} unresolvable.", context="check_apps")
                    continue

            app = {
                "id": head["id"],
                "application_type": head["application_type"],
                "discord_id": head["discord_id"],
                "discord_username": head["discord_username"] or str(head["id"]),
                "status": head["status"],
                "answers": head["answers"],
            }

            try:
                await post_transcript(self.client, channel, app)
            except TranscriptError as e:
                if e.retryable:
                    # Transient/config problem (e.g. archive channel missing). Stop the drain
                    # without advancing so we retry this same ticket next tick — never skip ahead.
                    log(ERROR, f"Transcript for app {head['id']} (#{head['app_number']}) failed, "
                               f"will retry: {e}", context="check_apps")
                    return
                log(INFO, f"App {head['id']} (#{head['app_number']}): {e} Marking transcribed.",
                    context="check_apps")
                await asyncio.to_thread(stamp_transcribed, head["id"])
                continue

            await asyncio.to_thread(stamp_transcribed, head["id"])
            log(INFO, f"Auto-transcribed app {head['id']} (#{head['app_number']}).", context="check_apps")
        else:
            log(INFO, f"_drain_transcripts hit the per-tick cap "
                      f"({self.AUTO_TRANSCRIBE_MAX_PER_TICK}); remaining tickets continue next tick.",
                context="check_apps")

    async def _delete_transcribed_channels(self):
        """Delete channels of already-transcribed guild/community tickets and mark
        them (channel_deleted_at), so transcribed applications don't linger in Closed
        Applications. Covers both the historical backlog and tickets just transcribed
        this tick. Idempotent — an already-gone channel is simply marked."""
        rows = await asyncio.to_thread(self._fetch_channels_to_delete)
        for app_id, app_number, channel_id in rows:
            try:
                await delete_transcribed_channel(self.client, app_id, channel_id)
                log(INFO, f"Deleted transcribed channel for app {app_id} (#{app_number}).",
                    context="check_apps")
            except Exception as e:
                # Transient failure (e.g. missing perms) — leave channel_deleted_at NULL so
                # this ticket is retried on a later tick.
                log(ERROR, f"Could not delete channel for app {app_id} (#{app_number}): {e}",
                    context="check_apps")

    @staticmethod
    def _fetch_channels_to_delete():
        """Transcribed guild/community tickets whose channel hasn't been deleted yet."""
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                """SELECT id, app_number, channel_id FROM applications
                    WHERE application_type IN ('guild', 'community')
                      AND transcribed_at IS NOT NULL
                      AND channel_deleted_at IS NULL
                      AND channel_id IS NOT NULL
                    ORDER BY app_number ASC
                    LIMIT %s""",
                (CheckApps.AUTO_TRANSCRIBE_MAX_PER_TICK,)
            )
            return db.cursor.fetchall()
        finally:
            db.close()

    @auto_transcribe_apps.before_loop
    async def before_auto_transcribe_apps(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.check_guild_leave.is_running():
            self.check_guild_leave.start()
        if not self.auto_close_web_apps.is_running():
            self.auto_close_web_apps.start()
        if not self.auto_transcribe_apps.is_running():
            self.auto_transcribe_apps.start()


def setup(client):
    client.add_cog(CheckApps(client))
