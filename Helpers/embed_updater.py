import asyncio

from Helpers.database import DB
from Helpers.variables import member_app_channel


async def update_poll_embed(client, channel_id: int, new_status: str, colour: int):
    """Edit the original poll embed's Status field and colour in the exec channel."""

    def _fetch_poll_id(cid):
        db = DB()
        try:
            db.connect()
            db.cursor.execute(
                "SELECT poll_message_id FROM new_app WHERE channel = %s", (cid,)
            )
            row = db.cursor.fetchone()
            return row[0] if row else None
        finally:
            db.close()

    def _update_db_status(cid, status):
        db = DB()
        try:
            db.connect()
            db.cursor.execute(
                "UPDATE new_app SET status = %s WHERE channel = %s", (status, cid)
            )
            db.connection.commit()
        finally:
            db.close()

    poll_message_id = await asyncio.to_thread(_fetch_poll_id, channel_id)
    if not poll_message_id:
        return

    exec_chan = client.get_channel(member_app_channel)
    if not exec_chan:
        return

    try:
        poll_msg = await exec_chan.fetch_message(poll_message_id)
    except Exception:
        return

    if not poll_msg.embeds:
        return

    embed = poll_msg.embeds[0].copy()
    embed.colour = colour

    for i, field in enumerate(embed.fields):
        if field.name == "Status":
            embed.set_field_at(i, name="Status", value=new_status, inline=True)
            break

    await asyncio.to_thread(_update_db_status, channel_id, new_status)
    await poll_msg.edit(embed=embed)


async def update_web_poll_embed(client, channel_id: int, new_status: str, colour: int):
    """Edit the poll embed for a website application (applications table)."""

    def _fetch_poll_id(cid):
        db = DB()
        try:
            db.connect()
            db.cursor.execute(
                "SELECT poll_message_id FROM applications WHERE channel_id = %s", (cid,)
            )
            row = db.cursor.fetchone()
            return row[0] if row else None
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

    poll_message_id = await asyncio.to_thread(_fetch_poll_id, channel_id)
    if not poll_message_id:
        return

    exec_chan = client.get_channel(member_app_channel)
    if not exec_chan:
        return

    try:
        poll_msg = await exec_chan.fetch_message(poll_message_id)
    except Exception:
        return

    if not poll_msg.embeds:
        return

    embed = poll_msg.embeds[0].copy()
    embed.colour = colour

    for i, field in enumerate(embed.fields):
        if field.name == "Status":
            embed.set_field_at(i, name="Status", value=new_status, inline=True)
            break

    await asyncio.to_thread(_update_db_status, channel_id, new_status)
    await poll_msg.edit(embed=embed)
