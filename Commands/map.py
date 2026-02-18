import discord
from discord.ext import commands
from discord.commands import slash_command, Option
from Helpers.variables import ALL_GUILD_IDS
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timezone
from io import BytesIO
import asyncio
import json
import requests
from typing import Tuple, Optional


def coordToPixel(x: int, z: int) -> Tuple[int, int]:
    return x + 2383, z + 6572


def mapCreator(guild_prefix: Optional[str] = None):
    """Builds the territory map. If guild_prefix is provided, zooms to that guild's territories.
    Returns (discord.File | None, discord.Embed). If file is None, only the embed should be sent.
    """
    map_img = Image.open("images/map/wynncraft_map.png").convert("RGBA")
    font = ImageFont.truetype("lib/documents/arial.ttf", 40)

    with open("data/territories_verbose.json", "r") as f:
        local_territories = json.load(f)

    # Build a map of guild prefix to color
    try:
        guilds_data = requests.get("https://athena.wynntils.com/cache/get/guildList", timeout=10).json()
        color_map = {g["prefix"]: g.get("color", "#FFFFFF") for g in guilds_data if g.get("prefix")}
    except Exception:
        color_map = {}

    # Fetch territory data
    try:
        territory_data = requests.get("https://api.wynncraft.com/v3/guild/list/territory", timeout=10).json()
    except Exception:
        territory_data = {}

    target_prefix = guild_prefix.strip().upper() if guild_prefix else None

    # Early-out if the specified guild owns 0 territories
    if target_prefix:
        owns_any = any(
            (info.get("guild", {}) or {}).get("prefix", "").upper() == target_prefix
            for info in territory_data.values()
        )
        if not owns_any:
            embed = discord.Embed(
                title=f"No territories found for `{guild_prefix}`",
                description="That guild currently owns 0 territories.",
                color=discord.Color.red(),
            )
            return None, embed

    overlay = Image.new("RGBA", map_img.size)
    overlay_draw = ImageDraw.Draw(overlay)
    draw = ImageDraw.Draw(map_img)

    # Draw trading routes
    for data in local_territories.values():
        routes = data.get("Trading Routes")
        if not routes:
            continue
        try:
            start = data["Location"]["start"]
            end = data["Location"]["end"]
            x1, z1 = (start[0] + end[0]) // 2, (start[1] + end[1]) // 2
            px1, py1 = coordToPixel(x1, z1)
        except KeyError:
            continue

        for dest in routes:
            destData = local_territories.get(dest)
            if not destData:
                continue
            try:
                s2, e2 = destData["Location"]["start"], destData["Location"]["end"]
                x2, z2 = (s2[0] + e2[0]) // 2, (s2[1] + e2[1]) // 2
                px2, py2 = coordToPixel(x2, z2)
                draw.line([(px1, py1), (px2, py2)], fill=(10, 10, 10), width=5)
            except KeyError:
                continue

    # Determine crop bounds if zooming
    do_zoom = target_prefix is not None
    bounds = [map_img.width, map_img.height, 0, 0] if do_zoom else None
    rects_drawn = 0

    # Draw territories
    for info in territory_data.values():
        try:
            (startX, startZ), (endX, endZ) = info["location"]["start"], info["location"]["end"]
            prefix = info["guild"]["prefix"]
        except (KeyError, TypeError):
            continue

        if target_prefix and prefix.upper() != target_prefix:
            continue

        color_hex = color_map.get(prefix, "#FFFFFF")
        try:
            color_rgb = tuple(int(color_hex[i : i + 2], 16) for i in (1, 3, 5))
        except Exception:
            color_rgb = (255, 255, 255)

        x1, y1 = coordToPixel(startX, startZ)
        x2, y2 = coordToPixel(endX, endZ)
        xMin, xMax = sorted([x1, x2])
        yMin, yMax = sorted([y1, y2])

        if bounds:
            bounds[0] = min(bounds[0], xMin)
            bounds[1] = min(bounds[1], yMin)
            bounds[2] = max(bounds[2], xMax)
            bounds[3] = max(bounds[3], yMax)

        overlay_draw.rectangle([xMin, yMin, xMax, yMax], fill=(*color_rgb, 64))
        draw.rectangle([xMin, yMin, xMax, yMax], outline=color_rgb, width=8)

        if prefix:
            try:
                bbox = font.getbbox(prefix)
                w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
                tx, ty = (xMin + xMax) // 2 - w // 2, (yMin + yMax) // 2 - h // 2
                # outline text
                for dx in (-2, 0, 2):
                    for dy in (-2, 0, 2):
                        if dx or dy:
                            draw.text((tx + dx, ty + dy), prefix, font=font, fill="black")
                draw.text((tx, ty), prefix, font=font, fill=color_rgb)
            except Exception as e:
                pass

        rects_drawn += 1

    # Composite overlay
    mapImg = Image.alpha_composite(map_img, overlay)

    # Crop if zoomed and at least one rect was drawn
    if do_zoom and rects_drawn:
        pad = 100
        x0 = max(bounds[0] - pad, 0)
        y0 = max(bounds[1] - pad, 0)
        x1 = min(bounds[2] + pad, mapImg.width)
        y1 = min(bounds[3] + pad, mapImg.height)

        # Guard against invalid crop box
        if x1 > x0 and y1 > y0:
            mapImg = mapImg.crop((x0, y0, x1, y1))

    # Resize
    sf = 0.4
    mapImg = mapImg.resize((int(mapImg.width * sf), int(mapImg.height * sf)), Image.LANCZOS)

    # Prepare file
    buf = BytesIO()
    mapImg.save(buf, format="PNG", optimize=True, compress_level=5)
    buf.seek(0)
    file = discord.File(buf, filename="wynn_map.png")

    # Embed
    embed = discord.Embed(
        title=f"Current Territory Map" + (f" for {guild_prefix}" if guild_prefix else ""),
        color=discord.Color.green(),
    )
    embed.set_image(url="attachment://wynn_map.png")
    return file, embed


class Map(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(
        description="Displays the full territory map, or a zoomed-in version for a specific guild prefix.",
        guild_ids=ALL_GUILD_IDS
    )
    async def map(self, ctx: discord.ApplicationContext, guild: Option(str, "Guild prefix to zoom in on", required=False)):
        """Slash command to show territory map."""
        await ctx.defer()
        file, embed = await asyncio.to_thread(mapCreator, guild)
        # if mapCreator returned (None, embed), send only the embed
        if file is None:
            return await ctx.followup.send(embed=embed)

        # otherwise send image + embed
        await ctx.followup.send(file=file, embed=embed)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Map(client))
