import discord
from discord.ext import commands
from discord.commands import user_command

from Helpers.classes import LinkAccount, NewMember
from Helpers.database import DB
from Helpers.functions import getPlayerUUID
from Helpers.variables import HOME_GUILD_IDS, discord_ranks, discord_rank_roles, ERROR_CHANNEL_ID


class RankDemote(commands.Cog):
    def __init__(self, client):
        self.client = client

    @user_command(
        name='Rank | Demote',
        default_member_permissions=discord.Permissions(manage_roles=True),
        guild_ids=HOME_GUILD_IDS
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
        try:
            # Fetch the invoker's Discord rank
            db.cursor.execute(
                "SELECT rank FROM discord_links WHERE discord_id = %s",
                (interaction.user.id,)
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
                return

            initiator_rank = initiator_row[0]
            if initiator_rank not in discord_ranks:
                embed = discord.Embed(
                    title=':no_entry: Error',
                    description=f'Your rank `{initiator_rank}` is not recognized. Please contact an admin.',
                    color=0xe33232
                )
                await interaction.respond(embed=embed, ephemeral=True)
                return
            initiator_index = list(discord_ranks).index(initiator_rank)

            # Prevent self-demotion
            if user.id == interaction.user.id:
                embed = discord.Embed(
                    title=':no_entry: Action forbidden',
                    description='You cannot demote yourself.',
                    color=0xe33232
                )
                await interaction.respond(embed=embed, ephemeral=True)
                return

            # Fetch the target user's Discord rank and UUID
            db.cursor.execute(
                "SELECT rank, uuid FROM discord_links WHERE discord_id = %s",
                (user.id,)
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
                return

            current_rank, uuid = row
            if current_rank not in discord_ranks:
                embed = discord.Embed(
                    title=':no_entry: Error',
                    description=f'Target\'s rank `{current_rank}` is not recognized. Please contact an admin.',
                    color=0xe33232
                )
                await interaction.respond(embed=embed, ephemeral=True)
                return
            current_rank_index = list(discord_ranks).index(current_rank)

            # Only allow demoting members with a lower rank than the initiator
            if current_rank_index >= initiator_index:
                embed = discord.Embed(
                    title=':no_entry: Permission denied',
                    description='You can only demote members with a lower rank than your own.',
                    color=0xe33232
                )
                await interaction.respond(embed=embed, ephemeral=True)
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

            # Apply role changes with rollback on partial failure
            added = False
            try:
                if roles_to_add:
                    await user.add_roles(
                        *roles_to_add,
                        reason=f'Demotion (ran by {interaction.user.name})',
                        atomic=True
                    )
                    added = True
                if roles_to_remove:
                    await user.remove_roles(
                        *roles_to_remove,
                        reason=f'Demotion (ran by {interaction.user.name})',
                        atomic=True
                    )
            except discord.HTTPException:
                # If add succeeded but remove failed, attempt to revert added roles
                if added and roles_to_add:
                    try:
                        await user.remove_roles(
                            *roles_to_add,
                            reason='Reverting failed demotion',
                            atomic=True
                        )
                    except discord.HTTPException:
                        pass  # Best-effort revert
                embed = discord.Embed(
                    title=':x: Demotion failed',
                    description=(
                        'A Discord error occurred while updating roles. '
                        'The demotion has been cancelled. Please try again.'
                    ),
                    color=0xe33232
                )
                await interaction.respond(embed=embed, ephemeral=True)
                return

            # Update the nickname if possible
            try:
                current = user.nick or user.name
                parts = current.split(' ', 1)
                base = parts[1] if len(parts) > 1 else parts[0]
                await user.edit(nick=f'{new_rank_key} {base}')
            except Exception:
                pass

            # Persist the new rank to the database
            try:
                db.cursor.execute(
                    "UPDATE discord_links SET rank = %s WHERE discord_id = %s",
                    (new_rank_key, user.id)
                )
                db.connection.commit()
            except Exception as db_err:
                # Roles changed but DB failed — log inconsistency for manual fix
                err_ch = self.client.get_channel(ERROR_CHANNEL_ID)
                if err_ch:
                    await err_ch.send(
                        f"## Role/DB Inconsistency — Demotion\n"
                        f"**User:** <@{user.id}> | **Expected rank:** `{new_rank_key}`\n"
                        f"Roles were updated in Discord but the database commit failed.\n"
                        f"```\n{str(db_err)[:500]}\n```"
                    )
                embed = discord.Embed(
                    title=':warning: Demotion partially failed',
                    description=(
                        'Roles were updated but the database change could not be saved. '
                        'An admin has been notified.'
                    ),
                    color=0xebdb34
                )
                await interaction.respond(embed=embed, ephemeral=True)
                return
        finally:
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
