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


def link_change_refusal(initiator_rank, target_rank):
    """Why the initiator may not (re)link or unlink the target, or None.

    The same rule /manage rank and /manage unlink already apply, in one
    place: the initiator needs a linked account with a recognised rank, and
    may only touch members ranked strictly below their own. A target with no
    rank on record is always fair game. Discord's manage_roles gate is a
    server-side default any admin can override per command, so the check has
    to live here too.
    """
    from Helpers.variables import discord_ranks

    if not initiator_rank or initiator_rank not in discord_ranks:
        return ':no_entry: You must link your account first.'
    if target_rank and target_rank in discord_ranks:
        ranks = list(discord_ranks)
        if ranks.index(target_rank) >= ranks.index(initiator_rank):
            return ':no_entry: You can only change links for members below your own rank.'
    return None


def registration_refusal(initiator_rank, target_rank, target_uuid, new_uuid, starting_rank):
    """Why the initiator may not register the target with this account, or None.

    Registering writes both identity and rank, so it takes the link rule above
    plus two more: it must not re-point an existing link, and it must not
    lower a rank. Those changes have their own commands, which show what they
    change. A rank outside discord_ranks, such as an ally rank, is left alone.
    """
    from Helpers.variables import discord_ranks

    refusal = link_change_refusal(initiator_rank, target_rank)
    if refusal:
        return refusal
    if target_uuid and str(target_uuid).replace('-', '').lower() != str(new_uuid).replace('-', '').lower():
        return ':no_entry: That member is already linked to a different Minecraft account. Use `/manage link` to change it.'
    if target_rank in discord_ranks:
        ranks = list(discord_ranks)
        if ranks.index(target_rank) > ranks.index(starting_rank):
            return (
                f':no_entry: That member already holds **{target_rank}**, above the starting rank of '
                f'**{starting_rank}**. Use `/manage rank`, or `/reset_roles` first if they left the guild.'
            )
    return None


def lookup_registration_refusal(cursor, initiator_id, target_id, new_uuid, starting_rank):
    """registration_refusal, reading both accounts from discord_links."""
    cursor.execute("SELECT rank FROM discord_links WHERE discord_id = %s", (int(initiator_id),))
    initiator = cursor.fetchone()
    cursor.execute("SELECT rank, uuid FROM discord_links WHERE discord_id = %s", (int(target_id),))
    target = cursor.fetchone()
    return registration_refusal(
        initiator[0] if initiator else None,
        target[0] if target else None,
        target[1] if target else None,
        new_uuid,
        starting_rank,
    )
