import json
import datetime
import asyncio
from datetime import timezone, time as dtime
from discord.ext import tasks, commands

from Helpers.database import DB


class CachePlayerActivity(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.cache_activity_data.start()
        self._startup_check_done = False

    def cog_unload(self):
        self.cache_activity_data.cancel()

    async def check_and_create_initial_cache(self):
        """Check if cache entry exists, create it if not"""
        try:
            db = DB()
            db.connect()

            # Check if cache entry exists
            db.cursor.execute(
                "SELECT cache_key FROM cache_entries WHERE cache_key = %s",
                ('player_activity_cache',)
            )
            exists = db.cursor.fetchone()
            db.close()

            if not exists:
                print("[CachePlayerActivity] No existing cache found, creating initial cache entry")
                await self.create_cache_entry()
            else:
                print("[CachePlayerActivity] Cache entry already exists")

        except Exception as e:
            print(f"[CachePlayerActivity] Error checking cache existence: {e}")
            if 'db' in locals():
                try:
                    db.close()
                except:
                    pass

    async def create_cache_entry(self):
        """Create cache entry with current available data"""
        try:
            # Load player_activity.json (most recent first)
            try:
                with open('player_activity.json', 'r', encoding='utf-8') as f:
                    activity_data = json.load(f)
            except FileNotFoundError:
                print("[CachePlayerActivity] player_activity.json not found, creating empty cache")
                activity_data = []
            except json.JSONDecodeError as e:
                print(f"[CachePlayerActivity] Error decoding player_activity.json: {e}, creating empty cache")
                activity_data = []

            if not isinstance(activity_data, list):
                print("[CachePlayerActivity] player_activity.json is not a list, creating empty cache")
                activity_data = []

            # Extract data for specific days
            cache_data = {
                'cached_at': datetime.datetime.now(timezone.utc).isoformat(),
                'days': {}
            }

            target_days = [1, 7, 14, 30]

            for day in target_days:
                if day < len(activity_data):
                    # Get the snapshot for this day
                    snapshot = activity_data[day]
                    cache_data['days'][f'day_{day}'] = {
                        'time': snapshot.get('time'),
                        'members': snapshot.get('members', [])
                    }
                    print(f"[CachePlayerActivity] Added data for day {day} with {len(snapshot.get('members', []))} members")
                else:
                    # Not enough historical data for this day
                    cache_data['days'][f'day_{day}'] = {
                        'time': None,
                        'members': []
                    }
                    print(f"[CachePlayerActivity] No data available for day {day}")

            # Save to database cache
            try:
                db = DB()
                db.connect()

                # Set expiration to epoch time (January 1, 1970)
                epoch_time = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)

                # Use ON CONFLICT to either insert or update the cache entry
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
                """, ('player_activity_cache', json.dumps(cache_data), epoch_time))

                db.connection.commit()
                db.close()

                print(f"[CachePlayerActivity] Successfully created initial cache for days: {target_days}")

            except Exception as e:
                print(f"[CachePlayerActivity] Failed to save initial cache: {e}")
                if 'db' in locals():
                    try:
                        db.close()
                    except:
                        pass

        except Exception as e:
            print(f"[CachePlayerActivity] Unexpected error creating cache: {e}")

    @tasks.loop(time=dtime(hour=0, minute=15, tzinfo=timezone.utc))
    async def cache_activity_data(self):
        """
        Daily task that runs at 00:15 UTC to cache player activity data
        for specific day intervals (1, 7, 14, and 30 days ago)
        """
        try:
            print("[CachePlayerActivity] Starting player activity cache task")

            # Load player_activity.json (most recent first)
            try:
                with open('player_activity.json', 'r', encoding='utf-8') as f:
                    activity_data = json.load(f)
            except FileNotFoundError:
                print("[CachePlayerActivity] player_activity.json not found")
                return
            except json.JSONDecodeError as e:
                print(f"[CachePlayerActivity] Error decoding player_activity.json: {e}")
                return

            if not isinstance(activity_data, list):
                print("[CachePlayerActivity] player_activity.json is not a list")
                return

            # Extract data for specific days
            # Index 0 is day 0 (today), so we want indices 1, 7, 14, and 30
            cache_data = {
                'cached_at': datetime.datetime.now(timezone.utc).isoformat(),
                'days': {}
            }

            target_days = [1, 7, 14, 30]

            for day in target_days:
                if day < len(activity_data):
                    # Get the snapshot for this day
                    snapshot = activity_data[day]
                    cache_data['days'][f'day_{day}'] = {
                        'time': snapshot.get('time'),
                        'members': snapshot.get('members', [])
                    }
                    print(f"[CachePlayerActivity] Added data for day {day} with {len(snapshot.get('members', []))} members")
                else:
                    # Not enough historical data for this day
                    cache_data['days'][f'day_{day}'] = {
                        'time': None,
                        'members': []
                    }
                    print(f"[CachePlayerActivity] No data available for day {day}")

            # Save to database cache
            try:
                db = DB()
                db.connect()

                # Set expiration to epoch time (January 1, 1970)
                epoch_time = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)

                # Use ON CONFLICT to either insert or update the cache entry
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
                """, ('player_activity_cache', json.dumps(cache_data), epoch_time))

                db.connection.commit()
                db.close()

                print(f"[CachePlayerActivity] Successfully cached player activity data for days: {target_days}")

            except Exception as e:
                print(f"[CachePlayerActivity] Failed to save to cache: {e}")
                if 'db' in locals():
                    try:
                        db.close()
                    except:
                        pass

        except Exception as e:
            print(f"[CachePlayerActivity] Unexpected error: {e}")

    @cache_activity_data.before_loop
    async def before_cache(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        # Run startup check once
        if not self._startup_check_done:
            self._startup_check_done = True
            await self.check_and_create_initial_cache()

        if not self.cache_activity_data.is_running():
            self.cache_activity_data.start()


def setup(client):
    client.add_cog(CachePlayerActivity(client))