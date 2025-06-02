import asyncio
import json
import os
import discord
import time
import datetime
import traceback
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
        self.previous_data = self._load_json("previous_data.json", {})
        self.member_file = "member_list.json"
        self.previous_members = self._load_json("member_list.json", {})
        self.member_file_exists = os.path.exists(self.member_file)
        self.raid_participants = {raid: {} for raid in self.RAID_NAMES}
        self.cold_start = True

    def _load_json(self, path, default):
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    return json.load(f)
        except Exception:
            print(f"[UpdateMemberData] Error loading {path}:")
            traceback.print_exc()
        return default

    def _save_json(self, path, data):
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception:
            print(f"[UpdateMemberData] Error saving {path}:")
            traceback.print_exc()
        return False

    def _make_progress_bar(self, percent: int, length: int = 20) -> str:
        filled = int(length * percent / 100)
        bar = "█" * filled + "─" * (length - filled)
        return f"[{bar}]"

    async def _announce_raid(self, raid, group, guild, db):
        try:
            participants = self.raid_participants[raid]
            names = [participants[uid]["name"] for uid in group]
            bolded = [f"**{n}**" for n in names]
            names_str = ", ".join(bolded[:-1]) + ", and " + bolded[-1] if len(bolded) > 1 else bolded[0]
            emoji = RAID_EMOJIS.get(raid, "")
            now = datetime.datetime.now(timezone.utc)
            print(f"{now} - ANNOUNCE: {names_str} completed {raid}")

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
                field_name = f"{guild.name} — Level {guild_level}" if guild_level is not None else "Progress"
                percent = guild_xp if guild_xp is not None else 100
                embed.add_field(
                    name=field_name,
                    value=self._make_progress_bar(percent) + (f" ({percent}%)" if guild_xp is not None else ""),
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
                            ign = EXCLUDED.ign;
                    """,
                    (uid, ign)
                )
            db.connection.commit()
        except Exception:
            print("[UpdateMemberData] Error in _announce_raid:")
            traceback.print_exc()

    @tasks.loop(minutes=5)
    async def update_member_data(self):
        now = datetime.datetime.now(timezone.utc)
        print(f"STARTING LOOP - {now}")
        db = None
        try:
            db = DB()
            db.connect()
            guild = Guild("The Aquarium")

            contribs = {m['uuid']: m['contributed'] for m in guild.all_members}

            cutoff = now - GUILD_TTL
            for raid, parts in list(self.raid_participants.items()):
                for uid, info in list(parts.items()):
                    try:
                        first_seen = info['first_seen'] if isinstance(info['first_seen'], datetime.datetime) else datetime.datetime.fromisoformat(info['first_seen'])
                        if first_seen < cutoff and not info.get('validated', False):
                            print(f"{now} - PRUNE: {info['name']} ({uid}) from {raid}")
                            parts.pop(uid)
                    except Exception:
                        print(f"[UpdateMemberData] Error pruning {raid} participant {uid}:")
                        traceback.print_exc()

            # joins/leaves
            try:
                prev_map = self.previous_members
                curr_map = {m['uuid']: {'name': m['name'], 'rank': m.get('rank')} for m in guild.all_members}
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
                        embed_join = discord.Embed(title="Guild Members Joined", timestamp=now, color=0x00FF00)
                        join_names = [getNameFromUUID(uid)[0] if isinstance(getNameFromUUID(uid), list) else str(getNameFromUUID(uid)) for uid in joined]
                        add_chunked(embed_join, "Joined", join_names)
                        await ch.send(embed=embed_join)
                    if left:
                        embed_leave = discord.Embed(title="Guild Members Left", timestamp=now, color=0xFF0000)
                        leave_names = [prev_map[uid]['name'] for uid in left]
                        add_chunked(embed_leave, "Left", leave_names)
                        await ch.send(embed=embed_leave)
                self.previous_members = curr_map
            except Exception:
                print("[UpdateMemberData] Error handling joins/leaves:")
                traceback.print_exc()

            # rank changes
            try:
                role_changes = []
                for uuid, info in curr_map.items():
                    if uuid in prev_map and prev_map[uuid].get('rank') != info['rank']:
                        role_changes.append((uuid, info['name'], prev_map[uuid]['rank'], info['rank']))
                if role_changes and not (self.cold_start and not self.member_file_exists):
                    ch = self.client.get_channel(LOG_CHANNEL)
                    embed_rank = discord.Embed(title="Guild Rank Changes", timestamp=now, color=0x0000FF)
                    for _, name, old, new in role_changes:
                        embed_rank.add_field(name=name, value=f"{old} → {new}", inline=False)
                    await ch.send(embed=embed_rank)
            except Exception:
                print("[UpdateMemberData] Error handling rank changes:")
                traceback.print_exc()

            # presence
            try:
                self._save_json(self.member_file, curr_map)
                await self.client.change_presence(activity=discord.CustomActivity(name=f"{guild.online} members online"))
            except Exception:
                print("[UpdateMemberData] Error updating presence:")
                traceback.print_exc()

            # activity tracking & detection
            t = int(time.time())
            memberlist = {'time': t, 'members': []}
            prev_data = self.previous_data
            new_data = {}
            for member in guild.all_members:
                try:
                    m = getPlayerDatav3(member['uuid'])
                    if not isinstance(m, dict):
                        continue
                    uuid = m['uuid']
                    name = m['username']
                    raids = m.get('globalData', {}).get('raids', {}).get('list', {})
                    new_counts = {r: raids.get(r, 0) for r in self.RAID_NAMES}
                    new_data[uuid] = {'raids': new_counts}
                    if not self.cold_start:
                        old_counts = prev_data.get(uuid, {'raids': {r: 0 for r in self.RAID_NAMES}})['raids']
                        for raid in self.RAID_NAMES:
                            diff = new_counts[raid] - old_counts.get(raid, 0)
                            if 0 < diff < 3:
                                parts = self.raid_participants[raid]
                                if uuid not in parts:
                                    parts[uuid] = {
                                        'name': name,
                                        'first_seen': now,
                                        'baseline_contrib': contribs.get(uuid, 0),
                                        'validated': False
                                    }
                                    print(f"{now} - DETECT: {name} in {raid}")
                except Exception:
                    print(f"[UpdateMemberData] Error detecting raid for member {member}:")
                    traceback.print_exc()

                # full activity
                if (t % 86400) < 300:
                    try:
                        db.cursor.execute(
                            """SELECT dl.ign, COALESCE(s.shells,0) FROM discord_links dl
                               LEFT JOIN shells s ON dl.discord_id=s.user
                               WHERE dl.uuid=%s""", (uuid,)
                        )
                        res = db.cursor.fetchone()
                        shells = res[1] if res else 0
                        db.cursor.execute(
                            """SELECT COALESCE(ur.uncollected_raids,0), COALESCE(ur.collected_raids,0) FROM discord_links dl
                               LEFT JOIN uncollected_raids ur ON dl.uuid=ur.uuid
                               WHERE dl.uuid=%s""", (uuid,)
                        )
                        res = db.cursor.fetchone()
                        raids_total = sum(res) if res else 0
                        memberlist['members'].append({
                            'name': name, 'uuid': uuid, 'rank': member.get('rank'),
                            'playtime': m.get('playtime'), 'contributed': m.get('contributed'),
                            'wars': m.get('globalData',{}).get('wars'), 'raids': raids_total, 'shells': shells
                        })
                    except Exception:
                        print(f"[UpdateMemberData] Error tracking activity for {uuid}:")
                        traceback.print_exc()

            # filter/validate & announce
            for raid, parts in list(self.raid_participants.items()):
                try:
                    # validate
                    for uid, info in list(parts.items()):
                        if not info.get('validated', False):
                            increase = contribs.get(uid, 0) - info['baseline_contrib']
                            if increase >= CONTRIBUTION_THRESHOLD:
                                info['validated'] = True
                            else:
                                parts.pop(uid)
                    # announce
                    valids = [uid for uid, inf in parts.items() if inf['validated']]
                    if len(valids) >= 4:
                        group = set(valids[:4])
                        print(f"{now} - ANNOUNCING group {group} for {raid}")
                        await self._announce_raid(raid, group, guild, db)
                        for uid in group:
                            parts.pop(uid)
                except Exception:
                    print(f"[UpdateMemberData] Error filtering/announcing for raid {raid}:")
                    traceback.print_exc()

            self.previous_data = new_data
            self._save_json("previous_data.json", new_data)

            if self.cold_start:
                print(f"{now} - Ending cold start")
                self.cold_start = False
            print(f"ENDING LOOP - {datetime.datetime.now(timezone.utc)}")
        except Exception:
            print(f"[UpdateMemberData] Fatal error in update_member_data:")
            traceback.print_exc()
        finally:
            if db:
                try:
                    db.close()
                except Exception:
                    print("[UpdateMemberData] Error closing DB:")
                    traceback.print_exc()

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
