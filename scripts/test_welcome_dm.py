"""Quick script to send the welcome DM to yourself using the dev bot."""

import asyncio
import json
import os

import discord
from dotenv import load_dotenv
from Helpers.variables import (
    WELCOME_CHANNEL_ID, ANNOUNCEMENT_CHANNEL_ID, FAQ_CHANNEL_ID, GUILD_BANK_CHANNEL_ID,
    RANK_UP_CHANNEL_ID, TAQ_ROLES_CHANNEL_ID, BOT_COMMAND_CHANNEL_ID, RAID_COLLECTING_CHANNEL_ID,
)

load_dotenv()

USER_ID = 170719819715313665


async def main():
    with open("data/guild_welcome_dm.json", "r", encoding="utf-8") as f:
        template_data = json.load(f)

    placeholders = {
        "[user]": f"<@{USER_ID}>",
        "[welcome_channel]": f"<#{WELCOME_CHANNEL_ID}>",
        "[announcement_channel]": f"<#{ANNOUNCEMENT_CHANNEL_ID}>",
        "[faq_channel]": f"<#{FAQ_CHANNEL_ID}>",
        "[guild_bank_channel]": f"<#{GUILD_BANK_CHANNEL_ID}>",
        "[rank_up_channel]": f"<#{RANK_UP_CHANNEL_ID}>",
        "[taq_roles_channel]": f"<#{TAQ_ROLES_CHANNEL_ID}>",
        "[bot_command_channel]": f"<#{BOT_COMMAND_CHANNEL_ID}>",
        "[raid_collecting_channel]": f"<#{RAID_COLLECTING_CHANNEL_ID}>",
    }

    def replace(text):
        for key, value in placeholders.items():
            text = text.replace(key, str(value))
        return text

    header = replace(template_data.get("header", ""))
    channels_title = template_data.get("channels_title", "")
    channels = "\n".join(
        f"> {replace(ch['channel'])} - {ch['description']}"
        for ch in template_data.get("channels", [])
    )
    footer = replace(template_data.get("footer", ""))

    message = f"{header}\n\n{channels_title}\n\n{channels}\n\n{footer}"

    client = discord.Client(intents=discord.Intents.default())

    @client.event
    async def on_ready():
        print(f"Logged in as {client.user}")
        try:
            user = await client.fetch_user(USER_ID)
            await user.send(message)
            print("Welcome DM sent!")
        except Exception as e:
            print(f"Failed: {e}")
        await client.close()

    await client.start(os.getenv("TEST_TOKEN"))


if __name__ == "__main__":
    asyncio.run(main())
