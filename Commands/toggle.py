import discord
from discord import SlashCommandGroup, ApplicationContext
from discord.ext import commands

from Helpers.database import DB
from Helpers.variables import guilds


class Toggle(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client

    toggle_group = SlashCommandGroup(
        'toggle', 'Toggle various bot settings',
        guild_ids=guilds,
        default_member_permissions=discord.Permissions(manage_roles=True)
    )

    @toggle_group.command(name='attack_ping', description='Toggle whether Spearhead is pinged when a claim is attacked')
    async def attack_ping(self, ctx: ApplicationContext):
        await ctx.defer(ephemeral=True)

        db = DB()
        db.connect()

        # Check current setting
        db.cursor.execute(
            "SELECT setting_value FROM guild_settings WHERE guild_id = %s AND setting_key = %s",
            (ctx.guild_id, 'attack_ping')
        )
        result = db.cursor.fetchone()

        # Default is True (pings enabled), toggle to opposite
        current_value = result[0] if result else True
        new_value = not current_value

        # Upsert the setting
        db.cursor.execute("""
            INSERT INTO guild_settings (guild_id, setting_key, setting_value, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (guild_id, setting_key)
            DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_at = NOW()
        """, (ctx.guild_id, 'attack_ping', new_value))

        db.connection.commit()
        db.close()

        status = "enabled" if new_value else "disabled"
        emoji = "🔔" if new_value else "🔕"

        await ctx.followup.send(
            f"{emoji} **Attack Ping:** Spearhead pings are now **{status}**.",
            ephemeral=True
        )

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Toggle(client))
