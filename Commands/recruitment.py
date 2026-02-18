import discord
from discord.ext import commands
from discord.commands import slash_command
from Helpers.database import get_recruitment_data
from Helpers.variables import EXEC_GUILD_IDS
from datetime import datetime
import csv
import io


class Recruitment(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(
        guild_ids=EXEC_GUILD_IDS,
        description='ADMIN: Get a list of guildless players for recruitment',
        default_member_permissions=discord.Permissions(administrator=True)
    )
    async def recruitment(
        self,
        ctx: discord.ApplicationContext,
        order_by: discord.Option(
            str,
            description='How to sort the results',
            choices=['Wars', 'First Join', 'Playtime', 'Raids', 'Max Level'],
            default='Playtime',
            required=False
        ),
        min_level: discord.Option(
            int,
            description='Minimum character level',
            default=None,
            required=False
        ),
        min_playtime: discord.Option(
            float,
            description='Minimum playtime in hours',
            default=None,
            required=False
        ),
        server: discord.Option(
            str,
            description='Filter by server region',
            choices=['All', 'NA', 'EU', 'AS'],
            default='All',
            required=False
        )
    ):
        await ctx.defer(ephemeral=True)

        # Load cached data
        data = get_recruitment_data()
        if not data:
            await ctx.followup.send(
                "No recruitment data available yet. The background scanner is still collecting data. "
                "Please try again in a few minutes.",
                ephemeral=True
            )
            return

        candidates = data.get('candidates', [])
        last_updated = data.get('last_updated', 'Unknown')

        # Apply filters
        if server and server != 'All':
            candidates = [c for c in candidates if c.get('server') == server]

        if min_level is not None:
            candidates = [c for c in candidates if c.get('max_level', 0) >= min_level]

        if min_playtime is not None:
            candidates = [c for c in candidates if c.get('playtime', 0) >= min_playtime]

        # Sort by selected field
        order_map = {
            'Wars': 'wars',
            'First Join': 'first_join',
            'Playtime': 'playtime',
            'Raids': 'raids',
            'Max Level': 'max_level'
        }
        sort_key = order_map.get(order_by, 'playtime')
        reverse_sort = sort_key != 'first_join'  # First join sorts ascending (oldest first)
        candidates.sort(key=lambda c: c.get(sort_key, 0), reverse=reverse_sort)

        if not candidates:
            await ctx.followup.send(
                f"No candidates found matching your filters.\n"
                f"Last scan: {last_updated}",
                ephemeral=True
            )
            return

        # Generate CSV
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(['Username', 'UUID', 'Server', 'Rank', 'Wars', 'First Join', 'Playtime (hrs)', 'Raids', 'Max Level'])

        for c in candidates:
            writer.writerow([
                c.get('username', ''),
                c.get('uuid', ''),
                c.get('server', ''),
                c.get('rank', ''),
                c.get('wars', 0),
                c.get('first_join', ''),
                c.get('playtime', 0),
                c.get('raids', 0),
                c.get('max_level', 0)
            ])

        csv_buffer.seek(0)
        csv_bytes = io.BytesIO(csv_buffer.getvalue().encode('utf-8'))

        # Build filter summary
        filters_applied = []
        if server and server != 'All':
            filters_applied.append(f"Server: {server}")
        if min_level is not None:
            filters_applied.append(f"Min Level: {min_level}")
        if min_playtime is not None:
            filters_applied.append(f"Min Playtime: {min_playtime}hrs")
        filter_text = ", ".join(filters_applied) if filters_applied else "None"

        await ctx.followup.send(
            f"**Recruitment Candidates**\n"
            f"Found **{len(candidates)}** guildless players\n"
            f"Sorted by: {order_by}\n"
            f"Filters: {filter_text}\n"
            f"Last scan: {last_updated}",
            file=discord.File(csv_bytes, filename=f'recruitment_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'),
            ephemeral=True
        )

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Recruitment(client))
