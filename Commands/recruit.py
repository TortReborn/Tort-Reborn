import io

import discord
from discord.ext import commands
from discord.commands import slash_command
import json
from requests import get

from Helpers.variables import te


class Recruit(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(description='Uploads a list of online players', guild_ids=[te])
    async def recruit(self, message,
                      order_by: discord.Option(str, choices=['Playtime', 'Average Playtime', 'Wars', 'Total Level'],
                                               require=True)):
        await message.defer()
        order_map = {'Playtime': 'playtime', 'Average Playtime': 'avg_playtime', 'Wars': 'wars',
                     'Total Level': 'total_level'}

        with open('online_players.json', 'r') as f:
            players_data = json.load(f)
            f.close()

        worlds = get('https://api.wynncraft.com/v3/player').json()

        data = []

        for player in worlds['players']:
            if player in players_data:
                data.append(players_data[player])

        data.sort(key=lambda player: player[order_map[order_by]], reverse=True)
        output = 'Username,Guild,Playtime,Average Playtime,Wars,Total Level\n'
        for p in data:
            output += f'{p["username"]},{p["guild"]},{p["playtime"]},{p["avg_playtime"]},{p["wars"]},{p["total_level"]}\n'

        buffer = io.BytesIO(output.encode('utf-8'))
        output_file = discord.File(buffer, filename='Recruitment.csv')

        await message.respond('', file=output_file)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Recruit(client))
