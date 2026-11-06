"""What a bot message is allowed to ping.

The client default lets a message ping only users it names. User-controlled
text reaches many sends, so a role or @everyone ping has to be asked for at the
send that means it.
"""

import discord

DEFAULT_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)
NO_MENTIONS = discord.AllowedMentions.none()
EVERYONE_PING = discord.AllowedMentions(everyone=True, roles=False, users=False)


def role_ping(role_id):
    """Allow a ping of this one role and nothing else."""
    return discord.AllowedMentions(
        everyone=False, users=False, roles=[discord.Object(int(role_id))],
    )


def user_ping(user_id):
    """Allow a ping of this one user and nothing else."""
    return discord.AllowedMentions(
        everyone=False, roles=False, users=[discord.Object(int(user_id))],
    )


def error_report_mentions(ping_user_id):
    """An error report pings the configured owner, never anything in its text."""
    return user_ping(ping_user_id) if ping_user_id else NO_MENTIONS


def fence_safe(value):
    """Text that can't close the code block it is shown in."""
    return str(value).replace('`', "'")
