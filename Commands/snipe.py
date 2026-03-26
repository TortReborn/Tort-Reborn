import asyncio
import json
import math
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from io import BytesIO

import discord
import requests
from discord.ext import commands, pages
from discord.commands import SlashCommandGroup, slash_command
from PIL import Image, ImageDraw, ImageFont

from Helpers.classes import Guild, Page, PlayerStats
from Helpers.database import DB, get_current_guild_data
from Helpers.functions import addLine, generate_badge, get_guild_color, vertical_gradient, round_corners
from Helpers.snipe_utils import ALL_TERRITORY_NAMES, display_hq, is_dry, normalize_hq_for_storage
from Helpers.variables import ALL_GUILD_IDS, HQ_TEAM_ROLE_ID, TAQ_GUILD_ID, SNIPE_LOG_CHANNEL_ID, discord_ranks

ROLE_CHOICES    = ['Tank', 'Healer', 'DPS']
_ROLE_ORDER     = ['Healer', 'Tank', 'DPS']
_ROLE_COLORS    = {
    'Healer': '#51D868',
    'Tank':   '#00D2E6',
    'DPS':    '#FF442F',
}
_PARTICIPANT_NAME_COLOR = '#b5b4b4'
_DIFFICULTY_COLORS = [
    (202, '#ff00ab'),
    (192, '#ff2121'),
    (167, '#f56217'),
    (120, '#ff9627'),
    (100, '#ffcd35'),
    (56,  '#4cb80f'),
    (0,   '#a8f785'),
]
LB_SORT_CHOICES = ['Total Snipes', 'Personal Best', 'Best Streak', 'Current Streak']
_LB_PER_PAGE        = 10
_LIST_PER_PAGE      = 10
_LIST_SORT_CHOICES  = ['Newest', 'Oldest', 'Hardest', 'Easiest', 'Least Conns']
_LIST_ORDER_SQL     = {
    'Newest':      "sl.sniped_at DESC",
    'Oldest':      "sl.sniped_at ASC",
    'Hardest':     "sl.difficulty DESC, sl.sniped_at DESC",
    'Easiest':     "sl.difficulty ASC, sl.sniped_at DESC",
    'Least Conns': "sl.conns ASC, sl.sniped_at DESC",
}
_ATHENA_GUILD_LIST_URL = 'https://athena.wynntils.com/cache/get/guildList'
_ATHENA_GUILD_TTL      = 6 * 60 * 60
_DEFAULT_GUILD_COLOR = '#ffffff'
_GUILD_COLOR_CACHE   = {}
_ATHENA_GUILD_COLORS = None
_ATHENA_GUILD_FETCHED_AT = 0.0

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

# ── DB helpers ───────────────────────────────────────────────────────────────

def _resolve_ign(db, ign: str) -> str:
    """Return the stored canonical casing for an IGN, or ign itself if unseen."""
    db.cursor.execute(
        'SELECT ign FROM snipe_participants WHERE LOWER(ign) = LOWER(%s) LIMIT 1', (ign,)
    )
    row = db.cursor.fetchone()
    return row[0] if row else ign


def _resolve_igns(db, igns: list[str]) -> dict[str, str]:
    """Resolve multiple IGNs to canonical casing in a single query.
    Returns a mapping of lowercased input → canonical stored IGN."""
    if not igns:
        return {}
    db.cursor.execute(
        'SELECT DISTINCT ON (LOWER(ign)) ign FROM snipe_participants WHERE LOWER(ign) = ANY(%s) ORDER BY LOWER(ign), ign',
        ([i.lower() for i in igns],)
    )
    return {row[0].lower(): row[0] for row in db.cursor.fetchall()}


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


# ── Shared table header renderer ────────────────────────────────────────────

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
    page_str = f'Page {page_num}' if total_pages == 1 else f'Page {page_num} of {total_pages}'
    ph = draw.textbbox((0, 0), page_str, font=f_label)[3]
    pw = draw.textbbox((0, 0), page_str, font=f_label)[2]
    draw.text((W - 22 - pw, H - 22 - ph), page_str, font=f_label, fill=ACCENT)


def _fit_font(text: str, draw, path: str, max_size: int, max_w: int) -> ImageFont.FreeTypeFont:
    """Return the largest font ≤ max_size that renders text within max_w pixels."""
    for size in range(max_size, 13, -2):
        font = ImageFont.truetype(path, size)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_w:
            return font
    return ImageFont.truetype(path, 14)


