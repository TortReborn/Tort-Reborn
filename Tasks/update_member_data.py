import asyncio
import json
import os
import sys
import discord
import datetime
import time
import traceback
from datetime import timezone, timedelta, time as dtime
from collections import deque
from discord.ext import tasks, commands

# ensure prints flush immediately
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
else:
    import os as _os
    sys.stdout = _os.fdopen(sys.stdout.fileno(), 'w', buffering=1)

from Helpers.classes import Guild, DB
from Helpers.functions import getPlayerDatav3, getNameFromUUID
from Helpers.variables import (
    raid_log_channel,
    log_channel,
    notg_emoji_id,
    tcc_emoji_id,
    tna_emoji_id,
    nol_emoji_id
)

RAID_ANNOUNCE_CHANNEL_ID = raid_log_channel
LOG_CHANNEL = log_channel
GUILD_TTL = timedelta(minutes=10)
CONTRIBUTION_THRESHOLD = 2_500_000_000
RATE_LIMIT = 100  # max calls per minute
CURRENT_ACTIVITY_FILE = "current_activity.json"

RAID_EMOJIS = {
    "Nest of the Grootslangs": notg_emoji_id,
    "The Canyon Colossus": tcc_emoji_id,
    "The Nameless Anomaly": tna_emoji_id,
    "Orphion's Nexus of Light": nol_emoji_id
}

# --- thread-safe DB + snapshot helpers ---

def _db_connect_with_retry(max_attempts: int = 3, backoff_first: float = 0.5):
    attempt, delay, last = 0, backoff_first, None
    while attempt < max_attempts:
        try:
            db = DB()
            db.connect()
            return db
        except Exception as e:
            last = e
            time.sleep(delay)
            delay *= 2
            attempt += 1
    raise last

def _upsert_raid_group_sync(uuid_list):
    if not uuid_list:
        return
    db = _db_connect_with_retry()
    try:
        for uid in uuid_list:
            db.cursor.execute("SELECT ign FROM discord_links WHERE uuid = %s", (uid,))
            row = db.cursor.fetchone()
            ign = row[0] if row else None
            db.cursor.execute(
                """
                INSERT INTO uncollected_raids AS ur (uuid, ign, uncollected_raids, collected_raids)
                VALUES (%s, %s, 1, 0)
                ON CONFLICT (uuid) DO UPDATE
                  SET uncollected_raids = ur.uncollected_raids + EXCLUDED.uncollected_raids,
                      ign               = EXCLUDED.ign;
                """,
                (uid, ign)
            )
        db.connection.commit()
    finally:
        db.close()

def _graid_increment_group_sync(uuid_list, raid_name: str):
    if not uuid_list: return
    db = _db_connect_with_retry()
    try:
        cur = db.cursor
        # find active event
        cur.execute("SELECT id FROM graid_events WHERE active = TRUE LIMIT 1")
        row = cur.fetchone()
        if not row:
            return
        event_id = row[0]

        # upsert totals; 1 completion per player per validated group
        for uid in uuid_list:
            cur.execute("""
                INSERT INTO graid_event_totals (event_id, uuid, total)
                VALUES (%s, %s, 1)
                ON CONFLICT (event_id, uuid) DO UPDATE
                  SET total = graid_event_totals.total + 1,
                      last_updated = NOW()
            """, (event_id, uid))
        db.connection.commit()
    finally:
        db.close()


