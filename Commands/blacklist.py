import math

import discord
from discord import option
from discord import SlashCommandGroup
from discord.ext import commands
from discord.ext import pages

from Helpers.database import get_blacklist, add_blacklist_entry, remove_blacklist_entry
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

    @blacklist_group.command(description='Add a player to the blacklist by IGN or UUID')
    async def add(self, message,
                  ign: discord.Option(str, name='player', required=True,
                                      description='In-game name or UUID of the player'),
                  reason: discord.Option(str, name='reason', required=False, default=None,
                                         description='Reason for blacklisting (max 1000 chars)')):
        if reason and len(reason) > 1000:
            embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                  description='Reason must be 1000 characters or fewer.',
                                  color=0xe33232)
            await message.respond(embed=embed, ephemeral=True)
            return

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

        blacklist_list = get_blacklist()
        for player in blacklist_list:
            if player['UUID'] == UUID[1]:
                if UUID[0] == player['ign'] and player.get('reason') == reason:
                    embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                          description=f'{UUID[0]} is already blacklisted.',
                                          color=0xe33232)
                    await message.respond(embed=embed, ephemeral=True)
                    return

        add_blacklist_entry(UUID[1], UUID[0], reason)

        resp = f':no_entry: Blacklisted `{UUID[0]}` (*{UUID[1]}*)'
        if reason:
            resp += f'\n**Reason:** {reason}'
        await message.respond(resp)

    @blacklist_group.command(description='Remove a player from the blacklist')
    @option("player", description="In-game name or UUID of the player", autocomplete=getBlacklistedPlayers)
    async def remove(self, message,
                     player):
        blacklist_list = get_blacklist()

        # Find the player to get their UUID for removal
        removed_player = None
        for entry in blacklist_list:
            if len(player) <= 16:
                if player == entry['ign']:
                    removed_player = entry
                    break
            else:
                if player == entry['UUID']:
                    removed_player = entry
                    break

        if not removed_player:
            embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                  description=f'`{player}` was not found on the blacklist.',
                                  color=0xe33232)
            await message.respond(embed=embed, ephemeral=True)
            return

        remove_blacklist_entry(removed_player['UUID'])

        await message.respond(
            f':white_check_mark: Removed `{removed_player["ign"]}` (*{removed_player["UUID"]}*) from the blacklist.')

    @blacklist_group.command(description='Check if a player is on the blacklist')
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

        for player in blacklist_list:
            if player['UUID'] == UUID[1]:
                if UUID[0] != player['ign']:
                    add_blacklist_entry(UUID[1], UUID[0], player.get('reason'))

                resp = f':no_entry: `{UUID[0]}` (*{UUID[1]}*) is on the blacklist.'
                if player.get('reason'):
                    resp += f'\n**Reason:** {player["reason"]}'
                await message.respond(resp)
                return

        await message.respond(
            f':white_check_mark: `{UUID[0]}` (*{UUID[1]}*) is not blacklisted.')

    @blacklist_group.command(description='View all blacklisted players')
    async def list(self, message):
        blacklist_list = get_blacklist()

        book = []
        blacklist_list.sort(key=lambda x: x['ign'], reverse=False)
        page_num = int(math.ceil(len(blacklist_list) / 20))
        page_num = 1 if page_num == 0 else page_num
        for page in range(page_num):
            page_blacklist = blacklist_list[(20 * page):20 + (20 * page)]
            all_data = '```\n'
            for player in page_blacklist:
                all_data += f'{player["ign"]}\n'
                all_data += f'  {player["UUID"]}\n'
                if player.get('reason'):
                    all_data += f'  Reason: {player["reason"]}\n'
            all_data += '```'
            embed = discord.Embed(title=f'Blacklisted players ({len(blacklist_list)})',
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
