import json
import os
import io
import aiohttp
import asyncio
import datetime
from math import ceil
from discord.ext import commands
from discord.commands import SlashCommandGroup, Option
import discord
from PIL import Image, ImageDraw, ImageFont

from Helpers.classes import Guild, DB
from Helpers.functions import getNameFromUUID
from Helpers.variables import te, guilds

GUILD_ID = te
BLACKLIST_FILE    = "aspect_blacklist.json"
DISTRIBUTION_FILE = "aspect_distribution.json"
PLAYER_ACTIVITY   = "player_activity.json"
AVATAR_CACHE_FILE = "avatar_cache.json"
AVATAR_CACHE_DIR  = "avatar_cache"
WEEKLY_THRESHOLD  = 5    # hours
MAX_COLUMNS       = 4
ROWS_PER_COLUMN   = 10
CELL_WIDTH        = 205  # per column width
PADDING           = 5
AVATAR_SIZE       = 28
LINE_SPACING      = 8

class AspectDistribution(commands.Cog):
    aspects = SlashCommandGroup(
        "aspects", "Manage aspect distribution", guild_ids=[GUILD_ID, guilds[0]]
    )
    blacklist = aspects.create_subgroup("blacklist", "Manage aspect distribution blacklist")

    def __init__(self, client):
        self.client = client
        os.makedirs(AVATAR_CACHE_DIR, exist_ok=True)
        for path, default in [
            (BLACKLIST_FILE, {"blacklist": []}),
            (DISTRIBUTION_FILE, {"queue": [], "marker": 0}),
            (AVATAR_CACHE_FILE, {})
        ]:
            if not os.path.exists(path):
                with open(path, "w") as f:
                    json.dump(default, f, indent=2)

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
        recent = next((m.get("playtime",0) for m in data[0]["members"] if m["uuid"]==uuid),0)
        older = next((m.get("playtime",0) for m in data[min(7,len(data)-1)]["members"] if m["uuid"]==uuid),0)
        return max(0.0, recent - older)

    def rebuild_queue(self):
        dist = self.load_json(DISTRIBUTION_FILE, {"queue": [], "marker": 0})
        old_q = dist.get("queue", [])
        old_m = dist.get("marker", 0)

        blacklist = set(self.load_json(BLACKLIST_FILE, {"blacklist": []})["blacklist"])
        guild = Guild("The Aquarium")
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)

        # 1) Rebuild in the exact same order as guild.all_members:
        eligible = []
        for m in guild.all_members:
            u = m["uuid"]
            joined = m.get("joined")
            if not joined or u in blacklist:
                continue
            try:
                dt = datetime.datetime.fromisoformat(joined.replace("Z", "+00:00"))
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            except:
                continue
            if dt > cutoff or self.get_weekly_playtime(u) < WEEKLY_THRESHOLD:
                continue
            eligible.append(u)

        # prune avatar cache for those no longer eligible
        cache = self.load_json(AVATAR_CACHE_FILE, {})
        changed = False
        for u, fn in list(cache.items()):
            if u not in eligible:
                path = os.path.join(AVATAR_CACHE_DIR, fn)
                if os.path.exists(path):
                    os.remove(path)
                del cache[u]
                changed = True
        if changed:
            self.save_json(AVATAR_CACHE_FILE, cache)

        # 2) Preserve old marker by UUID, or advance to the next eligible
        new_m = 0
        old_uuid = None
        if 0 <= old_m < len(old_q):
            old_uuid = old_q[old_m]

        if old_uuid:
            if old_uuid in eligible:
                new_m = eligible.index(old_uuid)
            else:
                # find their position in the full guild list, then pick the next eligible after them
                all_ids = [m["uuid"] for m in guild.all_members]
                try:
                    old_idx = all_ids.index(old_uuid)
                except ValueError:
                    old_idx = None

                if old_idx is not None:
                    for idx, u in enumerate(eligible):
                        if all_ids.index(u) > old_idx:
                            new_m = idx
                            break
                    # if none is after them, new_m stays 0 (wrap)

        dist["queue"] = eligible
        dist["marker"] = new_m
        self.save_json(DISTRIBUTION_FILE, dist)
        return dist


    async def get_avatar(self, uuid: str) -> bytes:
        cache = self.load_json(AVATAR_CACHE_FILE, {})
        if uuid in cache:
            path = os.path.join(AVATAR_CACHE_DIR, cache[uuid])
            if os.path.exists(path):
                return open(path,'rb').read()
        url = f"https://crafatar.com/avatars/{uuid}?size=64&overlay"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.read()
        fn = f"{uuid}.png"
        with open(os.path.join(AVATAR_CACHE_DIR, fn),'wb') as f: f.write(data)
        cache[uuid] = fn
        self.save_json(AVATAR_CACHE_FILE, cache)
        return data

    def make_distribution_image(self, avatar_bytes_list, names_list):
        title_font = ImageFont.truetype("arial.ttf", size=20)
        text_font  = ImageFont.truetype("arial.ttf", size=16)
        line_h = max(AVATAR_SIZE, text_font.getbbox("Ay")[3] - text_font.getbbox("Ay")[1]) + LINE_SPACING

        total = len(names_list)
        cols = min(MAX_COLUMNS, ceil(total/ROWS_PER_COLUMN))
        rows = min(total, ROWS_PER_COLUMN)

        img_w = cols * CELL_WIDTH
        img_h = PADDING*2 + line_h*(1 + rows)  # title + rows

        img = Image.new("RGBA", (img_w, img_h), (54,57,63,255))
        draw = ImageDraw.Draw(img)
        draw.rectangle([(0,0),(img_w-1,img_h-1)], outline=(26,115,232), width=3)

        y = PADDING
        title = "Aspect Distribution"
        tw = title_font.getbbox(title)[2]
        draw.text(((img_w - tw)//2, y), title, font=title_font, fill=(255,255,255))
        y += line_h

        for idx, (av_b, name) in enumerate(zip(avatar_bytes_list, names_list), start=1):
            col = (idx-1)//ROWS_PER_COLUMN
            row = (idx-1)%ROWS_PER_COLUMN
            x = col * CELL_WIDTH + PADDING
            y_pos = y + row * line_h
            av_img = Image.open(io.BytesIO(av_b)).convert("RGBA").resize((AVATAR_SIZE,AVATAR_SIZE))
            img.paste(av_img, (x,y_pos), av_img)
            line = f"{idx}. {name}"
            text_y = y_pos + (AVATAR_SIZE - text_font.getbbox(line)[3])//2
            draw.text((x + AVATAR_SIZE + 10, text_y), line, font=text_font, fill=(255,255,255))

        buf = io.BytesIO()
        img.save(buf,format="PNG")
        buf.seek(0)
        return buf

    @aspects.command(
        name="distribute",
        description="Given N aspects, pick next members in queue to receive them"
    )
    async def distribute(self, ctx: discord.ApplicationContext, amount: Option(int, "Number of aspects to distribute")):
        await ctx.defer()

        # 1 rebuild & grab queue + start position
        dist  = self.rebuild_queue()
        queue = dist["queue"]
        start = dist["marker"]

        # 2 drain DB for uncollected aspects
        remaining  = amount
        db         = DB(); db.connect()
        recipients = []
        db.cursor.execute("SELECT uuid,uncollected_aspects FROM uncollected_raids WHERE uncollected_aspects>0")
        for u, c in db.cursor.fetchall():
            if remaining <= 0:
                break
            take = min(remaining, c)
            if take > 0:
                recipients += [u] * take
                remaining -= take
                db.cursor.execute(
                    "UPDATE uncollected_raids SET uncollected_aspects = uncollected_aspects - %s WHERE uuid = %s",
                    (take, u)
                )
        db.connection.commit()
        db.close()

        # 3 fill from the rotation, cycling the queue as needed
        if remaining > 0 and queue:
            for i in range(remaining):
                idx = (start + i) % len(queue)
                u   = queue[idx]
                recipients.append(u)

        # 4 build names list one-to-one
        guild = Guild("The Aquarium")
        names = []
        for u in recipients:
            member = next((m for m in guild.all_members if m["uuid"] == u), None)
            if member:
                names.append(member["name"])
            else:
                looked_up = await getNameFromUUID(u)
                name = (looked_up[0] if isinstance(looked_up, (list, tuple)) else str(looked_up))
                names.append(name)

        # 5 fetch avatars in parallel
        avatars = await asyncio.gather(*(self.get_avatar(u) for u in recipients))

        # 6 update the marker by how many you actually displayed
        displayed = len(recipients)
        if queue:
            new_marker = (start + displayed) % len(queue)
            dist["marker"] = new_marker
            self.save_json(DISTRIBUTION_FILE, dist)

        # 7 render & send
        buf = self.make_distribution_image(avatars, names)
        await ctx.followup.send(file=discord.File(buf, "distribution.png"))


    @blacklist.command(
        name="add",
        description="Add someone to the aspect blacklist"
    )
    async def blacklist_add(self, ctx, user: Option(discord.Member, "Member to blacklist")):
        await ctx.defer(); db=DB(); db.connect()
        db.cursor.execute("SELECT uuid FROM discord_links WHERE discord_id=%s",(user.id,))
        row=db.cursor.fetchone(); db.close()
        if not row: return await ctx.followup.send("❌ That user has no linked game UUID.")
        uuid=row[0]; data=self.load_json(BLACKLIST_FILE,{"blacklist":[]})
        bl=data["blacklist"]
        if uuid in bl: return await ctx.followup.send("✅ Already blacklisted.")
        bl.append(uuid); self.save_json(BLACKLIST_FILE,{"blacklist":bl})
        await ctx.followup.send(f"✅ Added **{user.display_name}** to the aspect blacklist.")

    @blacklist.command(
        name="remove",
        description="Remove someone from the aspect blacklist"
    )
    async def blacklist_remove(self, ctx, user: Option(discord.Member, "Member to un‐blacklist")):
        await ctx.defer(); db=DB(); db.connect()
        db.cursor.execute("SELECT uuid FROM discord_links WHERE discord_id=%s",(user.id,))
        row=db.cursor.fetchone(); db.close()
        if not row: return await ctx.followup.send("❌ That user has no linked game UUID.")
        uuid=row[0]; data=self.load_json(BLACKLIST_FILE,{"blacklist":[]})
        bl=data["blacklist"]
        if uuid not in bl: return await ctx.followup.send("✅ User wasn’t on the aspect blacklist.")
        bl.remove(uuid); self.save_json(BLACKLIST_FILE,{"blacklist":bl})
        await ctx.followup.send(f"✅ Removed **{user.display_name}** from the aspect blacklist.")

    @commands.Cog.listener()
    async def on_ready(self):
        print('Aspects command loaded')


def setup(client):
    client.add_cog(AspectDistribution(client))
