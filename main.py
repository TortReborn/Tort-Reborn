import asyncio
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
from Helpers.storage import warm_background_cache
from Helpers.variables import IS_TEST_MODE, ERROR_CHANNEL_ID, PUBLIC_COMMANDS, ERROR_PING_USER_ID
from Helpers.logger import log, SYSTEM, SUCCESS, ERROR, INFO, WARN
from Helpers import logger
from Helpers import telemetry
from Commands.generate import ApplicationButtonView
from Helpers.views import ApplicationVoteView, ThreadVoteView, RecruitPaidView, RecruiterReviewView



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
        client.add_view(RecruitPaidView())
        client.add_view(RecruiterReviewView())
        # Commands are registered per guild, and Discord rejects the whole
        # sync with a 403 if any target guild is one the bot is not in — so a
        # single stale guild id silently costs every command everywhere. Drop
        # unreachable guilds first; a command left with no reachable guild is
        # skipped rather than falling through to a global registration.
        def _all_pending():
            # Before the first sync commands live in pending_application_commands.
            # walk_application_commands() reads the post-registration map, which
            # is still empty at this point and would filter nothing.
            for cmd in client.pending_application_commands:
                yield cmd
                walk = getattr(cmd, "walk_commands", None)
                if walk is not None:
                    yield from walk()

        reachable = {g.id for g in client.guilds}
        unreachable = set()
        for cmd in _all_pending():
            gids = getattr(cmd, "guild_ids", None)
            if not gids:
                continue
            missing = set(gids) - reachable
            if missing:
                unreachable |= missing
                cmd.guild_ids = [g for g in gids if g in reachable]
        if unreachable:
            log(WARN, "Skipping slash command sync for guilds the bot is not in: "
                      + ", ".join(str(g) for g in sorted(unreachable)))

        try:
            await client.sync_commands()
            client.synced = True
            log(SUCCESS, "Slash commands synced.")
        except Exception as e:
            # A sync failure (e.g. 403 when the bot lacks access to one guild)
            # must not kill the rest of on_ready — presence, the logger flush
            # loop, and the startup notification all live below this point.
            log(ERROR, f"Slash command sync failed; continuing startup: {e}")

    logger.start()

    if not getattr(client, 'bg_cache_warmed', False):
        client.bg_cache_warmed = True

        async def _warm_backgrounds():
            # Pre-fill the background memory cache so the first profile render
            # after a deploy skips the cold S3 read (~1.4s measured). Runs in a
            # worker thread, fire-and-forget: startup never waits on it.
            try:
                t = time.time()
                warmed = await asyncio.to_thread(warm_background_cache)
                log(SUCCESS, f"Warmed {warmed} profile backgrounds in {time.time() - t:.1f}s")
            except Exception as e:
                log(ERROR, f"Background cache warm failed (renders fall back to S3): {e}")

        asyncio.create_task(_warm_backgrounds())

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


@client.before_invoke
async def _telemetry_before(ctx: discord.ApplicationContext):
    """Start a timing accumulator for this invocation.

    queue_ms is the gap between Discord creating the interaction and the bot
    starting to run it — it separates "the bot was busy" from "the work was slow".

    discord.Interaction (py-cord 2.6) has no `created_at`, so derive the creation
    time from the interaction's snowflake id, which every interaction carries.
    """
    queue_ms = None
    try:
        created = discord.utils.snowflake_time(ctx.interaction.id)
        queue_ms = telemetry.queue_ms_from(created)
    except Exception:
        pass

    telemetry.begin(
        ctx.command.qualified_name if ctx.command else "unknown",
        guild_id=ctx.guild.id if ctx.guild else None,
        user_id=ctx.author.id if ctx.author else None,
        queue_ms=queue_ms,
    )


@client.after_invoke
async def _telemetry_after(ctx: discord.ApplicationContext):
    """Emit the invocation record.

    py-cord calls this from a `finally`, so it also runs for failed commands;
    sys.exc_info() is how we tell the two apart.
    """
    telemetry.finish(ok=sys.exc_info()[0] is None)


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
    ping = f'<@{ERROR_PING_USER_ID}>\n' if ERROR_PING_USER_ID else ''
    await ch.send(
        f'{ping}'
        f'## {ctx.author}{guild_info}, <#{ctx.channel_id}>:\n'
        f'```\n/{ctx.command.qualified_name}{options}\n```'
        f'## Traceback:\n'
        f'```\n{traceback_string}\n```'
    )

    try:
        if ctx.interaction.response.is_done():
            # Interaction was already deferred/responded — can't change ephemeral status.
            # Use followup which can independently be ephemeral.
            await ctx.followup.send(
                "⚠️ Something went wrong. This command might have recently been updated, please try again. If the issue persists, contact Thunderr.",
                ephemeral=True
            )
        else:
            await ctx.interaction.response.send_message(
                "⚠️ Something went wrong. This command might have recently been updated, please try again. If the issue persists, contact Thunderr.",
                ephemeral=True
            )
    except discord.HTTPException:
        pass  # Interaction expired or already responded


# =============================================================================
# Load Extensions
# =============================================================================
extensions = [
    # Commands
    'Commands.online',
    'Commands.activity',
    'Commands.profile',
    'Commands.priv_check',
    'Commands.progress',
    'Commands.worlds',
    'Commands.leaderboard',
    'Commands.background_admin',
    'Commands.background',
    'Commands.rankcheck',
    'Commands.new_member',
    'Commands.reset_roles',
    'Commands.raids',
    'Commands.graids',
    'Commands.manage',
    'Commands.toggle',
    'Commands.blacklist',
    'Commands.shell',
    'Commands.shell_exchange',
    'Commands.snipe',
    'Commands.generate',
    'Commands.lootpool',
    'Commands.aspects',
    'Commands.map',
    'Commands.graidevent',
    'Commands.graidlog',
    'Commands.treasury',
    'Commands.recruitment',
    'Commands.top_wars',
    'Commands.agenda',
    'Commands.wave_promote',
    'Commands.register',
    'Commands.app_commands',
    'Commands.kick_list',
    'Commands.cards',

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
    'Events.on_member_join',
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
    'Tasks.sync_discord_colors',
    'Tasks.check_website_apps',
    'Tasks.sync_vote_counts',
    'Tasks.kick_list_tracker',
    'Tasks.promotion_queue_processor',
    'Tasks.process_website_decisions',
    'Tasks.sync_war_builds',
    'Tasks.daily_musing',
    'Tasks.annihilation_parties',
    'Tasks.annihilation_announcements',
    'Tasks.loop_lag',
    'Tasks.presence_rollup',
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
