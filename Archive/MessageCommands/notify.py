import json
import time
from datetime import datetime
from io import BytesIO
import re

import discord
from discord import message_command
from discord.ext import commands

from Helpers.classes import BasicPlayerStats
from Helpers.database import DB
from Helpers.functions import generate_applicant_info
from Helpers.variables import member_app_channel


class ApplicationNotify(commands.Cog):
    def __init__(self, client):
        self.client = client

    @message_command(name='Application | Notify', default_member_permissions=discord.Permissions(manage_roles=True))
    async def application_notify(self, ctx, message):
        if ctx.interaction.user.guild_permissions.manage_roles:
            await ctx.defer(ephemeral=True)
            db = DB()
            db.connect()
            db.cursor.execute(f'SELECT * FROM new_app WHERE channel = \'{message.channel.id}\'')
            application = db.cursor.fetchone()
            if application:
                if ctx.interaction.guild.id != 729147655875199017:
                    # UPDATED 4/30/2025
                    ch = self.client.get_channel(member_app_channel)  # test
                else:
                    ch = self.client.get_channel(member_app_channel)
                if application[1] == 1:
                    try:
                        orig_msg = await ch.fetch_message(application[6])
                        orig_thread = orig_msg.thread
                        await orig_thread.delete()
                        await orig_msg.delete()
                    except Exception as e:
                        print(e)

                embed_title = f'Application {application[2].replace("ticket-", "")}'
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

                    embed_title = f'Application {application[2].replace("ticket-", "")} ({pdata.username})'

                    # blacklist check
                    with open('blacklist.json', 'r') as f:
                        blacklist = json.load(f)
                        f.close()

                    for player in blacklist:
                        if pdata.UUID == player['UUID']:
                            embed_description = f':no_entry: Player present on blacklist!\n**Name:** {pdata.username}\n**UUID:** {pdata.UUID}'

                embed = discord.Embed(title=embed_title, description=embed_description, colour=0x3ed63e)
                embed.add_field(name='Channel', value=f':link: <#{ctx.channel.id}>')
                embed.add_field(name='Status', value=':green_circle: Opened')

                if not pdata.error:
                    with BytesIO() as file:
                        img.save(file, format="PNG")
                        file.seek(0)
                        t = int(time.time())
                        player_info = discord.File(file,
                                                   filename=f"{application[2].replace('ticket-', '')}-{pdata.UUID}.png")
                        embed.set_image(
                            url=f"attachment://{application[2].replace('ticket-', '')}-{pdata.UUID}.png")
                    msg = await ch.send(f'<@&870767928704921651>', embed=embed, file=player_info)
                    player_uuid = pdata.UUID
                else:
                    player_uuid = ''
                    msg = await ch.send(f'<@&870767928704921651>', embed=embed)
                thread = await msg.create_thread(name=application[2].replace("ticket-", ""),
                                                 auto_archive_duration=1440)
                await msg.add_reaction('👍')
                await msg.add_reaction('🤷')
                await msg.add_reaction('👎')

                db.cursor.execute(
                    f'UPDATE new_app SET created=\'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\', posted = True, notif_msg_id = \'{msg.id}\', thread_id = \'{thread.id}\', uuid = \'{player_uuid}\' WHERE channel = \'{ctx.interaction.channel.id}\'')
                db.connection.commit()
                embed = discord.Embed(title='',
                                      description=f':white_check_mark: Application notification sent.',
                                      color=0x34eb40)
                await ctx.respond(embed=embed, delete_after=5)
            else:
                embed = discord.Embed(title=':information_source: Oops!',
                                      description=f'This command can only be used inside application channels.',
                                      color=0x4287f5)
                await ctx.respond(embed=embed)
            db.close()
        else:
            await ctx.respond(
                'You are missing Manage Roles permission(s) to run this command.', ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(ApplicationNotify(client))
