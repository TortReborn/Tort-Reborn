from collections import Counter
import datetime
import discord
import requests
import json
import time

from discord.ext import tasks, commands

from Helpers.variables import spearhead_role_id, territory_tracker_channel, military_channel


def getTerritoryData():
    url = 'https://api.wynncraft.com/v3/guild/list/territory'
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return False
    except ValueError:
        return False


def saveTerritoryData(data):
    with open('territories.json', 'w') as f:
        json.dump(data, f, indent=4)
        f.close()


def timeHeld(date_time_old, date_time_new):
    t_old = datetime.datetime.fromisoformat(date_time_old[0:len(date_time_old) - 1])
    t_new = datetime.datetime.fromisoformat(date_time_new[0:len(date_time_new) - 1])
    t_held = t_new.__sub__(t_old)

    d = t_held.days
    td = datetime.timedelta(seconds=t_held.seconds)
    t = str(td).split(":")

    return f"{d} d {t[0]} h {t[1]} m {t[2]} s"


class TerritoryTracker(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.territory_tracker.start()

    def cog_unload(self):
        self.territory_tracker.cancel()

    @tasks.loop(seconds=10)
    async def territory_tracker(self):
        if not self.client.is_ready():
            return

        channel = self.client.get_channel(territory_tracker_channel)
        if channel is None:
            return

        try:
            with open('territories.json', 'r') as f:
                old_data = json.load(f)
        except FileNotFoundError:
            old_data = {}

        new_data = getTerritoryData()
        if not new_data:
            return

        saveTerritoryData(new_data)

        # Build a count of territories per guild *after* this update
        new_counts = Counter()
        for info in new_data.values():
            new_counts[info['guild']['name']] += 1

        # Find only the changes involving The Aquarium
        owner_changes = {}
        for terr, new_info in new_data.items():
            old_info = old_data.get(terr)
            if not old_info:
                continue
            old_owner = old_info['guild']['name']
            new_owner = new_info['guild']['name']
            if old_owner != new_owner and ('The Aquarium' in (old_owner, new_owner)):
                owner_changes[terr] = {
                    'old': {
                        'owner': old_owner,
                        'prefix': old_info['guild']['prefix'],
                        'acquired': old_info['acquired']
                    },
                    'new': {
                        'owner': new_owner,
                        'prefix': new_info['guild']['prefix'],
                        'acquired': new_info['acquired']
                    }
                }

        for terr, change in owner_changes.items():
            old = change['old']
            new = change['new']

            # Determine gain vs loss
            if new['owner'] == 'The Aquarium':
                color = discord.Color.green()
                title = f"🟢 Territory Gained: **{terr}**"
            else:
                color = discord.Color.red()
                title = f"🔴 Territory Lost: **{terr}**"

            taken_dt = datetime.datetime.fromisoformat(new['acquired'].rstrip('Z'))
            taken_dt = taken_dt.replace(tzinfo=datetime.timezone.utc)

            embed = discord.Embed(
                title=title,
                color=color,
                #timestamp=taken_dt
            )
            embed.add_field(
                name="Old Owner",
                value=(
                    f"{old['owner']} [{old['prefix']}]\n"
                    f"Territories: {new_counts.get(old['owner'], 0)}"
                ),
                inline=True
            )

            embed.add_field(
                name="\u200b",
                value="➡️",
                inline=True
            )

            embed.add_field(
                name="New Owner",
                value=(
                    f"{new['owner']} [{new['prefix']}]\n"
                    f"Territories: {new_counts.get(new['owner'], 0)}"
                ),
                inline=True
            )

            await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_ready(self):
        print('TerritoryTracker task loaded')
        saveTerritoryData(getTerritoryData())
        if not self.territory_tracker.is_running():
            self.territory_tracker.start()
      

def setup(client):
    client.add_cog(TerritoryTracker(client))
