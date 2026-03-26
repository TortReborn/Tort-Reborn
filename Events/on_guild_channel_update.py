import json

import discord
from discord.ext import commands

from Helpers.database import DB
from Helpers.embed_updater import update_web_poll_embed
from Helpers.variables import CLOSED_CATEGORY_NAME, is_home_guild


class OnGuildChannelUpdate(commands.Cog):
    def __init__(self, client: discord.Client):
        self.client = client

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.TextChannel, after: discord.TextChannel):
        # Ignore channel updates from external (non-home) guilds
        if not is_home_guild(after.guild.id):
            return

        before_cat = getattr(before, 'category', None)
        after_cat = getattr(after, 'category', None)
        moved_to_closed = (
            after_cat and after_cat.name == CLOSED_CATEGORY_NAME
            and (not before_cat or before_cat.name != CLOSED_CATEGORY_NAME)
        )

        if not moved_to_closed:
            return

        # When a ticket arrives in Closed Applications, rename based on app type and decision
        db = DB(); db.connect()
        db.cursor.execute(
            "SELECT application_type, status, id, answers, app_number FROM applications WHERE channel_id = %s",
            (after.id,)
        )
        row = db.cursor.fetchone()
        db.close()

        if not row:
            return

        app_type, status, app_id, answers, app_number = row
        if isinstance(answers, str):
            answers = json.loads(answers)
        ign = (answers.get("ign") or "").strip()
        display_number = app_number if app_number is not None else app_id

        if status == "accepted":
            new_name = f"accepted-{display_number}-{ign}" if app_type == "guild" else f"c-accepted-{display_number}-{ign}"
        elif status == "denied":
            new_name = f"denied-{display_number}-{ign}" if app_type == "guild" else f"c-denied-{display_number}-{ign}"
        else:
            new_name = None

        if new_name and new_name != after.name:
            try:
                await after.edit(name=new_name)
            except Exception:
                pass

        await update_web_poll_embed(self.client, after.id, ":red_circle: Closed", 0xD93232)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(OnGuildChannelUpdate(client))