def _fit_wrap_lines(
    text: str,
    draw,
    path: str,
    max_size: int,
    max_w: int,
    max_lines: int,
    min_size: int = 12,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    words = text.split()
    if not words:
        font = ImageFont.truetype(path, max_size)
        return font, ['\u2014']

    def wrap_for_font(font):
        lines = []
        cur = words[0]
        for word in words[1:]:
            test = f'{cur} {word}'
            if draw.textbbox((0, 0), test, font=font)[2] <= max_w:
                cur = test
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
        return lines

    for size in range(max_size, min_size - 1, -1):
        font = ImageFont.truetype(path, size)
        lines = wrap_for_font(font)
        if len(lines) <= max_lines:
            return font, lines

    font = ImageFont.truetype(path, min_size)
    lines = wrap_for_font(font)
    if len(lines) <= max_lines:
        return font, lines

    clipped = lines[:max_lines]
    last = clipped[-1]
    while last and draw.textbbox((0, 0), f'{last}\u2026', font=font)[2] > max_w:
        last = last[:-1].rstrip()
    clipped[-1] = f'{last}\u2026' if last else '\u2026'
    return font, clipped


def _normalize_guild_tag(tag: str | None) -> str:
    return (tag or '').strip().upper()


def _strip_addline_format(text: str) -> str:
    if not text:
        return ''
    return re.sub(r'&(?:#[0-9a-fA-F]{6}|.)', '', text)


def _difficulty_color(diff: int | float | None) -> str:
    if diff is None:
        return '#ffffff'
    for threshold, color in _DIFFICULTY_COLORS:
        if diff >= threshold:
            return color
    return _DIFFICULTY_COLORS[-1][1]


def _format_difficulty_text(diff: int | float | None, prefix: str = '', suffix: str = '') -> str:
    if diff is None:
        return f'{prefix}\u2014{suffix}'
    diff_text = f'{diff:.1f}k' if isinstance(diff, float) and not diff.is_integer() else f'{int(diff)}k'
    color = _difficulty_color(diff)
    return f'{prefix}&#{color[1:]}{diff_text}&f{suffix}'


def _fit_addline_font(text: str, draw, path: str, max_size: int, max_w: int, min_size: int = 10) -> ImageFont.FreeTypeFont:
    plain = _strip_addline_format(text)
    sample = plain or '\u2014'
    for size in range(max_size, min_size - 1, -1):
        font = ImageFont.truetype(path, size)
        if draw.textbbox((0, 0), sample, font=font)[2] <= max_w:
            return font
    return ImageFont.truetype(path, min_size)


def _fit_addline_text(text: str, draw, path: str, max_size: int, max_w: int, min_size: int = 10) -> tuple[str, ImageFont.FreeTypeFont]:
    plain = _strip_addline_format(text) or '\u2014'
    font = _fit_addline_font(plain, draw, path, max_size, max_w, min_size=min_size)
    if draw.textbbox((0, 0), plain, font=font)[2] <= max_w:
        return plain, font

    clipped = plain
    while clipped and draw.textbbox((0, 0), f'{clipped}\u2026', font=font)[2] > max_w:
        clipped = clipped[:-1].rstrip(' ,')
    return (f'{clipped}\u2026' if clipped else '\u2026'), font


def _colorize_role_words(text: str) -> str:
    if not text or text == '\u2014':
        return text
    out = text
    for role in _ROLE_ORDER:
        out = re.sub(rf'\b{re.escape(role)}\b', f'&#{_ROLE_COLORS[role][1:]}{role}&f', out)
    return out


def _group_participants_by_role(pairs: list[tuple[str, str]]) -> dict[str, list[str]]:
    grouped = defaultdict(list)
    for ign, role in pairs:
        grouped[role].append(ign)
    return grouped


def _draw_role_columns(draw, pairs: list[tuple[str, str]], start_x: int, y: int, max_x: int) -> None:
    grouped = _group_participants_by_role(pairs)
    roles = ['Healer', 'Tank', 'DPS']
    total_w = max_x - start_x
    gap = 10
    usable_w = total_w - gap * (len(roles) - 1)
    weights = {'Healer': 0.34, 'Tank': 0.33, 'DPS': 0.33}
    col_ws = {}
    used = 0
    for idx, role in enumerate(roles):
        if idx == len(roles) - 1:
            col_ws[role] = max(56, usable_w - used)
        else:
            width = max(56, int(usable_w * weights[role]))
            col_ws[role] = width
            used += width
    role_font = ImageFont.truetype('images/profile/5x5.ttf', 14)
    label_ws = {role: draw.textbbox((0, 0), role, font=role_font)[2] for role in roles}
    label_hs = {role: draw.textbbox((0, 0), role, font=role_font)[3] for role in roles}
    cursor_x = start_x
    for idx, role in enumerate(roles):
        names = grouped.get(role)
        col_x = cursor_x
        col_w = col_ws[role]
        cursor_x += col_w + gap
        if not names:
            continue
        role_text = f'&#{_ROLE_COLORS[role][1:]}{role}'
        names_text = ', '.join(names)
        label_w = label_ws[role]
        names_x = col_x + label_w + 4
        names_max_w = max(18, col_x + col_w - names_x - 4)
        fitted_text, names_font = _fit_addline_text(names_text, draw, 'images/profile/game.ttf', 18, names_max_w, min_size=8)
        names_h = draw.textbbox((0, 0), fitted_text, font=names_font)[3]
        names_y = y + max(0, label_hs[role] - names_h)
        addLine(role_text, draw, role_font, col_x, y, drop_x=2, drop_y=2)
        addLine(f'&#{_PARTICIPANT_NAME_COLOR[1:]}{fitted_text}', draw, names_font, names_x, names_y, drop_x=2, drop_y=2)


def _load_athena_guild_colors_sync() -> dict[str, str]:
    global _ATHENA_GUILD_COLORS, _ATHENA_GUILD_FETCHED_AT

    now = time.time()
    if _ATHENA_GUILD_COLORS is not None and (now - _ATHENA_GUILD_FETCHED_AT) < _ATHENA_GUILD_TTL:
        return _ATHENA_GUILD_COLORS

    try:
        response = requests.get(_ATHENA_GUILD_LIST_URL, timeout=15)
        response.raise_for_status()
        payload = response.json()
        colors = {}
        for row in payload:
            prefix = _normalize_guild_tag(row.get('prefix'))
            color = (row.get('color') or '').strip()
            if prefix and color:
                colors[prefix] = color
        _ATHENA_GUILD_COLORS = colors
    except Exception:
        if _ATHENA_GUILD_COLORS is None:
            _ATHENA_GUILD_COLORS = {}
    finally:
        _ATHENA_GUILD_FETCHED_AT = now

    return _ATHENA_GUILD_COLORS


def _fetch_guild_color_sync(tag: str | None) -> str:
    norm = _normalize_guild_tag(tag)
    if not norm:
        return _DEFAULT_GUILD_COLOR
    cached = _GUILD_COLOR_CACHE.get(norm)
    if cached:
        return cached
    athena_color = _load_athena_guild_colors_sync().get(norm)
    if athena_color:
        _GUILD_COLOR_CACHE[norm] = athena_color
        return athena_color
    try:
        guild = Guild(norm)
        color = get_guild_color({'banner': guild.banner})
    except Exception:
        color = _DEFAULT_GUILD_COLOR
    _GUILD_COLOR_CACHE[norm] = color
    return color


async def _get_guild_color_map(tags) -> dict[str, str]:
    unique = {_normalize_guild_tag(tag) for tag in tags if _normalize_guild_tag(tag)}
    if unique:
        await asyncio.to_thread(_load_athena_guild_colors_sync)
    uncached = [tag for tag in unique if tag not in _GUILD_COLOR_CACHE]
    if uncached:
        await asyncio.gather(*(
            asyncio.to_thread(_fetch_guild_color_sync, tag)
            for tag in uncached
        ))
    return {tag: _GUILD_COLOR_CACHE.get(tag, _DEFAULT_GUILD_COLOR) for tag in unique}


def _row_bg_img(W, ROW_H=40):
    row_bg = Image.new('RGBA', (W - 56, ROW_H - 2), (0, 0, 0, 0))
    ImageDraw.Draw(row_bg).rounded_rectangle(
        ((0, 0), (W - 57, ROW_H - 3)), fill=(255, 255, 255, 15), radius=6
    )
    return row_bg


# def _get_earned_badges(total_snipes: int) -> list:
#     """Return ordered list of earned badge image paths (max 9 for 3×3 grid)."""
#     badges = []
#     # Total Snipes medal
#     if total_snipes >= 100:
#         badges.append('images/snipe/total_snipes/total_snipes_gold.png')
#     elif total_snipes >= 50:
#         badges.append('images/snipe/total_snipes/total_snipes_silver.png')
#     elif total_snipes >= 25:
#         badges.append('images/snipe/total_snipes/total_snipes_bronze.png')
#     # Future medals appended here (up to 9 total)
#     return badges


# ── Card generators ──────────────────────────────────────────────────────────

def _generate_snipe_card(
    ign, total_snipes, rank_total, rank_diff, pb_row,
    dry_snipes, zero_conn_count, most_in_day,
    unique_hqs, unique_guilds, first_snipe, latest_snipe,
    top_guilds, hq_rows, teammate_rows,
    top_role, streak_best, streak_cur, seasons_active,
    rank_text=None, rank_color=None, guild_colors=None,
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
        pb_text = _format_difficulty_text(diff, prefix=f'{display_hq(hq_abbr)} \u2014 ')
        pb_font = _fit_addline_font(pb_text, draw, 'images/profile/game.ttf', 35, LW - 8, min_size=20)
        addLine(pb_text, draw, pb_font, LX, 196, drop_x=4, drop_y=4)
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
        for i, (tm_ign, count) in enumerate(teammate_rows[:6]):
            s = 'snipe' if count == 1 else 'snipes'
            teammate_text = f'{tm_ign} \u2014 {count} {s}'
            teammate_font = _fit_addline_font(teammate_text, draw, 'images/profile/game.ttf', 26, LW - 8, min_size=18)
            addLine(teammate_text, draw, teammate_font, LX, 412 + i * 34, drop_x=3, drop_y=3)
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
        formatted_value = _colorize_role_words(value) if label == 'Top Role' else value
        v_font = _fit_addline_font(formatted_value, draw, 'images/profile/game.ttf', 32, BOX_W - 16)
        val_w  = draw.textbbox((0, 0), _strip_addline_format(formatted_value), font=v_font)[2]
        addLine(formatted_value, draw, v_font, bx + BOX_W - 8 - val_w, by + 38, drop_x=3, drop_y=3)

    # ── Right panel — lists ───────────────────────────────────────────────────
    LIST_Y, LIST_X, LIST_W = 294, 450, 390
    LIST_H = H - 40 - LIST_Y

    list_bg = Image.new('RGBA', (LIST_W, LIST_H), (0, 0, 0, 0))
    ImageDraw.Draw(list_bg).rounded_rectangle(
        ((0, 0), (LIST_W - 1, LIST_H - 1)), fill=(0, 0, 0, 40), radius=8
    )
    card.paste(list_bg, (LIST_X, LIST_Y + 18), list_bg)

    draw.text((LIST_X + 8, LIST_Y + 26), 'MOST SNIPED GUILDS', font=f_label, fill=ACCENT)
    if top_guilds:
        for i, (tag, count) in enumerate(top_guilds[:3]):
            color = (guild_colors or {}).get(_normalize_guild_tag(tag), _DEFAULT_GUILD_COLOR)
            x = addLine(f'&#{color[1:]}{tag}', draw, f_small, LIST_X + 8, LIST_Y + 52 + i * 34, drop_x=3, drop_y=3)
            addLine(f' \u2014 {count}', draw, f_small, x, LIST_Y + 52 + i * 34, drop_x=3, drop_y=3)
    else:
        draw.text((LIST_X + 8, LIST_Y + 52), 'None yet', font=f_small, fill='#555555')

    SECT2_Y = LIST_Y + 18 + LIST_H // 2
    draw.line([(LIST_X + 8, SECT2_Y), (LIST_X + LIST_W - 8, SECT2_Y)], fill=SEP, width=1)
    draw.text((LIST_X + 8, SECT2_Y + 8), 'MOST SNIPED HQs', font=f_label, fill=ACCENT)
    if hq_rows:
        for i, (abbr, count) in enumerate(hq_rows[:3]):
            hq_text = f'{display_hq(abbr)} \u2014 {count}'
            hq_font = _fit_addline_font(hq_text, draw, 'images/profile/game.ttf', 26, LIST_W - 16, min_size=18)
            addLine(hq_text, draw, hq_font, LIST_X + 8, SECT2_Y + 34 + i * 34, drop_x=3, drop_y=3)
    else:
        draw.text((LIST_X + 8, SECT2_Y + 34), 'None yet', font=f_small, fill='#555555')

    # ── Right panel — badges (3×3 grid) — disabled pending release ──────────
    # BADGE_SIZE = 80
    # BADGE_GAP  = 30
    # BADGE_AREA_X = COLS_X[2]                          # 850 — aligns with cols 2 & 3
    # BADGE_AREA_W = COLS_X[3] + BOX_W - BADGE_AREA_X  # 390
    # BADGE_AREA_Y = LIST_Y + 18              # 312
    # BADGE_AREA_H = H - 22 - BADGE_AREA_Y   # 326
    #
    # badge_bg = Image.new('RGBA', (BADGE_AREA_W, BADGE_AREA_H), (0, 0, 0, 0))
    # ImageDraw.Draw(badge_bg).rounded_rectangle(
    #     ((0, 0), (BADGE_AREA_W - 1, BADGE_AREA_H - 1)), fill=(0, 0, 0, 40), radius=8
    # )
    # card.paste(badge_bg, (BADGE_AREA_X, BADGE_AREA_Y), badge_bg)
    #
    # grid_w = 3 * BADGE_SIZE + 2 * BADGE_GAP   # 300
    # grid_h = 3 * BADGE_SIZE + 2 * BADGE_GAP   # 300
    # b_left = BADGE_AREA_X + (BADGE_AREA_W - grid_w) // 2
    # b_top  = BADGE_AREA_Y + (BADGE_AREA_H - grid_h) // 2
    #
    # # DEBUG: force all slots to empty medals
    # # earned = []
    # earned = _get_earned_badges(total_snipes)
    #
    # empty_medal = Image.open('images/snipe/misc/empty_medal.png').convert('RGBA').resize(
    #     (BADGE_SIZE, BADGE_SIZE), Image.LANCZOS
    # )
    # for slot in range(9):
    #     col = slot % 3
    #     row = slot // 3
    #     bx = b_left + col * (BADGE_SIZE + BADGE_GAP)
    #     by = b_top  + row * (BADGE_SIZE + BADGE_GAP)
    #     if slot < len(earned):
    #         badge_img = Image.open(earned[slot]).convert('RGBA').resize(
    #             (BADGE_SIZE, BADGE_SIZE), Image.LANCZOS
    #         )
    #         card.paste(badge_img, (bx, by), badge_img)
    #     else:
    #         card.paste(empty_medal, (bx, by), empty_medal)

    return card


def _generate_lb_card(page_rows, sort_by, season_label, page_num, total_pages, start_rank):
    W = 1000
    H = 130 + _LB_PER_PAGE * 44 + 50  # fixed height so all pages are identical
    card, draw = _card_base(W, H)

    ACCENT, DIM, SEP, WHITE = '#fad51e', '#b09010', '#3474eb', '#ffffff'
    f_title = ImageFont.truetype('images/profile/5x5.ttf',  28)
    f_label = ImageFont.truetype('images/profile/5x5.ttf',  20)
    f_small = ImageFont.truetype('images/profile/game.ttf', 24)

    CX        = [38, 108, 360, 472, 600, 780]
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
            best_str = _format_difficulty_text(row['best_diff']) if row['best_diff'] else '\u2014'
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
            addLine(_format_difficulty_text(best_diff) if best_diff is not None else '\u2014', draw, f_small, CX[3], ry + 4, drop_x=3, drop_y=3)
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

    draw.text((38, 28), 'SNIPE ROSTER', font=f_title, fill=ACCENT)
    draw.text((38, 66), season_label, font=f_label, fill='#ffffff')
    draw.line([(28, 100), (W - 28, 100)], fill=SEP, width=2)

    CX = [38, 118, 470, 630, 820]
    for h, x in zip(['RANK', 'PLAYER', 'SNIPES', 'BEST DIFF.', 'ROLES'], CX):
        draw.text((x, 104), h, font=f_label, fill=ACCENT)
    draw.line([(28, 126), (W - 28, 126)], fill=SEP, width=1)

    row_bg = _row_bg_img(W, ROW_H)
    if rows:
        for i, (ign, total, best_diff, roles_text) in enumerate(rows):
            ry = 134 + i * ROW_H
            if i % 2 == 0:
                card.paste(row_bg, (28, ry - 2), row_bg)
            draw.text((CX[0], ry + 4), f'#{i + 1}', font=f_small, fill=ACCENT)
            addLine(str(ign),    draw, f_small, CX[1], ry + 4, drop_x=3, drop_y=3)
            addLine(str(total),  draw, f_small, CX[2], ry + 4, drop_x=3, drop_y=3)
            addLine(_format_difficulty_text(best_diff) if best_diff is not None else '\u2014', draw, f_small, CX[3], ry + 4, drop_x=3, drop_y=3)
            roles_value = str(roles_text) if roles_text else '\u2014'
            roles_formatted = _colorize_role_words(roles_value)
            roles_font = _fit_addline_font(roles_formatted, draw, 'images/profile/game.ttf', 24, W - CX[4] - 34)
            addLine(roles_formatted, draw, roles_font, CX[4], ry + 6, drop_x=3, drop_y=3)
    else:
        draw.text((38, 140), 'No entries yet.', font=f_small, fill='#555555')
    return card


def _generate_duo_card(page_rows, season_label, page_num, total_pages, start_rank) -> Image.Image:
    ROW_H, W = 44, 1000
    H = 130 + _LB_PER_PAGE * ROW_H + 50
    card, draw = _card_base(W, H)

    ACCENT, SEP = '#fad51e', '#3474eb'
    f_title = ImageFont.truetype('images/profile/5x5.ttf',  30)
    f_label = ImageFont.truetype('images/profile/5x5.ttf',  20)
    f_small = ImageFont.truetype('images/profile/game.ttf', 26)

    draw.text((38, 28), 'SNIPE DUO LEADERBOARD', font=f_title, fill=ACCENT)
    draw.text((38, 66), season_label, font=f_label, fill='#ffffff')
    draw.line([(28, 100), (W - 28, 100)], fill=SEP, width=2)

    CX = [38, 100, 700, 860]
    for h, x in zip(['RANK', 'DUO', 'SHARED', 'BEST DIFF.'], CX):
        draw.text((x, 104), h, font=f_label, fill=ACCENT)
    draw.line([(28, 126), (W - 28, 126)], fill=SEP, width=1)

    row_bg = _row_bg_img(W, ROW_H)
    if page_rows:
        for i, (p1, p2, shared, best_diff) in enumerate(page_rows):
            ry = 134 + i * ROW_H
            if i % 2 == 0:
                card.paste(row_bg, (28, ry - 2), row_bg)
            draw.text((CX[0], ry + 4), f'#{start_rank + i}', font=f_small, fill=ACCENT)
            addLine(f'{p1} + {p2}', draw, f_small, CX[1], ry + 4, drop_x=3, drop_y=3)
            addLine(str(shared),    draw, f_small, CX[2], ry + 4, drop_x=3, drop_y=3)
            addLine(_format_difficulty_text(best_diff) if best_diff is not None else '\u2014', draw, f_small, CX[3], ry + 4, drop_x=3, drop_y=3)
    else:
        draw.text((38, 140), 'No entries yet.', font=f_small, fill='#555555')

    _draw_table_footer(draw, W, H, f_label, page_num, total_pages, ACCENT)
    return card


# ── Participant log formatter ─────────────────────────────────────────────────

def _format_team_compact(pairs: list[tuple[str, str]]) -> str:
    grouped = defaultdict(list)
    for ign, role in pairs:
        grouped[role].append(ign)

    parts = []
    for role in _ROLE_ORDER:
        if role in grouped:
            parts.append(f"{role} {' '.join(grouped[role])}")
    return ' '.join(parts) if parts else '\u2014'


def _format_team_names_compact(pairs: list[tuple[str, str]]) -> str:
    names = sorted({ign for ign, _ in pairs})
    return ', '.join(names) if names else '\u2014'


def _generate_overview_card(
    ign, rank_text, rank_color,
    most_common_team_any, team_any_count,
    most_common_team_roles, team_role_count,
    recent_snipes, participants_map,
    avg_diff, top_hq, top_hq_count, total_snipes_all, guild_colors,
) -> Image.Image:
    W, H = 1050, 640
    card, draw = _card_base(W, H)

    ACCENT, SEP, WHITE, DIM = '#fad51e', '#3474eb', '#ffffff', '#8ea3cc'
    f_title = ImageFont.truetype('images/profile/5x5.ttf', 30)
    f_label = ImageFont.truetype('images/profile/5x5.ttf', 20)
    f_name = ImageFont.truetype('images/profile/game.ttf', 46)
    f_body = ImageFont.truetype('images/profile/game.ttf', 22)

    LX, LW, SEP_X = 38, 315, 382

    draw.text((LX, 28), 'SNIPE OVERVIEW', font=f_title, fill=ACCENT)
    addLine(ign, draw, f_name, LX, 62, drop_x=5, drop_y=5)

    if rank_text and rank_color:
        badge = generate_badge(text=rank_text, base_color=rank_color, scale=2)
        card.paste(badge, (LX, 120), badge)

    team_label_y = 178
    draw.line([(LX, team_label_y - 12), (LX + LW, team_label_y - 12)], fill=SEP, width=2)
    draw.text((LX, team_label_y), 'MOST COMMON TEAM', font=f_label, fill=ACCENT)

    team_box = Image.new('RGBA', (LW, 138), (0, 0, 0, 0))
    ImageDraw.Draw(team_box).rounded_rectangle(
        ((0, 0), (LW - 1, 137)), fill=(0, 0, 0, 50), radius=10
    )
    card.paste(team_box, (LX, 208), team_box)

    def _draw_team_section(
        header_x: int,
        count_x: int,
        header_y: int,
        body_y: int,
        header: str,
        count: int,
        lines: list[str],
        font,
        colorize_roles: bool = False,
        line_gap: int = 18,
    ):
        draw.text((header_x, header_y), header, font=f_label, fill=ACCENT)
        draw.text((count_x, header_y), f'x{count}', font=f_label, fill=WHITE)

        for i, line in enumerate(lines):
            text = _colorize_role_words(line) if colorize_roles else line
            addLine(text, draw, font, LX + 12, body_y + i * line_gap, drop_x=2, drop_y=2)

    team_any_font, team_any_lines = _fit_wrap_lines(
        most_common_team_any, draw, 'images/profile/game.ttf', 16, LW - 32, 2, min_size=11
    )
    _draw_team_section(LX + 12, LX + 112, 216, 240, 'General', team_any_count, team_any_lines, team_any_font, line_gap=18)

    team_role_font, team_role_lines = _fit_wrap_lines(
        most_common_team_roles, draw, 'images/profile/game.ttf', 14, LW - 32, 2, min_size=10
    )
    _draw_team_section(
        LX + 12, LX + 192, 280, 306,
        'Matching Roles', team_role_count, team_role_lines, team_role_font,
        colorize_roles=True, line_gap=17
    )

    draw.line([(LX, 362), (LX + LW, 362)], fill=SEP, width=2)
    draw.text((LX, 374), 'QUICK STATS', font=f_label, fill=ACCENT)

    BOX_W, BOX_H = 150, 92
    stat_boxes = [
        ('Total Snipes', str(total_snipes_all)),
        ('Avg Diff.', _format_difficulty_text(avg_diff) if avg_diff is not None else '\u2014'),
    ]
    for idx, (label, value) in enumerate(stat_boxes):
        bx = LX + (idx % 2) * (BOX_W + 15)
        by = 408
        box = Image.new('RGBA', (BOX_W, BOX_H), (0, 0, 0, 0))
        ImageDraw.Draw(box).rounded_rectangle(
            ((0, 0), (BOX_W - 1, BOX_H - 1)), fill=(0, 0, 0, 55), radius=8
        )
        card.paste(box, (bx, by), box)
        draw.text((bx + 8, by + 8), label, font=f_label, fill=ACCENT)
        value_font = _fit_addline_font(value, draw, 'images/profile/game.ttf', 26, BOX_W - 16)
        value_w = draw.textbbox((0, 0), _strip_addline_format(value), font=value_font)[2]
        addLine(value, draw, value_font, bx + BOX_W - 8 - value_w, by + 44, drop_x=3, drop_y=3)

    top_box_x = LX
    top_box_y = 516
    top_box_w = LW
    top_box = Image.new('RGBA', (top_box_w, BOX_H), (0, 0, 0, 0))
    ImageDraw.Draw(top_box).rounded_rectangle(
        ((0, 0), (top_box_w - 1, BOX_H - 1)), fill=(0, 0, 0, 55), radius=8
    )
    card.paste(top_box, (top_box_x, top_box_y), top_box)
    draw.text((top_box_x + 8, top_box_y + 8), 'Top HQ', font=f_label, fill=ACCENT)
    top_value = f'{top_hq} ({top_hq_count})' if top_hq else '\u2014'
    top_font = _fit_font(top_value, draw, 'images/profile/game.ttf', 26, top_box_w - 16)
    top_w = draw.textbbox((0, 0), top_value, font=top_font)[2]
    addLine(top_value, draw, top_font, top_box_x + top_box_w - 8 - top_w, top_box_y + 44, drop_x=3, drop_y=3)

    draw.line([(SEP_X, 22), (SEP_X, H - 22)], fill=SEP, width=2)

    RX = SEP_X + 28
    draw.text((RX, 28), 'RECENT SNIPES', font=f_title, fill=ACCENT)
    draw.text((RX, 64), 'Last 5 snipes involving this player', font=f_label, fill=WHITE)
    draw.line([(RX - 10, 96), (W - 28, 96)], fill=SEP, width=2)

    row_h = 94
    if recent_snipes:
        row_w = W - RX - 30
        for i, (snipe_id, sniped_at, hq, guild_tag, difficulty, conns) in enumerate(recent_snipes[:5]):
            ry = 112 + i * row_h
            row = Image.new('RGBA', (row_w, row_h - 8), (0, 0, 0, 0))
            ImageDraw.Draw(row).rounded_rectangle(
                ((0, 0), (row_w - 1, row_h - 9)), fill=(0, 0, 0, 55), radius=8
            )
            card.paste(row, (RX - 2, ry - 2), row)

            date_text = sniped_at.strftime('%d/%m/%y') if sniped_at else '\u2014'
            hq_text = display_hq(hq)
            hq_font = _fit_font(hq_text, draw, 'images/profile/game.ttf', 24, 250)
            meta_text = _format_difficulty_text(difficulty, suffix=f'  |  {conns} conns')
            team_pairs = participants_map.get(snipe_id, [])
            guild_value = guild_tag or '\u2014'

            draw.text((RX + 10, ry + 8), date_text, font=f_label, fill=ACCENT)
            addLine(hq_text, draw, hq_font, RX + 120, ry + 6, drop_x=3, drop_y=3)
            guild_color = guild_colors.get(_normalize_guild_tag(guild_tag), _DEFAULT_GUILD_COLOR)
            meta_y = ry + 34
            meta_x = addLine(f'&#{guild_color[1:]}{guild_value}', draw, f_label, RX + 10, meta_y, drop_x=2, drop_y=2)
            addLine(meta_text, draw, f_label, meta_x + 18, meta_y, drop_x=2, drop_y=2)
            _draw_role_columns(draw, team_pairs, RX + 10, ry + 61, RX + row_w - 12)
    else:
        draw.text((RX, 126), 'No recent snipes found.', font=f_body, fill='#555555')

    return card


def _generate_list_card(page_rows, participants_map, guild_colors, sort_by, season_label, page_num, total_pages, start_idx):
    W, H = 1080, 740
    ROW_H = 58
    card, draw = _card_base(W, H)

    ACCENT, DIM, SEP, WHITE = '#fad51e', '#8ea3cc', '#3474eb', '#ffffff'
    f_title = ImageFont.truetype('images/profile/5x5.ttf', 28)
    f_label = ImageFont.truetype('images/profile/5x5.ttf', 20)
    f_small = ImageFont.truetype('images/profile/game.ttf', 24)

    CX = [38, 112, 270, 515, 760, 905]
    _draw_table_header(
        draw, W, f_title, f_label,
        'SNIPE LIST',
        f'{season_label}  \u2022  Sorted by: {sort_by}',
        ['#', 'DATE', 'HQ', 'GUILD', 'DIFF.', 'CONNS'],
        CX, None, [None, None, None, None, None, None],
        ACCENT, DIM, SEP, WHITE
    )

    row_bg = Image.new('RGBA', (W - 56, ROW_H - 4), (0, 0, 0, 0))
    ImageDraw.Draw(row_bg).rounded_rectangle(
        ((0, 0), (W - 57, ROW_H - 5)), fill=(0, 0, 0, 55), radius=6
    )

    if page_rows:
        for i, (snipe_id, sniped_at, hq, guild_tag, difficulty, conns) in enumerate(page_rows):
            ry = 128 + i * ROW_H
            card.paste(row_bg, (28, ry - 2), row_bg)

            date_text = sniped_at.strftime('%d/%m/%y') if sniped_at else '\u2014'
            hq_text = display_hq(hq)
            hq_font = _fit_font(hq_text, draw, 'images/profile/game.ttf', 24, CX[3] - CX[2] - 18)
            team_pairs = participants_map.get(snipe_id, [])

            draw.text((CX[0], ry + 2), f'#{start_idx + i}', font=f_small, fill=ACCENT)
            addLine(date_text, draw, f_small, CX[1], ry + 2, drop_x=3, drop_y=3)
            addLine(hq_text, draw, hq_font, CX[2], ry + 2, drop_x=3, drop_y=3)
            guild_color = guild_colors.get(_normalize_guild_tag(guild_tag), _DEFAULT_GUILD_COLOR)
            addLine(f'&#{guild_color[1:]}{guild_tag or "\u2014"}', draw, f_small, CX[3], ry + 2, drop_x=3, drop_y=3)
            addLine(_format_difficulty_text(difficulty), draw, f_small, CX[4], ry + 2, drop_x=3, drop_y=3)
            addLine(str(conns), draw, f_small, CX[5], ry + 2, drop_x=3, drop_y=3)
            _draw_role_columns(draw, team_pairs, CX[1], ry + 33, CX[4] - 12)
    else:
        draw.text((38, 140), 'No entries yet.', font=f_small, fill='#555555')

    _draw_table_footer(draw, W, H, f_label, page_num, total_pages, ACCENT)
    return card


def _format_participants_log(pairs: list[tuple[str, str]]) -> str:
    grouped = defaultdict(list)
    for ign, role in pairs:
        grouped[role].append(ign)
    parts = []
    for role in _ROLE_ORDER:
        if role in grouped:
            parts.append(f"{' '.join(grouped[role])} {role}")
    return ' / '.join(parts)


async def _hq_autocomplete(ctx: discord.AutocompleteContext):
    typed = (ctx.value or "").casefold()
    results = []
    for full_name in ALL_TERRITORY_NAMES:
        if not typed or typed in full_name.casefold():
            results.append(discord.OptionChoice(name=full_name, value=full_name))
        if len(results) == 25:
            break
    return results


# ── Cog ───────────────────────────────────────────────────────────────────────

class SnipeLogConfirmView(discord.ui.View):
    def __init__(self, invoker_id: int):
        super().__init__(timeout=60)
        self.invoker_id  = invoker_id
        self.confirmed   = None  # True / False / None (timeout)
        self.interaction = None  # set on button click
        self.message     = None  # set after send, for timeout cleanup

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.green, emoji='✅')
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message('Only you can confirm this.', ephemeral=True)
            return
        self.confirmed   = True
        self.interaction = interaction
        await interaction.response.edit_message(content='Logging snipe…', embed=None, view=None)
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red, emoji='❌')
    async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message('Only you can cancel this.', ephemeral=True)
            return
        self.confirmed   = False
        self.interaction = interaction
        await interaction.response.edit_message(content='Snipe log cancelled.', embed=None, view=None)
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(content='Timed out — snipe was not logged.', embed=None, view=self)
            except Exception:
                pass


