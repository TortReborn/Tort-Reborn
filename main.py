import datetime
import json
import sys
import time
import traceback

from dotenv import load_dotenv
import os

import discord
from discord import Embed

from Helpers.classes import Guild
from Helpers.variables import test

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
    exit(-1)

# Discord intents
intents = discord.Intents.default()
intents.typing = True
intents.presences = True
intents.members = True
intents.message_content = True

client = discord.Bot(intents=intents)


def on_crash(types, value, tb):
    crash = {"type": str(types), "value": str(value), "tb": str(tb), "timestamp": int(time.time())}
    with open('last_online.json', 'w') as f:
        json.dump(crash, f)


sys.excepthook = on_crash


@client.event
async def on_ready():
    guild = Guild('The Aquarium')
    await client.change_presence(activity=discord.CustomActivity(name=f'{guild.online} members online'))
    print('We have logged in as {0.user}'.format(client))
    print('\n'.join(guild.name for guild in client.guilds))

    if not test:
        now = int(time.time())
        crash_report = json.load(open('last_online.json', 'r'))

        downtime = now - crash_report['timestamp']

        embed = Embed(title=f'🟢 {client.user} is back online!', description=f'🕙 **Downtime**\n'
                                                                            f'`{datetime.timedelta(seconds=downtime)}`\n'
                                                                            f'\n'
                                                                            f'ℹ️ **Shutdown reason**\n'
                                                                            f'```\n{crash_report["type"]}\n{crash_report["value"]}```',
                      colour=0x1cd641)

        # ch = client.get_channel(1053736331404120114) CHANGE TO UNKNOWN
        ch = client.get_channel(1367285315236008036)
        await ch.send(embed=embed)


@client.event
async def on_disconnect():
    crash = {"type": "Disconnected", "value": "Bot disconnected from Discord", "tb": "Bot disconnected from Discord",
             "timestamp": int(time.time())}
    with open('last_online.json', 'w') as f:
        json.dump(crash, f)


if not test or test:
    @client.event
    async def on_application_command_error(ctx: discord.ApplicationContext, error: discord.DiscordException):
        options = ''
        traceback_string = ''
        tb = traceback.format_exception(error)
        if ctx.selected_options:
            for option in ctx.selected_options:
                options += f' {option["name"]}:{option["value"]}'
        if tb:
            for message in tb:
                traceback_string += f'{message}'

        ch = client.get_channel(1367285315236008036)
        if len(traceback_string) > 1500:
            traceback_string = "…(truncated)…\n" + traceback_string[:1500]
        await ch.send(f'## {ctx.author} in <#{ctx.channel_id}>:\n```\n/{ctx.command.qualified_name}{options}\n```\n## Traceback:\n```\n{traceback_string}\n```')
        raise error

# Load Commands
client.load_extension('Commands.online')
# client.load_extension('Commands.activity')
client.load_extension('Commands.profile')  # Profile needs work but has shell count
client.load_extension('Commands.progress')
client.load_extension('Commands.worlds')
client.load_extension('Commands.leaderboard')  # Leaderboard needs work too, functional tho
client.load_extension('Commands.background_admin')
client.load_extension('Commands.background')
client.load_extension('Commands.rankcheck')
#client.load_extension('Commands.bank_admin')
client.load_extension('Commands.new_member')
#client.load_extension('Commands.reset_roles')  # Kind of works but needs edits
client.load_extension('Commands.manage')
#client.load_extension('Commands.blacklist')
client.load_extension('Commands.shell')
#client.load_extension('Commands.contribution')
#client.load_extension('Commands.recruit')
#client.load_extension('Commands.build')
#client.load_extension('Commands.withdraw')
#client.load_extension('Commands.update_claim')
#client.load_extension('Commands.welcome_admin')
#client.load_extension('Commands.suggest_promotion')
#client.load_extension('Commands.ranking_up_setup')
client.load_extension('Commands.raid_collecting')
client.load_extension('Commands.lootpool')
client.load_extension('Commands.aspects')

# Load Dev Commands
client.load_extension('Commands.render_text')
client.load_extension('Commands.send_changelog')
client.load_extension('Commands.preview_changelog')
#client.load_extension('Commands.check_app')
#client.load_extension('Commands.custom_profile')
client.load_extension('Commands.progress_bar')
client.load_extension('Commands.rank_badge')
client.load_extension('Commands.restart')

# Load user commands
client.load_extension('UserCommands.new_member')
client.load_extension('UserCommands.rank_promote')
client.load_extension('UserCommands.rank_demote')
#client.load_extension('UserCommands.reset_roles')

# Load message command
# Think we can remove, redundant
# client.load_extension('MessageCommands.notify')

# Load events
client.load_extension('Events.on_message')
client.load_extension('Events.on_guild_channel_create')
client.load_extension('Events.on_guild_channel_update')
client.load_extension('Events.on_raw_reaction_add')

# Load tasks
client.load_extension('Tasks.guild_log')
client.load_extension('Tasks.update_member_data')
client.load_extension('Tasks.check_apps')
client.load_extension('Tasks.territory_tracker')

client.run(token)
