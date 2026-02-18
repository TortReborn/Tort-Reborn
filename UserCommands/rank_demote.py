import discord
from discord.ext import commands
from discord.commands import user_command

from Helpers.classes import LinkAccount, NewMember
from Helpers.database import DB
from Helpers.functions import getPlayerUUID
from Helpers.variables import ALL_GUILD_IDS, discord_ranks, discord_rank_roles


class RankDemote(commands.Cog):
    def __init__(self, client):
        self.client = client

    @user_command(
        name='Rank | Demote',
        default_member_permissions=discord.Permissions(manage_roles=True),
        guild_ids=ALL_GUILD_IDS
    )
    async def demote_member(self, interaction: discord.Interaction, user: discord.Member):
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

        # Prevent self-promotion
        if user.id == interaction.user.id:
            embed = discord.Embed(
                title=':no_entry: Action forbidden',
                description='You cannot demote yourself.',
                color=0xe33232
            )
            await interaction.respond(embed=embed, ephemeral=True)
            db.close()
            return

        # Fetch the target user's Discord rank and UUID
        db.cursor.execute(
            f"SELECT rank, uuid FROM discord_links WHERE discord_id = {user.id}"
        )
        row = db.cursor.fetchone()

        # Check if target is linked
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

        current_rank, uuid = row
        current_rank_index = list(discord_ranks).index(current_rank)

        # Only allow demoting members with a lower rank than the initiator
        if current_rank_index >= initiator_index:
            embed = discord.Embed(
                title=':no_entry: Permission denied',
                description='You can only demote members with a lower rank than your own.',
                color=0xe33232
            )
            await interaction.respond(embed=embed, ephemeral=True)
            db.close()
            return

        # Prevent demoting the lowest rank further
        if current_rank_index == 0:
            embed = discord.Embed(
                title=':warning: Oops! We\'ve hit bedrock bottom.',
                description=(
                    f'Cannot demote any further. <@{user.id}> is already at the lowest rank.'
                ),
                color=0xebdb34
            )
            await interaction.respond(embed=embed, ephemeral=True)
            db.close()
            return

        # Determine the new lower rank
        new_rank_key = list(discord_ranks)[current_rank_index - 1]
        new_rank = discord_ranks[new_rank_key]
        all_roles = interaction.guild.roles

        # Determine roles to add and remove
        roles_to_add = []
        for add_role_name in new_rank['roles']:
            role = discord.utils.find(lambda r: r.name == add_role_name, all_roles)
            if role and role not in user.roles:
                roles_to_add.append(role)

        roles_to_remove = []
        to_remove_names = [r for r in discord_rank_roles if r not in new_rank['roles']]
        for remove_role_name in to_remove_names:
            role = discord.utils.find(lambda r: r.name == remove_role_name, all_roles)
            if role and role in user.roles:
                roles_to_remove.append(role)

        # Apply role changes
        if roles_to_add:
            await user.add_roles(
                *roles_to_add,
                reason=f'Demotion (ran by {interaction.user.name})',
                atomic=True
            )
        if roles_to_remove:
            await user.remove_roles(
                *roles_to_remove,
                reason=f'Demotion (ran by {interaction.user.name})',
                atomic=True
            )

        # Update the nickname if possible
        try:
            parts = user.nick.split(' ', 1)
            base = parts[1] if len(parts) > 1 else parts[0]
            await user.edit(nick=f'{new_rank_key} {base}')
        except Exception:
            pass

        # Persist the new rank to the database
        db.cursor.execute(
            f"UPDATE discord_links SET rank = '{new_rank_key}' WHERE discord_id = {user.id}"
        )
        db.connection.commit()
        db.close()

        # Confirm success
        embed = discord.Embed(
            title=':white_check_mark: Demotion successful',
            description=f'<@{user.id}> demoted to **{new_rank_key}**',
            color=0x3ed63e
        )
        await interaction.respond(embed=embed)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(RankDemote(client))
