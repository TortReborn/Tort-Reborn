import discord
from discord.ext import commands
from discord.commands import slash_command
from discord import default_permissions

from Helpers.classes import BasicPlayerStats
from Helpers.database import DB
from Helpers.member_roles import removal_role_names, resolve_roles
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
        # Discord resolves a user id that has left the server to a bare User,
        # which has no roles to reset.
        if not hasattr(user, 'roles'):
            await message.respond(f'<@{user.id}> is not in this server.', ephemeral=True)
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

            # Check target's rank and recorded honorific status
            db.cursor.execute(
                'SELECT rank, was_honored_fish, was_retired_chief FROM discord_links WHERE discord_id = %s',
                (user.id,)
            )
            target_row = db.cursor.fetchone()
            was_honored_fish = was_retired_chief = False
            if target_row:
                target_rank, was_honored_fish, was_retired_chief = target_row
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
            to_add, to_remove = removal_role_names(was_honored_fish, was_retired_chief)
            roles_to_add = resolve_roles(all_roles, to_add, member=user, present=False)
            roles_to_remove = resolve_roles(all_roles, to_remove, member=user, present=True)

            if roles_to_add:
                await user.add_roles(*roles_to_add, reason=f'Roles reset (ran by {message.author.name})')
            if roles_to_remove:
                await user.remove_roles(*roles_to_remove, reason=f'Roles reset (ran by {message.author.name})')
            await user.edit(nick='')
        finally:
            db.close()

        description = f'Roles were reset for <@{user.id}>'
        restored = [r.name for r in roles_to_add if r.name != 'Ex-Member']
        if restored:
            description += '\nRestored: ' + ', '.join(f'`{r}`' for r in restored)
        embed = discord.Embed(title=':white_check_mark: Roles reset',
                              description=description, color=0x3ed63e)
        await message.respond(embed=embed)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(ResetRolesCommand(client))
