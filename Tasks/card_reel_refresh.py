"""Announce the six-hour reel refresh in each guild's card channel.

Reels refresh on a fixed clock rather than per player, so the boundary is a
shared moment worth marking. The loop fires on the same 00:00 / 06:00 / 12:00
/ 18:00 UTC boundaries the reel window is derived from (epoch // 21600), so
the post lands exactly when balances actually top up.

Guilds that have not set a card channel are skipped — configuring one with
/tank set-channel is what opts a server in.

The post mentions the reel ping role, which people give themselves with
/tank ping. Nothing else is ever mentioned: the allowed-mentions list is that
one role, so a stray @ in the embed cannot reach anyone.
"""

import asyncio
from datetime import time as dtime
from datetime import timezone

import discord
from discord.ext import commands, tasks

from Helpers import cards as cardlib
from Helpers.logger import ERROR, INFO, WARN, log
from Helpers.variables import CARD_PING_ROLE_ID, TAQ_GUILD_IDS

REFRESH_TIMES = [
    dtime(hour=h, minute=0, tzinfo=timezone.utc)
    for h in range(0, 24, cardlib.WINDOW_SECONDS // 3600)
]


class CardReelRefresh(commands.Cog):
    """Posts a marker when reels top up, pinging only those who asked."""

    def __init__(self, client):
        self.client = client
        # guild id -> last window announced, so a restart landing on the
        # boundary cannot post the same refresh twice.
        self._announced = {}
        self.announce.start()

    def cog_unload(self):
        if self.announce.is_running():
            self.announce.cancel()

    @tasks.loop(time=REFRESH_TIMES)
    async def announce(self):
        if not self.client.is_ready():
            return

        window = cardlib.current_window()
        nxt = cardlib.next_refresh_ts()

        for guild_id in TAQ_GUILD_IDS:
            if self._announced.get(guild_id) == window:
                continue
            try:
                channel_id = await asyncio.to_thread(
                    cardlib.db_get_card_channel, guild_id)
                if not channel_id:
                    continue

                channel = self.client.get_channel(channel_id)
                if channel is None:
                    log(WARN, f"Card channel {channel_id} not found for guild "
                              f"{guild_id}", context="card_refresh")
                    continue

                embed = discord.Embed(
                    title="Reels refreshed",
                    description=(
                        f"Everyone is topped up by **{cardlib.REELS_PER_WINDOW}** "
                        f"reels. Next refresh <t:{nxt}:R>."),
                    color=0x38C9BD)
                embed.set_footer(text="/reel to cast")

                # The mention lives in the message content, not the embed:
                # Discord never pings from inside an embed.
                if CARD_PING_ROLE_ID:
                    content = f"<@&{CARD_PING_ROLE_ID}>"
                    allowed = discord.AllowedMentions(
                        roles=[discord.Object(CARD_PING_ROLE_ID)],
                        users=False, everyone=False)
                else:
                    content, allowed = None, discord.AllowedMentions.none()
                await channel.send(content=content, embed=embed,
                                   allowed_mentions=allowed)
                self._announced[guild_id] = window
                log(INFO, f"Announced reel refresh in {channel_id}",
                    context="card_refresh")
            except discord.Forbidden:
                log(WARN, f"No permission to post the reel refresh in guild "
                          f"{guild_id}", context="card_refresh")
            except Exception as e:
                log(ERROR, f"Reel refresh announce failed for guild {guild_id}: "
                           f"{e!r}", context="card_refresh")

    @announce.before_loop
    async def _wait_until_ready(self):
        await self.client.wait_until_ready()


def setup(client):
    client.add_cog(CardReelRefresh(client))
