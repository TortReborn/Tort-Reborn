import time
from io import BytesIO

import discord
import requests
from PIL import Image, ImageDraw, ImageFont
from discord.ext import commands
from discord.commands import slash_command
import json

from Helpers.classes import PlayerStats
from Helpers.functions import pretty_date
from Helpers.variables import discord_ranks, guilds


class Custom_Profile(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(description='Displays a guild profile of guild member', guild_ids=[guilds[1]])
    async def custom_profile(self, message, player: discord.Option(str, require=True), guild_rank: discord.Option(str,
                                                                                                                  choices=[
                                                                                                                      'RECRUIT',
                                                                                                                      'RECRUITER',
                                                                                                                      'CAPTAIN',
                                                                                                                      'STRATEGIST',
                                                                                                                      'CHIEF',
                                                                                                                      'OWNER',
                                                                                                                      'Starfish',
                                                                                                                      'Manatee',
                                                                                                                      'Piranha',
                                                                                                                      'Barracuda',
                                                                                                                      'Angler',
                                                                                                                      'Hammerhead',
                                                                                                                      'Sailfish',
                                                                                                                      'Dolphin',
                                                                                                                      'Narwhal',
                                                                                                                      '✫✪✫ Hydra - Leader'],
                                                                                                                  default=''), last_seen: str = '',
                             days: int = 7, name: str = "", guild: str = "", xp_contribution: int = -1, member_for: int = -1):
        await message.defer()
        try:
            player = PlayerStats(player, days)
        except:
            embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                  description=f'Could not retrieve information of `{player}`.\nPlease check your spelling or try again later.',
                                  color=0xe33232)
            await message.respond(embed=embed, ephemeral=True)
            return
        if guild_rank != '':
            if guild_rank in ['Starfish', 'Manatee', 'Piranha', 'Barracuda', 'Angler', 'Hammerhead', 'Sailfish',
                              'Trial-Narwhal', 'Narwhal', '✫✪✫ Hydra - Leader']:
                player.taq = True
                player.rank = guild_rank
            else:
                player.taq = False
                player.guild_rank = guild_rank

        if name != "":
            player.username = name
        if guild != "":
            player.guild = guild
            if guild != "The Aquarium":
                player.taq = False
        if xp_contribution != -1:
            player.guild_contributed = xp_contribution
        if member_for != -1:
            player.in_guild_for = member_for
        if last_seen != '':
            player.last_joined = last_seen

        bg = Image.open('images/profile/bg.png')
        color = '#ffffff'
        draw = ImageDraw.Draw(bg)

        # background
        profile_pictures = json.load(open('backgrounds.json', 'r'))
        if player.UUID in profile_pictures:
            background = Image.open(f"images/profile_pictures/{profile_pictures[player.UUID]}")
            bg.paste(background, (0, 0), background)

        # skin
        url = f"https://visage.surgeplay.com/bust/500/{player.UUID}"
        response = requests.get(url)
        skin = Image.open(BytesIO(response.content))
        bg.paste(skin, (150, 26), skin)

        # guild and rank
        if player.guild:
            guild_font = ImageFont.truetype('images/profile/minecraft_font.ttf', 32)
            if player.linked and player.taq:
                rank = Image.open(f'images/profile/{discord_ranks[player.rank]["image"]}.png')
                color = discord_ranks[player.rank]['color']
            else:
                rank = Image.open(f'images/profile/{player.guild_rank}.png')
            rank_w, rank_h = rank.size
            bg.paste(rank, (400 - int(rank_w / 2), 526 - int(rank_h / 2)), rank)
            url = f'https://wynn-guild-banner.toki317.dev/banners/{player.guild}'
            response = requests.get(url)
            banner = Image.open(BytesIO(response.content))
            banner.thumbnail((100, 48))
            _, _, w, h = draw.textbbox((0, 0), player.guild, font=guild_font)
            draw.text(((800 - w + 39) / 2, 444), player.guild, font=guild_font, fill='#1c1b1b')
            draw.text(((800 - w + 30) / 2, 440), player.guild, font=guild_font)
            bg.paste(banner, (int((800 - w - 30) / 2), 435))

        # name
        name_font = ImageFont.truetype('images/profile/minecraft_font.ttf', 64)
        _, _, w, h = draw.textbbox((0, 0), player.username, font=name_font)
        draw.text(((800 - w) / 2, 560), player.username, font=name_font, fill=color)
        draw.text(((800 - w) / 2, 560), player.username, font=name_font, fill=color)

        # profile stats
        title_font = ImageFont.truetype('images/profile/5x5.ttf', 35)
        data_font = ImageFont.truetype('images/profile/minecraft_font.ttf', 42)
        if player.online:
            draw.text((50, 630), 'World', font=title_font, fill='#fad51e')
            draw.text((50, 660), player.server, font=data_font)
        else:
            draw.text((50, 630), 'Last seen', font=title_font, fill='#fad51e')
            if type(player.last_joined) == str:
                draw.text((50, 660), player.last_joined, font=data_font)
            else:
                draw.text((50, 660), pretty_date(player.last_joined), font=data_font)
        draw.text((50, 720), 'Rank', font=title_font, fill='#fad51e')
        draw.text((50, 750), player.tag, font=data_font, fill=player.tag_color)
        draw.text((50, 810), 'Playtime', font=title_font, fill='#fad51e')
        draw.text((50, 840), f'{player.playtime} hrs', font=data_font)
        draw.text((50, 900), 'Total Level', font=title_font, fill='#fad51e')
        draw.text((50, 930), f'{player.total_level}', font=data_font)

        if player.guild:
            vertical_divider = Image.open('images/profile/vertical_divider.png')
            bg.paste(vertical_divider, (398, 649), vertical_divider)
            title_font = ImageFont.truetype('images/profile/5x5.ttf', 35)
            data_font = ImageFont.truetype('images/profile/minecraft_font.ttf', 32)
            draw.text((450, 630), 'Guild member for', font=title_font, fill='#fad51e')
            if type(player.in_guild_for) == int:
                draw.text((450, 662), f'{player.in_guild_for} days', font=data_font)
                compare = player.in_guild_for >= 1
            else:
                draw.text((450, 662), f'{player.in_guild_for.days} days', font=data_font)
                compare = player.in_guild_for.days >= 1
            draw.text((450, 700), 'XP Contribution', font=title_font, fill='#fad51e')
            draw.text((450, 732), '{:,}'.format(player.guild_contributed), font=data_font)
            if player.taq and compare:
                horizontal_divider = Image.open('images/profile/horizontal_divider.png')
                bg.paste(horizontal_divider, (420, 786), horizontal_divider)
                draw.text((450, 790), f'{player.stats_days}-day stats', font=title_font, fill='#fad51e')
                draw.text((450, 830), f'Playtime', font=title_font, fill='#fad51e')
                draw.text((450, 862), f'{player.real_pt} hrs', font=data_font)
                draw.text((450, 900), f'XP Contribution', font=title_font, fill='#fad51e')
                draw.text((450, 932), '{:,}'.format(player.real_xp), font=data_font)

                # shells
                shells_img = Image.open('images/profile/shells.png')
                shells_img.thumbnail((50, 50))
                data_font = ImageFont.truetype('images/profile/minecraft_font.ttf', 42)
                _, _, w, h = draw.textbbox((0, 0), '{:,}'.format(player.shells), font=name_font)
                draw.text((780 - w, 10), '{:,}'.format(player.shells), font=data_font)
                bg.paste(shells_img, (718 - w, 13), shells_img)

        # embed
        possessive_noun = '\'s' if player.username[-1] != 's' else '\''
        embed = discord.Embed(title=player.username.replace("_", "\\_") + f'{possessive_noun} playercard',
                              description=f'<:discord:1026929770216292462> <@{player.discord}>' if player.linked else '',
                              color=int(color[1:], 16))
        if player.taq and not player.linked:
            embed.set_footer(
                text='Some information could be more accurate.\nUse the /link command to connect your minecraft '
                     'account.\nAlternatively you can ask our moderators to link it for you.',
                icon_url='https://media.discordapp.net/attachments/1004096609686143008/1039684902754455612/image.png'
                         '?width=671&height=671')
        with BytesIO() as file:
            bg.save(file, format="PNG")
            file.seek(0)
            t = int(time.time())
            profile_card = discord.File(file, filename=f"profile{t}.png")
            embed.set_image(url=f"attachment://profile{t}.png")

        await message.respond(embed=embed, file=profile_card)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Custom_Profile(client))
