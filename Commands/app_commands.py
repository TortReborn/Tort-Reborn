import asyncio
import json
from datetime import datetime, timezone
from io import BytesIO

import discord
from discord import SlashCommandGroup, ApplicationContext
from discord.ext import commands

from Helpers.classes import BasicPlayerStats
from Helpers.database import DB
from Helpers.embed_updater import update_web_poll_embed
from Helpers.functions import generate_applicant_info, getPlayerUUID, getPlayerDatav3
from Helpers.openai_helper import parse_application, match_recruiter_name
from Helpers.sheets import add_row
from Helpers.variables import (
    ALL_GUILD_IDS,
    MEMBER_APP_CHANNEL_ID,
    APP_MANAGER_ROLE_MENTION,
    INVITED_CATEGORY_NAME,
    CLOSED_CATEGORY_NAME,
    APP_ARCHIVE_CHANNEL_NAME,
    ERROR_CHANNEL_ID,
    MANUAL_REVIEW_ROLE_ID,
)


class WebAppCommands(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client

    app_group = SlashCommandGroup(
        'app', 'HR: Website application management commands',
        guild_ids=ALL_GUILD_IDS,
        default_member_permissions=discord.Permissions(manage_roles=True)
    )

    # --- Lookup helper ---

    async def _lookup_web_app(self, ctx, *, require_status=None, allow_status=None):
        """Look up an application record from the applications table.

        Args:
            require_status: If set, the app must have this exact status.
            allow_status: If set, the app must have one of these statuses.

        Returns (ticket_channel, row_dict) or None.
        """
        source = ctx.channel

        def _query(source_id):
            db = DB(); db.connect()
            try:
                # Try channel_id first, then thread_id
                db.cursor.execute(
                    """SELECT id, application_type, discord_id, thread_id, status,
                              channel_id, guild_leave_pending, answers, discord_username
                       FROM applications WHERE channel_id = %s""",
                    (source_id,)
                )
                row = db.cursor.fetchone()
                if not row:
                    db.cursor.execute(
                        """SELECT id, application_type, discord_id, thread_id, status,
                                  channel_id, guild_leave_pending, answers, discord_username
                           FROM applications WHERE thread_id = %s""",
                        (source_id,)
                    )
                    row = db.cursor.fetchone()
                return row
            finally:
                db.close()

        row = await asyncio.to_thread(_query, source.id)

        if not row:
            await ctx.followup.send(
                "No website application record found. Use this in an application channel or its exec thread.",
                ephemeral=True,
            )
            return None

        app_id, app_type, discord_id, thread_id, status, channel_id, guild_leave_pending, answers, discord_username = row

        if require_status and status != require_status:
            await ctx.followup.send(
                f"This application has status **{status}**, expected **{require_status}**.",
                ephemeral=True,
            )
            return None

        if allow_status and status not in allow_status:
            await ctx.followup.send(
                f"This application has status **{status}**, which is not valid for this action.",
                ephemeral=True,
            )
            return None

        # Parse answers if string
        if isinstance(answers, str):
            answers = json.loads(answers)

        # Resolve ticket channel
        ticket_channel = self.client.get_channel(channel_id)
        if ticket_channel is None:
            try:
                ticket_channel = await self.client.fetch_channel(channel_id)
            except Exception:
                await ctx.followup.send(
                    "Could not find the ticket channel.", ephemeral=True
                )
                return None

        row_dict = {
            "id": app_id,
            "application_type": app_type,
            "discord_id": discord_id,
            "discord_username": discord_username or str(app_id),
            "thread_id": thread_id,
            "status": status,
            "channel_id": channel_id,
            "guild_leave_pending": guild_leave_pending,
            "answers": answers,
        }

        return ticket_channel, row_dict

    # --- /app accept ---

    @app_group.command(name='accept', description='HR: Accept this website application')
    async def accept(self, ctx: ApplicationContext):
        await ctx.defer(ephemeral=True)

        result = await self._lookup_web_app(ctx, require_status='pending')
        if result is None:
            return

        channel, app = result
        now = datetime.now(timezone.utc)

        ign = (app["answers"].get("ign") or "").strip()
        applicant = await self._resolve_member(channel, int(app["discord_id"]))

        if app["application_type"] == "guild":
            await self._accept_guild(ctx, channel, app, applicant, ign, now)
        else:
            await self._accept_community(ctx, channel, app, applicant, ign, now)

    async def _accept_guild(self, ctx, channel, app, applicant, ign, now):
        mention = applicant.mention if applicant else f"<@{app['discord_id']}>"
        partytort = discord.utils.get(channel.guild.emojis, name="partytort")
        party_emoji = str(partytort) if partytort else "\U0001F389"
        app_id = app["id"]

        # Check if player is currently in a guild
        uuid = None
        in_guild = False
        in_taq = False
        current_guild_name = None

        if ign:
            uuid_data = await asyncio.to_thread(getPlayerUUID, ign)
            uuid = uuid_data[1] if uuid_data else None

            if uuid:
                player_data = await asyncio.to_thread(getPlayerDatav3, uuid)
                if isinstance(player_data, dict):
                    guild_info = player_data.get("guild")
                    if guild_info and isinstance(guild_info, dict):
                        current_guild_name = guild_info.get("name")
                        if current_guild_name:
                            in_guild = True
                            if current_guild_name == "The Aquarium":
                                in_taq = True

        # Send message based on guild status
        link_id = applicant.id if applicant else int(app["discord_id"])
        username = app["discord_username"]

        if in_taq:
            # Player is already in TAq — accept and register immediately
            await channel.send(
                f"Hey {mention},\n\n"
                f"Congratulations, your application to join **The Aquarium** has been "
                f"**accepted**! {party_emoji}\n\n"
                f"You're already in the guild — your Discord roles are being set up now.\n\n"
                f"Best Regards,\n"
                f"The Aquarium Applications Team"
            )

            # Link and mark as linked immediately
            if ign and uuid:
                await asyncio.to_thread(self._link_discord, link_id, ign, uuid, channel.id, linked=False)

            # Trigger auto-registration directly
            from Tasks.update_member_data import UpdateMemberData
            cog = self.client.get_cog("UpdateMemberData")
            if cog and uuid:
                try:
                    await cog._auto_register_joined_member(uuid, ign)
                except Exception as e:
                    from Helpers.logger import log, ERROR as LOG_ERROR
                    log(LOG_ERROR, f"Immediate registration failed for {ign}: {e}", context="app_commands")

            await update_web_poll_embed(self.client, channel.id, ":orange_circle: Registered", 0xFFE019)
            feedback = f"Application accepted. IGN: `{ign}`. Player was already in TAq — registered immediately."

        elif in_guild:
            await channel.send(
                f"Hey {mention},\n\n"
                f"Congratulations, your application to join **The Aquarium** has been "
                f"**accepted**! {party_emoji}\n\n"
                f"However, you're currently in **{current_guild_name}**. "
                f"Please leave your current guild so we can invite you. "
                f"Let us know when you've left!\n\n"
                f"Best Regards,\n"
                f"The Aquarium Applications Team"
            )

            if ign and uuid:
                await asyncio.to_thread(self._link_discord, link_id, ign, uuid, channel.id, linked=False)

            await update_web_poll_embed(self.client, channel.id, ":yellow_circle: Accepted - Pending Leave", 0xFFE019)
            feedback = (
                f"Application accepted. IGN: `{ign}`. "
                f"Player is currently in **{current_guild_name}**. Monitoring for guild leave."
            )
        else:
            await channel.send(
                f"Hey {mention},\n\n"
                f"Congratulations, your application to join **The Aquarium** has been "
                f"**accepted**! {party_emoji}\n\n"
                f"To join, just type `/gu join TAq` next time you're online. "
                f"Once you join the guild in-game, you will be given your Discord roles.\n\n"
                f"Best Regards,\n"
                f"The Aquarium Applications Team"
            )

            if ign and uuid:
                await asyncio.to_thread(self._link_discord, link_id, ign, uuid, channel.id, linked=False)

            guild = self.client.get_guild(channel.guild.id) or channel.guild
            invited_cat = discord.utils.get(guild.categories, name=INVITED_CATEGORY_NAME)
            if invited_cat:
                try:
                    await channel.edit(category=invited_cat)
                except discord.Forbidden:
                    await ctx.followup.send(
                        "Could not move ticket to Invited category (missing permissions).",
                        ephemeral=True,
                    )
            try:
                await channel.edit(name=f"inv-{username}")
            except discord.Forbidden:
                pass
            await update_web_poll_embed(self.client, channel.id, ":green_circle: Invited", 0x3ED63E)
            feedback = f"Application accepted. IGN: `{ign}`. User will be auto-registered when they join."

        # Update DB (guild_leave_pending only if in another guild, not TAq)
        await asyncio.to_thread(
            self._db_accept, app_id, now, in_guild and not in_taq
        )

        # Update exec thread
        await self._update_exec_thread(app["thread_id"], "accepted", "Guild Member", ign)

        # Recruiter tracking
        await self._process_recruiter_tracking(channel, ign, int(app["discord_id"]), app["id"])

        await ctx.followup.send(feedback, ephemeral=True)

    async def _accept_community(self, ctx, channel, app, applicant, ign, now):
        mention = applicant.mention if applicant else f"<@{app['discord_id']}>"
        app_id = app["id"]

        await channel.send(
            f"Hey {mention},\n\n"
            f"Congratulations, your application to become a **Community Member** of "
            f"The Aquarium has been **accepted**! \U0001F389\n\n"
            f"Welcome to the community!\n\n"
            f"Best Regards,\n"
            f"The Aquarium Applications Team"
        )

        # Auto-link and set nickname
        link_id = applicant.id if applicant else int(app["discord_id"])
        uuid = None
        if ign:
            uuid_data = await asyncio.to_thread(getPlayerUUID, ign)
            uuid = uuid_data[1] if uuid_data else None
            if uuid:
                await asyncio.to_thread(self._link_discord, link_id, ign, uuid, channel.id, linked=True)
            if applicant:
                try:
                    await applicant.edit(nick=ign)
                except discord.Forbidden:
                    pass

        # Assign "Tortoise - Community" role
        role_status = ""
        if applicant:
            guild = self.client.get_guild(channel.guild.id) or channel.guild
            community_role = discord.utils.get(guild.roles, name="Tortoise - Community")
            if community_role:
                try:
                    await applicant.add_roles(community_role, reason="Community member application accepted")
                    role_status = "Role assigned."
                except Exception as e:
                    role_status = f"**Role failed:** {e}"
            else:
                role_status = "**Role not found.**"
        else:
            role_status = "**Role skipped** (member not found)."

        # Rename channel
        username = app["discord_username"]
        try:
            await channel.edit(name=f"c-accepted-{username}")
        except discord.Forbidden:
            pass

        # Update poll embed
        await update_web_poll_embed(self.client, channel.id, ":orange_circle: Accepted", 0xFFE019)

        # Update DB
        await asyncio.to_thread(self._db_accept, app_id, now, False)

        # Update exec thread
        await self._update_exec_thread(app["thread_id"], "accepted", "Community Member", ign)

        await ctx.followup.send(
            f"Application accepted (Community Member). IGN: `{ign}`. {role_status}",
            ephemeral=True,
        )

    # --- /app deny ---

    @app_group.command(name='deny', description='HR: Deny this website application')
    async def deny(self, ctx: ApplicationContext):
        await ctx.defer(ephemeral=True)

        result = await self._lookup_web_app(ctx, require_status='pending')
        if result is None:
            return

        channel, app = result
        now = datetime.now(timezone.utc)

        applicant = await self._resolve_member(channel, int(app["discord_id"]))
        mention = applicant.mention if applicant else f"<@{app['discord_id']}>"

        await channel.send(
            f"Hi {mention},\n\n"
            f"We regret to inform you that your application to join our guild did not "
            f"meet our current standards. We appreciate your interest and thank you "
            f"for considering us.\n\n"
            f"Best Regards,\n"
            f"The Aquarium Applications Team"
        )

        # Rename channel
        username = app["discord_username"]
        new_name = f"denied-{username}" if app["application_type"] == "guild" else f"c-denied-{username}"
        try:
            await channel.edit(name=new_name)
        except discord.Forbidden:
            pass

        # Update poll embed
        await update_web_poll_embed(self.client, channel.id, ":orange_circle: Denied", 0xFFE019)

        # Update DB
        await asyncio.to_thread(self._db_deny, app["id"], now)

        # Update exec thread
        await self._update_exec_thread(app["thread_id"], "denied")

        await ctx.followup.send("Application denied.", ephemeral=True)

    # --- /app invited ---

    @app_group.command(name='invited', description='HR: Invite an accepted applicant who has left their guild')
    async def invited(self, ctx: ApplicationContext):
        await ctx.defer(ephemeral=True)

        result = await self._lookup_web_app(ctx, require_status='accepted')
        if result is None:
            return

        channel, app = result

        if app["application_type"] != "guild":
            await ctx.followup.send(
                "This command is only for accepted guild member applications.",
                ephemeral=True,
            )
            return

        if app["guild_leave_pending"]:
            await ctx.followup.send(
                "This applicant has not left their guild yet. "
                "The bot will notify you when they do.",
                ephemeral=True,
            )
            return

        applicant = await self._resolve_member(channel, int(app["discord_id"]))
        mention = applicant.mention if applicant else f"<@{app['discord_id']}>"
        partytort = discord.utils.get(channel.guild.emojis, name="partytort")
        party_emoji = str(partytort) if partytort else "\U0001F389"

        await channel.send(
            f"Hey {mention},\n\n"
            f"Great news! You're now ready to join **The Aquarium**! {party_emoji}\n\n"
            f"To join, just type `/gu join TAq` next time you're online. "
            f"Once you join the guild in-game, you will be given your Discord roles.\n\n"
            f"Best Regards,\n"
            f"The Aquarium Applications Team"
        )

        # Move to Invited category
        guild = self.client.get_guild(channel.guild.id) or channel.guild
        invited_cat = discord.utils.get(guild.categories, name=INVITED_CATEGORY_NAME)
        if invited_cat:
            try:
                await channel.edit(category=invited_cat)
            except discord.Forbidden:
                await ctx.followup.send(
                    "Could not move ticket to Invited category (missing permissions).",
                    ephemeral=True,
                )

        # Rename channel
        username = app["discord_username"]
        try:
            await channel.edit(name=f"inv-{username}")
        except discord.Forbidden:
            pass

        # Update poll embed
        await update_web_poll_embed(self.client, channel.id, ":green_circle: Invited", 0x3ED63E)

        await ctx.followup.send(
            "Invite sent. User will be auto-registered when they join the guild in-game.",
            ephemeral=True,
        )

    # --- /app close ---

    @app_group.command(name='close', description='HR: Close this application ticket')
    async def close(self, ctx: ApplicationContext):
        await ctx.defer(ephemeral=True)

        result = await self._lookup_web_app(ctx)
        if result is None:
            return

        channel, app = result

        # Check if already closed
        guild = self.client.get_guild(channel.guild.id) or channel.guild
        closed_cat = discord.utils.get(guild.categories, name=CLOSED_CATEGORY_NAME)
        if closed_cat and getattr(channel, 'category', None) == closed_cat:
            await ctx.followup.send("This application is already closed.", ephemeral=True)
            return

        # Send close message
        await channel.send("This application has been closed.")

        # Move to Closed Applications category (on_guild_channel_update handles rename + poll update)
        if closed_cat:
            try:
                await channel.edit(category=closed_cat)
            except discord.Forbidden:
                await ctx.followup.send(
                    "Could not move channel to Closed Applications (missing permissions).",
                    ephemeral=True,
                )
                return

        await ctx.followup.send("Application closed.", ephemeral=True)

    # --- /app transcribe ---

    @app_group.command(name='transcribe', description='HR: Transcribe this application to the archive channel')
    async def transcribe(self, ctx: ApplicationContext):
        await ctx.defer(ephemeral=True)

        result = await self._lookup_web_app(ctx)
        if result is None:
            return

        channel, app = result
        guild = self.client.get_guild(channel.guild.id) or channel.guild

        # Find the archive channel by name
        archive_chan = discord.utils.get(guild.text_channels, name=APP_ARCHIVE_CHANNEL_NAME)
        if not archive_chan:
            await ctx.followup.send(
                f"Archive channel `#{APP_ARCHIVE_CHANNEL_NAME}` not found.",
                ephemeral=True,
            )
            return

        # Fetch all messages
        messages = []
        async for msg in channel.history(limit=500, oldest_first=True):
            messages.append(msg)

        if not messages:
            await ctx.followup.send("No messages found in this channel.", ephemeral=True)
            return

        # Build transcript
        ign = (app["answers"].get("ign") or "").strip()
        type_label = "Guild Member" if app["application_type"] == "guild" else "Community Member"
        created_at = messages[0].created_at.strftime("%Y-%m-%d %H:%M:%S UTC")

        transcript_lines = [
            f"=== Application Transcript ===",
            f"Channel: #{channel.name}",
            f"Type: {type_label}",
            f"Applicant: {app['discord_username']} ({app['discord_id']})",
            f"IGN: {ign or 'N/A'}",
            f"Status: {app['status']}",
            f"Created: {created_at}",
            f"Messages: {len(messages)}",
            f"{'=' * 40}",
            "",
        ]

        for msg in messages:
            timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            author = msg.author.display_name
            bot_tag = " [BOT]" if msg.author.bot else ""
            transcript_lines.append(f"[{timestamp}] {author}{bot_tag}")

            if msg.content:
                transcript_lines.append(msg.content)

            for embed in msg.embeds:
                if embed.title:
                    transcript_lines.append(f"  [Embed: {embed.title}]")
                if embed.description:
                    transcript_lines.append(f"  {embed.description}")
                for field in embed.fields:
                    transcript_lines.append(f"  {field.name}: {field.value}")

            for att in msg.attachments:
                transcript_lines.append(f"  [Attachment: {att.filename} — {att.url}]")

            transcript_lines.append("")

        transcript_text = "\n".join(transcript_lines)

        # Build summary embed
        embed = discord.Embed(
            title=f"Transcript: #{channel.name}",
            color=0x2F3136,
        )
        embed.add_field(name="Type", value=type_label, inline=True)
        embed.add_field(name="Status", value=app["status"].title(), inline=True)
        embed.add_field(name="Applicant", value=f"<@{app['discord_id']}>", inline=True)
        if ign:
            embed.add_field(name="IGN", value=ign, inline=True)
        embed.add_field(name="Messages", value=str(len(messages)), inline=True)

        # Send as file attachment
        buf = BytesIO(transcript_text.encode("utf-8"))
        file = discord.File(buf, filename=f"transcript-{channel.name}.txt")

        await archive_chan.send(embed=embed, file=file)
        await ctx.followup.send(f"Transcript saved to {archive_chan.mention}.", ephemeral=True)

    # --- DB helpers ---

    @staticmethod
    def _db_accept(app_id, now, guild_leave_pending):
        db = DB(); db.connect()
        try:
            db.cursor.execute(
                """UPDATE applications
                   SET status = 'accepted', reviewed_at = %s, guild_leave_pending = %s
                   WHERE id = %s""",
                (now, guild_leave_pending, app_id)
            )
            db.connection.commit()
        finally:
            db.close()

    @staticmethod
    def _db_deny(app_id, now):
        db = DB(); db.connect()
        try:
            db.cursor.execute(
                """UPDATE applications
                   SET status = 'denied', reviewed_at = %s
                   WHERE id = %s""",
                (now, app_id)
            )
            db.connection.commit()
        finally:
            db.close()

    @staticmethod
    def _link_discord(discord_id, ign, uuid, app_channel, linked=False):
        db = DB(); db.connect()
        try:
            db.cursor.execute(
                """INSERT INTO discord_links (discord_id, ign, uuid, linked, rank, app_channel)
                   VALUES (%s, %s, %s, %s, '', %s)
                   ON CONFLICT (discord_id) DO UPDATE
                   SET ign = EXCLUDED.ign, uuid = EXCLUDED.uuid,
                       app_channel = EXCLUDED.app_channel,
                       linked = EXCLUDED.linked""",
                (discord_id, ign, uuid, linked, app_channel)
            )
            db.connection.commit()
        finally:
            db.close()

    # --- Shared helpers ---

    async def _resolve_member(self, channel, discord_id: int) -> discord.Member | None:
        guild = self.client.get_guild(channel.guild.id) or channel.guild
        member = guild.get_member(discord_id)
        if member is None:
            try:
                member = await guild.fetch_member(discord_id)
            except Exception:
                pass
        return member

    async def _update_exec_thread(self, thread_id, decision, app_type=None, ign=None):
        if not thread_id:
            return
        thread = self.client.get_channel(thread_id)
        if thread is None:
            try:
                thread = await self.client.fetch_channel(thread_id)
            except Exception:
                return
        if getattr(thread, "archived", False):
            await thread.edit(archived=False)

        if decision == "accepted":
            emoji = "\u2705"
            color = 0x3ED63E
        else:
            emoji = "\u274C"
            color = 0xD93232

        embed = discord.Embed(
            title=f"{emoji} Application {decision.capitalize()}",
            color=color,
        )
        if app_type:
            embed.add_field(name="Type", value=app_type, inline=True)
        if ign:
            embed.add_field(name="IGN", value=ign, inline=True)

        await thread.send(embed=embed)

    # --- Recruiter tracking ---

    async def _process_recruiter_tracking(self, channel, ign, applicant_discord_id=None, app_id=None):
        num = str(app_id) if app_id else channel.name

        text = ""
        async for msg in channel.history(limit=50, oldest_first=True):
            if msg.embeds:
                for embed in msg.embeds:
                    for field in embed.fields:
                        text += f"{field.name}: {field.value}\n"
                    if embed.description:
                        text += f"{embed.description}\n"
            elif not msg.author.bot:
                text += f"{msg.content}\n"

        if not text.strip():
            return

        result = await asyncio.to_thread(parse_application, text)
        if result.get("error"):
            err_ch = self.client.get_channel(ERROR_CHANNEL_ID)
            if err_ch:
                await err_ch.send(
                    f"## Recruiter Tracker - OpenAI Error\n"
                    f"**Ticket:** `{channel.name}`\n"
                    f"```\n{result['error'][:500]}\n```"
                )
            return

        recruiter = result.get("recruiter", "")
        certainty = result.get("certainty", 0.0)
        is_old_member = result.get("is_old_member", False)

        recruiter_format = None
        paid = "NYP"

        # Old member detection
        if not is_old_member and applicant_discord_id:
            uuid_row = await asyncio.to_thread(self._db_check_existing_uuid, applicant_discord_id)
            if uuid_row:
                is_old_member = True

        if not is_old_member and applicant_discord_id:
            applicant = await self._resolve_member(channel, applicant_discord_id)
            if applicant:
                ex_member_role_names = {'Ex-Member', 'Honored Fish', 'Retired Chief'}
                member_role_names = {r.name for r in applicant.roles}
                if ex_member_role_names & member_role_names:
                    is_old_member = True

        if is_old_member:
            recruiter = "old member"
            recruiter_format = {"bold": True, "fontColor": "#BF9000"}
            paid = "NP"

        # Recruiter matching
        if not is_old_member and recruiter:
            from Helpers.classes import Guild as WynnGuild
            try:
                guild_data = await asyncio.to_thread(WynnGuild, "TAq")
                guild_members = guild_data.all_members
                member_names = [m['name'] for m in guild_members]
                member_rank_map = {m['name'].lower(): m['rank'] for m in guild_members}

                db_names = await asyncio.to_thread(self._db_get_all_igns)
                for name in db_names:
                    if name not in member_names:
                        member_names.append(name)

                matched = _fuzzy_match_recruiter(recruiter, member_names)

                if matched is None:
                    ai_result = await asyncio.to_thread(match_recruiter_name, recruiter, member_names)
                    if not ai_result.get("error") and ai_result.get("confidence", 0) >= 0.70:
                        matched = ai_result["matched_name"]

                if matched:
                    recruiter = matched
                    wynn_rank = member_rank_map.get(matched.lower())
                    if wynn_rank == "owner":
                        recruiter_format = {"fontColor": "#A64D79"}
                        paid = "NP"
                    elif wynn_rank == "chief":
                        discord_rank = await asyncio.to_thread(self._get_discord_rank_for_ign, matched)
                        if discord_rank == "Narwhal":
                            recruiter_format = {"fontColor": "#A64D79"}
                        else:
                            recruiter_format = {"fontColor": "#9900FF"}
                        paid = "NP"
                else:
                    paid = "NP"
            except Exception as e:
                err_ch = self.client.get_channel(ERROR_CHANNEL_ID)
                if err_ch:
                    await err_ch.send(
                        f"## Recruiter Tracker - Match Error\n"
                        f"**Ticket:** `{channel.name}` | **Recruiter:** `{recruiter}`\n"
                        f"```\n{str(e)[:500]}\n```"
                    )
        elif not is_old_member and not recruiter:
            paid = "NP"

        # Write to sheet or flag for review
        if certainty >= 0.90 and ign:
            sheet_result = await asyncio.to_thread(
                add_row, num, ign, recruiter,
                paid=paid, recruiter_format=recruiter_format,
            )
            if not sheet_result.get("success"):
                err_ch = self.client.get_channel(ERROR_CHANNEL_ID)
                if err_ch:
                    await err_ch.send(
                        f"## Recruiter Tracker - Sheets Error\n"
                        f"**Ticket:** `{channel.name}` | **IGN:** `{ign}`\n"
                        f"```\n{sheet_result.get('error', 'Unknown')[:500]}\n```"
                    )
        else:
            review_ch = self.client.get_channel(MEMBER_APP_CHANNEL_ID)
            if review_ch:
                await review_ch.send(
                    f"<@&{MANUAL_REVIEW_ROLE_ID}> **Recruiter tracking needs manual review**\n"
                    f"**Ticket:** `{channel.name}` | **Parsed IGN:** `{ign}` | "
                    f"**Parsed Recruiter:** `{recruiter}` | **Certainty:** `{certainty:.0%}`\n"
                    f"Please update the recruiter sheet manually."
                )

    @staticmethod
    def _db_check_existing_uuid(discord_id: int):
        db = DB(); db.connect()
        try:
            db.cursor.execute(
                "SELECT uuid FROM discord_links WHERE discord_id = %s AND uuid IS NOT NULL",
                (discord_id,)
            )
            return db.cursor.fetchone()
        finally:
            db.close()

    @staticmethod
    def _db_get_all_igns() -> list[str]:
        db = DB(); db.connect()
        try:
            db.cursor.execute("SELECT ign FROM discord_links WHERE ign IS NOT NULL AND ign != ''")
            return [row[0] for row in db.cursor.fetchall()]
        finally:
            db.close()

    @staticmethod
    def _get_discord_rank_for_ign(ign: str) -> str | None:
        db = DB(); db.connect()
        try:
            db.cursor.execute(
                "SELECT rank FROM discord_links WHERE LOWER(ign) = LOWER(%s)",
                (ign,)
            )
            row = db.cursor.fetchone()
            return row[0] if row else None
        finally:
            db.close()

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def _fuzzy_match_recruiter(recruiter_input: str, member_names: list[str]) -> str | None:
    lower_input = recruiter_input.lower()

    for name in member_names:
        if name.lower() == lower_input:
            return name

    matches = [name for name in member_names if lower_input in name.lower()]
    if len(matches) == 1:
        return matches[0]

    return None


def setup(client):
    client.add_cog(WebAppCommands(client))
