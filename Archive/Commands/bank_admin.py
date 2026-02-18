import json
import os
import time
from io import BytesIO

import discord
from PIL import Image
from discord import SlashCommandGroup, option
from discord.ext import commands
from discord.ui import View

from Helpers.variables import GUILD_BANK_CHANNEL_ID, BOT_LOG_CHANNEL_ID, ALL_GUILD_IDS


class itemViewTaken(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Deposit', custom_id='deposit-button', style=discord.ButtonStyle.red)
    async def return_item(self, button, ctx: discord.ApplicationContext):
        await ctx.response.defer(ephemeral=True)
        log_channel = BOT_LOG_CHANNEL_ID
        with open('guild_bank.json', 'r') as f:
            mythics = json.load(f)
            f.close()
        embed = ctx.message.embeds[0]
        await ctx.message.delete()
        mythics[embed.title]["status"] = 'in-bank'
        with open('guild_bank.json', 'w') as f:
            json.dump(mythics, f, indent=4)
            f.close()
        await ctx.guild.get_channel(log_channel).send(
            f":green_square: <@{ctx.user.id}> deposited **{embed.title}** to Guild Bank",
            allowed_mentions=discord.AllowedMentions(users=False))


async def get_mythics(message: discord.AutocompleteContext):
    with open('guild_bank.json', 'r') as f:
        mythics = json.load(f)
        f.close()

    mythic_list = []
    for mythic in mythics.keys():
        if mythics[mythic]["status"] == 'in-bank':
            mythic_list.append(mythic)
    return [mythic for mythic in mythic_list if message.value.lower() in mythic.lower()]


class BankLog(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.gbank_channel = GUILD_BANK_CHANNEL_ID
        self.log_channel = BOT_LOG_CHANNEL_ID

    bank_log_group = SlashCommandGroup('bank_admin', 'ADMIN: Bank log admin commands',
                                       default_member_permissions=discord.Permissions(administrator=True),
                                       guild_ids=ALL_GUILD_IDS)

    @bank_log_group.command(description="ADMIN: Register new item to guild bank")
    async def register(self, message, item: discord.Option(str, require=True),
                       icon: discord.Option(discord.Attachment, default=None, require=False)):
        await message.defer(ephemeral=True)
        with open('guild_bank.json', 'r') as f:
            mythics = json.load(f)
            f.close()

        if item in mythics.keys():
            embed = discord.Embed(title=':information_source: Oops!',
                                  description=f'Item with this name already exists.',
                                  color=0x4287f5)
            await message.respond(embed=embed)
            return

        mythics[item] = {"name": item, "status": "in-bank"}
        embed = discord.Embed(title='Item registered', description=item, colour=0x31e32b)
        if icon:
            icon_data = await icon.read()
            icon_img = Image.open(BytesIO(icon_data))
            icon_name = int(time.time())
            mythics[item]["icon"] = f'{icon_name}.png'
            icon_img.save(f'./images/gb_icons/{icon_name}.png')
            embed.set_thumbnail(url=icon.url)

        with open('guild_bank.json', 'w') as f:
            json.dump(mythics, f, indent=4)
            f.close()

        await message.guild.get_channel(self.log_channel).send(
            f":ballot_box_with_check: <@{message.author.id}> registered **{item}** to Guild Bank",
            allowed_mentions=discord.AllowedMentions(users=False))
        await message.respond(embed=embed, delete_after=5)

    @bank_log_group.command(description="ADMIN: Remove item from guild bank database")
    @option("item", description="Pick item to remove", autocomplete=get_mythics)
    async def delete(self, message, item: str):
        await message.defer(ephemeral=True)
        with open('guild_bank.json', 'r') as f:
            mythics = json.load(f)
            f.close()

        if item not in mythics.keys():
            embed = discord.Embed(title=':information_source: Oops!',
                                  description=f'Item not found.',
                                  color=0x4287f5)
            await message.respond(embed=embed)
            return

        if mythics[item]["status"] != 'in-bank':
            embed = discord.Embed(title=':information_source: Oops!',
                                  description=f'Please deposit the item first.',
                                  color=0x4287f5)
            await message.respond(embed=embed)
            return

        if 'icon' in mythics[item]:
            os.remove(f'./images/gb_icons/{mythics[item]["icon"]}')
        mythics.pop(item, None)
        await message.guild.get_channel(self.log_channel).send(
            f":x: <@{message.author.id}> deleted **{item}** from Guild Bank",
            allowed_mentions=discord.AllowedMentions(users=False))
        await message.respond(f'{item} deleted.', ephemeral=True, delete_after=5)
        with open('guild_bank.json', 'w') as f:
            json.dump(mythics, f, indent=4)
            f.close()

    @bank_log_group.command(description="ADMIN: Withdraw item from guild bank for someone else")
    @option("item", description="Pick item to remove", autocomplete=get_mythics)
    async def withdraw(self, message, item: str, user: discord.Member):
        await message.defer(ephemeral=True)
        with open('guild_bank.json', 'r') as f:
            mythics = json.load(f)
            f.close()

        if item not in mythics.keys():
            embed = discord.Embed(title=':information_source: Oops!',
                                  description=f'Item not found.',
                                  color=0x4287f5)
            await message.respond(embed=embed)
            return

        if mythics[item]["status"] != 'in-bank':
            embed = discord.Embed(title=':information_source: Oops!',
                                  description=f'Item already held by <@{mythics[item]["status"]}>.',
                                  color=0x4287f5)
            await message.respond(embed=embed)
            return

        embed = discord.Embed(title=item, description=f':red_circle: Held by <@{user.id}>', colour=0xe32b2b)
        if 'icon' in mythics[item]:
            icon = discord.File(f'./images/gb_icons/{mythics[item]["icon"]}')
            embed.set_thumbnail(url=f'attachment://{mythics[item]["icon"]}')
            await message.guild.get_channel(self.gbank_channel).send(embed=embed, file=icon, view=itemViewTaken())
        else:
            await message.guild.get_channel(self.gbank_channel).send(embed=embed, view=itemViewTaken())
        await message.guild.get_channel(self.log_channel).send(
            f":red_square: <@{user.id}> withdrew **{item}** from Guild Bank",
            allowed_mentions=discord.AllowedMentions(users=False))
        await message.respond(f'{item} withdrawn.', ephemeral=True, delete_after=5)
        mythics[item]["status"] = message.author.id
        with open('guild_bank.json', 'w') as f:
            json.dump(mythics, f, indent=4)
            f.close()

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(BankLog(client))
