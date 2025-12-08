import discord
from discord.ext import commands
from discord.commands import slash_command
from Helpers.variables import guilds
from datetime import datetime
from dateutil import parser
import aiohttp
import asyncio
import csv
import io


class Recruitment(commands.Cog):
    def __init__(self, client):
        self.client = client
        # API Authentication (120 requests/minute vs 50 unauthenticated)
        self.API_TOKEN = "0SpWpvR4CFQ9NQI1hhth4F4zxPSbqwiW1aXgnrQvByU"
        self.REQUEST_DELAY = 0.55  # seconds between requests (safe margin under 120/min)

    @slash_command(
        guild_ids=guilds,
        description='Scan online players for recruitment candidates',
        default_member_permissions=discord.Permissions(administrator=True)
    )
    async def recruitment(
        self,
        ctx: discord.ApplicationContext,
        sort_by: discord.Option(
            str,
            description='How to sort the results',
            choices=['Total Playtime', 'Average Playtime'],
            default='Average Playtime',
            required=False
        )
    ):
        # Defer with ephemeral so only the user sees responses
        await ctx.defer(ephemeral=True)

        # Get initial player count to estimate time
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.wynncraft.com/v3/player') as resp:
                if resp.status != 200:
                    await ctx.followup.send("Failed to fetch online players. Please try again later.", ephemeral=True)
                    return
                worlds = await resp.json()

        total_players = worlds['total']

        # Calculate ETA (Steady mode: ~109 players/min)
        eta_min = total_players // 109 + 1

        await ctx.followup.send(
            f"**Recruitment scan started!**\n"
            f"Found **{total_players}** online players.\n"
            f"Estimated time: **~{eta_min} minute{'s' if eta_min != 1 else ''}**\n\n"
            f"I'll send you the results as a CSV file when complete.",
            ephemeral=True
        )

        # Run the scan in the background
        asyncio.create_task(self._run_scan(ctx, worlds, sort_by))

    async def _run_scan(self, ctx: discord.ApplicationContext, worlds: dict, sort_by: str):
        """Background task to scan all online players."""
        order_key = 'playtime' if sort_by == 'Total Playtime' else 'avg_playtime'
        candidates = []
        total_players = worlds['total']
        current_player = 0

        headers = {'Authorization': f'Bearer {self.API_TOKEN}'}

        async with aiohttp.ClientSession(headers=headers) as session:
            for player in worlds['players']:
                current_player += 1

                # Steady mode delay
                await asyncio.sleep(self.REQUEST_DELAY)

                successful = False
                retries = 0
                max_retries = 3
                player_identifier = player
                old_name = None
                mojang_username = None

                while not successful and retries < max_retries:
                    try:
                        async with session.get(f'https://api.wynncraft.com/v3/player/{player_identifier}') as response:
                            remaining = int(response.headers.get('ratelimit-remaining', 50))
                            reset_time = int(response.headers.get('ratelimit-reset', 60))

                            # Wait if running low on rate limit
                            if remaining <= 2:
                                await asyncio.sleep(reset_time + 1)

                            # Rate limited
                            if response.status == 429:
                                reset_time = int(response.headers.get('ratelimit-reset', 60))
                                await asyncio.sleep(reset_time + 1)
                                retries += 1
                                continue

                            # Handle 300 Multiple Choices (name change)
                            if response.status == 300:
                                data = await response.json()
                                uuid = None

                                if 'objects' in data and isinstance(data['objects'], dict):
                                    uuids = list(data['objects'].keys())
                                    if uuids:
                                        uuid = uuids[0]
                                elif isinstance(data, list) and len(data) > 0:
                                    uuid = data[0] if isinstance(data[0], str) else data[0].get('uuid', data[0].get('id'))
                                elif isinstance(data, dict):
                                    uuid = data.get('uuid') or data.get('id')

                                if uuid:
                                    # Get current username from Mojang
                                    new_username = await self._get_mojang_username(session, uuid)
                                    if new_username and new_username != player:
                                        old_name = player
                                        mojang_username = new_username
                                    player_identifier = uuid
                                    retries += 1
                                    continue
                                else:
                                    successful = True
                                    continue

                            # Other errors
                            if response.status != 200:
                                successful = True
                                continue

                            pdata = await response.json()
                            username = mojang_username if mojang_username else pdata.get('username', player)

                            if 'firstJoin' not in pdata:
                                successful = True
                                continue

                            first_join = parser.isoparse(pdata['firstJoin'])
                            member_for = datetime.now() - first_join.replace(tzinfo=None)

                            playtime = pdata.get('playtime', 0)

                            if member_for.days == 0:
                                avg_playtime = playtime
                            else:
                                avg_playtime = round(playtime / member_for.days, 2)

                            guild = ''
                            if pdata.get('guild'):
                                guild = pdata['guild'].get('name', '')

                            candidates.append({
                                'username': username,
                                'old_name': old_name,
                                'guild': guild,
                                'playtime': int(playtime),
                                'avg_playtime': avg_playtime,
                                'first_join': first_join.strftime('%Y-%m-%d')
                            })

                            successful = True

                    except Exception as e:
                        retries += 1
                        if retries < max_retries:
                            await asyncio.sleep(5 * retries)
                        else:
                            successful = True

        # Sort candidates
        candidates.sort(key=lambda p: p[order_key], reverse=True)

        # Create CSV file
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(['Username', 'Previous Name', 'Guild', 'Total Playtime (hrs)', 'Avg Playtime (hrs/day)', 'First Join'])

        for c in candidates:
            writer.writerow([
                c['username'],
                c['old_name'] or '',
                c['guild'],
                c['playtime'],
                c['avg_playtime'],
                c['first_join']
            ])

        csv_buffer.seek(0)
        csv_bytes = io.BytesIO(csv_buffer.getvalue().encode('utf-8'))

        # Send results
        sorted_by = "Total Playtime" if order_key == 'playtime' else "Average Playtime"
        try:
            await ctx.author.send(
                f"**Recruitment scan complete!**\n"
                f"Scanned **{total_players}** online players.\n"
                f"Found **{len(candidates)}** candidates (players with data).\n"
                f"Sorted by: {sorted_by}\n\n"
                f"Download the CSV file below:",
                file=discord.File(csv_bytes, filename=f'recruitment_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv')
            )
        except discord.Forbidden:
            # Can't DM user, try to follow up in channel (may fail if too much time passed)
            try:
                channel = ctx.channel
                await channel.send(
                    f"{ctx.author.mention} Your recruitment scan is complete! "
                    f"I couldn't DM you the results. Please enable DMs and run the command again.",
                    delete_after=30
                )
            except:
                pass

    async def _get_mojang_username(self, session: aiohttp.ClientSession, uuid: str) -> str | None:
        """Get current username from Mojang API using UUID."""
        try:
            clean_uuid = uuid.replace('-', '')
            async with session.get(f'https://sessionserver.mojang.com/session/minecraft/profile/{clean_uuid}') as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('name')
        except:
            pass
        return None

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Recruitment(client))
