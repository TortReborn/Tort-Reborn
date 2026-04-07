import asyncio
import json
from io import BytesIO

import discord
from discord.ext import tasks, commands

from Helpers.logger import log, INFO, ERROR
from Helpers.classes import BasicPlayerStats
from Helpers.database import DB, get_blacklist, get_next_app_number, get_next_hh_app_number
from Helpers.functions import generate_applicant_info
from Helpers.variables import (
    TAQ_GUILD_ID,
    EXEC_GUILD_ID,
    MEMBER_APP_CHANNEL_ID,
    HAMMERHEAD_APP_CHANNEL_ID,
    APP_MANAGER_ROLE_MENTION,
    EXEC_APP_MANAGER_ROLE_MENTION,
    APP_CATEGORY_NAME,
)
from Helpers.views import ApplicationVoteView, ThreadVoteView

# ---------------------------------------------------------------------------
# Question ID → Label mappings (must match website question IDs)
# ---------------------------------------------------------------------------

GUILD_QUESTION_LABELS = {
    "ign": "What is your IGN?",
    "timezone": "Timezone (in relation to GMT)",
    "stats_link": "Link to stats page",
    "age": "Age (optional)",
    "playtime": "Estimated playtime per day",
    "guild_experience": "Do you have any previous guild experience (name of the guild, rank, reason for leaving)?",
    "warring": "Are you interested in warring? If so, do you already have experience?",
    "know_about_taq": "What do you know about TAq?",
    "gain_from_taq": "What would you like to gain from joining TAq?",
    "contribute": "What would you contribute to TAq?",
    "anything_else": "Anything else you would like to tell us?",
    "reference": "How did you learn about TAq/reference for application? If recruited via party finder, include the recruiter's IGN.",
}

