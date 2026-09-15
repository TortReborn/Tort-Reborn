"""The DB side of registering a (re)joining member, shared by /new_member,
the NewMember modal and auto-registration (TAQ-76).

Registration used to write discord_links (linked = TRUE, rank, wars_on_join,
honorific snapshot) from three near-identical statements. Now it:

1. upserts the identity (discord_id <-> uuid, ign),
2. sets the Discord rank,
3. attaches the account and wars-on-join to the open membership stint,
4. records any honorific role the member is holding that the ledger does
   not know about (the TAQ-51 snapshot, as a ledger row), and
5. clears applications.guild_leave_pending.

Blocking; takes a cursor and leaves the commit to the caller.
"""

from Helpers import honorifics as hon
from Helpers import roster
from Helpers.links import assert_uuid_free


def upsert_identity(cursor, *, discord_id, ign, uuid, linked_by=None):
    """discord_id <-> uuid, refusing a uuid another account holds."""
    assert_uuid_free(cursor, uuid, discord_id)
    cursor.execute(
        """INSERT INTO discord_links (discord_id, ign, uuid, linked_by)
           VALUES (%s, %s, %s::uuid, %s)
           ON CONFLICT (discord_id) DO UPDATE
           SET ign = EXCLUDED.ign,
               uuid = EXCLUDED.uuid""",
        (int(discord_id), ign, str(uuid), linked_by),
    )
    hon.refresh_discord_id(cursor, uuid, discord_id)


def set_rank(cursor, discord_id, rank):
    cursor.execute(
        "UPDATE discord_links SET rank = %s WHERE discord_id = %s",
        (rank, int(discord_id)),
    )
    return cursor.rowcount > 0


def record_registration(cursor, *, discord_id, ign, uuid, rank, wars_on_join,
                        held_role_names=(), actor_id=None):
    """Everything registration writes. Returns the honorific keys it recorded
    from the member's roles (usually none)."""
    upsert_identity(cursor, discord_id=discord_id, ign=ign, uuid=uuid, linked_by=actor_id)
    set_rank(cursor, discord_id, rank)
    roster.attach_identity(cursor, uuid, discord_id, wars_on_join=wars_on_join)
    recorded = hon.record_held_roles(
        cursor, uuid=uuid, ign=ign, discord_id=int(discord_id), role_names=held_role_names,
        actor_id=actor_id or 0, note='recorded at registration',
    )
    cursor.execute(
        """UPDATE applications SET guild_leave_pending = FALSE
            WHERE discord_id = %s::TEXT AND guild_leave_pending = TRUE""",
        (int(discord_id),),
    )
    return recorded
