import datetime
import re
from collections.abc import Iterable

import discord

from Helpers.database import DB
from Helpers.functions import getPlayerUUID

PARTY_COUNT = 5
PARTY_SIZE = 10
BOARD_CLOSE_DELAY = datetime.timedelta()
VALID_SPECIAL_ROLES = {"healer", "guardian"}

_IGN_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
_WORLD_RE = re.compile(r"^[A-Za-z]{1,4}[0-9]{1,3}$")


class AnnihilationPartyError(ValueError):
    """A party operation failed for a reason safe to show to a Discord user."""


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def ensure_utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc)


def normalize_ign(value: str) -> str:
    value = (value or "").strip()
    if not _IGN_RE.fullmatch(value):
        raise AnnihilationPartyError(
            "Minecraft names must contain only letters, numbers, or underscores and be at most 16 characters."
        )
    return value


def normalize_build(value: str) -> str:
    value = " ".join((value or "").split())
    if not value:
        raise AnnihilationPartyError("Please provide your build.")
    if len(value) > 50:
        raise AnnihilationPartyError("Builds must be at most 50 characters")
    return value


def normalize_notes(value: str | None) -> str | None:
    value = " ".join((value or "").split())
    if not value:
        return None
    if len(value) > 50:
        raise AnnihilationPartyError("Notes must be at most 50 characters")
    return value


def normalize_world(value: str) -> str:
    value = (value or "").strip().upper()
    if not _WORLD_RE.fullmatch(value):
        raise AnnihilationPartyError(
            "Enter a Wynncraft world such as `WC12`, `EU3`, `NA8`, or `AS2`."
        )
    return value


def choose_available_slot(occupied_slots: Iterable[int]) -> int | None:
    occupied = {int(slot) for slot in occupied_slots}
    for slot in range(1, PARTY_SIZE + 1):
        if slot not in occupied:
            return slot
    return None


def _db() -> DB:
    db = DB()
    db.connect()
    return db


def _event_row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "schedule_at": ensure_utc(row[1]),
        "closes_at": ensure_utc(row[2]),
        "guild_id": row[3],
        "channel_id": row[4],
        "message_id": row[5],
        "thread_id": row[6],
        "status": row[7],
        "closed_at": ensure_utc(row[8]) if row[8] else None,
        "discord_closed_at": ensure_utc(row[9]) if row[9] else None,
    }


_EVENT_COLUMNS = """
    id, schedule_at, closes_at, guild_id, channel_id, message_id, thread_id,
    status, closed_at, discord_closed_at
"""


def ensure_event(
    schedule_at: datetime.datetime,
    guild_id: int,
    channel_id: int,
) -> dict:
    schedule_at = ensure_utc(schedule_at)
    closes_at = schedule_at + BOARD_CLOSE_DELAY
    db = _db()
    try:
        db.cursor.execute(
            f"""
            INSERT INTO annihilation_party_events
                (schedule_at, closes_at, guild_id, channel_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (schedule_at) DO UPDATE SET
                closes_at = EXCLUDED.closes_at,
                guild_id = EXCLUDED.guild_id,
                channel_id = EXCLUDED.channel_id,
                updated_at = NOW()
            RETURNING {_EVENT_COLUMNS}
            """,
            (schedule_at, closes_at, guild_id, channel_id),
        )
        event = _event_row_to_dict(db.cursor.fetchone())
        db.cursor.executemany(
            """
            INSERT INTO annihilation_parties (event_id, party_number)
            VALUES (%s, %s)
            ON CONFLICT (event_id, party_number) DO NOTHING
            """,
            [(event["id"], party_number) for party_number in range(1, PARTY_COUNT + 1)],
        )
        db.connection.commit()
        return event
    finally:
        db.close()


def set_board_message(event_id: int, message_id: int) -> None:
    db = _db()
    try:
        db.cursor.execute(
            """
            UPDATE annihilation_party_events
            SET message_id = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (message_id, event_id),
        )
        db.connection.commit()
    finally:
        db.close()


def set_board_thread(event_id: int, thread_id: int) -> None:
    db = _db()
    try:
        db.cursor.execute(
            """
            UPDATE annihilation_party_events
            SET thread_id = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (thread_id, event_id),
        )
        db.connection.commit()
    finally:
        db.close()


