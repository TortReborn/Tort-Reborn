import discord
from discord.ext import commands
from discord.commands import slash_command
from discord import default_permissions

from Helpers.classes import BasicPlayerStats
from Helpers.database import DB
from Helpers.variables import HOME_GUILD_IDS, discord_ranks


class ResetRolesCommand(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(guild_ids=HOME_GUILD_IDS, description="HR: Reset a user's roles")
    @default_permissions(manage_roles=True)
    async def reset_roles(self, message, user: discord.Member):
        if not message.interaction.user.guild_permissions.manage_roles:
            await message.respond('You are missing Manage Roles permission(s) to run this command.', ephemeral=True)
            return

        await message.defer(ephemeral=True)
        db = DB()
        db.connect()
        try:
            # Check initiator's rank
            db.cursor.execute(
                'SELECT rank FROM discord_links WHERE discord_id = %s',
                (message.interaction.user.id,)
            )
            initiator_row = db.cursor.fetchone()
            if not initiator_row:
                embed = discord.Embed(
                    title=':no_entry: Oops!',
                    description='You do not have a linked account.\nPlease use the `/manage link` command first.',
                    color=0xe33232
                )
                await message.respond(embed=embed, ephemeral=True)
                return

            initiator_rank = initiator_row[0]
            initiator_index = list(discord_ranks).index(initiator_rank)

            # Check target's rank
            db.cursor.execute(
                'SELECT rank FROM discord_links WHERE discord_id = %s',
                (user.id,)
            )
            target_row = db.cursor.fetchone()
            if target_row:
                target_rank = target_row[0]
                target_index = list(discord_ranks).index(target_rank)

                # Only allow resetting roles of members with a lower rank
                if target_index >= initiator_index:
                    embed = discord.Embed(
                        title=':no_entry: Permission denied',
                        description='You can only reset roles for members with a lower rank than your own.',
                        color=0xe33232
                    )
                    await message.respond(embed=embed, ephemeral=True)
                    return

            all_roles = message.interaction.guild.roles
            to_remove = ['Member', 'The Aquarium [TAq]', '☆Reef', 'Starfish', 'Manatee', '★Coastal Waters', 'Piranha',
                         'Barracuda', '★★ Azure Ocean', 'Angler', '★☆☆ Blue Sea', 'Hammerhead', '★★☆Deep Sea',
                         'Sailfish', '★★★Dark Sea', 'Dolphin', 'Narwhal', '★★★★Abyss Waters',
                         '🛡️MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀', '🛡️SR. MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
                         '🥇 RANKS⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀', '🛠️ PROFESSIONS⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
                         '✨ COSMETIC ROLES⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀', '🎖️MILITARY⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀', '🏹Spearhead',
                         '⚠️Standby', '🗡️FFA', 'DPS', 'Tank', 'Healer', 'Orca', 'War News', 'EcoFish',
                         '🏆 CONTRIBUTION ROLES⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀']
            roles_to_remove = []
            to_add = ['Ex-Member']
            roles_to_add = []

            for add_role in to_add:
                role = discord.utils.find(lambda r: r.name == add_role, all_roles)
                if role and role not in user.roles:
                    roles_to_add.append(role)

            if roles_to_add:
                await user.add_roles(*roles_to_add, reason=f'Roles reset (ran by {message.author.name})')

            for remove_role in to_remove:
                role = discord.utils.find(lambda r: r.name == remove_role, all_roles)
                if role and role in user.roles:
                    roles_to_remove.append(role)

            if roles_to_remove:
                await user.remove_roles(*roles_to_remove, reason=f'Roles reset (ran by {message.author.name})')
            await user.edit(nick='')
        finally:
            db.close()

        embed = discord.Embed(title=':white_check_mark: Roles reset',
                              description=f'Roles were reset for <@{user.id}>', color=0x3ed63e)
        await message.respond(embed=embed)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(ResetRolesCommand(client))
