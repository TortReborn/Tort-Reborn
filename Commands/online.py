import time
import os
from io import BytesIO

import discord
from PIL import Image, ImageDraw, ImageFont
from discord.ext import commands
from discord.commands import slash_command

from Helpers.classes import PlaceTemplate, Guild
from Helpers.variables import rank_map
from Helpers.functions import addLine, generate_banner, expand_image


class Online(commands.Cog):
    def __init__(self, client):
        self.client = client
        # Ensure cache directory exists
        self.cache_dir = os.path.join(os.getcwd(), 'banner_cache')
        os.makedirs(self.cache_dir, exist_ok=True)

    @slash_command(description='Sends a list of online guild members')
    async def online(self, ctx: discord.ApplicationContext, guild: discord.Option(str, required=True)):
        start = time.perf_counter()
        await ctx.defer()
        try:
            guild_data = Guild(guild)
        except Exception:
            embed = discord.Embed(
                title=':no_entry: Something went wrong',
                description=f'Wasn\'t able to retrieve data for {guild}.',
                color=0xe33232
            )
            await ctx.followup.send(embed=embed, ephemeral=True)
            return

        # Filter only online members
        online_members = [m for m in guild_data.all_members if m.get('online')]

        # Group by rank
        from collections import defaultdict
        players_by_rank = defaultdict(list)
        for m in online_members:
            players_by_rank[m['rank']].append(m)

        # Base image
        img = Image.new('RGBA', (700, 90), color=(0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Resources
        rank_star = Image.open('images/profile/rank_star.png')
        world_icon = Image.open('images/profile/world.png')
        world_icon.thumbnail((16, 16))
        bg_template = PlaceTemplate('images/profile/other.png')

        # Header banner and titles with caching
        # sanitize guild name for filename
        safe_name = ''.join(c for c in guild_data.name if c.isalnum() or c in (' ', '_')).rstrip()
        cache_path = os.path.join(self.cache_dir, f"{safe_name}.png")
        if os.path.exists(cache_path):
            banner = Image.open(cache_path)
        else:
            banner = generate_banner(guild_data.name, 2, style='2')
            if banner.mode != 'RGBA':
                banner = banner.convert('RGBA')
            banner.save(cache_path)

        # Paste banner using its alpha
        alpha = banner.split()[3]
        img.paste(banner, (10, 10), mask=alpha)

        # Draw text headers
        game_font = ImageFont.truetype('images/profile/game.ttf', 19)
        guild_font = ImageFont.truetype('images/profile/game.ttf', 38)
        title_font = ImageFont.truetype('images/profile/5x5.ttf', 20)
        addLine(f'&7{guild_data.prefix}', draw, game_font, 55, 10)
        addLine(f'&f{guild_data.name}', draw, guild_font, 55, 30)
        addLine(f'&f{len(online_members)}/{guild_data.members["total"]}', draw, game_font, 55, 70)

        # Draw players in descending rank order
        for rank in reversed(rank_map):
            members = players_by_rank.get(rank, [])
            if not members:
                continue

            # Expand for rank header
            img, draw = expand_image(img, border=(0, 0, 0, 25), fill=(0, 0, 0, 0))
            for i in range(len(rank_map[rank])):
                img.paste(rank_star, (10 + i * 12, img.height - 14), rank_star)
            label = rank if rank == 'OWNER' else f'{rank}S'
            addLine(f'&f{label}', draw, title_font,
                    10 + len(rank_map[rank]) * 12 + (5 if rank != 'RECRUIT' else 0),
                    img.height - 22)

            # Expand and draw each member
            x = 10
            img, draw = expand_image(img, border=(0, 0, 0, 36), fill=(0, 0, 0, 0))
            for m in members:
                bg_template.add(img, 335, (x, img.height - 34), True)
                addLine(f'&f{m["name"]}', draw, game_font, x + 10, img.height - 28)
                _, _, w, _ = draw.textbbox((0, 0), m.get('server', 'N/A'), font=game_font)
                addLine(f'&f{m.get("server", "N/A")}', draw, game_font, x + 325 - w, img.height - 28)
                img.paste(world_icon, (x + 250, img.height - 26), world_icon)
                img.paste(bg_template.divider, (x + 240, img.height - 34), bg_template.divider)
                x += 345

        # Final wrap and send
        img, draw = expand_image(img, border=(0, 0, 0, 10), fill=(0, 0, 0, 0))
        background = Image.new('RGBA', (img.width, img.height), color=(0, 0, 0, 0))
        bg_img = Image.open('images/profile/leaderboard_bg.png')
        if bg_img.mode != 'RGBA':
            bg_img = bg_img.convert('RGBA')
        background.paste(bg_img,
                         ((img.width - bg_img.width) // 2,
                          (img.height - bg_img.height) // 2),
                         bg_img.split()[3])
        background.paste(img, (0, 0), img)

        with BytesIO() as buffer:
            background.save(buffer, format='PNG')
            buffer.seek(0)
            await ctx.followup.send(file=discord.File(buffer, f'online_{int(time.time())}.png'))

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Online(client))