# Commands/graidlog.py
import asyncio
import time
from collections import Counter

import discord
from discord.commands import SlashCommandGroup, AutocompleteContext
from discord import Option
from discord.ext import commands

from Helpers.database import DB
from Helpers.variables import HOME_GUILD_IDS

RAID_SHORT = {
    "Nest of the Grootslangs": "NOTG",
    "The Canyon Colossus": "TCC",
    "The Nameless Anomaly": "TNA",
    "Orphion's Nexus of Light": "NOL",
}

RAID_SHORT_TO_FULL = {v: k for k, v in RAID_SHORT.items()}


def _short(raid_type: str | None) -> str:
    if not raid_type:
        return "Unknown"
    return RAID_SHORT.get(raid_type, "Unknown")


def _db():
    db = DB(); db.connect(); return db


# --- Autocomplete helpers ---

async def _ign_autocomplete(ctx: AutocompleteContext):
    prefix = (ctx.value or "").strip().lower()
    if len(prefix) < 2:
        return []
    db = _db()
    try:
        db.cursor.execute(
            "SELECT DISTINCT ign FROM graid_log_participants WHERE LOWER(ign) LIKE %s ORDER BY ign LIMIT 25",
            (f"{prefix}%",)
        )
        return [r[0] for r in db.cursor.fetchall()]
    finally:
        db.close()


async def _raid_type_autocomplete(ctx: AutocompleteContext):
    return ["NOTG", "TCC", "TNA", "NOL", "Unknown"]


