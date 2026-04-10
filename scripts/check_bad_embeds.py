"""Quick script to list dates of bad-format raid embeds."""
import re, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import discord
from Helpers.variables import RAID_LOG_CHANNEL_ID

RAID_NAMES_LOWER = {n.lower() for n in [
    "Nest of the Grootslangs", "The Canyon Colossus",
    "The Nameless Anomaly", "Orphion's Nexus of Light",
    "The Wartorn Palace",
]}
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    channel = client.get_channel(RAID_LOG_CHANNEL_ID)
    results = []
    async for msg in channel.history(limit=None, oldest_first=True):
        for embed in msg.embeds:
            title = embed.title or ""
            desc = embed.description or ""
            if "Completed!" not in title:
                continue
            raw = BOLD_RE.findall(desc)
            igns = [n for n in raw if n.lower() not in RAID_NAMES_LOWER]
            if len(igns) < 2:
                results.append(f"{msg.created_at.strftime('%Y-%m-%d %H:%M')}  title={title!r}  desc={desc[:80]!r}")

    with open(os.path.join(os.path.dirname(__file__), "bad_embeds.txt"), "w", encoding="utf-8") as f:
        for line in results:
            f.write(line + "\n")
        f.write(f"\nTotal: {len(results)}\n")
    print(f"Wrote {len(results)} bad embeds to scripts/bad_embeds.txt")
    await client.close()

token = os.getenv("TEST_TOKEN")
client.run(token)
