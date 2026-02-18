import re

import discord
from discord.ext import commands

from Helpers.database import DB
from Helpers.embed_updater import update_poll_embed, update_web_poll_embed
from Helpers.variables import CLOSED_CATEGORY_NAME


class OnGuildChannelUpdate(commands.Cog):
    def __init__(self, client: discord.Client):
        self.client = client

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.TextChannel, after: discord.TextChannel):
        name_changed = before.name != after.name
        before_cat = getattr(before, 'category', None)
        after_cat = getattr(after, 'category', None)
        moved_to_closed = (
            after_cat and after_cat.name == CLOSED_CATEGORY_NAME
            and (not before_cat or before_cat.name != CLOSED_CATEGORY_NAME)
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

                if not decision:
                    new_name = None
                elif app_type == "guild_member":
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
            else:
                # Check website applications table
                db = DB(); db.connect()
                db.cursor.execute(
                    "SELECT application_type, status, discord_username FROM applications WHERE channel_id = %s",
                    (after.id,)
                )
                web_row = db.cursor.fetchone()
                db.close()

                if web_row:
                    app_type, status, username = web_row

                    if status == "accepted":
                        new_name = f"web-accepted-{username}" if app_type == "guild" else f"web-c-accepted-{username}"
                    elif status == "denied":
                        new_name = f"web-denied-{username}" if app_type == "guild" else f"web-c-denied-{username}"
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
