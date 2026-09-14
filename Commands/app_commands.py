import asyncio
import json
from datetime import datetime, timezone

import discord
from discord import SlashCommandGroup, ApplicationContext
from discord.ext import commands

from Helpers.classes import BasicPlayerStats
from Helpers.app_transcript import (
    post_transcript,
    stamp_transcribed,
    delete_transcribed_channel,
    TranscriptError,
)
from Helpers.database import DB
from Helpers.embed_updater import update_web_poll_embed
from Helpers.functions import generate_applicant_info, getPlayerUUID, getPlayerDatav3
from Helpers.links import LinkConflictError, assert_row_linkable, assert_uuid_free
from Helpers.openai_helper import parse_recruiter_source
from Helpers.recruiting import record_pending_recruit, get_recruiter_stats, resolve_recruiter
from Helpers.views import RecruiterReviewView
from Helpers.variables import (
    HOME_GUILD_IDS,
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
        guild_ids=HOME_GUILD_IDS,
        default_member_permissions=discord.Permissions(manage_roles=True)
    )

    recruiter_group = SlashCommandGroup(
        'recruiter', 'HR: Recruiter payout tracking',
        guild_ids=HOME_GUILD_IDS,
        default_member_permissions=discord.Permissions(manage_roles=True)
    )

    @recruiter_group.command(name='stats', description="HR: Show a recruiter's credited recruits and unpaid payout")
    async def recruiter_stats(
        self, ctx: ApplicationContext,
        ign: discord.Option(str, description='Recruiter IGN')
    ):
        await ctx.defer(ephemeral=True)
        stats = await asyncio.to_thread(get_recruiter_stats, ign)
        if stats["total_recruits"] == 0:
            await ctx.followup.send(f"No credited recruits found for **{ign}**.", ephemeral=True)
            return
        paid_le = stats["total_le"] - stats["unpaid_le"]
        recruits_line = f"Credited recruits: **{stats['total_recruits']}**"
        if stats["excluded_recruits"]:
            recruits_line += f" ({stats['excluded_recruits']} as staff, not eligible for payout)"
        await ctx.followup.send(
            f"**{ign}**\n"
            f"{recruits_line}\n"
            f"Unpaid: **{stats['unpaid_le']} LE**\n"
            f"Paid out (all-time): **{paid_le} LE**",
            ephemeral=True,
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
                              channel_id, guild_leave_pending, answers, discord_username, app_number
                       FROM applications WHERE channel_id = %s""",
                    (source_id,)
                )
                row = db.cursor.fetchone()
                if not row:
                    db.cursor.execute(
                        """SELECT id, application_type, discord_id, thread_id, status,
                                  channel_id, guild_leave_pending, answers, discord_username, app_number
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

        app_id, app_type, discord_id, thread_id, status, channel_id, guild_leave_pending, answers, discord_username, app_number = row

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
            "app_number": app_number,
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
            link_conflict = None
            if ign and uuid:
                try:
                    await asyncio.to_thread(self._link_discord, link_id, ign, uuid, channel.id, linked=False)
                except LinkConflictError as e:
                    link_conflict = e

            # Trigger auto-registration directly
            from Tasks.update_member_data import UpdateMemberData
            cog = self.client.get_cog("UpdateMemberData")
            if cog and uuid and not link_conflict:
                try:
                    await cog._auto_register_joined_member(uuid, ign)
                except Exception as e:
                    from Helpers.logger import log, ERROR as LOG_ERROR
                    log(LOG_ERROR, f"Immediate registration failed for {ign}: {e}", context="app_commands")

            if link_conflict:
                await update_web_poll_embed(self.client, channel.id, ":red_circle: Link Conflict", 0xE33232)
                feedback = f"Application accepted, but NOT registered: {link_conflict.user_message()}"
            else:
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
                try:
                    await asyncio.to_thread(self._link_discord, link_id, ign, uuid, channel.id, linked=False)
                except LinkConflictError as e:
                    await ctx.followup.send(e.user_message(), ephemeral=True)

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
                try:
                    await asyncio.to_thread(self._link_discord, link_id, ign, uuid, channel.id, linked=False)
                except LinkConflictError as e:
                    await ctx.followup.send(e.user_message(), ephemeral=True)

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
            await update_web_poll_embed(self.client, channel.id, ":green_circle: Invited", 0x3ED63E)
            feedback = f"Application accepted. IGN: `{ign}`. User will be auto-registered when they join."

        # Update DB (guild_leave_pending only if in another guild, not TAq)
        await asyncio.to_thread(
            self._db_accept, app_id, now, in_guild and not in_taq
        )

        # Update exec thread
        await self._update_exec_thread(app["thread_id"], "accepted", "Guild Member", ign)

        # Recruiter tracking
        reference = (app["answers"].get("reference") or "").strip()
        await self._process_recruiter_tracking(channel, ign, int(app["discord_id"]), app["id"], reference=reference)

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
                try:
                    await asyncio.to_thread(self._link_discord, link_id, ign, uuid, channel.id, linked=True)
                except LinkConflictError as e:
                    await ctx.followup.send(e.user_message(), ephemeral=True)
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

        if app["application_type"] == "guild":
            await channel.send(
                f"Hi {mention},\n\n"
                f"We regret to inform you that your application to join our guild did not "
                f"meet our current standards. We appreciate your interest and thank you "
                f"for considering us.\n\n"
                f"Best Regards,\n"
                f"The Aquarium Applications Team"
            )
        else:
            await channel.send(
                f"Hi {mention},\n\n"
                f"We regret to inform you that your application to become a "
                f"**Community Member** of The Aquarium has been denied. "
                f"We appreciate your interest and thank you for considering us.\n\n"
                f"Best Regards,\n"
                f"The Aquarium Applications Team"
            )

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

        # Revoke applicant's access to the channel
        applicant = await self._resolve_member(channel, int(app["discord_id"]))
        if applicant:
            try:
                await channel.set_permissions(applicant, overwrite=None)
            except discord.Forbidden:
                pass

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

    # --- /app rescind ---

    @app_group.command(name='rescind', description='HR: Rescind an accepted application and close the ticket')
    async def rescind(self, ctx: ApplicationContext):
        await ctx.defer(ephemeral=True)

        result = await self._lookup_web_app(ctx, require_status='accepted')
        if result is None:
            return

        channel, app = result

        # Check if already closed
        guild = self.client.get_guild(channel.guild.id) or channel.guild
        closed_cat = discord.utils.get(guild.categories, name=CLOSED_CATEGORY_NAME)
        if closed_cat and getattr(channel, 'category', None) == closed_cat:
            await ctx.followup.send("This application is already closed.", ephemeral=True)
            return

        applicant = await self._resolve_member(channel, int(app["discord_id"]))

        # Log rescind message in the channel
        await channel.send("This application acceptance has been rescinded.")

        # Update DB status to denied
        link_conflict = await asyncio.to_thread(self._db_rescind, app["id"])
        if link_conflict:
            await ctx.followup.send(
                f"Rescinded, but the member row was not re-linked: {link_conflict.user_message()}",
                ephemeral=True,
            )

        # Update poll embed
        await update_web_poll_embed(self.client, channel.id,
                                    ":orange_circle: Rescinded", 0xFFE019)

        # Revoke applicant's access
        if applicant:
            try:
                await channel.set_permissions(applicant, overwrite=None)
            except discord.Forbidden:
                pass

        # Move to Closed Applications category
        if closed_cat:
            try:
                await channel.edit(category=closed_cat)
            except discord.Forbidden:
                await ctx.followup.send(
                    "Could not move channel to Closed Applications (missing permissions).",
                    ephemeral=True,
                )
                return

        # Update exec thread
        await self._update_exec_thread(app["thread_id"], "rescinded")

        await ctx.followup.send("Application rescinded and closed.", ephemeral=True)

    # --- /app transcribe ---

    @app_group.command(name='transcribe', description='HR: Transcribe this application to the archive channel')
    async def transcribe(self, ctx: ApplicationContext):
        await ctx.defer(ephemeral=True)

        result = await self._lookup_web_app(ctx)
        if result is None:
            return

        channel, app = result
        guild = self.client.get_guild(channel.guild.id) or channel.guild

        try:
            await post_transcript(self.client, channel, app)
        except TranscriptError as e:
            await ctx.followup.send(str(e), ephemeral=True)
            return

        await asyncio.to_thread(stamp_transcribed, app["id"])

        archive_chan = discord.utils.get(guild.text_channels, name=APP_ARCHIVE_CHANNEL_NAME)
        dest = archive_chan.mention if archive_chan else f"#{APP_ARCHIVE_CHANNEL_NAME}"
        # Confirm before deleting — this command is often run inside the channel being removed.
        await ctx.followup.send(f"Transcript saved to {dest}. Deleting this channel…", ephemeral=True)

        try:
            await delete_transcribed_channel(self.client, app["id"], app["channel_id"])
        except Exception as e:
            try:
                await ctx.followup.send(
                    f"⚠️ Transcript is saved, but the channel couldn't be deleted: {e}",
                    ephemeral=True,
                )
            except Exception:
                pass

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
    def _db_rescind(app_id):
        db = DB(); db.connect()
        try:
            db.cursor.execute(
                """UPDATE applications
                   SET status = 'denied', guild_leave_pending = FALSE
                   WHERE id = %s
                   RETURNING discord_id""",
                (app_id,)
            )
            row = db.cursor.fetchone()
            conflict = None
            if row:
                try:
                    assert_row_linkable(db.cursor, int(row[0]))
                    db.cursor.execute(
                        """UPDATE discord_links SET linked = TRUE
                           WHERE discord_id = %s AND linked = FALSE""",
                        (int(row[0]),)
                    )
                except LinkConflictError as e:
                    # Still record the denial; only the relink is blocked.
                    conflict = e
            db.connection.commit()
            return conflict
        finally:
            db.close()

    @staticmethod
    def _link_discord(discord_id, ign, uuid, app_channel, linked=False):
        db = DB(); db.connect()
        try:
            # Guard even the linked=False writes: a row seeded with another
            # member's uuid gets flipped to linked later (rescind/auto-register).
            assert_uuid_free(db.cursor, uuid, discord_id)
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
        elif decision == "rescinded":
            emoji = "\u21A9\uFE0F"
            color = 0xFF8C00
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

    # Non-person sources the AI parser normalizes to (see _PARSE_INSTRUCTIONS in
    # Helpers/openai_helper.py). Nobody to credit, so these skip manual review.
    _GENERIC_SOURCES = {"wynncord", "server list", "forums", "party finder"}

    async def _process_recruiter_tracking(self, channel, ign, applicant_discord_id=None, app_id=None, reference=None):
        reference_text = (reference or "").strip()
        if not reference_text:
            recruiter = ""
            certainty = 0.0
        else:
            result = await asyncio.to_thread(parse_recruiter_source, reference_text)
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

        # Old/returning members don't count as a fresh recruit. Based on a prior
        # accepted guild application, not discord_links (_accept_guild already
        # linked this applicant moments ago, so that'd flag everyone), plus roles
        # for members who predate the website application system.
        is_old_member = False
        if applicant_discord_id and app_id:
            is_old_member = await asyncio.to_thread(
                self._db_had_prior_accepted_guild_app, applicant_discord_id, app_id
            )
        if not is_old_member and applicant_discord_id:
            applicant = await self._resolve_member(channel, applicant_discord_id)
            if applicant:
                ex_member_role_names = {'Ex-Member', 'Honored Fish', 'Retired Chief'}
                if ex_member_role_names & {r.name for r in applicant.roles}:
                    is_old_member = True
        if is_old_member:
            return

        matched, excluded = None, False
        if recruiter:
            try:
                matched, excluded = await resolve_recruiter(recruiter, use_ai_fallback=True)
            except Exception as e:
                err_ch = self.client.get_channel(ERROR_CHANNEL_ID)
                if err_ch:
                    await err_ch.send(
                        f"## Recruiter Tracker - Match Error\n"
                        f"**Ticket:** `{channel.name}` | **Recruiter:** `{recruiter}`\n"
                        f"```\n{str(e)[:500]}\n```"
                    )

        # Below 0.90 confidence, or a named-but-unresolved recruiter, needs a human.
        # A recognized generic source (or no reference at all) needs no action.
        unresolved_named_recruiter = bool(recruiter) and not matched and recruiter.lower() not in self._GENERIC_SOURCES
        needs_review = certainty < 0.90 or unresolved_named_recruiter

        if needs_review:
            review_ch = self.client.get_channel(MEMBER_APP_CHANNEL_ID)
            if review_ch:
                embed = discord.Embed(
                    title="Recruiter tracking needs review",
                    description=f"Ticket **{channel.name}**",
                    color=0xEBDB34,
                )
                embed.add_field(name="IGN", value=ign or "?", inline=True)
                embed.add_field(name="Parsed recruiter", value=recruiter or "*none*", inline=True)
                embed.add_field(name="Certainty", value=f"{certainty:.0%}", inline=True)
                embed.set_footer(text=f"{app_id}|{applicant_discord_id or ''}|{ign}")
                await review_ch.send(
                    f"<@&{MANUAL_REVIEW_ROLE_ID}>", embed=embed, view=RecruiterReviewView(),
                )
            return

        if matched and ign and app_id:
            await asyncio.to_thread(
                record_pending_recruit, app_id, matched, ign, applicant_discord_id, excluded,
            )

    @staticmethod
    def _db_had_prior_accepted_guild_app(discord_id: int, current_app_id: int) -> bool:
        db = DB(); db.connect()
        try:
            db.cursor.execute(
                """SELECT 1 FROM applications
                   WHERE discord_id = %s AND application_type = 'guild'
                     AND status = 'accepted' AND id != %s
                   LIMIT 1""",
                (str(discord_id), current_app_id)
            )
            return db.cursor.fetchone() is not None
        finally:
            db.close()

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(WebAppCommands(client))
