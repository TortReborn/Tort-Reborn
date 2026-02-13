import discord
from discord.ext import commands
from discord.commands import slash_command
from discord import default_permissions

from Helpers.classes import LinkAccount, PlayerStats, BasicPlayerStats
from Helpers.database import DB
from Helpers.functions import getPlayerUUID, determine_starting_rank
from Helpers.variables import guilds, discord_ranks


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

            starting_rank = determine_starting_rank(user)
            rank_roles = discord_ranks[starting_rank]['roles']

            to_remove = ['Land Crab', 'Honored Fish', 'Retired Chief', 'Ex-Member']
            to_add = ['Member', 'The Aquarium [TAq]', *rank_roles, '🥇 RANKS⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
                      '🛠️ PROFESSIONS⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀', '✨ COSMETIC ROLES⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
                      'CONTRIBUTION ROLES⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀']
            roles_to_add = []
            roles_to_remove = []
            missing_roles = []
            all_roles = message.guild.roles

            # Validate roles to add
            for add_role in to_add:
                role = discord.utils.find(lambda r: r.name == add_role, all_roles)
                if role is None:
                    missing_roles.append(add_role)
                elif role not in user.roles:
                    roles_to_add.append(role)

            # Log and report missing roles
            if missing_roles:
                error_msg = f"⚠️ Warning: The following roles do not exist in this server:\n"
                for role_name in missing_roles:
                    error_msg += f"• `{role_name}`\n"
                error_msg += "\nPlease create these roles or update the command configuration."

                embed = discord.Embed(
                    title=':warning: Missing Roles Configuration Error',
                    description=error_msg,
                    color=0xff9900
                )
                await message.respond(embed=embed, ephemeral=True)
                return

            if roles_to_add:
                await user.add_roles(*roles_to_add, reason=f"New member registration (ran by {message.author.name})", atomic=True)

            # Validate roles to remove
            for remove_role in to_remove:
                role = discord.utils.find(lambda r: r.name == remove_role, all_roles)
                if role is not None and role in user.roles:
                    roles_to_remove.append(role)

            if roles_to_remove:
                await user.remove_roles(*roles_to_remove, reason=f"New member registration (ran by {message.author.name})", atomic=True)

            if len(rows) != 0:
                db.cursor.execute(
                    'UPDATE discord_links SET rank = %s, ign = %s, wars_on_join = %s, uuid = %s WHERE discord_id = %s',
                    (starting_rank, ign, pdata.wars, pdata.UUID, user.id))
                db.connection.commit()
            else:
                db.cursor.execute(
                    'INSERT INTO discord_links (discord_id, ign, uuid, linked, rank, wars_on_join) VALUES (%s, %s, %s, False, %s, %s)',
                    (user.id, pdata.username, pdata.UUID, starting_rank, pdata.wars))
                db.connection.commit()
            db.close()
            await user.edit(nick=f"{starting_rank} {ign}")
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
