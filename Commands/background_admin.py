import json
from datetime import datetime
from io import BytesIO
import re

import discord
from PIL import Image
from discord import SlashCommandGroup, option
from discord.ext import commands

from Helpers.database import DB


# Retrieve a list of all backgrounds available
async def get_all_backgrounds(message: discord.AutocompleteContext):
    db = DB()
    db.connect()

    backgrounds = []
    db.cursor.execute('SELECT name FROM profile_backgrounds')
    rows = db.cursor.fetchall()
    db.close()
    for bg in rows:
        backgrounds.append(bg[0])
    return [background for background in backgrounds if message.value.lower() in background.lower()]


# Retrieve a list of all backgrounds except the Default one
async def get_backgrounds(message: discord.AutocompleteContext):
    db = DB()
    db.connect()

    backgrounds = []
    db.cursor.execute('SELECT name FROM profile_backgrounds')
    rows = db.cursor.fetchall()
    db.close()
    for bg in rows:
        if bg[0] != 'Default':
            backgrounds.append(bg[0])
    return [background for background in backgrounds if message.value.lower() in background.lower()]


class BackgroundAdmin(commands.Cog):
    def __init__(self, client):
        self.client = client

    background_admin_group = SlashCommandGroup('background_admin', 'Background admin commands',
                                         default_member_permissions=discord.Permissions(administrator=True),
                                         guild_only=True)

    # Upload new PNG background to the database, required size 800x526
    @background_admin_group.command(description="Upload new PNG background to the database, required size 800x526")
    async def upload(self, message, image: discord.Option(discord.Attachment, require=True),
                     public: discord.Option(bool, require=True),
                     price: discord.Option(int, min_value=0, max_value=9999, require=True),
                     name: discord.Option(str, min_length=3, max_length=50, require=True),
                     description: discord.Option(str, default='')):
        await message.defer(ephemeral=True)
        if image.content_type != 'image/png':
            embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                  description=f'Attachment must be of PNG format!',
                                  color=0xe33232)
            await message.respond(embed=embed)
            return

        bg_data = await image.read()
        bg = Image.open(BytesIO(bg_data))

        if bg.size != (800, 526):
            embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                  description=f'Image must have a size of 800x526.\nYou can use this image [Cropping Tool](https://lunarity.space/background_crop/)',
                                  color=0xe33232)
            await message.respond(embed=embed)
            return

        db = DB()
        db.connect()

        db.cursor.execute(
            "INSERT INTO profile_backgrounds(public, price, name, description) VALUES (%s, %s, %s, %s) RETURNING id",
            (public, price, name, description)
        )
        bg_id = db.cursor.fetchone()[0]

        bg.save(f'./images/profile_backgrounds/{bg_id}.png')

        db.connection.commit()

        db.close()

        embed = discord.Embed(title=':white_check_mark: New background uploaded', color=0x34eb40)
        embed.add_field(name='Name', value=str(name))
        embed.add_field(name='Description', value=str(description))
        embed.add_field(name='Public', value=str(public))
        embed.add_field(name='Price', value=str(price))

        bg_file = discord.File(f'./images/profile_backgrounds/{bg_id}.png', filename=f"{bg_id}.png")
        embed.set_image(url=f"attachment://{bg_id}.png")

        with open('background.log', 'a') as f:
            curr_datetime = datetime.now()
            curr_datetime_str = curr_datetime.strftime("%d/%m/%Y, %H:%M:%S")
            f.write(
                f'[{curr_datetime_str}] {message.author.name} ({message.author.id}) uploaded {name} (ID: {bg_id}, Description: {description}, Public: {public}, Price: {price})\n')
            f.close()

        await message.respond(embed=embed, file=bg_file)

    # Forcefully unlock background for discord user, ignoring price and public lock
    @background_admin_group.command(description="Forcefully unlock background for discord user, ignoring price and public lock")
    @option("background", description="Pick a background to unlock", autocomplete=get_backgrounds)
    async def unlock(self, message, user: discord.Option(discord.Member, require=True), background: str):
        await message.defer(ephemeral=True)
        db = DB()
        db.connect()

        db.cursor.execute("SELECT * FROM profile_customization WHERE \"user\" = %s", (str(user.id),))
        row = db.cursor.fetchone()

        db.cursor.execute("SELECT id, name FROM profile_backgrounds WHERE UPPER(name) = UPPER(%s)", (background,))
        bg_id, bg_name = db.cursor.fetchone()

        # Check if user owns any backgrounds, if not insert new entry to table
        if not row:
            db.cursor.execute(
                "INSERT INTO profile_customization(\"user\", background, owned) VALUES (%s, 1, %s)",
                (str(user.id), json.dumps([bg_id]))
            )
            db.connection.commit()
            db.close()
            embed = discord.Embed(title=':unlock: Background unlocked!',
                                  description=f':frame_photo: **{bg_name}** was unlocked for <@{user.id}>.',
                                  color=0x34eb40)
            bg_file = discord.File(f'./images/profile_backgrounds/{bg_id}.png', filename=f"{bg_id}.png")
            embed.set_thumbnail(url=f"attachment://{bg_id}.png")
            await message.respond(embed=embed, file=bg_file)
            return

        bgs = row[2]
        # Check if user already owns selected background, if so return message
        if bg_id in bgs:
            embed = discord.Embed(title=':warning: Oops!',
                                  description=f'<@{user.id}> already owns **{bg_name}** background.',
                                  color=0xebdb34)
            await message.respond(embed=embed)
            db.close()
            return

        bgs.append(bg_id)
        db.cursor.execute(
            "UPDATE profile_customization SET owned = %s WHERE \"user\" = %s",
            (json.dumps(bgs), str(user.id))
        )
        db.connection.commit()

        with open('background.log', 'a') as f:
            curr_datetime = datetime.now()
            curr_datetime_str = curr_datetime.strftime("%d/%m/%Y, %H:%M:%S")
            f.write(
                f'[{curr_datetime_str}] {message.author.name} ({message.author.id}) unlocked {background} ({bg_id}) for {user.name} ({user.id})\n')
            f.close()

        embed = discord.Embed(title=':unlock: Background unlocked!',
                              description=f':frame_photo: **{bg_name}** was unlocked for <@{user.id}>.',
                              color=0x34eb40)
        bg_file = discord.File(f'./images/profile_backgrounds/{bg_id}.png', filename=f"{bg_id}.png")
        embed.set_thumbnail(url=f"attachment://{bg_id}.png")

        await message.respond(embed=embed, file=bg_file)
        db.close()

    # Forcefully set background for discord user whether they own the background or not
    @background_admin_group.command(description="Forcefully set background for discord user whether they own the background or not")
    @option("background", description="Pick a background to unlock", autocomplete=get_all_backgrounds)
    async def set(self, message, user: discord.Option(discord.Member, require=True),
                        background: str):
        await message.defer(ephemeral=True)
        db = DB()
        db.connect()

        db.cursor.execute("SELECT * FROM profile_customization WHERE \"user\" = %s", (str(user.id),))
        row = db.cursor.fetchone()

        db.cursor.execute("SELECT id, name FROM profile_backgrounds WHERE UPPER(name) = UPPER(%s)", (background,))
        bg_id, bg_name = db.cursor.fetchone()

        # Check if user exists in the database, if not insert new entry to table
        if not row:
            db.cursor.execute(
                "INSERT INTO profile_customization(\"user\", background, owned) VALUES (%s, %s, '[]')",
                (str(user.id), bg_id)
            )
            db.connection.commit()
            db.close()
            embed = discord.Embed(title=':white_check_mark: Background set!',
                                  description=f':frame_photo: **{bg_name}** was set as active background for <@{user.id}>.',
                                  color=0x34eb40)
            bg_file = discord.File(f'./images/profile_backgrounds/{bg_id}.png', filename=f"{bg_id}.png")
            embed.set_thumbnail(url=f"attachment://{bg_id}.png")
            await message.respond(embed=embed, file=bg_file)
            return

        db.cursor.execute(
            "UPDATE profile_customization SET background = %s WHERE \"user\" = %s",
            (bg_id, str(user.id))
        )
        db.connection.commit()

        with open('background.log', 'a') as f:
            curr_datetime = datetime.now()
            curr_datetime_str = curr_datetime.strftime("%d/%m/%Y, %H:%M:%S")
            f.write(
                f'[{curr_datetime_str}] {message.author.name} ({message.author.id}) set {background} ({bg_id}) for {user.name} ({user.id})\n')
            f.close()

        embed = discord.Embed(title=':white_check_mark: Background set!',
                              description=f':frame_photo: **{bg_name}** was set as active background for <@{user.id}>.',
                              color=0x34eb40)
        bg_file = discord.File(f'./images/profile_backgrounds/{bg_id}.png', filename=f"{bg_id}.png")
        embed.set_thumbnail(url=f"attachment://{bg_id}.png")

        await message.respond(embed=embed, file=bg_file)
        db.close()

    # Forcefully set a user's profile card gradient
    @background_admin_group.command(description="Forcefully set gradient for discord user whether they own the gradient or not")
    async def gradient(self, message, user: discord.Option(discord.Member, require=True),
                       top_color: discord.Option(str, min_length=6, max_length=7, require=True),
                       bottom_color: discord.Option(str, min_length=6, max_length=7, require=True)
                       ):
        await message.defer(ephemeral=True)

        # Validate hex inputs
        for label, color in [("Top color", top_color), ("Bottom color", bottom_color)]:
            if color[0] != '#':
                color = '#' + color
            match = re.search(r'^#(?:[0-9a-fA-F]{3}){1,2}$', color)
            if not match:
                await message.followup.send(f"❌ {label} `{color}` is not a valid 6-digit hex color.", ephemeral=True)
                return

        # Normalize hex codes to lowercase with leading #
        top_color = f"#{top_color.lstrip('#').lower()}"
        bottom_color = f"#{bottom_color.lstrip('#').lower()}"
        gradient = [top_color, bottom_color]

        db = DB()
        db.connect()

        db.cursor.execute("SELECT * FROM profile_customization WHERE \"user\" = %s", (str(user.id),))
        row = db.cursor.fetchone()

        # Check if user exists in the database, if not insert new entry to table
        if not row:
            db.cursor.execute(
                "INSERT INTO profile_customization(\"user\", background, owned, gradient) VALUES (%s, %s, '[]', %s)",
                (str(user.id), '1', json.dumps(gradient))
            )
            db.connection.commit()
            db.close()
            embed = discord.Embed(title=':white_check_mark: Gradient set!',
                                  description=f'Gradient set with Top Color: {top_color} and Bottom Color: {bottom_color} for <@{user.id}>.',
                                  color=0x34eb40)
            await message.respond(embed=embed)
            return

        db.cursor.execute(
            "UPDATE profile_customization SET gradient = %s WHERE \"user\" = %s",
            (json.dumps(gradient), str(user.id))
        )
        db.connection.commit()

        embed = discord.Embed(title=':white_check_mark: Gradient set!',
                              description=f'Gradient set with Top Color: {top_color} and Bottom Color: {bottom_color} for <@{user.id}>.',
                              color=0x34eb40)

        await message.respond(embed=embed)
        db.close()

    @commands.Cog.listener()
    async def on_ready(self):
        print('Background commands loaded')


def setup(client):
    client.add_cog(BackgroundAdmin(client))
