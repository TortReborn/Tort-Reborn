import asyncio
import json
import math
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from io import BytesIO

import discord
import requests
from discord.ext import commands, pages
from discord.commands import SlashCommandGroup, slash_command
from PIL import Image, ImageDraw, ImageFont

from Helpers.classes import Page, PlayerStats
from Helpers.database import DB
from Helpers.functions import addLine, generate_badge, vertical_gradient, round_corners
from Helpers.snipe_utils import HQ_CHOICES, display_hq, is_dry
from Helpers.variables import ALL_GUILD_IDS, SNIPE_LOG_CHANNEL_ID, discord_ranks

ROLE_CHOICES    = ['Tank', 'Healer', 'DPS', 'Solo']
_ROLE_ORDER     = ['Healer', 'Tank', 'DPS', 'Solo']
LB_SORT_CHOICES = ['Total Snipes', 'Personal Best', 'Best Streak', 'Current Streak']
_LB_PER_PAGE    = 10

_SEASON_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'war_season.json')

# ── Season helpers ───────────────────────────────────────────────────────────

def _get_current_season() -> int:
    try:
        with open(_SEASON_FILE, encoding='utf-8') as f:
            return json.load(f).get('current_season', 1)
    except (FileNotFoundError, json.JSONDecodeError):
        return 1


def _set_current_season(season: int) -> None:
    with open(_SEASON_FILE, 'w', encoding='utf-8') as f:
        json.dump({'current_season': season}, f)


def _season_clause(season_param) -> tuple[str, list]:
    """None → current season, 0 → all-time, N → specific season."""
    if season_param == 0:
        return '', []
    s = season_param if season_param is not None else _get_current_season()
    return 'AND sl.season = %s', [s]


def _season_label(season_param) -> str:
    if season_param == 0:
        return 'All Time'
    s = season_param if season_param is not None else _get_current_season()
    return f'Season {s}'


# ── Streak computation ───────────────────────────────────────────────────────

def _compute_streaks(dates: list) -> tuple[int, int]:
    if not dates:
        return 0, 0
    date_set = set(dates)
    dates_sorted = sorted(date_set)
    best = streak = 1
    for i in range(1, len(dates_sorted)):
        if (dates_sorted[i] - dates_sorted[i - 1]).days == 1:
            streak += 1
            best = max(best, streak)
        else:
            streak = 1
    today = datetime.now(timezone.utc).date()
    start = today if today in date_set else (
        today - timedelta(days=1) if (today - timedelta(days=1)) in date_set else None
    )
    current = 0
    if start:
        check = start
        while check in date_set:
            current += 1
            check -= timedelta(days=1)
    return best, current


# ── Leaderboard sort keys ────────────────────────────────────────────────────

_LB_SORT_KEY = {
    'Total Snipes':   lambda x: (-x['total'],       x['ign']),
    'Personal Best':  lambda x: (-x['best_diff'],   x['ign']),
    'Best Streak':    lambda x: (-x['best_streak'],  x['ign']),
    'Current Streak': lambda x: (-x['cur_streak'],  x['ign']),
}

# ── Paginator helper ─────────────────────────────────────────────────────────

def _make_paginator(book: list) -> pages.Paginator:
    p = pages.Paginator(pages=book)
    p.add_button(pages.PaginatorButton("prev",  emoji="<:left_arrow:1198703157501509682>",   style=discord.ButtonStyle.red))
    p.add_button(pages.PaginatorButton("next",  emoji="<:right_arrow:1198703156088021112>",  style=discord.ButtonStyle.green))
    p.add_button(pages.PaginatorButton("first", emoji="<:first_arrows:1198703152204103760>", style=discord.ButtonStyle.blurple))
    p.add_button(pages.PaginatorButton("last",  emoji="<:last_arrows:1198703153726627880>",  style=discord.ButtonStyle.blurple))
    return p


def _pages_from_cards(cards: list, prefix: str) -> list:
    """Convert a list of PIL Images to a list of Page objects with Discord Files."""
    book = []
    for i, card in enumerate(cards):
        buf = BytesIO()
        card.save(buf, format='PNG')
        buf.seek(0)
        book.append(Page(content='', files=[discord.File(buf, filename=f'{prefix}_{i}.png')]))
    return book


# ── Comprehensive leaderboard data fetch ─────────────────────────────────────

def _fetch_lb_data(db, sc: str, sp: list) -> list:
    """Fetch one row per (ign, snipe) and aggregate per player in Python."""
    db.cursor.execute(
        f"SELECT sp.ign, sl.hq, sl.difficulty, "
        f"DATE(sl.sniped_at AT TIME ZONE 'UTC') "
        f"FROM snipe_participants sp "
        f"JOIN snipe_logs sl ON sl.id = sp.snipe_id "
        f"WHERE 1=1 {sc}",
        sp
    )
    raw = db.cursor.fetchall()
    accum = defaultdict(lambda: {'best_diff': 0, 'best_hq': None, 'total': 0, 'dates': set()})
    for ign, hq, diff, snipe_date in raw:
        pd = accum[ign]
        pd['total'] += 1
        if diff > pd['best_diff']:
            pd['best_diff'] = diff
            pd['best_hq'] = hq
        if snipe_date:
            pd['dates'].add(snipe_date)
    result = []
    for ign, pd in accum.items():
        best_streak, cur_streak = _compute_streaks(list(pd['dates']))
        result.append({
            'ign': ign, 'total': pd['total'],
            'best_diff': pd['best_diff'], 'best_hq': pd['best_hq'],
            'best_streak': best_streak, 'cur_streak': cur_streak,
        })
    return result


