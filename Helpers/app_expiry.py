"""Retire accepted guild applications whose ticket closed before the player joined.

An accepted guild applicant has joined once a membership stint opened for
their linked uuid (TAQ-76). If their ticket is closed while no such stint
exists, they ghosted — the application would otherwise stay
'accepted' forever and be counted as a pending join by the website
(TAQ-77), eating an open guild slot that doesn't exist.
"""

# Guarded in SQL as well as in Python so a race with the roster sync can
# never expire a player who did join.
EXPIRE_UNJOINED_SQL = """\
UPDATE applications a SET status = 'expired'
 WHERE a.id = %s
   AND a.status = 'accepted'
   AND a.application_type = 'guild'
   AND NOT EXISTS (
     SELECT 1 FROM discord_links dl
     JOIN membership_stints ms ON ms.uuid = dl.uuid
     WHERE dl.discord_id = CAST(a.discord_id AS BIGINT)
       AND (ms.left_at IS NULL
            OR ms.joined_at >= COALESCE(a.submitted_at, a.reviewed_at, ms.joined_at) - INTERVAL '7 days')
   )"""


def expire_if_never_joined(cursor, app_id, app_type, status):
    """Expire an accepted guild application if its applicant never joined.

    Call when the application's ticket is closed. Returns True if the
    application was expired, False if it was left untouched (wrong
    type/status, or the applicant has joined).
    """
    if status != "accepted" or app_type != "guild":
        return False
    cursor.execute(EXPIRE_UNJOINED_SQL, (app_id,))
    return cursor.rowcount > 0
