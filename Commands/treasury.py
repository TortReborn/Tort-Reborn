import discord
from discord.ext import commands, pages
from discord.commands import slash_command
import datetime
import math

from Helpers.database import get_territory_data
from Helpers.variables import ALL_GUILD_IDS


class Treasury(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(description='Display all territories ordered by time held', guild_ids=ALL_GUILD_IDS)
    async def treasury(self, ctx: discord.ApplicationContext):
        await ctx.defer()

        try:
            # 1. Load territory data
            all_territories = get_territory_data()

            if not all_territories:
                embed = discord.Embed(
                    title='No Territories Found',
                    description='No territories currently held.',
                    color=discord.Color.red()
                )
                await ctx.followup.send(embed=embed)
                return

            # 2. Calculate time held for each territory
            now = datetime.datetime.now(datetime.timezone.utc)
            territories_with_time = []

            for terr_name, terr_data in all_territories.items():
                acquired_str = terr_data['acquired']
                acquired_dt = datetime.datetime.fromisoformat(acquired_str.rstrip('Z'))
                acquired_dt = acquired_dt.replace(tzinfo=datetime.timezone.utc)

                time_held = now - acquired_dt

                # Calculate formatted time string
                days = time_held.days
                hours, remainder = divmod(time_held.seconds, 3600)
                minutes, _ = divmod(remainder, 60)
                time_str = f"{days}d {hours}h {minutes}m"

                territories_with_time.append({
                    'name': terr_name,
                    'prefix': terr_data['guild']['prefix'],
                    'time_held_seconds': time_held.total_seconds(),
                    'time_str': time_str,
                    'acquired': acquired_dt
                })

            # 3. Sort by time held (longest first)
            territories_with_time.sort(key=lambda x: x['time_held_seconds'], reverse=True)

            # 4. Create paginated embeds (10 territories per page)
            territories_per_page = 10
            page_count = math.ceil(len(territories_with_time) / territories_per_page)
            book = []

            for page_num in range(page_count):
                start_idx = page_num * territories_per_page
                end_idx = min(start_idx + territories_per_page, len(territories_with_time))
                page_territories = territories_with_time[start_idx:end_idx]

                # Create formatted description with better styling
                description = '```\n'
                description += ' #   │ Guild │ Territory Name           │ Time Held\n'
                description += '═════╪═══════╪══════════════════════════╪═══════════════\n'

                for idx, terr in enumerate(page_territories, start=start_idx + 1):
                    # Format with rank number, guild prefix, territory name, and time held
                    rank_display = f'{idx:3d}'
                    prefix_display = f'{terr["prefix"]:5s}'
                    name_display = f'{terr["name"][:24]:24s}'
                    time_display = terr["time_str"]

                    description += f' {rank_display} │ {prefix_display} │ {name_display} │ {time_display}\n'

                description += '```'

                # Create embed with title and better formatting
                embed = discord.Embed(
                    title='Territory Treasury',
                    description=description,
                    color=0x5865F2  # Discord blurple
                )
                book.append(embed)

            # 5. Create paginator with navigation buttons
            paginator = pages.Paginator(pages=book)
            paginator.add_button(
                pages.PaginatorButton("first", emoji="<:first_arrows:1198703152204103760>",
                                      style=discord.ButtonStyle.blurple)
            )
            paginator.add_button(
                pages.PaginatorButton("prev", emoji="<:left_arrow:1198703157501509682>",
                                      style=discord.ButtonStyle.blurple)
            )
            paginator.add_button(
                pages.PaginatorButton("next", emoji="<:right_arrow:1198703156088021112>",
                                      style=discord.ButtonStyle.blurple)
            )
            paginator.add_button(
                pages.PaginatorButton("last", emoji="<:last_arrows:1198703153726627880>",
                                      style=discord.ButtonStyle.blurple)
            )

            await paginator.respond(ctx.interaction)

        except Exception as e:
            error_embed = discord.Embed(
                title='Error',
                description=f'An error occurred: {str(e)}',
                color=discord.Color.red()
            )
            await ctx.followup.send(embed=error_embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Treasury(client))
