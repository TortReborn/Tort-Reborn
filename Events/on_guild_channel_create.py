from io import BytesIO
from datetime import datetime, timezone

import discord
from discord import Embed
from discord.ext import commands
from discord.ui import View, Button
from PIL import Image, ImageDraw, ImageFont

from Helpers.database import DB
from Helpers.variables import guilds, member_app_channel, application_manager_role_id


class OnGuildChannelCreate(commands.Cog):
    def __init__(self, client):
        self.client = client

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        if not (
            channel.name.startswith("ticket-")
            and channel.guild.id == guilds[0]
            and channel.category
            and channel.category.name == "Guild Applications"
        ):
            return

        db = DB(); db.connect()
        webhook = await channel.create_webhook(name=channel.name)
        db.cursor.execute(
            """
            INSERT INTO new_app (channel, ticket, webhook)
            VALUES (%s, %s, %s)
            ON CONFLICT (channel) DO NOTHING;
            """,
            (channel.id, channel.name, webhook.url)
        )
        db.connection.commit()
        db.close()

        user_name = ""
        for target in channel.overwrites:
            if isinstance(target, discord.Member) and not target.bot:
                user_name = target.display_name
                break

        img = Image.open("images/profile/welcome.png")
        draw = ImageDraw.Draw(img)
        font = ImageFont.truetype("images/profile/game.ttf", 38)
        draw.text((198, 13), user_name or "", font=font, fill="#ffba02")

        with BytesIO() as buf:
            img.save(buf, format="PNG")
            buf.seek(0)
            welcome_file = discord.File(buf, filename=f"welcome_{channel.id}.png")

        view = View()
        # view.add_item(
        #     Button(
        #         label="Guild Member",
        #         url=f"https://tally.so/r/nrpr5X?ticket={channel.id}",
        #         emoji="🔵",
        #     )
        # )
        # view.add_item(
        #     Button(
        #         label="Community Member",
        #         url=f"https://tally.so/r/3XgBrz?ticket={channel.id}",
        #         emoji="🟢",
        #     )
        # )
        await channel.send(file=welcome_file, view=view)

        exec_chan = self.client.get_channel(member_app_channel)
        if not exec_chan:
            print(f"🚨 Exec channel {member_app_channel} not found")
            return

        ticket_num = channel.name.replace("ticket-", "")
        poll_embed = Embed(
            title=f"Application {ticket_num}",
            description="A new application has been opened—please vote below:",
            colour=0x3ed63e,
        )
        poll_embed.add_field(name="Channel", value=f"<#{channel.id}>", inline=True)
        poll_embed.add_field(name="Status",  value=":green_circle: Opened", inline=True)

        poll_msg = await exec_chan.send(
            application_manager_role_id, embed=poll_embed
        )
        thread = await poll_msg.create_thread(
            name=ticket_num, auto_archive_duration=1440
        )
        for emoji in ("👍", "🤷", "👎"):
            await poll_msg.add_reaction(emoji)

        db = DB(); db.connect()
        db.cursor.execute(
            """
            UPDATE new_app
               SET created_at    = %s,
                   posted        = TRUE,
                   thread_id = %s
             WHERE channel = %s;
            """,
            (
                datetime.now(timezone.utc),
                thread.id,
                channel.id
            )
        )
        db.connection.commit()
        db.close()

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(OnGuildChannelCreate(client))
