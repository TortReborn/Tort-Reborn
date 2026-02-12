import datetime

from discord.ext import tasks, commands

from Helpers.database import DB
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

    @commands.Cog.listener()
    async def on_ready(self):
        pass
        if not self.check_apps.is_running():
            self.check_apps.start()


def setup(client):
    client.add_cog(CheckApps(client))
