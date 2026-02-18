import json

import discord
from discord import option, default_permissions, slash_command
from discord.ext import commands
from discord.ui import View

from Helpers.variables import BOT_LOG_CHANNEL_ID, GUILD_BANK_CHANNEL_ID, ALL_GUILD_IDS


async def get_mythics(message: discord.AutocompleteContext):
    with open('guild_bank.json', 'r') as f:
        mythics = json.load(f)
        f.close()

    mythic_list = []
    for mythic in mythics.keys():
        if mythics[mythic]["status"] == 'in-bank':
            mythic_list.append(mythic)
    return [mythic for mythic in mythic_list if message.value.lower() in mythic.lower()]


class itemViewTaken(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Deposit', custom_id='deposit-button', style=discord.ButtonStyle.red)
    async def return_item(self, button, ctx: discord.ApplicationContext):
        await ctx.response.defer(ephemeral=True)
        self.BOT_LOG_CHANNEL_ID = BOT_LOG_CHANNEL_ID
        with open('guild_bank.json', 'r') as f:
            mythics = json.load(f)
            f.close()
        embed = ctx.message.embeds[0]
        await ctx.message.delete()
        mythics[embed.title]["status"] = 'in-bank'
        with open('guild_bank.json', 'w') as f:
            json.dump(mythics, f, indent=4)
            f.close()
        await ctx.guild.get_channel(BOT_LOG_CHANNEL_ID).send(
            f":green_square: <@{ctx.user.id}> deposited **{embed.title}** to Guild Bank",
            allowed_mentions=discord.AllowedMentions(users=False))


class Withdraw(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.gbank_channel = GUILD_BANK_CHANNEL_ID
        self.BOT_LOG_CHANNEL_ID = BOT_LOG_CHANNEL_ID

    @slash_command(description="HR: Withdraw item from guild bank", guild_ids=ALL_GUILD_IDS)
    @default_permissions(manage_roles=True)
    @option("item", description="Pick item to withdraw", autocomplete=get_mythics)
    async def withdraw(self, message, item: str):
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

        embed = discord.Embed(title=item, description=f':red_circle: Held by <@{message.author.id}>', colour=0xe32b2b)
        if 'icon' in mythics[item]:
            icon = discord.File(f'./images/gb_icons/{mythics[item]["icon"]}')
            embed.set_thumbnail(url=f'attachment://{mythics[item]["icon"]}')
            await message.guild.get_channel(self.gbank_channel).send(embed=embed, file=icon, view=itemViewTaken())
        else:
            await message.guild.get_channel(self.gbank_channel).send(embed=embed, view=itemViewTaken())
        await message.guild.get_channel(self.BOT_LOG_CHANNEL_ID).send(
            f":red_square: <@{message.author.id}> withdrew **{item}** from Guild Bank",
            allowed_mentions=discord.AllowedMentions(users=False))
        await message.respond(f'{item} withdrawn.', ephemeral=True, delete_after=5)
        mythics[item]["status"] = message.author.id
        with open('guild_bank.json', 'w') as f:
            json.dump(mythics, f, indent=4)
            f.close()

    @commands.Cog.listener()
    async def on_ready(self):
        self.client.add_view(itemViewTaken())
        pass


def setup(client):
    client.add_cog(Withdraw(client))
