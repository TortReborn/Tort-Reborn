"""
Recruiter tracking & payouts (DB-backed, replaces the old Sheets integration).

  1. Guild app accepted, recruiter matched -> record_pending_recruit() inserts
     a pending row (Commands/app_commands.py::_process_recruiter_tracking).
  2. Recruit hits Piranha -> credit_piranha_promotion() (called from the four
     promotion pathways) claims the pending row and posts the payout embed.
  3. Recruit leaves first -> void_pending_recruit() kills the pending row.
"""

import asyncio

import discord

from Helpers.database import DB
from Helpers.logger import log, ERROR
from Helpers.variables import TASK_BOARD_CHANNEL_ID


def payout_for_count(recruit_number: int) -> int:
    if recruit_number <= 2:
        return 5
    if recruit_number <= 5:
        return 10
    if recruit_number <= 9:
        return 15
    if recruit_number == 10:
        return 20
    return 10  # 11+, flat


# --- Recruiter matching ------------------------------------------------------


def _fuzzy_match(text: str, names: list[str]) -> str | None:
    lower = text.lower()
    for name in names:
        if name.lower() == lower:
            return name
    matches = [n for n in names if lower in n.lower()]
    return matches[0] if len(matches) == 1 else None


def _all_known_igns() -> list[str]:
    db = DB(); db.connect()
    try:
        db.cursor.execute("SELECT ign FROM discord_links WHERE ign IS NOT NULL AND ign != ''")
        return [row[0] for row in db.cursor.fetchall()]
    finally:
        db.close()


async def resolve_recruiter(text: str, use_ai_fallback: bool = False) -> tuple[str | None, bool]:
    """Match free-text recruiter input against the guild roster + known IGNs.
    Returns (matched_ign, excluded); excluded is True for owner/chief."""
    if not text:
        return None, False

    from Helpers.classes import Guild as WynnGuild
    guild_data = await asyncio.to_thread(WynnGuild, "TAq")
    guild_members = guild_data.all_members
    member_names = [m['name'] for m in guild_members]
    member_rank_map = {m['name'].lower(): m['rank'] for m in guild_members}

    db_names = await asyncio.to_thread(_all_known_igns)
    for name in db_names:
        if name not in member_names:
            member_names.append(name)

    matched = _fuzzy_match(text, member_names)
    if matched is None and use_ai_fallback:
        from Helpers.openai_helper import match_recruiter_name
        ai_result = await asyncio.to_thread(match_recruiter_name, text, member_names)
        if not ai_result.get("error") and ai_result.get("confidence", 0) >= 0.70:
            matched = ai_result["matched_name"]

    excluded = bool(matched) and member_rank_map.get(matched.lower()) in ("owner", "chief")
    return matched, excluded


# --- DB helpers (blocking, call via asyncio.to_thread) ----------------------


def record_pending_recruit(app_id: int, recruiter_ign: str, recruit_ign: str,
                            recruit_discord_id: int | None, excluded: bool = False) -> bool:
    """Insert a pending credit, or correct one that hasn't been finalized yet.
    Returns False if this app's credit was already claimed/posted (too late to change)."""
    db = DB(); db.connect()
    try:
        db.cursor.execute(
            """INSERT INTO recruit_credits (app_id, recruiter_ign, recruit_ign, recruit_discord_id, excluded)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (app_id) DO UPDATE
               SET recruiter_ign = EXCLUDED.recruiter_ign, excluded = EXCLUDED.excluded
               WHERE recruit_credits.posted_at IS NULL
               RETURNING id""",
            (app_id, recruiter_ign, recruit_ign,
             str(recruit_discord_id) if recruit_discord_id else None, excluded)
        )
        row = db.cursor.fetchone()
        db.connection.commit()
        return row is not None
    finally:
        db.close()


def _claim_and_finalize(ign: str) -> dict | None:
    """Claim the oldest pending credit for this recruit's IGN and compute their
    recruiter's payout. FOR UPDATE SKIP LOCKED so two promotion pathways firing
    for the same rank-up can't both claim/post it."""
    db = DB(); db.connect()
    try:
        db.cursor.execute(
            """SELECT id, recruiter_ign, recruit_ign, excluded FROM recruit_credits
               WHERE LOWER(recruit_ign) = LOWER(%s)
                 AND posted_at IS NULL AND voided_at IS NULL
               ORDER BY created_at ASC
               LIMIT 1
               FOR UPDATE SKIP LOCKED""",
            (ign,)
        )
        row = db.cursor.fetchone()
        if not row:
            db.connection.rollback()
            return None

        credit_id, recruiter_ign, recruit_ign, excluded = row

        if excluded:
            # Owner/chief: handled, never paid, never posted.
            db.cursor.execute(
                "UPDATE recruit_credits SET posted_at = NOW(), eligible_at = NOW() WHERE id = %s",
                (credit_id,)
            )
            db.connection.commit()
            return {"excluded": True}

        db.cursor.execute(
            """SELECT COUNT(*) FROM recruit_credits
               WHERE recruiter_ign = %s AND posted_at IS NOT NULL AND excluded = FALSE""",
            (recruiter_ign,)
        )
        recruit_number = db.cursor.fetchone()[0] + 1
        payout_le = payout_for_count(recruit_number)

        db.cursor.execute(
            """UPDATE recruit_credits
               SET posted_at = NOW(), eligible_at = NOW(),
                   recruit_number = %s, payout_le = %s
               WHERE id = %s""",
            (recruit_number, payout_le, credit_id)
        )
        db.connection.commit()
        return {
            "excluded": False,
            "credit_id": credit_id,
            "recruiter_ign": recruiter_ign,
            "recruit_ign": recruit_ign,
            "recruit_number": recruit_number,
            "payout_le": payout_le,
        }
    except Exception:
        db.connection.rollback()
        raise
    finally:
        db.close()


