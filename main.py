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
from Helpers.variables import IS_TEST_MODE, ERROR_CHANNEL_ID
from Helpers.logger import log, SYSTEM, SUCCESS, ERROR
from Helpers import logger
from Commands.generate import ApplicationButtonView



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
        await ch.send(embed=embed)


@client.event
async def on_connect():
    last_online = {
        "type": "Online",
        "value": "Bot was last connected",
        "tb": "",
        "timestamp": int(time.time())
    }
    set_last_online(last_online)


if not IS_TEST_MODE or IS_TEST_MODE:
    @client.event
    async def on_application_command_error(
        ctx: discord.ApplicationContext,
        error: discord.DiscordException
    ):
        options = ''
        traceback_string = ''
        tb_list = traceback.format_exception(error)
        if ctx.selected_options:
            for opt in ctx.selected_options:
                options += f' {opt["name"]}:{opt["value"]}'
        traceback_string = ''.join(tb_list)[:1500]
        if len(traceback_string) >= 1500:
            traceback_string = "…(truncated)…\n" + traceback_string

        ch = client.get_channel(ERROR_CHANNEL_ID)
        await ch.send(
            f'<@170719819715313665>\n'
            f'## {ctx.author} in <#{ctx.channel_id}>:\n'
            f'```\n/{ctx.command.qualified_name}{options}\n```'
            f'## Traceback:\n'
            f'```\n{traceback_string}\n```'
        )
        raise error


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
    # 'Commands.bank_admin',
    'Commands.new_member',
    'Commands.reset_roles',
    'Commands.raids',
    #'Commands.rank',
    'Commands.manage',
    'Commands.application',
    'Commands.toggle',
    # 'Commands.blacklist',
    'Commands.shell',
    'Commands.shell_exchange',
    # 'Commands.contribution',
    # 'Commands.recruit',
    # 'Commands.build',
    # 'Commands.withdraw',
    # 'Commands.update_claim',
    # 'Commands.suggest_promotion',
    # 'Commands.ranking_up_setup',
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

    # Dev Commands
    'Commands.render_text',
    # 'Commands.check_app',
    # 'Commands.custom_profile',
    'Commands.progress_bar',
    'Commands.rank_badge',
    'Commands.restart',

    # UserCommands
    'UserCommands.new_member',
    'UserCommands.rank_promote',
    'UserCommands.rank_demote',
    'UserCommands.reset_roles',

    # Events
    'Events.on_message',
    'Events.on_message_edit',
    'Events.on_guild_channel_create',
    'Events.on_guild_channel_update',
    'Events.on_raw_reaction_add',
    'Events.on_member_update',

    # Tasks
    'Tasks.guild_log',
    'Tasks.update_member_data',
    'Tasks.check_apps',
    'Tasks.territory_tracker',
    'Tasks.vanity_roles',
    'Tasks.graid_event_stop',
    'Tasks.recruitment_checker',
    'Tasks.cache_guild_colors',
    'Tasks.check_website_apps',
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
# Run Client
# =============================================================================
if __name__ == '__main__':
    try:
        client.run(token)
    except Exception:
        log(ERROR, "Fatal error while running client")
        traceback.print_exc()
        sys.exit(1)
