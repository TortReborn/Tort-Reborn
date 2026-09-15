"""Colours Discord renders, mirrored into the database for the Verge mod.

Every call here blocks; use asyncio.to_thread from the event loop.
"""

from dataclasses import dataclass

from Helpers.database import DB, get_current_guild_data_with_db
from Helpers.variables import discord_ranks


@dataclass(frozen=True)
class MemberColors:
    primary: int | None = None
    secondary: int | None = None
    tertiary: int | None = None
    role_name: str | None = None

    def as_row(self):
        return (self.primary, self.secondary, self.tertiary, self.role_name)


NO_COLORS = MemberColors()


def rendered_colors(member) -> MemberColors:
    for role in reversed(member.roles[1:]):
        colours = role.colours
        if colours.primary.value:
            return MemberColors(
                primary=colours.primary.value,
                secondary=colours.secondary.value if colours.secondary else None,
                tertiary=colours.tertiary.value if colours.tertiary else None,
                role_name=role.name[:100],
            )
    return NO_COLORS


def write_member_colors(updates: list[tuple[int, MemberColors]]) -> int:
    rows = [(*colors.as_row(), discord_id) for discord_id, colors in updates]
    if not rows:
        return 0

    db = DB()
    db.connect()
    try:
        db.cursor.executemany(
            "UPDATE discord_links"
            " SET color_primary = %s, color_secondary = %s, color_tertiary = %s,"
            "     color_role_name = %s, colors_synced_at = NOW()"
            " WHERE discord_id = %s",
            rows,
        )
        db.connection.commit()
    finally:
        db.close()
    return len(rows)


def stored_colors() -> dict[int, MemberColors]:
    db = DB()
    db.connect()
    try:
        guild_uuids = _current_guild_uuids(db)
        db.cursor.execute(
            "SELECT discord_id, uuid::text, color_primary, color_secondary, color_tertiary, color_role_name"
            " FROM discord_links WHERE uuid IS NOT NULL"
        )
        rows = db.cursor.fetchall()
    finally:
        db.close()

    # Unreadable roster falls back to everyone, never to none.
    if not guild_uuids:
        return {int(row[0]): MemberColors(row[2], row[3], row[4], row[5]) for row in rows}

    return {
        int(row[0]): MemberColors(row[2], row[3], row[4], row[5])
        for row in rows
        if _uuid_key(row[1]) in guild_uuids
    }


def _current_guild_uuids(db) -> set[str]:
    try:
        guild = get_current_guild_data_with_db(db)
    except Exception:
        return set()
    members = guild.get("members") if isinstance(guild, dict) else None
    if not isinstance(members, list):
        return set()
    return {
        key
        for key in (_uuid_key(m.get("uuid")) for m in members if isinstance(m, dict))
        if key
    }


def _uuid_key(value) -> str:
    return str(value).replace("-", "").lower() if value else ""


def rank_role_name(rank_key: str) -> str | None:
    rank = discord_ranks.get(rank_key)
    return rank["roles"][0] if rank else None


def rank_role_colors(guild) -> dict[str, MemberColors]:
    by_name = {role.name: role for role in guild.roles}
    out = {}
    for rank_key in discord_ranks:
        role = by_name.get(rank_role_name(rank_key))
        if role is None or not role.colours.primary.value:
            continue
        out[rank_key] = MemberColors(
            primary=role.colours.primary.value,
            secondary=role.colours.secondary.value if role.colours.secondary else None,
            tertiary=role.colours.tertiary.value if role.colours.tertiary else None,
            role_name=role.name[:100],
        )
    return out


def write_rank_colors(updates: dict[str, MemberColors]) -> int:
    rows = [(rank_key, *colors.as_row()) for rank_key, colors in updates.items()]
    if not rows:
        return 0

    db = DB()
    db.connect()
    try:
        db.cursor.executemany(
            "INSERT INTO rank_role_colors"
            " (rank_key, color_primary, color_secondary, color_tertiary, role_name, synced_at)"
            " VALUES (%s, %s, %s, %s, %s, NOW())"
            " ON CONFLICT (rank_key) DO UPDATE SET"
            "   color_primary = EXCLUDED.color_primary,"
            "   color_secondary = EXCLUDED.color_secondary,"
            "   color_tertiary = EXCLUDED.color_tertiary,"
            "   role_name = EXCLUDED.role_name,"
            "   synced_at = NOW()",
            rows,
        )
        db.connection.commit()
    finally:
        db.close()
    return len(rows)


def stored_rank_colors() -> dict[str, MemberColors]:
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "SELECT rank_key, color_primary, color_secondary, color_tertiary, role_name"
            " FROM rank_role_colors"
        )
        return {
            row[0]: MemberColors(row[1], row[2], row[3], row[4])
            for row in db.cursor.fetchall()
        }
    finally:
        db.close()