# ── Card base ────────────────────────────────────────────────────────────────

def _card_base(W: int, H: int):
    card = vertical_gradient(W, H, '#3474eb')
    card = round_corners(card, radius=20)
    inner = vertical_gradient(W - 40, H - 40, '#0e1e3f', '#05101f')
    card.paste(inner, (20, 20), inner)
    return card, ImageDraw.Draw(card)


# ── Shared table header renderer ─────────────────────────────────────────────

def _draw_table_header(draw, W, f_title, f_label, title, subtitle,
                       headers, cx, sort_by, sort_cols,
                       ACCENT, DIM, SEP, WHITE):
    draw.text((38, 26), title, font=f_title, fill=ACCENT)
    draw.text((38, 62), subtitle, font=f_label, fill=WHITE)
    draw.line([(28, 96), (W - 28, 96)], fill=SEP, width=2)
    for header, x, sc in zip(headers, cx, sort_cols):
        color = ACCENT if sc == sort_by else DIM
        label = header + ' \u25bc' if sc == sort_by else header
        draw.text((x, 100), label, font=f_label, fill=color)
    draw.line([(28, 120), (W - 28, 120)], fill=SEP, width=1)


def _draw_table_footer(draw, W, H, f_label, page_num, total_pages, ACCENT):
    page_str = f'Page {page_num} of {total_pages}'
    ph = draw.textbbox((0, 0), page_str, font=f_label)[3]
    pw = draw.textbbox((0, 0), page_str, font=f_label)[2]
    draw.text((W - 22 - pw, H - 20 - ph), page_str, font=f_label, fill=ACCENT)


def _fit_font(text: str, draw, path: str, max_size: int, max_w: int) -> ImageFont.FreeTypeFont:
    """Return the largest font ≤ max_size that renders text within max_w pixels."""
    for size in range(max_size, 13, -2):
        font = ImageFont.truetype(path, size)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_w:
            return font
    return ImageFont.truetype(path, 14)


def _row_bg_img(W, ROW_H=40):
    row_bg = Image.new('RGBA', (W - 56, ROW_H - 2), (0, 0, 0, 0))
    ImageDraw.Draw(row_bg).rounded_rectangle(
        ((0, 0), (W - 57, ROW_H - 3)), fill=(255, 255, 255, 15), radius=6
    )
    return row_bg


# ── Card generators ──────────────────────────────────────────────────────────

