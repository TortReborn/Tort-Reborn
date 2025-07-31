import discord
from discord.ext import commands
from discord.commands import user_command

from Helpers.classes import BasicPlayerStats
from Helpers.database import DB
from Helpers.variables import guilds, discord_ranks


class ResetRoles(commands.Cog):
    def __init__(self, client):
        self.client = client

    @user_command(
        name='Member | Remove',
        default_member_permissions=discord.Permissions(manage_roles=True),
        guild_ids=guilds
    )
    async def reset_roles(self, interaction: discord.Interaction, user: discord.Member):
        # Ensure the invoker has the Manage Roles permission
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message(
                'You are missing Manage Roles permission(s) to run this command.',
                ephemeral=True
            )
            return

        await interaction.defer(ephemeral=True)
        db = DB()
        db.connect()

        # Fetch the invoker's Discord rank
        db.cursor.execute(
            f"SELECT rank FROM discord_links WHERE discord_id = {interaction.user.id}"
        )
        initiator_row = db.cursor.fetchone()
        if not initiator_row:
            embed = discord.Embed(
                title=':no_entry: Oops!',
                description=(
                    'You do not have a linked account.\n'
                    'Please use the `/manage link` command first.'
                ),
                color=0xe33232
            )
            await interaction.respond(embed=embed, ephemeral=True)
            db.close()
            return

        initiator_rank = initiator_row[0]
        initiator_index = list(discord_ranks).index(initiator_rank)

        # Fetch the target user's Discord rank and UUID
        db.cursor.execute(
            f"SELECT rank, uuid FROM discord_links WHERE discord_id = {user.id}"
        )
        row = db.cursor.fetchone()
        if not row:
            embed = discord.Embed(
                title=':no_entry: Oops! Something did not go as intended.',
                description=(
                    f'<@{user.id}> does not have a linked account.\n'
                    'Please use the `/manage link` command first.'
                ),
                color=0xe33232
            )
            await interaction.respond(embed=embed, ephemeral=True)
            db.close()
            return

        target_rank, _ = row
        target_index = list(discord_ranks).index(target_rank)

        # Only allow resetting roles of members with a lower rank than the initiator
        if target_index >= initiator_index:
            embed = discord.Embed(
                title=':no_entry: Permission denied',
                description='You can only reset roles for members with a lower rank than your own.',
                color=0xe33232
            )
            await interaction.respond(embed=embed, ephemeral=True)
            db.close()
            return

        # Optional: gather player stats (unused)
        # pdata = BasicPlayerStats(row[2]) if row else None

        all_roles = interaction.guild.roles
        # Static list of roles to remove and roles to add
        to_remove = [
            'Member', 'The Aquarium [TAq]', '☆Reef', 'Starfish', 'Manatee',
            '★Coastal Waters', 'Piranha', 'Barracuda', '★★ Azure Ocean', 'Angler',
            '★☆☆ Blue Sea', 'Hammerhead', '★★☆Deep Sea', 'Sailfish',
            '★★★Dark Sea', 'Dolphin', 'Trial-Chief', 'Narwhal', '★★★★Abyss Waters',
            '🛡️MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
            '🛡️SR. MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
            '🥇 RANKS⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
            '🛠️ PROFESSIONS⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
            '✨ COSMETIC ROLES⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
            '🎖️MILITARY⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
            '🏹Spearhead', '⚠️Standby', '🗡️FFA', 'DPS', 'Tank', 'Healer', 'Orca',
            'War News', 'EcoFish'
        ]
        to_add = ['Ex-Member']

        roles_to_add = [r for r in all_roles if r.name in to_add and r not in user.roles]
        roles_to_remove = [r for r in all_roles if r.name in to_remove and r in user.roles]

        # Apply roles
        if roles_to_add:
            await user.add_roles(*roles_to_add, reason=f'Roles reset (ran by {interaction.user.name})')
        if roles_to_remove:
            await user.remove_roles(*roles_to_remove, reason=f'Roles reset (ran by {interaction.user.name})')

        # Clear nickname
        await user.edit(nick='')

        db.close()
        
        embed = discord.Embed(
            title=':white_check_mark: Roles reset',
            description=f'Roles were reset for <@{user.id}>',
            color=0x3ed63e
        )
        await interaction.respond(embed=embed)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(ResetRoles(client))