COMMUNITY_QUESTION_LABELS = {
    "ign": "What is your IGN?",
    "guild": "What guild are you in?",
    "why_community": "Why do you want to become a community member of TAq?",
    "contribute": "What would you contribute to the community?",
    "anything_else": "Is there anything else you want to say?",
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

# ---------------------------------------------------------------------------
# Hammerhead Application Question Labels & Ordering
# ---------------------------------------------------------------------------

HAMMERHEAD_GENERAL_LABELS = {
    "hh_ign_rank": "What is your IGN and current rank in TAq?",
    "hh_candidate_fit": "What makes you a good candidate and fit for the Hammerhead role?",
    "hh_hr_meaning": "In your own words, what does being HR mean and why do you want to be part of it?",
    "hh_missing_hr": "Do you think there is currently something missing in the HR team that you believe you can help address? Elaborate.",
    "hh_conflict": "Conflict between members, especially in a guild of this size, is often unavoidable. Generally speaking, how would you work to address and solve a conflict between two members?",
    "hh_vibe": "What would you describe as the overall vibe of TAq given your interactions with the members and community?",
}
HAMMERHEAD_GENERAL_ORDER = [
    "hh_ign_rank", "hh_candidate_fit", "hh_hr_meaning",
    "hh_missing_hr", "hh_conflict", "hh_vibe",
]

HAMMERHEAD_TASK_SECTIONS = {
    "Recruitment": {
        "labels": {
            "hh_recruit_experience": "Do you have experience doing any kind of recruitment in the past? If so, please elaborate.",
            "hh_recruit_strategies": "What are some recruitment strategies and how do you effectively use them?",
            "hh_recruit_retention": "How do you make sure initially recruited members are more likely to stay around?",
        },
        "order": ["hh_recruit_experience", "hh_recruit_strategies", "hh_recruit_retention"],
    },
    "Wars": {
        "labels": {
            "hh_war_importance": "Why is participating in wars important for a guild?",
            "hh_war_experience": "How much war experience do you have? You can list war count, whether you've done HQ snipes and snaking, etc. How successful have you been in the past?",
            "hh_eco_knowledge": "Do you know how to eco / the basics of it?",
            "hh_war_teaching": "Are you interested in teaching new members how to war? If so, how would you go about teaching others?",
        },
        "order": ["hh_war_importance", "hh_war_experience", "hh_eco_knowledge", "hh_war_teaching"],
    },
    "Events": {
        "labels": {
            "hh_event_ideas": "Do you have any current ideas for events the guild could do right now? Please elaborate if so.",
            "hh_event_success": "What makes for a fun, successful event for a community, in your opinion?",
            "hh_event_experience": "Do you have any previous experience in organizing events and/or skills you feel may be useful for it?",
        },
        "order": ["hh_event_ideas", "hh_event_success", "hh_event_experience"],
    },
    "Ing/Mat Grinding": {
        "labels": {
            "hh_crafting_willing": "Would you be willing to craft items used in wars and Annihilation events?",
            "hh_past_contributions": "Have you contributed ings/mats in the past for the guild? If so, what kinds and how many?",
            "hh_gbank_tracking": "Would you be comfortable in helping keep track of our inventory counts (gbank) and replenishing consumables / informing people with guild stock access in time so we never have a shortage?",
        },
        "order": ["hh_crafting_willing", "hh_past_contributions", "hh_gbank_tracking"],
    },
    "Raid": {
        "labels": {
            "hh_raid_experience": "What experience do you have raiding in the 4 different raids? Any experience raiding with guild members?",
            "hh_raid_teaching": "A new player is in your raid party. How do you generally go about introducing them to the raid and helping them out?",
        },
        "order": ["hh_raid_experience", "hh_raid_teaching"],
    },
}

HAMMERHEAD_FINAL_LABELS = {
    "hh_dedication": "Are you willing to dedicate time outside of your server playtime to our meetings, potential discussions or the development of needed suggestions?",
    "hh_expertise": "Is there any kind of expertise you have that is not currently needed by our HR but you think might be a big help to the guild and that you can imagine contributing with?",
}
HAMMERHEAD_FINAL_ORDER = ["hh_dedication", "hh_expertise"]


def _format_hammerhead_sections(answers: dict) -> list[str]:
    """Format hammerhead application answers into sections for embedding.

    Returns a list of text sections:
    [general_text, section1_text, ..., final_text]
    """
    sections = []

    # General questions
    blocks = []
    for key in HAMMERHEAD_GENERAL_ORDER:
        value = answers.get(key, "")
        if not value:
            value = "No response"
        label = HAMMERHEAD_GENERAL_LABELS.get(key, key)
        blocks.append(f"**{label}**\n{value}")
    sections.append("\n\n".join(blocks))

    # Task selector
    selected_tasks = answers.get("hh_tasks", [])
    if isinstance(selected_tasks, str):
        selected_tasks = json.loads(selected_tasks) if selected_tasks.startswith("[") else [selected_tasks]

    tasks_display = ", ".join(selected_tasks) if selected_tasks else "None"
    sections[0] += f"\n\n**What kinds of Hammerhead tasks would you like to focus on?**\n{tasks_display}"

    # Task-specific sections
    for task_name in selected_tasks:
        task_cfg = HAMMERHEAD_TASK_SECTIONS.get(task_name)
        if not task_cfg:
            continue
        blocks = []
        for key in task_cfg["order"]:
            value = answers.get(key, "")
            if not value:
                value = "No response"
            label = task_cfg["labels"].get(key, key)
            blocks.append(f"**{label}**\n{value}")
        if blocks:
            section_text = f"__**{task_name}**__\n\n" + "\n\n".join(blocks)
            sections.append(section_text)

    # Final questions
    blocks = []
    for key in HAMMERHEAD_FINAL_ORDER:
        value = answers.get(key, "")
        if not value:
            value = "No response"
        label = HAMMERHEAD_FINAL_LABELS.get(key, key)
        blocks.append(f"**{label}**\n{value}")
    if blocks:
        sections.append("\n\n".join(blocks))

    return sections


def _format_answers(answers: dict, app_type: str) -> str:
    """Format JSONB answers into a readable string with bold questions and answers on new lines."""
    if app_type == "guild":
        labels = GUILD_QUESTION_LABELS
        order = GUILD_QUESTION_ORDER
    else:
        labels = COMMUNITY_QUESTION_LABELS
        order = COMMUNITY_QUESTION_ORDER

    blocks = []
    for key in order:
        value = answers.get(key, "")
        if not value:
            continue
        label = labels.get(key, key)
        blocks.append(f"**{label}**\n{value}")

    # Include any extra keys not in the predefined order
    for key, value in answers.items():
        if key not in order and value:
            label = labels.get(key, key)
            blocks.append(f"**{label}**\n{value}")

    return "\n\n".join(blocks)


async def _send_chunked(channel, text: str, limit: int = 2000):
    """Send *text* to *channel*, splitting on paragraph boundaries if needed."""
    while text:
        if len(text) <= limit:
            await channel.send(text)
            break
        # Try to split at a double-newline (paragraph break)
        cut = text.rfind("\n\n", 0, limit)
        if cut <= 0:
            # Fall back to single newline
            cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            # Last resort: hard cut at limit
            cut = limit
        await channel.send(text[:cut])
        text = text[cut:].lstrip("\n")


class CheckWebsiteApps(commands.Cog):
    def __init__(self, client):
        self.client = client

    @tasks.loop(minutes=1)
    async def check_website_apps(self):
        """Poll the applications table for new website submissions.
        Guild restriction: creates channels in TAQ_GUILD_ID (home guild) only."""
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

        if app_type == "hammerhead":
            await self._process_hammerhead_application(
                app_id, discord_id, discord_username, discord_avatar, answers, submitted_at
            )
            return

        type_label = "Guild Member" if app_type == "guild" else "Community Member"

        # Extract IGN from answers and get next app number
        ign = answers.get("ign", "").strip() or None
        app_number = await asyncio.to_thread(get_next_app_number)
        name_part = ign or discord_username
        prefix = "c-" if app_type == "community" else ""
        channel_label = f"{prefix}{app_number}-{name_part}"

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

        # Give moderator and sr moderator roles access
        for role_name in (
            '🛡️MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
            '🛡️SR. MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
        ):
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )

        channel = await guild.create_text_channel(
            name=channel_label,
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
        combined = f"{intro}{formatted}"
        if len(combined) <= 2000:
            await channel.send(combined)
        else:
            await channel.send(intro)
            await _send_chunked(channel, formatted)

        # Post poll embed in exec channel
        exec_chan = self.client.get_channel(MEMBER_APP_CHANNEL_ID)
        if not exec_chan:
            log(ERROR, f"Exec channel {MEMBER_APP_CHANNEL_ID} not found", context="check_website_apps")
            await asyncio.to_thread(self._update_application, app_id, channel.id, None, None)
            return

        poll_embed = discord.Embed(
            title=f"Application {channel_label}",
            description="",
            colour=0x3ED63E,
        )
        poll_embed.add_field(name="Channel", value=f"<#{channel.id}>", inline=True)
        poll_embed.add_field(name="Type", value=type_label, inline=True)
        poll_embed.add_field(name="Status", value=":green_circle: Received", inline=True)

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
                    filename = f"{channel_label}-{pdata.UUID}.png"
                    player_info_file = discord.File(buf, filename=filename)
                    poll_embed.set_image(url=f"attachment://{filename}")
                    poll_embed.title = f"Application {channel_label} ({pdata.username})"

                    # Check blacklist
                    blacklist = get_blacklist()
                    for player in blacklist:
                        if pdata.UUID == player["UUID"]:
                            desc = (
                                f":no_entry: Player present on blacklist!\n"
                                f"**Name:** {pdata.username}\n**UUID:** {pdata.UUID}"
                            )
                            if player.get("reason"):
                                desc += f"\n**Reason:** {player['reason']}"
                            poll_embed.description = desc
                            break
            except Exception as e:
                log(ERROR, f"Stats image error for {ign}: {e}", context="check_website_apps")

        # Send poll message with ping and vote buttons
        vote_view = ApplicationVoteView()
        if player_info_file:
            poll_msg = await exec_chan.send(
                f"{APP_MANAGER_ROLE_MENTION} **New {type_label} application received!**",
                embed=poll_embed,
                file=player_info_file,
                view=vote_view,
            )
        else:
            poll_msg = await exec_chan.send(
                f"{APP_MANAGER_ROLE_MENTION} **New {type_label} application received!**",
                embed=poll_embed,
                view=vote_view,
            )

        # Create discussion thread
        thread = await poll_msg.create_thread(
            name=channel_label, auto_archive_duration=1440
        )

        # Post the application content in the thread
        stats_line = f"<https://wynncraft.com/stats/player/{ign}>\n" if ign else ""
        thread_header = f"**Application from {mention} ({discord_username}):**\n{stats_line}\n"
        thread_combined = f"{thread_header}{formatted}"
        if len(thread_combined) <= 2000:
            await thread.send(thread_combined)
        else:
            await thread.send(thread_header)
            await _send_chunked(thread, formatted)

        # Send vote buttons in the thread
        await thread.send("**Vote on this application:**", view=ThreadVoteView())

        # Update applications table with channel_id, thread_id, poll_message_id, app_number
        await asyncio.to_thread(self._update_application, app_id, channel.id, thread.id, poll_msg.id, app_number)

    async def _process_hammerhead_application(
        self, app_id, discord_id, discord_username, discord_avatar, answers, submitted_at
    ):
        """Process a Hammerhead (HR) application — send embeds to exec channel."""
        # Extract IGN from the ign_rank answer (format: "IGN, Rank")
        ign_rank = answers.get("hh_ign_rank", "").strip()
        ign = ign_rank.split(",")[0].strip() if ign_rank else discord_username
        app_number = await asyncio.to_thread(get_next_hh_app_number)

        # Get the hammerhead app channel in the exec guild
        exec_chan = self.client.get_channel(HAMMERHEAD_APP_CHANNEL_ID)
        if not exec_chan:
            log(ERROR, f"Hammerhead app channel {HAMMERHEAD_APP_CHANNEL_ID} not found", context="check_website_apps")
            await asyncio.to_thread(self._update_application, app_id, HAMMERHEAD_APP_CHANNEL_ID, None, None, app_number)
            return

        # Format the application into sections
        sections = _format_hammerhead_sections(answers)

        # Build embeds from sections, respecting Discord's 4096-char description limit
        embeds_data = []  # list of description strings, each ≤ 4096 chars
        current_desc = ""
        for section in sections:
            # If adding this section would exceed the limit, start a new embed
            if current_desc and len(current_desc) + len("\n\n") + len(section) > 4000:
                embeds_data.append(current_desc)
                current_desc = section
            else:
                current_desc = f"{current_desc}\n\n{section}" if current_desc else section
        if current_desc:
            embeds_data.append(current_desc)

        total_parts = len(embeds_data)
        mention = f"<@{discord_id}>"

        # Format submission time
        if hasattr(submitted_at, 'strftime'):
            time_str = submitted_at.strftime("%A, %B %d, %Y %I:%M %p")
        else:
            time_str = str(submitted_at)

        # Send embeds as separate messages
        # Vote buttons go on the last message (same message the thread hangs off of)
        first_msg = None
        last_msg = None
        for i, desc in enumerate(embeds_data):
            part_label = f"(Part {i + 1}/{total_parts})" if total_parts > 1 else ""
            embed = discord.Embed(
                title=f"Hammerhead Application - {ign} {part_label}",
                description=desc,
                colour=0x04B0EB,  # Hammerhead rank colour
            )
            if i == 0:
                embed.add_field(name="Applicant", value=f"{mention} ({discord_username})", inline=True)
                embed.add_field(name="Type", value="Hammerhead", inline=True)
            if i == total_parts - 1:
                embed.add_field(name="Status", value=":green_circle: Received", inline=True)
                embed.set_footer(text=f"Submission Time: {time_str}")

            is_last = i == total_parts - 1

            if i == 0:
                # First message gets the ping
                msg = await exec_chan.send(
                    f"{EXEC_APP_MANAGER_ROLE_MENTION} **New Hammerhead application received!**",
                    embed=embed,
                    view=ApplicationVoteView() if is_last else None,
                )
                first_msg = msg
                last_msg = msg
            else:
                msg = await exec_chan.send(
                    embed=embed,
                    view=ApplicationVoteView() if is_last else None,
                )
                last_msg = msg

        # Create discussion thread off the last embed message
        thread = None
        if last_msg:
            thread_name = f"hh-{app_number}-{ign}"
            thread = await last_msg.create_thread(
                name=thread_name, auto_archive_duration=1440
            )

            # Post vote buttons in the thread
            await thread.send("**Vote on this application:**", view=ThreadVoteView())

        # Update DB — poll_message_id points to last message (has vote buttons + status)
        poll_msg_id = last_msg.id if last_msg else None
        thread_id = thread.id if thread else None
        await asyncio.to_thread(
            self._update_application, app_id, HAMMERHEAD_APP_CHANNEL_ID,
            thread_id, poll_msg_id, app_number
        )

    @staticmethod
    def _update_application(app_id, channel_id, thread_id, poll_message_id, app_number=None):
        """Blocking: update the applications row with Discord IDs."""
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                """
                UPDATE applications
                SET channel_id = %s, thread_id = %s, poll_message_id = %s,
                    poll_status = ':green_circle: Received', app_number = %s
                WHERE id = %s
                """,
                (channel_id, thread_id, poll_message_id, app_number, app_id),
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
