import base64
import datetime
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from textwrap import dedent

import discord
from discord.ext import commands
from discord.commands import SlashCommandGroup
from discord.ui import View, button
from discord import Permissions

from Helpers.database import DB
from Helpers.logger import log, ERROR
from Helpers.variables import (
    HOME_GUILD_IDS,
    TAQ_GUILD_ID,
    WEBSITE_URL,
    RAID_COLLECTING_CHANNEL_ID,
    WAR_INFO_CHANNEL_ID,
    SHELL_EXCHANGE_CHANNEL_ID,
    SHELL_EMOJI,
    ASPECT_EMOJI,
)

APPLICATION_TOKEN_SECRET = os.getenv("APPLICATION_TOKEN_SECRET", "")


# ---- Application button helpers ----

def _generate_token(user: discord.User | discord.Member, app_type: str) -> str:
    """Generate a signed token carrying the user's Discord identity."""
    payload = json.dumps({
        "discord_id": str(user.id),
        "discord_username": user.name,
        "discord_avatar": user.avatar.key if user.avatar else "",
        "type": app_type,
        "exp": int(time.time()) + 1800,  # 30 minutes
    })
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    sig = hmac.new(
        APPLICATION_TOKEN_SECRET.encode(),
        payload_b64.encode(),
        hashlib.sha256,
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{payload_b64}.{sig_b64}"


class ApplicationButtonView(discord.ui.View):
    """Persistent view with two application buttons."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Guild Member Application",
        style=discord.ButtonStyle.primary,
        custom_id="apply_guild",
        emoji="\U0001F420",
    )
    async def guild_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._handle_button(interaction, "guild")

    @discord.ui.button(
        label="Community Member Application",
        style=discord.ButtonStyle.success,
        custom_id="apply_community",
        emoji="\U0001FABC",
    )
    async def community_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._handle_button(interaction, "community")

    async def _handle_button(self, interaction: discord.Interaction, app_type: str):
        await interaction.response.defer(ephemeral=True)

        token = _generate_token(interaction.user, app_type)
        url = f"{WEBSITE_URL}/apply/{app_type}?token={token}"

        type_label = "Guild Member" if app_type == "guild" else "Community Member"

        embed = discord.Embed(
            title=f"{type_label} Application",
            description=(
                f"Click the link below to fill out your application on our website.\n\n"
                f"**[Open Application Form]({url})**\n\n"
                f"This link expires in **30 minutes** and is unique to your Discord account."
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="Do not share this link with anyone else.")

        await interaction.followup.send(embed=embed, ephemeral=True)


# ---- Raid collecting views ----

class ClaimView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @button(label="Claim rewards", style=discord.ButtonStyle.green, custom_id="raid_claim")
    async def claim(self, _: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        db = DB(); db.connect()
        db.cursor.execute(
            "SELECT uuid FROM discord_links WHERE discord_id = %s",
            (interaction.user.id,)
        )
        row = db.cursor.fetchone()
        if not row:
            db.close()
            return await interaction.followup.send(
                "❌ You don't have a linked game account. Please use `/link` first.",
                ephemeral=True
            )

        uuid = row[0]
        db.cursor.execute(
            "SELECT uncollected_raids FROM uncollected_raids WHERE uuid = %s",
            (uuid,)
        )
        row   = db.cursor.fetchone()
        count = row[0] if row else 0
        db.close()

        if count <= 0:
            return await interaction.followup.send(
                "✅ You have no uncollected raids right now.",
                ephemeral=True
            )

        view = ConvertView(uuid, count)
        interaction.client.add_view(view)
        await interaction.followup.send(
            f"You have **{count}** uncollected raid(s).\nConvert them into:",
            view=view,
            ephemeral=True
        )


class ConvertView(View):
    def __init__(self, uuid: str, count: int):
        super().__init__(timeout=None)
        self.uuid  = uuid
        self.count = count

    @button(label="Shells", style=discord.ButtonStyle.primary, custom_id="convert_shells")
    async def shells(self, _: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        db = DB(); db.connect()

        db.cursor.execute("""
            UPDATE uncollected_raids
                SET uncollected_raids = uncollected_raids - %s,
                    collected_raids   = collected_raids   + %s
            WHERE uuid = %s AND uncollected_raids >= %s
        """, (self.count, self.count, self.uuid, self.count))
        db.connection.commit()

        if db.cursor.rowcount == 0:
            db.close()
            await interaction.followup.send("❌ You cannot claim raids twice.", ephemeral=True)
            return

        # Get IGN from discord_links
        db.cursor.execute(
            "SELECT ign FROM discord_links WHERE discord_id = %s",
            (interaction.user.id,)
        )
        ign_row = db.cursor.fetchone()
        ign = ign_row[0] if ign_row else "Unknown"

        db.cursor.execute("""
            INSERT INTO shells AS sh ("user", shells, balance, ign)
                VALUES (%s, %s, %s, %s)
            ON CONFLICT ("user") DO UPDATE SET
                shells  = sh.shells + EXCLUDED.shells,
                balance = sh.balance + EXCLUDED.balance,
                ign     = EXCLUDED.ign
        """, (str(interaction.user.id), self.count, self.count, ign))

        db.connection.commit()
        db.close()

        await interaction.edit_original_response(
            content=f"✅ Converted **{self.count}** raid(s) into shells!",
            view=None
        )

    @button(label="Aspects", style=discord.ButtonStyle.secondary, custom_id="convert_aspects")
    async def aspects(self, _: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        db = DB(); db.connect()

        db.cursor.execute("""
            SELECT uncollected_raids, uncollected_aspects
            FROM uncollected_raids
            WHERE uuid = %s
        """, (self.uuid,))
        row = db.cursor.fetchone()

        if not row:
            db.close()
            return await interaction.followup.send("❌ No raid data found.", ephemeral=True)

        total_raids = row[0]
        current_aspects = row[1]

        aspect_count = total_raids // 2
        if aspect_count == 0:
            db.close()
            return await interaction.followup.send(
                "❌ You need at least 2 uncollected raids to claim 1 Aspect.",
                ephemeral=True
            )

        remainder_raids = total_raids % 2
        raids_spent = aspect_count * 2

        db.cursor.execute("""
            UPDATE uncollected_raids
               SET uncollected_raids   = %s,
                   uncollected_aspects = uncollected_aspects + %s,
                   collected_raids     = collected_raids + %s
             WHERE uuid = %s AND uncollected_raids >= %s
        """, (remainder_raids, aspect_count, raids_spent, self.uuid, raids_spent))
        db.connection.commit()

        if db.cursor.rowcount == 0:
            db.close()
            await interaction.followup.send("❌ You cannot claim raids twice.", ephemeral=True)
            return

        db.close()

        await interaction.edit_original_response(
            content=(
                f"✅ Converted **{raids_spent}** uncollected raid(s) into "
                f"**{aspect_count}** new uncollected aspect(s)!\n"
                f"• You now have **{current_aspects + aspect_count}** total uncollected aspect(s).\n"
                f"• **{remainder_raids}** raid(s) remain uncollected."
            ),
            view=None
        )


# ---- Shell convert view ----

class ShellConvertView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @button(label="Shells -> Aspects", style=discord.ButtonStyle.green, custom_id="shells_to_aspects")
    async def shells_to_aspects(self, _: discord.ui.Button, interaction: discord.Interaction):
        # Linked account check
        db = DB(); db.connect()
        db.cursor.execute(
            "SELECT uuid FROM discord_links WHERE discord_id = %s",
            (interaction.user.id,)
        )
        link_row = db.cursor.fetchone()
        if not link_row:
            db.close()
            return await interaction.response.send_message(
                "You don't have a linked game account. Please use `/link` first.",
                ephemeral=True
            )
        uuid = str(link_row[0])

        # No pending uncollected aspects allowed
        db.cursor.execute(
            "SELECT uncollected_aspects FROM uncollected_raids WHERE uuid = %s",
            (uuid,)
        )
        raids_row = db.cursor.fetchone()
        if raids_row and raids_row[0] > 0:
            db.close()
            return await interaction.response.send_message(
                f"You already have **{raids_row[0]}** uncollected aspect(s) waiting. "
                "Collect them before converting more shells.",
                ephemeral=True
            )

        # Shell balance check (need at least 4 for one aspect)
        db.cursor.execute(
            'SELECT balance, last_aspect_convert_at FROM shells WHERE "user" = %s',
            (str(interaction.user.id),)
        )
        shells_row = db.cursor.fetchone()
        balance = shells_row[0] if shells_row else 0
        if balance < 6:
            db.close()
            return await interaction.response.send_message(
                f"You need at least **6** shells to convert (you have **{balance}**).",
                ephemeral=True
            )

        # Cooldown check (3 days between conversions)
        last_convert_at = shells_row[1] if shells_row else None
        if last_convert_at is not None:
            ready_at = last_convert_at + datetime.timedelta(days=3)
            if datetime.datetime.now(datetime.timezone.utc) < ready_at:
                db.close()
                ts = int(ready_at.timestamp())
                return await interaction.response.send_message(
                    f"You are on cooldown. You can convert again <t:{ts}:R>.",
                    ephemeral=True
                )
        db.close()

        # All guards passed -- open the amount modal (cap at 40 per conversion)
        max_aspects = min(balance // 6, 40)
        modal = ShellsToAspectsModal(
            discord_id=interaction.user.id,
            uuid=uuid,
            balance=balance,
            max_aspects=max_aspects,
        )
        await interaction.response.send_modal(modal)


# ---- Shells to aspects modal ----

class ShellsToAspectsModal(discord.ui.Modal):
    def __init__(self, discord_id: int, uuid: str, balance: int, max_aspects: int):
        super().__init__(title="Shells -> Aspects")
        self.discord_id  = discord_id
        self.uuid        = uuid
        self.balance     = balance
        self.max_aspects = max_aspects

        self.amount_input = discord.ui.InputText(
            label=f"How many aspects? (max {max_aspects}, 6 shells each)",
            placeholder=f"Enter a number from 1 to {max_aspects}",
            style=discord.InputTextStyle.short,
            max_length=4,
            required=True,
        )
        self.add_item(self.amount_input)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Parse and validate input
        raw = self.amount_input.value.strip()
        if not raw.isdigit() or int(raw) < 1:
            return await interaction.followup.send(
                "Enter a valid number of aspects (at least 1).",
                ephemeral=True
            )
        amount = int(raw)
        if amount > self.max_aspects:
            return await interaction.followup.send(
                f"You can convert at most **{self.max_aspects}** aspect(s) "
                f"with your current balance ({self.balance} shells).",
                ephemeral=True
            )

        cost = amount * 6
        remaining = self.balance - cost

        # Preview embed with conversion breakdown -- user must confirm
        embed = discord.Embed(title="Confirm Conversion", color=discord.Color.blurple())
        embed.add_field(name="Current Shells", value=f"{self.balance} {SHELL_EMOJI}", inline=False)
        embed.add_field(name="Cost", value=f"{cost} {SHELL_EMOJI} ({amount} {ASPECT_EMOJI})", inline=False)
        embed.add_field(name="Remaining Shells", value=f"{remaining} {SHELL_EMOJI}", inline=False)
        embed.set_footer(text="Click Confirm to complete the conversion.")

        view = ShellConfirmView(
            discord_id=self.discord_id,
            uuid=self.uuid,
            balance=self.balance,
            amount=amount,
            cost=cost,
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


# ---- Shell confirm view ----

class ShellConfirmView(View):
    def __init__(self, discord_id: int, uuid: str, balance: int, amount: int, cost: int):
        super().__init__(timeout=180)
        self.discord_id = discord_id
        self.uuid       = uuid
        self.balance    = balance
        self.amount     = amount
        self.cost       = cost

    @button(label="Confirm", style=discord.ButtonStyle.green, custom_id="shell_confirm")
    async def confirm(self, _: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Single atomic transaction: deduct shells and credit aspects
        db = DB(); db.connect()
        try:
            db.cursor.execute("""
                UPDATE shells
                   SET balance                = balance - %s,
                       last_aspect_convert_at = NOW()
                 WHERE "user" = %s AND balance >= %s
            """, (self.cost, str(self.discord_id), self.cost))

            if db.cursor.rowcount == 0:
                db.connection.rollback()
                db.close()
                await interaction.edit_original_response(embed=None, view=None,
                    content="You no longer have enough shells for this conversion.")
                return

            # UPSERT handles both existing and new uncollected_raids rows
            db.cursor.execute("""
                INSERT INTO uncollected_raids (uuid, uncollected_aspects)
                     VALUES (%s, %s)
                ON CONFLICT (uuid) DO UPDATE
                        SET uncollected_aspects =
                              uncollected_raids.uncollected_aspects + EXCLUDED.uncollected_aspects
            """, (self.uuid, self.amount))

            # Audit log entry matching 
            db.cursor.execute(
                "INSERT INTO audit_log (log_type, actor_name, actor_id, action) VALUES (%s, %s, %s, %s)",
                ('shell', interaction.user.name, interaction.user.id,
                 f"converted {self.cost} shells into {self.amount} aspect(s) (self-service).")
            )

            db.connection.commit()
        except Exception as e:
            log(ERROR, f"shell convert transaction failed for {interaction.user} ({self.discord_id}): {e}", context="generate")
            db.connection.rollback()
            db.close()
            await interaction.edit_original_response(embed=None, view=None,
                content="An internal error occurred. No shells were deducted. Please try again.")
            return
        db.close()

        await interaction.edit_original_response(
            embed=None, view=None,
            content=(
                f"Converted **{self.cost}** {SHELL_EMOJI} into **{self.amount}** {ASPECT_EMOJI}!\n"
                f"Your aspects have been added to the collection queue."
            )
        )

    @button(label="Cancel", style=discord.ButtonStyle.secondary, custom_id="shell_confirm_cancel")
    async def cancel(self, _: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=None, view=None, content="Conversion cancelled.")

    async def on_timeout(self):
        # Disable all buttons when the 3-minute window expires
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self, content="This conversion request has expired.", embed=None)
        except Exception:
            pass  # message may already be gone


# ---- Cog ----

class Generate(commands.Cog):
    generate = SlashCommandGroup(
        "generate", "ADMIN: Generate persistent messages/panels",
        guild_ids=HOME_GUILD_IDS,
        default_member_permissions=Permissions(administrator=True),
    )

    def __init__(self, client):
        self.client = client

    @generate.command(name="app_header", description="ADMIN: Post or update the application header with buttons")
    async def app_header(
        self,
        ctx: discord.ApplicationContext,
        level_requirement: discord.Option(int, "Minimum level requirement (e.g. 60, 80)", required=True),
        activity_requirement: discord.Option(str, "Weekly activity requirement (e.g. '4 hours a week')", required=True),
    ):
        await ctx.defer(ephemeral=True)

        embed = discord.Embed(
            title="The Aquarium \u2014 Applications",
            description=(
                "Welcome! Choose an application type below to get started.\n\n"
                "**Guild Member**\n"
                "Join The Aquarium as an in-game guild member.\n"
                f"- Level **{level_requirement}+**\n"
                f"- **{activity_requirement}** weekly activity\n\n"
                "**Community Member**\n"
                "Become part of our community without joining the in-game guild. "
                "Hang out, chat, and participate in events!"
            ),
            color=0x2B82D4,
        )
        embed.set_footer(text="Click a button below to begin your application.")

        view = ApplicationButtonView()

        existing_msg = None
        async for msg in ctx.channel.history(limit=50):
            if msg.author.id == self.client.user.id and msg.components:
                for row in msg.components:
                    for child in row.children:
                        if getattr(child, "custom_id", None) in ("apply_guild", "apply_community"):
                            existing_msg = msg
                            break
                    if existing_msg:
                        break
            if existing_msg:
                break

        if existing_msg:
            await existing_msg.edit(embed=embed, view=view)
            await ctx.followup.send("Application header updated!", ephemeral=True)
        else:
            await ctx.channel.send(embed=embed, view=view)
            await ctx.followup.send("Application header posted!", ephemeral=True)

    @generate.command(name="raid_collecting", description="ADMIN: Post the Raid Collecting panel")
    async def raid_collecting(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)

        channel = self.client.get_channel(RAID_COLLECTING_CHANNEL_ID)
        if not channel:
            return await ctx.followup.send(
                "❌ Could not find the raid-collecting channel.",
                ephemeral=True
            )

        banner_path = Path(__file__).parent.parent / "Images" / "raids" / "raidcollectingbanner.png"
        banner_file = discord.File(str(banner_path), filename="raidcollectingbanner.png")
        await channel.send(file=banner_file)

        view = ClaimView()
        self.client.add_view(view)

        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_image(url="attachment://raidcollectingbanner.png")
        embed.description = dedent(f"""
            ❓ **How to Claim Your Raid Rewards**

            After completing raids, you become eligible for rewards. You can claim **Aspects** or **Shells** depending on how many raids you've completed.

            Make sure to check your eligibility by clicking on the **Claim rewards** button below. You'll see how many raids you've completed and which rewards are available.

            {ASPECT_EMOJI} **Claim Aspects**
            • 1 Aspect for every 2 Guild Raids you complete.

            {SHELL_EMOJI} **Claim Shells**
            • 1 Shell for every Guild Raid you complete.

            **How to Claim**
            1. Click the **Claim rewards** button.
            2. Choose either **Aspects** or **Shells**.
            3. Your claimed rewards will be updated automatically!

            _Complete more raids to earn more rewards!_
            """)
        await channel.send(embed=embed, view=view)

        await ctx.followup.send("✅ Posted the raid-collecting message.", ephemeral=True)

    @generate.command(name="shell_convert", description="ADMIN: Post the Shells -> Aspects conversion panel")
    async def shell_convert(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)

        view = ShellConvertView()
        self.client.add_view(view)

        # Embed styled to match the raid collecting panel
        embed = discord.Embed(color=discord.Color.blurple())
        embed.description = dedent(f"""
            {SHELL_EMOJI} **Convert Shells into Aspects**

            Spend your shells to earn aspects directly. Shell conversion lets you use your shell balance to receive aspects without needing to complete additional raids.

            Conversion rate: **6 {SHELL_EMOJI} = 1 {ASPECT_EMOJI}**

            **Requirements**
            - No uncollected aspects already waiting
            - At least **6** shells in balance
            - At most **40** aspects per conversion
            - 3-day cooldown between conversions

            **How to Convert**
            1. Click the **Shells -> Aspects** button.
            2. Enter how many aspects you want to claim.
            3. Confirm your conversion in the preview.

            _Your aspects will be added to the collection queue automatically. Note: depending on current demand, it may take up to a week or two for your aspects to be delivered._
            """)

        await ctx.channel.send(embed=embed, view=view)
        await ctx.followup.send("Posted the shell conversion panel.", ephemeral=True)

    @generate.command(name="promotions", description="ADMIN: Post the promotions / rank-up info message")
    async def promotions(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)

        description = (
            "Promotions rely on your role in the guild and how you help it! We value all sorts of "
            "contributions, trying our best to give everyone a chance to rank up regardless of "
            "their interests and skills.\n"
            "\n"
            "🕗\u200e \u200e \u200e **Passive contributions**\n"
            "> • Being active in guild chat and/or on Discord\n"
            "> • Joining voice calls\n"
            "> • Playing with other guild members\n"
            "> • Helping out a fellow guild member\n"
            "> • Giving recommendations, advice and feedback.\n"
            "\n"
            "We are thankful for any positive effort that contributes to making TAq a friendly "
            "and welcoming community! ♡\n"
            "\n"
            f"🔧\u200e \u200e \u200e **Active contributions**\n"
            f"> • Joining the [war](https://discord.com/channels/{TAQ_GUILD_ID}/{WAR_INFO_CHANNEL_ID}) effort\n"
            f"> • Completing [Guild Raids](https://discord.com/channels/{TAQ_GUILD_ID}/{RAID_COLLECTING_CHANNEL_ID})\n"
            "> • Starting up giveaways (DM any chief)\n"
            "> • Recruiting new guild members\n"
            f"> • Donating [ingredients or materials](https://discord.com/channels/{TAQ_GUILD_ID}/{SHELL_EXCHANGE_CHANNEL_ID})\n"
            "\n"
            "The first rank-ups are easy to achieve. To get promoted to Manatee (recruiter), "
            "something as easy as regularly chatting with the guild is enough!\n"
            f"After reaching Angler, an [application]({WEBSITE_URL}/login?redirect=/apply/hammerhead) "
            "is required in order to rank up and become a part of our HR team."
        )

        warring_description = (
            "⚔️ **Ranking up through warring**\n"
            "Jumping into guild wars is like hitting the fast lane to rank up in no time! It's an "
            "absolute blast and a great way to dive into exciting end-game content. You get to "
            "team up with fellow guild members, form strategies, and kick some towers!\n"
            "\n"
            "> Being active in wars is super important for our guild because it keeps us strong "
            "and competitive. Plus, it's not just about the thrill – having war power means we "
            "get to hold territories and generate sweet emeralds, which we can then spend on "
            "guild events and community giveaways. So, if you're up for some action-packed fun "
            "and want to help our guild thrive, join the war efforts today!\n"
            "\n"
            "The amount of wars you participate in will always be taken into account for "
            "promotion waves. Bonus points if you help with starting rounds of FFA, teaching "
            "other members, pinging when we get attacked, etc!\n"
            "\n"
            "⏩ Rank up shortcuts\n"
            "```\n"
            "- Starfish/Manatee → Piranha = learn how to queue\n"
            "- Piranha → Barracuda = learning about defending our claim\n"
            "- Barracuda → Angler = learn how to eco\n"
            "```\n"
            "We are always looking for new warrers, so do not hesitate to ask for information!"
        )

        embed1 = discord.Embed(description=description, color=0xA198E1)
        embed2 = discord.Embed(description=warring_description, color=0xA198E1)
        await ctx.channel.send(embeds=[embed1, embed2])
        await ctx.followup.send("Posted the promotions info message.", ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        self.client.add_view(ApplicationButtonView())
        self.client.add_view(ClaimView())
        self.client.add_view(ShellConvertView())


def setup(client):
    client.add_cog(Generate(client))
