# Commands/graidlog.py
import asyncio
from collections import Counter

import discord
from discord.commands import SlashCommandGroup, AutocompleteContext
from discord import Option
from discord.ext import commands

from Helpers.database import DB, get_current_guild_data
from Helpers.variables import HOME_GUILD_IDS, RAID_LOG_CHANNEL_ID, NOTG_EMOJI, TCC_EMOJI, TNA_EMOJI, NOL_EMOJI, TWP_EMOJI

RAID_NAMES = [
    "Nest of the Grootslangs",
    "The Canyon Colossus",
    "The Nameless Anomaly",
    "Orphion's Nexus of Light",
    "The Wartorn Palace",
]

RAID_SHORT = {
    "Nest of the Grootslangs": "NOTG",
    "The Canyon Colossus": "TCC",
    "The Nameless Anomaly": "TNA",
    "Orphion's Nexus of Light": "NOL",
    "The Wartorn Palace": "TWP",
}

RAID_SHORT_TO_FULL = {v: k for k, v in RAID_SHORT.items()}

RAID_EMOJIS = {
    "Nest of the Grootslangs": NOTG_EMOJI,
    "The Canyon Colossus": TCC_EMOJI,
    "The Nameless Anomaly": TNA_EMOJI,
    "Orphion's Nexus of Light": NOL_EMOJI,
    "The Wartorn Palace": TWP_EMOJI,
}


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
    return ["NOTG", "TCC", "TNA", "NOL", "TWP", "Unknown"]


async def _member_autocomplete(ctx: AutocompleteContext):
    """Autocomplete from current guild members."""
    prefix = (ctx.value or "").strip().lower()
    data = await asyncio.to_thread(get_current_guild_data)
    members = data.get('members', []) if isinstance(data, dict) else []
    names = sorted(set(
        m.get('name') or m.get('username') or ''
        for m in members
        if m.get('name') or m.get('username')
    ))
    if prefix:
        names = [n for n in names if n.lower().startswith(prefix)]
    return names[:25]


