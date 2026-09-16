"""Guild-leave monitoring for accepted applicants who must leave another guild.

An accepted guild application whose applicant is still in another guild is
flagged guild_leave_pending; Tasks/check_apps.py polls the Wynncraft API
until the player shows up guildless, then pings the exec thread so they can
be invited. The flag is only cleared by auto-registration, so it outlives the
join whenever the player is registered by hand (/register, "new member"), or
leaves their old guild and joins TAq inside a single poll window. Months
later, the next time the API shows that player guildless -- they left or
were kicked from TAq -- the bot un-archives their old exec thread and
announces they "can now be invited" (TAQ-81).

Two guards close that: the poll only considers applicants without a live
discord_links row, and any pending flag on a joined applicant is cleared
instead of acted on.
"""

# A membership stint since the application means the applicant joined;
# nothing left to monitor. Same NOT EXISTS shape as Helpers/app_expiry.py
# (TAQ-76: membership history is membership_stints, not a flag on the
# identity row).
PENDING_LEAVE_SQL = """\
SELECT a.id, a.channel_id, a.thread_id, a.discord_id, a.answers->>'ign' AS ign
  FROM applications a
 WHERE a.status = 'accepted'
   AND a.application_type = 'guild'
   AND a.guild_leave_pending = TRUE
   AND NOT EXISTS (
     SELECT 1 FROM discord_links dl
     JOIN membership_stints ms ON ms.uuid = dl.uuid
     WHERE dl.discord_id = CAST(a.discord_id AS BIGINT)
       AND (ms.left_at IS NULL
            OR ms.left_at >= COALESCE(a.submitted_at, a.reviewed_at))
   )"""

CLEAR_STALE_LEAVE_SQL = """\
UPDATE applications a SET guild_leave_pending = FALSE
 WHERE a.guild_leave_pending = TRUE
   AND EXISTS (
     SELECT 1 FROM discord_links dl
     JOIN membership_stints ms ON ms.uuid = dl.uuid
     WHERE dl.discord_id = CAST(a.discord_id AS BIGINT)
       AND (ms.left_at IS NULL
            OR ms.left_at >= COALESCE(a.submitted_at, a.reviewed_at))
   )
RETURNING a.id, a.answers->>'ign'"""

CLEAR_LEAVE_SQL = "UPDATE applications SET guild_leave_pending = FALSE WHERE id = %s"

HOME_GUILD_NAME = "The Aquarium"


def clear_stale_pending_leaves(cursor):
    """Drop guild_leave_pending from applications whose applicant already joined.

    Returns the (app_id, ign) pairs that were cleared so the caller can log them.
    """
    cursor.execute(CLEAR_STALE_LEAVE_SQL)
    return cursor.fetchall()


def fetch_pending_leaves(cursor):
    """Accepted guild applications still waiting on the applicant to leave a guild."""
    cursor.execute(PENDING_LEAVE_SQL)
    return cursor.fetchall()


def current_guild_name(player_data):
    """Name of the guild a Wynncraft v3 player payload says the player is in, or None."""
    if not isinstance(player_data, dict):
        return None
    guild_info = player_data.get("guild")
    if not guild_info or not isinstance(guild_info, dict):
        return None
    return guild_info.get("name") or None


def classify_pending_leave(player_data):
    """Decide what the poll should do with one pending-leave applicant.

    'left'    -- guildless: clear the flag and notify the exec thread.
    'joined'  -- already in TAq: clear the flag quietly, there is nobody to invite.
    'waiting' -- still in another guild: check again next tick.
    'unknown' -- no usable player payload (API error): leave everything alone.
    """
    if not isinstance(player_data, dict):
        return "unknown"
    guild_info = player_data.get("guild")
    if guild_info is not None and not isinstance(guild_info, dict):
        return "unknown"  # malformed payload -- don't ping on it
    name = current_guild_name(player_data)
    if name is None:
        return "left"
    if name == HOME_GUILD_NAME:
        return "joined"
    return "waiting"
