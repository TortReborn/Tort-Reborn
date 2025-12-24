import datetime
import json
import os
import time
from io import BytesIO

import discord
import requests
from PIL import Image
from discord import option, default_permissions, slash_command
from discord.ext import commands
from discord.ui import View, Modal, InputText

from Helpers.database import DB, get_current_guild_data
from Helpers.functions import getPlayerUUID
from Helpers.variables import test, te, promotion_channel, guilds


async def get_members(message: discord.AutocompleteContext):
    current_data = get_current_guild_data()
    members = current_data.get('members', []) if isinstance(current_data, dict) else current_data

    member_list = []
    for member in members:
        member_list.append(member['name'])
    return [member for member in member_list if message.value.lower() in member.lower()]


class LockRecord(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Lock', emoji='🔒', custom_id='lock-button', style=discord.ButtonStyle.gray)
    async def return_item(self, button, ctx: discord.ApplicationContext):
        await ctx.response.send_modal(LockConfirmation(title='Lock Confirmation', message=ctx.message))


class LockConfirmation(Modal):
    def __init__(self, message, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.message = message
        self.add_item(InputText(label="Locking prevents any new entries to be added", placeholder="Type CONFIRM to confirm this operation."))

    async def callback(self, interaction: discord.Interaction):
        if self.children[0].value == 'CONFIRM':
            embed = self.message.embeds[0]
            username, UUID = getPlayerUUID(embed.description.replace('## ', ''))
            embed.color = 0x7a7a7a
            db = DB()
            db.connect()
            db.cursor.execute(f'DELETE FROM promotion_suggestions WHERE uuid = \'{UUID}\'')
            db.connection.commit()
            embed.set_thumbnail(url=f"attachment://skin_{UUID}.png")
            embed.set_footer(text=f'Locked by {interaction.user.name}')
            await self.message.edit(embed=embed, view=None)
            await interaction.response.send_message(f'Promotion suggestion record locked', delete_after=5, ephemeral=True)
            db.close()
        else:
            await interaction.response.send_message(f'Operation aborted', delete_after=5,
                                                    ephemeral=True)


class SuggestPromotion(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.promo_channel = promotion_channel

    @slash_command(description="Suggest promotion of a member", guild_ids=[te, guilds[1]])
    @option("member", description="In-Game name (case-sensitive)", autocomplete=get_members)
    async def suggest_promotion(self, message, member: str, description: str):
        await message.defer(ephemeral=True)
        taq_member = False
        username, UUID = getPlayerUUID(member)

        current_data = get_current_guild_data()
        members = current_data.get('members', []) if isinstance(current_data, dict) else current_data

        for m in members:
            if m['uuid'] == UUID:
                taq_member = True
                break

        if not taq_member:
            embed = discord.Embed(title=':information_source: Oops!',
                                  description=f'`{member}` not found in TAq member list.',
                                  color=0x4287f5)
            await message.respond(embed=embed)
            return

        db = DB()
        db.connect()

        db.cursor.execute(f'SELECT * FROM promotion_suggestions WHERE uuid = \'{UUID}\'')
        result = db.cursor.fetchone()

        try:
            headers = {'User-Agent': os.getenv("visage_UA")}
            url = f"https://visage.surgeplay.com/bust/500/{UUID}"
            response = requests.get(url, headers=headers)
            skin = Image.open(BytesIO(response.content))
        except Exception as e:
            print(e)
            skin = Image.open('images/profile/x-steve500.png')

        u_timenow = time.mktime(datetime.datetime.now().timetuple())
        embed = discord.Embed(title='', description=f'## {username}', color=0x3ed63e)

        with BytesIO() as file:
            skin.save(file, format="PNG")
            file.seek(0)
            skin = discord.File(file, filename=f"skin_{UUID}.png")
            embed.set_thumbnail(url=f"attachment://skin_{UUID}.png")

        if result:
            entries = json.loads(result[2])
            msg = await message.guild.get_channel(self.promo_channel).fetch_message(result[1])
            entries.append({"user_id": message.author.name, "description": description, "time": u_timenow})
            for i, entry in enumerate(entries):
                embed.add_field(
                    name=f'{i + 1}. {entry["user_id"]} <t:{str(int(entry["time"]))}:d> <t:{str(int(entry["time"]))}:t>',
                    value=entry["description"], inline=False)
            db.cursor.execute(
                f'UPDATE promotion_suggestions SET entries =  \'{json.dumps(entries)}\' WHERE uuid = \'{UUID}\'')
            db.connection.commit()
            await msg.edit(embed=embed, view=LockRecord())
            await message.respond('Entry added', delete_after=3)
        else:
            embed.add_field(name=f'1. {message.author.name} <t:{str(int(u_timenow))}:d> <t:{str(int(u_timenow))}:t>',
                            value=description)
            msg = await message.guild.get_channel(self.promo_channel).send(embed=embed, file=skin, view=LockRecord())
            db.cursor.execute(f'INSERT INTO promotion_suggestions (uuid, message_id, entries) VALUES '
                              f'(\'{UUID}\', \'{msg.id}\', \'{json.dumps([{"user_id": message.author.name, "description": description, "time": u_timenow}])}\')')
            db.connection.commit()
            await message.respond('Entry added', delete_after=3)
        db.close()

    @commands.Cog.listener()
    async def on_ready(self):
        self.client.add_view(LockRecord())
        pass


def setup(client):
    client.add_cog(SuggestPromotion(client))
