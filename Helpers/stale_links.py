"""Members who left the in-game guild but still carry a Discord rank.

With guild_roster as the membership source (TAQ-76) this is one query:
discord_links rows holding a member rank whose uuid is not on the roster.
Those are exactly the people waiting for a roles reset -- the in-game diff
closed their stint, nobody has pressed the button yet.
"""

from Helpers.variables import discord_ranks

STALE_LINKS_SQL = """\
SELECT dl.discord_id, dl.ign, dl.uuid::text, dl.rank
  FROM discord_links dl
 WHERE dl.rank IS NOT NULL
   AND dl.rank = ANY(%s)
   AND NOT EXISTS (SELECT 1 FROM guild_roster gr WHERE gr.uuid = dl.uuid)"""


def fetch_stale_taq_links(cursor):
    cursor.execute(STALE_LINKS_SQL, (list(discord_ranks),))
    return cursor.fetchall()


def stale_taq_links(rows, discord_member_ids=None):
    """Shape the rows for display / reset, optionally keeping only accounts
    still present in the Discord server (``discord_member_ids``). Sorted by
    rank (lowest first), then ign."""
    member_ids = {int(user_id) for user_id in discord_member_ids} if discord_member_ids is not None else None
    rank_order = {rank: index for index, rank in enumerate(discord_ranks)}
    stale = []
    for discord_id, ign, uuid, rank in rows:
        if rank not in discord_ranks:
            continue
        if member_ids is not None and int(discord_id) not in member_ids:
            continue
        stale.append({
            "discord_id": int(discord_id),
            "ign": ign or "Unknown",
            "uuid": str(uuid),
            "rank": rank,
        })
    return sorted(stale, key=lambda row: (rank_order[row["rank"]], row["ign"].lower(), row["discord_id"]))


def render_stale_taq_links(rows):
    if not rows:
        return "No stale linked members found."
    lines = [f"Found {len(rows)} stale linked member(s):"]
    lines += [
        f"{row['ign']} | {row['rank']} | Discord: {row['discord_id']} | UUID: {row['uuid']}"
        for row in rows
    ]
    return "\n".join(lines)


def split_stale_report(text, limit=1900):
    chunks = []
    current = []
    size = 0
    for line in text.splitlines():
        line_size = len(line) + 1
        if current and size + line_size > limit:
            chunks.append("\n".join(current))
            current = []
            size = 0
        current.append(line)
        size += line_size
    if current:
        chunks.append("\n".join(current))
    return chunks
