from collections import Counter
import datetime
import discord
import json
import asyncio
import os
import random
from typing import Dict, List, Set

import aiohttp
from discord.ext import tasks, commands

from Helpers.logger import log, INFO, ERROR
from Helpers.database import DB
from Helpers.variables import (
    SPEARHEAD_ROLE_ID,
    TERRITORY_TRACKER_CHANNEL_ID,
    GLOBAL_TERR_TRACKER_CHANNEL_ID,
    MILITARY_CHANNEL_ID,
    claims,
)

_TERRITORY_EXTERNALS_CACHE = None
DEBUG_HQ_CONGRATS = False

# ---------- HTTP (aiohttp single session + retries) ----------

_TERRITORY_URL = "https://api.wynncraft.com/v3/guild/list/territory"
_http_session: aiohttp.ClientSession | None = None

async def _get_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
        headers = {"Authorization": f"Bearer {os.getenv('WYNN_LOOP_TOKEN')}"}
        _http_session = aiohttp.ClientSession(timeout=timeout, raise_for_status=True, headers=headers)
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

# ---------- Territory persistence (database cache) ----------

def _read_territories_sync() -> dict:
    try:
        db = DB()
        db.connect()
        db.cursor.execute("SELECT data FROM cache_entries WHERE cache_key = 'territories'")
        row = db.cursor.fetchone()
        db.close()
        if row and row[0]:
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    except Exception:
        pass
    return {}

