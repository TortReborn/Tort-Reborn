import datetime
import json
import aiohttp

from discord.ext import tasks, commands

from Helpers.database import DB


class CacheGuildColors(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.cache_guild_colors.start()

    def cog_unload(self):
        self.cache_guild_colors.cancel()

    @tasks.loop(hours=1)
    async def cache_guild_colors(self):
        if not self.client.is_ready():
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('https://athena.wynntils.com/cache/get/guildList') as resp:
                    if resp.status != 200:
                        print(f"[cache_guild_colors] Failed to fetch from Wynntils API: {resp.status}")
                        return
                    guilds = await resp.json()

            # Store in database cache
            db = DB()
            db.connect()

            epoch_time = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)

            db.cursor.execute("""
                INSERT INTO cache_entries (cache_key, data, expires_at, fetch_count)
                VALUES (%s, %s, %s, 1)
                ON CONFLICT (cache_key)
                DO UPDATE SET
                    data = EXCLUDED.data,
                    created_at = NOW(),
                    expires_at = EXCLUDED.expires_at,
                    fetch_count = cache_entries.fetch_count + 1,
                    last_error = NULL,
                    error_count = 0
            """, ('guildColors', json.dumps(guilds), epoch_time))

            db.connection.commit()
            db.close()

            print(f"[cache_guild_colors] Updated cache with {len(guilds)} guilds")

        except Exception as e:
            print(f"[cache_guild_colors] Error: {e}")

    @cache_guild_colors.before_loop
    async def before_cache(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.cache_guild_colors.is_running():
            self.cache_guild_colors.start()


def setup(client):
    client.add_cog(CacheGuildColors(client))
