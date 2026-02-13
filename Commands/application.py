import asyncio
import json
import re
from datetime import datetime, timezone
from io import BytesIO

import discord
from discord import ApplicationContext
from discord.ext import commands

from Helpers.classes import BasicPlayerStats
from Helpers.database import DB
from Helpers.embed_updater import update_poll_embed
from Helpers.functions import generate_applicant_info, getPlayerUUID, getNameFromUUID
from Helpers.openai_helper import detect_application, detect_rejoin_intent, extract_ign, parse_application
from Helpers.sheets import add_row
from Helpers.variables import (
    guilds,
    te,
    member_app_channel,
    application_manager_role_id,
    invited_category_name,
    error_channel,
    manual_review_role_id,
)


class ApplicationCommands(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client

    async def _lookup_app(self, ctx) -> tuple | None:
        """Look up an application record from a ticket channel or exec thread.

        Returns (ticket_channel, row) or None if not found / already decided.
        row = (applicant_discord_id, thread_id, app_type, decision, ign, channel_id)
        """
        source = ctx.channel

        # Try ticket channel ID first, then exec thread ID
        db = DB(); db.connect()
        db.cursor.execute(
            """SELECT applicant_discord_id, thread_id, app_type, decision, ign, channel
               FROM new_app WHERE channel = %s""",
            (source.id,)
        )
        row = db.cursor.fetchone()
        if not row:
            db.cursor.execute(
                """SELECT applicant_discord_id, thread_id, app_type, decision, ign, channel
                   FROM new_app WHERE thread_id = %s""",
                (source.id,)
            )
            row = db.cursor.fetchone()
        db.close()

        if not row:
            await ctx.followup.send(
                "No application record found. Use this in a ticket channel or its exec thread.",
                ephemeral=True,
            )
            return None

        applicant_discord_id, thread_id, app_type, existing_decision, stored_ign, ticket_channel_id = row

        if existing_decision is not None:
            await ctx.followup.send(
                f"This application has already been **{existing_decision}**.",
                ephemeral=True,
            )
            return None

        # Resolve the ticket channel
        ticket_channel = self.client.get_channel(ticket_channel_id)
        if ticket_channel is None:
            try:
                ticket_channel = await self.client.fetch_channel(ticket_channel_id)
            except Exception:
                await ctx.followup.send(
                    "Could not find the ticket channel.", ephemeral=True
                )
                return None

        return ticket_channel, (applicant_discord_id, thread_id, app_type, existing_decision, stored_ign)

    @discord.slash_command(
        name="accept",
        description="Accept this ticket's application",
        guild_ids=guilds + [te],
        default_member_permissions=discord.Permissions(manage_channels=True),
    )
    async def accept(self, ctx: ApplicationContext):
        await ctx.defer(ephemeral=True)

        result = await self._lookup_app(ctx)
        if result is None:
            return

        channel, (applicant_discord_id, thread_id, app_type, _, stored_ign) = result

        if not app_type:
            await ctx.followup.send(
                "No application type detected yet. The applicant may not have submitted "
                "their application, or the AI could not determine the type.",
                ephemeral=True,
            )
            return

        applicant = None
        if applicant_discord_id:
            applicant = await self._resolve_member(channel, applicant_discord_id)

        now = datetime.now(timezone.utc)

        if app_type == "guild_member":
            await self._accept_guild_member(ctx, channel, applicant, applicant_discord_id, thread_id, stored_ign, now)
        else:
            await self._accept_community_member(ctx, channel, applicant, applicant_discord_id, thread_id, stored_ign, now)

    async def _accept_guild_member(self, ctx, channel, applicant, applicant_discord_id, thread_id, stored_ign, now):
        """Handle guild member acceptance."""
        mention = applicant.mention if applicant else "Applicant"
        partytort = discord.utils.get(channel.guild.emojis, name="partytort")
        party_emoji = str(partytort) if partytort else "\U0001F389"
        await channel.send(
            f"Hey {mention},\n\n"
            f"Congratulations, your application to join **The Aquarium** has been "
            f"**accepted**! {party_emoji}\n\n"
            f"To join, just type `/gu join TAq` next time you're online. "
            f"Once you join the guild in-game, you will be given your Discord roles.\n\n"
            f"Best Regards,\n"
            f"The Aquarium Applications Team"
        )

        # Extract IGN: use stored IGN if available, otherwise extract from channel messages
        ign = stored_ign or ""
        if not ign:
            application_text = await self._collect_application_text(channel, applicant)
            ign_result = await asyncio.to_thread(extract_ign, application_text)
            ign = ign_result.get("ign", "")
            confidence = ign_result.get("confidence", 0.0)
        else:
            confidence = 1.0  # Already stored from AI detection

        if confidence < 0.7 or not ign:
            await ctx.followup.send(
                f"Application accepted. **Could not auto-extract IGN** "
                f"(confidence: {confidence:.0%}).\n"
                f"Parsed IGN: `{ign}`\n"
                f"Please manually link the user with `/manage link`.",
                ephemeral=True,
            )
        else:
            # Auto-link in discord_links (use discord_id even if member object wasn't resolved)
            link_id = applicant.id if applicant else applicant_discord_id
            if link_id:
                uuid_data = await asyncio.to_thread(getPlayerUUID, ign)
                uuid = uuid_data[1] if uuid_data else None

                db = DB(); db.connect()
                db.cursor.execute(
                    """INSERT INTO discord_links (discord_id, ign, uuid, linked, rank, app_channel)
                       VALUES (%s, %s, %s, FALSE, '', %s)
                       ON CONFLICT (discord_id) DO UPDATE
                       SET ign = EXCLUDED.ign, uuid = EXCLUDED.uuid,
                           app_channel = EXCLUDED.app_channel,
                           linked = FALSE""",
                    (link_id, ign, uuid, channel.id)
                )
                db.connection.commit()
                db.close()

            await ctx.followup.send(
                f"Application accepted. IGN: `{ign}` (confidence: {confidence:.0%}). "
                f"User will be auto-registered when they join the guild in-game.",
                ephemeral=True,
            )

        # Move ticket to "Invited" category and rename
        guild = self.client.get_guild(channel.guild.id) or channel.guild
        invited_cat = discord.utils.get(guild.categories, name=invited_category_name)
        if invited_cat:
            try:
                await channel.edit(category=invited_cat)
            except discord.Forbidden:
                await ctx.followup.send(
                    "Could not move ticket to Invited category (missing permissions).",
                    ephemeral=True,
                )
        else:
            await ctx.followup.send(
                f"Could not find category named \"{invited_category_name}\" in the server.",
                ephemeral=True,
            )

        # Update DB
        db = DB(); db.connect()
        db.cursor.execute(
            """UPDATE new_app
               SET decision = 'accepted', decision_at = %s, app_type = %s, ign = %s
               WHERE channel = %s""",
            (now, "guild_member", ign or None, channel.id)
        )
        db.connection.commit()
        db.close()

        # Update poll embed status
        await update_poll_embed(self.client, channel.id, ":green_circle: Invited", 0x3ED63E)

        # Update exec thread
        await self._update_exec_thread(thread_id, "accepted", "guild_member", ign)

        # Process recruiter tracking (Google Sheets)
        await self._process_recruiter_tracking(channel, ign)

    async def _accept_community_member(self, ctx, channel, applicant, applicant_discord_id, thread_id, stored_ign, now):
        """Handle community member acceptance."""
        mention = applicant.mention if applicant else "Applicant"
        await channel.send(
            f"Hey {mention},\n\n"
            f"Congratulations, your application to become a **Community Member** of "
            f"The Aquarium has been **accepted**! \U0001F389\n\n"
            f"Welcome to the community!\n\n"
            f"Best Regards,\n"
            f"The Aquarium Applications Team"
        )

        # Extract IGN: use stored IGN if available, otherwise extract from channel messages
        ign = stored_ign or ""
        if not ign:
            application_text = await self._collect_application_text(channel, applicant)
            ign_result = await asyncio.to_thread(extract_ign, application_text)
            ign = ign_result.get("ign", "")
            confidence = ign_result.get("confidence", 0.0)
        else:
            confidence = 1.0

        # Link IGN and set nickname
        link_id = applicant.id if applicant else applicant_discord_id
        if confidence >= 0.7 and ign and link_id:
            uuid_data = await asyncio.to_thread(getPlayerUUID, ign)
            uuid = uuid_data[1] if uuid_data else None

            db = DB(); db.connect()
            db.cursor.execute(
                """INSERT INTO discord_links (discord_id, ign, uuid, linked, rank, app_channel)
                   VALUES (%s, %s, %s, TRUE, '', %s)
                   ON CONFLICT (discord_id) DO UPDATE
                   SET ign = EXCLUDED.ign, uuid = EXCLUDED.uuid,
                       app_channel = EXCLUDED.app_channel,
                       linked = TRUE""",
                (link_id, ign, uuid, channel.id)
            )
            db.connection.commit()
            db.close()

            # Set nickname to MC username (requires member object)
            if applicant:
                try:
                    await applicant.edit(nick=ign)
                except discord.Forbidden:
                    pass  # Can't change nickname (e.g. server owner)

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

        # Update DB
        db = DB(); db.connect()
        db.cursor.execute(
            """UPDATE new_app
               SET decision = 'accepted', decision_at = %s, app_type = 'community_member',
                   ign = %s
               WHERE channel = %s""",
            (now, ign or None, channel.id)
        )
        db.connection.commit()
        db.close()

        # Rename ticket to c-accepted-NUM
        num_match = re.search(r'(\d+)', channel.name)
        ticket_num = num_match.group(1) if num_match else channel.name.split("-", 1)[-1]
        try:
            await channel.edit(name=f"c-accepted-{ticket_num}")
        except discord.Forbidden:
            pass

        # Update poll embed status
        await update_poll_embed(self.client, channel.id, ":orange_circle: Accepted", 0xFFE019)

        # Update exec thread
        await self._update_exec_thread(thread_id, "accepted", "community_member", ign)

        if confidence >= 0.7 and ign:
            await ctx.followup.send(
                f"Application accepted (Community Member). IGN: `{ign}`. Nickname updated. {role_status}",
                ephemeral=True,
            )
        else:
            await ctx.followup.send(
                f"Application accepted (Community Member). **Could not auto-extract IGN** "
                f"(confidence: {confidence:.0%}). Parsed IGN: `{ign}`\n"
                f"{role_status}\n"
                f"Please manually link with `/manage link`.",
                ephemeral=True,
            )

    @discord.slash_command(
        name="deny",
        description="Deny this ticket's application",
        guild_ids=guilds + [te],
        default_member_permissions=discord.Permissions(manage_channels=True),
    )
    async def deny(self, ctx: ApplicationContext):
        await ctx.defer(ephemeral=True)

        result = await self._lookup_app(ctx)
        if result is None:
            return

        channel, (applicant_discord_id, thread_id, app_type, _, _) = result

        applicant = None
        if applicant_discord_id:
            applicant = await self._resolve_member(channel, applicant_discord_id)
        mention = applicant.mention if applicant else "Applicant"
        now = datetime.now(timezone.utc)

        # Send denial message in the ticket channel
        await channel.send(
            f"Hi {mention},\n\n"
            f"We regret to inform you that your application to join our guild did not "
            f"meet our current standards. We appreciate your interest and thank you "
            f"for considering us.\n\n"
            f"Best Regards,\n"
            f"The Aquarium Applications Team"
        )

        # Update DB
        db = DB(); db.connect()
        db.cursor.execute(
            """UPDATE new_app
               SET decision = 'denied', decision_at = %s
               WHERE channel = %s""",
            (now, channel.id)
        )
        db.connection.commit()
        db.close()

        # Rename ticket based on app type
        num_match = re.search(r'(\d+)', channel.name)
        ticket_num = num_match.group(1) if num_match else channel.name.split("-", 1)[-1]
        new_name = f"denied-{ticket_num}" if app_type == "guild_member" else f"c-denied-{ticket_num}"
        try:
            await channel.edit(name=new_name)
        except discord.Forbidden:
            pass

        # Update poll embed status
        await update_poll_embed(self.client, channel.id, ":orange_circle: Denied", 0xFFE019)

        # Update exec thread
        await self._update_exec_thread(thread_id, "denied")

        await ctx.followup.send(
            "Application denied.",
            ephemeral=True,
        )

    @discord.slash_command(
        name="receive",
        description="Manually detect and process the last application message in this ticket",
        guild_ids=guilds + [te],
        default_member_permissions=discord.Permissions(manage_channels=True),
    )
    async def receive(self, ctx: ApplicationContext):
        await ctx.defer(ephemeral=True)

        # Must be in a Guild Applications ticket channel
        if not (
            ctx.channel.category
            and ctx.channel.category.name == "Guild Applications"
            and ctx.channel.name.startswith("ticket-")
        ):
            await ctx.followup.send(
                "This command can only be used in a `ticket-` channel under **Guild Applications**.",
                ephemeral=True,
            )
            return

        # Look up the application record
        db = DB(); db.connect()
        db.cursor.execute(
            "SELECT applicant_discord_id, app_type, thread_id FROM new_app WHERE channel = %s",
            (ctx.channel.id,)
        )
        row = db.cursor.fetchone()
        db.close()

        if not row:
            await ctx.followup.send(
                "No application record found for this channel.",
                ephemeral=True,
            )
            return

        stored_discord_id, app_type, thread_id = row

        if app_type is not None:
            await ctx.followup.send(
                f"This application has already been detected as **{app_type}**.",
                ephemeral=True,
            )
            return

        # Find the applicant's discord ID if not stored yet
        if stored_discord_id is None:
            stored_discord_id = await self._find_ticket_opener(ctx.channel)
            if stored_discord_id:
                db = DB(); db.connect()
                db.cursor.execute(
                    "UPDATE new_app SET applicant_discord_id = %s WHERE channel = %s",
                    (stored_discord_id, ctx.channel.id)
                )
                db.connection.commit()
                db.close()

        # Find the last non-bot message (from the applicant if known, otherwise any non-bot)
        target_message = None
        async for msg in ctx.channel.history(limit=50):
            if msg.author.bot:
                continue
            if stored_discord_id and msg.author.id != stored_discord_id:
                continue
            target_message = msg
            break

        if not target_message:
            await ctx.followup.send(
                "Could not find a recent applicant message in this channel.",
                ephemeral=True,
            )
            return

        # --- Ex-member check (same logic as on_message) ---
        is_ex_member = False
        mc_name = ""
        if stored_discord_id:
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
                applicant_member = await self._resolve_member(ctx.channel, stored_discord_id)
                if applicant_member:
                    ex_member_role_names = {'Ex-Member', 'Honored Fish', 'Retired Chief'}
                    member_role_names = {r.name for r in applicant_member.roles}
                    if ex_member_role_names & member_role_names:
                        is_ex_member = True

            if is_ex_member:
                detection = await asyncio.to_thread(detect_rejoin_intent, target_message.content)
                if detection.get("error"):
                    await ctx.followup.send(
                        f"AI detection error: `{detection['error'][:200]}`",
                        ephemeral=True,
                    )
                    return
                if not detection["is_application"] or detection["confidence"] < 0.4:
                    await ctx.followup.send(
                        "The message was not detected as an application (ex-member rejoin check).\n"
                        f"Confidence: {detection.get('confidence', 0):.0%}",
                        ephemeral=True,
                    )
                    return

                detected_type = detection["app_type"]

                if not mc_name:
                    mc_name = await self._extract_ign_from_text(target_message.content)
                    if not mc_name:
                        ign_result = await asyncio.to_thread(extract_ign, target_message.content)
                        if not ign_result.get("error") and ign_result.get("confidence", 0) >= 0.5:
                            mc_name = ign_result["ign"]

        # --- Regular applicant path ---
        if not is_ex_member:
            detection = await asyncio.to_thread(detect_application, target_message.content)

            if detection.get("error"):
                await ctx.followup.send(
                    f"AI detection error: `{detection['error'][:200]}`",
                    ephemeral=True,
                )
                return

            if not detection["is_application"] or detection["confidence"] < 0.7:
                await ctx.followup.send(
                    "The message was not detected as an application.\n"
                    f"Confidence: {detection.get('confidence', 0):.0%}",
                    ephemeral=True,
                )
                return

            detected_type = detection["app_type"]

            mc_name = await self._extract_ign_from_text(target_message.content)
            if not mc_name:
                ign_result = await asyncio.to_thread(extract_ign, target_message.content)
                if not ign_result.get("error") and ign_result.get("confidence", 0) >= 0.7:
                    mc_name = ign_result["ign"]

        # --- Process the detected application (mirrors on_message._process_detected_application) ---
        # Generate player stats image
        pdata = None
        blacklist_warning = ""
        if mc_name:
            pdata = await asyncio.to_thread(BasicPlayerStats, mc_name)
            if pdata.error:
                pdata = None
            else:
                try:
                    with open('blacklist.json', 'r') as f:
                        blacklist = json.load(f)
                    for player in blacklist:
                        if pdata.UUID == player['UUID']:
                            blacklist_warning = (
                                f':no_entry: Player present on blacklist!\n'
                                f'**Name:** {pdata.username}\n**UUID:** {pdata.UUID}'
                            )
                except FileNotFoundError:
                    pass

        # Update DB with the detected type and IGN
        db = DB(); db.connect()
        db.cursor.execute(
            "UPDATE new_app SET app_type = %s, ign = %s WHERE channel = %s",
            (detected_type, mc_name or None, ctx.channel.id)
        )
        db.connection.commit()
        db.close()

        # Update poll embed to "Received" and rename channel
        await update_poll_embed(self.client, ctx.channel.id, ":green_circle: Received", 0x3ED63E)
        num_match = re.search(r'(\d+)', ctx.channel.name)
        ticket_num = num_match.group(1) if num_match else ctx.channel.name.split("-", 1)[-1]
        try:
            await ctx.channel.edit(name=f"received-{ticket_num}")
        except Exception:
            pass

        # Re-fetch thread_id if needed
        if not thread_id:
            db = DB(); db.connect()
            db.cursor.execute(
                "SELECT thread_id FROM new_app WHERE channel = %s",
                (ctx.channel.id,)
            )
            trow = db.cursor.fetchone()
            db.close()
            if trow:
                thread_id = trow[0]

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

                embed_title = f"Application {ticket_num}"
                if pdata:
                    embed_title += f" ({pdata.username})"

                embed = discord.Embed(
                    title=embed_title,
                    description=blacklist_warning,
                    colour=0x3ed63e,
                )
                embed.add_field(name="Channel", value=f":link: <#{ctx.channel.id}>", inline=True)
                embed.add_field(name="Type", value=type_label, inline=True)

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

                app_content = target_message.content[:1900]
                await thread.send(
                    f"**Application from {target_message.author.mention}:**\n>>> {app_content}"
                )

        # Send thank-you message in the ticket channel
        await ctx.channel.send(
            f"Hi {target_message.author.mention},\n\n"
            f"Thank you for your interest in joining The Aquarium! \U0001F420\n"
            f"Your application has been received and is greatly appreciated.\n\n"
            f"We'll be carefully reviewing it and aim to get back to you within 12 hours.\n\n"
            f"Best regards,\n"
            f"The Aquarium Applications Team"
        )

        type_label = "Guild Member" if detected_type == "guild_member" else "Community Member"
        await ctx.followup.send(
            f"Application manually received. Type: **{type_label}**, IGN: `{mc_name or 'unknown'}`",
            ephemeral=True,
        )

    # --- Helper methods ---

    async def _resolve_member(self, channel, discord_id: int) -> discord.Member | None:
        """Resolve a member from the ticket channel's guild (TAq server).

        Uses the bot's guild cache to ensure we always look up the correct guild,
        regardless of which server the command was run from.
        """
        guild = self.client.get_guild(channel.guild.id)
        if guild is None:
            guild = channel.guild
        member = guild.get_member(discord_id)
        if member is None:
            try:
                member = await guild.fetch_member(discord_id)
            except Exception:
                pass
        return member

    async def _collect_application_text(self, channel, applicant) -> str:
        """Collect the application text from a ticket channel."""
        text = ""
        async for msg in channel.history(limit=50, oldest_first=True):
            if applicant and msg.author.id == applicant.id:
                text += msg.content + "\n"
            elif not msg.author.bot:
                text += msg.content + "\n"
            if msg.embeds:
                for embed in msg.embeds:
                    for field in embed.fields:
                        text += f"{field.name}: {field.value}\n"
                    if embed.description:
                        text += f"{embed.description}\n"
        return text

    async def _update_exec_thread(self, thread_id, decision, app_type=None, ign=None):
        """Send a status update to the exec discussion thread."""
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
            label = "Guild Member" if app_type == "guild_member" else "Community Member"
            embed.add_field(name="Type", value=label, inline=True)
        if ign:
            embed.add_field(name="IGN", value=ign, inline=True)

        await thread.send(embed=embed)

    async def _process_recruiter_tracking(self, channel, ign):
        """Process recruiter tracking for accepted guild member applications."""
        ticket_num_match = re.search(r'(\d+)', channel.name)
        ticket_num = ticket_num_match.group(1) if ticket_num_match else channel.name

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
            err_ch = self.client.get_channel(error_channel)
            if err_ch:
                await err_ch.send(
                    f"## Recruiter Tracker - OpenAI Error\n"
                    f"**Ticket:** `{channel.name}`\n"
                    f"```\n{result['error'][:500]}\n```"
                )
            return

        recruiter = result.get("recruiter", "")
        certainty = result.get("certainty", 0.0)

        if certainty >= 0.90 and ign:
            sheet_result = await asyncio.to_thread(add_row, ticket_num, ign, recruiter)
            if not sheet_result.get("success"):
                err_ch = self.client.get_channel(error_channel)
                if err_ch:
                    await err_ch.send(
                        f"## Recruiter Tracker - Sheets Error\n"
                        f"**Ticket:** `{channel.name}` | **IGN:** `{ign}`\n"
                        f"```\n{sheet_result.get('error', 'Unknown')[:500]}\n```"
                    )
        else:
            review_ch = self.client.get_channel(member_app_channel)
            if review_ch:
                await review_ch.send(
                    f"<@&{manual_review_role_id}> **Recruiter tracking needs manual review**\n"
                    f"**Ticket:** `{channel.name}` | **Parsed IGN:** `{ign}` | "
                    f"**Parsed Recruiter:** `{recruiter}` | **Certainty:** `{certainty:.0%}`\n"
                    f"Please update the recruiter sheet manually."
                )

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

    async def _find_ticket_opener(self, channel) -> int | None:
        """Parse the Ticket Tool welcome message to find the ticket opener's Discord ID."""
        async for msg in channel.history(limit=10, oldest_first=True):
            if msg.author.bot and msg.mentions:
                if "welcome to" in msg.content.lower():
                    for mentioned in msg.mentions:
                        if not mentioned.bot:
                            return mentioned.id
            if msg.author.bot and msg.embeds:
                for embed in msg.embeds:
                    desc = embed.description or ""
                    if "welcome" in desc.lower():
                        match = re.search(r'<@!?(\d+)>', desc)
                        if match:
                            return int(match.group(1))
        for target in channel.overwrites:
            if isinstance(target, discord.Member) and not target.bot:
                return target.id
        return None

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(ApplicationCommands(client))