class GraidLog(commands.Cog):
    def __init__(self, client):
        self.client = client

    graid_log = SlashCommandGroup(
        "graid-log",
        "Graid log statistics and browsing",
        guild_ids=HOME_GUILD_IDS,
        default_member_permissions=discord.Permissions(manage_roles=True),
    )

    # --- /graid-log leaderboard ---

    @graid_log.command(name="leaderboard", description="Graid leaderboard by raid count")
    async def leaderboard(
        self,
        ctx: discord.ApplicationContext,
        sort: Option(str, "Sort column", autocomplete=_raid_type_autocomplete, required=False, default=None),
    ):
        await ctx.defer()
        db = _db()
        try:
            cur = db.cursor

            cur.execute("""
                SELECT glp.ign, gl.raid_type, COUNT(*) as cnt
                FROM graid_log_participants glp
                JOIN graid_logs gl ON glp.log_id = gl.id
                GROUP BY glp.ign, gl.raid_type
            """)

            players: dict[str, dict[str, int]] = {}
            for ign, raid_type, cnt in cur.fetchall():
                if ign not in players:
                    players[ign] = {"total": 0, "NOTG": 0, "TCC": 0, "TNA": 0, "NOL": 0, "Unknown": 0}
                s = _short(raid_type)
                players[ign][s] += cnt
                players[ign]["total"] += cnt

            if not players:
                await ctx.followup.send("No graid log data found.")
                return

            sort_key = (sort or "").upper()
            if sort_key in ("NOTG", "TCC", "TNA", "NOL", "UNKNOWN"):
                sorted_players = sorted(players.items(), key=lambda x: (-x[1].get(sort_key, 0), -x[1]["total"]))
            else:
                sorted_players = sorted(players.items(), key=lambda x: -x[1]["total"])

            top = sorted_players[:20]
            lines = []
            for i, (ign, data) in enumerate(top, 1):
                medal = "\U0001f947" if i == 1 else ("\U0001f948" if i == 2 else ("\U0001f949" if i == 3 else f"`{i:>2}.`"))
                type_parts = [f"{t}:{data[t]}" for t in ["NOTG", "TCC", "TNA", "NOL"] if data[t] > 0]
                type_str = f" ({', '.join(type_parts)})" if type_parts else ""
                lines.append(f"{medal} **{ign}** — {data['total']}{type_str}")

            embed = discord.Embed(
                title="Graid Leaderboard",
                description="\n".join(lines),
                color=0x3474EB,
            )
            embed.set_footer(text=f"{len(players)} total players")
            await ctx.followup.send(embed=embed)

        finally:
            db.close()

    # --- /graid-log stats ---

    @graid_log.command(name="stats", description="View a player's graid statistics")
    async def stats(
        self,
        ctx: discord.ApplicationContext,
        ign: Option(str, "Player IGN", autocomplete=_ign_autocomplete, required=True),
    ):
        await ctx.defer()
        db = _db()
        try:
            cur = db.cursor

            cur.execute("""
                SELECT gl.id, gl.raid_type, gl.completed_at
                FROM graid_log_participants glp
                JOIN graid_logs gl ON glp.log_id = gl.id
                WHERE LOWER(glp.ign) = LOWER(%s)
                ORDER BY gl.completed_at DESC
            """, (ign,))

            rows = cur.fetchall()
            if not rows:
                await ctx.followup.send(f"No graid logs found for **{ign}**.", ephemeral=True)
                return

            total = len(rows)
            type_counts = Counter()
            day_counts = Counter()
            raid_ids = []

            for rid, rtype, completed in rows:
                type_counts[_short(rtype)] += 1
                day_counts[completed.strftime("%Y-%m-%d")] += 1
                raid_ids.append(rid)

            best_day, best_day_count = day_counts.most_common(1)[0]

            # Top teammates
            unique_ids = list(set(raid_ids))[:500]
            teammates = Counter()
            if unique_ids:
                placeholders = ",".join(["%s"] * len(unique_ids))
                cur.execute(
                    f"SELECT ign FROM graid_log_participants WHERE log_id IN ({placeholders}) AND LOWER(ign) != LOWER(%s)",
                    unique_ids + [ign]
                )
                for (tm_ign,) in cur.fetchall():
                    teammates[tm_ign] += 1

            type_lines = [f"**{t}**: {c}" for t, c in type_counts.most_common()]
            embed = discord.Embed(
                title=f"Graid Stats: {ign}",
                description=f"**{total}** total raids",
                color=0x3474EB,
            )
            embed.add_field(name="Raid Types", value="\n".join(type_lines) or "—", inline=True)

            tm_lines = [f"{tm}: {c}" for tm, c in teammates.most_common(5)]
            embed.add_field(name="Top Teammates", value="\n".join(tm_lines) or "—", inline=True)
            embed.add_field(name="Best Day", value=f"{best_day} ({best_day_count} raids)", inline=False)

            first_raid = rows[-1][2]
            last_raid = rows[0][2]
            embed.set_footer(text=f"First raid: {first_raid.strftime('%Y-%m-%d')} | Latest: {last_raid.strftime('%Y-%m-%d')}")

            await ctx.followup.send(embed=embed)

        finally:
            db.close()

    # --- /graid-log list ---

    @graid_log.command(name="list", description="Browse graid log entries")
    async def list_logs(
        self,
        ctx: discord.ApplicationContext,
        ign: Option(str, "Filter by player", autocomplete=_ign_autocomplete, required=False, default=None),
        raid_type: Option(str, "Filter by raid type", autocomplete=_raid_type_autocomplete, required=False, default=None),
    ):
        await ctx.defer()
        db = _db()
        try:
            cur = db.cursor

            conditions = []
            params = []

            if ign:
                conditions.append("gl.id IN (SELECT log_id FROM graid_log_participants WHERE LOWER(ign) = LOWER(%s))")
                params.append(ign)

            if raid_type:
                full_name = RAID_SHORT_TO_FULL.get(raid_type.upper())
                if full_name:
                    conditions.append("gl.raid_type = %s")
                    params.append(full_name)
                elif raid_type.lower() == "unknown":
                    conditions.append("gl.raid_type IS NULL")

            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            cur.execute(f"""
                SELECT gl.id, gl.raid_type, gl.completed_at
                FROM graid_logs gl
                {where}
                ORDER BY gl.completed_at DESC
                LIMIT 20
            """, params)

            rows = cur.fetchall()
            if not rows:
                await ctx.followup.send("No graid logs found with those filters.", ephemeral=True)
                return

            log_ids = [r[0] for r in rows]
            placeholders = ",".join(["%s"] * len(log_ids))
            cur.execute(
                f"SELECT log_id, ign FROM graid_log_participants WHERE log_id IN ({placeholders}) ORDER BY ign",
                log_ids
            )
            parts_map: dict[int, list[str]] = {}
            for lid, pign in cur.fetchall():
                parts_map.setdefault(lid, []).append(pign)

            lines = []
            for rid, rtype, completed in rows:
                short = _short(rtype)
                names = ", ".join(parts_map.get(rid, []))
                ts = completed.strftime("%m/%d %H:%M")
                lines.append(f"`{ts}` **{short}** — {names}")

            embed = discord.Embed(
                title="Graid Log",
                description="\n".join(lines),
                color=0x3474EB,
            )
            embed.set_footer(text=f"Showing latest {len(rows)} entries")
            await ctx.followup.send(embed=embed)

        finally:
            db.close()

    # --- /graid-log overview ---

    @graid_log.command(name="overview", description="Overview of all graid activity")
    async def overview(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        db = _db()
        try:
            cur = db.cursor

            cur.execute("SELECT COUNT(*) FROM graid_logs")
            total_raids = cur.fetchone()[0]

            cur.execute("SELECT COUNT(DISTINCT ign) FROM graid_log_participants")
            unique_players = cur.fetchone()[0]

            cur.execute("SELECT raid_type, COUNT(*) FROM graid_logs GROUP BY raid_type ORDER BY COUNT(*) DESC")
            type_lines = [f"**{_short(rt)}**: {cnt}" for rt, cnt in cur.fetchall()]

            cur.execute("""
                SELECT glp.ign, COUNT(*) as cnt
                FROM graid_log_participants glp
                JOIN graid_logs gl ON glp.log_id = gl.id
                GROUP BY glp.ign ORDER BY cnt DESC LIMIT 5
            """)
            top_lines = [f"{'🥇🥈🥉'[i] if i < 3 else f'{i+1}.'} **{ign}** — {cnt}" for i, (ign, cnt) in enumerate(cur.fetchall())]

            embed = discord.Embed(title="Graid Overview", color=0x3474EB)
            embed.add_field(name="Summary", value=f"**{total_raids}** raids by **{unique_players}** players", inline=False)
            embed.add_field(name="Raid Types", value="\n".join(type_lines) or "—", inline=True)
            embed.add_field(name="Top Players", value="\n".join(top_lines) or "—", inline=True)

            await ctx.followup.send(embed=embed)

        finally:
            db.close()


def setup(client):
    client.add_cog(GraidLog(client))
