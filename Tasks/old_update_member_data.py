import asyncio
import json
import os
import discord
import time
import datetime
from datetime import timezone, timedelta
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
        self.previous_data = self._load_json("previous_data.json", {})
        self.member_file = "member_list.json"
        self.previous_members = self._load_json("member_list.json", {})
        self.member_file_exists = os.path.exists(self.member_file)
        # track users: raid_name -> { server: { uuid: {"name": str, "first_seen": datetime, "server":str} } }
        self.raid_participants = {raid: {} for raid in self.RAID_NAMES}
        self.cold_start = True

    def _load_json(self, path, default):
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[UpdateMemberData] Failed to load {path}: {e}")
        return default

    def _save_json(self, path, data):
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[UpdateMemberData] Failed to save {path}: {e}")

    def _make_progress_bar(self, percent: int, length: int = 20) -> str:
        filled = int(length * percent / 100)
        bar = "█" * filled + "─" * (length - filled)
        return f"[{bar}]"

    async def _announce_raid(self, raid, group, server, guild, db):
        participants = self.raid_participants[raid][server]
        names = [participants[uid]["name"] for uid in group]
        bolded = [f"**{n}**" for n in names]
        if len(bolded) > 1:
            *first, last = bolded
            names_str = ", ".join(first) + ", and " + last
        else:
            names_str = bolded[0]

        emoji = RAID_EMOJIS.get(raid, "")
        now = datetime.datetime.now(timezone.utc)
        print(f"{now} - ANNOUNCE: {names_str} completed {raid}")

        channel = self.client.get_channel(RAID_ANNOUNCE_CHANNEL_ID)
        if channel:
            guild_level = getattr(guild, "level", None)
            guild_xp = getattr(guild, "xpPercent", None)

            title = f"{emoji} {raid} Completed!"
            footer = "Guild Raid Tracker"

            embed = discord.Embed(
                title=title,
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

            embed.set_footer(text=footer)
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
                    SET
                    uncollected_raids = ur.uncollected_raids + EXCLUDED.uncollected_raids,
                    ign               = EXCLUDED.ign;
                """,
                (uid, ign)
            )
        db.connection.commit()


    @tasks.loop(minutes=5)
    async def update_member_data(self):
        now = datetime.datetime.now(timezone.utc)
        print(f"STARTING LOOP - {now}")
        if self.cold_start:
            print(f"{now} - Starting member tracking (cold start)")

        # Prune stale participants per server
        cutoff = now - GUILD_TTL
        for raid, server_queues in self.raid_participants.items():
            for server, parts in list(server_queues.items()):
                for uid, info in list(parts.items()):
                    first_seen = (
                        datetime.datetime.fromisoformat(info["first_seen"])
                        if isinstance(info["first_seen"], str)
                        else info["first_seen"]
                    )
                    if first_seen < cutoff:
                        print(f"{now} - PRUNE: {info['name']} ({uid}) from {raid} on {server}")
                        parts.pop(uid)
                # remove empty server-queue
                if not parts:
                    server_queues.pop(server)

        db = None
        try:
            db = DB()
            db.connect()
            guild = Guild("The Aquarium")

            prev_map = self.previous_members
            curr_map = {m["uuid"]: {"name": m["name"], "rank": m.get("rank")} for m in guild.all_members}

            joined = set(curr_map) - set(prev_map)
            left = set(prev_map) - set(curr_map)

            if (joined or left) and not (self.cold_start and not self.member_file_exists):
                ch = self.client.get_channel(LOG_CHANNEL)

                def add_chunked(embed_obj, field_name, name_list):
                    chunk = ""
                    for ign in name_list:
                        piece = f"**{ign}**" if not chunk else f", **{ign}**"
                        if len(chunk) + len(piece) > 1024:
                            embed_obj.add_field(name=field_name, value=chunk, inline=False)
                            chunk = f"**{ign}**"
                            field_name += " cont."
                        else:
                            chunk += piece
                    if chunk:
                        embed_obj.add_field(name=field_name, value=chunk, inline=False)

                joined_igns = []
                for uid in joined:
                    raw = getNameFromUUID(uid)
                    ign = raw[0] if isinstance(raw, list) and raw else str(raw)
                    joined_igns.append(ign)

                left_igns = [
                    prev_map[uid]["name"] if isinstance(prev_map[uid], dict) else prev_map[uid]
                    for uid in left
                ]

                if joined_igns:
                    embed_join = discord.Embed(
                        title="Guild Members Joined",
                        timestamp=now,
                        color=0x00FF00
                    )
                    add_chunked(embed_join, "Joined", joined_igns)
                    await ch.send(embed=embed_join)

                if left_igns:
                    embed_leave = discord.Embed(
                        title="Guild Members Left",
                        timestamp=now,
                        color=0xFF0000
                    )
                    add_chunked(embed_leave, "Left", left_igns)
                    await ch.send(embed=embed_leave)

            role_changes = []
            for uuid, info in curr_map.items():
                if uuid in prev_map and isinstance(prev_map[uuid], dict):
                    old_rank = prev_map[uuid].get("rank")
                    new_rank = info.get("rank")
                    if old_rank and new_rank and old_rank != new_rank:
                        role_changes.append((uuid, info["name"], old_rank, new_rank))

            if role_changes and not (self.cold_start and not self.member_file_exists):
                ch = self.client.get_channel(LOG_CHANNEL)
                embed2 = discord.Embed(
                    title="Guild Rank Changes",
                    timestamp=now,
                    color=0x0000FF
                )
                for _, name, old_rank, new_rank in role_changes:
                    embed2.add_field(name=name, value=f"{old_rank} → {new_rank}", inline=False)
                await ch.send(embed=embed2)

            self.previous_members = curr_map
            self._save_json(self.member_file, curr_map)

            await self.client.change_presence(
                activity=discord.CustomActivity(name=f"{guild.online} members online")
            )

            t = int(time.mktime(datetime.datetime.now().timetuple()))
            memberlist = {'time': t, 'members': []}

            new_data = {}
            # Build up new_data with server included
            for member in guild.all_members:
                m = getPlayerDatav3(member["uuid"])
                if not isinstance(m, dict):
                    continue

                uuid = m["uuid"]
                name = m["username"]
                server = m.get("server")
                raids = m.get("globalData", {}).get("raids", {}).get("list", {})

                new_data[uuid] = {
                    "raids": {r: raids.get(r, 0) for r in self.RAID_NAMES},
                    "server": server
                }

                if not self.cold_start:
                    old = self.previous_data.get(uuid, {"raids": {r: 0 for r in self.RAID_NAMES}})
                    for raid in self.RAID_NAMES:
                        new_count = new_data[uuid]["raids"][raid]
                        old_count = old["raids"].get(raid, 0)
                        diff = new_count - old_count

                        if 0 < diff < 3:
                            # get (or create) the queue for this raid+server
                            server_queues = self.raid_participants[raid]
                            if server not in server_queues:
                                server_queues[server] = {}
                            parts = server_queues[server]

                            # add first sighting
                            if uuid not in parts:
                                parts[uuid] = {"name": name, "first_seen": now, "server": server}
                                print(f"{now} - DETECT: {name} in {raid} on server {server}")

                            # once we have 4 from the same server, announce & clear that server’s queue
                            if len(parts) == 4:
                                group = frozenset(parts.keys())
                                print(f"{now} - ANNOUNCING group {group} for {raid} on {server}")
                                await self._announce_raid(raid, group, server, guild, db)
                                server_queues[server] = {}

                # Full Activity Tracking
                if (t % 86400) < 300:
                    db.cursor.execute(
                        '''SELECT discord_links.ign, COALESCE(shells.shells, 0) AS shells 
                           FROM discord_links
                           LEFT JOIN shells ON discord_links.discord_id = shells.user
                           WHERE discord_links.uuid = %s''',
                        (m["uuid"],)
                    )
                    res = db.cursor.fetchone()
                    if res:
                        shells = res[1]
                    else:
                        shells = 0
                    db.cursor.execute(
                        '''SELECT 
                               COALESCE(uncollected_raids.uncollected_raids, 0),
                               COALESCE(uncollected_raids.collected_raids, 0)
                           FROM discord_links
                           LEFT JOIN uncollected_raids ON discord_links.uuid = uncollected_raids.uuid
                           WHERE discord_links.uuid = %s''',
                        (m["uuid"],)
                    )
                    res = db.cursor.fetchone()
                    if res:
                        raids = int(res[0]) + int(res[1])
                    else:
                        raids = 0

                    memberlist['members'].append(
                        {"name": m['username'], "uuid": m['uuid'], "rank": member['rank'],
                         "playtime": m['playtime'], "contributed": member['contributed'],
                         'wars': m['globalData']['wars'], 'raids': raids, 'shells': shells})

                await asyncio.sleep(0.5)

            if (t % 86400) < 300:
                with open("player_activity.json", 'r') as f:
                    old_data = json.loads(f.read())
                old_data.insert(0, memberlist)
                with open("player_activity.json", 'w') as f:
                    json.dump(old_data[:60], f)

            self.previous_data = new_data
            self._save_json("previous_data.json", new_data)

            if self.cold_start:
                print(f"{now} - Ending cold start")
                self.cold_start = False

            now = datetime.datetime.now(timezone.utc)
            print(f"ENDING LOOP - {now}")

        except Exception as e:
            print(f"{now} - [UpdateMemberData] Fatal error: {e}")
        finally:
            if db:
                try:
                    db.close()
                except:
                    pass


    @update_member_data.before_loop
    async def before_update(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        print("UpdateMemberData task loaded")
        if not self.update_member_data.is_running():
            self.update_member_data.start()


def setup(client):
    client.add_cog(UpdateMemberData(client))
