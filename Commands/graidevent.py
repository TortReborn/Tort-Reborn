# Commands/graidevent.py
import asyncio
import datetime
from datetime import timezone
import time
from typing import Optional, List, Tuple

import discord
from discord.commands import slash_command, AutocompleteContext
from discord import default_permissions, Option
from discord.ext import commands

from Helpers.database import DB
from Helpers.variables import ALL_GUILD_IDS

# simple TTL cache to avoid hammering DB
_EVENT_CACHE = {"items": [], "ts": 0.0}
_EVENT_TTL = 30.0  # seconds

def _query_event_titles(prefix: str) -> list[str]:
    db = DB(); db.connect()
    try:
        if prefix:
            db.cursor.execute(
                """SELECT title
                FROM graid_events
                WHERE active = FALSE AND title ILIKE %s
                ORDER BY updated_at DESC
                LIMIT 25""",
                (f"%{prefix}%",)
            )
        else:
            db.cursor.execute(
                """SELECT title
                FROM graid_events
                WHERE active = FALSE
                ORDER BY updated_at DESC
                LIMIT 25"""
            )
        return [r[0] for r in db.cursor.fetchall()]
    finally:
        db.close()

def _db():
    db = DB(); db.connect(); return db

def _get_active_event(cur):
    cur.execute("SELECT id, title, start_ts, end_ts, low_rank_reward, high_rank_reward, min_completions "
                "FROM graid_events WHERE active = TRUE LIMIT 1")
    row = cur.fetchone()
    if not row: return None
    return {
        "id": row[0], "title": row[1], "start_ts": row[2], "end_ts": row[3],
        "low": row[4], "high": row[5], "minc": row[6]
    }

def _top5_for_event(cur, event_id: int, min_completions: int) -> List[Tuple[str, int]]:
    cur.execute(
        "SELECT uuid::uuid, total FROM graid_event_totals "
        "WHERE event_id = %s AND total >= %s ORDER BY total DESC, uuid ASC LIMIT 5",
        (event_id, min_completions)
    )
    return [(u, t) for (u, t) in cur.fetchall()]

def _uuids_to_mentions(cur, pairs: List[Tuple[str,int]]) -> List[str]:
    if not pairs: return []
    uuids = [u for (u, _) in pairs]
    cur.execute(
        """
        SELECT uuid, discord_id, ign
        FROM discord_links
        WHERE uuid = ANY(%s::uuid[])
        """,
        (uuids,),
    )
    m = {r[0]: (r[1], r[2]) for r in cur.fetchall()}  # {uuid: (discord_id, ign)}
    out = []
    for u, total in pairs:
        did, ign = m.get(u, (None, None))
        who = f"<@{did}>" if did else (f"**{ign}**" if ign else f"`{u[:8]}`")
        out.append(f"{who} — {total}")
    return out

