import discord
import requests
from discord.ext import commands
from discord.commands import SlashCommandGroup
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from Helpers.variables import mythics
from Helpers.functions import wrap_text, get_multiline_text_size
from Helpers.database import DB
import time
import os
import json


class LootPool(commands.Cog):
    lootpool = SlashCommandGroup(
        name="lootpool",
        description="Commands to fetch weekly lootpool data"
    )

    def __init__(self, client):
        self.client = client

    def _format_list(self, items):
        return "\n".join(items) if items else "None"

    def _cache_data(self, cache_key: str, data: dict):
        """Cache API data to database"""
        try:
            db = DB()
            db.connect()
            
            # Set expiration to epoch time (January 1, 1970)
            epoch_time = datetime.fromtimestamp(0, tz=timezone.utc)
            
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
            """, (cache_key, json.dumps(data), epoch_time))
            
            db.connection.commit()
            db.close()
            
        except Exception as e:
            print(f"[LootPool._cache_data] Failed to save {cache_key} to cache: {e}")
            # Don't let database errors prevent the command from working
            try:
                if 'db' in locals():
                    db.close()
            except:
                pass

    async def _init_session(self):
        session = requests.Session()
        try:
            session.get("https://nori.fish/api/tokens")
        except Exception:
            pass
        return session

    @lootpool.command(
        name="aspects",
        description="Provides weekly aspects data as an image"
    )
    async def aspects(self, ctx: discord.ApplicationContext):
        await ctx.defer()

        # Fetch API data
        try:
            resp = requests.get("https://nori.fish/api/aspects")
            resp.raise_for_status()
            data = resp.json()
            
            # Cache the aspects data
            self._cache_data('aspectData', data)
            
        except Exception:
            embed = discord.Embed(
                title=":no_entry: Error",
                description="Failed to fetch aspects data. Please try again later.",
                color=0xe33232
            )
            await ctx.followup.send(embed=embed)
            return

        loot = data.get("Loot", {})
        raids = ["TNA", "TCC", "NOL", "NOTG"]

        # Load mapping JSON and invert to {aspect_name: class}
        try:
            with open('aspect_class_map.json', 'r') as f:
                class_map = json.load(f)
        except Exception:
            class_map = {}
        aspect_to_class = {name: cls for cls, names in class_map.items() for name in names}

        # Layout settings
        cols = len(raids)
        col_w = 300
        padding = 20
        line_spacing = 8
        raid_icon_size = 144
        class_icon_size = 24

        # Prepare fonts and dummy draw
        title_font = ImageFont.truetype("images/profile/game.ttf", 18)
        dummy_img = Image.new('RGBA', (1,1), (0,0,0,0))
        dummy_draw = ImageDraw.Draw(dummy_img)

        # Compute max lines to determine canvas height
        max_lines = 0
        for raid in raids:
            count = 0
            for rarity in ["Mythic", "Fabled", "Legendary"]:
                for aspect in loot.get(raid, {}).get(rarity, []):
                    text = aspect.replace("Aspect of ", "")
                    text = text[:1].upper() + text[1:] if text else text
                    wrapped = wrap_text(text, title_font, col_w - 20, dummy_draw)
                    count += wrapped.count("\n") + 1
            max_lines = max(max_lines, count)

        # Canvas size
        line_h = get_multiline_text_size("Test", title_font)[1]
        img_h = padding + raid_icon_size + max_lines * (line_h + line_spacing) + padding
        img_w = cols * col_w + padding * (cols + 1)

        # Create canvas
        img = Image.new("RGBA", (img_w, img_h), (0,0,0,0))
        draw = ImageDraw.Draw(img)

        # Draw each raid column
        for i, raid in enumerate(raids):
            x0 = padding + i * (col_w + padding)
            y0 = padding + raid_icon_size // 2
            y1 = img_h - padding

            # Background panel
            draw.rounded_rectangle(
                (x0, y0, x0 + col_w, y1+padding),
                radius=10,
                fill=(0,0,0,255),
                outline=(36,0,89,255),
                width=4
            )

            # Raid icon
            raid_path = f"images/raids/{raid}.png"
            if os.path.isfile(raid_path):
                raid_icon = Image.open(raid_path).convert("RGBA")
                raid_icon.thumbnail((raid_icon_size, raid_icon_size))
                ix = x0 + (col_w - raid_icon.width) // 2
                img.paste(raid_icon, (ix, padding), raid_icon)

            # Draw aspects below icon
            ty = padding + raid_icon_size + line_spacing
            for rarity, color in [("Mythic",(170,0,170,255)), ("Fabled",(255,85,85,255)), ("Legendary",(85,255,255,255))]:
                for aspect in loot.get(raid, {}).get(rarity, []):
                    # Prepare text
                    if "Aspect of a " in aspect:
                        text = aspect.replace("Aspect of a ", "")
                    elif "Aspect of the " in aspect:
                        text = aspect.replace("Aspect of the ", "")
                    elif "Aspect of " in aspect:
                        text = aspect.replace("Aspect of ", "")
                    else:
                        text = aspect

                    # Class icon if available
                    cls = aspect_to_class.get(aspect)
                    offset = 0
                    if cls:
                        icon_path = f"images/raids/aspect_{cls}.png"
                        if os.path.isfile(icon_path):
                            ci = Image.open(icon_path).convert("RGBA")
                            ci.thumbnail((class_icon_size, class_icon_size))
                            img.paste(ci, (x0+10, ty), ci)
                            offset = class_icon_size + 5

                    # Wrap and draw
                    wrapped = wrap_text(text, title_font, col_w - 20 - offset, draw)
                    draw.multiline_text((x0+10+offset, ty), wrapped, font=title_font, fill=color)
                    _, h = get_multiline_text_size(wrapped, title_font)
                    ty += h + line_spacing

        # Try to pull a timestamp from the API (fallback to now if missing)
        ts = data.get("Timestamp")
        next_rot = ts + 604800  # one week in seconds

        # Prepare the image file
        with BytesIO() as buf:
            img.save(buf, format="PNG")
            buf.seek(0)
            file = discord.File(buf, filename="aspects.png")

            # Build the embed
            embed = discord.Embed(
                title="Weekly Raid Aspects",
                color=0x7a187a  # match your lootruns color
            )
            embed.add_field(
                name=":arrows_counterclockwise: Next rotation:",
                value=f"<t:{next_rot}:f>"  # full datetime format
            )
            embed.set_image(url="attachment://aspects.png")

            # Send embed + image together
            await ctx.followup.send(embed=embed, file=file)

    @lootpool.command(
        name="lootruns",
        description="Provides weekly loot run data (Mythic Only)"
    )
    async def lootruns(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        session = await self._init_session()
        csrf_token = session.cookies.get('csrf_token') or session.cookies.get('csrftoken')
        headers = {}
        if csrf_token:
            headers['X-CSRF-Token'] = csrf_token

        try:
            resp = session.get("https://nori.fish/api/lootpool", headers=headers)
            resp.raise_for_status()
            data = resp.json()
            
            # Cache the lootpool data
            self._cache_data('lootpoolData', data)
            
        except Exception:
            embed = discord.Embed(
                title=":no_entry: Error",
                description="Failed to fetch loot run data. Please try again later.",
                color=0xe33232
            )
            await ctx.followup.send(embed=embed)
            return

        embed = discord.Embed(
            title="Weekly Mythic Lootpool",
            color=0x7a187a,
        )
        embed.add_field(name=":arrows_counterclockwise: Next rotation:", value=f'<t:{(data.get("Timestamp")) + 604800}:f>')

        loot = data.get("Loot", {})
        region_widths = []
        n_regions = 0
        longest = 0
        for region, region_data in loot.items():
            n_regions += 1
            length = len(region_data.get("Mythic", [])) + 1
            region_widths.append(156 * length)
            longest = max(longest, length)

        w = 156 * longest
        h = 263 * n_regions  # height based on number of lr regions for future proofing
        lr_lp = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(lr_lp)

        shiny = Image.open("images/mythics/shiny.png").convert("RGBA")
        shiny.thumbnail((36, 36))

        count = 0
        for region_name, region_data in loot.items():
            r = region_widths[count]
            x1 = (w - r) / 2
            x2 = w - x1
            y1 = 35 + (255 * count)
            y2 = 250 + (255 * count)

            draw.rounded_rectangle(xy=(x1, y1, x2, y2), radius=3, fill=(0, 0, 0, 200))
            draw.rectangle(xy=(x1 + 4, y1 + 4, x2 - 4, y2 - 4), fill=(36, 0, 89, 255))
            draw.rectangle(xy=(x1 + 8, y1 + 8, x2 - 8, y2 - 8), fill=(0, 0, 0, 200))

            shiny_item = region_data['Shiny']['Item']
            items = [shiny_item] + region_data['Mythic']
            for i, item in enumerate(items):
                item_img_file = mythics.get(item)
                try:
                    item_img = Image.open(os.path.join('images/mythics/', item_img_file))
                    item_img.thumbnail((100, 100))
                    x = int(x1 + 28 + i * 156)
                    y = int(y1 + 25)
                    lr_lp.paste(item_img, (x, y), item_img)
                    if item == shiny_item and i < 1:
                        lr_lp.paste(shiny, (x, y), shiny)

                    # Item name
                    item_font = ImageFont.truetype("images/profile/game.ttf", 20)
                    name_text = wrap_text(item, item_font, 156, draw)
                    text_w, text_h = get_multiline_text_size(name_text, item_font)
                    draw.multiline_text(
                        (x + (100 - text_w) // 2, y + 115),
                        name_text,
                        font=item_font,
                        fill=(170, 0, 170, 255),
                        align="center",
                        spacing=0
                    )

                    # Shiny tracker
                    tracker_font = ImageFont.truetype("images/profile/game.ttf", 18)
                    if item == shiny_item and i < 1:
                        lines_in_name = name_text.count("\n") + 1
                        tracker_text_raw = region_data['Shiny']['Tracker']
                        wrapped_tracker = wrap_text(tracker_text_raw, tracker_font, 140, draw)
                        tracker_lines = wrapped_tracker.count("\n") + 1
                        tracker_y = y + 115 + (lines_in_name * 20)

                        tracker_w, tracker_h = get_multiline_text_size(wrapped_tracker, tracker_font)
                        draw.multiline_text(
                            (x + (100 - tracker_w) // 2, tracker_y),
                            wrapped_tracker,
                            font=tracker_font,
                            fill=(255, 170, 0, 255),
                            align="center",
                            spacing=0
                        )

                except Exception as e:
                    print(e)
                    embed = discord.Embed(
                        title=":no_entry: Error",
                        description="Could not generate lootpool image. Please try again later.",
                        color=0xe33232
                    )
                    await ctx.followup.send(embed=embed)
                    return

            count += 1

        title_font = ImageFont.truetype('images/profile/game.ttf', 40)
        draw.text(xy=(w / 2, 16), text="Silent Expanse Expedition", font=title_font, fill=(85, 227, 64, 255), stroke_width=3,
                  stroke_fill=(33, 33, 33, 255), align="center", anchor="mt")
        draw.text(xy=(w / 2, 271), text="The Corkus Traversal", font=title_font, fill=(237, 202, 59, 255), stroke_width=3,
                  stroke_fill=(107, 77, 22, 255), align="center", anchor="mt")
        draw.text(xy=(w / 2, 526), text="Sky Islands Exploration", font=title_font, fill=(88, 214, 252, 255), stroke_width=3,
                  stroke_fill=(31, 55, 108, 255), align="center", anchor="mt")
        draw.text(xy=(w / 2, 781), text="Molten Heights Hike", font=title_font, fill=(189, 30, 30, 255), stroke_width=3,
                  stroke_fill=(99, 11, 11, 255), align="center", anchor="mt")
        draw.text(xy=(w / 2, 1036), text="Canyon of the Lost Excursion (South)", font=title_font, fill=(52, 64, 235, 255), stroke_width=3,
                  stroke_fill=(21, 27, 115, 255), align="center", anchor="mt")

        with BytesIO() as file:
            lr_lp.save(file, format="PNG")
            file.seek(0)
            t = int(time.time())
            lr_lootpool = discord.File(file, filename=f"lootpool{t}.png")
            embed.set_image(url=f"attachment://lootpool{t}.png")

        await ctx.followup.send(embed=embed, file=lr_lootpool)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(LootPool(client))
