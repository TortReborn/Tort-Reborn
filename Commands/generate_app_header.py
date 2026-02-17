import base64
import hashlib
import hmac
import json
import os
import time

import discord
from discord.ext import commands

from Helpers.variables import guilds, te, WEBSITE_URL

APPLICATION_TOKEN_SECRET = os.getenv("APPLICATION_TOKEN_SECRET", "")


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


class GenerateAppHeader(commands.Cog):
    def __init__(self, client):
        self.client = client

    @discord.slash_command(
        name="generate-app-header",
        description="Post or update the application header with buttons in this channel",
        guild_ids=guilds + [te],
        default_member_permissions=discord.Permissions(administrator=True),
    )
    async def generate_app_header(
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

        # Check if there's already a bot message with this view in the channel
        existing_msg = None
        async for msg in ctx.channel.history(limit=50):
            if msg.author.id == self.client.user.id and msg.components:
                # Check if it has our application buttons
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

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(GenerateAppHeader(client))