def _generate_snipe_card(
    ign, total_snipes, rank_total, rank_diff, pb_row,
    dry_snipes, zero_conn_count, most_in_day,
    unique_hqs, unique_guilds, first_snipe, latest_snipe,
    top_guilds, hq_rows, teammate_rows,
    top_role, streak_best, streak_cur, seasons_active,
    rank_text=None, rank_color=None,
) -> Image.Image:
    W, H = 1300, 660
    card, draw = _card_base(W, H)

    f_title = ImageFont.truetype('images/profile/5x5.ttf',  30)
    f_label = ImageFont.truetype('images/profile/5x5.ttf',  22)
    f_name  = ImageFont.truetype('images/profile/game.ttf', 50)
    f_pb    = ImageFont.truetype('images/profile/game.ttf', 35)
    f_value = ImageFont.truetype('images/profile/game.ttf', 32)
    f_small = ImageFont.truetype('images/profile/game.ttf', 26)

    ACCENT = '#fad51e'
    SEP    = '#3474eb'
    LX, LW, SEP_X = 38, 385, 432

    # ── Left panel ───────────────────────────────────────────────────────────
    draw.text((LX, 28), 'SNIPE STATS', font=f_title, fill=ACCENT)
    addLine(ign, draw, f_name, LX, 62, drop_x=5, drop_y=5)

    if rank_text and rank_color:
        badge = generate_badge(text=rank_text, base_color=rank_color, scale=2)
        card.paste(badge, (LX, 120), badge)

    draw.line([(LX, 162), (LX + LW, 162)], fill=SEP, width=2)
    draw.text((LX, 170), 'PERSONAL BEST', font=f_label, fill=ACCENT)

    if pb_row:
        hq_abbr, diff, _ = pb_row
        pb_text = f"{display_hq(hq_abbr)} \u2014 {diff}k"
        if draw.textbbox((0, 0), pb_text, font=f_pb)[2] > LW - 8:
            pb_text = f"{hq_abbr} \u2014 {diff}k"
        addLine(pb_text, draw, f_pb, LX, 196, drop_x=4, drop_y=4)
    else:
        draw.text((LX, 196), 'No snipes yet', font=f_pb, fill='#555555')

    draw.line([(LX, 244), (LX + LW, 244)], fill=SEP, width=2)

    def _fmt(ts):
        return ts.strftime('%d/%m/%y') if ts else '\u2014'

    draw.text((LX, 252), 'FIRST SNIPE',  font=f_label, fill=ACCENT)
    addLine(_fmt(first_snipe),  draw, f_small, LX, 276, drop_x=3, drop_y=3)
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

    draw.line([(SEP_X, 22), (SEP_X, H - 22)], fill=SEP, width=2)

    # ── Right panel — stat boxes (3×4) ───────────────────────────────────────
    BOX_W, BOX_H = 190, 82
    COLS_X = [450, 650, 850, 1050]
    ROWS_Y = [22, 112, 202]

    box_img = Image.new('RGBA', (BOX_W, BOX_H), (0, 0, 0, 0))
    ImageDraw.Draw(box_img).rounded_rectangle(
        ((0, 0), (BOX_W - 1, BOX_H - 1)), fill=(0, 0, 0, 55), radius=8
    )

    stat_entries = [
        ('Total Snipes',   str(total_snipes)),
        ('Rank (Total)',   f'#{rank_total}' if rank_total else '\u2014'),
        ('Rank (Diff.)',   f'#{rank_diff}'  if rank_diff  else '\u2014'),
        ('Dry Snipes',     str(dry_snipes)),
        ('0 Conn Snipes',  str(zero_conn_count)),
        ('Best Day',       str(most_in_day)),
        ('Unique HQs',     str(unique_hqs)),
        ('Unique Guilds',  str(unique_guilds)),
        ('Top Role',       top_role or '\u2014'),
        ('Best Streak',    str(streak_best)),
        ('Cur. Streak',    str(streak_cur)),
        ('Seasons Active', str(seasons_active)),
    ]

    for idx, (label, value) in enumerate(stat_entries):
        bx = COLS_X[idx % 4]
        by = ROWS_Y[idx // 4]
        card.paste(box_img, (bx, by), box_img)
        draw.text((bx + 8, by + 7), label, font=f_label, fill=ACCENT)
        v_font = _fit_font(value, draw, 'images/profile/game.ttf', 32, BOX_W - 16)
        val_w  = draw.textbbox((0, 0), value, font=v_font)[2]
        addLine(value, draw, v_font, bx + BOX_W - 8 - val_w, by + 38, drop_x=3, drop_y=3)

    # ── Right panel — lists ───────────────────────────────────────────────────
    LIST_Y, LIST_X, LIST_W = 294, 450, 360
    LIST_H = H - 40 - LIST_Y

    list_bg = Image.new('RGBA', (LIST_W, LIST_H), (0, 0, 0, 0))
    ImageDraw.Draw(list_bg).rounded_rectangle(
        ((0, 0), (LIST_W - 1, LIST_H - 1)), fill=(0, 0, 0, 40), radius=8
    )
    card.paste(list_bg, (LIST_X, LIST_Y + 18), list_bg)

    draw.text((LIST_X + 8, LIST_Y + 26), 'TOP 3 GUILDS SNIPED', font=f_label, fill=ACCENT)
    if top_guilds:
        for i, (tag, count) in enumerate(top_guilds[:3]):
            addLine(f'{tag} \u2014 {count}', draw, f_small, LIST_X + 8, LIST_Y + 52 + i * 34, drop_x=3, drop_y=3)
    else:
        draw.text((LIST_X + 8, LIST_Y + 52), 'None yet', font=f_small, fill='#555555')

    SECT2_Y = LIST_Y + 162
    draw.line([(LIST_X + 8, SECT2_Y), (LIST_X + LIST_W - 8, SECT2_Y)], fill=SEP, width=1)
    draw.text((LIST_X + 8, SECT2_Y + 8), 'MOST SNIPED HQs', font=f_label, fill=ACCENT)
    if hq_rows:
        for i, (abbr, count) in enumerate(hq_rows[:3]):
            hq_text = f'{display_hq(abbr)} \u2014 {count}'
            if draw.textbbox((0, 0), hq_text, font=f_small)[2] > LIST_W - 16:
                hq_text = f'{abbr} \u2014 {count}'
            addLine(hq_text, draw, f_small, LIST_X + 8, SECT2_Y + 32 + i * 34, drop_x=3, drop_y=3)
    else:
        draw.text((LIST_X + 8, SECT2_Y + 32), 'None yet', font=f_small, fill='#555555')

    return card


def _generate_lb_card(page_rows, sort_by, season_label, page_num, total_pages, start_rank):
    W = 1000
    H = 130 + _LB_PER_PAGE * 44 + 50  # fixed height so all pages are identical
    card, draw = _card_base(W, H)

    ACCENT, DIM, SEP, WHITE = '#fad51e', '#b09010', '#3474eb', '#ffffff'
    f_title = ImageFont.truetype('images/profile/5x5.ttf',  28)
    f_label = ImageFont.truetype('images/profile/5x5.ttf',  20)
    f_small = ImageFont.truetype('images/profile/game.ttf', 24)

    CX        = [38, 108, 360, 472, 685, 822]
    HEADERS   = ['RANK', 'PLAYER', 'SNIPES', 'BEST DIFF.', 'BEST STREAK', 'CUR. STREAK']
    SORT_COLS = [None, None, 'Total Snipes', 'Personal Best', 'Best Streak', 'Current Streak']

    _draw_table_header(draw, W, f_title, f_label,
                       'SNIPE LEADERBOARD',
                       f'{season_label}  \u2022  Sorted by: {sort_by}',
                       HEADERS, CX, sort_by, SORT_COLS,
                       ACCENT, DIM, SEP, WHITE)

    row_bg = _row_bg_img(W)
    if page_rows:
        for i, row in enumerate(page_rows):
            ry = 128 + i * 44
            if i % 2 == 0:
                card.paste(row_bg, (28, ry - 2), row_bg)
            rank = start_rank + i
            best_str = f"{row['best_hq']} \u2014 {row['best_diff']}k" if row['best_hq'] else '\u2014'
            draw.text((CX[0], ry + 4), f'#{rank}', font=f_small, fill=ACCENT)
            addLine(str(row['ign']),         draw, f_small, CX[1], ry + 4, drop_x=3, drop_y=3)
            addLine(str(row['total']),       draw, f_small, CX[2], ry + 4, drop_x=3, drop_y=3)
            addLine(best_str,                draw, f_small, CX[3], ry + 4, drop_x=3, drop_y=3)
            addLine(str(row['best_streak']), draw, f_small, CX[4], ry + 4, drop_x=3, drop_y=3)
            addLine(str(row['cur_streak']),  draw, f_small, CX[5], ry + 4, drop_x=3, drop_y=3)
    else:
        draw.text((38, 140), 'No entries yet.', font=f_small, fill='#555555')

    _draw_table_footer(draw, W, H, f_label, page_num, total_pages, ACCENT)
    return card


def _generate_roles_card(page_rows, role, sort_by, season_label, page_num, total_pages, start_rank):
    W = 800
    H = 130 + _LB_PER_PAGE * 44 + 50  # fixed height so all pages are identical
    card, draw = _card_base(W, H)

    ACCENT, DIM, SEP, WHITE = '#fad51e', '#b09010', '#3474eb', '#ffffff'
    f_title = ImageFont.truetype('images/profile/5x5.ttf',  28)
    f_label = ImageFont.truetype('images/profile/5x5.ttf',  20)
    f_small = ImageFont.truetype('images/profile/game.ttf', 24)

    CX        = [38, 108, 450, 620]
    HEADERS   = ['RANK', 'PLAYER', 'TIMES', 'BEST DIFF.']
    SORT_COLS = [None, None, 'Amount', 'Highest Difficulty']

    _draw_table_header(draw, W, f_title, f_label,
                       f'{role.upper()} LEADERBOARD',
                       f'{season_label}  \u2022  Sorted by: {sort_by}',
                       HEADERS, CX, sort_by, SORT_COLS,
                       ACCENT, DIM, SEP, WHITE)

    row_bg = _row_bg_img(W)
    if page_rows:
        for i, (ign, times, best_diff) in enumerate(page_rows):
            ry = 128 + i * 44
            if i % 2 == 0:
                card.paste(row_bg, (28, ry - 2), row_bg)
            rank = start_rank + i
            draw.text((CX[0], ry + 4), f'#{rank}', font=f_small, fill=ACCENT)
            addLine(str(ign),                            draw, f_small, CX[1], ry + 4, drop_x=3, drop_y=3)
            addLine(str(times),                          draw, f_small, CX[2], ry + 4, drop_x=3, drop_y=3)
            addLine(f'{best_diff}k' if best_diff else '\u2014', draw, f_small, CX[3], ry + 4, drop_x=3, drop_y=3)
    else:
        draw.text((38, 140), 'No entries yet.', font=f_small, fill='#555555')

    _draw_table_footer(draw, W, H, f_label, page_num, total_pages, ACCENT)
    return card


def _generate_team_card(rows: list, season_label: str) -> Image.Image:
    ROW_H, W = 40, 1100
    H = max(200, 120 + len(rows) * ROW_H + 40)
    card, draw = _card_base(W, H)

    ACCENT, SEP = '#fad51e', '#3474eb'
    f_title = ImageFont.truetype('images/profile/5x5.ttf',  30)
    f_label = ImageFont.truetype('images/profile/5x5.ttf',  22)
    f_small = ImageFont.truetype('images/profile/game.ttf', 26)

    draw.text((38, 28), 'SNIPE TEAM ROSTER', font=f_title, fill=ACCENT)
    draw.text((38, 66), season_label, font=f_label, fill='#ffffff')
    draw.line([(28, 100), (W - 28, 100)], fill=SEP, width=2)

    CX = [38, 118, 480, 640, 850]
    for h, x in zip(['RANK', 'PLAYER', 'SNIPES', 'BEST DIFF.', 'ROLE'], CX):
        draw.text((x, 104), h, font=f_label, fill=ACCENT)
    draw.line([(28, 126), (W - 28, 126)], fill=SEP, width=1)

    row_bg = _row_bg_img(W, ROW_H)
    if rows:
        for i, (ign, total, best_diff, top_role) in enumerate(rows):
            ry = 134 + i * ROW_H
            if i % 2 == 0:
                card.paste(row_bg, (28, ry - 2), row_bg)
            draw.text((CX[0], ry + 4), f'#{i + 1}', font=f_small, fill=ACCENT)
            addLine(str(ign),    draw, f_small, CX[1], ry + 4, drop_x=3, drop_y=3)
            addLine(str(total),  draw, f_small, CX[2], ry + 4, drop_x=3, drop_y=3)
            addLine(f'{best_diff}k' if best_diff else '\u2014', draw, f_small, CX[3], ry + 4, drop_x=3, drop_y=3)
            addLine(str(top_role) if top_role else '\u2014',    draw, f_small, CX[4], ry + 4, drop_x=3, drop_y=3)
    else:
        draw.text((38, 140), 'No entries yet.', font=f_small, fill='#555555')
    return card


def _generate_duo_card(rows: list, season_label: str) -> Image.Image:
    ROW_H, W = 44, 1000
    H = max(200, 120 + len(rows) * ROW_H + 40)
    card, draw = _card_base(W, H)

    ACCENT, SEP = '#fad51e', '#3474eb'
    f_title = ImageFont.truetype('images/profile/5x5.ttf',  30)
    f_label = ImageFont.truetype('images/profile/5x5.ttf',  22)
    f_small = ImageFont.truetype('images/profile/game.ttf', 26)

    draw.text((38, 28), 'SNIPE DUO LEADERBOARD', font=f_title, fill=ACCENT)
    draw.text((38, 66), season_label, font=f_label, fill='#ffffff')
    draw.line([(28, 100), (W - 28, 100)], fill=SEP, width=2)

    CX = [38, 100, 700, 860]
    for h, x in zip(['RANK', 'DUO', 'SHARED', 'BEST DIFF.'], CX):
        draw.text((x, 104), h, font=f_label, fill=ACCENT)
    draw.line([(28, 126), (W - 28, 126)], fill=SEP, width=1)

    row_bg = _row_bg_img(W, ROW_H)
    if rows:
        for i, (p1, p2, shared, best_diff) in enumerate(rows):
            ry = 134 + i * ROW_H
            if i % 2 == 0:
                card.paste(row_bg, (28, ry - 2), row_bg)
            draw.text((CX[0], ry + 4), f'#{i + 1}', font=f_small, fill=ACCENT)
            addLine(f'{p1} + {p2}', draw, f_small, CX[1], ry + 4, drop_x=3, drop_y=3)
            addLine(str(shared),    draw, f_small, CX[2], ry + 4, drop_x=3, drop_y=3)
            addLine(f'{best_diff}k' if best_diff else '\u2014', draw, f_small, CX[3], ry + 4, drop_x=3, drop_y=3)
    else:
        draw.text((38, 140), 'No entries yet.', font=f_small, fill='#555555')
    return card


# ── Participant log formatter ─────────────────────────────────────────────────

def _format_participants_log(pairs: list[tuple[str, str]]) -> str:
    grouped = defaultdict(list)
    for ign, role in pairs:
        grouped[role].append(ign)
    parts = []
    for role in _ROLE_ORDER:
        if role in grouped:
            parts.append(' '.join(grouped[role]) + ' ' + role)
    return ' / '.join(parts)


# ── Cog ───────────────────────────────────────────────────────────────────────

class SnipeTracker(commands.Cog):
    def __init__(self, client):
        self.client = client

    snipe = SlashCommandGroup('snipe', 'Snipe tracking commands', guild_ids=ALL_GUILD_IDS)

    # ── /snipe log ────────────────────────────────────────────────────────────

    @snipe.command(name='log', description='Log a territory snipe')
    async def log_snipe(
        self,
        ctx: discord.ApplicationContext,
        participants:   discord.Option(str, "Participants as 'IGN Role, IGN Role, ...'", required=True),
        hq:             discord.Option(str, 'HQ location', choices=HQ_CHOICES, required=True),
        difficulty:     discord.Option(int, 'Difficulty in thousands (e.g. 192 for 192k)', required=True),
        guild:          discord.Option(str, 'Guild tag that owned the HQ', required=True),
        conns:          discord.Option(int, 'Connections (0–6)', required=True, min_value=0, max_value=6),
        snipe_date:     discord.Option(str, 'Date as DD/MM/YYYY or Unix timestamp (defaults to now)', required=False, default=None),
        log_to_channel: discord.Option(bool, 'Post to snipe log channel', required=False, default=False),
        image:          discord.Option(discord.Attachment, 'Screenshot (required when logging to channel)', required=False, default=None),
        season:         discord.Option(int, 'Season (defaults to current)', required=False, default=None),
    ):
        await ctx.defer(ephemeral=True)

        if not discord.utils.get(ctx.author.roles, name='War Trainer'):
            await ctx.followup.send(':no_entry: You must have the **War Trainer** role to log snipes.', ephemeral=True)
            return
        if log_to_channel and image is None:
            await ctx.followup.send(':no_entry: You must attach a screenshot when logging to the snipe channel.', ephemeral=True)
            return

        pairs = []
        role_choices_lower = {r.lower(): r for r in ROLE_CHOICES}
        ign_parts = []
        for token in participants.replace(',', ' ').split():
            role = role_choices_lower.get(token.lower())
            if role:
                if not ign_parts:
                    await ctx.followup.send(f':no_entry: Role `{token}` has no preceding IGN.', ephemeral=True)
                    return
                pairs.append((' '.join(ign_parts), role))
                ign_parts = []
            else:
                ign_parts.append(token)
        if ign_parts:
            await ctx.followup.send(
                f":no_entry: `{' '.join(ign_parts)}` has no role. Valid roles: {', '.join(ROLE_CHOICES)}.",
                ephemeral=True
            )
            return
        if not pairs:
            await ctx.followup.send(':no_entry: You must provide at least one participant.', ephemeral=True)
            return

        if snipe_date is not None:
            if '/' in snipe_date:
                try:
                    dt = datetime.strptime(snipe_date.strip(), '%d/%m/%Y')
                    ts = int(dt.replace(tzinfo=timezone.utc).timestamp())
                except ValueError:
                    await ctx.followup.send(':no_entry: Invalid date. Use `DD/MM/YYYY`.', ephemeral=True)
                    return
            else:
                try:
                    ts = int(snipe_date)
                except ValueError:
                    await ctx.followup.send(':no_entry: Invalid date. Use `DD/MM/YYYY` or a Unix timestamp.', ephemeral=True)
                    return
        else:
            ts = int(time.time())

        season = season if season is not None else _get_current_season()
        dry    = is_dry(hq, conns)

        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                'INSERT INTO snipe_logs (hq, difficulty, sniped_at, guild_tag, conns, logged_by, season) '
                'VALUES (%s, %s, to_timestamp(%s), %s, %s, %s, %s) RETURNING id',
                (hq, difficulty, ts, guild.upper(), str(conns), ctx.author.id, season)
            )
            snipe_id = db.cursor.fetchone()[0]
            for ign, role in pairs:
                db.cursor.execute(
                    'INSERT INTO snipe_participants (snipe_id, ign, role) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING',
                    (snipe_id, ign, role)
                )
            db.connection.commit()
        finally:
            db.close()

        conn_display = f'{conns} (Dry)' if dry else str(conns)
        embed = discord.Embed(
            title='Snipe Logged',
            description=f'**{display_hq(hq)}** sniped from **{guild.upper()}**',
            color=0x2ecc71
        )
        embed.add_field(name='Difficulty',   value=f'{difficulty}k', inline=True)
        embed.add_field(name='Connections',  value=conn_display,      inline=True)
        embed.add_field(name='Participants', value='\n'.join(f'**{i}** \u2014 {r}' for i, r in pairs), inline=False)
        await ctx.followup.send(embed=embed, ephemeral=True)

        if log_to_channel:
            snipe_dt       = datetime.fromtimestamp(ts, tz=timezone.utc)
            diff_label     = 'Drysnipe' if dry else f'{conns} Conns'
            participants_str = _format_participants_log(pairs)
            log_text = (
                f"**Date:** {snipe_dt.strftime('%d/%m/%y')}\n"
                f"**Participants:** {participants_str}\n"
                f"**Location:** {display_hq(hq)} ({guild.upper()})\n"
                f"**Difficulty:** {diff_label} / {difficulty}k\n"
                f"**Result:** Success"
            )
            resp     = requests.get(image.url)
            img_file = discord.File(BytesIO(resp.content), filename=image.filename)
            channel  = ctx.bot.get_channel(SNIPE_LOG_CHANNEL_ID)
            if channel:
                await channel.send(content=log_text, file=img_file)

    # ── /snipe stats ──────────────────────────────────────────────────────────

    @snipe.command(name='stats', description='View snipe statistics for a player')
    async def snipe_stats(
        self,
        ctx: discord.ApplicationContext,
        ign: discord.Option(str, 'Player IGN', required=True),
    ):
        await ctx.defer()

        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                'SELECT total, rank FROM ('
                '  SELECT ign, COUNT(*) AS total, RANK() OVER (ORDER BY COUNT(*) DESC) AS rank'
                '  FROM snipe_participants GROUP BY ign'
                ') ranked WHERE ign = %s', (ign,)
            )
            rank_total_row = db.cursor.fetchone()
            total_snipes   = rank_total_row[0] if rank_total_row else 0
            rank_total     = rank_total_row[1] if rank_total_row else None

            db.cursor.execute(
                'SELECT hq, best_diff, rank FROM ('
                '  SELECT sp.ign,'
                '    MAX(sl.difficulty) AS best_diff,'
                '    (SELECT sl2.hq FROM snipe_logs sl2 JOIN snipe_participants sp2 ON sp2.snipe_id = sl2.id'
                '     WHERE sp2.ign = sp.ign ORDER BY sl2.difficulty DESC LIMIT 1) AS hq,'
                '    RANK() OVER (ORDER BY MAX(sl.difficulty) DESC) AS rank'
                '  FROM snipe_logs sl JOIN snipe_participants sp ON sp.snipe_id = sl.id GROUP BY sp.ign'
                ') ranked WHERE ign = %s', (ign,)
            )
            pb_row    = db.cursor.fetchone()
            rank_diff = pb_row[2] if pb_row else None

            db.cursor.execute(
                'SELECT COUNT(*) FROM snipe_logs sl JOIN snipe_participants sp ON sp.snipe_id = sl.id'
                " WHERE sp.ign = %s AND sl.conns = '0'", (ign,)
            )
            zero_conn_count = db.cursor.fetchone()[0]

            db.cursor.execute(
                'SELECT sl.hq, sl.conns FROM snipe_logs sl'
                ' JOIN snipe_participants sp ON sp.snipe_id = sl.id WHERE sp.ign = %s', (ign,)
            )
            dry_snipes = sum(
                1 for hq_abbr, c in db.cursor.fetchall()
                if c.lstrip('-').isdigit() and is_dry(hq_abbr, int(c))
            )

            db.cursor.execute(
                'SELECT MIN(sl.sniped_at), MAX(sl.sniped_at) FROM snipe_logs sl'
                ' JOIN snipe_participants sp ON sp.snipe_id = sl.id WHERE sp.ign = %s', (ign,)
            )
            time_row     = db.cursor.fetchone()
            first_snipe  = time_row[0] if time_row else None
            latest_snipe = time_row[1] if time_row else None

            db.cursor.execute(
                'SELECT COUNT(DISTINCT sl.guild_tag), COUNT(DISTINCT sl.hq) FROM snipe_logs sl'
                ' JOIN snipe_participants sp ON sp.snipe_id = sl.id WHERE sp.ign = %s', (ign,)
            )
            uniq_row      = db.cursor.fetchone()
            unique_guilds = uniq_row[0] if uniq_row else 0
            unique_hqs    = uniq_row[1] if uniq_row else 0

            db.cursor.execute(
                'SELECT MAX(daily_count) FROM ('
                '  SELECT COUNT(*) AS daily_count FROM snipe_logs sl'
                '  JOIN snipe_participants sp ON sp.snipe_id = sl.id'
                '  WHERE sp.ign = %s GROUP BY DATE(sl.sniped_at)'
                ') daily', (ign,)
            )
            most_in_day = db.cursor.fetchone()[0] or 0

            db.cursor.execute(
                'SELECT sl.guild_tag, COUNT(*) AS n FROM snipe_logs sl'
                ' JOIN snipe_participants sp ON sp.snipe_id = sl.id'
                ' WHERE sp.ign = %s GROUP BY sl.guild_tag ORDER BY n DESC LIMIT 3', (ign,)
            )
            top_guilds = db.cursor.fetchall()

            db.cursor.execute(
                'SELECT sl.hq, COUNT(*) AS n FROM snipe_logs sl'
                ' JOIN snipe_participants sp ON sp.snipe_id = sl.id'
                ' WHERE sp.ign = %s GROUP BY sl.hq ORDER BY n DESC', (ign,)
            )
            hq_rows = db.cursor.fetchall()

            db.cursor.execute(
                'SELECT other_sp.ign, COUNT(*) AS shared FROM snipe_participants other_sp'
                ' WHERE other_sp.snipe_id IN ('
                '   SELECT sp.snipe_id FROM snipe_participants sp WHERE sp.ign = %s'
                ' ) AND other_sp.ign != %s'
                ' GROUP BY other_sp.ign ORDER BY shared DESC LIMIT 3', (ign, ign)
            )
            teammate_rows = db.cursor.fetchall()

            # Most played role + count (all-time)
            db.cursor.execute(
                'SELECT role, COUNT(*) FROM snipe_participants WHERE ign = %s'
                ' GROUP BY role ORDER BY COUNT(*) DESC LIMIT 1', (ign,)
            )
            role_row = db.cursor.fetchone()
            top_role = f'{role_row[0]} ({role_row[1]})' if role_row else None

            db.cursor.execute(
                'SELECT COUNT(DISTINCT sl.season) FROM snipe_logs sl'
                ' JOIN snipe_participants sp ON sp.snipe_id = sl.id WHERE sp.ign = %s', (ign,)
            )
            seasons_active = db.cursor.fetchone()[0] or 0

            db.cursor.execute(
                "SELECT DISTINCT DATE(sl.sniped_at AT TIME ZONE 'UTC') FROM snipe_logs sl"
                ' JOIN snipe_participants sp ON sp.snipe_id = sl.id WHERE sp.ign = %s', (ign,)
            )
            snipe_dates          = [row[0] for row in db.cursor.fetchall()]
            streak_best, streak_cur = _compute_streaks(snipe_dates)

        finally:
            db.close()

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
            top_role, streak_best, streak_cur, seasons_active,
            rank_text=rank_text, rank_color=rank_color,
        )
        buf = BytesIO()
        card.save(buf, format='PNG')
        buf.seek(0)
        await ctx.followup.send(file=discord.File(buf, filename=f'snipe_{ign}.png'))

    # ── /snipe leaderboard ────────────────────────────────────────────────────

    @snipe.command(name='leaderboard', description='View snipe leaderboards')
    async def snipe_leaderboard(
        self,
        ctx: discord.ApplicationContext,
        sort:   discord.Option(str, 'Sort by', choices=LB_SORT_CHOICES, required=False, default='Total Snipes'),
        season: discord.Option(int, 'Season (0 = all-time, default = current)', required=False, default=None),
    ):
        await ctx.defer()
        sc, sp  = _season_clause(season)
        sl      = _season_label(season)

        db = DB()
        db.connect()
        try:
            player_stats = _fetch_lb_data(db, sc, sp)
        finally:
            db.close()

        player_stats.sort(key=_LB_SORT_KEY[sort])

        PER_PAGE    = 10
        total_pages = max(1, math.ceil(len(player_stats) / PER_PAGE))
        cards = [
            _generate_lb_card(
                player_stats[i * PER_PAGE:(i + 1) * PER_PAGE],
                sort, sl, i + 1, total_pages, i * PER_PAGE + 1
            )
            for i in range(total_pages)
        ]
        await _make_paginator(_pages_from_cards(cards, 'lb')).respond(ctx.interaction)

    # ── /snipe roles ──────────────────────────────────────────────────────────

    @snipe.command(name='roles', description='View role leaderboards')
    async def snipe_roles(
        self,
        ctx: discord.ApplicationContext,
        role:   discord.Option(str, 'Role to view', choices=ROLE_CHOICES, required=True),
        sort:   discord.Option(str, 'Sort by', choices=['Amount', 'Highest Difficulty'], required=False, default='Amount'),
        season: discord.Option(int, 'Season (0 = all-time, default = current)', required=False, default=None),
    ):
        await ctx.defer()
        sc, sp = _season_clause(season)
        sl     = _season_label(season)
        order  = 'COUNT(*) DESC' if sort == 'Amount' else 'MAX(sl.difficulty) DESC'

        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                f'SELECT sp.ign, COUNT(*) AS times, MAX(sl.difficulty) AS best_diff '
                f'FROM snipe_participants sp '
                f'JOIN snipe_logs sl ON sl.id = sp.snipe_id '
                f'WHERE sp.role = %s {sc} '
                f'GROUP BY sp.ign ORDER BY {order}',
                [role] + sp
            )
            all_rows = db.cursor.fetchall()
        finally:
            db.close()

        PER_PAGE    = 10
        total_pages = max(1, math.ceil(len(all_rows) / PER_PAGE))
        cards = [
            _generate_roles_card(
                all_rows[i * PER_PAGE:(i + 1) * PER_PAGE],
                role, sort, sl, i + 1, total_pages, i * PER_PAGE + 1
            )
            for i in range(total_pages)
        ]
        await _make_paginator(_pages_from_cards(cards, 'roles')).respond(ctx.interaction)

    # ── /snipe team ───────────────────────────────────────────────────────────

    @snipe.command(name='team', description='View the snipe team roster')
    async def snipe_team(
        self,
        ctx: discord.ApplicationContext,
        season: discord.Option(int, 'Season (0 = all-time, default = current)', required=False, default=None),
    ):
        await ctx.defer()
        sc, sp = _season_clause(season)
        sl     = _season_label(season)
        sc2    = sc.replace('AND sl.', 'AND sl2.')

        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                f'SELECT sp.ign, COUNT(*) AS total, MAX(sl.difficulty) AS best_diff,'
                f'  (SELECT role FROM snipe_participants sp2'
                f'   JOIN snipe_logs sl2 ON sl2.id = sp2.snipe_id'
                f'   WHERE sp2.ign = sp.ign {sc2}'
                f'   GROUP BY role ORDER BY COUNT(*) DESC LIMIT 1) AS top_role'
                f' FROM snipe_participants sp'
                f' JOIN snipe_logs sl ON sl.id = sp.snipe_id'
                f' WHERE 1=1 {sc}'
                f' GROUP BY sp.ign ORDER BY total DESC',
                sp + sp
            )
            rows = db.cursor.fetchall()
        finally:
            db.close()

        card = _generate_team_card(rows, sl)
        buf = BytesIO()
        card.save(buf, format='PNG')
        buf.seek(0)
        await ctx.followup.send(file=discord.File(buf, filename='team.png'))

    # ── /snipe duos ───────────────────────────────────────────────────────────

    @snipe.command(name='duos', description='View top snipe duos')
    async def snipe_duos(
        self,
        ctx: discord.ApplicationContext,
        season: discord.Option(int, 'Season (0 = all-time, default = current)', required=False, default=None),
    ):
        await ctx.defer()
        sc, sp = _season_clause(season)
        sl     = _season_label(season)

        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                f'SELECT a.ign, b.ign, COUNT(*) AS shared, MAX(sl.difficulty) AS best_diff'
                f' FROM snipe_participants a'
                f' JOIN snipe_participants b ON a.snipe_id = b.snipe_id AND a.ign < b.ign'
                f' JOIN snipe_logs sl ON sl.id = a.snipe_id'
                f' WHERE 1=1 {sc}'
                f' GROUP BY a.ign, b.ign ORDER BY shared DESC LIMIT 10',
                sp
            )
            rows = db.cursor.fetchall()
        finally:
            db.close()

        card = _generate_duo_card(rows, sl)
        buf = BytesIO()
        card.save(buf, format='PNG')
        buf.seek(0)
        await ctx.followup.send(file=discord.File(buf, filename='duos.png'))

    # ── /warseason ────────────────────────────────────────────────────────────

    @slash_command(description='Set the current war season', guild_ids=ALL_GUILD_IDS)
    async def warseason(
        self,
        ctx: discord.ApplicationContext,
        season: discord.Option(int, 'Season number', required=True, min_value=1),
    ):
        await ctx.defer(ephemeral=True)
        if not discord.utils.get(ctx.author.roles, name='War Trainer'):
            await ctx.followup.send(':no_entry: You must have the **War Trainer** role to set the war season.', ephemeral=True)
            return
        _set_current_season(season)
        await ctx.followup.send(f'War season set to **Season {season}**.', ephemeral=True)


def setup(client):
    client.add_cog(SnipeTracker(client))
