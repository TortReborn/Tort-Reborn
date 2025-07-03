import asyncio
import json
import os
import discord
import datetime
import time
import traceback
from datetime import timezone, timedelta, time as dtime
from discord.ext import tasks, commands

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
CONTRIBUTION_THRESHOLD = 2_000_000_000

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
        self.raid_participants = {raid: {} for raid in self.RAID_NAMES}
        self.cold_start = True
        self._semaphore = asyncio.Semaphore(10)

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
        participants = self.raid_participants[raid]
        names = [participants[uid]["name"] for uid in group]
        bolded = [f"**{n}**" for n in names]
        if len(bolded) > 1:
            names_str = ", ".join(bolded[:-1]) + ", and " + bolded[-1]
        else:
            names_str = bolded[0]
        emoji = RAID_EMOJIS.get(raid, "")
        now = datetime.datetime.now(timezone.utc)
        channel = self.client.get_channel(RAID_ANNOUNCE_CHANNEL_ID)
        if channel:
            guild_level = getattr(guild, "level", None)
            guild_xp = getattr(guild, "xpPercent", None)
            embed = discord.Embed(
                title=f"{emoji} {raid} Completed!",
                description=names_str,
                timestamp=now,
                color=0x00FF00
            )
            if guild_level is not None and guild_xp is not None:
                embed.add_field(
                    name=f"{guild.name} — Level {guild_level}",
                    value=self._make_progress_bar(guild_xp) + f" ({guild_xp}%)",
                    inline=False
                )
            else:
                embed.add_field(
                    name="Progress",
                    value=self._make_progress_bar(100),
                    inline=False
                )
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

    @tasks.loop(minutes=2)  # run every 2 minutes, splitting fetches into two waves
    async def update_member_data(self):
        now = datetime.datetime.now(timezone.utc)
        print(f"STARTING LOOP - {now}")
        db = DB(); db.connect()
        guild = Guild("The Aquarium")

        # Prune stale participants
        cutoff = now - GUILD_TTL
        for raid, parts in list(self.raid_participants.items()):
            for uid, info in list(parts.items()):
                first = info["first_seen"]
                if isinstance(first, str):
                    first = datetime.datetime.fromisoformat(first)
                if first < cutoff and not info.get("validated", False):
                    print(f"{now} - PRUNE: {info['name']} ({uid}) from {raid}")
                    parts.pop(uid)

        # Join/leave detection
        prev_map = self.previous_members
        curr_map = {m["uuid"]: {"name": m["name"], "rank": m.get("rank")} for m in guild.all_members}
        joined = set(curr_map) - set(prev_map)
        left = set(prev_map) - set(curr_map)
        if (joined or left) and not (self.cold_start and not self.member_file_exists):
            ch = self.client.get_channel(LOG_CHANNEL)
            def add_chunked(embed, title, items):
                chunk = ""
                for ign in items:
                    piece = f"**{ign}**" if not chunk else f", **{ign}**"
                    if len(chunk) + len(piece) > 1024:
                        embed.add_field(name=title, value=chunk, inline=False)
                        chunk = f"**{ign}**"
                        title += " cont."
                    else:
                        chunk += piece
                if chunk:
                    embed.add_field(name=title, value=chunk, inline=False)
            if joined:
                join_names = [getNameFromUUID(u)[0] if isinstance(getNameFromUUID(u),list) else str(getNameFromUUID(u)) for u in joined]
                ej = discord.Embed(title="Guild Members Joined", timestamp=now, color=0x00FF00)
                add_chunked(ej, "Joined", join_names); await ch.send(embed=ej)
            if left:
                leave_names = [prev_map[u]["name"] for u in left]
                el = discord.Embed(title="Guild Members Left", timestamp=now, color=0xFF0000)
                add_chunked(el, "Left", leave_names); await ch.send(embed=el)
        self.previous_members = curr_map
        self._save_json(self.member_file, curr_map)

        # Rank changes
        role_changes = [(u, curr_map[u]["name"], prev_map[u]["rank"], curr_map[u]["rank"]) for u in curr_map if u in prev_map and prev_map[u].get("rank") != curr_map[u]["rank"]]
        if role_changes and not (self.cold_start and not self.member_file_exists):
            ch = self.client.get_channel(LOG_CHANNEL)
            er = discord.Embed(title="Guild Rank Changes", timestamp=now, color=0x0000FF)
            for _, name, old, new in role_changes:
                er.add_field(name=name, value=f"{old} → {new}", inline=False)
            await ch.send(embed=er)

        # Presence update
        await self.client.change_presence(activity=discord.CustomActivity(name=f"{guild.online} members online"))

        # Concurrent fetch of player data in two waves to respect rate limits (max 75 requests/min)
        members = guild.all_members
        async def fetch(uuid):
            async with self._semaphore:
                return await asyncio.to_thread(getPlayerDatav3, uuid)

        # First wave (up to 75 members)
        wave1 = members[:75]
        results1 = await asyncio.gather(*(fetch(m["uuid"]) for m in wave1), return_exceptions=True)
        # Pause to avoid exceeding 120 calls/min
        await asyncio.sleep(60)
        # Second wave (remaining members)
        wave2 = members[75:]
        results2 = await asyncio.gather(*(fetch(m["uuid"]) for m in wave2), return_exceptions=True)

        # Combine both waves
        results = results1 + results2

        # Raid detection via XP diff
        prev_saved = self.previous_data
        contribs = {r["uuid"]: r.get("contributed",0) for r in results if isinstance(r,dict)}
        new_data = {}
        for m in results:
            if not isinstance(m, dict):
                continue
            uid, name = m["uuid"], m["username"]
            raids = m.get("globalData",{}).get("raids",{}).get("list",{})
            counts = {r: raids.get(r,0) for r in self.RAID_NAMES}
            cur = m.get("contributed",0)
            new_data[uid] = {"raids":counts, "contributed":cur}
            if not self.cold_start:
                old_counts = prev_saved.get(uid,{}).get("raids",{r:0 for r in self.RAID_NAMES})
                for raid in self.RAID_NAMES:
                    diff = counts[raid] - old_counts.get(raid,0)
                    if 0 < diff < 3 and uid not in self.raid_participants[raid]:
                        base = prev_saved.get(uid,{}).get("contributed",0)
                        self.raid_participants[raid][uid] = {"name":name,"first_seen":now,"baseline_contrib":base,"validated":False}
                        print(f"{now} - DETECT: {name} in {raid}")

        # Validation & announcement
        for raid, parts in list(self.raid_participants.items()):
            for uid, info in list(parts.items()):
                if not info.get("validated") and contribs.get(uid,0) - info["baseline_contrib"] >= CONTRIBUTION_THRESHOLD:
                    parts[uid]["validated"] = True
            valids = [u for u,i in parts.items() if i.get("validated")]
            if len(valids) >= 4:
                group = set(valids[:4])
                await self._announce_raid(raid, group, guild, db)
                for u in group:
                    parts.pop(u)

        self.previous_data = new_data
        self._save_json("previous_data.json", new_data)
        if self.cold_start:
            self.cold_start = False
        print(f"ENDING LOOP - {datetime.datetime.now(timezone.utc)}")

    @tasks.loop(time=dtime(hour=0, minute=1, tzinfo=timezone.utc))
    async def daily_activity_snapshot(self):
        print("Starting daily activity snapshot")
        db = DB(); db.connect()
        guild = Guild("The Aquarium")
        t = int(time.time())
        snapshot = {'time': t, 'members': []}
        members = guild.all_members
        async def fetch(uuid):
            async with self._semaphore:
                return await asyncio.to_thread(getPlayerDatav3, uuid)
        profiles = await asyncio.gather(*(fetch(m["uuid"]) for m in members), return_exceptions=True)
        for m in profiles:
            if not isinstance(m, dict):
                continue
            uuid = m['uuid']
            snapshot['members'].append({
                'name': m['username'],
                'uuid': uuid,
                'playtime': m.get('playtime'),
                'contributed': m.get('contributed'),
                'wars': m.get('globalData',{}).get('wars'),
                'shells': (db.cursor.execute("SELECT COALESCE(s.shells,0) FROM discord_links dl LEFT JOIN shells s ON dl.discord_id=s.user WHERE dl.uuid=%s",(uuid,)), db.cursor.fetchone()[0])[1],
                'raids': sum((db.cursor.execute("SELECT COALESCE(ur.uncollected_raids,0),COALESCE(ur.collected_raids,0) FROM discord_links dl LEFT JOIN uncollected_raids ur ON dl.uuid=ur.uuid WHERE dl.uuid=%s",(uuid,)), db.cursor.fetchone()))
            })
        path = "player_activity.json"
        old = self._load_json(path, [])
        old.insert(0, snapshot)
        with open(path,'w') as f:
            json.dump(old[:60],f,indent=2)
        db.close()
        print("Daily activity snapshot complete")

    @update_member_data.before_loop
    async def before_update(self):
        await self.client.wait_until_ready()

    @daily_activity_snapshot.before_loop
    async def before_snapshot(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if self._has_started:
            return
        self._has_started = True
        if not self.update_member_data.is_running():
            self.update_member_data.start()
        if not self.daily_activity_snapshot.is_running():
            self.daily_activity_snapshot.start()


def setup(client):
    client.add_cog(UpdateMemberData(client))
