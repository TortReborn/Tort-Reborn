import asyncio
from typing import Dict, List, Tuple

import discord
from discord.ext import commands
from discord.commands import slash_command, Option
from PIL import Image

from Helpers.database import DB
from Helpers.graid_cards import GraidPlayerCard
from Helpers.graid_stats import RaidRange, has_tracked_raids, player_raid_stats
from Helpers.logger import log, ERROR
from Helpers.raid_card import RaidCardBase
from Helpers.rate_limiter import external_rate_limit
from Helpers.roster import is_member


class GuildRaids(RaidCardBase, commands.Cog):
    TITLE = "Guild Raids"
    FILE_PREFIX = "graids"
    STAT_KEY = "guildRaids"
    RANK_QUALIFIERS: Tuple[str, ...] = ("srgplayers",)

    RAIDS = [
        ("NOTG", "Nest of the Grootslangs", ("Nest of the Grootslangs",), ("grootslangSrGPlayers",), ("grootslang",)),
        ("NOL",  "Orphion's Nexus of Light", ("Orphion's Nexus of Light",), ("orphionSrGPlayers",), ("orphion",)),
        ("TCC",  "The Canyon Colossus", ("The Canyon Colossus",), ("colossusSrGPlayers",), ("colossus",)),
        ("TNA",  "The Nameless Anomaly", ("The Nameless Anomaly",), ("namelessSrGPlayers",), ("nameless", "anomaly")),
        # WTP is internally called "Fruma" by the API -- frumaSrGPlayers tracks WTP guild raid rank
        ("WTP",  "The Queen's Wartorn Palace", ("The Wartorn Palace", "Wartorn Palace"), ("frumaSrGPlayers",), ("fruma",)),
    ]

    @slash_command(
        name="graids",
        description="Show all-time guild raid rankings and counts for a player",
        integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install},
        contexts={discord.InteractionContextType.guild, discord.InteractionContextType.bot_dm, discord.InteractionContextType.private_channel},
    )
    @external_rate_limit()
    async def graids(self,
                     ctx: discord.ApplicationContext,
                     name: Option(str, "Minecraft username", required=True)):
        await self._run(ctx, name)

    async def _extra_cards(self, ctx: discord.ApplicationContext, player: Dict,
                           player_stats) -> List[Image.Image]:
        """The raids we tracked, for a caller on the roster. Everyone else gets
        page 1 alone, with no hint that more exists."""
        uuid = player.get("uuid")
        if not uuid:
            return []
        try:
            return await asyncio.to_thread(self._tracked_cards, ctx.author.id, uuid, player, player_stats)
        except Exception as e:
            log(ERROR, f"Tracked graid pages failed for {player.get('username')}: {e}", context="graids")
            return []

    def _tracked_cards(self, author_id: int, uuid: str, player: Dict,
                       player_stats) -> List[Image.Image]:
        db = DB()
        db.connect()
        try:
            cursor = db.cursor
            if not is_member(cursor, discord_id=author_id):
                return []
            if not has_tracked_raids(cursor, uuid):
                return []
            raid_range = RaidRange(label="All-Time")
            stats = player_raid_stats(cursor, uuid, player.get("username") or "", raid_range)
        finally:
            db.close()

        if stats is None:
            return []

        card = GraidPlayerCard(
            player,
            player_stats,
            player_stats.tag_color,
            *player_stats.gradient,
            player_stats.background,
        )
        return card.render(stats, raid_range.label)


def setup(client: commands.Bot):
    client.add_cog(GuildRaids(client))