def saveTerritoryData(data):
    try:
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
        """, ('territories', json.dumps(data), epoch_time))

        db.connection.commit()
        db.close()

    except Exception as e:
        log(ERROR, f"Failed to save to cache: {e}", context="territory_tracker")
        if 'db' in locals():
            try:
                db.close()
            except:
                pass

def save_territory_exchanges(owner_changes: dict):
    """
    Persist individual territory ownership changes to territory_exchanges table.

    owner_changes: dict mapping territory_name -> {
        'old': {'owner': str, 'prefix': str, 'acquired': str},
        'new': {'owner': str, 'prefix': str, 'acquired': str},
    }
    """
    if not owner_changes:
        return

    try:
        db = DB()
        db.connect()

        # Ensure table + indexes exist (matches website schema)
        db.cursor.execute("""
            CREATE TABLE IF NOT EXISTS territory_exchanges (
                exchange_time TIMESTAMPTZ NOT NULL,
                territory     VARCHAR(100) NOT NULL,
                attacker_name VARCHAR(100) NOT NULL,
                defender_name VARCHAR(100)
            )
        """)
        # Drop NOT NULL on defender_name for existing tables (season start = no defenders)
        db.cursor.execute("""
            ALTER TABLE territory_exchanges
            ALTER COLUMN defender_name DROP NOT NULL
        """)
        db.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_te_territory_time
            ON territory_exchanges (territory, exchange_time DESC)
        """)
        db.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_te_time
            ON territory_exchanges (exchange_time)
        """)

        # Ensure guild_prefixes table exists
        db.cursor.execute("""
            CREATE TABLE IF NOT EXISTS guild_prefixes (
                guild_name   VARCHAR(100) PRIMARY KEY,
                guild_prefix VARCHAR(10) NOT NULL
            )
        """)

        guilds_seen = {}  # name -> prefix

        for terr, change in owner_changes.items():
            new_info = change['new']
            old_info = change['old']

            # Use the Wynncraft API's acquired timestamp for the new owner
            exchange_time = new_info['acquired']

            db.cursor.execute("""
                INSERT INTO territory_exchanges
                    (exchange_time, territory, attacker_name, defender_name)
                VALUES (%s, %s, %s, %s)
            """, (exchange_time, terr, new_info['owner'], old_info['owner']))

            if new_info['owner']:
                guilds_seen[new_info['owner']] = new_info['prefix']
            if old_info['owner']:
                guilds_seen[old_info['owner']] = old_info['prefix']

        # Upsert guild prefixes
        for guild_name, guild_prefix in guilds_seen.items():
            db.cursor.execute("""
                INSERT INTO guild_prefixes (guild_name, guild_prefix)
                VALUES (%s, %s)
                ON CONFLICT (guild_name) DO UPDATE
                SET guild_prefix = EXCLUDED.guild_prefix
            """, (guild_name, guild_prefix))

        db.connection.commit()
        db.close()
    except Exception as e:
        log(ERROR, f"Failed to save territory exchanges: {e}", context="territory_tracker")
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


# Helper functions for new features
def get_all_hq_territories():
    """Get a set of all HQ territory names from claims configuration."""
    hq_territories = set()
    for claim_name, cfg in claims.items():
        hq = cfg.get("hq")
        if hq:
            hq_territories.add(hq)
    return hq_territories


def _load_territory_externals():
    global _TERRITORY_EXTERNALS_CACHE
    if _TERRITORY_EXTERNALS_CACHE is not None:
        return _TERRITORY_EXTERNALS_CACHE
    try:
        with open("data/territory_externals.json", "r", encoding="utf-8") as f:
            _TERRITORY_EXTERNALS_CACHE = json.load(f)
    except Exception:
        _TERRITORY_EXTERNALS_CACHE = {}
    return _TERRITORY_EXTERNALS_CACHE


def _get_claim_by_hq(hq_name: str):
    for claim_name, cfg in claims.items():
        if cfg.get("hq") == hq_name:
            return claim_name, cfg
    return None, None


def _hq_connections_by_hq():
    return {
        cfg.get("hq"): cfg.get("connections", [])
        for cfg in claims.values()
        if cfg.get("hq")
    }


def _evaluate_hq_difficulty(hq_name: str, claim_holder_guild: str, data: Dict):
    territory_externals = _load_territory_externals()
    externals = list(territory_externals.get(hq_name, []))
    conns_by_hq = _hq_connections_by_hq()
    excluded = set(conns_by_hq.get(hq_name, []))
    filtered = [t for t in externals if t not in excluded]
    reduced = len(filtered) != len(externals)
    total = len(filtered)
    if total <= 1:
        return False, total, 0, reduced
    owned = sum(
        1
        for t in filtered
        if data.get(t, {}).get("guild", {}).get("name") == claim_holder_guild
    )
    return (owned / total) >= 0.5, total, owned, reduced


def _claim_owner_counts(claim_cfg: Dict, data: Dict):
    hq = claim_cfg.get("hq")
    conns = claim_cfg.get("connections", [])
    members = [hq] + conns if hq else conns
    counts = Counter()
    for terr in members:
        owner = data.get(terr, {}).get("guild", {}).get("name")
        if owner:
            counts[owner] += 1
    return len(members), counts


def _mega_claim_suppressed(data: Dict):
    ragni_cfg = claims.get("Ragni")
    detlas_cfg = claims.get("Detlas")
    if not ragni_cfg or not detlas_cfg:
        return False
    ragni_total, ragni_counts = _claim_owner_counts(ragni_cfg, data)
    detlas_total, detlas_counts = _claim_owner_counts(detlas_cfg, data)
    if ragni_total == 0 or detlas_total == 0:
        return False
    guilds = set(ragni_counts.keys()) | set(detlas_counts.keys())
    for guild in guilds:
        if (
            ragni_counts.get(guild, 0) / ragni_total >= 0.5
            and detlas_counts.get(guild, 0) / detlas_total >= 0.5
        ):
            return True
    return False


class TerritoryTracker(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.territory_tracker.start()

    def cog_unload(self):
        self.territory_tracker.cancel()
        asyncio.create_task(_close_session())

    @tasks.loop(seconds=10)
    async def territory_tracker(self):
        # Guild restriction: operates on home guild channels only
        # (TERRITORY_TRACKER_CHANNEL_ID, GLOBAL_TERR_TRACKER_CHANNEL_ID, MILITARY_CHANNEL_ID)
        try:
            if not self.client.is_ready():
                return

            channel = self.client.get_channel(TERRITORY_TRACKER_CHANNEL_ID)
            if channel is None:
                return

            global_channel = self.client.get_channel(GLOBAL_TERR_TRACKER_CHANNEL_ID)

            old_data = await asyncio.to_thread(_read_territories_sync)

            new_data = await getTerritoryData()
            if not new_data:
                return

            await asyncio.to_thread(saveTerritoryData, new_data)

            # tally post-update counts
            new_counts = Counter()
            for info in new_data.values():
                guild_name = (info.get('guild') or {}).get('name')
                if guild_name:
                    new_counts[guild_name] += 1

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

                        # determine which territory and who took it
                        if hq in lost:
                            lost_terr = hq
                            terr_type = "HQ"
                        elif lost:
                            lost_terr = lost[0]
                            terr_type = "connection"
                        else:
                            lost_terr = None
                            terr_type = "connection"

                        # Check spearhead ping conditions:
                        # 1. Guild owns more than 7 territories
                        # 2. We had held all territories in this claim for >20 minutes
                        aquarium_territory_count = sum(
                            1 for info in old_data.values()
                            if info.get('guild', {}).get('name') == 'The Aquarium'
                        )

                        should_ping_spearhead = False
                        if aquarium_territory_count > 7:
                            # Check if we had held all claim territories for >20 minutes
                            current_time = datetime.datetime.now(datetime.timezone.utc)
                            most_recent_acquisition = None

                            for terr in members:
                                terr_info = old_data.get(terr)
                                if terr_info and terr_info.get('guild', {}).get('name') == 'The Aquarium':
                                    acquired_str = terr_info.get('acquired', '')
                                    if acquired_str:
                                        acquired_time = datetime.datetime.fromisoformat(acquired_str.rstrip('Z'))
                                        acquired_time = acquired_time.replace(tzinfo=datetime.timezone.utc)
                                        if most_recent_acquisition is None or acquired_time > most_recent_acquisition:
                                            most_recent_acquisition = acquired_time

                            if most_recent_acquisition:
                                time_held = current_time - most_recent_acquisition
                                if time_held.total_seconds() > 1200:  # 20 minutes
                                    should_ping_spearhead = True

                        # Alert
                        alert_chan = self.client.get_channel(MILITARY_CHANNEL_ID)

                        # Check if attack pings are enabled via toggle
                        if should_ping_spearhead and alert_chan:
                            try:
                                db = DB()
                                db.connect()
                                db.cursor.execute(
                                    "SELECT setting_value FROM guild_settings WHERE guild_id = %s AND setting_key = %s",
                                    (alert_chan.guild.id, 'attack_ping')
                                )
                                result = db.cursor.fetchone()
                                db.close()
                                # Default to True if no setting exists, but respect toggle if set
                                if result is not None and not result[0]:
                                    should_ping_spearhead = False
                            except Exception:
                                pass  # If DB check fails, use the existing should_ping_spearhead value

                        # get the guild that took the territory and build message
                        if lost_terr:
                            attacker = new_data.get(lost_terr, {}).get("guild", {}).get("name", "Unknown")
                            attacker_prefix = new_data.get(lost_terr, {}).get("guild", {}).get("prefix", "???")
                            if should_ping_spearhead:
                                mention = f"<@&{SPEARHEAD_ROLE_ID}>"
                                msg = f"{mention} **Attack on {claim_name}!** {terr_type.capitalize()} **{lost_terr}** taken by **{attacker} [{attacker_prefix}]**"
                            else:
                                msg = f"**Attack on {claim_name}!** {terr_type.capitalize()} **{lost_terr}** taken by **{attacker} [{attacker_prefix}]**"
                        else:
                            if should_ping_spearhead:
                                mention = f"<@&{SPEARHEAD_ROLE_ID}>"
                                msg = f"{mention} **Attack on {claim_name}!** A {terr_type} was taken."
                            else:
                                msg = f"**Attack on {claim_name}!** A {terr_type} was taken."

                        if alert_chan:
                            await alert_chan.send(msg)

            # ---------- Territory Change Embeds ----------
            owner_changes = {}
            all_owner_changes = {}
            for terr, new_info in new_data.items():
                old_info = old_data.get(terr)
                if not old_info:
                    continue
                old_guild = old_info.get('guild') or {}
                new_guild = new_info.get('guild') or {}
                old_owner = old_guild.get('name')
                new_owner = new_guild.get('name')
                if old_owner != new_owner:
                    change_data = {
                        'old': {
                            'owner': old_owner,
                            'prefix': old_guild.get('prefix'),
                            'acquired': old_info.get('acquired')
                        },
                        'new': {
                            'owner': new_owner,
                            'prefix': new_guild.get('prefix'),
                            'acquired': new_info.get('acquired')
                        }
                    }
                    all_owner_changes[terr] = change_data
                    if 'The Aquarium' in (old_owner, new_owner):
                        owner_changes[terr] = change_data

            # Persist exchanges to territory_exchanges table
            if all_owner_changes:
                await asyncio.to_thread(save_territory_exchanges, all_owner_changes)

            # Check for HQ captures and send congratulations
            hq_territories = get_all_hq_territories()
            for terr, change in owner_changes.items():
                old = change['old']
                new = change['new']

                # Check if this is an HQ capture by The Aquarium
                if (terr in hq_territories and
                    old['owner'] != 'The Aquarium' and
                    new['owner'] == 'The Aquarium'):

                    # Find which claim this HQ belongs to
                    claim_name, _ = _get_claim_by_hq(terr)

                    if claim_name:
                        claim_holder_guild = old['owner']
                        mega_suppressed = False
                        if terr in ("Nomads' Refuge", "Mine Base Plains"):
                            mega_suppressed = _mega_claim_suppressed(new_data)

                        difficulty_valid = False
                        total_externals = 0
                        owned_externals = 0
                        conns_reduced = False
                        if not mega_suppressed:
                            (difficulty_valid, total_externals, owned_externals,
                             conns_reduced) = _evaluate_hq_difficulty(
                                terr, claim_holder_guild, new_data
                            )

                        if not mega_suppressed and difficulty_valid:
                            # Send congratulations message to military channel (no ping)
                            alert_chan = self.client.get_channel(MILITARY_CHANNEL_ID)
                            if alert_chan:
                                congrats_msg = f"🎉 Congratulations on a successful snipe of **{claim_name}** owned by **{old['owner']}**!"
                                await alert_chan.send(congrats_msg)
                        elif DEBUG_HQ_CONGRATS:
                            log(INFO,
                                f"HQ Congrats Suppressed: "
                                f"hq={terr} "
                                f"snipe_guild={new['owner']} "
                                f"claim_holder_guild={claim_holder_guild} "
                                f"externals_total={total_externals} "
                                f"externals_owned={owned_externals} "
                                f"conns_reduced={conns_reduced} "
                                f"mega_claim_suppressed={mega_suppressed}",
                                context="territory_tracker"
                            )

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

            # ---------- Global Territory Tracker Embeds ----------
            if global_channel:
                for terr, change in all_owner_changes.items():
                    old = change['old']
                    new = change['new']

                    # Determine color and title
                    if new['owner'] == 'The Aquarium':
                        color = discord.Color.green()
                        title = f"🟢 Territory Gained: **{terr}**"
                    elif old['owner'] == 'The Aquarium':
                        color = discord.Color.red()
                        title = f"🔴 Territory Lost: **{terr}**"
                    else:
                        color = discord.Color.from_rgb(255, 255, 255)
                        title = f"⚪ Territory Changed: **{terr}**"

                    global_embed = discord.Embed(title=title, color=color)
                    global_embed.add_field(
                        name="Old Owner",
                        value=(
                            f"{old['owner']} [{old['prefix']}]\n"
                            f"Territories: {new_counts.get(old['owner'], 0)}"
                        ),
                        inline=True
                    )
                    global_embed.add_field(
                        name="\u200b",
                        value="➜",
                        inline=True
                    )
                    global_embed.add_field(
                        name="New Owner",
                        value=(
                            f"{new['owner']} [{new['prefix']}]\n"
                            f"Territories: {new_counts.get(new['owner'], 0)}"
                        ),
                        inline=True
                    )

                    await global_channel.send(embed=global_embed)

        except Exception as e:
            # Log and continue; the task loop will run again next tick
            log(ERROR, f"error: {e!r}", context="territory_tracker")

    @commands.Cog.listener()
    async def on_ready(self):
        data = await getTerritoryData()
        if data:
            await asyncio.to_thread(saveTerritoryData, data)
        if not self.territory_tracker.is_running():
            self.territory_tracker.start()


def setup(client):
    client.add_cog(TerritoryTracker(client))