def get_linked_account(discord_id: int) -> dict | None:
    db = _db()
    try:
        db.cursor.execute(
            "SELECT ign, uuid FROM discord_links WHERE discord_id = %s",
            (discord_id,),
        )
        row = db.cursor.fetchone()
        if not row:
            return None
        return {"ign": row[0], "uuid": str(row[1]) if row[1] else None}
    finally:
        db.close()


def _load_event(where_sql: str, params: tuple) -> dict | None:
    db = _db()
    try:
        db.cursor.execute(
            f"SELECT {_EVENT_COLUMNS} FROM annihilation_party_events WHERE {where_sql}",
            params,
        )
        row = db.cursor.fetchone()
        if not row:
            return None
        event = _event_row_to_dict(row)

        db.cursor.execute(
            """
            SELECT party_number, world
            FROM annihilation_parties
            WHERE event_id = %s
            ORDER BY party_number
            """,
            (event["id"],),
        )
        parties = {
            party_number: {
                "party_number": party_number,
                "world": world,
                "members": [],
            }
            for party_number, world in db.cursor.fetchall()
        }
        for party_number in range(1, PARTY_COUNT + 1):
            parties.setdefault(
                party_number,
                {"party_number": party_number, "world": None, "members": []},
            )

        db.cursor.execute(
            """
            SELECT
                id, discord_id, ign, uuid, party_number, slot_number, build,
                combat_role, bringing_scrolls, notes, party_joined_at,
                created_at, updated_at
            FROM annihilation_party_members
            WHERE event_id = %s
            ORDER BY party_number, party_joined_at, id
            """,
            (event["id"],),
        )
        for member_row in db.cursor.fetchall():
            member = {
                "id": member_row[0],
                "discord_id": member_row[1],
                "ign": member_row[2],
                "uuid": str(member_row[3]) if member_row[3] else None,
                "party_number": member_row[4],
                "slot_number": member_row[5],
                "build": member_row[6],
                "combat_role": member_row[7],
                "bringing_scrolls": member_row[8],
                "notes": member_row[9],
                "party_joined_at": ensure_utc(member_row[10]),
                "created_at": ensure_utc(member_row[11]),
                "updated_at": ensure_utc(member_row[12]),
                "is_leader": False,
            }
            parties[member["party_number"]]["members"].append(member)

        for party in parties.values():
            if party["members"]:
                party["members"][0]["is_leader"] = True

        event["parties"] = parties
        return event
    finally:
        db.close()


def get_event(event_id: int) -> dict | None:
    return _load_event("id = %s", (event_id,))


def get_event_by_message(message_id: int) -> dict | None:
    return _load_event("message_id = %s", (message_id,))


def get_member(event_id: int, member_id: int) -> dict | None:
    event = get_event(event_id)
    if not event:
        return None
    for party in event["parties"].values():
        for member in party["members"]:
            if member["id"] == member_id:
                return member
    return None


def get_member_for_discord(event_id: int, discord_id: int) -> dict | None:
    event = get_event(event_id)
    if not event:
        return None
    for party in event["parties"].values():
        for member in party["members"]:
            if member["discord_id"] == discord_id:
                return member
    return None


def _lock_open_event(db: DB, event_id: int) -> None:
    db.cursor.execute(
        """
        SELECT status, closes_at
        FROM annihilation_party_events
        WHERE id = %s
        FOR UPDATE
        """,
        (event_id,),
    )
    row = db.cursor.fetchone()
    if not row:
        raise AnnihilationPartyError("This Annihilation party board no longer exists.")
    if row[0] != "active" or ensure_utc(row[1]) <= utc_now():
        raise AnnihilationPartyError("Sign-ups for this Annihilation event are closed")


def _linked_uuid_for_ign(db: DB, discord_id: int, ign: str) -> str | None:
    db.cursor.execute(
        "SELECT ign, uuid FROM discord_links WHERE discord_id = %s",
        (discord_id,),
    )
    row = db.cursor.fetchone()
    if row and row[1] and row[0].casefold() == ign.casefold():
        return str(row[1])
    return None


def _linked_account_for_discord(db: DB, discord_id: int) -> dict | None:
    db.cursor.execute(
        "SELECT ign, uuid FROM discord_links WHERE discord_id = %s",
        (discord_id,),
    )
    row = db.cursor.fetchone()
    if not row:
        return None
    return {"ign": row[0], "uuid": str(row[1]) if row[1] else None}


