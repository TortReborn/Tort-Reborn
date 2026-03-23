import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from io import BytesIO

import discord
import requests
from discord.ext import commands
from discord.commands import SlashCommandGroup

from Helpers.database import DB
from Helpers.variables import ALL_GUILD_IDS, SNIPE_LOG_CHANNEL_ID

# Display names shown in bot messages
HQ_LOCATIONS = {
    'BT':     "Bandit's Toll",
    'CC':     "Corkus City",
    'CO':     "Cinfras Outskirts",
    'BTRAIL': "Bloody Trail",
    'CI':     "Central Islands",
    'NWE':    "Nivla Woods Exit",
    'PTT':    "Path to Talor",
    'CW':     "Corrupted Warfront",
    'NN':     "Nodguj Nation",
    'NR':     "Nomad's Refugee",
    'MBP':    "Mine Base Plains",
    'AL':     "Almuji",
}

# Exact keys as they appear in territories_verbose.json
_HQ_TERRITORY_NAMES = {
    'BT':     "Bandit's Toll",
    'CC':     "Corkus City",
    'CO':     "Cinfras Outskirts",
    'BTRAIL': "Bloody Trail",
    'CI':     "Central Islands",
    'NWE':    "Nivla Woods Exit",
    'PTT':    "Path to Talor",
    'CW':     "Corrupted Warfront",
    'NN':     "Nodguj Nation",
    'NR':     "Nomads' Refuge",
    'MBP':    "Mine Base Plains",
    'AL':     "Almuj",
}

_terr_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'territories_verbose.json')
with open(_terr_path, encoding='utf-8') as _f:
    _territories = json.load(_f)

# Max connections per HQ derived from Trading Routes count
HQ_MAX_CONNS = {
    abbr: len(_territories[name]['Trading Routes'])
    for abbr, name in _HQ_TERRITORY_NAMES.items()
}

ROLE_CHOICES  = ['Tank', 'Healer', 'DPS', 'Solo']
HQ_CHOICES    = list(HQ_LOCATIONS.keys())
_ROLE_ORDER   = ['Healer', 'Tank', 'DPS', 'Solo']


def _is_dry(hq: str, conns: int) -> bool:
    return conns == HQ_MAX_CONNS.get(hq, -1)


def _format_participants_log(pairs: list[tuple[str, str]]) -> str:
    """Group players by role (Healer → Tank → DPS → Solo), format as 'P1 P2 Role'."""
    grouped = defaultdict(list)
    for ign, role in pairs:
        grouped[role].append(ign)
    parts = []
    for role in _ROLE_ORDER:
        if role in grouped:
            parts.append(" ".join(grouped[role]) + " " + role)
    return " / ".join(parts)


