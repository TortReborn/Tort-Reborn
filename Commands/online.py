import time
import json
from io import BytesIO

import discord
from PIL import Image, ImageDraw, ImageFont
from discord.ext import commands
from discord.commands import slash_command

from Helpers.classes import PlaceTemplate, Guild
from Helpers.variables import rank_map
from Helpers.functions import getOnlinePlayers, getOnlinePlayersUUID, getUsernameFromUUID, addLine, generate_banner, expand_image


class Online(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(description='Sends a list of online guild members')
    async def online(self, message, guild: discord.Option(str, required=True)):
        await message.defer()
        try:
            guild_data = Guild(guild)
        except:
            embed = discord.Embed(title=':no_entry: Something went wrong',
                                  description=f'Wasn\'t able to retrieve data for {guild}.', color=0xe33232)
            await message.respond(embed=embed, ephemeral=True)
            return

        # Build the online player lists (UUID-based primary, username fallback)
        uuid_map = getOnlinePlayersUUID()
        name_map_raw = getOnlinePlayers()
        name_map = {n.lower(): s for n, s in name_map_raw.items()}

        members = []
        for member in guild_data.all_members:
            server = None

            # Prefer UUID match (handles username changes)
            member_uuid = member.get('uuid')
            if member_uuid:
                server = uuid_map.get(member_uuid.lower())

            # Fallback to username lookup if UUID missing or not found
            if not server:
                server = name_map.get(member['name'].lower())

            if server:
                # Fetch up-to-date IGN from Mojang (if different)
                latest_name = None
                if member_uuid:
                    latest_name = getUsernameFromUUID(member_uuid)

                display_name = latest_name if latest_name else member['name']

                member['online'] = True
                member['server'] = server
                member['display_name'] = display_name
                members.append(member)

        # Accurate online count
        guild_online_count = len(members)

        img = Image.new('RGBA', (700, 90), color='#00000000')
        d = ImageDraw.Draw(img)
        d.fontmode = '1'
        rank_star = Image.open('images/profile/rank_star.png')
        gameFont = ImageFont.truetype('images/profile/game.ttf', 19)
        guildFont = ImageFont.truetype('images/profile/game.ttf', 38)
        titleFont = ImageFont.truetype('images/profile/5x5.ttf', 20)
        world_icon = Image.open('images/profile/world.png')
        world_icon.thumbnail((16, 16))
        bg = PlaceTemplate('images/profile/other.png')
        banner = generate_banner(guild_data.name, 2, style='2')
        img.paste(banner, (10, 10))
        addLine('&7' + guild_data.prefix, d, gameFont, 55, 10)
        addLine('&f' + guild_data.name, d, guildFont, 55, 30)
        player_data = {'owner': [], 'chief': [], 'strategist': [], 'captain': [], 'recruiter': [], 'recruit': []}

        for player in members:
            pname = player.get('display_name', player['name'])
            player_data[player['rank']].append({'name': pname, 'WC': player['server']})

        addLine(f'&f{guild_online_count}/{guild_data.members["total"]}', d, gameFont, 55, 70)

        for rank in player_data:
            if player_data[rank]:
                x = 700
                img, d = expand_image(img, border=(0, 0, 0, 25), fill='#00000000')
                for s in range(len(rank_map[rank])):
                    img.paste(rank_star, (10 + (s * 12), img.height - 14), rank_star)
                addLine('&f' + rank + 'S' if rank != 'OWNER' else '&f' + rank, d, titleFont,
                        10 + len(rank_map[rank]) * 12 + (5 if rank != 'RECRUIT' else 0), img.height - 22)
                for player in player_data[rank]:
                    if x == 700:
                        img, d = expand_image(img, border=(0, 0, 0, 36), fill='#00000000')
                        x = 10
                    bg.add(img, 335, (x, img.height - 34), True)
                    addLine('&f'+player['name'], d, gameFont, x + 10, img.height - 28)
                    _, _, w, h = d.textbbox((0, 0), player['WC'], font=gameFont)
                    addLine('&f' + player['WC'], d, gameFont, x + 325 - w, img.height - 28)
                    img.paste(world_icon, (x + 250, img.height - 26), world_icon)
                    img.paste(bg.divider, (x + 240, img.height - 34), bg.divider)
                    x += 345

        img, d = expand_image(img, border=(0, 0, 0, 10), fill='#00000000')

        background = Image.new('RGBA', (img.width, img.height), color='#00000000')
        bg_img = Image.open('images/profile/leaderboard_bg.png')
        background.paste(bg_img,
                         (int(img.width / 2) - int(bg_img.width / 2),
                          int(img.height / 2) - int(bg_img.height / 2)))
        background.paste(img, (0, 0), img)

        with BytesIO() as file:
            background.save(file, format="PNG")
            file.seek(0)
            t = int(time.time())
            profile_card = discord.File(file, filename=f"profile{t}.png")

        await message.respond(file=profile_card)

    @commands.Cog.listener()
    async def on_ready(self):
        print('Online command loaded')


def setup(client):
    client.add_cog(Online(client))
