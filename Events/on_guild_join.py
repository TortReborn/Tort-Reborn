import time

import discord
from discord.ext import commands

from Helpers.variables import ERROR_CHANNEL_ID, is_home_guild
from Helpers.logger import log, INFO


class OnGuildJoin(commands.Cog):
    def __init__(self, client):
        self.client = client
        self._recent_joins = {}   # guild_id -> timestamp, for rate limiting join notifications
        self._recent_removes = {} # guild_id -> timestamp, for rate limiting remove notifications

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        log(INFO, f'Joined new guild: {guild.name} (ID: {guild.id}, Members: {guild.member_count})', context='on_guild_join')

        # Don't notify for home guilds (reconnections)
        if is_home_guild(guild.id):
            return

        # Rate limit notifications (max 1 per guild per hour)
        now = time.monotonic()
        last_join = self._recent_joins.get(guild.id, 0)
        if now - last_join < 3600:
            return
        self._recent_joins[guild.id] = now

        # Clean up old entries to prevent memory leaks
        self._recent_joins = {
            gid: ts for gid, ts in self._recent_joins.items()
            if now - ts < 3600
        }

        # Send notification to error/log channel
        ch = self.client.get_channel(ERROR_CHANNEL_ID)
        if ch:
            embed = discord.Embed(
                title='\U0001f4e5 Joined New Server',
                description=(
                    f'**Server:** {guild.name}\n'
                    f'**ID:** `{guild.id}`\n'
                    f'**Members:** {guild.member_count}\n'
                    f'**Owner:** <@{guild.owner_id}>'
                ),
                colour=0x3474eb
            )
            await ch.send(embed=embed)
        else:
            log(INFO, f'Error channel {ERROR_CHANNEL_ID} not found. Could not send guild join notification for {guild.name} ({guild.id}).', context='on_guild_join')

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        """Log when bot is removed from a server."""
        log(INFO, f'Removed from guild: {guild.name} (ID: {guild.id})', context='on_guild_remove')

        if is_home_guild(guild.id):
            return

        # Rate limit notifications (max 1 per guild per hour)
        now = time.monotonic()
        last_event = self._recent_removes.get(guild.id, 0)
        if now - last_event < 3600:
            return
        self._recent_removes[guild.id] = now

        # Clean up old entries to prevent memory leaks
        self._recent_removes = {
            gid: ts for gid, ts in self._recent_removes.items()
            if now - ts < 3600
        }

        ch = self.client.get_channel(ERROR_CHANNEL_ID)
        if ch:
            embed = discord.Embed(
                title='\U0001f4e4 Removed From Server',
                description=(
                    f'**Server:** {guild.name}\n'
                    f'**ID:** `{guild.id}`\n'
                    f'**Owner:** <@{guild.owner_id}>'
                ),
                colour=0xe33232
            )
            await ch.send(embed=embed)
        else:
            log(INFO, f'Error channel {ERROR_CHANNEL_ID} not found. Could not send guild remove notification for {guild.name} ({guild.id}).', context='on_guild_remove')


def setup(client):
    client.add_cog(OnGuildJoin(client))
