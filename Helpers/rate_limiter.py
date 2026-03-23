"""
Rate limiter for public commands used in external (non-home) servers.

Provides per-user and per-guild rate limiting that only activates
when commands are used outside of home guilds.
"""

import time
from discord.ext import commands
from Helpers.variables import is_home_guild


class RateLimitExceeded(commands.CheckFailure):
    """Raised when a rate limit is exceeded. Handled silently by the error handler."""
    pass


class RateLimiter:
    """Tracks per-user and per-guild command invocations within a sliding time window."""

    def __init__(self, per_user_limit: int, per_guild_limit: int, window_seconds: int):
        self.per_user_limit = per_user_limit
        self.per_guild_limit = per_guild_limit
        self.window_seconds = window_seconds
        self._user_calls: dict[int, list[float]] = {}
        self._guild_calls: dict[int, list[float]] = {}

    def _prune_bucket(self, bucket: list[float], cutoff: float) -> list[float]:
        """Remove expired timestamps from a single bucket."""
        return [ts for ts in bucket if ts > cutoff]

    def _full_cleanup(self, calls: dict[int, list[float]], cutoff: float) -> None:
        """Remove all expired entries across all keys to reclaim memory."""
        expired_keys = []
        for key, timestamps in calls.items():
            calls[key] = [ts for ts in timestamps if ts > cutoff]
            if not calls[key]:
                expired_keys.append(key)
        for key in expired_keys:
            del calls[key]

    def check(self, user_id: int, guild_id: int) -> tuple[bool, str]:
        """
        Check if a command invocation is allowed.

        Returns:
            (allowed, reason) — True if allowed, False with a reason if rate limited.
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds

        # Periodic full cleanup to prevent unbounded memory growth
        if len(self._user_calls) > 10000:
            self._full_cleanup(self._user_calls, cutoff)
        if len(self._guild_calls) > 10000:
            self._full_cleanup(self._guild_calls, cutoff)

        # Prune and check per-user limit
        user_timestamps = self._user_calls.get(user_id, [])
        user_timestamps = self._prune_bucket(user_timestamps, cutoff)
        if len(user_timestamps) >= self.per_user_limit:
            self._user_calls[user_id] = user_timestamps
            return False, 'Per-user rate limit exceeded.'

        # Prune and check per-guild limit
        guild_timestamps = self._guild_calls.get(guild_id, [])
        guild_timestamps = self._prune_bucket(guild_timestamps, cutoff)
        if len(guild_timestamps) >= self.per_guild_limit:
            self._guild_calls[guild_id] = guild_timestamps
            return False, 'Per-guild rate limit exceeded.'

        # Record the call
        user_timestamps.append(now)
        self._user_calls[user_id] = user_timestamps

        guild_timestamps.append(now)
        self._guild_calls[guild_id] = guild_timestamps

        return True, ''


# Module-level default instance
_default_limiter = RateLimiter(per_user_limit=5, per_guild_limit=30, window_seconds=60)


def external_rate_limit(per_user: int = 5, per_guild: int = 30, window: int = 60):
    """
    Decorator that rate-limits public commands in external (non-home) servers.

    Skips rate limiting entirely for home guilds and DMs.
    Sends an ephemeral response and raises RateLimitExceeded if the limit is hit.

    Args:
        per_user: Max invocations per user within the time window.
        per_guild: Max invocations per guild within the time window.
        window: Time window in seconds.
    """
    # Reuse the default limiter if params match defaults
    if per_user == 5 and per_guild == 30 and window == 60:
        limiter = _default_limiter
    else:
        limiter = RateLimiter(per_user, per_guild, window)

    async def predicate(ctx):
        # Skip rate limiting for home guilds and DMs
        if not ctx.guild or is_home_guild(ctx.guild.id):
            return True

        allowed, reason = limiter.check(ctx.author.id, ctx.guild.id)
        if not allowed:
            await ctx.respond(
                "You're being rate limited. Please try again in a moment.",
                ephemeral=True
            )
            raise RateLimitExceeded(reason)

        return True

    return commands.check(predicate)
