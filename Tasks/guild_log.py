import asyncio
import datetime
import json
import time
import dateutil
import discord
from discord.ext import tasks, commands

from Helpers.classes import Guild
from Helpers.database import DB
from Helpers.functions import savePlayers, date_diff, getPlayerDatav3
from Helpers.variables import test, error_channel


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

                # Update recruiter tracking sheet
                try:
                    from Helpers.sheets import find_by_ign, update_type, update_paid
                    sheet_row = await asyncio.to_thread(find_by_ign, player['name'])

                    # UUID fallback: if not found by API name, try DB-stored IGN
                    sheet_ign = player['name']
                    if not (sheet_row.get("success") and sheet_row.get("data")):
                        alt_ign = await asyncio.to_thread(self._db_get_ign_by_uuid, uuid)
                        if alt_ign and alt_ign.lower() != player['name'].lower():
                            sheet_row = await asyncio.to_thread(find_by_ign, alt_ign)
                            if sheet_row.get("success") and sheet_row.get("data"):
                                sheet_ign = alt_ign

                    if sheet_row.get("success") and sheet_row.get("data"):
                        await asyncio.to_thread(update_type, sheet_ign, "Left")
                        paid = sheet_row["data"].get("paid", "")
                        if paid in ("NYP", "NP"):
                            await asyncio.to_thread(update_paid, sheet_ign, "LG")
                    else:
                        # Send diagnostic if we couldn't find them at all
                        alt_ign = await asyncio.to_thread(self._db_get_ign_by_uuid, uuid)
                        err_ch = self.client.get_channel(error_channel)
                        if err_ch:
                            await err_ch.send(
                                f"## Recruiter Tracker - Leave: IGN Not Found\n"
                                f"**API Name:** `{player['name']}` | "
                                f"**DB Name:** `{alt_ign or 'N/A'}` | "
                                f"**UUID:** `{uuid}`\n"
                                f"Player left guild but was not found on the recruiter sheet."
                            )
                except Exception as e:
                    err_ch = self.client.get_channel(error_channel)
                    if err_ch:
                        await err_ch.send(
                            f"## Recruiter Tracker - Leave Update Error\n"
                            f"**Player:** `{player['name']}`\n"
                            f"```\n{str(e)[:500]}\n```"
                        )

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
                    if test:
                        guild_general = self.client.get_channel(1367285315236008036)
                    else:
                        guild_general = self.client.get_channel(748900470575071293)
                    welcome_msg = f"Dive right in, <@{rows[0][0]}>! The water's fine."
                    embed = discord.Embed(title='',
                                          description=f':ocean: {welcome_msg}',
                                          color=0x4287f5)
                    await guild_general.send(embed=embed)
                    ping_msg = await guild_general.send(f"<@{rows[0][0]}>")
                    await ping_msg.delete()

                await channel.send(
                    '🟩 <t:' + str(int(u_timenow)) + ':d> <t:' + str(int(u_timenow)) + ':t> | **' + player[
                        'name'].replace(
                        '_', '\\_') + f'** {discord_id} joined the guild! | ' + player['rank'].upper())

    @staticmethod
    def _db_get_ign_by_uuid(uuid: str) -> str | None:
        """Blocking: look up stored IGN by UUID in discord_links."""
        db = DB(); db.connect()
        try:
            db.cursor.execute(
                "SELECT ign FROM discord_links WHERE uuid = %s",
                (uuid,)
            )
            row = db.cursor.fetchone()
            return row[0] if row else None
        finally:
            db.close()

    @guild_log.before_loop
    async def guild_log_before_loop(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.guild_log.is_running():   
            self.guild_log.start()


def setup(client):
    client.add_cog(GuildLog(client))
