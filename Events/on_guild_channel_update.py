import json
import discord
from discord import Embed
from discord.ext import commands

from Helpers.database import DB
from Helpers.variables import member_app_channel

class OnGuildChannelUpdate(commands.Cog):
    def __init__(self, client):
        self.client = client

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.TextChannel, after: discord.TextChannel):
        db = DB()
        db.connect()
        db.cursor.execute(
            """
            SELECT channel, ticket, status
              FROM new_app
             WHERE channel = %s
            """,
            (before.id,)
        )
        row = db.cursor.fetchone()
        if not row:
            db.close()
            return

        exec_chan = self.client.get_channel(member_app_channel)
        if not exec_chan:
            print(f"🚨 Exec channel {member_app_channel} not found")
            db.close()
            return

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

        if new_status in (":hourglass: In Queue", ":hourglass: Invited"):
            colour = 0xFFE019
        elif new_status != ":green_circle: Opened":
            colour = 0xD93232
        else:
            colour = 0x3ED63E

        db.cursor.execute(
            "UPDATE new_app SET status = %s WHERE channel = %s",
            (new_status, before.id)
        )
        db.connection.commit()
        db.close()

        ticket_str = row[1].replace("ticket-", "")
        embed = Embed(
            title=f"Application {ticket_str}",
            description="Status updated — please review below:",
            colour=colour,
        )
        embed.add_field(name="Channel", value=f"<#{before.id}>", inline=True)
        embed.add_field(name="Status",  value=new_status, inline=True)

        await exec_chan.send(embed=embed)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(OnGuildChannelUpdate(client))