def _validate_party_values(
    build: str,
    party_number: int,
    combat_role: str | None,
    notes: str | None,
) -> tuple[str, int, str | None, str | None]:
    build = normalize_build(build)
    notes = normalize_notes(notes)
    combat_role = (combat_role or "").strip().lower() or None
    try:
        party_number = int(party_number)
    except (TypeError, ValueError) as exc:
        raise AnnihilationPartyError("Choose a valid party") from exc
    if not 1 <= party_number <= PARTY_COUNT:
        raise AnnihilationPartyError("Choose a valid party")
    if combat_role is not None and combat_role not in VALID_SPECIAL_ROLES:
        raise AnnihilationPartyError("Choose Healer, Guardian, or leave Special Role blank.")
    return build, party_number, combat_role, notes


def _validate_entry_values(
    ign: str,
    build: str,
    party_number: int,
    combat_role: str | None,
    notes: str | None,
) -> tuple[str, str, int, str | None, str | None]:
    ign = normalize_ign(ign)
    build, party_number, combat_role, notes = _validate_party_values(
        build, party_number, combat_role, notes
    )
    return ign, build, party_number, combat_role, notes


def _assert_unique_identity(
    db: DB,
    event_id: int,
    discord_id: int,
    ign: str,
    *,
    uuid: str | None = None,
    exclude_member_id: int | None = None,
) -> None:
    # Identity is the Minecraft uuid when known — the name comparison alone
    # lets a renamed player hold two slots (or be blocked by someone's old name).
    identity_sql = "(discord_id = %s OR LOWER(ign) = LOWER(%s))"
    params: list = [event_id, discord_id, ign]
    if uuid:
        identity_sql = "(discord_id = %s OR LOWER(ign) = LOWER(%s) OR uuid = %s)"
        params.append(uuid)
    exclude_sql = ""
    if exclude_member_id is not None:
        exclude_sql = "AND id <> %s"
        params.append(exclude_member_id)
    db.cursor.execute(
        f"""
        SELECT discord_id, ign
        FROM annihilation_party_members
        WHERE event_id = %s
          AND {identity_sql}
          {exclude_sql}
        LIMIT 1
        """,
        tuple(params),
    )
    row = db.cursor.fetchone()
    if not row:
        return
    if row[0] == discord_id:
        raise AnnihilationPartyError(
            "You already have an entry. Use **Modify entry** instead"
        )
    # A uuid match can surface a row under a different (older) name — report
    # the name actually on the board, not the one that was typed.
    raise AnnihilationPartyError(f"`{row[1]}` is already registered for this event.")


def _available_slot(db: DB, event_id: int, party_number: int) -> int:
    db.cursor.execute(
        """
        SELECT slot_number
        FROM annihilation_party_members
        WHERE event_id = %s AND party_number = %s
        ORDER BY slot_number
        """,
        (event_id, party_number),
    )
    slot = choose_available_slot(row[0] for row in db.cursor.fetchall())
    if slot is None:
        raise AnnihilationPartyError(f"Party {party_number} is already full")
    return slot


