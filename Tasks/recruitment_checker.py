import re
import os
import random
import asyncio
from datetime import datetime, timezone
from discord.ext import tasks, commands
import aiohttp

from Helpers.logger import log, INFO, WARN, ERROR
from Helpers.database import save_recruitment_data


class RecruitmentChecker(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.API_TOKEN = os.getenv('RECRUITMENT_TOKEN')
        self.MAX_REQUESTS_PER_MINUTE = 120
        self.SAFETY_MARGIN = 0.9  # Use 90% of rate limit to be safe
        self.LOOP_DURATION = 600  # 10 minutes in seconds
        self.CUTOFF_TIME = 585  # 9:45 in seconds - stop early if not done
        self.recruitment_loop.start()

    def cog_unload(self):
        self.recruitment_loop.cancel()

    def calculate_request_delay(self) -> float:
        """Calculate delay between requests to stay under rate limit.

        With 120 req/min limit and safety margin, we target ~108 req/min.
        That's 60/108 = ~0.56 seconds minimum between requests.
        """
        effective_limit = self.MAX_REQUESTS_PER_MINUTE * self.SAFETY_MARGIN
        min_delay = 60.0 / effective_limit  # ~0.56 seconds
        return max(min_delay, 0.5)  # Never go below 0.5s

    def extract_server_region(self, server_name: str) -> str:
        """Extract region prefix from server name (e.g., 'NA44' -> 'NA')"""
        if not server_name:
            return "??"
        match = re.match(r'^([A-Za-z]+)', server_name)
        if match:
            region = match.group(1).upper()
            if region in ('NA', 'EU', 'AS'):
                return region
        return "??"

    def get_max_character_level(self, characters: dict) -> int:
        """Get the highest level from all characters"""
        if not characters:
            return 0
        max_level = 0
        for char_uuid, char_data in characters.items():
            level = char_data.get('level', 0)
            if level > max_level:
                max_level = level
        return max_level

    @tasks.loop(minutes=10)
    async def recruitment_loop(self):
        """Background task that scans online players for guildless recruitment candidates"""
        try:
            start_time = datetime.now(timezone.utc)
            candidates = []
            total_scanned = 0

            headers = {'Authorization': f'Bearer {self.API_TOKEN}'}
            request_delay = self.calculate_request_delay()

            async with aiohttp.ClientSession(headers=headers) as session:
                # Fetch online players
                async with session.get('https://api.wynncraft.com/v3/player') as resp:
                    if resp.status != 200:
                        log(ERROR, f"Failed to fetch online players: {resp.status}", context="recruitment")
                        return
                    online_data = await resp.json()

                players = online_data.get('players', {})
                total_players = len(players)

                # Randomize player order for fairness
                player_list = list(players.items())
                random.shuffle(player_list)

                log(INFO, f"Found {total_players} online players", context="recruitment")

                for player_name, server_name in player_list:
                    # Check if we've hit the cutoff time (9:45)
                    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
                    if elapsed >= self.CUTOFF_TIME:
                        log(INFO, f"Reached time cutoff ({self.CUTOFF_TIME}s), ending scan early", context="recruitment")
                        break

                    total_scanned += 1

                    retries = 0
                    max_retries = 3

                    while retries < max_retries:
                        try:
                            async with session.get(
                                f'https://api.wynncraft.com/v3/player/{player_name}?fullResult'
                            ) as response:
                                # Handle rate limiting
                                remaining = int(response.headers.get('ratelimit-remaining', 50))
                                reset_time = int(response.headers.get('ratelimit-reset', 60))

                                if remaining <= 2:
                                    log(WARN, f"Rate limit low, waiting {reset_time}s", context="recruitment")
                                    await asyncio.sleep(reset_time + 1)

                                if response.status == 429:
                                    reset_time = int(response.headers.get('ratelimit-reset', 60))
                                    log(WARN, f"Rate limited, waiting {reset_time}s", context="recruitment")
                                    await asyncio.sleep(reset_time + 1)
                                    retries += 1
                                    continue

                                if response.status != 200:
                                    break

                                pdata = await response.json()

                                # Skip players in a guild
                                if pdata.get('guild'):
                                    break

                                # Extract candidate data
                                first_join_raw = pdata.get('firstJoin', '')
                                first_join = first_join_raw[:10] if first_join_raw else ''  # Extract YYYY-MM-DD

                                candidate = {
                                    'username': pdata.get('username', player_name),
                                    'uuid': pdata.get('uuid', ''),
                                    'server': self.extract_server_region(server_name),
                                    'rank': pdata.get('supportRank', ''),
                                    'wars': pdata.get('globalData', {}).get('wars', 0),
                                    'first_join': first_join,
                                    'playtime': pdata.get('playtime', 0),
                                    'raids': pdata.get('globalData', {}).get('raids', {}).get('total', 0),
                                    'max_level': self.get_max_character_level(pdata.get('characters', {}))
                                }
                                candidates.append(candidate)
                                break

                        except Exception as e:
                            retries += 1
                            if retries < max_retries:
                                await asyncio.sleep(5 * retries)
                            else:
                                log(ERROR, f"Failed to fetch {player_name}: {e}", context="recruitment")
                                break

                    # Rate limit delay after each player (not before)
                    await asyncio.sleep(request_delay)

            # Calculate scan duration
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds()

            # Save results
            result = {
                'last_updated': end_time.isoformat(),
                'scan_duration_seconds': round(duration, 2),
                'total_scanned': total_scanned,
                'total_online': total_players,
                'candidates': candidates
            }

            save_recruitment_data(result)

            log(INFO, f"Scan complete. Found {len(candidates)} guildless candidates "
                  f"out of {total_scanned}/{total_players} players in {duration:.1f}s", context="recruitment")

        except Exception as e:
            log(ERROR, f"Error during scan: {e}", context="recruitment")

    @recruitment_loop.before_loop
    async def before_recruitment_loop(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.recruitment_loop.is_running():
            self.recruitment_loop.start()


def setup(client):
    client.add_cog(RecruitmentChecker(client))
