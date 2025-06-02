import discord
import requests
from discord.ext import commands
from discord.commands import SlashCommandGroup
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from Helpers.variables import mythics
from Helpers.functions import wrap_text, get_multiline_text_size
import time
import os


class LootPool(commands.Cog):
    lootpool = SlashCommandGroup(
        name="lootpool",
        description="Commands to fetch weekly lootpool data"
    )

    def __init__(self, client):
        self.client = client

    def _format_list(self, items):
        return "\n".join(items) if items else "None"

    async def _init_session(self):
        session = requests.Session()
        try:
            session.get("https://nori.fish/api/tokens")
        except Exception:
            pass
        return session

    @lootpool.command(
        name="aspects",
        description="Provides weekly aspects data"
    )
    async def aspects(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        try:
            resp = requests.get("https://nori.fish/api/aspects")
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            embed = discord.Embed(
                title=":no_entry: Error",
                description="Failed to fetch aspects data. Please try again later.",
                color=0xe33232
            )
            await ctx.followup.send(embed=embed)
            return

        timestamp = data.get("Timestamp")
        embed = discord.Embed(
            title="Weekly Aspects Lootpool",
            color=0x4585db,
            timestamp=datetime.utcfromtimestamp(timestamp)
        )
        loot = data.get("Loot", {})
        count = 0
        for raid_name, rarities in loot.items():
            if count and count % 2 == 0:
                embed.add_field(name='\u200b', value='\u200b', inline=False)
            mythic = self._format_list(rarities.get("Mythic", []))
            fabled = self._format_list(rarities.get("Fabled", []))
            legendary = self._format_list(rarities.get("Legendary", []))
            value = (
                f"**Mythic**:\n{mythic}\n"
                f"**Fabled**:\n{fabled}\n"
                f"**Legendary**:\n{legendary}"
            )
            embed.add_field(name=raid_name, value=value, inline=True)
            count += 1

        embed.set_footer(text=f"Last Updated: {datetime.utcfromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        await ctx.followup.send(embed=embed)

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
        except Exception:
            embed = discord.Embed(
                title=":no_entry: Error",
                description="Failed to fetch loot run data. Please try again later.",
                color=0xe33232
            )
            await ctx.followup.send(embed=embed)
            return

        timestamp = data.get("Timestamp")
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

        # await ctx.followup.send(file=lr_lootpool)
        await ctx.followup.send(embed=embed, file=lr_lootpool)

    @commands.Cog.listener()
    async def on_ready(self):
        print("LootPool cog loaded")


def setup(client):
    client.add_cog(LootPool(client))
