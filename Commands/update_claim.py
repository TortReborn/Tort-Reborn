import json
import re

import discord
from discord import default_permissions, guild_only
from discord.ext import commands
from discord.commands import slash_command

from Helpers.variables import te, guilds


async def update(long_link, hq):
    with open('mapmaker_decompression.json', 'r') as f:
        decomp = json.load(f)
        f.close()
    with open('neighbor_terrs.json', 'r') as f:
        terr_data = json.load(f)
        f.close()

    link_split = re.split("[+\\-=]", long_link)
    guild_terrs = link_split[link_split.index('TAq') + 2]
    conns = [terr_data[terr] for terr in terr_data if terr.lower() == hq.lower()]
    new_claim = dict()
    new_claim.update({
        "conns": conns[0],
        "territories": [
            decomp['territoriesFromCompression'][
                decomp['territoryIdFromCompression'].index(guild_terrs[2 * i] + guild_terrs[2 * i + 1])]
            for i in range(round(len(guild_terrs) / 2))
        ]
    })

    with open('claim.json', 'w') as f:
        json.dump(new_claim, f, indent=4)
        f.close()


class UpdateClaim(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(description='Updates the guild claim in the territory tracker', guild_ids=[guilds[0],te,guilds[1]])
    @default_permissions(administrator=True)
    async def update_claim(self, message, hq: discord.Option(str, name='hq', required=True, description='Location of the guild headquarters'), link: discord.Option(str, name='long_link', required=True, description='The long link from map maker')):
        await message.defer()
        try:
            await update(link, hq)
        except Exception as e:
            print(e)
            embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                  description=f'Could not update claim.\nPlease check your spelling and map maker link or try again later.',
                                  color=0xe33232)
            await message.respond(embed=embed, ephemeral=True)
            return
        await message.respond(f'Updated claim successfully', ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(UpdateClaim(client))