def _stamp_task_board_message(credit_id: int, message_id: int) -> None:
    db = DB(); db.connect()
    try:
        db.cursor.execute(
            "UPDATE recruit_credits SET task_board_message_id = %s WHERE id = %s",
            (message_id, credit_id)
        )
        db.connection.commit()
    finally:
        db.close()


def void_pending_recruit(api_ign: str, uuid: str | None = None) -> None:
    """Void a pending credit when the recruit leaves before reaching Piranha.
    Matches on their current API name or linked IGN (covers a rename)."""
    db = DB(); db.connect()
    try:
        db.cursor.execute(
            """UPDATE recruit_credits
               SET voided_at = NOW()
               WHERE posted_at IS NULL AND voided_at IS NULL
                 AND (LOWER(recruit_ign) = LOWER(%s)
                      OR (%s IS NOT NULL
                          AND LOWER(recruit_ign) = LOWER((SELECT ign FROM discord_links WHERE uuid = %s))))""",
            (api_ign, uuid, uuid)
        )
        db.connection.commit()
    finally:
        db.close()


def mark_recruit_paid(credit_id: int, paid_by: str) -> bool:
    """Returns False if it was already marked paid."""
    db = DB(); db.connect()
    try:
        db.cursor.execute(
            """UPDATE recruit_credits
               SET paid = TRUE, paid_at = NOW(), paid_by = %s
               WHERE id = %s AND paid = FALSE
               RETURNING id""",
            (paid_by, credit_id)
        )
        row = db.cursor.fetchone()
        db.connection.commit()
        return row is not None
    finally:
        db.close()


def get_credit_by_message(message_id: int):
    """Returns (id, paid, recruiter_ign, recruit_ign, payout_le) or None."""
    db = DB(); db.connect()
    try:
        db.cursor.execute(
            """SELECT id, paid, recruiter_ign, recruit_ign, payout_le
               FROM recruit_credits WHERE task_board_message_id = %s""",
            (message_id,)
        )
        return db.cursor.fetchone()
    finally:
        db.close()


def get_recruiter_stats(ign: str) -> dict:
    """Recruit counts + LE for this recruiter. Owner/chief recruiters still get a
    count (recruiting is still real, they just don't get paid for it)."""
    db = DB(); db.connect()
    try:
        db.cursor.execute(
            """SELECT COUNT(*) FILTER (WHERE NOT excluded),
                      COUNT(*) FILTER (WHERE excluded),
                      COALESCE(SUM(payout_le) FILTER (WHERE paid = FALSE), 0),
                      COALESCE(SUM(payout_le), 0)
               FROM recruit_credits
               WHERE LOWER(recruiter_ign) = LOWER(%s) AND posted_at IS NOT NULL""",
            (ign,)
        )
        paid_eligible, excluded_count, unpaid_le, total_le = db.cursor.fetchone()
        return {
            "total_recruits": paid_eligible + excluded_count,
            "excluded_recruits": excluded_count,
            "unpaid_le": unpaid_le,
            "total_le": total_le,
        }
    finally:
        db.close()


# --- Called from the four promotion pathways ----------------------------------


async def credit_piranha_promotion(client: discord.Client, ign: str) -> None:
    """Call once a member reaches Piranha. No-ops if they have no pending credit."""
    try:
        result = await asyncio.to_thread(_claim_and_finalize, ign)
    except Exception as e:
        log(ERROR, f"Failed to claim recruit credit for {ign}: {e}", context="recruiting")
        return

    if result is None or result.get("excluded"):
        return

    channel = client.get_channel(TASK_BOARD_CHANNEL_ID)
    if channel is None:
        log(ERROR, f"TASK_BOARD_CHANNEL_ID not reachable, could not post payout for {ign}",
            context="recruiting")
        return

    embed = discord.Embed(title="Recruiter Payout", color=0xAAF64A)
    embed.add_field(name="Recruiter", value=discord.utils.escape_markdown(result["recruiter_ign"]), inline=True)
    embed.add_field(name="Recruit", value=discord.utils.escape_markdown(result["recruit_ign"]), inline=True)
    embed.add_field(name="Recruit #", value=str(result["recruit_number"]), inline=True)
    embed.add_field(name="Payout", value=f"{result['payout_le']} LE", inline=True)

    from Helpers.views import RecruitPaidView
    message = await channel.send(embed=embed, view=RecruitPaidView())
    await asyncio.to_thread(_stamp_task_board_message, result["credit_id"], message.id)
