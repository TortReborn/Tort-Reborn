import json
import os
import datetime
from discord.ext import commands
from discord.commands import SlashCommandGroup, Option
import discord

from Helpers.classes import Guild, DB
from Helpers.functions import getNameFromUUID
from Helpers.variables import te

GUILD_ID = te
BLACKLIST_FILE    = "aspect_blacklist.json"
DISTRIBUTION_FILE = "aspect_distribution.json"
PLAYER_ACTIVITY   = "player_activity.json"
WEEKLY_THRESHOLD  = 5  # hours

class AspectDistribution(commands.Cog):
    aspects = SlashCommandGroup(
        "aspects",
        "Manage aspect distribution",
        guild_ids=[GUILD_ID]
    )
    blacklist = aspects.create_subgroup("blacklist", "Manage aspect distribution blacklist")

    def __init__(self, client):
        self.client = client
        if not os.path.exists(BLACKLIST_FILE):
            with open(BLACKLIST_FILE, "w") as f:
                json.dump({"blacklist": []}, f, indent=2)
        if not os.path.exists(DISTRIBUTION_FILE):
            with open(DISTRIBUTION_FILE, "w") as f:
                json.dump({"queue": [], "marker": 0}, f, indent=2)

    def load_json(self, path, default):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except:
            return default

    def save_json(self, path, data):
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def get_weekly_playtime(self, uuid: str) -> float:
        data = self.load_json(PLAYER_ACTIVITY, [])
        if not data:
            return 0.0
        recent = next((m.get("playtime", 0.0) for m in data[0]["members"] if m["uuid"] == uuid), 0.0)
        idx = 7 if len(data) > 7 else len(data) - 1
        older = next((m.get("playtime", 0.0) for m in data[idx]["members"] if m["uuid"] == uuid), 0.0)
        delta = recent - older
        return delta if delta > 0 else 0.0

    def rebuild_queue(self):
        dist = self.load_json(DISTRIBUTION_FILE, {"queue": [], "marker": 0})
        old_queue = dist.get("queue", [])
        old_marker = dist.get("marker", 0)

        bl = self.load_json(BLACKLIST_FILE, {"blacklist": []})["blacklist"]
        guild = Guild("The Aquarium")

        # current UTC time
        now = datetime.datetime.now(datetime.timezone.utc)

        # index members by UUID
        member_info = {m["uuid"]: m for m in guild.all_members}
        guild_order = list(member_info.keys())

        eligible = []
        for uuid in guild_order:
            if uuid in bl:
                continue

            info = member_info[uuid]
            joined_str = info.get("joined")
            if not joined_str:
                continue

            # parse "2024-08-20T21:20:22.426000Z" → datetime with UTC
            joined_dt = datetime.datetime.fromisoformat(joined_str.replace("Z", "+00:00"))
            if (now - joined_dt) < datetime.timedelta(days=7):
                continue

            if self.get_weekly_playtime(uuid) < WEEKLY_THRESHOLD:
                continue

            eligible.append(uuid)

        # find new_marker in the new eligible queue
        new_marker = 0
        if 0 <= old_marker < len(old_queue):
            last_uuid = old_queue[old_marker]
            if last_uuid in eligible:
                new_marker = eligible.index(last_uuid)
            elif last_uuid in guild_order:
                start = guild_order.index(last_uuid)
                for i in range(1, len(guild_order)):
                    cand = guild_order[(start + i) % len(guild_order)]
                    if cand in eligible:
                        new_marker = eligible.index(cand)
                        break

        dist["queue"] = eligible
        dist["marker"] = new_marker
        self.save_json(DISTRIBUTION_FILE, dist)
        return dist



    @aspects.command(
        name="distribute",
        description="Given N aspects, pick next members in queue to receive them"
    )
    async def distribute(
        self,
        ctx: discord.ApplicationContext,
        amount: Option(int, "Number of aspects to distribute")
    ):
        await ctx.defer()
        dist = self.rebuild_queue()
        queue = dist["queue"]
        remaining = amount
        db = DB(); db.connect()
        recipients = []

        # first redeem any uncollected_aspects
        db.cursor.execute(
            "SELECT uuid, uncollected_aspects FROM uncollected_raids WHERE uncollected_aspects > 0"
        )
        for uuid, acount in db.cursor.fetchall():
            if remaining <= 0:
                break
            take = min(remaining, acount)
            if take > 0:
                db.cursor.execute(
                    "UPDATE uncollected_raids SET uncollected_aspects = uncollected_aspects - %s WHERE uuid = %s",
                    (take, uuid)
                )
                recipients.extend([uuid] * take)
                remaining -= take
        db.connection.commit()

        # then distribute the rest from the queue
        if remaining > 0 and queue:
            start = dist["marker"]
            for i in range(remaining):
                recipients.append(queue[(start + i) % len(queue)])
            dist["marker"] = (start + remaining) % len(queue)
            self.save_json(DISTRIBUTION_FILE, dist)

        # lookup IGNs
        igns = []
        for uuid in recipients:
            db.cursor.execute("SELECT ign FROM discord_links WHERE uuid = %s", (uuid,))
            row = db.cursor.fetchone()
            if row and row[0]:
                igns.append(row[0])
            else:
                raw = getNameFromUUID(uuid)
                igns.append(raw[0] if isinstance(raw, list) and raw else str(raw))
        db.close()

        mention_list = "\n".join(f"- {n}" for n in igns)
        await ctx.followup.send(
            f"🏅 Distributed **{len(recipients)}** aspect(s) to:\n{mention_list}"
        )

    @blacklist.command(
        name="add",
        description="Add someone to the aspect blacklist"
    )
    async def blacklist_add(
        self,
        ctx: discord.ApplicationContext,
        user: Option(discord.Member, "Member to blacklist")
    ):
        await ctx.defer()
        db = DB(); db.connect()
        db.cursor.execute("SELECT uuid FROM discord_links WHERE discord_id = %s", (user.id,))
        row = db.cursor.fetchone()
        db.close()
        if not row:
            return await ctx.followup.send("❌ That user has no linked game UUID.")
        uuid = row[0]
        data = self.load_json(BLACKLIST_FILE, {"blacklist": []})
        bl = data["blacklist"]
        if uuid in bl:
            return await ctx.followup.send("✅ Already blacklisted.")
        bl.append(uuid)
        self.save_json(BLACKLIST_FILE, {"blacklist": bl})
        await ctx.followup.send(f"✅ Added **{user.display_name}** to the aspect blacklist.")

    @blacklist.command(
        name="remove",
        description="Remove someone from the aspect blacklist"
    )
    async def blacklist_remove(
        self,
        ctx: discord.ApplicationContext,
        user: Option(discord.Member, "Member to un‐blacklist")
    ):
        await ctx.defer()
        db = DB(); db.connect()
        db.cursor.execute("SELECT uuid FROM discord_links WHERE discord_id = %s", (user.id,))
        row = db.cursor.fetchone()
        db.close()
        if not row:
            return await ctx.followup.send("❌ That user has no linked game UUID.")
        uuid = row[0]
        data = self.load_json(BLACKLIST_FILE, {"blacklist": []})
        bl = data["blacklist"]
        if uuid not in bl:
            return await ctx.followup.send("✅ User wasn’t on the blacklist.")
        bl.remove(uuid)
        self.save_json(BLACKLIST_FILE, {"blacklist": bl})
        await ctx.followup.send(f"✅ Removed **{user.display_name}** from the aspect blacklist.")

    @commands.Cog.listener()
    async def on_ready(self):
        print('Aspects command loaded')

def setup(client):
    client.add_cog(AspectDistribution(client))
