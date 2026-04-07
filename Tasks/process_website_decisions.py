import asyncio
import base64
import json
from io import BytesIO

import discord
from discord.ext import tasks, commands

from Helpers.logger import log, INFO, ERROR
from Helpers.database import DB
from Helpers.embed_updater import update_web_poll_embed, update_hammerhead_poll_embed
from Helpers.functions import getPlayerDatav3, getPlayerUUID
from Helpers.variables import TAQ_GUILD_ID, INVITED_CATEGORY_NAME


class ProcessWebsiteDecisions(commands.Cog):
    def __init__(self, client):
        self.client = client

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    @tasks.loop(minutes=1)
    async def process_decisions(self):
        """Poll for website-made accept/deny decisions and execute them.
        Guild restriction: operates exclusively on TAQ_GUILD_ID (home guild)."""
        rows = await asyncio.to_thread(self._claim_pending_decisions)
        if not rows:
            return

        for row in rows:
            try:
                await self._process_decision(row)
            except Exception as e:
                app_id = row[0]
                log(ERROR, f"Error processing website decision for app {app_id}: {e}",
                    context="process_website_decisions")

    @staticmethod
    def _claim_pending_decisions():
        """Atomically claim unprocessed website decisions."""
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                """
                UPDATE applications
                SET bot_processed = TRUE
                WHERE id IN (
                    SELECT id FROM applications
                    WHERE bot_processed = FALSE
                      AND status IN ('accepted', 'denied')
                      AND channel_id IS NOT NULL
                      AND channel_id != -1
                    ORDER BY reviewed_at ASC
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id, application_type, discord_id, discord_username,
                          status, answers, channel_id, thread_id, poll_message_id,
                          guild_leave_pending, invite_image, app_number
                """
            )
            rows = db.cursor.fetchall()
            db.connection.commit()
            return rows
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _process_decision(self, row):
        (app_id, app_type, discord_id, discord_username,
         status, answers, channel_id, thread_id, poll_message_id,
         guild_leave_pending, invite_image, app_number) = row

        if isinstance(answers, str):
            answers = json.loads(answers)

        # Hammerhead applications: just update embed color/status
        if app_type == "hammerhead":
            await self._process_hammerhead_decision(
                app_id, status, answers, channel_id, thread_id, poll_message_id
            )
            return

        ign = (answers.get("ign") or "").strip()

        # Resolve guild and channel
        guild = self.client.get_guild(TAQ_GUILD_ID)
        if not guild:
            log(ERROR, f"Could not find guild {TAQ_GUILD_ID}", context="process_website_decisions")
            return

        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.client.fetch_channel(channel_id)
            except Exception:
                log(ERROR, f"Could not find channel {channel_id} for app {app_id}",
                    context="process_website_decisions")
                return

        # Resolve applicant
        applicant = guild.get_member(int(discord_id))
        if applicant is None:
            try:
                applicant = await guild.fetch_member(int(discord_id))
            except Exception:
                applicant = None

        # Use app_number (from app_counter) for channel naming, fall back to app_id
        display_number = app_number if app_number is not None else app_id

        if status == "accepted":
            if app_type == "guild":
                await self._accept_guild(app_id, channel, applicant, discord_id,
                                         discord_username, ign, thread_id, invite_image, answers,
                                         display_number=display_number)
            else:
                await self._accept_community(app_id, channel, applicant, discord_id,
                                             discord_username, ign, thread_id,
                                             display_number=display_number)
        elif status == "denied":
            await self._deny(app_id, app_type, channel, applicant, discord_id,
                             discord_username, ign, thread_id,
                             display_number=display_number)

    # ------------------------------------------------------------------
    # Accept — Guild
    # ------------------------------------------------------------------

    async def _accept_guild(self, app_id, channel, applicant, discord_id,
                            discord_username, ign, thread_id, invite_image, answers=None,
                            display_number=None):
        mention = applicant.mention if applicant else f"<@{discord_id}>"
        partytort = discord.utils.get(channel.guild.emojis, name="partytort")
        party_emoji = str(partytort) if partytort else "\U0001F389"

        # Look up player UUID and guild status
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

        # Build the invite image attachment if provided
        invite_file = None
        if invite_image:
            invite_file = self._decode_invite_image(invite_image, discord_username)

        # Auto-link in discord_links
        link_id = applicant.id if applicant else int(discord_id)

        if in_taq:
            # Player is already in The Aquarium — accept and register immediately
            msg_text = (
                f"Hey {mention},\n\n"
                f"Congratulations, your application to join **The Aquarium** has been "
                f"**accepted**! {party_emoji}\n\n"
                f"You're already in the guild — your Discord roles are being set up now.\n\n"
                f"Best Regards,\n"
                f"The Aquarium Applications Team"
            )

            if invite_file:
                await channel.send(msg_text, file=invite_file)
            else:
                await channel.send(msg_text)

            if ign and uuid:
                await asyncio.to_thread(self._link_discord, link_id, ign, uuid, channel.id, linked=False)

            # Trigger immediate registration (same pattern as app_commands.py)
            cog = self.client.get_cog("UpdateMemberData")
            if cog and uuid:
                try:
                    await cog._auto_register_joined_member(uuid, ign)
                except Exception as e:
                    log(ERROR, f"Immediate registration failed for {ign}: {e}",
                        context="process_website_decisions")

            await update_web_poll_embed(self.client, channel.id,
                                        ":orange_circle: Registered", 0xFFE019)
            await asyncio.to_thread(self._db_set_guild_leave, app_id, False)

        elif in_guild:
            # Player is in another guild — pending leave
            msg_text = (
                f"Hey {mention},\n\n"
                f"Congratulations, your application to join **The Aquarium** has been "
                f"**accepted**! {party_emoji}\n\n"
                f"However, you're currently in **{current_guild_name}**. "
                f"Please leave your current guild so we can invite you. "
                f"Let us know when you've left!\n\n"
                f"Best Regards,\n"
                f"The Aquarium Applications Team"
            )

            if invite_file:
                await channel.send(msg_text, file=invite_file)
            else:
                await channel.send(msg_text)

            if ign and uuid:
                await asyncio.to_thread(self._link_discord, link_id, ign, uuid, channel.id, linked=False)

            await update_web_poll_embed(self.client, channel.id,
                                        ":yellow_circle: Accepted - Pending Leave", 0xFFE019)
            await asyncio.to_thread(self._db_set_guild_leave, app_id, True)

        else:
            # Player is not in any guild — send invite instructions
            msg_text = (
                f"Hey {mention},\n\n"
                f"Congratulations, your application to join **The Aquarium** has been "
                f"**accepted**! {party_emoji}\n\n"
                f"To join, just type `/gu join TAq` next time you're online. "
                f"Once you join the guild in-game, you will be given your Discord roles.\n\n"
                f"Best Regards,\n"
                f"The Aquarium Applications Team"
            )

            if invite_file:
                await channel.send(msg_text, file=invite_file)
            else:
                await channel.send(msg_text)

            if ign and uuid:
                await asyncio.to_thread(self._link_discord, link_id, ign, uuid, channel.id, linked=False)

            guild_obj = self.client.get_guild(channel.guild.id) or channel.guild
            invited_cat = discord.utils.get(guild_obj.categories, name=INVITED_CATEGORY_NAME)
            if invited_cat:
                try:
                    await channel.edit(category=invited_cat)
                except discord.Forbidden:
                    pass
            try:
                await channel.edit(name=f"accepted-{display_number}-{ign}")
            except discord.Forbidden:
                pass
            await update_web_poll_embed(self.client, channel.id,
                                        ":green_circle: Invited", 0x3ED63E)
            await asyncio.to_thread(self._db_set_guild_leave, app_id, False)

        # Update exec thread
        await self._update_exec_thread(thread_id, "accepted", "Guild Member", ign)

        # Recruiter tracking (delegate to AppCommands cog if available)
        reference = (answers.get("reference") or "").strip() if isinstance(answers, dict) else ""
        app_cog = self.client.get_cog("WebAppCommands")
        if app_cog and hasattr(app_cog, "_process_recruiter_tracking"):
            try:
                await app_cog._process_recruiter_tracking(channel, ign, int(discord_id), app_id, reference=reference)
            except Exception as e:
                log(ERROR, f"Recruiter tracking failed for app {app_id}: {e}",
                    context="process_website_decisions")

        log(INFO, f"Processed website accept for guild app {app_id} (IGN: {ign})",
            context="process_website_decisions")

    # ------------------------------------------------------------------
    # Accept — Community
    # ------------------------------------------------------------------

    async def _accept_community(self, app_id, channel, applicant, discord_id,
                                discord_username, ign, thread_id,
                                display_number=None):
        mention = applicant.mention if applicant else f"<@{discord_id}>"

        await channel.send(
            f"Hey {mention},\n\n"
            f"Congratulations, your application to become a **Community Member** of "
            f"The Aquarium has been **accepted**! \U0001F389\n\n"
            f"Welcome to the community!\n\n"
            f"Best Regards,\n"
            f"The Aquarium Applications Team"
        )

        # Auto-link with linked=True (community members are immediately linked)
        link_id = applicant.id if applicant else int(discord_id)
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
        if applicant:
            guild_obj = self.client.get_guild(channel.guild.id) or channel.guild
            community_role = discord.utils.get(guild_obj.roles, name="Tortoise - Community")
            if community_role:
                try:
                    await applicant.add_roles(community_role,
                                              reason="Community member application accepted (website)")
                except Exception as e:
                    log(ERROR, f"Failed to assign community role for app {app_id}: {e}",
                        context="process_website_decisions")

        # Rename channel
        try:
            await channel.edit(name=f"c-accepted-{display_number}-{ign}")
        except discord.Forbidden:
            pass

        # Update poll embed
        await update_web_poll_embed(self.client, channel.id,
                                    ":orange_circle: Accepted", 0xFFE019)

        # Update exec thread
        await self._update_exec_thread(thread_id, "accepted", "Community Member", ign)

        log(INFO, f"Processed website accept for community app {app_id} (IGN: {ign})",
            context="process_website_decisions")

    # ------------------------------------------------------------------
    # Deny
    # ------------------------------------------------------------------

    async def _deny(self, app_id, app_type, channel, applicant, discord_id,
                    discord_username, ign, thread_id,
                    display_number=None):
        mention = applicant.mention if applicant else f"<@{discord_id}>"

        await channel.send(
            f"Hi {mention},\n\n"
            f"We regret to inform you that your application to join our guild did not "
            f"meet our current standards. We appreciate your interest and thank you "
            f"for considering us.\n\n"
            f"Best Regards,\n"
            f"The Aquarium Applications Team"
        )

        # Rename channel
        new_name = (f"denied-{display_number}-{ign}" if app_type == "guild"
                    else f"c-denied-{display_number}-{ign}")
        try:
            await channel.edit(name=new_name)
        except discord.Forbidden:
            pass

        # Update poll embed
        await update_web_poll_embed(self.client, channel.id,
                                    ":orange_circle: Denied", 0xFFE019)

        # Update exec thread
        await self._update_exec_thread(thread_id, "denied")

        log(INFO, f"Processed website deny for app {app_id}",
            context="process_website_decisions")

    # ------------------------------------------------------------------
    # Hammerhead decision — just update embed + thread notification
    # ------------------------------------------------------------------

    async def _process_hammerhead_decision(self, app_id, status, answers, channel_id, thread_id, poll_message_id):
        ign_rank = (answers.get("hh_ign_rank") or "").strip()
        ign = ign_rank.split(",")[0].strip() if ign_rank else "Unknown"

        if status == "accepted":
            new_status = ":green_circle: Accepted"
            colour = 0x3ED63E
        else:
            new_status = ":red_circle: Denied"
            colour = 0xD93232

        # Update the poll embed on the last message
        await update_hammerhead_poll_embed(
            self.client, app_id, poll_message_id, new_status, colour
        )

        # Post decision notification in thread
        await self._update_exec_thread(thread_id, status, "Hammerhead", ign)

        log(INFO, f"Processed hammerhead {status} for app {app_id} (IGN: {ign})",
            context="process_website_decisions")

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_invite_image(invite_image_b64: str, username: str):
        """Decode a base64 data URL into a discord.File."""
        try:
            # Strip data URL prefix if present (e.g., "data:image/png;base64,...")
            if "," in invite_image_b64:
                header, data = invite_image_b64.split(",", 1)
            else:
                data = invite_image_b64

            image_bytes = base64.b64decode(data)
            buf = BytesIO(image_bytes)

            # Determine extension from header
            ext = "png"
            if "image/jpeg" in invite_image_b64 or "image/jpg" in invite_image_b64:
                ext = "jpg"
            elif "image/gif" in invite_image_b64:
                ext = "gif"
            elif "image/webp" in invite_image_b64:
                ext = "webp"

            return discord.File(buf, filename=f"invite-{username}.{ext}")
        except Exception:
            return None

    @staticmethod
    def _link_discord(discord_id, ign, uuid, app_channel, linked=False):
        db = DB()
        db.connect()
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

    @staticmethod
    def _db_set_guild_leave(app_id, guild_leave_pending):
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                "UPDATE applications SET guild_leave_pending = %s WHERE id = %s",
                (guild_leave_pending, app_id)
            )
            db.connection.commit()
        finally:
            db.close()

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
            title=f"{emoji} Application {decision.capitalize()} (via website)",
            color=color,
        )
        if app_type:
            embed.add_field(name="Type", value=app_type, inline=True)
        if ign:
            embed.add_field(name="IGN", value=ign, inline=True)

        await thread.send(embed=embed)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @process_decisions.before_loop
    async def before_process_decisions(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.process_decisions.is_running():
            self.process_decisions.start()


def setup(client):
    client.add_cog(ProcessWebsiteDecisions(client))