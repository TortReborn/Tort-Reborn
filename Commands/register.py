import asyncio

import discord
from discord import ApplicationContext, Option, SlashCommandGroup
from discord.ext import commands

from Helpers.database import DB
from Helpers.functions import getPlayerUUID
from Helpers.links import LinkConflictError, assert_uuid_free
from Helpers.registration import set_rank, upsert_identity
from Helpers.variables import HOME_GUILD_IDS


MODERATOR_ROLE_NAME = '🛡️MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀'

ALLY_GUILDS = {
    'Nerfuria': {
        'role_id': 1414022229435482163,
        'rank': 'Navigator',
    },
}


class Register(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client

    register_group = SlashCommandGroup(
        'register',
        'HR: Register Discord users',
        guild_ids=HOME_GUILD_IDS,
        default_member_permissions=discord.Permissions(manage_roles=True),
    )

    @register_group.command(name='ally', description='HR: Register an allied guild member')
    async def ally(
        self,
        ctx: ApplicationContext,
        user: discord.Member,
        ign: str,
        guild: Option(str, 'Allied guild', choices=list(ALLY_GUILDS.keys())),
    ):
        await ctx.defer(ephemeral=True)

        if not self._is_moderator_or_higher(ctx.user):
            await ctx.followup.send(
                f'You need `{MODERATOR_ROLE_NAME}` or a higher role to use this command.',
                ephemeral=True,
            )
            return

        ally_config = ALLY_GUILDS[guild]
        ally_role = ctx.guild.get_role(ally_config['role_id'])
        if ally_role is None:
            await ctx.followup.send(
                f'Could not find the configured {guild} role (`{ally_config["role_id"]}`).',
                ephemeral=True,
            )
            return

        player_data = await asyncio.to_thread(getPlayerUUID, ign)
        if not player_data:
            await ctx.followup.send(
                f'Could not find a Minecraft account for `{ign}`.',
                ephemeral=True,
            )
            return

        canonical_ign, uuid = player_data
        rank = ally_config['rank']

        try:
            await asyncio.to_thread(self._check_link_conflict, user.id, uuid)
        except LinkConflictError as e:
            await ctx.followup.send(e.user_message(), ephemeral=True)
            return

        try:
            if ally_role not in user.roles:
                await user.add_roles(
                    ally_role,
                    reason=f'Ally registration for {guild} (ran by {ctx.user.name})',
                    atomic=True,
                )
            await user.edit(nick=f'{rank} {canonical_ign}')
        except discord.Forbidden:
            await ctx.followup.send(
                'I do not have permission to update that user or assign the ally role.',
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await ctx.followup.send(
                f'Discord rejected the member update: `{e}`',
                ephemeral=True,
            )
            return

        try:
            await asyncio.to_thread(self._upsert_ally_link, user.id, canonical_ign, uuid, rank)
        except LinkConflictError as e:
            await ctx.followup.send(e.user_message(), ephemeral=True)
            return

        await ctx.followup.send(
            f'Registered {user.mention} as **{rank} {discord.utils.escape_markdown(canonical_ign)}** for **{guild}**.',
            ephemeral=True,
        )

    @staticmethod
    def _check_link_conflict(discord_id: int, uuid: str):
        """Blocking DB read — call via asyncio.to_thread."""
        db = DB()
        db.connect()
        try:
            assert_uuid_free(db.cursor, uuid, discord_id)
        finally:
            db.close()

    @staticmethod
    def _upsert_ally_link(discord_id: int, ign: str, uuid: str, rank: str):
        """Identity plus the ally rank (a 'kind = ally' row in rank_definitions,
        so it is never mistaken for guild membership)."""
        db = DB()
        db.connect()
        try:
            upsert_identity(db.cursor, discord_id=discord_id, ign=ign, uuid=uuid)
            set_rank(db.cursor, discord_id, rank)
            db.connection.commit()
        finally:
            db.close()

    @staticmethod
    def _is_moderator_or_higher(member: discord.Member) -> bool:
        moderator_role = discord.utils.get(member.guild.roles, name=MODERATOR_ROLE_NAME)
        if moderator_role is None:
            return False
        return member.top_role >= moderator_role

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Register(client))
