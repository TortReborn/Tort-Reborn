import re

import discord
from discord.ext import commands

from Helpers.database import DB
from Helpers.embed_updater import update_poll_embed
from Helpers.variables import closed_category_name


class OnGuildChannelUpdate(commands.Cog):
    def __init__(self, client: discord.Client):
        self.client = client

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.TextChannel, after: discord.TextChannel):
        name_changed = before.name != after.name
        before_cat = getattr(before, 'category', None)
        after_cat = getattr(after, 'category', None)
        moved_to_closed = (
            after_cat and after_cat.name == closed_category_name
            and (not before_cat or before_cat.name != closed_category_name)
        )

        if not name_changed and not moved_to_closed:
            return

        # Detect ticket closure (Ticket Tool renames to "closed-XXXX")
        if name_changed and after.name.startswith("closed-") and not before.name.startswith("closed-"):
            await update_poll_embed(self.client, after.id, ":red_circle: Closed", 0xD93232)

        # When a ticket arrives in Closed Applications, rename based on app type and decision
        if moved_to_closed:
            db = DB(); db.connect()
            db.cursor.execute(
                "SELECT app_type, decision FROM new_app WHERE channel = %s",
                (after.id,)
            )
            row = db.cursor.fetchone()
            db.close()

            if row:
                app_type, decision = row
                num_match = re.search(r'(\d+)', after.name)
                ticket_num = num_match.group(1) if num_match else after.name.split("-", 1)[-1]

                if app_type == "guild_member":
                    new_name = f"accepted-{ticket_num}" if decision == "accepted" else f"denied-{ticket_num}"
                elif app_type == "community_member":
                    new_name = f"c-accepted-{ticket_num}" if decision == "accepted" else f"c-denied-{ticket_num}"
                else:
                    new_name = None

                if new_name and new_name != after.name:
                    try:
                        await after.edit(name=new_name)
                    except Exception:
                        pass

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(OnGuildChannelUpdate(client))
