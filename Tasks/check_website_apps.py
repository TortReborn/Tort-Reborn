import asyncio
import json
from io import BytesIO

import discord
from discord.ext import tasks, commands

from Helpers.logger import log, INFO, ERROR
from Helpers.classes import BasicPlayerStats
from Helpers.database import DB, get_blacklist
from Helpers.functions import generate_applicant_info
from Helpers.variables import (
    TAQ_GUILD_ID,
    MEMBER_APP_CHANNEL_ID,
    APP_MANAGER_ROLE_MENTION,
    APP_CATEGORY_NAME,
)

# ---------------------------------------------------------------------------
# Question ID → Label mappings (must match website question IDs)
# ---------------------------------------------------------------------------

GUILD_QUESTION_LABELS = {
    "ign": "IGN",
    "timezone": "Timezone (in relation to GMT)",
    "stats_link": "Link to stats page",
    "age": "Age",
    "playtime": "Estimated playtime per day",
    "guild_experience": "Previous guild experience",
    "warring": "Warring interest/experience",
    "know_about_taq": "What do you know about TAq?",
    "gain_from_taq": "What would you like to gain from joining TAq?",
    "contribute": "What would you contribute to TAq?",
    "anything_else": "Anything else",
    "reference": "How did you learn about TAq",
}

COMMUNITY_QUESTION_LABELS = {
    "ign": "IGN",
    "guild": "Current guild",
    "why_community": "Why community member?",
    "contribute": "What would you contribute?",
    "anything_else": "Anything else",
}

# Order in which questions should appear (for consistent formatting)
GUILD_QUESTION_ORDER = [
    "ign", "timezone", "stats_link", "age", "playtime",
    "guild_experience", "warring", "know_about_taq",
    "gain_from_taq", "contribute", "anything_else", "reference",
]
COMMUNITY_QUESTION_ORDER = [
    "ign", "guild", "why_community", "contribute", "anything_else",
]


def _format_answers(answers: dict, app_type: str) -> str:
    """Format JSONB answers into a readable string."""
    if app_type == "guild":
        labels = GUILD_QUESTION_LABELS
        order = GUILD_QUESTION_ORDER
    else:
        labels = COMMUNITY_QUESTION_LABELS
        order = COMMUNITY_QUESTION_ORDER

    lines = []
    for key in order:
        value = answers.get(key, "")
        if not value:
            continue
        label = labels.get(key, key)
        lines.append(f"**{label}:** {value}")

    # Include any extra keys not in the predefined order
    for key, value in answers.items():
        if key not in order and value:
            label = labels.get(key, key)
            lines.append(f"**{label}:** {value}")

    return "\n".join(lines)


