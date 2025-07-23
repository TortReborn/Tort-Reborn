import asyncio
import json
import re
import time
from datetime import datetime
from io import BytesIO

import discord
from discord.ext import commands

from Helpers.classes import BasicPlayerStats
from Helpers.database import DB
from Helpers.functions import generate_applicant_info


class OnMessage(commands.Cog):
    def __init__(self, client):
        self.client = client

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.client.user:
            return

        if message.author.bot and message.author.discriminator != '0000':
            return

        if message.channel.name.startswith('ticket-') and message.channel.category.name == 'Guild Applications':
            if not message.author.bot:
                role = discord.utils.find(lambda r: r.name == 'Member', message.guild.roles)
                if role in message.author.roles:
                    return
            db = DB()
            db.connect()
            db.cursor.execute(f'SELECT * FROM new_app WHERE channel = \'{message.channel.id}\'')
            result = db.cursor.fetchone()
            if result:
                if result[1] == 0:
                    if message.guild.id != 729147655875199017:
                        # Updated 4/30/2025
                        ch = self.client.get_channel(1367283441850122330)  # test
                    else:
                        ch = self.client.get_channel(889162191150931978)

                    embed_title = f'Application {message.channel.name.replace("ticket-", "")}'
                    embed_description = ''
                    mc_name = ''
                    # check if application is sent by bot
                    if message.author.bot:
                        if message.embeds[0].title == ':no_entry: Oops! Something did not go as intended.':
                            return
                        for field in message.embeds[0].fields:
                            if field.name == 'Minecraft Username':
                                mc_name = field.value
                                break
                    # else search for stats link in message content
                    else:
                        stats_link = re.findall("wynncraft\\.com\\/stats\\/player.*", message.content)
                        if stats_link:
                            mc_name = stats_link[0].split('/')[-1]

                    pdata = BasicPlayerStats(mc_name)

                    if not pdata.error:
                        img = generate_applicant_info(pdata)

                        embed_title = f'Application {message.channel.name.replace("ticket-", "")} ({pdata.username})'

                        # blacklist check
                        with open('blacklist.json', 'r') as f:
                            blacklist = json.load(f)
                            f.close()

                        for player in blacklist:
                            if pdata.UUID == player['UUID']:
                                embed_description = f':no_entry: Player present on blacklist!\n**Name:** {pdata.username}\n**UUID:** {pdata.UUID}'

                    embed = discord.Embed(title=embed_title, description=embed_description, colour=0x3ed63e)
                    embed.add_field(name='Channel', value=f':link: <#{message.channel.id}>')
                    embed.add_field(name='Status', value=':green_circle: Opened')

                    if not pdata.error:
                        with BytesIO() as file:
                            img.save(file, format="PNG")
                            file.seek(0)
                            t = int(time.time())
                            player_info = discord.File(file, filename=f"{message.channel.name.replace('ticket-', '')}-{pdata.UUID}.png")
                            embed.set_image(url=f"attachment://{message.channel.name.replace('ticket-', '')}-{pdata.UUID}.png")
                        msg = await ch.send(f'<@&870767928704921651>', embed=embed, file=player_info)
                        player_uuid = pdata.UUID
                    else:
                        player_uuid = ''
                        msg = await ch.send(f'<@&870767928704921651>', embed=embed)
                    thread = await msg.create_thread(name=message.channel.name.replace('ticket-', ''),
                                                     auto_archive_duration=1440)
                    await msg.add_reaction('👍')
                    await msg.add_reaction('🤷')
                    await msg.add_reaction('👎')

                    db.cursor.execute(
                        f'UPDATE new_app SET created=\'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\', posted = True, notif_msg_id = \'{msg.id}\', thread_id = \'{thread.id}\', uuid = \'{player_uuid}\' WHERE channel = \'{message.channel.id}\'')
                    db.connection.commit()
            db.close()
        elif message.channel.id == 729163031321509938:
            if 'how long' in message.content.lower() and 'why' in message.content.lower() and 'kick' in message.content.lower():
                pass
            else:
                await message.delete()
                reply = await message.channel.send(':no_entry: Please use the format in pinned messages.')
                await asyncio.sleep(5)
                await reply.delete()

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(OnMessage(client))
