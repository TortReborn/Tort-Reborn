"""Honored Fish / Retired Chief as a ledger (member_honorifics, TAQ-76).

The honorifics used to be a snapshot on discord_links (was_honored_fish /
was_retired_chief, TAQ-51) that recorded what roles a member held when they
re-registered, so removal could hand them back. That record lived on the
identity row, was overwritten on each rejoin, and vanished when the website
queue deleted the row. This ledger is keyed by the player's uuid, one row per
grant, closed (never deleted) on revoke, so "what were they before?" has an
answer after they have left Discord entirely.

Retired Chief implies Honored Fish; that stays a derivation in
Helpers/member_roles.removal_role_names, not a second row here.

Rows are keyed by uuid. A holder who has never linked a Minecraft account
(most pre-website ex-members) is recorded by discord_id alone; the uuid is
attached the moment they link (``attach_uuid``), and lookups by discord_id
find both shapes.

All DB functions are blocking and take a cursor; callers own the checkout and
the commit.
"""

HONORED_FISH = 'honored_fish'
RETIRED_CHIEF = 'retired_chief'
HONORIFICS = (HONORED_FISH, RETIRED_CHIEF)

# Ledger key -> Discord role name, and back.
ROLE_NAMES = {HONORED_FISH: 'Honored Fish', RETIRED_CHIEF: 'Retired Chief'}
BY_ROLE_NAME = {v: k for k, v in ROLE_NAMES.items()}
LABELS = {HONORED_FISH: 'Honored Fish', RETIRED_CHIEF: 'Retired Chief'}


def active_honorifics(cursor, *, uuid=None, discord_id=None):
    """(honored_fish, retired_chief) currently on record for a player, looked
    up by uuid or by the Discord account on the grant row."""
    if uuid is not None:
        cursor.execute(
            "SELECT honorific FROM member_honorifics WHERE uuid = %s::uuid AND revoked_at IS NULL",
            (str(uuid),),
        )
    elif discord_id is not None:
        cursor.execute(
            """SELECT mh.honorific FROM member_honorifics mh
                WHERE mh.revoked_at IS NULL
                  AND (mh.discord_id = %s
                       OR mh.uuid = (SELECT uuid FROM discord_links WHERE discord_id = %s))""",
            (int(discord_id), int(discord_id)),
        )
    else:
        return False, False
    held = {row[0] for row in cursor.fetchall()}
    return HONORED_FISH in held, RETIRED_CHIEF in held


def grant(cursor, *, uuid=None, ign, honorific, granted_by, discord_id=None, note=None):
    """Open a grant. Idempotent: an existing open row for the same player and
    honorific is returned as-is (its id, created=False). One of ``uuid`` /
    ``discord_id`` is required; with only a discord_id the row is keyed by
    the account until the player links."""
    if honorific not in HONORIFICS:
        raise ValueError(f"unknown honorific {honorific!r}")
    if uuid is None and discord_id is None:
        raise ValueError("grant needs a uuid or a discord_id")
    if uuid is not None:
        cursor.execute(
            "SELECT id FROM member_honorifics WHERE uuid = %s::uuid AND honorific = %s AND revoked_at IS NULL",
            (str(uuid), honorific),
        )
    else:
        cursor.execute(
            "SELECT id FROM member_honorifics WHERE discord_id = %s AND honorific = %s AND revoked_at IS NULL",
            (int(discord_id), honorific),
        )
    row = cursor.fetchone()
    if row:
        return row[0], False
    cursor.execute(
        """INSERT INTO member_honorifics (uuid, discord_id, ign, honorific, granted_by, note)
           VALUES (%s::uuid, %s, %s, %s, %s, %s) RETURNING id""",
        (str(uuid) if uuid is not None else None, discord_id, ign, honorific, int(granted_by), note),
    )
    return cursor.fetchone()[0], True


def revoke(cursor, *, uuid=None, discord_id=None, honorific, revoked_by, note=None):
    """Close the open grant (by uuid, or by discord_id for unlinked holders).
    Returns True if one was open."""
    if uuid is not None:
        where, params = "uuid = %s::uuid", (str(uuid),)
    elif discord_id is not None:
        where, params = "(discord_id = %s AND uuid IS NULL)", (int(discord_id),)
    else:
        return False
    cursor.execute(
        f"""UPDATE member_honorifics
               SET revoked_by = %s, revoked_at = NOW(),
                   note = CASE WHEN %s IS NULL THEN note ELSE COALESCE(note || ' | ', '') || %s END
             WHERE {where} AND honorific = %s AND revoked_at IS NULL""",
        (int(revoked_by), note, note, *params, honorific),
    )
    return cursor.rowcount > 0


def refresh_discord_id(cursor, uuid, discord_id):
    """A player linked a (new) Discord account: point their open grants at it
    so the rejoin lookup by discord_id keeps working, and adopt any grants
    that were recorded against the account before it had a uuid."""
    attach_uuid(cursor, discord_id, uuid)
    cursor.execute(
        "UPDATE member_honorifics SET discord_id = %s WHERE uuid = %s::uuid AND revoked_at IS NULL",
        (int(discord_id), str(uuid)),
    )
    return cursor.rowcount


def attach_uuid(cursor, discord_id, uuid):
    """Give discord-only rows their uuid once the account links. A row that
    would collide with an open uuid-keyed grant is closed as a duplicate
    instead."""
    cursor.execute(
        """UPDATE member_honorifics mh
              SET revoked_at = NOW(), revoked_by = 0,
                  note = COALESCE(note || ' | ', '') || 'merged into uuid-keyed grant'
            WHERE mh.discord_id = %s AND mh.uuid IS NULL AND mh.revoked_at IS NULL
              AND EXISTS (SELECT 1 FROM member_honorifics o
                           WHERE o.uuid = %s::uuid AND o.honorific = mh.honorific AND o.revoked_at IS NULL)""",
        (int(discord_id), str(uuid)),
    )
    cursor.execute(
        "UPDATE member_honorifics SET uuid = %s::uuid WHERE discord_id = %s AND uuid IS NULL",
        (str(uuid), int(discord_id)),
    )
    return cursor.rowcount


def history(cursor, *, uuid=None, discord_id=None):
    """Every grant for a player, newest first, as dicts."""
    if uuid is not None:
        where, params = "uuid = %s::uuid", (str(uuid),)
    elif discord_id is not None:
        where, params = "(discord_id = %s OR uuid = (SELECT uuid FROM discord_links WHERE discord_id = %s))", (int(discord_id), int(discord_id))
    else:
        return []
    cursor.execute(
        f"""SELECT id, honorific, granted_by, granted_at, revoked_by, revoked_at, note, ign, discord_id, uuid::text
              FROM member_honorifics
             WHERE {where}
             ORDER BY granted_at DESC""",
        params,
    )
    keys = ['id', 'honorific', 'granted_by', 'granted_at', 'revoked_by', 'revoked_at', 'note', 'ign', 'discord_id', 'uuid']
    return [dict(zip(keys, row)) for row in cursor.fetchall()]


def record_held_roles(cursor, *, uuid, ign, discord_id, role_names, actor_id, note):
    """Registration/backfill: any honorific role the member holds that the
    ledger does not know about becomes a grant. Returns the keys recorded."""
    recorded = []
    for role_name in set(role_names):
        key = BY_ROLE_NAME.get(role_name)
        if key is None:
            continue
        _, created = grant(cursor, uuid=uuid, ign=ign, honorific=key, granted_by=actor_id,
                           discord_id=discord_id, note=note)
        if created:
            recorded.append(key)
    return recorded