class SnipeTracker(commands.Cog):
    def __init__(self, client):
        self.client = client

    snipe = SlashCommandGroup(
        'snipe',
        'Snipe tracking commands',
        integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install},
        contexts={discord.InteractionContextType.guild, discord.InteractionContextType.bot_dm, discord.InteractionContextType.private_channel},
    )

    async def _get_home_member(self, user_id: int) -> discord.Member | None:
        guild = self.client.get_guild(TAQ_GUILD_ID)
        if guild is None:
            return None

        member = guild.get_member(user_id)
        if member is not None:
            return member

        try:
            return await guild.fetch_member(user_id)
        except (discord.NotFound, discord.HTTPException):
            return None

    async def cog_check(self, ctx: discord.ApplicationContext) -> bool:
        member = await self._get_home_member(ctx.author.id)
        if not member or not discord.utils.get(member.roles, id=HQ_TEAM_ROLE_ID):
            await ctx.respond(':no_entry: You need the **HQ Team** role to use snipe commands.', ephemeral=True)
            return False
        return True

    # ── /snipe log ────────────────────────────────────────────────────────────

    @snipe.command(name='log', description='Log a territory snipe')
    async def log_snipe(
        self,
        ctx: discord.ApplicationContext,
        participants:   discord.Option(str, "Participants as 'IGN Role, IGN Role, ...'", required=True),
        hq:             discord.Option(str, 'HQ location (name or abbreviation)', autocomplete=_hq_autocomplete, required=True),
        difficulty:     discord.Option(int, 'Difficulty in thousands (e.g. 192 for 192k)', required=True),
        guild:          discord.Option(str, 'Guild tag that owned the HQ', required=True),
        conns:          discord.Option(int, 'Connections (0–6)', required=True, min_value=0, max_value=6),
        snipe_date:     discord.Option(str, 'Date as DD/MM/YYYY or Unix timestamp (defaults to now)', required=False, default=None),
        log_to_channel: discord.Option(bool, 'Post to snipe log channel', required=False, default=False),
        image:          discord.Option(discord.Attachment, 'Screenshot (required when logging to channel)', required=False, default=None),
        season:         discord.Option(int, 'Season (defaults to current)', required=False, default=None),
        notes:          discord.Option(str, 'Additional notes (only shown in snipe log channel post)', required=False, default=None),
    ):
        await ctx.defer(ephemeral=True)

        # Check War Trainer role
        member = await self._get_home_member(ctx.author.id)
        if not member or not discord.utils.get(member.roles, name='War Trainer'):
            await ctx.followup.send(':no_entry: You must have the **War Trainer** role to log snipes.', ephemeral=True)
            return
        if log_to_channel and image is None:
            await ctx.followup.send(':no_entry: You must attach a screenshot when logging to the snipe channel.', ephemeral=True)
            return

        # Validate and normalize HQ
        hq_raw = hq
        hq = normalize_hq_for_storage(hq)
        if hq is None:
            await ctx.followup.send(f':no_entry: Unknown HQ `{hq_raw}`. Type a territory name.', ephemeral=True)
            return

        # Parse participants string into (IGN, role) pairs
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

        # Parse snipe date
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

        # Resolve season + dry flag
        season = season if season is not None else _get_current_season()
        dry    = is_dry(hq, conns)

        # Build confirmation embed
        conn_display = f'{conns} (Dry)' if dry else str(conns)
        snipe_dt_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%d/%m/%Y')
        preview_embed = discord.Embed(
            title='Confirm Snipe Log',
            description=f'**{display_hq(hq)}** sniped from **{guild.upper()}**',
            color=0xe67e22
        )
        preview_embed.add_field(name='Difficulty',   value=f'{difficulty}k',  inline=True)
        preview_embed.add_field(name='Connections',  value=conn_display,       inline=True)
        preview_embed.add_field(name='Date',         value=snipe_dt_str,       inline=True)
        preview_embed.add_field(name='Season',       value=str(season),        inline=True)
        preview_embed.add_field(name='Participants', value=_format_participants_log(pairs), inline=False)
        if notes:
            preview_embed.add_field(name='Notes', value=notes, inline=False)
        if log_to_channel:
            preview_embed.set_footer(text='This snipe will also be posted to the snipe log channel.')

        # Show confirm view and wait for response
        view = SnipeLogConfirmView(invoker_id=ctx.author.id)
        msg  = await ctx.followup.send(embed=preview_embed, view=view, ephemeral=True)
        view.message = msg
        await view.wait()

        if not view.confirmed:
            return

        # Write snipe and participants to DB
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                'INSERT INTO snipe_logs (hq, difficulty, sniped_at, guild_tag, conns, logged_by, season) '
                'VALUES (%s, %s, to_timestamp(%s), %s, %s, %s, %s) RETURNING id',
                (hq, difficulty, ts, guild.upper(), conns, ctx.author.id, season)
            )
            snipe_id = db.cursor.fetchone()[0]
            canonical_map = _resolve_igns(db, [ign for ign, _ in pairs])
            for ign, role in pairs:
                ign = canonical_map.get(ign.lower(), ign)
                db.cursor.execute(
                    'INSERT INTO snipe_participants (snipe_id, ign, role) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING',
                    (snipe_id, ign, role)
                )
            db.connection.commit()
        finally:
            db.close()

        # Send success embed
        success_embed = discord.Embed(
            title='Snipe Logged',
            description=f'**{display_hq(hq)}** sniped from **{guild.upper()}**',
            color=0x2ecc71
        )
        success_embed.add_field(name='Difficulty',   value=f'{difficulty}k', inline=True)
        success_embed.add_field(name='Connections',  value=conn_display,      inline=True)
        success_embed.add_field(name='Participants', value=_format_participants_log(pairs), inline=False)
        await view.interaction.edit_original_response(content='', embed=success_embed)

        # Post to snipe log channel
        if log_to_channel:
            snipe_dt         = datetime.fromtimestamp(ts, tz=timezone.utc)
            diff_label       = 'Drysnipe' if dry else f'{conns} Conns'
            participants_str = _format_participants_log(pairs)
            log_text = (
                f"**Date:** {snipe_dt.strftime('%d/%m/%y')}\n"
                f"**Participants:** {participants_str}\n"
                f"**Location:** {display_hq(hq)} ({guild.upper()})\n"
                f"**Difficulty:** {diff_label} / {difficulty}k\n"
                f"**Result:** Success"
                + (f"\n**Notes:** {notes}" if notes else "")
            )
            resp     = await asyncio.to_thread(requests.get, image.url)
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
            # Resolve canonical IGN casing
            ign = _resolve_ign(db, ign)

            # Total snipes + rank by count
            db.cursor.execute(
                'SELECT total, rank FROM ('
                '  SELECT ign, COUNT(*) AS total, RANK() OVER (ORDER BY COUNT(*) DESC) AS rank'
                '  FROM snipe_participants GROUP BY ign'
                ') ranked WHERE ign = %s', (ign,)
            )
            rank_total_row = db.cursor.fetchone()
            total_snipes   = rank_total_row[0] if rank_total_row else 0
            rank_total     = rank_total_row[1] if rank_total_row else None

            # Personal best difficulty + difficulty rank
            db.cursor.execute(
                'SELECT hq, best_diff, rank FROM ('
                '  SELECT sp.ign,'
                '    MAX(sl.difficulty) AS best_diff,'
                '    (SELECT sl2.hq FROM snipe_logs sl2 JOIN snipe_participants sp2 ON sp2.snipe_id = sl2.id'
                '     WHERE sp2.ign = sp.ign ORDER BY sl2.difficulty DESC, sl2.id DESC LIMIT 1) AS hq,'
                '    RANK() OVER (ORDER BY MAX(sl.difficulty) DESC) AS rank'
                '  FROM snipe_logs sl JOIN snipe_participants sp ON sp.snipe_id = sl.id GROUP BY sp.ign'
                ') ranked WHERE ign = %s', (ign,)
            )
            pb_row    = db.cursor.fetchone()
            rank_diff = pb_row[2] if pb_row else None

            # Zero-connection snipes
            db.cursor.execute(
                'SELECT COUNT(*) FROM snipe_logs sl JOIN snipe_participants sp ON sp.snipe_id = sl.id'
                ' WHERE sp.ign = %s AND sl.conns = 0', (ign,)
            )
            zero_conn_count = db.cursor.fetchone()[0]

            # Dry snipe count
            db.cursor.execute(
                'SELECT sl.hq, sl.conns FROM snipe_logs sl'
                ' JOIN snipe_participants sp ON sp.snipe_id = sl.id WHERE sp.ign = %s', (ign,)
            )
            dry_snipes = sum(
                1 for hq_abbr, c in db.cursor.fetchall()
                if is_dry(hq_abbr, c)
            )

            # First and latest snipe timestamps
            db.cursor.execute(
                'SELECT MIN(sl.sniped_at), MAX(sl.sniped_at) FROM snipe_logs sl'
                ' JOIN snipe_participants sp ON sp.snipe_id = sl.id WHERE sp.ign = %s', (ign,)
            )
            time_row     = db.cursor.fetchone()
            first_snipe  = time_row[0] if time_row else None
            latest_snipe = time_row[1] if time_row else None

            # Unique guilds and HQs sniped
            db.cursor.execute(
                'SELECT COUNT(DISTINCT sl.guild_tag), COUNT(DISTINCT sl.hq) FROM snipe_logs sl'
                ' JOIN snipe_participants sp ON sp.snipe_id = sl.id WHERE sp.ign = %s', (ign,)
            )
            uniq_row      = db.cursor.fetchone()
            unique_guilds = uniq_row[0] if uniq_row else 0
            unique_hqs    = uniq_row[1] if uniq_row else 0

            # Most snipes in a single day
            db.cursor.execute(
                'SELECT MAX(daily_count) FROM ('
                '  SELECT COUNT(*) AS daily_count FROM snipe_logs sl'
                '  JOIN snipe_participants sp ON sp.snipe_id = sl.id'
                '  WHERE sp.ign = %s GROUP BY DATE(sl.sniped_at)'
                ') daily', (ign,)
            )
            most_in_day = db.cursor.fetchone()[0] or 0

            # Top 3 most sniped guilds
            db.cursor.execute(
                'SELECT sl.guild_tag, COUNT(*) AS n FROM snipe_logs sl'
                ' JOIN snipe_participants sp ON sp.snipe_id = sl.id'
                ' WHERE sp.ign = %s GROUP BY sl.guild_tag ORDER BY n DESC LIMIT 3', (ign,)
            )
            top_guilds = db.cursor.fetchall()

            # All sniped HQs by frequency
            db.cursor.execute(
                'SELECT sl.hq, COUNT(*) AS n FROM snipe_logs sl'
                ' JOIN snipe_participants sp ON sp.snipe_id = sl.id'
                ' WHERE sp.ign = %s GROUP BY sl.hq ORDER BY n DESC', (ign,)
            )
            hq_rows = db.cursor.fetchall()

            # Top teammates by shared snipe count
            db.cursor.execute(
                'SELECT other_sp.ign, COUNT(*) AS shared FROM snipe_participants other_sp'
                ' WHERE other_sp.snipe_id IN ('
                '   SELECT sp.snipe_id FROM snipe_participants sp WHERE sp.ign = %s'
                ' ) AND other_sp.ign != %s'
                ' GROUP BY other_sp.ign ORDER BY shared DESC LIMIT 6', (ign, ign)
            )
            teammate_rows = db.cursor.fetchall()

            # Most played role + count (all-time)
            db.cursor.execute(
                'SELECT role, COUNT(*) FROM snipe_participants WHERE ign = %s'
                ' GROUP BY role ORDER BY COUNT(*) DESC LIMIT 1', (ign,)
            )
            role_row = db.cursor.fetchone()
            top_role = f'{role_row[0]} ({role_row[1]})' if role_row else None

            # Number of distinct seasons with at least one snipe
            db.cursor.execute(
                'SELECT COUNT(DISTINCT sl.season) FROM snipe_logs sl'
                ' JOIN snipe_participants sp ON sp.snipe_id = sl.id WHERE sp.ign = %s', (ign,)
            )
            seasons_active = db.cursor.fetchone()[0] or 0

            # Compute daily snipe streaks
            db.cursor.execute(
                "SELECT DISTINCT DATE(sl.sniped_at AT TIME ZONE 'UTC') FROM snipe_logs sl"
                ' JOIN snipe_participants sp ON sp.snipe_id = sl.id WHERE sp.ign = %s', (ign,)
            )
            snipe_dates          = [row[0] for row in db.cursor.fetchall()]
            streak_best, streak_cur = _compute_streaks(snipe_dates)

        finally:
            db.close()

        # Fetch TAq rank badge info
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

        # Fetch guild colors for top guilds
        guild_colors = await _get_guild_color_map(tag for tag, _ in top_guilds)

        # Generate and send stats card
        card = _generate_snipe_card(
            ign, total_snipes, rank_total, rank_diff, pb_row,
            dry_snipes, zero_conn_count, most_in_day,
            unique_hqs, unique_guilds, first_snipe, latest_snipe,
            top_guilds, hq_rows, teammate_rows,
            top_role, streak_best, streak_cur, seasons_active,
            rank_text=rank_text, rank_color=rank_color, guild_colors=guild_colors,
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
        # Resolve season clause and label
        sc, sp  = _season_clause(season)
        sl      = _season_label(season)

        # Fetch and sort all player stats
        db = DB()
        db.connect()
        try:
            player_stats = _fetch_lb_data(db, sc, sp)
        finally:
            db.close()

        player_stats.sort(key=_LB_SORT_KEY[sort])

        # Paginate into leaderboard cards
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
        # Resolve season and sort order
        sc, sp = _season_clause(season)
        sl     = _season_label(season)
        order  = 'COUNT(*) DESC' if sort == 'Amount' else 'MAX(sl.difficulty) DESC'

        # Fetch role participants
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

        # Paginate into role leaderboard cards
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

    @snipe.command(name='roster', description='View the snipe roster')
    async def snipe_roster(
        self,
        ctx: discord.ApplicationContext,
        season: discord.Option(int, 'Season (0 = all-time, default = current)', required=False, default=None),
        inguild: discord.Option(bool, 'Only show players currently in TAq', required=False, default=False),
    ):
        await ctx.defer()
        # Resolve season
        sc, sp = _season_clause(season)
        sl     = _season_label(season)

        # Fetch per-player totals and role flags
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                f"SELECT sp.ign, COUNT(*) AS total, MAX(sl.difficulty) AS best_diff, "
                f"MAX(CASE WHEN sp.role = 'Healer' THEN 1 ELSE 0 END) AS has_healer, "
                f"MAX(CASE WHEN sp.role = 'Tank' THEN 1 ELSE 0 END) AS has_tank, "
                f"MAX(CASE WHEN sp.role = 'DPS' THEN 1 ELSE 0 END) AS has_dps "
                f' FROM snipe_participants sp'
                f' JOIN snipe_logs sl ON sl.id = sp.snipe_id'
                f' WHERE 1=1 {sc}'
                f' GROUP BY sp.ign ORDER BY total DESC',
                sp
            )
            raw_rows = db.cursor.fetchall()
        finally:
            db.close()

        # Optionally filter to current TAq members
        current_names = None
        if inguild:
            current_data = get_current_guild_data()
            current_members = current_data.get('members', []) if isinstance(current_data, dict) else []
            current_names = {
                (member.get('name') or member.get('username') or '').casefold()
                for member in current_members
                if member.get('name') or member.get('username')
            }
            if not current_names:
                await ctx.followup.send(':no_entry: Current TAq member data is unavailable right now.')
                return

        # Build role strings per player
        rows = []
        for ign, total, best_diff, has_healer, has_tank, has_dps in raw_rows:
            if current_names is not None and ign.casefold() not in current_names:
                continue
            roles = []
            if has_healer:
                roles.append('Healer')
            if has_tank:
                roles.append('Tank')
            if has_dps:
                roles.append('DPS')
            rows.append((ign, total, best_diff, ', '.join(roles) if roles else '\u2014'))

        # Generate and send roster card
        card = _generate_team_card(rows, sl)
        buf = BytesIO()
        card.save(buf, format='PNG')
        buf.seek(0)
        await ctx.followup.send(file=discord.File(buf, filename='roster.png'))

    # ── /snipe duos ───────────────────────────────────────────────────────────

    @snipe.command(name='duos', description='View snipe duos')
    async def snipe_duos(
        self,
        ctx: discord.ApplicationContext,
        season: discord.Option(int, 'Season (0 = all-time, default = current)', required=False, default=None),
    ):
        await ctx.defer()
        # Resolve season
        sc, sp = _season_clause(season)
        sl     = _season_label(season)

        # Fetch all duo pairs with shared snipe count
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                f'SELECT a.ign, b.ign, COUNT(*) AS shared, MAX(sl.difficulty) AS best_diff'
                f' FROM snipe_participants a'
                f' JOIN snipe_participants b ON a.snipe_id = b.snipe_id AND a.ign < b.ign'
                f' JOIN snipe_logs sl ON sl.id = a.snipe_id'
                f' WHERE 1=1 {sc}'
                f' GROUP BY a.ign, b.ign ORDER BY shared DESC, best_diff DESC, a.ign ASC, b.ign ASC',
                sp
            )
            rows = db.cursor.fetchall()
        finally:
            db.close()

        # Paginate into duo leaderboard cards
        total_pages = max(1, math.ceil(len(rows) / _LB_PER_PAGE))
        cards = [
            _generate_duo_card(
                rows[i * _LB_PER_PAGE:(i + 1) * _LB_PER_PAGE],
                sl,
                i + 1,
                total_pages,
                i * _LB_PER_PAGE + 1,
            )
            for i in range(total_pages)
        ]
        await _make_paginator(_pages_from_cards(cards, 'duos')).respond(ctx.interaction)

    # ── /snipe overview ───────────────────────────────────────────────────────

    @snipe.command(name='overview', description='View the snipe overview of a player')
    async def snipe_overview(
        self,
        ctx: discord.ApplicationContext,
        ign: discord.Option(str, 'IGN of the player', required=True),
    ):
        await ctx.defer()

        # SQL CASE for deterministic role sort order
        role_case = (
            "CASE sp.role "
            "WHEN 'Healer' THEN 1 "
            "WHEN 'Tank' THEN 2 "
            "WHEN 'DPS' THEN 3 "
            "ELSE 99 END"
        )

        db = DB()
        db.connect()
        try:
            # Resolve canonical IGN casing
            ign = _resolve_ign(db, ign)

            # Total snipes and average difficulty
            db.cursor.execute(
                'SELECT COUNT(*), AVG(sl.difficulty) '
                'FROM snipe_logs sl '
                'JOIN snipe_participants sp ON sp.snipe_id = sl.id '
                'WHERE sp.ign = %s',
                (ign,)
            )
            summary_row = db.cursor.fetchone()
            total_snipes_all = summary_row[0] or 0
            avg_diff = float(summary_row[1]) if summary_row and summary_row[1] is not None else None

            # Most frequently sniped HQ
            db.cursor.execute(
                'SELECT sl.hq, COUNT(*) AS n '
                'FROM snipe_logs sl '
                'JOIN snipe_participants sp ON sp.snipe_id = sl.id '
                'WHERE sp.ign = %s '
                'GROUP BY sl.hq ORDER BY n DESC, sl.hq ASC LIMIT 1',
                (ign,)
            )
            top_hq_row = db.cursor.fetchone()
            top_hq = display_hq(top_hq_row[0]) if top_hq_row else None
            top_hq_count = top_hq_row[1] if top_hq_row else 0

            # Last 5 snipes for this player
            db.cursor.execute(
                'SELECT sl.id, sl.sniped_at, sl.hq, sl.guild_tag, sl.difficulty, sl.conns '
                'FROM snipe_logs sl '
                'JOIN snipe_participants sp ON sp.snipe_id = sl.id '
                'WHERE sp.ign = %s '
                'ORDER BY sl.sniped_at DESC LIMIT 5',
                (ign,)
            )
            recent_snipes = db.cursor.fetchall()

            # All participants for snipes involving this player
            db.cursor.execute(
                f'SELECT sp.snipe_id, sp.ign, sp.role '
                f'FROM snipe_participants sp '
                f'WHERE sp.snipe_id IN ('
                f'  SELECT snipe_id FROM snipe_participants WHERE ign = %s'
                f') '
                f'ORDER BY sp.snipe_id DESC, {role_case}, sp.ign ASC',
                (ign,)
            )
            team_rows = db.cursor.fetchall()
        finally:
            db.close()

        # Guard: no snipes logged yet
        if total_snipes_all == 0:
            await ctx.followup.send(f":no_entry: `{ign}` has no logged snipes.")
            return

        # Group participants by snipe ID
        participants_all = defaultdict(list)
        for snipe_id, member_ign, role in team_rows:
            participants_all[snipe_id].append((member_ign, role))

        # Find most common team compositions
        team_counter_any = Counter(
            _format_team_names_compact(pairs)
            for pairs in participants_all.values()
            if pairs
        )
        team_counter_roles = Counter(
            _format_team_compact(pairs)
            for pairs in participants_all.values()
            if pairs
        )
        most_common_team_any, team_any_count = team_counter_any.most_common(1)[0] if team_counter_any else ('\u2014', 0)
        most_common_team_roles, team_role_count = team_counter_roles.most_common(1)[0] if team_counter_roles else ('\u2014', 0)

        # Isolate participants for the 5 recent snipes
        recent_ids = {row[0] for row in recent_snipes}
        recent_participants = {
            snipe_id: pairs for snipe_id, pairs in participants_all.items() if snipe_id in recent_ids
        }
        guild_colors = await _get_guild_color_map(guild_tag for _, _, _, guild_tag, _, _ in recent_snipes)

        # Fetch TAq rank badge info
        rank_text = rank_color = None
        try:
            player = await asyncio.to_thread(PlayerStats, ign, 1)
            if not player.error:
                if player.taq and player.linked and player.rank in discord_ranks:
                    rank_text = player.rank.upper()
                    rank_color = discord_ranks[player.rank]['color']
                elif player.guild_rank:
                    rank_text = player.guild_rank.upper()
                    rank_color = '#a0aeb0'
        except Exception:
            pass

        # Generate and send overview card
        card = _generate_overview_card(
            ign, rank_text, rank_color,
            most_common_team_any, team_any_count,
            most_common_team_roles, team_role_count,
            recent_snipes, recent_participants,
            avg_diff, top_hq, top_hq_count, total_snipes_all, guild_colors,
        )
        buf = BytesIO()
        card.save(buf, format='PNG')
        buf.seek(0)
        await ctx.followup.send(file=discord.File(buf, filename=f'snipe_overview_{ign}.png'))

    # ── /snipe list ───────────────────────────────────────────────────────────

    @snipe.command(name='list', description='View the full snipe log')
    async def snipe_list(
        self,
        ctx: discord.ApplicationContext,
        sort: discord.Option(str, 'Sort by', choices=_LIST_SORT_CHOICES, required=False, default='Newest'),
        season: discord.Option(int, 'Season (0 = all-time, default = current)', required=False, default=None),
    ):
        await ctx.defer()
        # Resolve season and sort order
        sc, sp = _season_clause(season)
        sl = _season_label(season)
        order_sql = _LIST_ORDER_SQL[sort]

        # SQL CASE for deterministic role sort order
        role_case = (
            "CASE sp.role "
            "WHEN 'Healer' THEN 1 "
            "WHEN 'Tank' THEN 2 "
            "WHEN 'DPS' THEN 3 "
            "ELSE 99 END"
        )

        db = DB()
        db.connect()
        try:
            # Fetch all snipe entries for the season
            db.cursor.execute(
                f'SELECT sl.id, sl.sniped_at, sl.hq, sl.guild_tag, sl.difficulty, sl.conns '
                f'FROM snipe_logs sl '
                f'WHERE 1=1 {sc} '
                f'ORDER BY {order_sql}',
                sp
            )
            all_rows = db.cursor.fetchall()

            # Fetch all participants for the filtered snipes
            db.cursor.execute(
                f'SELECT sp.snipe_id, sp.ign, sp.role '
                f'FROM snipe_participants sp '
                f'JOIN snipe_logs sl ON sl.id = sp.snipe_id '
                f'WHERE 1=1 {sc} '
                f'ORDER BY sp.snipe_id DESC, {role_case}, sp.ign ASC',
                sp
            )
            participant_rows = db.cursor.fetchall()
        finally:
            db.close()

        # Map participants to their snipe IDs
        participants_map = defaultdict(list)
        for snipe_id, ign_name, role in participant_rows:
            participants_map[snipe_id].append((ign_name, role))
        guild_colors = await _get_guild_color_map(guild_tag for _, _, _, guild_tag, _, _ in all_rows)

        # Paginate into list cards
        total_pages = max(1, math.ceil(len(all_rows) / _LIST_PER_PAGE))
        cards = [
            _generate_list_card(
                all_rows[i * _LIST_PER_PAGE:(i + 1) * _LIST_PER_PAGE],
                participants_map,
                guild_colors,
                sort,
                sl,
                i + 1,
                total_pages,
                i * _LIST_PER_PAGE + 1,
            )
            for i in range(total_pages)
        ]
        await _make_paginator(_pages_from_cards(cards, 'snipe_list')).respond(ctx.interaction)

    # ── /warseason ────────────────────────────────────────────────────────────

    @slash_command(description='Set the current war season', guild_ids=ALL_GUILD_IDS)
    async def warseason(
        self,
        ctx: discord.ApplicationContext,
        season: discord.Option(int, 'Season number', required=True, min_value=1),
    ):
        await ctx.defer(ephemeral=True)
        # Check War Trainer role
        member = await self._get_home_member(ctx.author.id)
        if not member or not discord.utils.get(member.roles, name='War Trainer'):
            await ctx.followup.send(':no_entry: You must have the **War Trainer** role to set the war season.', ephemeral=True)
            return
        _set_current_season(season)
        await ctx.followup.send(f'War season set to **Season {season}**.', ephemeral=True)


def setup(client):
    client.add_cog(SnipeTracker(client))
