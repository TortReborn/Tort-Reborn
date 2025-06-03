from io import BytesIO

import discord
from PIL import Image
from discord import SlashCommandGroup, option
from discord.ext import commands
from discord.commands import slash_command
import json

from Helpers.database import DB
from Helpers.functions import getPlayerUUID


async def get_all_backgrounds(message: discord.AutocompleteContext):
    db = DB()
    db.connect()

    backgrounds = []
    db.cursor.execute('SELECT name FROM profile_backgrounds WHERE public = %s', (True,))
    rows = db.cursor.fetchall()
    db.close()
    for bg in rows:
        backgrounds.append(bg[0])
    return [background for background in backgrounds if message.value.lower() in background.lower()]


class Background(commands.Cog):
    def __init__(self, client):
        self.client = client

    background_group = SlashCommandGroup('background', 'Background commands')

    @background_group.command(description="List all available backgrounds")
    async def list(self, message, owned_only: discord.Option(bool, default=False)):
        await message.defer()
        db = DB()
        db.connect()
        if not owned_only:
            title = 'List of available profile backgrounds'
            db.cursor.execute('SELECT owned FROM profile_customization WHERE \"user\" = %s', (str(message.author.id),))
            row = db.cursor.fetchone()
            if row:
                owned_backgrounds = row[0]
            else:
                owned_backgrounds = []
            owned_backgrounds.insert(0, 0)
            db.cursor.execute(
                'SELECT id, name FROM profile_backgrounds WHERE id = ANY(%s) OR public = %s',
                (owned_backgrounds, True)
            )
            backgrounds = db.cursor.fetchall()
        else:
            title = 'List of owned profile backgrounds'
            db.cursor.execute('SELECT owned FROM profile_customization WHERE \"user\" = %s', (str(message.author.id),))
            row = db.cursor.fetchone()
            if row:
                owned_backgrounds = row[0]
            else:
                owned_backgrounds = []
            owned_backgrounds.insert(0, 0)
            db.cursor.execute(
                'SELECT id, name FROM profile_backgrounds WHERE id = ANY(%s)',
                (owned_backgrounds,)
            )
            backgrounds = db.cursor.fetchall()

        db.close()
        str_backgrounds = ''
        for bg in backgrounds:
            str_backgrounds += (':white_check_mark: ' if bg[0] in owned_backgrounds else ':white_small_square: ') + f'{bg[1]}\n'

        embed = discord.Embed(title=title, description=str_backgrounds, color=0x3474eb)
        embed.set_footer(text='✅ Owned')
        await message.respond(embed=embed)

    @background_group.command(description="Preview a background")
    @option("background", description="Pick a background to preview", autocomplete=get_all_backgrounds)
    async def preview(self, message, background: str):
        await message.defer()
        db = DB()
        db.connect()
        db.cursor.execute(
            'SELECT id, public, price, name, description FROM profile_backgrounds WHERE UPPER(name) = UPPER(%s)', (background,)
        )
        bg = db.cursor.fetchone()
        db.cursor.execute('SELECT owned FROM profile_customization WHERE \"user\" = %s', (str(message.author.id),))
        row = db.cursor.fetchone()
        if row:
            owned_backgrounds = row[0]
        else:
            owned_backgrounds = []
        owned_backgrounds.insert(0, 0)
        db.close()
        if not bg:
            embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                  description=f'Could not find a background with name `{background}`.\nPlease check your spelling or try again later.',
                                  color=0xe33232)
            await message.respond(embed=embed)
            return

        bg_id = bg[0]
        bg_price = bg[2]
        bg_name = bg[3]
        bg_description = bg[4]

        embed = discord.Embed(title=f'{bg_name} *(preview)*', description=bg_description, color=0x3474eb)

        if bg_price != 0:
            embed.add_field(name='Price', value=str(bg_price))

        if bg_id in owned_backgrounds:
            embed.add_field(name=f':white_check_mark: Owned', value=f'You own this background, apply it by using:\n`/background set background: {bg_name}`', inline=False)

        bg_file = discord.File(f'./images/profile_backgrounds/{bg_id}.png', filename=f"{bg_id}.png")
        embed.set_image(url=f"attachment://{bg_id}.png")

        await message.respond(embed=embed, file=bg_file)

    @background_group.command(description="Set a background as active")
    @option("background", description="Pick a background to set as your active one.", autocomplete=get_all_backgrounds)
    async def set(self, message, background: str):
        await message.defer(ephemeral=True)
        db = DB()
        db.connect()
        db.cursor.execute(
            'SELECT * FROM profile_backgrounds WHERE UPPER(name) = UPPER(%s)', (background,)
        )
        bg = db.cursor.fetchone()
        if not bg:
            embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                  description=f'Could not find a background with name **{background}**.\nPlease check your spelling or try again later.',
                                  color=0xe33232)
            await message.respond(embed=embed)
            db.close()
            return

        bg_id = bg[0]
        bg_name = bg[1]

        db.cursor.execute('SELECT owned FROM profile_customization WHERE \"user\" = %s', (str(message.author.id),))
        row = db.cursor.fetchone()
        if row:
            owned_backgrounds = row[0]
        else:
            owned_backgrounds = []
        owned_backgrounds.insert(0, 0)

        if bg_id in owned_backgrounds:
            db.cursor.execute(
                'UPDATE profile_customization SET background = %s WHERE \"user\" = %s',
                (bg_id, str(message.author.id))
            )
            db.connection.commit()
            embed = discord.Embed(title=':white_check_mark: Background set!',
                                  description=f':frame_photo: **{bg_name}** was set as your active background.',
                                  color=0x34eb40)
            bg_file = discord.File(f'./images/profile_backgrounds/{bg_id}.png', filename=f"{bg_id}.png")
            embed.set_thumbnail(url=f"attachment://{bg_id}.png")

            await message.respond(embed=embed, file=bg_file)
        else:
            embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                  description=f'You do not own **{bg_name}** background.\n',
                                  color=0xe33232)
            await message.respond(embed=embed)

        db.close()

    @commands.Cog.listener()
    async def on_ready(self):
        print('Background command loaded')


def setup(client):
    client.add_cog(Background(client))
