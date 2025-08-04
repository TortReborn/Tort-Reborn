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
RATE_LIMIT = 75  # max calls per minute
CURRENT_ACTIVITY_FILE = "current_activity.json"

RAID_EMOJIS = {
    "Nest of the Grootslangs": notg_emoji_id,
    "The Canyon Colossus": tcc_emoji_id,
    "The Nameless Anomaly": tna_emoji_id,
    "Orphion's Nexus of Light": nol_emoji_id
}

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

    async def _announce_raid(self, raid, group, guild, db):
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
        for uid in group:
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
            last_join = pf.get('lastJoin')  # ISO8601 string

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



    @tasks.loop(minutes=2)
    async def update_member_data(self):
        now = datetime.datetime.now(timezone.utc)
        print(f"STARTING LOOP - {now}", flush=True)
        # open DB off the event loop
        db = DB()
        await asyncio.to_thread(db.connect)

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

        prev=self.previous_data
        new_data={}
        # 7: Build new snapshot & detect unvalidated
        for m in results:
            if not isinstance(m,dict): continue
            uid,uname=m['uuid'],m['username']
            raids=m.get('globalData',{}).get('raids',{}).get('list',{})
            counts={r:raids.get(r,0) for r in self.RAID_NAMES}
            contributed=contrib_map.get(uid,0)
            new_data[uid]={'raids':counts,'contributed':contributed}
            if not self.cold_start:
                old_counts=prev.get(uid,{}).get('raids',{r:0 for r in self.RAID_NAMES})
                for raid in self.RAID_NAMES:
                    diff=counts[raid]-old_counts.get(raid,0)
                    if 0<diff<3 and uid not in self.raid_participants[raid]['unvalidated'] and uid not in self.raid_participants[raid]['validated']:
                        self.raid_participants[raid]['unvalidated'][uid]={'name':uname,'first_seen':now,'baseline_contrib':prev.get(uid,{}).get('contributed',0)}
                        print(f"{now} - DETECT unvalidated {uname} in {raid}",flush=True)

        # 8: XP jumps
        xp_jumps=set()
        for uid,info in new_data.items():
            old_c=prev.get(uid,{}).get('contributed',0)
            new_c=info['contributed']
            if new_c-old_c>=CONTRIBUTION_THRESHOLD:
                xp_jumps.add(uid)
                print(f"{now} - XP threshold met for {uid} (diff: {new_c-old_c} >= {CONTRIBUTION_THRESHOLD})",flush=True)

        # 9: Validate via XP jump
        for raid,queues in self.raid_participants.items():
            for uid in list(queues['unvalidated']):
                if uid in xp_jumps:
                    info=queues['unvalidated'].pop(uid)
                    queues['validated'][uid]=info
                    print(f"{now} - VALIDATED {info['name']} for {raid} via XP jump",flush=True)

        # 10: Validate via contrib diff
        for raid,queues in self.raid_participants.items():
            for uid,info in list(queues['unvalidated'].items()):
                base=info['baseline_contrib']
                curr=new_data[uid]['contributed']
                if curr-base>=CONTRIBUTION_THRESHOLD:
                    queues['validated'][uid]=info
                    queues['unvalidated'].pop(uid)
                    print(f"{now} - VALIDATED {info['name']} for {raid} (contrib diff: {curr-base} >= {CONTRIBUTION_THRESHOLD})",flush=True)

        # 11: Announce raids
        for raid in self.RAID_NAMES:
            vals=self.raid_participants[raid]['validated']
            if len(vals)>=4:
                group=set(list(vals)[:4])
                await self._announce_raid(raid,group,guild,db)
                for uid in group: vals.pop(uid)

        # 12: Persist
        self.previous_data=new_data
        self._save_json("previous_data.json",new_data)
        self.cold_start=False

        # Write constantly-updating snapshot for "today"
        # Build pf_map from the results list we already have
        pf_map = {m['uuid']: m for m in results if isinstance(m, dict)}
        # rank_map already exists as curr_map -> {'uuid': {'name':..., 'rank':...}}
        rank_map = {u: info.get('rank') for u, info in curr_map.items()}
        # offload the DB‐heavy snapshot to a thread
        await asyncio.to_thread(
            self._write_current_snapshot,
            db, guild, contrib_map, rank_map, pf_map
        )

        db.close()
        
        print(f"ENDING LOOP - {datetime.datetime.now(timezone.utc)}",flush=True)

    @tasks.loop(time=dtime(hour=0, minute=1, tzinfo=timezone.utc))
    async def daily_activity_snapshot(self):
        print("Starting daily activity snapshot", flush=True)
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

        # 3: write out json, keeping only last 60 days
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