def add_member(
    event_id: int,
    discord_id: int,
    ign: str,
    build: str,
    party_number: int,
    combat_role: str | None,
    bringing_scrolls: bool,
    notes: str | None,
) -> int:
    ign, build, party_number, combat_role, notes = _validate_entry_values(
        ign, build, party_number, combat_role, notes
    )
    # Resolve the typed name's Minecraft uuid before taking the event lock —
    # no HTTP while holding the transaction. The discord link is preferred
    # inside the transaction; Mojang covers alts and unlinked names, so the
    # row is never silently written without an identity.
    player_data = getPlayerUUID(ign)
    fallback_uuid = player_data[1] if player_data else None
    db = _db()
    try:
        _lock_open_event(db, event_id)
        uuid = _linked_uuid_for_ign(db, discord_id, ign) or fallback_uuid
        _assert_unique_identity(db, event_id, discord_id, ign, uuid=uuid)
        slot_number = _available_slot(db, event_id, party_number)
        db.cursor.execute(
            """
            INSERT INTO annihilation_party_members (
                event_id, discord_id, ign, uuid, party_number, slot_number,
                build, combat_role, bringing_scrolls, notes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                event_id,
                discord_id,
                ign,
                uuid,
                party_number,
                slot_number,
                build,
                combat_role,
                bool(bringing_scrolls),
                notes,
            ),
        )
        member_id = db.cursor.fetchone()[0]
        db.connection.commit()
        return member_id
    except Exception:
        db.connection.rollback()
        raise
    finally:
        db.close()


def update_member(
    event_id: int,
    actor_discord_id: int,
    member_id: int,
    can_manage: bool,
    build: str,
    party_number: int,
    combat_role: str | None,
    bringing_scrolls: bool,
    notes: str | None,
) -> None:
    build, party_number, combat_role, notes = _validate_party_values(
        build, party_number, combat_role, notes
    )
    db = _db()
    try:
        _lock_open_event(db, event_id)
        db.cursor.execute(
            """
            SELECT discord_id, party_number, slot_number, ign, uuid
            FROM annihilation_party_members
            WHERE event_id = %s AND id = %s
            FOR UPDATE
            """,
            (event_id, member_id),
        )
        row = db.cursor.fetchone()
        if not row:
            raise AnnihilationPartyError("That party entry no longer exists")
        owner_discord_id, old_party, old_slot, old_ign, old_uuid = row
        if owner_discord_id != actor_discord_id and not can_manage:
            raise AnnihilationPartyError("You can only modify your own party entry.")

        linked = _linked_account_for_discord(db, owner_discord_id)
        if linked:
            ign = normalize_ign(linked["ign"])
            uuid = linked["uuid"]
            _assert_unique_identity(
                db,
                event_id,
                owner_discord_id,
                ign,
                uuid=uuid,
                exclude_member_id=member_id,
            )
        else:
            ign = old_ign
            uuid = str(old_uuid) if old_uuid else None

        party_changed = party_number != old_party
        slot_number = (
            _available_slot(db, event_id, party_number) if party_changed else old_slot
        )
        db.cursor.execute(
            """
            UPDATE annihilation_party_members
            SET
                ign = %s,
                uuid = %s,
                party_number = %s,
                slot_number = %s,
                build = %s,
                combat_role = %s,
                bringing_scrolls = %s,
                notes = %s,
                party_joined_at = CASE
                    WHEN party_number <> %s THEN NOW()
                    ELSE party_joined_at
                END,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                ign,
                uuid,
                party_number,
                slot_number,
                build,
                combat_role,
                bool(bringing_scrolls),
                notes,
                party_number,
                member_id,
            ),
        )
        if party_changed:
            _clear_empty_party_world(db, event_id, old_party)
        db.connection.commit()
    except Exception:
        db.connection.rollback()
        raise
    finally:
        db.close()


def _clear_empty_party_world(db: DB, event_id: int, party_number: int) -> None:
    db.cursor.execute(
        """
        UPDATE annihilation_parties
        SET world = NULL, updated_at = NOW()
        WHERE event_id = %s
          AND party_number = %s
          AND NOT EXISTS (
              SELECT 1
              FROM annihilation_party_members
              WHERE event_id = %s AND party_number = %s
          )
        """,
        (event_id, party_number, event_id, party_number),
    )


def remove_member(
    event_id: int,
    actor_discord_id: int,
    member_id: int,
    can_manage: bool,
) -> dict:
    db = _db()
    try:
        _lock_open_event(db, event_id)
        db.cursor.execute(
            """
            SELECT discord_id, ign, party_number
            FROM annihilation_party_members
            WHERE event_id = %s AND id = %s
            FOR UPDATE
            """,
            (event_id, member_id),
        )
        row = db.cursor.fetchone()
        if not row:
            raise AnnihilationPartyError("That party entry no longer exists")
        owner_discord_id, ign, party_number = row
        if owner_discord_id != actor_discord_id and not can_manage:
            raise AnnihilationPartyError("You can only remove your own party entry.")
        db.cursor.execute(
            "DELETE FROM annihilation_party_members WHERE id = %s",
            (member_id,),
        )
        _clear_empty_party_world(db, event_id, party_number)
        db.connection.commit()
        return {"ign": ign, "party_number": party_number}
    except Exception:
        db.connection.rollback()
        raise
    finally:
        db.close()


def set_party_world(
    event_id: int,
    actor_discord_id: int,
    party_number: int,
    world: str,
    can_manage: bool = False,
) -> str:
    world = normalize_world(world)
    db = _db()
    try:
        _lock_open_event(db, event_id)
        db.cursor.execute(
            """
            SELECT discord_id
            FROM annihilation_party_members
            WHERE event_id = %s AND party_number = %s
            ORDER BY party_joined_at, id
            LIMIT 1
            """,
            (event_id, party_number),
        )
        row = db.cursor.fetchone()
        if not row:
            raise AnnihilationPartyError("That party has no members")
        if row[0] != actor_discord_id and not can_manage:
            raise AnnihilationPartyError(
                "Only the current party leader can change the paryt world"
            )
        db.cursor.execute(
            """
            UPDATE annihilation_parties
            SET world = %s, updated_at = NOW()
            WHERE event_id = %s AND party_number = %s
            """,
            (world, event_id, party_number),
        )
        db.connection.commit()
        return world
    except Exception:
        db.connection.rollback()
        raise
    finally:
        db.close()


