import json
import discord
from discord import Embed
from discord.ext import commands

from Helpers.database import DB
# from Helpers.variables import member_app_channel  # <- no longer needed

class OnGuildChannelUpdate(commands.Cog):
    def __init__(self, client):
        self.client = client

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.TextChannel, after: discord.TextChannel):
        db = DB()
        db.connect()
        db.cursor.execute(
            """
            SELECT channel, ticket, status, thread_id
              FROM new_app
             WHERE channel = %s
            """,
            (before.id,)
        )
        row = db.cursor.fetchone()
        if not row:
            db.close()
            return

        # Unpack DB fields we need
        _, ticket, _, thread_id = row

        # Determine new status
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

        # Pick colour
        if new_status in (":hourglass: In Queue", ":hourglass: Invited"):
            colour = 0xFFE019
        elif new_status != ":green_circle: Opened":
            colour = 0xD93232
        else:
            colour = 0x3ED63E

        # Update DB
        db.cursor.execute(
            "UPDATE new_app SET status = %s WHERE channel = %s",
            (new_status, before.id)
        )
        db.connection.commit()
        db.close()

        # Prepare embed
        ticket_str = ticket.replace("ticket-", "") if ticket else "?"
        embed = Embed(
            title=f"Application {ticket_str}",
            description="Status updated — please review below:",
            colour=colour,
        )
        embed.add_field(name="Channel", value=f"<#{before.id}>", inline=True)
        embed.add_field(name="Status",  value=new_status, inline=True)

        # Send to the associated thread (not the general/exec channel)
        if not thread_id:
            print("🚨 No thread_id stored for this application; cannot post update.")
            return

        thread = self.client.get_channel(thread_id)  # Threads are Channels in discord.py
        if thread is None:
            # Try fetching if not cached
            try:
                thread = await self.client.fetch_channel(thread_id)
            except Exception as e:
                print(f"🚨 Could not fetch thread {thread_id}: {e}")
                return

        # Ensure it's a thread and unarchive if necessary
        if isinstance(thread, discord.Thread):
            try:
                if getattr(thread, "archived", False):
                    await thread.edit(archived=False)
                await thread.send(embed=embed)
            except Exception as e:
                print(f"🚨 Failed to send update to thread {thread_id}: {e}")
        else:
            print(f"🚨 Channel {thread_id} is not a thread; got {type(thread)}")

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(OnGuildChannelUpdate(client))
