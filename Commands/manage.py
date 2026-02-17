import os
import time
from datetime import datetime
from io import BytesIO

import discord
from discord import SlashCommandGroup, ApplicationContext
from discord.ext import commands
from discord.ui import Modal, InputText
from PIL import Image, ImageDraw, ImageFont, ImageOps
import requests

from Helpers.classes import LinkAccount, PlayerStats, PlayerShells
from Helpers.database import DB
from Helpers.functions import addLine, split_sentence, expand_image, getPlayerUUID
from Helpers.variables import guilds, discord_ranks, discord_rank_roles


class ShellModalName(Modal):
    def __init__(self, user: discord.Member, operation: str, amount: int, reason: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.operation = operation
        self.amount = amount
        self.reason = reason
        self.add_item(
            InputText(
                label="Player's Name", 
                placeholder="Player's In-Game Name without rank"
            )
        )

    async def callback(self, interaction: discord.Interaction):
        db = DB(); db.connect()
        img = Image.new('RGBA', (375, 95), '#100010e2')
        draw = ImageDraw.Draw(img); draw.fontmode = '1'
        font = ImageFont.truetype('images/profile/game.ttf', 19)

        player = PlayerStats(self.children[0].value, 1)
        try:
            url = f"https://visage.surgeplay.com/bust/75/{player.UUID}"
            skin = Image.open(BytesIO(requests.get(url).content))
        except:
            skin = Image.open('images/profile/x-steve.webp')
        img.paste(skin, (10, 10), skin)

        if self.operation == 'add':
            new_amount = player.shells + self.amount
            diff = f'+{self.amount}'
        else:
            new_amount = player.shells - self.amount
            diff = f'-{self.amount}'
        addLine(f'&7All-Time: &f{new_amount} &7({diff}&7)', draw, font, 95, 61)

        db.cursor.execute(
            "INSERT INTO discord_links (discord_id, ign, linked, rank) VALUES (%s, %s, 0, '') ON CONFLICT (discord_id) DO UPDATE SET ign=EXCLUDED.ign;",
            (self.user.id, self.children[0].value)
        )
        db.cursor.execute(
            "INSERT INTO shells (\"user\", shells) VALUES (%s, %s) ON CONFLICT (\"user\") DO UPDATE SET shells=EXCLUDED.shells;",
            (str(self.user.id), new_amount)
        )
        db.connection.commit()

        addLine(f'&f{player.username}', draw, font, 95, 15)
        addLine(f'&7Balance: &f{new_amount} &7({diff}&7)', draw, font, 95, 40)

        if self.reason:
            for line in split_sentence(self.reason):
                img, draw = expand_image(img)
                addLine(f'&3{line}', draw, font, 10, img.height - 25)

        img = ImageOps.expand(img, border=(2,2), fill='#100010e2')
        draw = ImageDraw.Draw(img)
        draw.rectangle((2,2,img.width-3,img.height-3), outline='#240059', width=2)

        buf = BytesIO(); img.save(buf, 'PNG'); buf.seek(0)
        file = discord.File(buf, filename=f"shell_{int(time.time())}.png")
        await interaction.response.send_message(file=file)
        db.close()


class Manage(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client

    manage_group = SlashCommandGroup(
        'manage', 'Guild management commands',
        guild_ids=guilds,
        default_member_permissions=discord.Permissions(manage_roles=True)
    )


    @manage_group.command(name='rank', description='Assign or update a user’s guild rank')
    async def rank(
        self,
        ctx: ApplicationContext,
        user: discord.Member,
        rank: discord.Option(str, choices=list(discord_ranks.keys()))
    ):
        db = DB(); db.connect()
        # Fetch invoker and target ranks
        db.cursor.execute(
            "SELECT rank FROM discord_links WHERE discord_id = %s",
            (ctx.user.id,)
        )
        inv = db.cursor.fetchone()
        if not inv:
            await ctx.respond(':no_entry: You must link your account before assigning ranks.', ephemeral=True)
            db.close()
            return
        initiator_rank = inv[0]
        initiator_index = list(discord_ranks).index(initiator_rank)

        # Prevent self-assignment
        if user.id == ctx.user.id:
            await ctx.respond(':no_entry: You cannot change your own rank.', ephemeral=True)
            db.close()
            return

        db.cursor.execute(
            "SELECT rank FROM discord_links WHERE discord_id = %s",
            (user.id,)
        )
        tgt = db.cursor.fetchone()
        if not tgt:
            # Let the existing modal handle linking
            rows = True
        else:
            current_rank = tgt[0]
            target_index = list(discord_ranks).index(current_rank)
            if target_index >= initiator_index:
                await ctx.respond(':no_entry: You can only change ranks for members below your own.', ephemeral=True)
                db.close()
                return

        # Proceed with role updates
        db.cursor.execute(
            "SELECT ign FROM discord_links WHERE discord_id = %s", (user.id,)
        )
        rows = db.cursor.fetchall()

        added = 'Added Roles:'
        removed = 'Removed Roles:'
        all_roles = ctx.guild.roles

        if rows:
            await ctx.defer(ephemeral=True)
            # Apply new rank roles
            for role_name in discord_ranks[rank]['roles']:
                role_obj = discord.utils.get(all_roles, name=role_name)
                if role_obj and role_obj not in user.roles:
                    await user.add_roles(role_obj)
                    added += f"\n - {role_name}"
            # Remove old rank roles
            for role_name in [r for r in discord_rank_roles if r not in discord_ranks[rank]['roles']]:
                role_obj = discord.utils.get(all_roles, name=role_name)
                if role_obj and role_obj in user.roles:
                    await user.remove_roles(role_obj)
                    removed += f"\n - {role_name}"
            # Update DB
            db.cursor.execute(
                "UPDATE discord_links SET rank = %s WHERE discord_id = %s",
                (rank, user.id)
            )
            db.connection.commit()
            # Update nickname
            try:
                current = user.nick or user.name
                parts = current.split(' ', 1)
                base = parts[1] if len(parts) > 1 else parts[0]
                await user.edit(nick=f"{rank} {base}")
            except:
                pass
            await ctx.followup.send(f"{added}\n\n{removed}", ephemeral=True)
        else:
            modal = LinkAccount(
                title="Link User to Minecraft IGN",
                user=user,
                rank=rank,
                added=added,
                removed=removed
            )
            await ctx.interaction.response.send_modal(modal)
        db.close()

    @manage_group.command(name='shells', description='Add or remove shells from a user')
    async def shells(
        self,
        ctx: ApplicationContext,
        operation: discord.Option(str, choices=['add','remove']),
        user: discord.Member,
        amount: int,
        # reason: discord.Option(str, required=False, default='')
    ):
        db = DB(); db.connect()
        db.cursor.execute(
            "SELECT ign FROM discord_links WHERE discord_id = %s", (user.id,)
        )
        rows = db.cursor.fetchall()

        if rows:
            await ctx.defer()
            ign = rows[0][0]

            # Ensure user exists in shells table
            db.cursor.execute('SELECT shells, balance FROM shells WHERE "user" = %s', (str(user.id),))
            row2 = db.cursor.fetchone()

            if row2:
                current_shells, current_balance = row2
            else:
                current_shells, current_balance = 0, 0
                db.cursor.execute(
                    'INSERT INTO shells ("user", shells, balance, ign) VALUES (%s, %s, %s, %s)',
                    (str(user.id), 0, 0, ign)
                )
                db.connection.commit()

            player = PlayerShells(user.id)
            img = Image.new('RGBA', (375, 95), '#100010e2')
            draw = ImageDraw.Draw(img); draw.fontmode = '1'
            font = ImageFont.truetype('images/profile/game.ttf', 19)

            try:
                headers = {'User-Agent': os.getenv("visage_UA")}
                url = f"https://visage.surgeplay.com/bust/75/{player.UUID}"
                skin = Image.open(BytesIO(requests.get(url, headers=headers).content))
            except:
                skin = Image.open('images/profile/X-Steve.webp')

            img.paste(skin, (10, 10), skin)

            if operation == 'add':
                new_total = current_shells + amount
                new_balance = current_balance + amount
                diff = f'+{amount}'
                db.cursor.execute(
                    'UPDATE shells SET shells = %s, balance = %s, ign = %s WHERE "user" = %s',
                    (new_total, new_balance, ign, str(user.id))
                )
            else:
                new_balance = current_balance - amount
                diff = f'-{amount}'
                db.cursor.execute(
                    'UPDATE shells SET balance = %s, ign = %s WHERE "user" = %s',
                    (new_balance, ign, str(user.id))
                )
                new_total = current_shells

            addLine(f'&f{player.username}', draw, font, 95, 15)
            addLine(f'&7Balance: &f{new_balance} &7({diff}&7)', draw, font, 95, 40)
            if operation == 'add':
                addLine(f'&7All-Time: &f{new_total} &7({diff}&7)', draw, font, 95, 61)
            else:
                addLine(f'&7All-Time: &f{new_total}', draw, font, 95, 61)

            db.connection.commit()

            try:
                db.cursor.execute(
                    "INSERT INTO audit_log (log_type, actor_name, actor_id, action) VALUES (%s, %s, %s, %s)",
                    ('shell', ctx.user.name, ctx.user.id, f'{operation}ed {amount} to {player.username}.')
                )
                db.connection.commit()
            except Exception as e:
                print(f"[manage shells] audit_log write failed: {e}")

            img = ImageOps.expand(img, border=(2,2), fill='#100010e2')
            draw = ImageDraw.Draw(img)
            draw.rectangle((2, 2, img.width - 3, img.height - 3), outline='#240059', width=2)
            buf = BytesIO(); img.save(buf, 'PNG'); buf.seek(0)
            file = discord.File(buf, filename=f"shells_{int(time.time())}.png")
            await ctx.followup.send(file=file)
        else:
            modal = ShellModalName(
                title="Link User to Minecraft IGN",
                user=user,
                operation=operation,
                amount=amount,
                # set to empty for now
                reason=''
            )
            await ctx.interaction.response.send_modal(modal)
        db.close()

    @manage_group.command(name='link', description='Link a user to an IGN')
    async def link(
        self,
        ctx: ApplicationContext,
        user: discord.Member,
        ign: str
    ):
        await ctx.defer(ephemeral=True)
        db = DB(); db.connect()
        uuid = getPlayerUUID(ign)[1]
        db.cursor.execute(
            "SELECT * FROM discord_links WHERE discord_id = %s", (user.id,)
        )
        if db.cursor.fetchone():
            db.cursor.execute(
                "UPDATE discord_links SET ign = %s, uuid = %s WHERE discord_id = %s",
                (ign, uuid, user.id)
            )
            db.cursor.execute(
                "INSERT INTO shells (\"user\") VALUES (%s) ON CONFLICT DO NOTHING",
                (str(user.id),)
            )
            db.connection.commit()
            try:
                base = user.nick.split(' ')[0]
                await user.edit(nick=f"{base} {ign}")
            except:
                pass
            await ctx.followup.send(
                f'Updated link for **{user.name}** to **{ign}**',
                ephemeral=True
            )
        else:
            base = (user.nick.split(' ')[0] if user.nick else '')
            db.cursor.execute(
                "INSERT INTO discord_links (discord_id, ign, uuid, linked, rank) VALUES (%s,%s,%s,False,%s)",
                (user.id, ign, uuid, base)
            )
            db.connection.commit()
            await ctx.followup.send(
                f'Linked **{user.name}** to **{ign}**',
                ephemeral=True
            )
        db.close()

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Manage(client))
