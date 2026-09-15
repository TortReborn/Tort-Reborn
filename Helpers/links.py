"""Guards for discord_links writes.

discord_links is the identity table: one Discord account <-> one Minecraft
account. The database enforces both directions (discord_id is the primary
key, discord_links_uuid_uq covers the uuid). Duplicate rows for a uuid would
fan out every uuid join in the bot and website -- duplicating leaderboard
rows and double-counting raid points -- so the write paths check up front and
tell the invoker who holds the account, instead of failing on the constraint.

Until the TAQ-76 overhaul the uniqueness was partial (``WHERE linked``) and
"unlinked history" rows could share a uuid. There is no such state any more:
a row exists iff the identity is established, and unlinking is a delete.
"""


class LinkConflictError(Exception):
    """Raised when a uuid is already linked to a different Discord account."""

    def __init__(self, uuid, other_discord_id, other_ign):
        self.uuid = uuid
        self.other_discord_id = other_discord_id
        self.other_ign = other_ign
        super().__init__(
            f"Minecraft account {other_ign} ({uuid}) is already linked to "
            f"Discord account {other_discord_id}"
        )

    def user_message(self):
        """Standard operator-facing explanation for command responses."""
        return (
            f":no_entry: **{self.other_ign}** is already linked to "
            f"<@{self.other_discord_id}>. Unlink that account first "
            f"(`/manage unlink`) before linking it elsewhere."
        )


def find_linked_uuid_conflict(cursor, uuid, discord_id):
    """Return (discord_id, ign) of a *different* Discord account holding this
    uuid, or None."""
    if not uuid:
        return None
    cursor.execute(
        "SELECT discord_id, ign FROM discord_links"
        " WHERE uuid = %s::uuid AND discord_id <> %s"
        " LIMIT 1",
        (str(uuid), int(discord_id)),
    )
    return cursor.fetchone()


def assert_uuid_free(cursor, uuid, discord_id):
    """Raise LinkConflictError if uuid is linked to a different Discord account."""
    conflict = find_linked_uuid_conflict(cursor, uuid, discord_id)
    if conflict:
        raise LinkConflictError(uuid, conflict[0], conflict[1])


def assert_row_linkable(cursor, discord_id):
    """Transitional: the application/registration callers still flip rows to
    linked = TRUE until the next commits switch them to upsert_identity."""
    cursor.execute("SELECT uuid FROM discord_links WHERE discord_id = %s", (discord_id,))
    row = cursor.fetchone()
    if row and row[0]:
        assert_uuid_free(cursor, row[0], discord_id)
