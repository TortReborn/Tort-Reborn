import asyncio

import discord
from discord import ApplicationContext, Option, SlashCommandGroup
from discord.ext import commands

from Helpers.alliances import enabled_alliances
from Helpers.ally_registration import AllyRegistrationError, register_ally
from Helpers.links import LinkConflictError
from Helpers.variables import HOME_GUILD_IDS


MODERATOR_ROLE_NAME = '🛡️MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀'


async def _alliance_autocomplete(ctx: discord.AutocompleteContext):
    alliances = await asyncio.to_thread(enabled_alliances)
    query = (ctx.value or '').strip().casefold()
    return [
        discord.OptionChoice(name=f'{ally.name} [{ally.prefix}]'[:100], value=str(ally.id))
        for ally in alliances
        if query in ally.name.casefold() or query in ally.prefix.casefold()
    ][:25]


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
        guild: Option(str, 'Allied guild', autocomplete=_alliance_autocomplete),
    ):
        await ctx.defer(ephemeral=True)

        if not self._is_moderator_or_higher(ctx.user):
            await ctx.followup.send(
                f'You need `{MODERATOR_ROLE_NAME}` or a higher role to use this command.',
                ephemeral=True,
            )
            return

        try:
            registered = await register_ally(user, ign, guild, actor_name=ctx.user.name)
        except LinkConflictError as error:
            await ctx.followup.send(error.user_message(), ephemeral=True)
            return
        except AllyRegistrationError as error:
            await ctx.followup.send(str(error), ephemeral=True)
            return
        except discord.Forbidden:
            await ctx.followup.send('I could not update the nickname or guild role.', ephemeral=True)
            return
        except discord.HTTPException:
            await ctx.followup.send('Discord rejected the member update.', ephemeral=True)
            return

        await ctx.followup.send(
            f'Registered {user.mention} as **{registered.alliance.rank} '
            f'{discord.utils.escape_markdown(registered.ign)}** for **{registered.alliance.name}**.',
            ephemeral=True,
        )

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
