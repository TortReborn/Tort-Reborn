import asyncio

import discord
from discord.ext import tasks, commands

from Helpers.database import DB
from Helpers.logger import log, ERROR, INFO
from Helpers.poll_edit import safe_edit_poll
from Helpers.variables import MEMBER_APP_CHANNEL_ID


class SyncVoteCounts(commands.Cog):
    """Background task that syncs website vote counts to Discord poll embeds."""

    def __init__(self, client):
        self.client = client

    @tasks.loop(minutes=2)
    async def sync_votes(self):
        """Poll the database for pending applications and update their Discord embeds.
        Guild restriction: operates on home guild channel only (MEMBER_APP_CHANNEL_ID)."""
        rows = await asyncio.to_thread(self._fetch_pending_polls)
        if not rows:
            return

        exec_chan = self.client.get_channel(MEMBER_APP_CHANNEL_ID)
        if not exec_chan:
            return

        for app_id, poll_message_id in rows:
            try:
                counts = await asyncio.to_thread(self._get_vote_counts, app_id)
                vote_text = f"Accept: {counts['accept']} | Deny: {counts['deny']} | Abstain: {counts['abstain']}"

                # Skip if no votes to display
                if counts["accept"] + counts["deny"] + counts["abstain"] == 0:
                    continue

                # Quick check: skip if the embed already has the correct vote text
                try:
                    poll_msg = await exec_chan.fetch_message(poll_message_id)
                except discord.NotFound:
                    continue
                except Exception:
                    continue

                if poll_msg.embeds:
                    already_current = False
                    for field in poll_msg.embeds[0].fields:
                        if field.name == "Votes" and field.value == vote_text:
                            already_current = True
                            break
                    if already_current:
                        continue

                # Use factory to avoid closure variable capture bug
                def _make_modifier(vt):
                    def _modify(embed):
                        found = False
                        for i, field in enumerate(embed.fields):
                            if field.name == "Votes":
                                embed.set_field_at(i, name="Votes", value=vt, inline=False)
                                found = True
                                break
                        if not found:
                            embed.add_field(name="Votes", value=vt, inline=False)
                    return _modify

                await safe_edit_poll(
                    exec_chan, poll_message_id,
                    modify_embed=_make_modifier(vote_text),
                )

            except Exception as e:
                log(ERROR, f"Error syncing votes for app {app_id}: {e}", context="sync_vote_counts")

    @staticmethod
    def _fetch_pending_polls():
        """Blocking: get all pending applications with poll messages."""
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                """SELECT id, poll_message_id FROM applications
                   WHERE status = 'pending' AND poll_message_id IS NOT NULL"""
            )
            return db.cursor.fetchall()
        finally:
            db.close()

    @staticmethod
    def _get_vote_counts(application_id: int) -> dict:
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                """SELECT
                    COUNT(*) FILTER (WHERE vote = 'accept') as accept_count,
                    COUNT(*) FILTER (WHERE vote = 'deny') as deny_count,
                    COUNT(*) FILTER (WHERE vote = 'abstain') as abstain_count
                FROM application_votes
                WHERE application_id = %s""",
                (application_id,)
            )
            row = db.cursor.fetchone()
            if row:
                return {"accept": row[0], "deny": row[1], "abstain": row[2]}
            return {"accept": 0, "deny": 0, "abstain": 0}
        finally:
            db.close()

    @sync_votes.before_loop
    async def before_sync_votes(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.sync_votes.is_running():
            self.sync_votes.start()


def setup(client):
    client.add_cog(SyncVoteCounts(client))
