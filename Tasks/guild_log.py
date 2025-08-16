import datetime
import json
import time
import random

import dateutil
import discord
from discord.ext import tasks, commands

from Helpers.classes import Guild
from Helpers.database import DB
from Helpers.functions import savePlayers, date_diff, getPlayerDatav3
from Helpers.variables import test


class GuildLog(commands.Cog):
    def __init__(self, client):
        self.client = client

    @tasks.loop(minutes=1)
    async def guild_log(self):
        if test:
            guild_log = 'lunarity.json'
            new_data = Guild('Lunarity').all_members
            channel = self.client.get_channel(1367285315236008036)
        else:
            channel = self.client.get_channel(936679740385931414)
            guild_log = 'theaquarium.json'
            new_data = Guild('The%20Aquarium').all_members
        with open(guild_log, 'r') as f:
            old_data = json.loads(f.read())
        savePlayers(new_data)
        for player in old_data:
            uuid = player['uuid']
            found = False
            for item in new_data:
                if uuid == item['uuid']:
                    found = True
                    if player != item:
                        for key in player:
                            if player[key] != item[key]:
                                if key not in ['name', 'contributed', 'online', 'server', 'contributionRank']:
                                    db = DB()
                                    db.connect()
                                    db.cursor.execute(f'SELECT * FROM discord_links WHERE uuid = \'{uuid}\'')
                                    rows = db.cursor.fetchall()
                                    db.close()
                                    discord_id = f' (<@{rows[0][0]}>) ' if len(rows) != 0 else ''
                                    u_timenow = time.mktime(datetime.datetime.now().timetuple())
                                    await channel.send(
                                        '🟦 <t:' + str(int(u_timenow)) + ':d> <t:' + str(int(u_timenow)) + ':t> | **' +
                                        player['name'].replace('_', '\\_') + f'** {discord_id} | ' + player[key].upper() + ' ➜ ' + item[key].upper())
                                elif key == 'name':
                                    db = DB()
                                    db.connect()
                                    db.cursor.execute(f'SELECT * FROM discord_links WHERE uuid = \'{uuid}\'')
                                    rows = db.cursor.fetchall()
                                    db.close()
                                    discord_id = f' (<@{rows[0][0]}>) ' if len(rows) != 0 else ''
                                    u_timenow = time.mktime(datetime.datetime.now().timetuple())
                                    await channel.send(
                                        '🟦 <t:' + str(int(u_timenow)) + ':d> <t:' + str(int(u_timenow)) + ':t> | **' +
                                        player[key] + f'** {discord_id} ➜ ' + item[key])
                                else:
                                    pass
                    else:
                        pass
                else:
                    pass
            if not found:
                joined = dateutil.parser.isoparse(player['joined'])
                in_guild_for = datetime.datetime.now() - joined.replace(tzinfo=None)
                u_timenow = time.mktime(datetime.datetime.now().timetuple())
                try:
                    playerdata = getPlayerDatav3(player['uuid'])
                    test_last_joined = playerdata['lastJoin']
                    if test_last_joined:
                        lastjoined = dateutil.parser.isoparse(test_last_joined)
                    else:
                        # TAq Creation Date
                        lastjoined = dateutil.parser.isoparse("2020-03-22T11:11:17.810000Z")
                    lastseen = ' | Last seen **' + str(date_diff(lastjoined)) + '** days ago'
                except:
                    lastseen = ''
                db = DB()
                db.connect()
                db.cursor.execute(f'SELECT * FROM discord_links WHERE uuid = \'{uuid}\'')
                rows = db.cursor.fetchall()
                db.close()
                discord_id = f' (<@{rows[0][0]}>) ' if len(rows) != 0 else ''
                await channel.send(
                    '🟥 <t:' + str(int(u_timenow)) + ':d> <t:' + str(int(u_timenow)) + ':t> | **' + player[
                        'name'].replace('_', '\\_') + f'** {discord_id} has left the guild! | ' + player[
                        'rank'].upper() + ' | member for **' + str(in_guild_for.days) + f' days**{lastseen}')
        for player in new_data:
            uuid = player['uuid']
            found = False
            for item in old_data:
                if uuid == item['uuid']:
                    found = True
                    continue
            if not found:
                u_timenow = time.mktime(datetime.datetime.now().timetuple())
                db = DB()
                db.connect()
                db.cursor.execute(f'SELECT * FROM discord_links WHERE uuid = \'{uuid}\'')
                rows = db.cursor.fetchall()
                db.close()
                discord_id = f' (<@{rows[0][0]}>) ' if len(rows) != 0 else ''
                if len(rows) != 0:
                    with open('welcome_messages.txt', 'r') as f:
                        messages = f.readlines()
                        f.close()

                    messages.pop()

                    if test:
                        guild_general = self.client.get_channel(1367285315236008036)
                    else:
                        guild_general = self.client.get_channel(748900470575071293)
                    embed = discord.Embed(title='',
                                          description=f':ocean: {random.choice(messages).replace("[User]", f"<@{rows[0][0]}>")}',
                                          color=0x4287f5)
                    await guild_general.send(embed=embed)
                    ping_msg = await guild_general.send(f"<@{rows[0][0]}>")
                    await ping_msg.delete()

                await channel.send(
                    '🟩 <t:' + str(int(u_timenow)) + ':d> <t:' + str(int(u_timenow)) + ':t> | **' + player[
                        'name'].replace(
                        '_', '\\_') + f'** {discord_id} joined the guild! | ' + player['rank'].upper())

    @guild_log.before_loop
    async def guild_log_before_loop(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.guild_log.is_running():   
            self.guild_log.start()


def setup(client):
    client.add_cog(GuildLog(client))
