import discord
from discord.ext import commands

from Helpers.bot_panels import parse_role_button
from Helpers.role_buttons import toggle_role


class RoleButtons(commands.Cog):
    """Answers every panel role button (custom_id role:<role>[:<required role>]).
    Listening on interactions instead of registering a persistent view per
    role lets the website add a role button for any role without a restart."""

    def __init__(self, client):
        self.client = client

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type is not discord.InteractionType.component:
            return
        parsed = parse_role_button((interaction.data or {}).get("custom_id", ""))
        if parsed:
            await toggle_role(interaction, *parsed)


def setup(client):
    client.add_cog(RoleButtons(client))