def _write_current_snapshot_sync(contrib_map, rank_map, pf_map):
    snap = {'time': int(time.time()), 'members': []}
    uuids = list(pf_map.keys())
    if not uuids:
        with open(CURRENT_ACTIVITY_FILE, "w", encoding="utf-8") as f:
            json.dump(snap, f, indent=2)
        
        # Save empty snapshot to database cache
        try:
            db = _db_connect_with_retry()
            
            # Set expiration to epoch time (January 1, 1970)
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
            """, ('guildData', json.dumps(snap), epoch_time))
            
            db.connection.commit()
            db.close()
            
        except Exception as e:
            print(f"[_write_current_snapshot_sync] Failed to save empty snapshot to cache: {e}")
            try:
                if 'db' in locals():
                    db.close()
            except:
                pass
        return

    db = _db_connect_with_retry()
    try:
        sql = """
        SELECT
        dl.uuid,
        COALESCE(s.shells, 0) AS shells,
        COALESCE(ur.uncollected_raids, 0) + COALESCE(ur.collected_raids, 0) AS raids_total
        FROM discord_links dl
        LEFT JOIN shells s ON dl.discord_id = s.user
        LEFT JOIN uncollected_raids ur ON dl.uuid = ur.uuid
        WHERE dl.uuid = ANY(%s::uuid[]);
        """
        db.cursor.execute(sql, (uuids,))
        rows = db.cursor.fetchall()
        stats_by_uuid = {row[0]: {'shells': row[1], 'raids': row[2]} for row in rows}
    finally:
        db.close()

    for uuid, pf in pf_map.items():
        if not isinstance(pf, dict):
            continue
        username  = pf.get('username') or pf.get('name')
        last_join = pf.get('lastJoin', "2020-03-22T11:11:17.810000Z")
        entry     = stats_by_uuid.get(uuid, {'shells': 0, 'raids': 0})
        snap['members'].append({
            'name':        username,
            'uuid':        uuid,
            'rank':        rank_map.get(uuid),
            'playtime':    pf.get('playtime'),
            'contributed': contrib_map.get(uuid),
            'wars':        pf.get('globalData', {}).get('wars'),
            'shells':      entry['shells'],
            'raids':       entry['raids'],
            'lastJoin':    last_join
        })

    with open(CURRENT_ACTIVITY_FILE, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2)
    
    # Save to database cache
    try:
        db = _db_connect_with_retry()
        
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
        """, ('guildData', json.dumps(snap), epoch_time))
        
        db.connection.commit()
        db.close()
        
    except Exception as e:
        print(f"[_write_current_snapshot_sync] Failed to save to cache: {e}")
        # Don't let database errors prevent the file save from working
        try:
            if 'db' in locals():
                db.close()
        except:
            pass


