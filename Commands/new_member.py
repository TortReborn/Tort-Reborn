import asyncio

import discord
from discord.ext import commands
from discord.commands import slash_command
from discord import default_permissions

from Helpers.classes import LinkAccount, PlayerStats, BasicPlayerStats
from Helpers.database import DB
from Helpers.functions import getPlayerUUID, determine_starting_rank
from Helpers.links import LinkConflictError, assert_uuid_free
from Helpers.member_roles import registration_role_names
from Helpers.registration import record_registration
from Helpers.variables import HOME_GUILD_IDS


def _fetch_new_member_data(user_id, ign):
    """Fetch the player's stats and check the uuid is free to link.
    Blocking (HTTP + DB read) — always call via asyncio.to_thread. The DB is
    checked out only after the BasicPlayerStats HTTP has completed."""
    pdata = BasicPlayerStats(ign)

    if not pdata.error:
        db = DB(); db.connect()
        try:
            assert_uuid_free(db.cursor, pdata.UUID, user_id)
        finally:
            db.close()

    return pdata


def _record(user_id, ign, uuid, rank, wars, held_role_names, actor_id):
    """Blocking DB write — call via asyncio.to_thread."""
    db = DB(); db.connect()
    try:
        recorded = record_registration(
            db.cursor, discord_id=user_id, ign=ign, uuid=uuid, rank=rank, wars_on_join=wars,
            held_role_names=held_role_names, actor_id=actor_id,
        )
        db.connection.commit()
        return recorded
    finally:
        db.close()


class NewMember(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(guild_ids=HOME_GUILD_IDS, description="HR: Register a new guild member")
    @default_permissions(manage_roles=True)
    async def new_member(self, message, user: discord.Member, ign):
        if message.interaction.user.guild_permissions.manage_roles:
            await message.defer(ephemeral=True)
            try:
                pdata = await asyncio.to_thread(_fetch_new_member_data, user.id, ign)
            except LinkConflictError as e:
                await message.respond(e.user_message(), ephemeral=True)
                return
            if pdata.error:
                embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                      description=f'Could not retrieve information of `{ign}`.\nPlease check your spelling or try again later.',
                                      color=0xe33232)
                await message.respond(embed=embed, ephemeral=True)
                return

            starting_rank = determine_starting_rank(user)
            held_role_names = [r.name for r in user.roles]
            to_add, to_remove = registration_role_names(starting_rank)
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

            try:
                await asyncio.to_thread(
                    _record, user.id, pdata.username, pdata.UUID, starting_rank, pdata.wars,
                    held_role_names, message.interaction.user.id,
                )
            except LinkConflictError as e:
                await message.respond(e.user_message(), ephemeral=True)
                return
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
