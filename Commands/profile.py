import os
import time
from io import BytesIO
import asyncio

import discord
import requests
from PIL import Image, ImageDraw, ImageFont
from discord.ext import commands
from discord.commands import slash_command
import json

from Helpers.classes import PlayerStats
from Helpers.functions import pretty_date, generate_rank_badge, generate_banner, getData, format_number, addLine, vertical_gradient, round_corners, generate_badge
from Helpers.variables import discord_ranks, minecraft_colors, minecraft_banner_colors


class Profile(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(description='Displays a guild profile of guild member')
    async def profile(self, ctx: discord.ApplicationContext, name: discord.Option(str, required=True), days: discord.Option(int, min=1, max=30, default=7)):
        await ctx.defer()
        player = await asyncio.to_thread(PlayerStats, name, days)
        if player.error:
            embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                  description=f'Could not retrieve information of `{name}`.\nPlease check your spelling or try again later.',
                                  color=0xe33232)
            await ctx.followup.send(embed=embed, ephemeral=True)
            return

        # Base Image + Edge Gradient
        card = vertical_gradient(main_color=player.tag_color)
        card = round_corners(card)
        draw = ImageDraw.Draw(card)

        # TODO: Custom card color
        # Card Color/Pattern
        if player.background == 2:
            card_color = vertical_gradient(width=850, height=1130, main_color='#4585db', secondary_color='#2f2b73')
        else:
            card_color = vertical_gradient(width=850, height=1130, main_color="#293786", secondary_color="#1d275e")
        card.paste(card_color, (25, 25), card_color)

        # Background Outline
        bg_outline = vertical_gradient(width=818, height=545, main_color=player.tag_color, reverse=True)
        bg_outline = round_corners(bg_outline)
        card.paste(bg_outline, (41, 100), bg_outline)

        # Background
        background = Image.open(f"images/profile_backgrounds/{player.background}.png")
        background = round_corners(background, radius=20)
        card.paste(background, (50, 110), background)

        # Player Name
        name_font = ImageFont.truetype('images/profile/game.ttf', 50)
        addLine(text=player.username, draw=draw, font=name_font, x=50, y=40, drop_x=7, drop_y=7)

        # Player Avatar
        try:
            headers = {'User-Agent': os.getenv("visage_UA")}
            url = f"https://visage.surgeplay.com/bust/500/{player.UUID}"
            response = requests.get(url, headers=headers)
            skin = Image.open(BytesIO(response.content))
        except Exception as e:
            print(e)
            skin = Image.open('images/profile/x-steve500.png')
        skin.thumbnail((480, 480))
        card.paste(skin, (200, 156), skin)

        # Wynn Rank Badge
        rank = generate_rank_badge(player.tag_display, player.tag_color)
        rank_w, rank_h = rank.size
        card.paste(rank, (450 - int(rank_w / 2), 96), rank)

        # Guild Related
        if player.guild:
            # Get Guild Color
            try:
                guild_banner = getData(player.guild)['banner']
                if guild_banner['base'] in ['BLACK', 'GRAY', 'BROWN']:
                    for layer in guild_banner['layers']:
                        if layer['colour'] not in ['BLACK', 'GRAY', 'BROWN']:
                            guild_colour = layer['colour']
                            break
                        else:
                            guild_colour = "WHITE"
                else:
                    guild_colour = guild_banner['base']
            except:
                guild_colour = "WHITE"

            # Guild Name Badge
            guild_badge = generate_badge(text=player.guild, base_color='#{:02x}{:02x}{:02x}'.format(*minecraft_banner_colors[guild_colour]), scale=3)
            guild_badge.crop(guild_badge.getbbox())
            card.paste(guild_badge, (108, 615), guild_badge)

            # Guild Rank Badge Generation
            if player.taq and player.linked:
                try:
                    guild_rank_badge = generate_badge(text=player.rank.upper(), base_color=discord_ranks[player.rank]['color'], scale=3)
                except:
                    guild_rank_badge = generate_badge(text=player.guild_rank.upper(), base_color='#a0aeb0', scale=3)
            else:
                guild_rank_badge = generate_badge(text=player.guild_rank.upper(), base_color='#a0aeb0', scale=3)
            guild_rank_badge.crop(guild_rank_badge.getbbox())

            # Membership Time Badge Generation
            member_for_badge = generate_badge(text=f'{player.in_guild_for.days} D', base_color='#363636', scale=3)
            member_for_badge.crop(member_for_badge.getbbox())

            # Insert Membership & Rank Badges
            grb_w = guild_rank_badge.width
            card.paste(member_for_badge, (90 + grb_w, 667), member_for_badge)
            card.paste(guild_rank_badge, (108, 667), guild_rank_badge)

            # Guild Banner
            banner = generate_banner(player.guild, 15, "2")
            banner.thumbnail((157, 157))
            card.paste(banner, (41, 562))

        # Build out data to place in boxes
        card_entries = {}
        try:
            if player.online:
                card_entries['World'] = player.server
            else:
                card_entries['Last Seen'] = pretty_date(player.last_joined)
            card_entries['Total Level'] = f'{player.total_level}'
            card_entries['Playtime'] = f'{int(player.playtime)} hrs'
            if player.taq and player.in_guild_for.days >= 1:
                card_entries[f'Playtime / {player.stats_days} D'] = f'{int(player.real_pt)} hrs'        # TAq only
            card_entries['Wars'] = str(player.wars)
            if player.taq and player.in_guild_for.days >= 1:
                card_entries[f'Wars / {player.stats_days} D'] = str(player.real_wars)                   # TAq only
            if player.guild:
                card_entries['Guild XP'] = format_number(player.guild_contributed)
            if player.taq and player.in_guild_for.days >= 1:
                card_entries[f'Guild XP / {player.stats_days} D'] = format_number(player.real_xp)       # TAq only
            if player.taq:
                card_entries['Guild Raids'] = str(player.guild_raids)
                if player.in_guild_for.days >= 1:
                    card_entries[f'Guild Raids / {player.stats_days} D'] = str(player.real_raids)       # TAq only
            if len(card_entries) < 10:
                card_entries['Killed Mobs'] = str(player.mobs)
            if len(card_entries) < 10:
                card_entries['Chests Looted'] = str(player.chests)
            if len(card_entries) < 10:
                card_entries['Quests'] = str(player.quests)
        except Exception as e:
            print(e)

        entry_keys = list(card_entries.keys())

        title_font = ImageFont.truetype('images/profile/5x5.ttf', 40)
        data_font = ImageFont.truetype('images/profile/game.ttf', 35)
        box = Image.new('RGBA', (390, 75), (0, 0, 0, 0))
        box_draw = ImageDraw.Draw(box)
        box_draw.rounded_rectangle(((0, 0), (390, 75)), fill=(0, 0, 0, 30), radius=10)

        for entry in range(len(card_entries)):
            card.paste(box, (50 + ((entry % 2) * 410), 730 + (int(entry / 2) * 85)), box)
            draw.text((60 + ((entry % 2) * 410), 720 + (int(entry / 2) * 85)), text=entry_keys[entry], font=title_font, fill='#fad51e')
            draw.text((430 + ((entry % 2) * 410), 765 + (int(entry / 2) * 85)), text=card_entries[entry_keys[entry]], font=data_font, anchor="ra")

        if player.guild:
            if player.taq and player.in_guild_for.days >= 1:
                # Shells
                data_font = ImageFont.truetype('images/profile/game.ttf', 50)
                shells_img = Image.open('images/profile/shells.png')
                shells_img.thumbnail((50, 50))
                addLine(text=str(player.balance), draw=draw, font=data_font, x=781, y=46, drop_x=7, drop_y=7, anchor="rt")
                card.paste(shells_img, (800, 40), shells_img)

        with BytesIO() as file:
            card.save(file, format="PNG")
            file.seek(0)
            t = int(time.time())
            profile_card = discord.File(file, filename=f"profile{t}.png")

        await ctx.followup.send(file=profile_card)

        if player.linked:
            if str(ctx.author.id) == player.discord and player.in_guild_for.days >= 365 and 2 not in player.backgrounds_owned:
                embed = discord.Embed(title=':tada: New background unlocked!',
                                      description=f'<@{player.discord}> unlocked the **1 Year Anniversary** background!',
                                      color=0x34eb40)
                bg_file = discord.File(f'./images/profile_backgrounds/3.png', filename=f"3.png")
                embed.set_thumbnail(url=f"attachment://3.png")

                unlock = player.unlock_background('1 Year Anniversary')
                if unlock:
                    await ctx.channel.send(embed=embed, file=bg_file)

            if str(ctx.author.id) == player.discord and player.rank.upper() in ['NARWHAL','HYDRA'] and 1 not in player.backgrounds_owned:
                embed = discord.Embed(title=':tada: New background unlocked!',
                                      description=f'<@{player.discord}> unlocked the **TAq Sea Turtle** background!',
                                      color=0x34eb40)
                bg_file = discord.File(f'./images/profile_backgrounds/2.png', filename=f"2.png")
                embed.set_thumbnail(url=f"attachment://2.png")

                unlock = player.unlock_background('TAq Sea Turtle')
                if unlock:
                    await ctx.channel.send(embed=embed, file=bg_file)

    @commands.Cog.listener()
    async def on_ready(self):
        print('NewProfile command loaded')


def setup(client):
    client.add_cog(Profile(client))
