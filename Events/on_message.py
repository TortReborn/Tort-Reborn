import asyncio
import json
import re
from io import BytesIO

import discord
from discord.ext import commands

from Helpers.classes import BasicPlayerStats
from Helpers.database import DB, get_blacklist
from Helpers.functions import generate_applicant_info, getNameFromUUID
from Helpers.embed_updater import update_poll_embed
from Helpers.openai_helper import (
    detect_application, detect_rejoin_intent, extract_ign,
    validate_application_completeness, validate_exmember_completeness,
)
from Helpers.variables import application_manager_role_id, APPLICATION_FORMAT_MESSAGE


class OnMessage(commands.Cog):
    def __init__(self, client):
        self.client = client

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.client.user:
            return

        # --- Special channel format enforcement (kick appeals) ---
        if message.channel.id == 729163031321509938:
            if 'how long' in message.content.lower() and 'why' in message.content.lower() and 'kick' in message.content.lower():
                pass
            else:
                await message.delete()
                reply = await message.channel.send(':no_entry: Please use the format in pinned messages.')
                await asyncio.sleep(5)
                await reply.delete()
            return

        # --- Application ticket detection ---
        if not (
            message.channel.category
            and message.channel.category.name == 'Guild Applications'
            and message.channel.name.startswith('ticket-')
        ):
            return

        # Skip all bot messages
        if message.author.bot:
            return

        # Look up the application record
        db = DB()
        db.connect()
        db.cursor.execute(
            "SELECT applicant_discord_id, app_type, thread_id, app_complete, app_message_id FROM new_app WHERE channel = %s",
            (message.channel.id,)
        )
        row = db.cursor.fetchone()
        db.close()

        if not row:
            return

        stored_discord_id, app_type, thread_id, app_complete, app_message_id = row

        # If we don't have the applicant_discord_id yet, try to parse from Ticket Tool message
        if stored_discord_id is None:
            stored_discord_id = await self._find_ticket_opener(message.channel)
            if stored_discord_id:
                db = DB(); db.connect()
                db.cursor.execute(
                    "UPDATE new_app SET applicant_discord_id = %s WHERE channel = %s",
                    (stored_discord_id, message.channel.id)
                )
                db.connection.commit()
                db.close()

        # Only process messages from the ticket opener
        if message.author.id != stored_discord_id:
            return

        # If the application is already complete and forwarded, nothing to do
        if app_type is not None and app_complete:
            return

        # --- Determine ex-member status (needed for both first-time and revalidation) ---
        is_ex_member = False
        mc_name = ""

        db = DB(); db.connect()
        db.cursor.execute(
            "SELECT uuid FROM discord_links WHERE discord_id = %s",
            (stored_discord_id,)
        )
        link_row = db.cursor.fetchone()
        db.close()

        if link_row and link_row[0]:
            # Known ex-member via discord_links - resolve IGN from UUID
            is_ex_member = True
            ex_member_uuid = str(link_row[0])
            resolved = await asyncio.to_thread(getNameFromUUID, ex_member_uuid)
            mc_name = resolved[0] if resolved else ""
        else:
            # Fallback: check Discord roles for ex-member indicators
            ex_member_role_names = {'Ex-Member', 'Honored Fish', 'Retired Chief'}
            member_role_names = {r.name for r in message.author.roles}
            if ex_member_role_names & member_role_names:
                is_ex_member = True

        # If app_type detected but NOT complete, this is a follow-up message — revalidate
        if app_type is not None and not app_complete:
            await self._handle_revalidation(message, app_type, thread_id, is_ex_member, mc_name)
            return

        # =====================================================================
        # First-time detection (app_type is None)
        # =====================================================================

        if is_ex_member:
            # Lenient rejoin intent detection
            detection = await asyncio.to_thread(detect_rejoin_intent, message.content)
            if detection.get("error"):
                print(f"[on_message] Rejoin detection error for {message.channel.name}: {detection['error']}")
                return
            if not detection["is_application"] or detection["confidence"] < 0.4:
                return

            detected_type = detection["app_type"]

            # IGN fallback if UUID resolution failed or no UUID available
            if not mc_name:
                mc_name = await self._extract_ign_from_text(message.content)
                if not mc_name:
                    ign_result = await asyncio.to_thread(extract_ign, message.content)
                    if not ign_result.get("error") and ign_result.get("confidence", 0) >= 0.5:
                        mc_name = ign_result["ign"]

            # Validate completeness for guild_member apps
            if detected_type == "guild_member":
                validation = await asyncio.to_thread(validate_exmember_completeness, message.content)
                if not validation.get("error") and not validation["complete"]:
                    await self._save_incomplete(message, detected_type, mc_name)
                    await self._notify_incomplete(message, validation["missing_fields"], is_ex_member=True)
                    return

            await self._process_detected_application(message, detected_type, mc_name, thread_id)
            return

        # --- Regular applicant path ---
        detection = await asyncio.to_thread(detect_application, message.content)

        if detection.get("error"):
            print(f"[on_message] AI detection error for {message.channel.name}: {detection['error']}")
            return

        if not detection["is_application"] or detection["confidence"] < 0.7:
            return

        detected_type = detection["app_type"]  # "guild_member" or "community_member"

        # Extract IGN from the application text
        mc_name = await self._extract_ign_from_text(message.content)
        if not mc_name:
            ign_result = await asyncio.to_thread(extract_ign, message.content)
            if not ign_result.get("error") and ign_result.get("confidence", 0) >= 0.7:
                mc_name = ign_result["ign"]

        # Validate completeness for guild_member apps (community_member stays lax)
        if detected_type == "guild_member":
            validation = await asyncio.to_thread(validate_application_completeness, message.content)
            if not validation.get("error") and not validation["complete"]:
                await self._save_incomplete(message, detected_type, mc_name)
                await self._notify_incomplete(message, validation["missing_fields"], is_ex_member=False)
                return

        await self._process_detected_application(message, detected_type, mc_name, thread_id)

    # =====================================================================
    # Completeness helpers
    # =====================================================================

    async def _save_incomplete(self, message, detected_type, mc_name):
        """Save app_type and message ID to DB without marking as complete."""
        db = DB(); db.connect()
        db.cursor.execute(
            "UPDATE new_app SET app_type = %s, ign = %s, app_message_id = %s WHERE channel = %s",
            (detected_type, mc_name or None, message.id, message.channel.id)
        )
        db.connection.commit()
        db.close()

    async def _notify_incomplete(self, message, missing_fields, is_ex_member):
        """Send the incomplete application notification to the ticket channel."""
        missing_list = "\n".join(f"- {field}" for field in missing_fields)

        await message.channel.send(
            f"Hey {message.author.mention},\n\n"
            f"Thanks for your interest in The Aquarium! It looks like your application "
            f"is missing some required information:\n\n"
            f"**Missing fields:**\n{missing_list}\n\n"
            f"Please fill out all required fields. You can either **edit your existing message** "
            f"or **send a new message** with the complete application.\n\n"
            f"{APPLICATION_FORMAT_MESSAGE}\n"
            f"\u200b \u200b \u200b \u200b \u200b \u200b \u200b \u200b \u200b \u200b \u200b \u200b "
            f"\u200b \u200b \u200b \u200b \u200b \u200b \u200b \u200b \u200b \u200b \u200b \u200b "
            f"\u200b \u200b \u200b \u200b \u200b  \u200b \u200b \u200b \u200b \u200b \u200b \u200b "
            f"\u200b\u200b \u200b \u200b \u200b  \u200b \u200b \u200b  \u200b \u200b \u200b \u200b "
            f"(Copy and fill out in your application ticket)"
        )

    async def _handle_revalidation(self, message, app_type, thread_id, is_ex_member, mc_name):
        """Re-validate when applicant sends a new message after an incomplete detection."""
        if app_type == "community_member":
            return  # Community member apps don't need strict validation

        validator = validate_exmember_completeness if is_ex_member else validate_application_completeness
        validation = await asyncio.to_thread(validator, message.content)

        if validation.get("error"):
            print(f"[on_message] Revalidation error for {message.channel.name}: {validation['error']}")
            return

        # Try to extract IGN from the new message if we don't have one
        if not mc_name:
            mc_name = await self._extract_ign_from_text(message.content)
            if not mc_name:
                ign_result = await asyncio.to_thread(extract_ign, message.content)
                conf_threshold = 0.5 if is_ex_member else 0.7
                if not ign_result.get("error") and ign_result.get("confidence", 0) >= conf_threshold:
                    mc_name = ign_result["ign"]

        # Update message ID and IGN
        db = DB(); db.connect()
        db.cursor.execute(
            "UPDATE new_app SET app_message_id = %s, ign = %s WHERE channel = %s",
            (message.id, mc_name or None, message.channel.id)
        )
        db.connection.commit()
        db.close()

        if not validation["complete"]:
            # Still incomplete — don't spam the format message again
            return

        # Complete! Forward to app managers
        await self._process_detected_application(message, app_type, mc_name, thread_id)

    # =====================================================================
    # IGN extraction
    # =====================================================================

    async def _extract_ign_from_text(self, text) -> str:
        """Try to extract an IGN from a wynncraft stats link in the text."""
        stats_match = re.search(
            r"wynncraft\.com/stats/player[/\s]*"
            r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[\w]+)",
            text
        )
        if stats_match:
            captured = stats_match.group(1)
            if re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', captured):
                resolved = await asyncio.to_thread(getNameFromUUID, captured)
                if resolved:
                    return resolved[0]
            else:
                return captured
        return ""

    # =====================================================================
    # Application forwarding (shared by on_message, on_message_edit, /receive)
    # =====================================================================

    async def _process_detected_application(self, message, detected_type, mc_name, thread_id):
        """Shared processing after an application is detected and validated as complete."""
        # Atomically mark as complete — prevents double-processing from edits + new messages
        db = DB(); db.connect()
        db.cursor.execute(
            "UPDATE new_app SET app_complete = TRUE WHERE channel = %s AND app_complete = FALSE RETURNING channel",
            (message.channel.id,)
        )
        updated = db.cursor.fetchone()
        db.connection.commit()
        db.close()
        if not updated:
            return  # Another handler already processed this

        # Generate player stats image
        pdata = None
        blacklist_warning = ""
        if mc_name:
            pdata = await asyncio.to_thread(BasicPlayerStats, mc_name)
            if pdata.error:
                pdata = None
            else:
                # Blacklist check
                blacklist = get_blacklist()
                for player in blacklist:
                    if pdata.UUID == player['UUID']:
                        blacklist_warning = (
                            f':no_entry: Player present on blacklist!\n'
                            f'**Name:** {pdata.username}\n**UUID:** {pdata.UUID}'
                        )

        # Update DB with the detected type, IGN, and message ID
        db = DB(); db.connect()
        db.cursor.execute(
            "UPDATE new_app SET app_type = %s, ign = %s, app_message_id = %s WHERE channel = %s",
            (detected_type, mc_name or None, message.id, message.channel.id)
        )
        db.connection.commit()
        db.close()

        # Update poll embed to "Received" and rename channel
        await update_poll_embed(self.client, message.channel.id, ":green_circle: Received", 0x3ED63E)
        num_match = re.search(r'(\d+)', message.channel.name)
        ticket_num = num_match.group(1) if num_match else message.channel.name.split("-", 1)[-1]
        try:
            await message.channel.edit(name=f"received-{ticket_num}")
        except Exception:
            pass

        # Re-fetch thread_id (may have been stored by on_guild_channel_create after our initial query)
        if not thread_id:
            db = DB(); db.connect()
            db.cursor.execute(
                "SELECT thread_id FROM new_app WHERE channel = %s",
                (message.channel.id,)
            )
            row = db.cursor.fetchone()
            db.close()
            if row:
                thread_id = row[0]

        # Post to the exec thread
        if thread_id:
            thread = self.client.get_channel(thread_id)
            if thread is None:
                try:
                    thread = await self.client.fetch_channel(thread_id)
                except Exception:
                    thread = None

            if thread:
                if getattr(thread, "archived", False):
                    await thread.edit(archived=False)

                type_label = "Guild Member" if detected_type == "guild_member" else "Community Member"

                # Build the stats embed
                embed_title = f"Application {ticket_num}"
                if pdata:
                    embed_title += f" ({pdata.username})"

                embed = discord.Embed(
                    title=embed_title,
                    description=blacklist_warning,
                    colour=0x3ed63e,
                )
                embed.add_field(name="Channel", value=f":link: <#{message.channel.id}>", inline=True)
                embed.add_field(name="Type", value=type_label, inline=True)

                # Send with stats image if available
                if pdata:
                    img = generate_applicant_info(pdata)
                    with BytesIO() as file:
                        img.save(file, format="PNG")
                        file.seek(0)
                        player_info = discord.File(
                            file,
                            filename=f"{ticket_num}-{pdata.UUID}.png"
                        )
                        embed.set_image(url=f"attachment://{ticket_num}-{pdata.UUID}.png")
                        await thread.send(
                            f"{application_manager_role_id} **New {type_label} application received!**",
                            embed=embed,
                            file=player_info,
                        )
                else:
                    await thread.send(
                        f"{application_manager_role_id} **New {type_label} application received!**",
                        embed=embed,
                    )

                # Copy the application message text into the thread
                app_content = message.content[:1900]
                await thread.send(
                    f"**Application from {message.author.mention}:**\n>>> {app_content}"
                )

        # Send thank-you message in the ticket channel
        await message.channel.send(
            f"Hi {message.author.display_name},\n\n"
            f"Thank you for your interest in joining The Aquarium! \U0001F420\n"
            f"Your application has been received and is greatly appreciated.\n\n"
            f"We'll be carefully reviewing it and aim to get back to you within 12 hours.\n\n"
            f"Best regards,\n"
            f"The Aquarium Applications Team"
        )

    async def _find_ticket_opener(self, channel) -> int | None:
        """Parse the Ticket Tool welcome message to find the ticket opener's Discord ID.

        Ticket Tool sends: "Hello @username, welcome to The Aquarium!"
        The mentioned user is the ticket opener.
        """
        async for msg in channel.history(limit=10, oldest_first=True):
            # Check for Ticket Tool bot messages with mentions
            if msg.author.bot and msg.mentions:
                if "welcome to" in msg.content.lower():
                    for mentioned in msg.mentions:
                        if not mentioned.bot:
                            return mentioned.id
            # Also check embeds (some Ticket Tool configs use embeds)
            if msg.author.bot and msg.embeds:
                for embed in msg.embeds:
                    desc = embed.description or ""
                    if "welcome" in desc.lower():
                        match = re.search(r'<@!?(\d+)>', desc)
                        if match:
                            return int(match.group(1))

        # Fallback: use channel permission overwrites
        for target in channel.overwrites:
            if isinstance(target, discord.Member) and not target.bot:
                return target.id
        return None

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(OnMessage(client))
