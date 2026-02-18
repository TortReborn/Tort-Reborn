import asyncio
import re

import discord
from discord.ext import commands

from Helpers.database import DB
from Helpers.functions import getNameFromUUID
from Helpers.logger import log, ERROR
from Helpers.openai_helper import (
    extract_ign,
    validate_application_completeness,
    validate_exmember_completeness,
)


class OnMessageEdit(commands.Cog):
    def __init__(self, client):
        self.client = client

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        """Handle message edits in application tickets for re-validation."""
        # Only process if message content changed
        if "content" not in payload.data:
            return

        channel_id = int(payload.data.get("channel_id", 0))
        message_id = payload.message_id

        # Check if this channel has an incomplete application with this message ID
        db = DB(); db.connect()
        db.cursor.execute(
            """SELECT applicant_discord_id, app_type, thread_id, app_complete, app_message_id
               FROM new_app WHERE channel = %s""",
            (channel_id,)
        )
        row = db.cursor.fetchone()
        db.close()

        if not row:
            return

        stored_discord_id, app_type, thread_id, app_complete, app_message_id = row

        # Only care about edits to the tracked application message
        if app_message_id != message_id:
            return

        # Skip if already complete and forwarded
        if app_complete:
            return

        # Must have an app_type detected (otherwise we haven't identified it yet)
        if app_type is None:
            return

        # Skip community member apps (no strict validation)
        if app_type == "community_member":
            return

        # Verify the author is the ticket opener
        author_id = int(payload.data.get("author", {}).get("id", 0))
        if author_id != stored_discord_id:
            return

        new_content = payload.data["content"]

        # Determine ex-member status
        is_ex_member = False
        mc_name = ""

        db = DB(); db.connect()
        db.cursor.execute(
            "SELECT uuid FROM discord_links WHERE discord_id = %s",
            (stored_discord_id,)
        )
        link_row = db.cursor.fetchone()
        db.close()

        if link_row and link_row[0]:
            is_ex_member = True
            ex_member_uuid = str(link_row[0])
            resolved = await asyncio.to_thread(getNameFromUUID, ex_member_uuid)
            mc_name = resolved[0] if resolved else ""
        else:
            # Check roles — need to fetch the member
            channel = self.client.get_channel(channel_id)
            if channel:
                try:
                    member = await channel.guild.fetch_member(stored_discord_id)
                    ex_member_role_names = {'Ex-Member', 'Honored Fish', 'Retired Chief'}
                    member_role_names = {r.name for r in member.roles}
                    if ex_member_role_names & member_role_names:
                        is_ex_member = True
                except Exception:
                    pass

        # Validate
        validator = validate_exmember_completeness if is_ex_member else validate_application_completeness
        validation = await asyncio.to_thread(validator, new_content)

        if validation.get("error"):
            log(ERROR, f"Validation error for channel {channel_id}: {validation['error']}", context="on_message_edit")
            return

        # Extract IGN from updated content
        if not mc_name:
            mc_name = self._extract_ign_from_text_sync(new_content)
            if not mc_name:
                ign_result = await asyncio.to_thread(extract_ign, new_content)
                conf_threshold = 0.5 if is_ex_member else 0.7
                if not ign_result.get("error") and ign_result.get("confidence", 0) >= conf_threshold:
                    mc_name = ign_result["ign"]

        if not validation["complete"]:
            # Still incomplete — update IGN if we found one
            if mc_name:
                db = DB(); db.connect()
                db.cursor.execute(
                    "UPDATE new_app SET ign = %s WHERE channel = %s",
                    (mc_name, channel_id)
                )
                db.connection.commit()
                db.close()
            return

        # Application is now complete! Fetch the full message and forward.
        channel = self.client.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.client.fetch_channel(channel_id)
            except Exception:
                return

        try:
            message = await channel.fetch_message(message_id)
        except Exception:
            return

        # Delegate to the OnMessage cog's processing method
        on_message_cog = self.client.get_cog("OnMessage")
        if on_message_cog:
            await on_message_cog._process_detected_application(
                message, app_type, mc_name, thread_id
            )

    def _extract_ign_from_text_sync(self, text) -> str:
        """Synchronous IGN extraction from stats link (no UUID resolution)."""
        stats_match = re.search(
            r"wynncraft\.com/stats/player[/\s]*"
            r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[\w]+)",
            text
        )
        if stats_match:
            captured = stats_match.group(1)
            if not re.fullmatch(r'[0-9a-fA-F\-]{36}', captured):
                return captured
        return ""

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(OnMessageEdit(client))