class GraidEvent(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(name="graid_event_start", guild_ids=ALL_GUILD_IDS, description="HR: Start a new GRAID event")
    @default_permissions(manage_roles=True)
    async def graid_start(
        self, ctx: discord.ApplicationContext,
        title: str,
        end_date_iso: str,              # e.g., "2025-09-30T23:59:59Z" or "2025-09-30"
        low_rank_reward: int,
        high_rank_reward: int,
        min_completions: int
    ):
        db = _db()
        try:
            cur = db.cursor
            if _get_active_event(cur):
                await ctx.respond("❌ A GRAID event is already active.", ephemeral=True); return

            # parse end date
            try:
                if "T" in end_date_iso:
                    end_ts = datetime.datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
                else:
                    # date only -> assume end of day UTC
                    d = datetime.date.fromisoformat(end_date_iso)
                    end_ts = datetime.datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc)
            except Exception:
                await ctx.respond("❌ end_date must be ISO format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ).", ephemeral=True); return

            cur.execute(
                "INSERT INTO graid_events (title, start_ts, end_ts, active, low_rank_reward, high_rank_reward, min_completions, created_by_discord) "
                "VALUES (%s, NOW(), %s, TRUE, %s, %s, %s, %s) RETURNING id",
                (title, end_ts, low_rank_reward, high_rank_reward, min_completions, ctx.user.id)
            )
            event_id = cur.fetchone()[0]
            db.connection.commit()
            await ctx.respond(
                f"✅ **GRAID started**: **{title}**\n"
                f"Start: now • End: {end_ts.isoformat()}\n"
                f"Rewards: low={low_rank_reward}, high={high_rank_reward}\n"
                f"Min completions: {min_completions}\n"
                f"(id={event_id})", ephemeral=True
            )
        finally:
            db.close()

    @slash_command(name="graid_event_stop", guild_ids=ALL_GUILD_IDS, description="HR: Stop the current GRAID event")
    @default_permissions(manage_roles=True)
    async def graid_stop(self, ctx: discord.ApplicationContext):
        db = _db()
        try:
            cur = db.cursor
            ev = _get_active_event(cur)
            if not ev:
                await ctx.respond("ℹ️ No active GRAID event.", ephemeral=True); return

            # close event
            cur.execute("UPDATE graid_events SET active=FALSE, end_ts = COALESCE(end_ts, NOW()) WHERE id = %s", (ev["id"],))
            # fetch winners
            winners = _top5_for_event(cur, ev["id"], ev["minc"])
            db.connection.commit()

            lines = _uuids_to_mentions(cur, winners)
            desc = "\n".join(lines) if lines else "_No qualifying participants (below min completions)._"
            embed = discord.Embed(
                title=f"GRAID Ended: {ev['title']}",
                description=desc,
                color=discord.Color.blurple()
            )
            embed.add_field(name="Settings",
                            value=f"Min completions: **{ev['minc']}**\nRewards: low={ev['low']}, high={ev['high']}",
                            inline=False)
            await ctx.respond(embed=embed, ephemeral=True)
        finally:
            db.close()

    @slash_command(name="graid_event_info", guild_ids=ALL_GUILD_IDS, description="HR: Show the active GRAID event")
    @default_permissions(manage_roles=True)
    async def graid_info(self, ctx: discord.ApplicationContext):
        db = _db()
        try:
            cur = db.cursor
            ev = _get_active_event(cur)
            if not ev:
                await ctx.respond("ℹ️ No active GRAID event.", ephemeral=True); return
            top = _top5_for_event(cur, ev["id"], ev["minc"])
            lines = _uuids_to_mentions(cur, top)
            desc = "\n".join(lines) if lines else "_No one on the board yet._"
            embed = discord.Embed(
                title=f"Active GRAID: {ev['title']}",
                description=desc,
                color=discord.Color.green()
            )
            embed.add_field(name="Window", value=f"Start: {ev['start_ts'].isoformat()}\nEnd: {ev['end_ts'].isoformat() if ev['end_ts'] else '—'}", inline=False)
            embed.add_field(name="Rules", value=f"Min completions: **{ev['minc']}**\nRewards: low={ev['low']}, high={ev['high']}", inline=False)
            await ctx.respond(embed=embed, ephemeral=True)
        finally:
            db.close()

    async def _graid_title_autocomplete(ctx: AutocompleteContext):
        # ctx.value is the user’s partial text
        prefix = (ctx.value or "").strip()
        now = time.time()
        # very lightweight cache (optional)
        if now - _EVENT_CACHE["ts"] < _EVENT_TTL and not prefix:
            return _EVENT_CACHE["items"]
        titles = await asyncio.to_thread(_query_event_titles, prefix)
        if not prefix:
            _EVENT_CACHE["items"], _EVENT_CACHE["ts"] = titles, now
        return titles  # list[str] (max 25 shown by Discord)

    # Then change your command signature:
    @slash_command(name="graid_event_set", description="HR: Activate an existing GRAID (by title)")
    @default_permissions(manage_roles=True)
    async def graid_set(
        self,
        ctx: discord.ApplicationContext,
        title: Option(str, "Pick an existing event", autocomplete=_graid_title_autocomplete),
        reset_counters: bool = False
    ):
        db = _db()
        try:
            cur = db.cursor
            if _get_active_event(cur):
                await ctx.respond("❌ A GRAID event is already active.", ephemeral=True); return

            cur.execute("SELECT id FROM graid_events WHERE title = %s LIMIT 1", (title,))
            row = cur.fetchone()
            if not row:
                await ctx.respond("❌ No event with that title.", ephemeral=True); return
            event_id = row[0]

            if reset_counters:
                cur.execute("DELETE FROM graid_event_totals WHERE event_id = %s", (event_id,))
                cur.execute("UPDATE graid_events SET start_ts = NOW(), end_ts = NULL WHERE id = %s", (event_id,))
            cur.execute("UPDATE graid_events SET active = TRUE WHERE id = %s", (event_id,))
            db.connection.commit()
            await ctx.respond(f"✅ Activated **{title}** (id={event_id})", ephemeral=True)
        finally:
            db.close()

def setup(client):
    client.add_cog(GraidEvent(client))
