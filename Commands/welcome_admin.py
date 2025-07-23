import json
import os
import time
from io import BytesIO

import discord
from PIL import Image
from discord import SlashCommandGroup, option
from discord.ext import commands
from discord.ui import View

from Helpers.variables import test, guildbank_channel, log_channel


class itemViewTaken(View):
    def __init__(self):
        super().__init__(timeout=None)


async def get_messages(message: discord.AutocompleteContext):
    with open('welcome_messages.txt', 'r') as f:
        messages = f.readlines()
        f.close()

    message_list = []
    for msg in messages:
        message_list.append(msg.replace('\n', ''))
    return [msg for msg in message_list if message.value.lower() in msg.lower()]


class WelcomeMessages(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.gbank_channel = guildbank_channel
        self.log_channel = log_channel

    welcome_group = SlashCommandGroup('welcome_admin', 'Welcome message admin commands',
                                      default_member_permissions=discord.Permissions(administrator=True),
                                      guild_only=True)

    @welcome_group.command(description="Register new welcome message")
    async def register(self, ctx, message: str):
        await ctx.defer(ephemeral=True)
        with open('welcome_messages.txt', 'r') as f:
            messages = f.readlines()
            f.close()

        if '[User]' not in message:
            embed = discord.Embed(title=':information_source: Oops!',
                                  description=f'Please include `[User]` (Case-sensitive) placeholder in the message.',
                                  color=0x4287f5)
            await ctx.respond(embed=embed)
            return

        for msg in messages:
            if msg.lower() == message.lower():
                embed = discord.Embed(title=':information_source: Oops!',
                                      description=f'This welcome message already exists.',
                                      color=0x4287f5)
                await ctx.respond(embed=embed)
                return

        embed = discord.Embed(title='Welcome message registered', description=message, colour=0x31e32b)

        with open('welcome_messages.txt', 'a') as f:
            f.write(f'{message}\n')
            f.close()

        await ctx.respond(embed=embed, delete_after=15)

    @welcome_group.command(description="Remove welcome message")
    @option("message", description="Pick message to remove", autocomplete=get_messages)
    async def delete(self, ctx, message: str):
        await ctx.defer(ephemeral=True)
        with open('welcome_messages.txt', 'r') as f:
            messages = f.readlines()
            f.close()

        for i, msg in enumerate(messages):
            print(msg)
            if msg.lower() == message.lower().replace('\n', ''):
                messages.pop(i)

                with open('welcome_messages.txt', 'w') as f:
                    f.write(''.join(messages))
                    f.close()

                await ctx.respond(f'Message deleted.', ephemeral=True, delete_after=5)
                return

        embed = discord.Embed(title=':information_source: Oops!',
                              description=f'Welcome message not found',
                              color=0x4287f5)
        await ctx.respond(embed=embed)
        return

    @welcome_group.command(description="List of all welcome messages")
    async def list(self, ctx):
        await ctx.respond('List of all welcome messages can be downloaded [here](https://api.lunarity.space/download_welcome_messages)', ephemeral=True)


    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(WelcomeMessages(client))
