import asyncio
import time
from io import BytesIO

import discord
from discord.commands import AutocompleteContext, SlashCommandGroup
from discord import Option
from discord.ext import commands, pages

from Helpers.classes import Page
from Helpers.database import DB
from Helpers.graid_cards import guild_pages, leaderboard_pages, log_pages
from Helpers.graid_stats import (
    LEADERBOARD_DEFAULT_LIMIT,
    RAID_ORDER,
    RANGE_CHOICES,
    UNKNOWN,
    guild_stats,
    leaderboard,
    log_entries,
    resolve_range,
)
from Helpers.logger import log, ERROR
from Helpers.pagination import add_paginator_buttons, respond_paginator
from Helpers.roster import is_member
from Helpers.variables import HOME_GUILD_IDS

SORT_CHOICES = ["Total", *RAID_ORDER]
NOT_A_MEMBER = ":no_entry: Guild raid stats are for TAq members."


async def _player_autocomplete(ctx: AutocompleteContext):
    prefix = (ctx.value or "").strip().lower()
    if len(prefix) < 2:
        return []
    return await asyncio.to_thread(_player_names, prefix)


def _player_names(prefix: str) -> list[str]:
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "SELECT DISTINCT ign FROM graid_log_participants"
            " WHERE LOWER(ign) LIKE %s ORDER BY ign LIMIT 25",
            (f"{prefix}%",),
        )
        return [row[0] for row in db.cursor.fetchall()]
    finally:
        db.close()


class GraidStats(commands.Cog):
    def __init__(self, client):
        self.client = client

    graidstats = SlashCommandGroup(
        "graidstats",
        "Stats for the guild raids we track",
        guild_ids=HOME_GUILD_IDS,
    )

    async def _members_only(self, ctx: discord.ApplicationContext) -> bool:
        allowed = await asyncio.to_thread(self._is_member, ctx.author.id)
        if not allowed:
            await ctx.followup.send(NOT_A_MEMBER, ephemeral=True)
        return allowed

    @staticmethod
    def _is_member(discord_id: int) -> bool:
        db = DB()
        db.connect()
        try:
            return is_member(db.cursor, discord_id=discord_id)
        finally:
            db.close()

    async def _send(self, ctx: discord.ApplicationContext, cards: list, prefix: str, empty: str):
        if not cards:
            await ctx.followup.send(empty, ephemeral=True)
            return

        stamp = int(time.time())
        files = []
        for index, card in enumerate(cards):
            buf = BytesIO()
            card.save(buf, format="PNG")
            buf.seek(0)
            files.append(discord.File(buf, filename=f"{prefix}_{stamp}_{index}.png"))

        if len(files) == 1:
            await ctx.followup.send(file=files[0])
            return

        paginator = pages.Paginator(pages=[Page(content="", files=[f]) for f in files])
        add_paginator_buttons(paginator)
        await respond_paginator(paginator, ctx.interaction)

    @graidstats.command(name="top", description="Guild raid leaderboard")
    async def top(
        self,
        ctx: discord.ApplicationContext,
        sort: Option(str, "Sort by total or one raid", choices=SORT_CHOICES, required=False, default="Total"),
        period: Option(str, "Time range", choices=RANGE_CHOICES, required=False, default="All-Time"),
        full: Option(bool, "Show every player instead of the top 60", required=False, default=False),
    ):
        await ctx.defer()
        if not await self._members_only(ctx):
            return
        try:
            cards, label = await asyncio.to_thread(self._leaderboard_cards, sort, period, full)
        except Exception as e:
            log(ERROR, f"/graidstats top failed: {e}", context="graidstats")
            await ctx.followup.send("Could not build the leaderboard.", ephemeral=True)
            return
        await self._send(ctx, cards, "graid_top", f"No tracked raids for {label}.")

    def _leaderboard_cards(self, sort: str, range_choice: str, full: bool):
        db = DB()
        db.connect()
        try:
            raid_range = resolve_range(db.cursor, range_choice)
            rows = leaderboard(
                db.cursor,
                raid_range,
                sort=None if sort == "Total" else sort,
                limit=None if full else LEADERBOARD_DEFAULT_LIMIT,
            )
        finally:
            db.close()
        return leaderboard_pages(rows, raid_range.label, sort), raid_range.label

    @graidstats.command(name="guild", description="Guild-wide raid totals and activity")
    async def guild(
        self,
        ctx: discord.ApplicationContext,
        period: Option(str, "Time range", choices=RANGE_CHOICES, required=False, default="All-Time"),
    ):
        await ctx.defer()
        if not await self._members_only(ctx):
            return
        try:
            cards, label = await asyncio.to_thread(self._guild_cards, period)
        except Exception as e:
            log(ERROR, f"/graidstats guild failed: {e}", context="graidstats")
            await ctx.followup.send("Could not build the guild overview.", ephemeral=True)
            return
        await self._send(ctx, cards, "graid_guild", f"No tracked raids for {label}.")

    def _guild_cards(self, range_choice: str):
        db = DB()
        db.connect()
        try:
            raid_range = resolve_range(db.cursor, range_choice)
            stats = guild_stats(db.cursor, raid_range)
        finally:
            db.close()
        if stats.total_raids == 0:
            return [], raid_range.label
        return guild_pages(stats), raid_range.label

    @graidstats.command(name="browse", description="Browse the guild raid log")
    async def browse(
        self,
        ctx: discord.ApplicationContext,
        player: Option(str, "Only raids with this player", autocomplete=_player_autocomplete, required=False, default=None),
        raid: Option(str, "Only this raid", choices=[*RAID_ORDER, UNKNOWN], required=False, default=None),
        period: Option(str, "Time range", choices=RANGE_CHOICES, required=False, default="All-Time"),
    ):
        await ctx.defer()
        if not await self._members_only(ctx):
            return
        try:
            cards = await asyncio.to_thread(self._log_cards, player, raid, period)
        except Exception as e:
            log(ERROR, f"/graidstats log failed: {e}", context="graidstats")
            await ctx.followup.send("Could not read the raid log.", ephemeral=True)
            return
        await self._send(ctx, cards, "graid_log", "No raids matched those filters.")

    def _log_cards(self, player: str | None, raid: str | None, range_choice: str):
        db = DB()
        db.connect()
        try:
            raid_range = resolve_range(db.cursor, range_choice)
            entries = log_entries(db.cursor, raid_range, ign=player, raid_type=raid)
        finally:
            db.close()

        label = " · ".join([p for p in (player, raid) if p] + [raid_range.label])
        return log_pages(entries, raid_range.label, label)


def setup(client):
    client.add_cog(GraidStats(client))