class CheckWebsiteApps(commands.Cog):
    def __init__(self, client):
        self.client = client

    @tasks.loop(minutes=1)
    async def check_website_apps(self):
        """Poll the applications table for new website submissions."""
        rows = await asyncio.to_thread(self._fetch_pending_apps)
        if not rows:
            return

        for row in rows:
            try:
                await self._process_application(row)
            except Exception as e:
                log(ERROR, f"Error processing app {row[0]}: {e}", context="check_website_apps")

    @staticmethod
    def _fetch_pending_apps():
        """Blocking: atomically claim pending website applications by setting channel_id = -1."""
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                """
                UPDATE applications
                SET channel_id = -1
                WHERE id IN (
                    SELECT id FROM applications
                    WHERE status = 'pending' AND channel_id IS NULL
                    ORDER BY submitted_at ASC
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id, application_type, discord_id, discord_username,
                          discord_avatar, answers, submitted_at
                """
            )
            rows = db.cursor.fetchall()
            db.connection.commit()
            return rows
        finally:
            db.close()

    async def _process_application(self, row):
        app_id, app_type, discord_id, discord_username, discord_avatar, answers, submitted_at = row

        # Parse answers if it's a string
        if isinstance(answers, str):
            answers = json.loads(answers)

        type_label = "Guild Member" if app_type == "guild" else "Community Member"

        # Extract IGN from answers
        ign = answers.get("ign", "").strip() or None

        # Resolve the guild
        guild = self.client.get_guild(TAQ_GUILD_ID)
        if not guild:
            log(ERROR, f"Could not find guild {TAQ_GUILD_ID}", context="check_website_apps")
            return

        # Find the applications category
        category = discord.utils.get(guild.categories, name=APP_CATEGORY_NAME)
        if not category:
            log(ERROR, f"Could not find category '{APP_CATEGORY_NAME}'", context="check_website_apps")
            return

        # Resolve the applicant as a guild member
        applicant = guild.get_member(int(discord_id))
        if applicant is None:
            try:
                applicant = await guild.fetch_member(int(discord_id))
            except Exception:
                applicant = None

        # Create channel with permission overwrites
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True
            ),
        }
        if applicant:
            overwrites[applicant] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )

        channel_name = f"web-{discord_username}"
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
        )

        # Format and post the combined welcome + application in the channel
        mention = applicant.mention if applicant else f"<@{discord_id}>"
        formatted = _format_answers(answers, app_type)

        intro = (
            f"Hi {mention}, thank you for applying! \U0001F420\n"
            f"Your **{type_label}** application has been received and is being reviewed. "
            f"We aim to get back to you within 12 hours.\n\n"
        )
        combined = f"{intro}>>> {formatted}"
        if len(combined) <= 2000:
            await channel.send(combined)
        else:
            await channel.send(intro)
            await channel.send(f">>> {formatted}")

        # Post poll embed in exec channel
        exec_chan = self.client.get_channel(MEMBER_APP_CHANNEL_ID)
        if not exec_chan:
            log(ERROR, f"Exec channel {MEMBER_APP_CHANNEL_ID} not found", context="check_website_apps")
            await asyncio.to_thread(self._update_application, app_id, channel.id, None, None)
            return

        poll_embed = discord.Embed(
            title=f"Application web-{discord_username}",
            description="A new website application has been submitted\u2014please vote below:",
            colour=0x3ED63E,
        )
        poll_embed.add_field(name="Channel", value=f"<#{channel.id}>", inline=True)
        poll_embed.add_field(name="Type", value=type_label, inline=True)
        poll_embed.add_field(name="Status", value=":green_circle: Received", inline=True)
        if ign:
            poll_embed.add_field(name="IGN", value=ign, inline=True)

        # Generate player stats image if IGN is available
        player_info_file = None
        if ign:
            try:
                pdata = await asyncio.to_thread(BasicPlayerStats, ign)
                if not pdata.error:
                    img = generate_applicant_info(pdata)
                    buf = BytesIO()
                    img.save(buf, format="PNG")
                    buf.seek(0)
                    filename = f"web-{discord_username}-{pdata.UUID}.png"
                    player_info_file = discord.File(buf, filename=filename)
                    poll_embed.set_image(url=f"attachment://{filename}")
                    poll_embed.title = f"Application web-{discord_username} ({pdata.username})"

                    # Check blacklist
                    blacklist = get_blacklist()
                    for player in blacklist:
                        if pdata.UUID == player["UUID"]:
                            poll_embed.description = (
                                f":no_entry: Player present on blacklist!\n"
                                f"**Name:** {pdata.username}\n**UUID:** {pdata.UUID}"
                            )
                            break
            except Exception as e:
                log(ERROR, f"Stats image error for {ign}: {e}", context="check_website_apps")

        # Send poll message with ping
        if player_info_file:
            poll_msg = await exec_chan.send(
                f"{APP_MANAGER_ROLE_MENTION} **New {type_label} application received!**",
                embed=poll_embed,
                file=player_info_file,
            )
        else:
            poll_msg = await exec_chan.send(
                f"{APP_MANAGER_ROLE_MENTION} **New {type_label} application received!**",
                embed=poll_embed,
            )

        # Create discussion thread and add reactions
        thread = await poll_msg.create_thread(
            name=f"web-{discord_username}", auto_archive_duration=1440
        )
        for emoji in ("\U0001F44D", "\U0001F937", "\U0001F44E"):
            await poll_msg.add_reaction(emoji)

        # Post the application content in the thread
        await thread.send(f"**Application from {mention} ({discord_username}):**\n>>> {formatted}")

        # Update applications table with channel_id, thread_id, poll_message_id
        await asyncio.to_thread(self._update_application, app_id, channel.id, thread.id, poll_msg.id)

    @staticmethod
    def _update_application(app_id, channel_id, thread_id, poll_message_id):
        """Blocking: update the applications row with Discord IDs."""
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                """
                UPDATE applications
                SET channel_id = %s, thread_id = %s, poll_message_id = %s,
                    poll_status = ':green_circle: Received'
                WHERE id = %s
                """,
                (channel_id, thread_id, poll_message_id, app_id),
            )
            db.connection.commit()
        finally:
            db.close()

    @check_website_apps.before_loop
    async def before_check_website_apps(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.check_website_apps.is_running():
            self.check_website_apps.start()


def setup(client):
    client.add_cog(CheckWebsiteApps(client))