class GraidCommands(commands.Cog):
    def __init__(self, client):
        self.client = client

    graid = SlashCommandGroup(
        "graid",
        "Guild raid tracking commands",
        guild_ids=HOME_GUILD_IDS,
        default_member_permissions=discord.Permissions(manage_roles=True),
    )

    # --- /graid log (ADMIN only) ---

    @graid.command(name="log", description="ADMIN: Manually log a guild raid")
    async def log_raid(
        self,
        ctx: discord.ApplicationContext,
        raid_type: Option(str, "Raid type", choices=["NOTG", "TCC", "TNA", "NOL", "TWP"], required=True),
        player1: Option(str, "First participant", autocomplete=_member_autocomplete, required=True),
        player2: Option(str, "Second participant", autocomplete=_member_autocomplete, required=True),
        player3: Option(str, "Third participant", autocomplete=_member_autocomplete, required=True),
        player4: Option(str, "Fourth participant", autocomplete=_member_autocomplete, required=True),
    ):
        await ctx.defer(ephemeral=True)

        # Validate all 4 are current guild members
        current_data = await asyncio.to_thread(get_current_guild_data)
        current_members = current_data.get('members', []) if isinstance(current_data, dict) else []
        current_names = {
            (m.get('name') or m.get('username') or '').casefold(): (m.get('name') or m.get('username') or '')
            for m in current_members
            if m.get('name') or m.get('username')
        }

        if not current_names:
            await ctx.followup.send(':no_entry: Guild member data unavailable. Try again later.', ephemeral=True)
            return

        players = [player1, player2, player3, player4]
        non_members = [p for p in players if p.casefold() not in current_names]
        if non_members:
            listed = ', '.join(f'`{n}`' for n in non_members)
            await ctx.followup.send(f':no_entry: Not current guild members: {listed}', ephemeral=True)
            return

        # Normalize casing to match guild data
        players = [current_names.get(p.casefold(), p) for p in players]

        # Check for duplicates
        if len(set(p.casefold() for p in players)) < 4:
            await ctx.followup.send(':no_entry: All 4 participants must be different players.', ephemeral=True)
            return

        full_raid_name = RAID_SHORT_TO_FULL.get(raid_type)

        # Insert into database
        db = _db()
        try:
            cur = db.cursor

            # Check for active event
            cur.execute("SELECT id FROM graid_events WHERE active = TRUE LIMIT 1")
            row = cur.fetchone()
            event_id = row[0] if row else None

            cur.execute(
                "INSERT INTO graid_logs (event_id, raid_type) VALUES (%s, %s) RETURNING id",
                (event_id, full_raid_name)
            )
            log_id = cur.fetchone()[0]

            for ign in players:
                cur.execute("SELECT uuid FROM discord_links WHERE LOWER(ign) = LOWER(%s)", (ign,))
                uuid_row = cur.fetchone()
                uuid_val = uuid_row[0] if uuid_row else None
                cur.execute(
                    "INSERT INTO graid_log_participants (log_id, uuid, ign) VALUES (%s, %s, %s)",
                    (log_id, uuid_val, ign)
                )

            # Upsert totals if event is active
            if event_id is not None:
                for ign in players:
                    cur.execute("SELECT uuid FROM discord_links WHERE LOWER(ign) = LOWER(%s)", (ign,))
                    uuid_row = cur.fetchone()
                    if uuid_row and uuid_row[0]:
                        cur.execute("""
                            INSERT INTO graid_event_totals (event_id, uuid, total)
                            VALUES (%s, %s, 1)
                            ON CONFLICT (event_id, uuid) DO UPDATE
                              SET total = graid_event_totals.total + 1,
                                  last_updated = NOW()
                        """, (event_id, uuid_row[0]))

            db.connection.commit()
        finally:
            db.close()

        # Post to raid-log channel
        channel = self.client.get_channel(RAID_LOG_CHANNEL_ID)
        if channel:
            bolded = [f"**{discord.utils.escape_markdown(n)}**" for n in players]
            names_str = ", ".join(bolded[:-1]) + ", and " + bolded[-1]
            emoji = RAID_EMOJIS.get(full_raid_name, "")
            embed = discord.Embed(
                title=f"{emoji} {full_raid_name} Completed!",
                description=names_str,
                color=0x00FF00,
            )
            await channel.send(embed=embed)

        await ctx.followup.send(
            f":white_check_mark: Logged **{raid_type}** raid with {', '.join(players)}",
            ephemeral=True,
        )

    # --- /graid leaderboard ---

    @graid.command(name="leaderboard", description="HR: Guild raid leaderboard")
    async def leaderboard(
        self,
        ctx: discord.ApplicationContext,
        sort: Option(str, "Sort column", autocomplete=_raid_type_autocomplete, required=False, default=None),
    ):
        await ctx.defer()
        db = _db()
        try:
            cur = db.cursor
            # UUID-first aggregation with display names from discord_links
            cur.execute("""
                SELECT COALESCE(dl.ign, glp.ign) AS display_name, glp.uuid, gl.raid_type, COUNT(*) as cnt
                FROM graid_log_participants glp
                JOIN graid_logs gl ON glp.log_id = gl.id
                LEFT JOIN discord_links dl ON glp.uuid = dl.uuid
                GROUP BY glp.uuid, COALESCE(dl.ign, glp.ign), gl.raid_type
            """)

            players: dict[str, dict[str, int]] = {}  # keyed by uuid string
            display_names: dict[str, str] = {}
            for display_name, uuid, raid_type, cnt in cur.fetchall():
                key = str(uuid) if uuid else display_name
                if key not in players:
                    players[key] = {"total": 0, "NOTG": 0, "TCC": 0, "TNA": 0, "NOL": 0, "TWP": 0, "Unknown": 0}
                    display_names[key] = display_name
                s = _short(raid_type)
                players[key][s] += cnt
                players[key]["total"] += cnt

            # Apply offsets (all-time)
            cur.execute("SELECT gro.uuid, gro.raid_offset, dl.ign FROM graid_raid_offsets gro LEFT JOIN discord_links dl ON gro.uuid = dl.uuid")
            for uuid, offset, dl_name in cur.fetchall():
                key = str(uuid)
                if key in players:
                    players[key]["total"] += offset
                else:
                    name = dl_name or key
                    players[key] = {"total": offset, "NOTG": 0, "TCC": 0, "TNA": 0, "NOL": 0, "TWP": 0, "Unknown": 0}
                    display_names[key] = name

            if not players:
                await ctx.followup.send("No guild raid data found.")
                return

            sort_key = (sort or "").upper()
            if sort_key in ("NOTG", "TCC", "TNA", "NOL", "TWP", "UNKNOWN"):
                sorted_players = sorted(players.items(), key=lambda x: (-x[1].get(sort_key, 0), -x[1]["total"]))
            else:
                sorted_players = sorted(players.items(), key=lambda x: -x[1]["total"])

            top = sorted_players[:20]
            lines = []
            for i, (key, data) in enumerate(top, 1):
                name = display_names.get(key, key)
                type_parts = [f"{t}:{data[t]}" for t in ["NOTG", "TCC", "TNA", "NOL", "TWP"] if data[t] > 0]
                type_str = f" ({', '.join(type_parts)})" if type_parts else ""
                lines.append(f"`{i:>2}.` **{name}** — {data['total']}{type_str}")

            embed = discord.Embed(
                title="Guild Raid Leaderboard",
                description="\n".join(lines),
                color=0x3474EB,
            )
            embed.set_footer(text=f"{len(players)} total players")
            await ctx.followup.send(embed=embed)
        finally:
            db.close()

    # --- /graid stats ---

    @graid.command(name="stats", description="HR: View a player's guild raid statistics")
    async def stats(
        self,
        ctx: discord.ApplicationContext,
        ign: Option(str, "Player IGN", autocomplete=_ign_autocomplete, required=True),
    ):
        await ctx.defer()
        db = _db()
        try:
            cur = db.cursor

            # Resolve IGN → UUID
            cur.execute("SELECT uuid FROM discord_links WHERE LOWER(ign) = LOWER(%s) AND uuid IS NOT NULL", (ign,))
            uuid_row = cur.fetchone()
            player_uuid = uuid_row[0] if uuid_row else None

            if player_uuid:
                cur.execute("""
                    SELECT gl.id, gl.raid_type, gl.completed_at
                    FROM graid_log_participants glp
                    JOIN graid_logs gl ON glp.log_id = gl.id
                    WHERE glp.uuid = %s
                    ORDER BY gl.completed_at DESC
                """, (player_uuid,))
            else:
                cur.execute("""
                    SELECT gl.id, gl.raid_type, gl.completed_at
                    FROM graid_log_participants glp
                    JOIN graid_logs gl ON glp.log_id = gl.id
                    WHERE LOWER(glp.ign) = LOWER(%s)
                    ORDER BY gl.completed_at DESC
                """, (ign,))

            rows = cur.fetchall()
            if not rows:
                await ctx.followup.send(f"No guild raid logs found for **{ign}**.", ephemeral=True)
                return

            total = len(rows)

            # Add offset
            if player_uuid:
                cur.execute("SELECT raid_offset FROM graid_raid_offsets WHERE uuid = %s", (player_uuid,))
                off_row = cur.fetchone()
                if off_row:
                    total += off_row[0]

            type_counts = Counter()
            day_counts = Counter()
            raid_ids = []

            for rid, rtype, completed in rows:
                type_counts[_short(rtype)] += 1
                day_counts[completed.strftime("%Y-%m-%d")] += 1
                raid_ids.append(rid)

            best_day, best_day_count = day_counts.most_common(1)[0]

            # Teammates with display names from discord_links
            unique_ids = list(set(raid_ids))[:500]
            teammates = Counter()
            if unique_ids:
                placeholders = ",".join(["%s"] * len(unique_ids))
                exclude = player_uuid or '00000000-0000-0000-0000-000000000000'
                cur.execute(
                    f"""SELECT COALESCE(dl.ign, glp.ign) AS display_name
                        FROM graid_log_participants glp
                        LEFT JOIN discord_links dl ON glp.uuid = dl.uuid
                        WHERE glp.log_id IN ({placeholders}) AND glp.uuid != %s""",
                    unique_ids + [exclude]
                )
                for (tm_name,) in cur.fetchall():
                    teammates[tm_name] += 1

            type_lines = [f"**{t}**: {c}" for t, c in type_counts.most_common()]
            embed = discord.Embed(
                title=f"Guild Raid Stats: {ign}",
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

    # --- /graid list ---

    @graid.command(name="list", description="HR: Browse guild raid log entries")
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
                # Resolve IGN → UUID for accurate filtering across name changes
                cur.execute("SELECT uuid FROM discord_links WHERE LOWER(ign) = LOWER(%s) AND uuid IS NOT NULL", (ign,))
                uuid_row = cur.fetchone()
                if uuid_row:
                    conditions.append("gl.id IN (SELECT log_id FROM graid_log_participants WHERE uuid = %s)")
                    params.append(uuid_row[0])
                else:
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
                await ctx.followup.send("No guild raid logs found with those filters.", ephemeral=True)
                return

            # Display names from discord_links
            log_ids = [r[0] for r in rows]
            placeholders = ",".join(["%s"] * len(log_ids))
            cur.execute(
                f"""SELECT glp.log_id, COALESCE(dl.ign, glp.ign) AS display_name
                    FROM graid_log_participants glp
                    LEFT JOIN discord_links dl ON glp.uuid = dl.uuid
                    WHERE glp.log_id IN ({placeholders})
                    ORDER BY display_name""",
                log_ids
            )
            parts_map: dict[int, list[str]] = {}
            for lid, pname in cur.fetchall():
                parts_map.setdefault(lid, []).append(pname)

            lines = []
            for rid, rtype, completed in rows:
                short = _short(rtype)
                names = ", ".join(parts_map.get(rid, []))
                ts = completed.strftime("%m/%d %H:%M")
                lines.append(f"`{ts}` **{short}** — {names}")

            embed = discord.Embed(
                title="Guild Raid Log",
                description="\n".join(lines),
                color=0x3474EB,
            )
            embed.set_footer(text=f"Showing latest {len(rows)} entries")
            await ctx.followup.send(embed=embed)
        finally:
            db.close()

    # --- /graid overview ---

    @graid.command(name="overview", description="HR: Overview of all guild raid activity")
    async def overview(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        db = _db()
        try:
            cur = db.cursor

            # Total raids + offset
            cur.execute("SELECT COUNT(*) FROM graid_logs")
            total_raids = cur.fetchone()[0]
            cur.execute("SELECT COALESCE(SUM(raid_offset), 0) FROM graid_raid_offsets")
            total_raids += cur.fetchone()[0]

            cur.execute("SELECT COUNT(DISTINCT uuid) FROM graid_log_participants")
            unique_players = cur.fetchone()[0]

            cur.execute("SELECT raid_type, COUNT(*) FROM graid_logs GROUP BY raid_type ORDER BY COUNT(*) DESC")
            type_lines = [f"**{_short(rt)}**: {cnt}" for rt, cnt in cur.fetchall()]

            # Top players UUID-first with offsets
            cur.execute("""
                SELECT COALESCE(dl.ign, glp.ign) AS display_name, glp.uuid, COUNT(*) as cnt
                FROM graid_log_participants glp
                LEFT JOIN discord_links dl ON glp.uuid = dl.uuid
                GROUP BY glp.uuid, COALESCE(dl.ign, glp.ign)
                ORDER BY cnt DESC LIMIT 10
            """)
            top_raw = [(name, uuid, cnt) for name, uuid, cnt in cur.fetchall()]

            # Add offsets to top players
            cur.execute("SELECT uuid, raid_offset FROM graid_raid_offsets")
            offsets = {str(u): o for u, o in cur.fetchall()}

            top_with_offsets = []
            for name, uuid, cnt in top_raw:
                total = cnt + offsets.get(str(uuid), 0)
                top_with_offsets.append((name, total))
            top_with_offsets.sort(key=lambda x: -x[1])

            top_lines = [f"`{i+1}.` **{name}** — {total}" for i, (name, total) in enumerate(top_with_offsets[:5])]

            embed = discord.Embed(title="Guild Raid Overview", color=0x3474EB)
            embed.add_field(name="Summary", value=f"**{total_raids}** raids by **{unique_players}** players", inline=False)
            embed.add_field(name="Raid Types", value="\n".join(type_lines) or "—", inline=True)
            embed.add_field(name="Top Players", value="\n".join(top_lines) or "—", inline=True)
            await ctx.followup.send(embed=embed)
        finally:
            db.close()


def setup(client):
    client.add_cog(GraidCommands(client))