class SnipeTracker(commands.Cog):
    def __init__(self, client):
        self.client = client

    snipe = SlashCommandGroup(
        "snipe",
        "Snipe tracking commands",
        guild_ids=ALL_GUILD_IDS,
    )

    @snipe.command(name="log", description="Log a territory snipe")
    async def log_snipe(
        self,
        ctx: discord.ApplicationContext,
        participant1:   discord.Option(str, "Participant 1 IGN", required=True),
        role1:          discord.Option(str, "Role for participant 1", choices=ROLE_CHOICES, required=True),
        hq:             discord.Option(str, "HQ location", choices=HQ_CHOICES, required=True),
        difficulty:     discord.Option(int, "Difficulty in thousands (e.g. 192 for 192k)", required=True),
        guild:          discord.Option(str, "Guild tag that owned the HQ", required=True),
        conns:          discord.Option(int, "How many connections the HQ had (0–6)", required=True, min_value=0, max_value=6),
        participant2:   discord.Option(str, "Participant 2 IGN", required=False, default=None),
        role2:          discord.Option(str, "Role for participant 2", choices=ROLE_CHOICES, required=False, default=None),
        participant3:   discord.Option(str, "Participant 3 IGN", required=False, default=None),
        role3:          discord.Option(str, "Role for participant 3", choices=ROLE_CHOICES, required=False, default=None),
        participant4:   discord.Option(str, "Participant 4 IGN", required=False, default=None),
        role4:          discord.Option(str, "Role for participant 4", choices=ROLE_CHOICES, required=False, default=None),
        participant5:   discord.Option(str, "Participant 5 IGN", required=False, default=None),
        role5:          discord.Option(str, "Role for participant 5", choices=ROLE_CHOICES, required=False, default=None),
        snipe_date:     discord.Option(int, "Unix timestamp of snipe (defaults to now)", required=False, default=None),
        log_to_channel: discord.Option(bool, "Post this snipe to the snipe log channel", required=False, default=False),
        image:          discord.Option(discord.Attachment, "Screenshot of the snipe result (required when logging to channel)", required=False, default=None),
    ):
        await ctx.defer(ephemeral=True)

        # Check War Trainer role
        if not discord.utils.get(ctx.author.roles, name="War Trainer"):
            await ctx.followup.send(":no_entry: You must have the **War Trainer** role to log snipes.", ephemeral=True)
            return

        # Validate log_to_channel requires an image
        if log_to_channel and image is None:
            await ctx.followup.send(":no_entry: You must attach a screenshot when logging to the snipe channel.", ephemeral=True)
            return

        # Build and validate participant/role pairs
        pairs = [(participant1, role1)]
        for p, r, n in [
            (participant2, role2, 2),
            (participant3, role3, 3),
            (participant4, role4, 4),
            (participant5, role5, 5),
        ]:
            if p is not None and r is None:
                await ctx.followup.send(f":no_entry: You must also provide a role for participant {n}.", ephemeral=True)
                return
            if p is not None:
                pairs.append((p, r))

        ts = snipe_date if snipe_date is not None else int(time.time())
        dry = _is_dry(hq, conns)

        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                """
                INSERT INTO snipe_logs (hq, difficulty, sniped_at, guild_tag, conns, logged_by)
                VALUES (%s, %s, to_timestamp(%s), %s, %s, %s)
                RETURNING id
                """,
                (hq, difficulty, ts, guild.upper(), str(conns), ctx.author.id)
            )
            snipe_id = db.cursor.fetchone()[0]

            for ign, role in pairs:
                db.cursor.execute(
                    """
                    INSERT INTO snipe_participants (snipe_id, ign, role)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (snipe_id, ign) DO NOTHING
                    """,
                    (snipe_id, ign, role)
                )

            db.connection.commit()
        finally:
            db.close()

        # Ephemeral confirmation embed
        conn_display = f"{conns} (Dry)" if dry else str(conns)
        embed = discord.Embed(
            title=":crossed_swords: Snipe Logged",
            description=f"**{HQ_LOCATIONS[hq]}** sniped from **{guild.upper()}**",
            color=0x2ecc71
        )
        embed.add_field(name="Difficulty", value=f"{difficulty}k", inline=True)
        embed.add_field(name="Connections", value=conn_display, inline=True)
        embed.add_field(
            name="Participants",
            value="\n".join(f"**{ign}** — {role}" for ign, role in pairs),
            inline=False
        )
        await ctx.followup.send(embed=embed, ephemeral=True)

        # Post to snipe log channel if requested
        if log_to_channel:
            snipe_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            date_str = snipe_dt.strftime('%d/%m/%y')
            participants_str = _format_participants_log(pairs)
            diff_label = "Drysnipe" if dry else f"{conns} Conns"

            log_text = (
                f"**Date:** {date_str}\n"
                f"**Participants:** {participants_str}\n"
                f"**Location:** {HQ_LOCATIONS[hq]} ({guild.upper()})\n"
                f"**Difficulty:** {diff_label} / {difficulty}k\n"
                f"**Result:** Success"
            )

            resp = requests.get(image.url)
            img_file = discord.File(BytesIO(resp.content), filename=image.filename)

            channel = ctx.bot.get_channel(SNIPE_LOG_CHANNEL_ID)
            if channel:
                await channel.send(content=log_text, file=img_file)

    @snipe.command(name="stats", description="View snipe statistics for a player")
    async def snipe_stats(
        self,
        ctx: discord.ApplicationContext,
        ign: discord.Option(str, "Player IGN", required=True),
    ):
        await ctx.defer()

        db = DB()
        db.connect()
        try:
            # Total snipes + leaderboard rank by total
            db.cursor.execute(
                """
                SELECT total, rank FROM (
                    SELECT ign, COUNT(*) AS total,
                           RANK() OVER (ORDER BY COUNT(*) DESC) AS rank
                    FROM snipe_participants
                    GROUP BY ign
                ) ranked WHERE ign = %s
                """,
                (ign,)
            )
            rank_total_row = db.cursor.fetchone()
            total_snipes  = rank_total_row[0] if rank_total_row else 0
            rank_total    = rank_total_row[1] if rank_total_row else None

            # Personal best + leaderboard rank by difficulty
            db.cursor.execute(
                """
                SELECT hq, best_diff, rank FROM (
                    SELECT sp.ign,
                           MAX(sl.difficulty) AS best_diff,
                           (SELECT sl2.hq FROM snipe_logs sl2
                            JOIN snipe_participants sp2 ON sp2.snipe_id = sl2.id
                            WHERE sp2.ign = sp.ign
                            ORDER BY sl2.difficulty DESC LIMIT 1) AS hq,
                           RANK() OVER (ORDER BY MAX(sl.difficulty) DESC) AS rank
                    FROM snipe_logs sl
                    JOIN snipe_participants sp ON sp.snipe_id = sl.id
                    GROUP BY sp.ign
                ) ranked WHERE ign = %s
                """,
                (ign,)
            )
            pb_row     = db.cursor.fetchone()
            rank_diff  = pb_row[2] if pb_row else None

            # 0-conn count
            db.cursor.execute(
                """
                SELECT COUNT(*)
                FROM snipe_logs sl
                JOIN snipe_participants sp ON sp.snipe_id = sl.id
                WHERE sp.ign = %s AND sl.conns = '0'
                """,
                (ign,)
            )
            zero_conn_count = db.cursor.fetchone()[0]

            # Dry snipes — fetch hq+conns for all snipes, check in Python
            db.cursor.execute(
                """
                SELECT sl.hq, sl.conns
                FROM snipe_logs sl
                JOIN snipe_participants sp ON sp.snipe_id = sl.id
                WHERE sp.ign = %s
                """,
                (ign,)
            )
            dry_snipes = sum(
                1 for hq_abbr, c in db.cursor.fetchall()
                if c.lstrip('-').isdigit() and _is_dry(hq_abbr, int(c))
            )

            # First and latest snipe timestamps
            db.cursor.execute(
                """
                SELECT MIN(sl.sniped_at), MAX(sl.sniped_at)
                FROM snipe_logs sl
                JOIN snipe_participants sp ON sp.snipe_id = sl.id
                WHERE sp.ign = %s
                """,
                (ign,)
            )
            time_row = db.cursor.fetchone()
            first_snipe  = time_row[0] if time_row else None
            latest_snipe = time_row[1] if time_row else None

            # Unique guilds and unique HQs
            db.cursor.execute(
                """
                SELECT COUNT(DISTINCT sl.guild_tag), COUNT(DISTINCT sl.hq)
                FROM snipe_logs sl
                JOIN snipe_participants sp ON sp.snipe_id = sl.id
                WHERE sp.ign = %s
                """,
                (ign,)
            )
            uniq_row       = db.cursor.fetchone()
            unique_guilds  = uniq_row[0] if uniq_row else 0
            unique_hqs     = uniq_row[1] if uniq_row else 0

            # Most snipes in a single day
            db.cursor.execute(
                """
                SELECT MAX(daily_count) FROM (
                    SELECT COUNT(*) AS daily_count
                    FROM snipe_logs sl
                    JOIN snipe_participants sp ON sp.snipe_id = sl.id
                    WHERE sp.ign = %s
                    GROUP BY DATE(sl.sniped_at)
                ) daily
                """,
                (ign,)
            )
            most_in_day = db.cursor.fetchone()[0] or 0

            # Top 3 guilds sniped
            db.cursor.execute(
                """
                SELECT sl.guild_tag, COUNT(*) AS snipe_count
                FROM snipe_logs sl
                JOIN snipe_participants sp ON sp.snipe_id = sl.id
                WHERE sp.ign = %s
                GROUP BY sl.guild_tag
                ORDER BY snipe_count DESC
                LIMIT 3
                """,
                (ign,)
            )
            top_guilds = db.cursor.fetchall()

            # Most sniped HQs
            db.cursor.execute(
                """
                SELECT sl.hq, COUNT(*) AS snipe_count
                FROM snipe_logs sl
                JOIN snipe_participants sp ON sp.snipe_id = sl.id
                WHERE sp.ign = %s
                GROUP BY sl.hq
                ORDER BY snipe_count DESC
                """,
                (ign,)
            )
            hq_rows = db.cursor.fetchall()

            db.cursor.execute(
                """
                SELECT other_sp.ign, COUNT(*) AS shared_count
                FROM snipe_participants other_sp
                WHERE other_sp.snipe_id IN (
                    SELECT sp.snipe_id FROM snipe_participants sp WHERE sp.ign = %s
                )
                  AND other_sp.ign != %s
                GROUP BY other_sp.ign
                ORDER BY shared_count DESC
                LIMIT 3
                """,
                (ign, ign)
            )
            teammate_rows = db.cursor.fetchall()

        finally:
            db.close()

        embed = discord.Embed(
            title=f":dart: Snipe Stats — {ign}",
            color=0x3474eb
        )

        # Personal best
        if pb_row:
            hq_abbr, diff, _ = pb_row
            embed.add_field(
                name="Personal Best",
                value=f"**{HQ_LOCATIONS.get(hq_abbr, hq_abbr)}** — {diff}k",
                inline=False
            )
        else:
            embed.add_field(name="Personal Best", value="No snipes logged yet.", inline=False)

        def _fmt_ts(ts) -> str:
            return ts.strftime('%d/%m/%y') if ts else "—"

        # Row: rank by total | rank by difficulty | total snipes
        embed.add_field(name="Rank (Total)",      value=f"#{rank_total}" if rank_total else "—", inline=True)
        embed.add_field(name="Rank (Difficulty)", value=f"#{rank_diff}"  if rank_diff  else "—", inline=True)
        embed.add_field(name="Total Snipes",      value=str(total_snipes),                        inline=True)

        # Row: dry snipes | 0-conn snipes | best day
        embed.add_field(name="Dry Snipes",    value=str(dry_snipes),      inline=True)
        embed.add_field(name="0 Conn Snipes", value=str(zero_conn_count), inline=True)
        embed.add_field(name="Best Day",      value=str(most_in_day),     inline=True)

        # Row: unique HQs | unique guilds | spacer
        embed.add_field(name="Unique HQs",    value=str(unique_hqs),    inline=True)
        embed.add_field(name="Unique Guilds", value=str(unique_guilds), inline=True)
        embed.add_field(name="\u200b",        value="\u200b",           inline=True)

        # Row: first snipe | latest snipe | spacer
        embed.add_field(name="First Snipe",  value=_fmt_ts(first_snipe),  inline=True)
        embed.add_field(name="Latest Snipe", value=_fmt_ts(latest_snipe), inline=True)
        embed.add_field(name="\u200b",       value="\u200b",              inline=True)

        # Two list columns side by side
        guilds_text = "\n".join(f"**{tag}** — {count}" for tag, count in top_guilds) if top_guilds else "None yet"
        hq_text = (
            "\n".join(f"**{HQ_LOCATIONS.get(abbr, abbr)}** — {count}" for abbr, count in hq_rows)
            if hq_rows else "None yet"
        )
        embed.add_field(name="Top 3 Guilds Sniped", value=guilds_text, inline=True)
        embed.add_field(name="Most Sniped HQs",     value=hq_text,     inline=True)

        # Top 3 teammates (full width)
        tm_text = (
            "\n".join(
                f"**{tm_ign}** — {count} {'snipe' if count == 1 else 'snipes'} together"
                for tm_ign, count in teammate_rows
            )
            if teammate_rows else "None yet"
        )
        embed.add_field(name="Top 3 Teammates", value=tm_text, inline=False)

        await ctx.followup.send(embed=embed)


def setup(client):
    client.add_cog(SnipeTracker(client))
