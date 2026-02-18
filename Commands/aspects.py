"""
Commands/aspects.py
Aspect distribution commands - now using database storage.
"""

import io
import os
import asyncio
import datetime
from datetime import timezone, timedelta
from math import ceil

import aiohttp
import discord
from discord.ext import commands
from discord.commands import SlashCommandGroup, Option
from PIL import Image, ImageDraw, ImageFont

from Helpers.classes import Guild, DB
from Helpers.functions import getNameFromUUID
from Helpers.variables import te
from Helpers import aspect_db
from Helpers.storage import get_cached_avatar, save_cached_avatar


GUILD_ID = te
MAX_COLUMNS = 4
ROWS_PER_COLUMN = 10
CELL_WIDTH = 205
PADDING = 5
AVATAR_SIZE = 28
LINE_SPACING = 8


class AspectDistribution(commands.Cog):
    aspects = SlashCommandGroup("aspects", "Manage aspect distribution", guild_ids=[GUILD_ID])
    blacklist = aspects.create_subgroup("blacklist", "Manage aspect distribution blacklist")

    def __init__(self, client):
        self.client = client

    def create_default_avatar(self, uuid: str) -> bytes:
        """Create a simple default avatar with a question mark."""
        img = Image.new("RGBA", (64, 64), (100, 100, 100, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle([(0, 0), (63, 63)], outline=(150, 150, 150, 255), width=2)
        
        try:
            font = ImageFont.truetype("images/profile/game.ttf", size=30)
        except Exception:
            font = ImageFont.load_default()
        
        char = "?"
        bbox = draw.textbbox((0, 0), char, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (64 - text_width) // 2
        y = (64 - text_height) // 2
        draw.text((x, y), char, fill=(255, 255, 255, 255), font=font)
        
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf.read()

    async def get_avatar(self, uuid: str) -> bytes:
        # Check R2 cache first
        cached = get_cached_avatar(uuid)
        if cached:
            return cached

        url = f"https://vzge.me/face/64/{uuid}"
        headers = {'User-Agent': os.getenv("visage_UA", "")}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        print(f"Warning: Visage returned status {resp.status} for UUID {uuid}")
                        return None

                    data = await resp.read()

                    if data.startswith(b'<!DOCTYPE') or data.startswith(b'<html'):
                        print(f"Warning: Visage returned HTML error page for UUID {uuid}")
                        return None

                    is_png = data[:8] == b'\x89PNG\r\n\x1a\n'
                    is_jpeg = data[:3] == b'\xff\xd8\xff'

                    if not (is_png or is_jpeg):
                        print(f"Warning: Invalid image data received for UUID {uuid}")
                        return None

            # Cache in R2
            save_cached_avatar(uuid, data)
            return data
        except Exception as e:
            print(f"Warning: Failed to fetch avatar for UUID {uuid}: {e}")
            return None

    def make_distribution_image(self, avatar_bytes_list, names_list):
        title_font = ImageFont.truetype("images/profile/game.ttf", size=20)
        text_font = ImageFont.truetype("images/profile/game.ttf", size=16)
        line_h = max(AVATAR_SIZE, text_font.getbbox("Ay")[3] - text_font.getbbox("Ay")[1]) + LINE_SPACING

        total = len(names_list)
        cols = min(MAX_COLUMNS, ceil(total / ROWS_PER_COLUMN))
        rows = min(total, ROWS_PER_COLUMN)

        img_w = cols * CELL_WIDTH
        img_h = PADDING * 2 + line_h * (1 + rows)

        img = Image.new("RGBA", (img_w, img_h), (54, 57, 63, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle([(0, 0), (img_w - 1, img_h - 1)], outline=(26, 115, 232), width=3)

        y = PADDING
        title = "Aspect Distribution"
        tw = title_font.getbbox(title)[2]
        draw.text(((img_w - tw) // 2, y), title, font=title_font, fill=(255, 255, 255))
        y += line_h

        for idx, (av_b, name) in enumerate(zip(avatar_bytes_list, names_list), start=1):
            col = (idx - 1) // ROWS_PER_COLUMN
            row = (idx - 1) % ROWS_PER_COLUMN
            x = col * CELL_WIDTH + PADDING
            y_pos = y + row * line_h

            try:
                av_img = Image.open(io.BytesIO(av_b)).convert("RGBA").resize((AVATAR_SIZE, AVATAR_SIZE))
                img.paste(av_img, (x, y_pos), av_img)
            except Exception:
                fallback = Image.new("RGBA", (AVATAR_SIZE, AVATAR_SIZE), (80, 80, 80, 255))
                draw_fb = ImageDraw.Draw(fallback)
                draw_fb.rectangle([(0, 0), (AVATAR_SIZE - 1, AVATAR_SIZE - 1)], outline=(120, 120, 120, 255))
                img.paste(fallback, (x, y_pos), fallback)

            line = f"{idx}. {name}"
            text_y = y_pos + (AVATAR_SIZE - text_font.getbbox(line)[3]) // 2
            draw.text((x + AVATAR_SIZE + 10, text_y), line, font=text_font, fill=(255, 255, 255))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    @aspects.command(name="distribute", description="Given N aspects, pick next members in queue to receive them")
    async def distribute(self, ctx: discord.ApplicationContext, amount: Option(int, "Number of aspects to distribute")):
        await ctx.defer()

        db = DB()
        db.connect()

        try:
            # Rebuild queue and get current state
            queue, start = aspect_db.rebuild_queue(db)
            
            # Get guild for member lookup
            guild = Guild("The Aquarium")
            cutoff = datetime.datetime.now(timezone.utc) - timedelta(days=7)
            
            # Build member map for 7-day check
            member_map = {}
            for m in guild.all_members:
                uuid = m["uuid"]
                joined = m.get("joined")
                if joined:
                    try:
                        dt = datetime.datetime.fromisoformat(joined.replace("Z", "+00:00"))
                        dt = dt.replace(tzinfo=timezone.utc)
                        member_map[uuid] = dt
                    except Exception:
                        pass

            # 1. Drain uncollected aspects from DB first
            remaining = amount
            recipients = []
            
            for uuid, count in aspect_db.get_uncollected_aspects(db):
                if remaining <= 0:
                    break
                
                # Convert uuid to string for comparison
                uuid_str = str(uuid)
                
                join_date = member_map.get(uuid_str)
                if not join_date or join_date > cutoff:
                    continue
                
                take = min(remaining, count)
                if take > 0:
                    recipients += [uuid_str] * take
                    remaining -= take
                    aspect_db.deduct_uncollected_aspects(db, uuid_str, take)
            
            db.connection.commit()

            # 2. Fill from rotation queue
            if remaining > 0 and queue:
                for i in range(remaining):
                    idx = (start + i) % len(queue)
                    recipients.append(queue[idx])

            # 3. Build names list
            names = []
            for u in recipients:
                member = next((m for m in guild.all_members if m["uuid"] == u), None)
                if member:
                    names.append(member["name"])
                else:
                    looked_up = await getNameFromUUID(u)
                    name = looked_up[0] if isinstance(looked_up, (list, tuple)) else str(looked_up)
                    names.append(name)

            # 4. Fetch avatars
            avatar_tasks = [self.get_avatar(u) for u in recipients]
            avatar_results = await asyncio.gather(*avatar_tasks)
            
            avatars = []
            for avatar_data, uuid in zip(avatar_results, recipients):
                if avatar_data is None:
                    avatars.append(self.create_default_avatar(uuid))
                else:
                    avatars.append(avatar_data)

            # 5. Update marker
            displayed = len(recipients)
            if queue:
                new_marker = (start + displayed) % len(queue)
                aspect_db.save_queue_state(db, queue, new_marker)

            # 6. Log distribution
            distribution_list = []
            for uuid, name in zip(recipients, names):
                distribution_list.append({"uuid": uuid, "ign": name})
            aspect_db.log_distribution(db, ctx.author.id, distribution_list, amount)

            # 7. Render and send
            buf = self.make_distribution_image(avatars, names)
            await ctx.followup.send(file=discord.File(buf, "distribution.png"))

        finally:
            db.close()

    @aspects.command(name="queue", description="Show queue of uncollected aspects")
    async def queue(self, ctx: discord.ApplicationContext):
        await ctx.defer()

        db = DB()
        db.connect()

        try:
            rows = aspect_db.get_uncollected_aspects(db)
            
            if not rows:
                embed = discord.Embed(
                    title="Aspect Queue",
                    description="No uncollected aspects at the moment.",
                    color=0x2F3136
                )
                return await ctx.followup.send(embed=embed)

            guild = Guild("The Aquarium")
            lines = []
            total = 0
            
            for uuid, count in rows:
                total += count
                uuid_str = str(uuid)
                member = next((m for m in guild.all_members if m["uuid"] == uuid_str), None)
                if member:
                    name = member["name"]
                else:
                    looked_up = await getNameFromUUID(uuid_str)
                    name = looked_up[0] if isinstance(looked_up, (list, tuple)) else str(looked_up)
                lines.append(f"{name}: {count}")

            description = "\n".join(lines) + f"\n\n**Total: {total}**"
            embed = discord.Embed(
                title="Raid Aspect Queue",
                description=description,
                color=0x2F3136
            )
            await ctx.followup.send(embed=embed)

        finally:
            db.close()

    @blacklist.command(name="add", description="Add someone to the aspect blacklist")
    async def blacklist_add(self, ctx, user: Option(discord.Member, "Member to blacklist")):
        await ctx.defer()
        
        db = DB()
        db.connect()

        try:
            db.cursor.execute("SELECT uuid FROM discord_links WHERE discord_id = %s", (user.id,))
            row = db.cursor.fetchone()
            
            if not row:
                return await ctx.followup.send("❌ That user has no linked game UUID.")
            
            uuid = str(row[0])
            
            if not aspect_db.add_to_blacklist(db, uuid, ctx.author.id):
                return await ctx.followup.send("✅ Already blacklisted.")
            
            await ctx.followup.send(f"✅ Added **{user.display_name}** to the aspect blacklist.")

        finally:
            db.close()

    @blacklist.command(name="remove", description="Remove someone from the aspect blacklist")
    async def blacklist_remove(self, ctx, user: Option(discord.Member, "Member to un-blacklist")):
        await ctx.defer()
        
        db = DB()
        db.connect()

        try:
            db.cursor.execute("SELECT uuid FROM discord_links WHERE discord_id = %s", (user.id,))
            row = db.cursor.fetchone()
            
            if not row:
                return await ctx.followup.send("❌ That user has no linked game UUID.")
            
            uuid = str(row[0])
            
            if not aspect_db.remove_from_blacklist(db, uuid):
                return await ctx.followup.send("✅ User wasn't on the aspect blacklist.")
            
            await ctx.followup.send(f"✅ Removed **{user.display_name}** from the aspect blacklist.")

        finally:
            db.close()

    @aspects.command(name="rotation", description="Show current rotation queue and marker position")
    async def rotation(self, ctx: discord.ApplicationContext):
        """Show the current rotation queue state."""
        await ctx.defer()
        
        db = DB()
        db.connect()
        
        try:
            queue, marker = aspect_db.get_queue_state(db)
            
            if not queue:
                embed = discord.Embed(
                    title="Rotation Queue",
                    description="Queue is empty. Run `/aspects distribute` to rebuild.",
                    color=0x2F3136
                )
                return await ctx.followup.send(embed=embed)
            
            guild = Guild("The Aquarium")
            
            # Show next 10 in rotation
            lines = []
            for i in range(min(10, len(queue))):
                idx = (marker + i) % len(queue)
                uuid = queue[idx]
                member = next((m for m in guild.all_members if m["uuid"] == uuid), None)
                name = member["name"] if member else uuid[:8]
                prefix = "→ " if i == 0 else "  "
                lines.append(f"{prefix}{i+1}. {name}")
            
            description = "\n".join(lines)
            description += f"\n\n**Total in queue:** {len(queue)}"
            description += f"\n**Current position:** {marker + 1}/{len(queue)}"
            
            embed = discord.Embed(
                title="Rotation Queue (Next 10)",
                description=description,
                color=0x2F3136
            )
            await ctx.followup.send(embed=embed)
            
        finally:
            db.close()

    @aspects.command(name="rebuild", description="Force rebuild the rotation queue")
    async def rebuild(self, ctx: discord.ApplicationContext):
        """Force rebuild the queue from current guild data."""
        await ctx.defer()
        
        db = DB()
        db.connect()
        
        try:
            queue, marker = aspect_db.rebuild_queue(db)
            
            embed = discord.Embed(
                title="Queue Rebuilt",
                description=f"✅ Rebuilt queue with **{len(queue)}** eligible members.\nMarker position: {marker + 1}",
                color=0x00FF00
            )
            await ctx.followup.send(embed=embed)
            
        finally:
            db.close()

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(AspectDistribution(client))
