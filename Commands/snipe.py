import asyncio
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
from PIL import Image, ImageDraw, ImageFont

from Helpers.classes import PlayerStats
from Helpers.database import DB
from Helpers.functions import addLine, generate_badge, vertical_gradient, round_corners
from Helpers.variables import ALL_GUILD_IDS, SNIPE_LOG_CHANNEL_ID, discord_ranks

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


def _generate_snipe_card(
    ign, total_snipes, rank_total, rank_diff, pb_row,
    dry_snipes, zero_conn_count, most_in_day,
    unique_hqs, unique_guilds, first_snipe, latest_snipe,
    top_guilds, hq_rows, teammate_rows,
    rank_text=None, rank_color=None,
) -> Image.Image:
    W, H = 1300, 600

    # Outer edge gradient + rounded corners
    card = vertical_gradient(W, H, '#3474eb')
    card = round_corners(card, radius=20)

    # Inner dark gradient
    inner = vertical_gradient(W - 40, H - 40, '#0e1e3f', '#05101f')
    card.paste(inner, (20, 20), inner)
    draw = ImageDraw.Draw(card)

    f_title = ImageFont.truetype('images/profile/5x5.ttf',  30)
    f_label = ImageFont.truetype('images/profile/5x5.ttf',  22)
    f_name  = ImageFont.truetype('images/profile/game.ttf', 50)
    f_pb    = ImageFont.truetype('images/profile/game.ttf', 35)
    f_value = ImageFont.truetype('images/profile/game.ttf', 32)
    f_small = ImageFont.truetype('images/profile/game.ttf', 26)

    ACCENT = '#fad51e'
    SEP    = '#3474eb'
    LX     = 38
    LW     = 385
    SEP_X  = 432

    # ── LEFT PANEL ──────────────────────────────────────────────────────────

    draw.text((LX, 28), 'SNIPE STATS', font=f_title, fill=ACCENT)
    addLine(ign, draw, f_name, LX, 62, drop_x=5, drop_y=5)

    # Guild rank badge below the IGN
    if rank_text and rank_color:
        badge = generate_badge(text=rank_text, base_color=rank_color, scale=2)
        card.paste(badge, (LX, 120), badge)

    draw.line([(LX, 162), (LX + LW, 162)], fill=SEP, width=2)

    draw.text((LX, 170), 'PERSONAL BEST', font=f_label, fill=ACCENT)
    if pb_row:
        hq_abbr, diff, _ = pb_row
        addLine(f"{HQ_LOCATIONS.get(hq_abbr, hq_abbr)} \u2014 {diff}k", draw, f_pb, LX, 196, drop_x=4, drop_y=4)
    else:
        draw.text((LX, 196), 'No snipes yet', font=f_pb, fill='#555555')

    draw.line([(LX, 244), (LX + LW, 244)], fill=SEP, width=2)

    def _fmt(ts):
        return ts.strftime('%d/%m/%y') if ts else '\u2014'

    draw.text((LX, 252), 'FIRST SNIPE', font=f_label, fill=ACCENT)
    addLine(_fmt(first_snipe), draw, f_small, LX, 276, drop_x=3, drop_y=3)
    draw.text((LX, 312), 'LATEST SNIPE', font=f_label, fill=ACCENT)
    addLine(_fmt(latest_snipe), draw, f_small, LX, 336, drop_x=3, drop_y=3)

    draw.line([(LX, 378), (LX + LW, 378)], fill=SEP, width=2)

    draw.text((LX, 386), 'TOP TEAMMATES', font=f_label, fill=ACCENT)
    if teammate_rows:
        for i, (tm_ign, count) in enumerate(teammate_rows[:3]):
            s = 'snipe' if count == 1 else 'snipes'
            addLine(f'{tm_ign} \u2014 {count} {s}', draw, f_small, LX, 412 + i * 36, drop_x=3, drop_y=3)
    else:
        draw.text((LX, 412), 'None yet', font=f_small, fill='#555555')

    # Vertical separator
    draw.line([(SEP_X, 22), (SEP_X, 578)], fill=SEP, width=2)

    # ── RIGHT PANEL — STAT BOXES ────────────────────────────────────────────

    BOX_W, BOX_H = 190, 82
    COLS_X = [450, 650, 850, 1050]
    ROWS_Y = [22,  112]

    box_img = Image.new('RGBA', (BOX_W, BOX_H), (0, 0, 0, 0))
    ImageDraw.Draw(box_img).rounded_rectangle(
        ((0, 0), (BOX_W - 1, BOX_H - 1)), fill=(0, 0, 0, 55), radius=8
    )

    stat_entries = [
        ('Total Snipes',  str(total_snipes)),
        ('Rank (Total)',  f'#{rank_total}' if rank_total else '\u2014'),
        ('Rank (Diff.)',  f'#{rank_diff}'  if rank_diff  else '\u2014'),
        ('Dry Snipes',    str(dry_snipes)),
        ('0 Conn Snipes', str(zero_conn_count)),
        ('Best Day',      str(most_in_day)),
        ('Unique HQs',    str(unique_hqs)),
        ('Unique Guilds', str(unique_guilds)),
    ]

    for idx, (label, value) in enumerate(stat_entries):
        bx = COLS_X[idx % 4]
        by = ROWS_Y[idx // 4]
        card.paste(box_img, (bx, by), box_img)
        draw.text((bx + 8, by + 7), label, font=f_label, fill=ACCENT)
        val_w = draw.textbbox((0, 0), value, font=f_value)[2]
        addLine(value, draw, f_value, bx + BOX_W - 8 - val_w, by + 38, drop_x=3, drop_y=3)

    # ── RIGHT PANEL — LISTS ─────────────────────────────────────────────────

    LIST_Y  = 212
    LIST_X  = 450
    LIST_W  = 360
    LIST_H  = H - 40 - LIST_Y  # 348px

    list_bg = Image.new('RGBA', (LIST_W, LIST_H), (0, 0, 0, 0))
    ImageDraw.Draw(list_bg).rounded_rectangle(
        ((0, 0), (LIST_W - 1, LIST_H - 1)), fill=(0, 0, 0, 40), radius=8
    )
    card.paste(list_bg, (LIST_X, LIST_Y + 18), list_bg)

    # Top 3 Guilds Sniped
    draw.text((LIST_X + 8, LIST_Y + 26), 'TOP 3 GUILDS SNIPED', font=f_label, fill=ACCENT)
    if top_guilds:
        for i, (tag, count) in enumerate(top_guilds[:3]):
            addLine(f'{tag} \u2014 {count}', draw, f_small, LIST_X + 8, LIST_Y + 52 + i * 34, drop_x=3, drop_y=3)
    else:
        draw.text((LIST_X + 8, LIST_Y + 52), 'None yet', font=f_small, fill='#555555')

    # Most Sniped HQs (stacked below guilds)
    SECT2_Y = LIST_Y + 162
    draw.line([(LIST_X + 8, SECT2_Y), (LIST_X + LIST_W - 8, SECT2_Y)], fill=SEP, width=1)
    draw.text((LIST_X + 8, SECT2_Y + 8), 'MOST SNIPED HQs', font=f_label, fill=ACCENT)
    if hq_rows:
        for i, (abbr, count) in enumerate(hq_rows[:3]):
            addLine(f'{HQ_LOCATIONS.get(abbr, abbr)} \u2014 {count}', draw, f_small, LIST_X + 8, SECT2_Y + 32 + i * 34, drop_x=3, drop_y=3)
    else:
        draw.text((LIST_X + 8, SECT2_Y + 32), 'None yet', font=f_small, fill='#555555')

    return card


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
        participants:   discord.Option(str, "Participants as 'IGN Role, IGN Role, ...' e.g. 'Steve Tank, Alex Healer'", required=True),
        hq:             discord.Option(str, "HQ location", choices=HQ_CHOICES, required=True),
        difficulty:     discord.Option(int, "Difficulty in thousands (e.g. 192 for 192k)", required=True),
        guild:          discord.Option(str, "Guild tag that owned the HQ", required=True),
        conns:          discord.Option(int, "How many connections the HQ had (0–6)", required=True, min_value=0, max_value=6),
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

        # Parse "IGN Role, IGN Role, ..." CSV input
        pairs = []
        role_choices_lower = {r.lower(): r for r in ROLE_CHOICES}
        for i, entry in enumerate(participants.split(','), start=1):
            parts = entry.strip().split()
            if len(parts) < 2:
                await ctx.followup.send(
                    f":no_entry: Entry {i} `{entry.strip()}` is missing a role. Format: `IGN Role, IGN Role, ...`",
                    ephemeral=True
                )
                return
            role_raw = parts[-1]
            ign = ' '.join(parts[:-1])
            role = role_choices_lower.get(role_raw.lower())
            if role is None:
                await ctx.followup.send(
                    f":no_entry: Unknown role `{role_raw}` for `{ign}`. Valid roles: {', '.join(ROLE_CHOICES)}.",
                    ephemeral=True
                )
                return
            pairs.append((ign, role))

        if not pairs:
            await ctx.followup.send(":no_entry: You must provide at least one participant.", ephemeral=True)
            return

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
            title="Snipe Logged",
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

        # Fetch guild rank for badge display
        rank_text = rank_color = None
        try:
            player = await asyncio.to_thread(PlayerStats, ign, 1)
            if not player.error:
                if player.taq and player.linked and player.rank in discord_ranks:
                    rank_text  = player.rank.upper()
                    rank_color = discord_ranks[player.rank]['color']
                elif player.guild_rank:
                    rank_text  = player.guild_rank.upper()
                    rank_color = '#a0aeb0'
        except Exception:
            pass

        card = _generate_snipe_card(
            ign, total_snipes, rank_total, rank_diff, pb_row,
            dry_snipes, zero_conn_count, most_in_day,
            unique_hqs, unique_guilds, first_snipe, latest_snipe,
            top_guilds, hq_rows, teammate_rows,
            rank_text=rank_text, rank_color=rank_color,
        )
        buf = BytesIO()
        card.save(buf, format='PNG')
        buf.seek(0)
        await ctx.followup.send(file=discord.File(buf, filename=f'snipe_{ign}.png'))


def setup(client):
    client.add_cog(SnipeTracker(client))