class UpdateMemberData(commands.Cog):
    RAID_NAMES = [
        "Nest of the Grootslangs",
        "The Canyon Colossus",
        "The Nameless Anomaly",
        "Orphion's Nexus of Light"
    ]

    def __init__(self, client):
        self.client = client
        self._has_started = False
        self.previous_data = self._load_json("previous_data.json", {})
        self.member_file = "member_list.json"
        self.previous_members = self._load_json(self.member_file, {})
        self.member_file_exists = os.path.exists(self.member_file)
        self.raid_participants = {raid: {"unvalidated": {}, "validated": {}} for raid in self.RAID_NAMES}
        self.cold_start = True
        self.request_times = deque()
        self._semaphore = asyncio.Semaphore(5)

    def _load_json(self, path, default):
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    return json.load(f)
        except Exception:
            traceback.print_exc()
        return default

    def _save_json(self, path, data):
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception:
            traceback.print_exc()
        return False

    def _make_progress_bar(self, percent: int, length: int = 20) -> str:
        filled = int(length * percent / 100)
        bar = "█" * filled + "─" * (length - filled)
        return f"[{bar}]"

    async def _announce_raid(self, raid, group, guild):
        print(f"Announcing raid {raid}: {group}", flush=True)
        participants = self.raid_participants[raid]["validated"]
        names = [participants[uid]["name"] for uid in group]
        bolded = [f"**{n}**" for n in names]
        names_str = ", ".join(bolded[:-1]) + ", and " + bolded[-1] if len(bolded) > 1 else bolded[0]
        emoji = RAID_EMOJIS.get(raid, "")
        now = datetime.datetime.now(timezone.utc)
        channel = self.client.get_channel(RAID_ANNOUNCE_CHANNEL_ID)
        if channel:
            embed = discord.Embed(
                title=f"{emoji} {raid} Completed!",
                description=names_str,
                timestamp=now,
                color=0x00FF00
            )
            guild_level = getattr(guild, "level", None)
            guild_xp = getattr(guild, "xpPercent", None)
            if guild_level is not None and guild_xp is not None:
                embed.add_field(
                    name=f"{guild.name} — Level {guild_level}",
                    value=self._make_progress_bar(guild_xp) + f" ({guild_xp}%)",
                    inline=False
                )
            else:
                embed.add_field(name="Progress", value=self._make_progress_bar(100), inline=False)
            embed.set_footer(text="Guild Raid Tracker")
            await channel.send(embed=embed)
        
        await asyncio.to_thread(_upsert_raid_group_sync, list(group))
        await asyncio.to_thread(_graid_increment_group_sync, list(group), raid)

    def _write_current_snapshot(self, db, guild, contrib_map, rank_map, pf_map):
        """
        Write an always‐fresh snapshot to CURRENT_ACTIVITY_FILE,
        using a single bulk query for shells and raids totals,
        joining strictly on IDs (no ign).
        """
        snap = {'time': int(time.time()), 'members': []}
        cur = db.cursor

        # 1. Gather all UUIDs we need stats for
        uuids = list(pf_map.keys())
        if not uuids:
            self._save_json(CURRENT_ACTIVITY_FILE, snap)
            return

        # 2. Bulk‐fetch shells and raids_total for every uuid in one go
        sql = """
        SELECT
        dl.uuid,
        COALESCE(s.shells, 0) AS shells,
        COALESCE(ur.uncollected_raids, 0)
            + COALESCE(ur.collected_raids, 0) AS raids_total
        FROM discord_links dl
        LEFT JOIN shells s
        ON dl.discord_id = s.user
        LEFT JOIN uncollected_raids ur
        ON dl.uuid = ur.uuid
        WHERE dl.uuid = ANY(%s::uuid[]);
        """
        cur.execute(sql, (uuids,))
        rows = cur.fetchall()

        # 3. Build lookup dict by uuid
        stats_by_uuid = {
            row[0]: {'shells': row[1], 'raids': row[2]}
            for row in rows
        }

        # 4. Populate the snapshot
        for uuid, pf in pf_map.items():
            if not isinstance(pf, dict):
                continue

            username  = pf.get('username') or pf.get('name')
            last_join = pf.get('lastJoin', "2020-03-22T11:11:17.810000Z")  # ISO8601 string

            entry      = stats_by_uuid.get(uuid, {'shells': 0, 'raids': 0})
            shells     = entry['shells']
            raids_total= entry['raids']

            snap['members'].append({
                'name':        username,
                'uuid':        uuid,
                'rank':        rank_map.get(uuid),
                'playtime':    pf.get('playtime'),
                'contributed': contrib_map.get(uuid),
                'wars':        pf.get('globalData', {}).get('wars'),
                'shells':      shells,
                'raids':       raids_total,
                'lastJoin':    last_join
            })

        # 5. Write out JSON
        self._save_json(CURRENT_ACTIVITY_FILE, snap)



    @tasks.loop(minutes=3)
    async def update_member_data(self):
        now = datetime.datetime.now(timezone.utc)
        print(f"🟦 STARTING LOOP - {now}", flush=True)

        # fetch guild over HTTP off the event loop
        guild = await asyncio.to_thread(Guild, "The Aquarium")

        # 1 Pull latest guild contributions
        contrib_map = {member['uuid']: member.get('contributed', 0) for member in guild.all_members}

        # 2: Prune stale unvalidated entries
        cutoff = now - GUILD_TTL
        for raid, queues in self.raid_participants.items():
            for uid, info in list(queues['unvalidated'].items()):
                first = info['first_seen']
                if isinstance(first, str): first = datetime.datetime.fromisoformat(first)
                if first < cutoff:
                    print(f"{now} - PRUNE unvalidated {info['name']} in {raid}", flush=True)
                    queues['unvalidated'].pop(uid)

        # 3: Member join/leave
        prev_map = self.previous_members
        curr_map = {m['uuid']: {'name': m['name'], 'rank': m.get('rank')} for m in guild.all_members}
        joined, left = set(curr_map) - set(prev_map), set(prev_map) - set(curr_map)
        if (joined or left) and not (self.cold_start and not self.member_file_exists):
            ch = self.client.get_channel(LOG_CHANNEL)
            def add_chunked(embed, title, items):
                chunk = ''
                for ign in items:
                    piece = f"**{ign}**" if not chunk else f", **{ign}**"
                    if len(chunk)+len(piece)>1024:
                        embed.add_field(name=title, value=chunk, inline=False)
                        chunk=f"**{ign}**"; title+=' cont.'
                    else:
                        chunk+=piece
                if chunk: embed.add_field(name=title, value=chunk, inline=False)
            if joined:
                ej = discord.Embed(title='Guild Members Joined', timestamp=now, color=0x00FF00)
                add_chunked(ej, 'Joined', [getNameFromUUID(u)[0] for u in joined])
                await ch.send(embed=ej)
            if left:
                el = discord.Embed(title='Guild Members Left', timestamp=now, color=0xFF0000)
                add_chunked(el, 'Left', [prev_map[u]['name'] for u in left])
                await ch.send(embed=el)
        self.previous_members = curr_map
        self._save_json(self.member_file, curr_map)

        # 4: Rank changes
        role_changes = [(u, curr_map[u]['name'], prev_map[u]['rank'], curr_map[u]['rank'])
                        for u in curr_map if u in prev_map and prev_map[u].get('rank')!=curr_map[u]['rank']]
        if role_changes and not (self.cold_start and not self.member_file_exists):
            ch = self.client.get_channel(LOG_CHANNEL)
            er = discord.Embed(title='Guild Rank Changes', timestamp=now, color=0x0000FF)
            for _,name,old,new in role_changes:
                er.add_field(name=name,value=f"{old} → {new}",inline=False)
            await ch.send(embed=er)

        # 5: Update presence
        await self.client.change_presence(activity=discord.CustomActivity(name=f"{guild.online} members online"))

        # 6: Fetch individual raid data
        results=[]
        for m in guild.all_members:
            async with self._semaphore:
                cutoff_ts=datetime.datetime.now(timezone.utc)-timedelta(minutes=1)
                while self.request_times and self.request_times[0]<cutoff_ts:
                    self.request_times.popleft()
                if len(self.request_times)>=RATE_LIMIT:
                    wait=(self.request_times[0]+timedelta(minutes=1)-datetime.datetime.now(timezone.utc)).total_seconds()
                    print(f"Rate limit reached, sleeping {wait:.1f}s",flush=True)
                    await asyncio.sleep(wait)
                self.request_times.append(datetime.datetime.now(timezone.utc))
                res=await asyncio.to_thread(getPlayerDatav3,m['uuid'])
            results.append(res)

        prev = self.previous_data

        # --- 7: Build new snapshot (carry forward) & detect unvalidated only with fresh data ---
        new_data = dict(prev)      # carry forward last-known values for members we didn't fetch
        fresh = set()              # UUIDs we successfully fetched this iteration

        for m in results:
            if not isinstance(m, dict):
                continue

            uid, uname = m['uuid'], m['username']
            fresh.add(uid)

            raids = m.get('globalData', {}).get('raids', {}).get('list', {})
            counts = {r: raids.get(r, 0) for r in self.RAID_NAMES}

            # Use current contrib if present; otherwise fall back to last-known (carry-forward)
            carried_contrib = new_data.get(uid, {}).get('contributed', 0)
            contributed = contrib_map.get(uid, carried_contrib)

            new_data[uid] = {
                'raids': counts,
                'contributed': contributed
            }

            # Only detect “unvalidated” if:
            #  - we have fresh data for this UID (we do),
            #  - we are not on cold start,
            #  - AND we have a previous baseline for this UID.
            if not self.cold_start and uid in prev:
                old_counts = prev.get(uid, {}).get('raids', {r: 0 for r in self.RAID_NAMES})
                for raid in self.RAID_NAMES:
                    diff = counts[raid] - old_counts.get(raid, 0)
                    if (
                        0 < diff < 3 and
                        uid not in self.raid_participants[raid]['unvalidated'] and
                        uid not in self.raid_participants[raid]['validated']
                    ):
                        self.raid_participants[raid]['unvalidated'][uid] = {
                            'name': uname,
                            'first_seen': now,
                            'baseline_contrib': prev.get(uid, {}).get('contributed', 0)
                        }
                        print(f"{now} - DETECT unvalidated {uname} in {raid}", flush=True)

        # --- 8: XP jumps (only consider fresh data + existing baseline) ---
        xp_jumps = set()
        for uid in fresh:
            if uid not in prev:   # no baseline => skip
                continue
            old_c = prev[uid].get('contributed', 0)
            new_c = new_data[uid].get('contributed', 0)
            if new_c - old_c >= CONTRIBUTION_THRESHOLD:
                xp_jumps.add(uid)
                print(f"{now} - XP threshold met for {uid} (diff: {new_c-old_c} >= {CONTRIBUTION_THRESHOLD})", flush=True)

        # --- 9: Validate via XP jump ---
        for raid, queues in self.raid_participants.items():
            for uid in list(queues['unvalidated']):
                if uid in xp_jumps:
                    info = queues['unvalidated'].pop(uid)
                    queues['validated'][uid] = info
                    print(f"{now} - VALIDATED {info['name']} for {raid} via XP jump", flush=True)

        # --- 10: Validate via contrib diff (only if we have fresh data this tick) ---
        for raid, queues in self.raid_participants.items():
            for uid, info in list(queues['unvalidated'].items()):
                if uid not in fresh:
                    # No fresh data for this UID this tick—don’t try to validate
                    print(f"{datetime.datetime.now(timezone.utc)} - SKIP contrib validation for {uid}: no fresh data", flush=True)
                    continue
                base = info['baseline_contrib']
                curr = new_data.get(uid, {}).get('contributed', 0)
                if curr - base >= CONTRIBUTION_THRESHOLD:
                    queues['validated'][uid] = info
                    queues['unvalidated'].pop(uid)
                    print(f"{now} - VALIDATED {info['name']} for {raid} (contrib diff: {curr-base} >= {CONTRIBUTION_THRESHOLD})", flush=True)

        # --- 11: Announce raids (unchanged) ---
        for raid in self.RAID_NAMES:
            vals = self.raid_participants[raid]['validated']
            if len(vals) >= 4:
                group = set(list(vals)[:4])
                await self._announce_raid(raid, group, guild)
                for uid in group:
                    vals.pop(uid)

        # --- 12: Persist (unchanged, but now includes carry-forward) ---
        self.previous_data = new_data
        self._save_json("previous_data.json", new_data)
        self.cold_start = False

        # Build pf_map ONLY from fresh results for the current snapshot write
        pf_map = {m['uuid']: m for m in results if isinstance(m, dict)}
        rank_map = {u: info.get('rank') for u, info in curr_map.items()}
        await asyncio.to_thread(
            _write_current_snapshot_sync,
            contrib_map, rank_map, pf_map
        )
        
        print(f"🟨 ENDING LOOP - {datetime.datetime.now(timezone.utc)}",flush=True)

    @tasks.loop(time=dtime(hour=0, minute=1, tzinfo=timezone.utc))
    async def daily_activity_snapshot(self):
        print("Starting daily activity snapshot", flush=True)

        # --- Guard: ensure only one snapshot per UTC day (most-recent-first file) ---
        pth = "player_activity.json"
        today_utc = datetime.datetime.now(timezone.utc).date()
        try:
            old = self._load_json(pth, [])
            if old:
                last_ts = old[0].get("time")
                if isinstance(last_ts, (int, float)):
                    last_date = datetime.datetime.fromtimestamp(last_ts, tz=timezone.utc).date()
                    if last_date == today_utc:
                        print("Daily snapshot already exists for today; skipping duplicate.", flush=True)
                        return
        except Exception:
            traceback.print_exc()
            # If the file is malformed, proceed to write a fresh snapshot below.

        # --- Build snapshot (unchanged) ---
        db = DB(); db.connect()
        guild = Guild("The Aquarium")
        snap = {'time': int(time.time()), 'members': []}

        # 1: build contrib and rank maps from recent guild state
        contrib_map = {m['uuid']: m.get('contributed', 0) for m in guild.all_members}
        rank_map    = {m['uuid']: m.get('rank')       for m in guild.all_members}

        # 2: snapshot each member currently in guild
        for m in guild.all_members:
            uuid = m['uuid']
            username = m['name']

            async with self._semaphore:
                pf = await asyncio.to_thread(getPlayerDatav3, uuid)
            if not isinstance(pf, dict):
                continue

            # shells
            db.cursor.execute(
                "SELECT COALESCE(s.shells, 0) "
                "FROM discord_links dl "
                "LEFT JOIN shells s ON dl.discord_id = s.user "
                "WHERE dl.uuid = %s",
                (uuid,)
            )
            row = db.cursor.fetchone()
            sh = row[0] if row else 0

            # raids
            db.cursor.execute(
                "SELECT COALESCE(ur.uncollected_raids, 0) + COALESCE(ur.collected_raids, 0) "
                "FROM discord_links dl "
                "LEFT JOIN uncollected_raids ur ON dl.uuid = ur.uuid "
                "WHERE dl.uuid = %s",
                (uuid,)
            )
            row = db.cursor.fetchone()
            rd = row[0] if row else 0

            snap['members'].append({
                'name': username,
                'uuid': uuid,
                'rank': rank_map.get(uuid),
                'playtime': pf.get('playtime'),
                'contributed': contrib_map.get(uuid),
                'wars': pf.get('globalData', {}).get('wars'),
                'shells': sh,
                'raids': rd
            })

        # 3: write out json
        pth = "player_activity.json"
        old = self._load_json(pth, [])
        old.insert(0, snap)
        with open(pth, 'w') as f:
            json.dump(old, f, indent=2)
        db.close()
        print("Daily activity snapshot complete", flush=True)
    
    @update_member_data.before_loop
    async def before_update(self):
        await self.client.wait_until_ready()

    @daily_activity_snapshot.before_loop
    async def before_snapshot(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if self._has_started: return
        self._has_started=True
        if not self.update_member_data.is_running(): self.update_member_data.start()
        if not self.daily_activity_snapshot.is_running(): self.daily_activity_snapshot.start()

    @update_member_data.error
    async def on_update_member_data_error(self, error):
        print("🚨 update_member_data loop raised:", file=sys.stderr)
        traceback.print_exc()
        # restart the loop after a short pause
        await asyncio.sleep(5)
        if not self.update_member_data.is_running():
            self.update_member_data.start()


def setup(client):
    client.add_cog(UpdateMemberData(client))
