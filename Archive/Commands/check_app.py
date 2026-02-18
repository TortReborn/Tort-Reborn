import time

from discord import Embed
from discord.ext import commands
from discord.commands import slash_command

from Helpers.database import DB
from Helpers.variables import EXEC_GUILD_IDS


class CheckApp(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(guild_ids=EXEC_GUILD_IDS)
    async def check_app(self, message, ticket_number: int):
        db = DB()
        db.connect()

        db.cursor.execute(f'SELECT * FROM new_app WHERE ticket = \'ticket-{ticket_number}\'')
        rows = db.cursor.fetchall()

        if len(rows) != 0:
            app_creation = time.mktime(rows[0][5].timetuple())
            remind_in = app_creation + (3600 * 8)
            current_time = time.time()

            embed = Embed(title=f"Ticket {ticket_number}")
            embed.add_field(name="Status", value=f"{rows[0][4]}", inline=False)
            embed.add_field(name="App Creation", value=f"<t:{int(app_creation)}:f>\n(<t:{int(app_creation)}:R>)")
            embed.add_field(name="Reminder", value=f"<t:{int(remind_in)}:f>\n(<t:{int(remind_in)}:R>)")
            embed.add_field(name="Current time", value=f"<t:{int(current_time)}:f>")

            await message.respond(embed=embed)
        else:
            await message.respond("Ticket not found")
            return

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(CheckApp(client))
