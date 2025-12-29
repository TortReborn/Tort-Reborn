import discord
from discord.ext import commands
from discord.commands import slash_command
from discord.ui import View, button
from discord import Permissions
from pathlib import Path
from textwrap import dedent

from Helpers.database import DB
from Helpers.variables import guilds, raid_collecting_channel, shell_emoji_id, aspect_emoji_id

CHANNEL_ID = raid_collecting_channel
GUILD_ID   = guilds[0]

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
                "❌ You don’t have a linked game account. Please use `/link` first.",
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

        # Get current raid count from database
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

        # Calculate conversion amounts
        aspect_count = total_raids // 2
        if aspect_count == 0:
            db.close()
            return await interaction.followup.send(
                "❌ You need at least 2 uncollected raids to claim 1 Aspect.",
                ephemeral=True
            )

        remainder_raids = total_raids % 2
        raids_spent = aspect_count * 2

        # Update with race condition check
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


class RaidCollecting(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(
        guild_ids=[GUILD_ID],
        description="Post the Raid Collecting panel into the designated channel.",
        default_member_permissions=Permissions(administrator=True),
        dm_permission=False
    )
    async def postraidcollecting(self, ctx: discord.ApplicationContext):
        """Posts the Raid Collecting banner, embed + button."""
        await ctx.defer(ephemeral=True)

        channel = self.client.get_channel(CHANNEL_ID)
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

            {aspect_emoji_id} **Claim Aspects**
            • 1 Aspect for every 2 Guild Raids you complete.

            {shell_emoji_id} **Claim Shells**
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
        self.client.add_view(ClaimView())
        pass

def setup(client):
    client.add_cog(RaidCollecting(client))