import datetime
import sys
import time
import traceback
import logging

from dotenv import load_dotenv
import os

import discord
from discord import Embed

from Helpers.classes import Guild
from Helpers.database import get_last_online, set_last_online
from Helpers.variables import IS_TEST_MODE, ERROR_CHANNEL_ID, PUBLIC_COMMANDS
from Helpers.logger import log, SYSTEM, SUCCESS, ERROR, INFO
from Helpers import logger
from Commands.generate import ApplicationButtonView
from Helpers.views import ApplicationVoteView, ThreadVoteView



# Only show INFO+ from root, suppress discord.py's DEBUG/INFO noise
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s'
)
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)

# get bot token
load_dotenv()
if os.getenv("TEST_MODE", "").lower() == "true":
    log(SYSTEM, "Starting in TEST mode...")
    token = os.getenv("TEST_TOKEN")
elif os.getenv("TEST_MODE", "").lower() == "false":
    log(SYSTEM, "Starting in PRODUCTION mode...")
    token = os.getenv("TOKEN")
else:
    log(ERROR, "Could not get TOKEN. Please check your .env file.")
    sys.exit(-1)

# Discord intents
intents = discord.Intents.default()
intents.typing = True
intents.presences = True
intents.members = True
intents.message_content = True

client = discord.Bot(intents=intents)
logger.init(client)


def on_crash(exc_type, value, tb):
    crash = {
        "type": str(exc_type),
        "value": str(value),
        "tb": "".join(traceback.format_tb(tb)),
        "timestamp": int(time.time())
    }
    set_last_online(crash)


sys.excepthook = on_crash


@client.event
async def on_ready():
    if not getattr(client, 'synced', False):
        client.add_view(ApplicationButtonView())
        client.add_view(ApplicationVoteView())
        client.add_view(ThreadVoteView())
        await client.sync_commands()
        client.synced = True
        log(SUCCESS, "Slash commands synced.")

    logger.start()

    guild = Guild('The Aquarium')
    await client.change_presence(
        activity=discord.CustomActivity(name=f'{guild.online} members online')
    )
    log(SYSTEM, f'Logged in as {client.user}')
    for g in client.guilds:
        log(SYSTEM, f'Connected to guild: {g.name}')

    if not IS_TEST_MODE:
        now = int(time.time())
        crash_report = get_last_online()
        downtime = now - crash_report['timestamp']

        desc = f'🕙 **Downtime**\n`{datetime.timedelta(seconds=downtime)}`\n\n'
        if crash_report.get("type") not in ("Online",):
            desc += (
                f'ℹ️ **Shutdown reason**\n'
                f'```\n{crash_report["type"]}\n{crash_report["value"]}```'
            )
        else:
            desc += 'ℹ️ Graceful shutdown or connection loss'

        embed = Embed(
            title=f'🟢 {client.user} is back online!',
            description=desc,
            colour=0x1cd641
        )
        ch = client.get_channel(ERROR_CHANNEL_ID)
        if ch:
            await ch.send(embed=embed)
        else:
            log(ERROR, f'Error channel {ERROR_CHANNEL_ID} not found. Could not send startup notification.')


@client.event
async def on_connect():
    last_online = {
        "type": "Online",
        "value": "Bot was last connected",
        "tb": "",
        "timestamp": int(time.time())
    }
    set_last_online(last_online)


@client.event
async def on_application_command_error(
    ctx: discord.ApplicationContext,
    error: discord.DiscordException
):
    # Silently ignore rate limit errors (already responded with ephemeral message)
    from Helpers.rate_limiter import RateLimitExceeded
    if isinstance(error, RateLimitExceeded):
        return

    options = ''
    traceback_string = ''
    tb_list = traceback.format_exception(error)
    if ctx.selected_options:
        for opt in ctx.selected_options:
            options += f' {opt["name"]}:{opt["value"]}'
    traceback_string = ''.join(tb_list)[:1500]
    if len(traceback_string) >= 1500:
        traceback_string = "…(truncated)…\n" + traceback_string

    guild_info = f' in **{ctx.guild.name}**' if ctx.guild else ' in DMs'

    ch = client.get_channel(ERROR_CHANNEL_ID)
    if ch is None:
        log(ERROR, f'Error channel not found. Error in /{ctx.command.qualified_name}: {traceback_string}')
        raise error
    await ch.send(
        f'<@170719819715313665>\n'
        f'## {ctx.author}{guild_info}, <#{ctx.channel_id}>:\n'
        f'```\n/{ctx.command.qualified_name}{options}\n```'
        f'## Traceback:\n'
        f'```\n{traceback_string}\n```'
    )


