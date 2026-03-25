import asyncio

import discord
from discord import SlashCommandGroup, ApplicationContext
from discord.ext import commands

from Helpers.database import DB
from Helpers.functions import getPlayerUUID
from Helpers.variables import HOME_GUILD_IDS
from Tasks.kick_list_tracker import (
    _add_to_kick_list_sync,
    _remove_from_kick_list_sync,
    _fetch_kick_list_sync,
    build_kick_list_embed,
    refresh_kick_list_message,
)


class KickList(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client

    kicklist = SlashCommandGroup(
        "kicklist",
        "Manage the guild kick list",
        guild_ids=HOME_GUILD_IDS,
        default_member_permissions=discord.Permissions(manage_roles=True),
    )

    @kicklist.command(name="add", description="Add a player to the kick list")
    async def add(
        self,
        ctx: ApplicationContext,
        ign: discord.Option(str, "Player's in-game name"),
        tier: discord.Option(int, "Kick priority tier", choices=[1, 2, 3]),
    ):
        await ctx.interaction.response.defer(ephemeral=True)

        result = await asyncio.to_thread(getPlayerUUID, ign)
        if not result:
            await ctx.followup.send(f"Could not find player **{ign}**.", ephemeral=True)
            return

        username, uuid = result

        def _get_actor_ign(discord_id):
            db = DB(); db.connect()
            db.cursor.execute("SELECT ign FROM discord_links WHERE discord_id = %s", (discord_id,))
            row = db.cursor.fetchone()
            db.close()
            return row[0] if row else None

        added_by = await asyncio.to_thread(_get_actor_ign, ctx.author.id) or ctx.author.display_name

        await asyncio.to_thread(_add_to_kick_list_sync, uuid, username, tier, added_by)
        priority = {1: "High", 2: "Medium", 3: "Low"}[tier]
        await ctx.followup.send(
            f"Added **{username}** to the kick list ({priority} Priority).", ephemeral=True
        )

        await refresh_kick_list_message(self.client)

    @kicklist.command(name="remove", description="Remove a player from the kick list")
    async def remove(
        self,
        ctx: ApplicationContext,
        ign: discord.Option(str, "Player's in-game name"),
    ):
        await ctx.interaction.response.defer(ephemeral=True)

        removed = await asyncio.to_thread(_remove_from_kick_list_sync, ign)
        if not removed:
            await ctx.followup.send(
                f"**{ign}** was not found on the kick list.", ephemeral=True
            )
            return

        await ctx.followup.send(
            f"Removed **{ign}** from the kick list.", ephemeral=True
        )

        await refresh_kick_list_message(self.client)

    @kicklist.command(name="view", description="View the current kick list")
    async def view(self, ctx: ApplicationContext):
        await ctx.interaction.response.defer(ephemeral=True)

        rows = await asyncio.to_thread(_fetch_kick_list_sync)
        embed = build_kick_list_embed(rows)
        await ctx.followup.send(embed=embed, ephemeral=True)


def setup(client: commands.Bot):
    client.add_cog(KickList(client))
