from dataclasses import dataclass

from Helpers.database import DB


@dataclass(frozen=True)
class Alliance:
    id: int
    name: str
    prefix: str
    role_id: int
    rank: str
    rank_kind: str | None


def enabled_alliances() -> list[Alliance]:
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            """SELECT a.id, a.guild_name, a.guild_prefix, a.discord_role_id,
                      a.display_rank, r.kind
               FROM guild_alliances a
               LEFT JOIN rank_definitions r ON r.name = a.display_rank
               WHERE a.enabled = TRUE
               ORDER BY LOWER(a.guild_name), a.id"""
        )
        return [Alliance(*row) for row in db.cursor.fetchall()]
    finally:
        db.close()
