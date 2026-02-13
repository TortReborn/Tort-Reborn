import asyncio
import datetime

from discord.ext import tasks, commands

from Helpers.database import DB
from Helpers.functions import getPlayerDatav3, getPlayerUUID
from Helpers.variables import application_manager_role_id


class CheckApps(commands.Cog):
    def __init__(self, client):
        self.client = client

    @tasks.loop(minutes=1)
    async def check_apps(self):
        # --- 8-hour reminder for open applications ---
        db = DB()
        db.connect()
        db.cursor.execute(
            """
            SELECT channel, created_at, thread_id
              FROM new_app
             WHERE status   = ':green_circle: Opened'
               AND reminder = FALSE
               AND posted   = TRUE
            """
        )
        rows = db.cursor.fetchall()
        db.close()

        now_utc = datetime.datetime.now(datetime.timezone.utc)

        for channel_id, created_at, thread_id in rows:
            if thread_id is None:
                continue

            try:
                elapsed = (now_utc - created_at).total_seconds()

                if elapsed < 8 * 3600:
                    continue

                app_channel = self.client.get_channel(channel_id)
                if not app_channel or app_channel.category.name != "Guild Applications":
                    continue

                thread = self.client.get_channel(thread_id)
                if not thread:
                    continue

                hours = int(elapsed // 3600)
                await thread.send(f"{application_manager_role_id} {hours} hours passed since app creation.")

                db = DB()
                db.connect()
                db.cursor.execute(
                    "UPDATE new_app SET reminder = TRUE WHERE channel = %s",
                    (channel_id,)
                )
                db.connection.commit()
                db.close()

            except Exception as e:
                print(f"Error in CheckApps for row {(channel_id, created_at, thread_id)}: {e}")

    @check_apps.before_loop
    async def before_check_apps(self):
        await self.client.wait_until_ready()

    # --- Guild leave monitoring for accepted applicants ---

    @tasks.loop(minutes=3)
    async def check_guild_leave(self):
        """Monitor accepted guild member applications where the player needs to leave their current guild."""
        db = DB()
        db.connect()
        db.cursor.execute(
            """
            SELECT channel, thread_id, ign, applicant_discord_id
              FROM new_app
             WHERE decision = 'accepted'
               AND app_type = 'guild_member'
               AND guild_leave_pending = TRUE
            """
        )
        rows = db.cursor.fetchall()
        db.close()

        if not rows:
            return

        for channel_id, thread_id, ign, applicant_discord_id in rows:
            try:
                await self._check_single_pending_leave(
                    channel_id, thread_id, ign, applicant_discord_id
                )
            except Exception as e:
                print(f"[check_guild_leave] Error for channel {channel_id}: {e}")

    async def _check_single_pending_leave(self, channel_id, thread_id, ign, applicant_discord_id):
        """Check if a single pending-leave applicant has left their guild."""
        if not ign:
            return

        # Get UUID: try discord_links first, then Mojang lookup
        uuid = None
        if applicant_discord_id:
            db = DB()
            db.connect()
            db.cursor.execute(
                "SELECT uuid FROM discord_links WHERE discord_id = %s",
                (applicant_discord_id,)
            )
            link_row = db.cursor.fetchone()
            db.close()
            if link_row and link_row[0]:
                uuid = str(link_row[0])

        if not uuid:
            uuid_data = await asyncio.to_thread(getPlayerUUID, ign)
            uuid = uuid_data[1] if uuid_data else None

        if not uuid:
            return

        # Check current guild status
        player_data = await asyncio.to_thread(getPlayerDatav3, uuid)
        if not isinstance(player_data, dict):
            return  # API error, skip this cycle

        guild_info = player_data.get("guild")
        still_in_guild = bool(guild_info and isinstance(guild_info, dict) and guild_info.get("name"))

        if still_in_guild:
            return  # Player is still in a guild, check again next cycle

        # --- Player has left their guild! ---

        # 1. Update database
        db = DB()
        db.connect()
        db.cursor.execute(
            "UPDATE new_app SET guild_leave_pending = FALSE WHERE channel = %s",
            (channel_id,)
        )
        db.connection.commit()
        db.close()

        # 2. Post notification in exec thread
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
                await thread.send(
                    f"{application_manager_role_id} **{ign}** has left their guild! "
                    f"They can now be invited.\n"
                    f"Run `/invite` in the ticket channel or this thread to send them the invite message."
                )

        print(f"[check_guild_leave] {ign} has left their guild. Notified exec thread.")

    @check_guild_leave.before_loop
    async def before_check_guild_leave(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.check_apps.is_running():
            self.check_apps.start()
        if not self.check_guild_leave.is_running():
            self.check_guild_leave.start()


def setup(client):
    client.add_cog(CheckApps(client))
