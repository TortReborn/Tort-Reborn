import asyncio

from Helpers.database import DB
from Helpers.poll_edit import safe_edit_poll
from Helpers.variables import MEMBER_APP_CHANNEL_ID


async def update_web_poll_embed(client, channel_id: int, new_status: str, colour: int):
    """Edit the poll embed for a website application (applications table)."""

    def _fetch_poll_data(cid):
        db = DB()
        try:
            db.connect()
            db.cursor.execute(
                "SELECT id, poll_message_id FROM applications WHERE channel_id = %s", (cid,)
            )
            row = db.cursor.fetchone()
            return (row[0], row[1]) if row else (None, None)
        finally:
            db.close()

    def _update_db_status(cid, status):
        db = DB()
        try:
            db.connect()
            db.cursor.execute(
                "UPDATE applications SET poll_status = %s WHERE channel_id = %s", (status, cid)
            )
            db.connection.commit()
        finally:
            db.close()

    def _get_vote_counts(app_id):
        db = DB()
        try:
            db.connect()
            db.cursor.execute(
                """SELECT
                    COUNT(*) FILTER (WHERE vote = 'accept'),
                    COUNT(*) FILTER (WHERE vote = 'deny'),
                    COUNT(*) FILTER (WHERE vote = 'abstain')
                FROM application_votes WHERE application_id = %s""",
                (app_id,)
            )
            row = db.cursor.fetchone()
            if row:
                return {"accept": row[0], "deny": row[1], "abstain": row[2]}
            return {"accept": 0, "deny": 0, "abstain": 0}
        finally:
            db.close()

    app_id, poll_message_id = await asyncio.to_thread(_fetch_poll_data, channel_id)
    if not poll_message_id:
        return

    exec_chan = client.get_channel(MEMBER_APP_CHANNEL_ID)
    if not exec_chan:
        return

    # Pre-fetch vote counts outside the lock to minimize lock hold time
    counts = None
    if app_id:
        counts = await asyncio.to_thread(_get_vote_counts, app_id)

    def _modify(embed):
        embed.colour = colour
        for i, field in enumerate(embed.fields):
            if field.name == "Status":
                embed.set_field_at(i, name="Status", value=new_status, inline=True)
                break

        if counts:
            total = counts["accept"] + counts["deny"] + counts["abstain"]
            if total > 0:
                vote_text = f"Accept: {counts['accept']} | Deny: {counts['deny']} | Abstain: {counts['abstain']}"
                found_votes = False
                for i, field in enumerate(embed.fields):
                    if field.name == "Votes":
                        embed.set_field_at(i, name="Votes", value=vote_text, inline=False)
                        found_votes = True
                        break
                if not found_votes:
                    embed.add_field(name="Votes", value=vote_text, inline=False)

    await asyncio.to_thread(_update_db_status, channel_id, new_status)
    await safe_edit_poll(exec_chan, poll_message_id, modify_embed=_modify, include_view=False)