def close_due_events(now: datetime.datetime | None = None) -> list[int]:
    now = ensure_utc(now or utc_now())
    db = _db()
    try:
        db.cursor.execute(
            """
            UPDATE annihilation_party_events
            SET status = 'closed', closed_at = %s, updated_at = NOW()
            WHERE status = 'active' AND closes_at <= %s
            RETURNING id
            """,
            (now, now),
        )
        event_ids = [row[0] for row in db.cursor.fetchall()]
        db.connection.commit()
        return event_ids
    finally:
        db.close()


def get_unfinalized_closed_events() -> list[dict]:
    db = _db()
    try:
        db.cursor.execute(
            f"""
            SELECT {_EVENT_COLUMNS}
            FROM annihilation_party_events
            WHERE status = 'closed' AND discord_closed_at IS NULL
            ORDER BY closed_at
            """
        )
        return [_event_row_to_dict(row) for row in db.cursor.fetchall()]
    finally:
        db.close()


def mark_discord_closed(event_id: int) -> None:
    db = _db()
    try:
        db.cursor.execute(
            """
            UPDATE annihilation_party_events
            SET discord_closed_at = NOW(), updated_at = NOW()
            WHERE id = %s
            """,
            (event_id,),
        )
        db.connection.commit()
    finally:
        db.close()


def role_display(role: str | None) -> tuple[str, str]:
    return {
        "healer": ("❤️‍🩹", "Healer"),
        "guardian": ("🛡️", "Guardian"),
    }.get(role, ("", ""))


def _display_text(value: str, limit: int) -> str:
    value = discord.utils.escape_mentions(value or "")
    value = discord.utils.escape_markdown(value)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


def build_board_embeds(event: dict) -> list[discord.Embed]:
    closed = event["status"] == "closed" or event["schedule_at"] <= utc_now()
    colour = 0x747F8D if closed else 0xE00000
    schedule_ts = int(event["schedule_at"].timestamp())
    status_text = "Sign-ups are closed!" if closed else "Sign-ups are open!"
    thread_text = (
        f"\nDiscussion: <#{event['thread_id']}>" if event.get("thread_id") else ""
    )

    header = discord.Embed(
        title="Prelude to Annihilation",
        description=(
            "Hateful echoes erupt from the Portal.\n"
            "The province of Wynn faces **Annihilation**.\n\n"
            f"Annihilation starts <t:{schedule_ts}:F> (<t:{schedule_ts}:R>),\n"
            f"{status_text}{thread_text}\n\n"
            "Prepare to defend the province at the **Corruption Portal**!"
        ),
        colour=colour,
    )

    embeds = [header]
    for party_number in range(1, PARTY_COUNT + 1):
        party = event["parties"][party_number]
        members = party["members"]
        if not members:
            continue
        leader = next((member for member in members if member["is_leader"]), None)
        world = party.get("world") or "Not set"
        leader_name = f"<@{leader['discord_id']}>" if leader else "None"

        lines = []
        for display_position, member in enumerate(members, start=1):
            role_icon, _ = role_display(member["combat_role"])
            crown = "👑 " if member["is_leader"] else ""
            scrolls = " 📜" if member["bringing_scrolls"] else ""
            marker = f"{crown}{role_icon}".strip()
            marker = f"{marker} " if marker else ""
            line = (
                f"**{display_position:02d}.** {marker}<@{member['discord_id']}>"
                f"\n*{_display_text(member['build'], 18)}*{scrolls}"
            )
            if member.get("notes"):
                line += f" · {_display_text(member['notes'], 18)}"
            lines.append(line)

        if len(members) < PARTY_SIZE:
            lines.append(f"**{len(members) + 1:02d}.** `<Available>`")

        party_embed = discord.Embed(
            title=f"Party {party_number}: {len(members)} / {PARTY_SIZE}",
            description="\n".join(lines),
            colour=colour,
        )
        party_embed.add_field(name="World", value=world, inline=True)
        party_embed.add_field(name="Leader", value=leader_name, inline=True)
        embeds.append(party_embed)

    return embeds
