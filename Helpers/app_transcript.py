import asyncio
import datetime
from io import BytesIO

import discord

from Helpers.database import DB
from Helpers.variables import APP_ARCHIVE_CHANNEL_NAME

CLOSED_POLL_STATUS = ":red_circle: Closed"
AUTO_TRANSCRIBE_DELAY = datetime.timedelta(days=3)


class TranscriptError(Exception):
    """Raised when a transcript cannot be posted.

    `retryable` distinguishes a transient/config problem (e.g. archive channel
    missing — retry later, do NOT advance the queue) from a permanent one
    (e.g. empty channel — advance past it).
    """

    def __init__(self, message, *, retryable):
        super().__init__(message)
        self.retryable = retryable


def classify_transcript_candidate(head, now, delay=AUTO_TRANSCRIBE_DELAY):
    """Decide what to do with the lowest un-transcribed ticket (`head`).

    `head` is a dict with keys `poll_status`, `channel_id`, `closed_at`,
    `effective_closed_at`, or None when there is no un-transcribed ticket.

    Returns:
        "none"       -- nothing to do
        "wait"       -- head not ready (still open, no close time, or delay not elapsed)
        "skip"       -- closed but no channel to transcribe; advance past it
        "transcribe" -- closed, has a channel, and the delay has elapsed
    """
    if head is None:
        return "none"
    # A ticket counts as closed if poll_status says so OR closed_at is stamped.
    # closed_at is written once when the channel enters Closed Applications and
    # never regresses, so it wins over poll_status, which later lifecycle writers
    # (e.g. auto-registration finishing after the close) can clobber.
    is_closed = (
        head.get("poll_status") == CLOSED_POLL_STATUS
        or head.get("closed_at") is not None
    )
    if not is_closed:
        return "wait"  # still open -> blocks higher tickets (strict order)
    if not head.get("channel_id"):
        return "skip"  # closed but nothing to transcribe
    closed_at = head.get("effective_closed_at")
    if closed_at is None:
        return "wait"  # closed with no timestamp -> don't transcribe blindly
    if closed_at + delay > now:
        return "wait"  # 3-day delay not elapsed
    return "transcribe"


def build_transcript_text(app, messages, channel_name):
    """Build the plain-text transcript body for an application channel. Pure."""
    ign = (app["answers"].get("ign") or "").strip()
    type_label = "Guild Member" if app["application_type"] == "guild" else "Community Member"
    created_at = messages[0].created_at.strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        "=== Application Transcript ===",
        f"Channel: #{channel_name}",
        f"Type: {type_label}",
        f"Applicant: {app['discord_username']} ({app['discord_id']})",
        f"IGN: {ign or 'N/A'}",
        f"Status: {app['status']}",
        f"Created: {created_at}",
        f"Messages: {len(messages)}",
        "=" * 40,
        "",
    ]

    for msg in messages:
        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        author = msg.author.display_name
        bot_tag = " [BOT]" if msg.author.bot else ""
        lines.append(f"[{timestamp}] {author}{bot_tag}")

        if msg.content:
            lines.append(msg.content)

        for embed in msg.embeds:
            if embed.title:
                lines.append(f"  [Embed: {embed.title}]")
            if embed.description:
                lines.append(f"  {embed.description}")
            for field in embed.fields:
                lines.append(f"  {field.name}: {field.value}")

        for att in msg.attachments:
            lines.append(f"  [Attachment: {att.filename} — {att.url}]")

        lines.append("")

    return "\n".join(lines)


async def post_transcript(client, channel, app):
    """Transcribe `channel` to the archive channel. Raises TranscriptError on
    failure; returns True on success. Does not touch the DB or the source channel."""
    guild = channel.guild
    archive_chan = discord.utils.get(guild.text_channels, name=APP_ARCHIVE_CHANNEL_NAME)
    if not archive_chan:
        raise TranscriptError(
            f"Archive channel `#{APP_ARCHIVE_CHANNEL_NAME}` not found.", retryable=True
        )

    messages = []
    async for msg in channel.history(limit=500, oldest_first=True):
        messages.append(msg)

    if not messages:
        raise TranscriptError("No messages found in this channel.", retryable=False)

    ign = (app["answers"].get("ign") or "").strip()
    type_label = "Guild Member" if app["application_type"] == "guild" else "Community Member"
    transcript_text = build_transcript_text(app, messages, channel.name)

    embed = discord.Embed(title=f"Transcript: #{channel.name}", color=0x2F3136)
    embed.add_field(name="Type", value=type_label, inline=True)
    embed.add_field(name="Status", value=app["status"].title(), inline=True)
    embed.add_field(name="Applicant", value=f"<@{app['discord_id']}>", inline=True)
    if ign:
        embed.add_field(name="IGN", value=discord.utils.escape_markdown(ign), inline=True)
    embed.add_field(name="Messages", value=str(len(messages)), inline=True)

    buf = BytesIO(transcript_text.encode("utf-8"))
    file = discord.File(buf, filename=f"transcript-{channel.name}.txt")
    await archive_chan.send(embed=embed, file=file)
    return True


def stamp_transcribed(app_id):
    """Mark an application transcribed so the auto-transcribe queue advances past it."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "UPDATE applications SET transcribed_at = NOW() WHERE id = %s",
            (app_id,),
        )
        db.connection.commit()
    finally:
        db.close()


def stamp_channel_deleted(app_id):
    """Mark a transcribed application's channel as deleted so cleanup won't retry it."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "UPDATE applications SET channel_deleted_at = NOW() WHERE id = %s",
            (app_id,),
        )
        db.connection.commit()
    finally:
        db.close()


async def delete_transcribed_channel(client, app_id, channel_id):
    """Delete a transcribed ticket's channel, then stamp channel_deleted_at.

    Idempotent: a channel that is already gone (NotFound) is treated as deleted and
    still stamped. Transient errors (e.g. Forbidden) propagate so the caller can log
    and retry on a later pass — channel_deleted_at is only stamped once the channel
    is confirmed gone.
    """
    channel = client.get_channel(channel_id) if channel_id else None
    if channel is None and channel_id:
        try:
            channel = await client.fetch_channel(channel_id)
        except discord.NotFound:
            channel = None  # already gone -> treat as deleted
    if channel is not None:
        await channel.delete(reason="Application transcribed and archived")
    await asyncio.to_thread(stamp_channel_deleted, app_id)
