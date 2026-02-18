import base64
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
from Helpers.variables import (
    ALL_GUILD_IDS,
    WEBSITE_URL,
    RAID_COLLECTING_CHANNEL_ID,
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


# ---- Cog ----

class Generate(commands.Cog):
    generate = SlashCommandGroup(
        "generate", "ADMIN: Generate persistent messages/panels",
        guild_ids=ALL_GUILD_IDS,
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

    @commands.Cog.listener()
    async def on_ready(self):
        self.client.add_view(ApplicationButtonView())
        self.client.add_view(ClaimView())


def setup(client):
    client.add_cog(Generate(client))
