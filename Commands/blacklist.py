import math

import discord
from discord import option
from discord import SlashCommandGroup
from discord.ext import commands
from discord.ext import pages

from Helpers.database import get_blacklist, save_blacklist
from Helpers.functions import getPlayerUUID, getNameFromUUID
from Helpers.variables import EXEC_GUILD_IDS


async def getBlacklistedPlayers(message: discord.AutocompleteContext):
    blacklist = get_blacklist()
    return [player['ign'] for player in blacklist if message.value.lower() in player['ign'].lower()]


class Blacklist(commands.Cog):
    def __init__(self, client):
        self.client = client

    blacklist_group = SlashCommandGroup('blacklist', 'Blacklist related commands',
                                        guild_ids=EXEC_GUILD_IDS)

    @blacklist_group.command()
    async def add(self, message,
                  ign: discord.Option(str, name='player', required=True,
                                      description='In-game name or UUID of the player')):
        blacklist_list = get_blacklist()

        if len(ign) > 16:
            UUID = getNameFromUUID(ign)
        else:
            UUID = getPlayerUUID(ign)

        if not UUID:
            embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                  description=f'Could not retrieve information of `{ign}`.\nPlease check your spelling or try again later.',
                                  color=0xe33232)
            await message.respond(embed=embed, ephemeral=True)
            return

        for i,player in enumerate(blacklist_list):
            if player['UUID'] == UUID[1]:
                if UUID[0] == player['ign']:
                    embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                          description=f'{UUID[0]} is already blacklisted.',
                                          color=0xe33232)
                    await message.respond(embed=embed, ephemeral=True)
                    return
                else:
                    blacklist_list[i] = {'ign': UUID[0], 'UUID': UUID[1]}
                    save_blacklist(blacklist_list)
                    embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                          description=f'{UUID[0]} is already blacklisted as {player["ign"]}. Updated In-Game name.',
                                          color=0xe33232)
                    await message.respond(embed=embed, ephemeral=True)
                    return

        blacklist_list.append({'ign': UUID[0], 'UUID': UUID[1]})
        save_blacklist(blacklist_list)

        await message.respond(f':no_entry: Blacklisted `{UUID[0]}` (*{UUID[1]}*)')

    @blacklist_group.command()
    @option("player", description="In-game name or UUID of the player", autocomplete=getBlacklistedPlayers)
    async def remove(self, message,
                     player):
        blacklist_list = get_blacklist()

        removed = False
        for i, players in enumerate(blacklist_list):
            if len(player) <= 16:
                if player == players['ign']:
                    removed_player = blacklist_list.pop(i)
                    removed = True
                    break
            else:
                if player == players['UUID']:
                    removed_player = blacklist_list.pop(i)
                    removed = True
                    break

        if not removed:
            embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                  description=f'`{player}` was not found on the blacklist.',
                                  color=0xe33232)
            await message.respond(embed=embed, ephemeral=True)
            return

        save_blacklist(blacklist_list)

        await message.respond(
            f':white_check_mark: Removed `{removed_player["ign"]}` (*{removed_player["UUID"]}*) from the blacklist.')

    @blacklist_group.command()
    async def check(self, message, ign: discord.Option(str, name='player', required=True,
                                      description='In-game name or UUID of the player')):
        blacklist_list = get_blacklist()

        if len(ign) > 16:
            UUID = getNameFromUUID(ign)
        else:
            UUID = getPlayerUUID(ign)

        if not UUID:
            embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                  description=f'Could not retrieve information of `{ign}`.\nPlease check your spelling or try again later.',
                                  color=0xe33232)
            await message.respond(embed=embed, ephemeral=True)
            return

        for i, player in enumerate(blacklist_list):
            if player['UUID'] == UUID[1]:
                if UUID[0] == player['ign']:
                    await message.respond(
                        f':no_entry: `{UUID[0]}` (*{UUID[1]}*) is on the blacklist.')
                    return
                else:
                    await message.respond(
                        f':no_entry: `{UUID[0]}` (*{UUID[1]}*) was blacklisted as {player["ign"]}. Updated to new In-Game name')
                    return

        await message.respond(
            f':white_check_mark: `{UUID[0]}` (*{UUID[1]}*) is not blacklisted.')

    @blacklist_group.command()
    async def list(self, message):
        blacklist_list = get_blacklist()

        book = []
        blacklist_list.sort(key=lambda x: x['ign'], reverse=False)
        page_num = int(math.ceil(len(blacklist_list) / 30))
        page_num = 1 if page_num == 0 else page_num
        for page in range(page_num):
            page_blacklist = blacklist_list[(30 * page):30 + (30 * page)]
            all_data = '```ansi\n[1;37m Player Name        UUID' \
                       '\n╘═════════════════╪═════════════════════════════════════╛\n'
            for player in page_blacklist:
                all_data = all_data + '[0;0m {:16s} │ {:36s} \n'.format(player['ign'], player['UUID'])
            all_data += '```'
            embed = discord.Embed(title='Blacklisted players',
                                  description=all_data)
            book.append(embed)
        final_book = pages.Paginator(pages=book)
        final_book.add_button(
            pages.PaginatorButton("prev", emoji="<:left_arrow:1198703157501509682>", style=discord.ButtonStyle.red))
        final_book.add_button(
            pages.PaginatorButton("next", emoji="<:right_arrow:1198703156088021112>", style=discord.ButtonStyle.green))
        final_book.add_button(pages.PaginatorButton("first", emoji="<:first_arrows:1198703152204103760>",
                                                    style=discord.ButtonStyle.blurple))
        final_book.add_button(pages.PaginatorButton("last", emoji="<:last_arrows:1198703153726627880>",
                                                    style=discord.ButtonStyle.blurple))
        await final_book.respond(message.interaction)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Blacklist(client))
