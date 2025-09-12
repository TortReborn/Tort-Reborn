from collections import Counter
import datetime
import discord
import json
import asyncio
import random
from typing import Dict, List, Set

import aiohttp
from discord.ext import tasks, commands

from Helpers.database import DB
from Helpers.variables import (
    spearhead_role_id,
    territory_tracker_channel,
    military_channel,
    claims,
)

# ---------- HTTP (aiohttp single session + retries) ----------

_TERRITORY_URL = "https://api.wynncraft.com/v3/guild/list/territory"
_http_session: aiohttp.ClientSession | None = None

async def _get_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
        _http_session = aiohttp.ClientSession(timeout=timeout, raise_for_status=True)
    return _http_session

async def _close_session():
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()

async def getTerritoryData():
    try:
        sess = await _get_session()
        for attempt in range(3):
            try:
                async with sess.get(_TERRITORY_URL) as resp:
                    return await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
                if attempt == 2:
                    return False
                await asyncio.sleep((2 ** attempt) + random.uniform(0, 0.3))
    except Exception:
        return False

# ---------- File I/O (run in thread) ----------

def _read_territories_sync() -> dict:
    try:
        with open('territories.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

def saveTerritoryData(data):
    # Save to JSON file
    with open('territories.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)
        f.close()
    
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
        """, ('territories', json.dumps(data), epoch_time))
        
        db.connection.commit()
        db.close()
        
    except Exception as e:
        print(f"[saveTerritoryData] Failed to save to cache: {e}")
        # Don't let database errors prevent the file save from working
        if 'db' in locals():
            try:
                db.close()
            except:
                pass

# ---------- Time helper (unchanged) ----------

def timeHeld(date_time_old, date_time_new):
    t_old = datetime.datetime.fromisoformat(date_time_old[0:len(date_time_old) - 1])
    t_new = datetime.datetime.fromisoformat(date_time_new[0:len(date_time_new) - 1])
    t_held = t_new.__sub__(t_old)

    d = t_held.days
    td = datetime.timedelta(seconds=t_held.seconds)
    t = str(td).split(":")

    return f"{d} d {t[0]} h {t[1]} m {t[2]} s"


class TerritoryTracker(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.territory_tracker.start()

    def cog_unload(self):
        self.territory_tracker.cancel()
        asyncio.create_task(_close_session())

    @tasks.loop(seconds=10)
    async def territory_tracker(self):
        try:
            if not self.client.is_ready():
                return

            channel = self.client.get_channel(territory_tracker_channel)
            if channel is None:
                return

            old_data = await asyncio.to_thread(_read_territories_sync)

            new_data = await getTerritoryData()
            if not new_data:
                return

            await asyncio.to_thread(saveTerritoryData, new_data)

            # tally post-update counts
            new_counts = Counter()
            for info in new_data.values():
                new_counts[info['guild']['name']] += 1

            # ---------- CLAIM-BROKEN ALERTS (CONFIG-DRIVEN) ----------
            # fires on transition: previously owned ALL tiles in claim → now missing any tile
            if old_data and claims:
                for claim_name, cfg in claims.items():
                    hq = cfg.get("hq")
                    conns: List[str] = cfg.get("connections", [])
                    if not hq:
                        continue
                    members: List[str] = [hq] + conns

                    def _owns_all(data: Dict) -> bool:
                        for t in members:
                            owner = data.get(t, {}).get("guild", {}).get("name")
                            if owner != 'The Aquarium':
                                return False
                        return True

                    old_all = _owns_all(old_data)
                    new_all = _owns_all(new_data)

                    if old_all and not new_all:
                        # what flipped away from our guild?
                        lost = [
                            t for t in members
                            if old_data.get(t, {}).get("guild", {}).get("name") == "The Aquarium"
                            and new_data.get(t, {}).get("guild", {}).get("name") != "The Aquarium"
                        ]

                        # classify cause
                        if hq in lost:
                            cause_text = f"**HQ {hq}**"
                        elif lost:
                            cause_text = f"connection **{lost[0]}**"
                        else:
                            cause_text = "a connection"

                        # Alert
                        alert_chan = self.client.get_channel(military_channel)
                        mention = f"<@&{spearhead_role_id}>"
                        msg = f"{mention} TAq claim **broken** in **{claim_name}** — lost {cause_text}."

                        if alert_chan:
                            await alert_chan.send(msg)

            # ---------- Territory Change Embeds ----------
            owner_changes = {}
            for terr, new_info in new_data.items():
                old_info = old_data.get(terr)
                if not old_info:
                    continue
                old_owner = old_info['guild']['name']
                new_owner = new_info['guild']['name']
                if old_owner != new_owner and ('The Aquarium' in (old_owner, new_owner)):
                    owner_changes[terr] = {
                        'old': {
                            'owner': old_owner,
                            'prefix': old_info['guild']['prefix'],
                            'acquired': old_info['acquired']
                        },
                        'new': {
                            'owner': new_owner,
                            'prefix': new_info['guild']['prefix'],
                            'acquired': new_info['acquired']
                        }
                    }

            for terr, change in owner_changes.items():
                old = change['old']
                new = change['new']

                # Determine gain vs loss
                if new['owner'] == 'The Aquarium':
                    color = discord.Color.green()
                    title = f"🟢 Territory Gained: **{terr}**"
                else:
                    color = discord.Color.red()
                    title = f"🔴 Territory Lost: **{terr}**"

                taken_dt = datetime.datetime.fromisoformat(new['acquired'].rstrip('Z'))
                taken_dt = taken_dt.replace(tzinfo=datetime.timezone.utc)

                embed = discord.Embed(
                    title=title,
                    color=color,
                    # timestamp=taken_dt
                )
                embed.add_field(
                    name="Old Owner",
                    value=(
                        f"{old['owner']} [{old['prefix']}]\n"
                        f"Territories: {new_counts.get(old['owner'], 0)}"
                    ),
                    inline=True
                )

                embed.add_field(
                    name="\u200b",
                    value="➜",
                    inline=True
                )

                embed.add_field(
                    name="New Owner",
                    value=(
                        f"{new['owner']} [{new['prefix']}]\n"
                        f"Territories: {new_counts.get(new['owner'], 0)}"
                    ),
                    inline=True
                )

                await channel.send(embed=embed)

        except Exception as e:
            # Log and continue; the task loop will run again next tick
            print(f"[territory_tracker] error: {e!r}")

    @commands.Cog.listener()
    async def on_ready(self):
        data = await getTerritoryData()
        if data:
            await asyncio.to_thread(saveTerritoryData, data)
        if not self.territory_tracker.is_running():
            self.territory_tracker.start()


def setup(client):
    client.add_cog(TerritoryTracker(client))
