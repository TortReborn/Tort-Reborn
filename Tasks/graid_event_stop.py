# Commands/graid_event_stop.py
from discord.ext import commands, tasks

from Helpers.database import DB

def _db():
    db = DB(); db.connect(); return db

class GraidAutoStop(commands.Cog):
    """Background task that auto-stops any active GRAID whose end_ts has passed."""
    def __init__(self, client):
        self.client = client
        self._task.start()

    def cog_unload(self):
        if self._task.is_running():
            self._task.cancel()

    @tasks.loop(minutes=1)
    async def _task(self):
        """
        Runs every minute. If there are active events with end_ts in the past,
        mark them inactive. We do the comparison in the DB (now() vs end_ts).
        """
        db = _db()
        try:
            cur = db.cursor
            cur.execute(
                """
                UPDATE graid_events
                SET active = FALSE
                WHERE active = TRUE
                  AND end_ts IS NOT NULL
                  AND now() >= end_ts
                RETURNING id, title, end_ts
                """
            )
            rows = cur.fetchall()  # events auto-stopped (usually 0 or 1)
            if rows:
                db.connection.commit()
                for eid, title, ts in rows:
                    print(f"[graid_autostop] auto-stopped id={eid} title={title!r} at end_ts={ts.isoformat()}")
        except Exception as e:
            print(f"[graid_autostop] error: {e!r}")
        finally:
            db.close()

    @_task.before_loop
    async def _wait_until_ready(self):
        # Ensure the bot is fully ready before the first run
        await self.client.wait_until_ready()

def setup(client):
    client.add_cog(GraidAutoStop(client))
