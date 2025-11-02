import datetime
import json
import sys
import time
import traceback
import logging

from dotenv import load_dotenv
import os

import discord
from discord import Embed

from Helpers.classes import Guild
from Helpers.variables import test



# Only show INFO+ from root, suppress discord.py’s DEBUG/INFO noise
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s'
)
logging.getLogger('discord').setLevel(logging.WARNING)

# get bot token
load_dotenv()
if os.getenv("TEST_MODE", "").lower() == "true":
    print("Starting in TEST mode...")
    token = os.getenv("TEST_TOKEN")
elif os.getenv("TEST_MODE", "").lower() == "false":
    print("Starting in PRODUCTION mode...")
    token = os.getenv("TOKEN")
else:
    print("Error: Could not get TOKEN. Please check your .env file.")
    sys.exit(-1)

# Discord intents
intents = discord.Intents.default()
intents.typing = True
intents.presences = True
intents.members = True
intents.message_content = True

client = discord.Bot(intents=intents)


def on_crash(exc_type, value, tb):
    crash = {
        "type": str(exc_type),
        "value": str(value),
        "tb": "".join(traceback.format_tb(tb)),
        "timestamp": int(time.time())
    }
    with open('last_online.json', 'w') as f:
        json.dump(crash, f)


sys.excepthook = on_crash


@client.event
async def on_ready():
    if not getattr(client, 'synced', False):
        await client.sync_commands()
        client.synced = True
        print("✅ Slash commands synced.")

    guild = Guild('The Aquarium')
    await client.change_presence(
        activity=discord.CustomActivity(name=f'{guild.online} members online')
    )
    print(f'🟪 We have logged in as {client.user}')
    for g in client.guilds:
        print(f'🟪 Connected to guild: {g.name}')

    if not test:
        now = int(time.time())
        crash_report = json.load(open('last_online.json', 'r'))
        downtime = now - crash_report['timestamp']

        embed = Embed(
            title=f'🟢 {client.user} is back online!',
            description=(
                f'🕙 **Downtime**\n'
                f'`{datetime.timedelta(seconds=downtime)}`\n\n'
                f'ℹ️ **Shutdown reason**\n'
                f'```\n{crash_report["type"]}\n{crash_report["value"]}```'
            ),
            colour=0x1cd641
        )
        ch = client.get_channel(1367285315236008036)
        await ch.send(embed=embed)


@client.event
async def on_disconnect():
    crash = {
        "type": "Disconnected",
        "value": "Bot disconnected from Discord",
        "tb": "Bot disconnected from Discord",
        "timestamp": int(time.time())
    }
    with open('last_online.json', 'w') as f:
        json.dump(crash, f)


if not test or test:
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

        ch = client.get_channel(1367285315236008036)
        await ch.send(
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
    # 'Commands.blacklist',
    'Commands.shell',
    # 'Commands.contribution',
    # 'Commands.recruit',
    # 'Commands.build',
    # 'Commands.withdraw',
    # 'Commands.update_claim',
    # 'Commands.welcome_admin',
    # 'Commands.suggest_promotion',
    # 'Commands.ranking_up_setup',
    'Commands.raid_collecting',
    'Commands.lootpool',
    'Commands.aspects',
    'Commands.map',
    'Commands.graidevent',
    'Commands.treasury',

    # Dev Commands
    'Commands.render_text',
    'Commands.send_changelog',
    'Commands.preview_changelog',
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
    'Events.on_guild_channel_create',
    'Events.on_guild_channel_update',
    'Events.on_raw_reaction_add',

    # Tasks
    'Tasks.guild_log',
    'Tasks.update_member_data',
    'Tasks.check_apps',
    'Tasks.territory_tracker',
    'Tasks.vanity_roles',
    'Tasks.graid_event_stop',
    'Tasks.cache_player_activity',
]

for ext in extensions:
    try:
        client.load_extension(ext)
        print(f"✅ Loaded extension {ext}")
    except Exception:
        print(f"❌ Failed to load extension {ext}", file=sys.stderr)
        traceback.print_exc()


# =============================================================================
# Run Client
# =============================================================================
if __name__ == '__main__':
    try:
        client.run(token)
    except Exception:
        print("Fatal error while running client:", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