# =============================================================================
# Load Extensions
# =============================================================================
extensions = [
    # Commands
    'Commands.online',
    'Commands.activity',
    'Commands.profile',
    'Commands.progress',
    'Commands.worlds',
    'Commands.leaderboard',
    'Commands.background_admin',
    'Commands.background',
    'Commands.rankcheck',
    'Commands.new_member',
    'Commands.reset_roles',
    'Commands.raids',
    'Commands.manage',
    'Commands.toggle',
    'Commands.blacklist',
    'Commands.shell',
    'Commands.shell_exchange',
    'Commands.generate',
    'Commands.lootpool',
    'Commands.aspects',
    'Commands.map',
    'Commands.graidevent',
    'Commands.treasury',
    'Commands.recruitment',
    'Commands.top_wars',
    'Commands.agenda',
    'Commands.wave_promote',
    'Commands.app_commands',
    'Commands.kick_list',

    # Dev Commands
    'Commands.render_text',
    'Commands.progress_bar',
    'Commands.rank_badge',
    'Commands.restart',

    # UserCommands
    'UserCommands.new_member',
    'UserCommands.rank_promote',
    'UserCommands.rank_demote',
    'UserCommands.reset_roles',

    # Events
    'Events.on_guild_channel_update',
    'Events.on_raw_reaction_add',
    'Events.on_member_update',
    'Events.on_guild_join',

    # Tasks
    'Tasks.update_member_data',
    'Tasks.check_apps',
    'Tasks.territory_tracker',
    'Tasks.vanity_roles',
    'Tasks.graid_event_stop',
    'Tasks.recruitment_checker',
    'Tasks.cache_guild_colors',
    'Tasks.check_website_apps',
    'Tasks.sync_vote_counts',
    'Tasks.kick_list_tracker',
    'Tasks.promotion_queue_processor',
    'Tasks.process_website_decisions',
]

for ext in extensions:
    try:
        client.load_extension(ext)
        log(SUCCESS, f"Loaded extension {ext}")
    except Exception:
        error_msg = f"Failed to load extension {ext}\n```\n{traceback.format_exc()}\n```"
        log(ERROR, error_msg)
        traceback.print_exc()


# =============================================================================
# Security Audit: Validate Global Command Registration
# =============================================================================
def audit_global_commands():
    """
    Validate that only explicitly allowlisted commands are globally registered.
    Logs warnings for any commands that are global but not in the allowlist.
    """
    warnings = []
    for cmd in client.pending_application_commands:
        # Get the command name (top-level name for groups)
        cmd_name = cmd.name if hasattr(cmd, 'name') else str(cmd)

        # Check if this command is globally registered (no guild_ids restriction)
        guild_ids = getattr(cmd, 'guild_ids', None)
        is_global = guild_ids is None or len(guild_ids) == 0

        if is_global:
            # Check if this is a known public command
            if cmd_name in PUBLIC_COMMANDS:
                continue
            # Unknown global command - potential security issue
            warnings.append(f"  - /{cmd_name} (global registration not in PUBLIC_COMMANDS allowlist)")

    if warnings:
        log(ERROR, f"SECURITY AUDIT: Found {len(warnings)} globally registered command(s) not in allowlist:")
        for w in warnings:
            log(ERROR, w)
    else:
        log(SUCCESS, f"Security audit passed: All {len(PUBLIC_COMMANDS)} public commands validated")


# Run audit after extensions are loaded
audit_global_commands()


# =============================================================================
# Run Client
# =============================================================================
if __name__ == '__main__':
    try:
        client.run(token)
    except Exception:
        log(ERROR, "Fatal error while running client")
        traceback.print_exc()
        sys.exit(1)
