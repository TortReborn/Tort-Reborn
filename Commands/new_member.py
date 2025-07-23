import discord
from discord.ext import commands
from discord.commands import slash_command
from discord import default_permissions

from Helpers.classes import LinkAccount, PlayerStats, BasicPlayerStats
from Helpers.database import DB
from Helpers.functions import getPlayerUUID
from Helpers.variables import guilds


class NewMember(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(guild_ids=guilds)
    @default_permissions(manage_roles=True)
    async def new_member(self, message, user: discord.Member, ign):
        if message.interaction.user.guild_permissions.manage_roles:
            db = DB()
            db.connect()
            db.cursor.execute(f'SELECT * FROM discord_links WHERE discord_id = \'{user.id}\'')
            rows = db.cursor.fetchall()
            await message.defer(ephemeral=True)
            pdata = BasicPlayerStats(ign)
            if pdata.error:
                embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                      description=f'Could not retrieve information of `{ign}`.\nPlease check your spelling or try again later.',
                                      color=0xe33232)
                await message.respond(embed=embed, ephemeral=True)
                return

            to_remove = ['Land Crab', 'Honored Fish', 'Ex-Member']
            to_add = ['Member', 'The Aquarium [TAq]', '☆Reef', 'Starfish', '🥇 RANKS⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
                      '🛠️ PROFESSIONS⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀', '✨ COSMETIC ROLES⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀']
            roles_to_add = []
            roles_to_remove = []
            all_roles = message.guild.roles
            for add_role in to_add:
                role = discord.utils.find(lambda r: r.name == add_role, all_roles)
                if role not in user.roles:
                    roles_to_add.append(role)

            await user.add_roles(*roles_to_add, reason=f"New member registration (ran by {message.author.name})", atomic=True)

            for remove_role in to_remove:
                role = discord.utils.find(lambda r: r.name == remove_role, all_roles)
                if role in user.roles:
                    roles_to_remove.append(role)

            await user.remove_roles(*roles_to_remove, reason=f"New member registration (ran by {message.author.name})", atomic=True)

            if len(rows) != 0:
                db.cursor.execute(
                    f'UPDATE discord_links SET rank = \'Starfish\', ign = \'{ign}\', wars_on_join = {pdata.wars}, uuid= \'{pdata.UUID}\' WHERE discord_id = \'{user.id}\'')
                db.connection.commit()
            else:
                db.cursor.execute(
                    f'INSERT INTO discord_links (discord_id, ign, uuid, linked, rank, wars_on_join) VALUES ({user.id}, \'{pdata.username}\',\'{pdata.UUID}\' , 0, \'Starfish\', {pdata.wars});')
                db.connection.commit()
            db.close()
            await user.edit(nick="Starfish " + ign)
            embed = discord.Embed(title=':white_check_mark: New member registered', description=f'<@{user.id}> was linked to `{pdata.username}`', color=0x3ed63e)
            await message.respond(embed=embed)
        else:
            await message.respond(
                'You are missing Manage Roles permission(s) to run this command.')

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(NewMember(client))
