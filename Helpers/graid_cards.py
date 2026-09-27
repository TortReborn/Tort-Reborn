"""Cards for the guild raids we track ourselves.

The player pages reuse the portrait column the Wynncraft raid card draws, so
/graids reads as one card with more pages.
"""

import math

from PIL import Image, ImageDraw

from Helpers.functions import addLine, round_corners, vertical_gradient
from Helpers.graid_stats import RAID_ORDER, UNKNOWN, WEEKDAYS
from Helpers.player_canvas import PlayerCanvas

ACCENT = "#fad51e"
WHITE = "#ffffff"
DIM = "#8ea3cc"
BLOCK = (0, 0, 0, 55)
GRID = (255, 255, 255, 38)
RADAR_FILL = (250, 213, 30, 70)

FONT_LABEL = "images/profile/5x5.ttf"
FONT_VALUE = "images/profile/game.ttf"

RAID_COLORS = {
    "NOTG": "#4cb80f",
    "TCC": "#00d2e6",
    "TNA": "#a05cff",
    "NOL": "#ffcd35",
    "WTP": "#ff442f",
    UNKNOWN: "#6b7a99",
}


def _font(path, size):
    return PlayerCanvas._font(path, size)


def _block(card, x, y, w, h, radius=10, fill=BLOCK):
    box = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(box).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=fill)
    card.paste(box, (x, y), box)


def _panel(width, height):
    card = vertical_gradient(width, height, "#3474eb")
    card = round_corners(card, radius=20)
    inner = vertical_gradient(width - 40, height - 40, "#0e1e3f", "#05101f")
    card.paste(inner, (20, 20), inner)
    return card, ImageDraw.Draw(card)


def _heading(draw, x, y, title, subtitle):
    draw.text((x, y), title, font=_font(FONT_LABEL, 30), fill=ACCENT)
    if subtitle:
        draw.text((x, y + 36), subtitle, font=_font(FONT_LABEL, 20), fill=DIM)


def _date(value):
    return value.strftime("%d %b %Y") if value else "N/A"


def _radar(card, cx, cy, radius, fractions, rings=4, scale=4, halo=16):
    """`fractions` is one 0..1 value per axis, clockwise from the top; the axis
    count comes from its length. Oversampled because Pillow has no
    anti-aliasing."""
    count = len(fractions)
    span = (radius + halo) * 2
    layer = Image.new("RGBA", (span * scale, span * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    middle = span * scale / 2

    draw.ellipse((0, 0, span * scale - 1, span * scale - 1), fill=(0, 0, 0, 30))

    def point(index, distance):
        angle = math.radians(-90 + index * 360 / count)
        return (middle + math.cos(angle) * distance * scale,
                middle + math.sin(angle) * distance * scale)

    for ring in range(1, rings + 1):
        step = radius * ring / rings
        draw.ellipse((middle - step * scale, middle - step * scale,
                      middle + step * scale, middle + step * scale),
                     outline=GRID, width=scale)
    for index in range(count):
        draw.line([(middle, middle), point(index, radius)], fill=GRID, width=scale)

    if any(fractions):
        shape = [point(index, radius * value) for index, value in enumerate(fractions)]
        draw.polygon(shape, fill=RADAR_FILL, outline=ACCENT, width=2 * scale)
        for px, py in shape:
            size = 4 * scale
            draw.rectangle((px - size, py - size, px + size, py + size), fill=ACCENT)

    layer = layer.resize((span, span), Image.Resampling.LANCZOS)
    card.paste(layer, (cx - span // 2, cy - span // 2), layer)


def _icon(short, size):
    try:
        icon = Image.open(f"images/raids/{short}.png").convert("RGBA")
    except FileNotFoundError:
        return None
    icon.thumbnail((size, size))
    return icon


EMPTY_CELL = (255, 255, 255, 20)


def _cells(fraction, count):
    fraction = max(0.0, min(1.0, fraction))
    if fraction <= 0:
        return 0
    return max(1, round(fraction * count))


def _cell_size(length, count, gap):
    """Integer cell and offset, so every gap in a meter is the same width.
    Rounding each cell's position separately drifts them by a pixel."""
    cell = max(2, (length - gap * (count - 1)) // count)
    used = cell * count + gap * (count - 1)
    return cell, (length - used) // 2


def _meter(card, x, y, w, h, fraction, color, count=18, gap=3):
    cell, offset = _cell_size(w, count, gap)
    filled = _cells(fraction, count)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for index in range(count):
        left = offset + index * (cell + gap)
        draw.rectangle((left, 0, left + cell - 1, h - 1),
                       fill=color if index < filled else EMPTY_CELL)
    card.paste(layer, (x, y), layer)


def _vmeter(card, x, y, w, h, fraction, color, count=9, gap=3):
    cell, offset = _cell_size(h, count, gap)
    filled = _cells(fraction, count)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for index in range(count):
        bottom = h - 1 - offset - index * (cell + gap)
        draw.rectangle((0, bottom - cell + 1, w - 1, bottom),
                       fill=color if index < filled else EMPTY_CELL)
    card.paste(layer, (x, y), layer)


# ── Player pages, appended to /graids ────────────────────────────────────────

class GraidPlayerCard(PlayerCanvas):
    """The two tracked-raid pages, on the canvas /graids page 1 uses."""

    def __init__(self, player, player_stats, tag_color, grad_start, grad_end, bg_index):
        self.player = player
        self.player_stats = player_stats
        self.tag_color = tag_color
        self.grad_start = grad_start
        self.grad_end = grad_end
        self.bg_index = bg_index

    def _base(self):
        card = self._build_base_canvas(self.tag_color, self.grad_start, self.grad_end)
        draw = ImageDraw.Draw(card)
        self._draw_background(card, self.tag_color, self.bg_index)
        self._draw_player_header(card, draw, self.player, self.tag_color)
        self._draw_rank_badge(card, self.player_stats, self.tag_color)
        self._draw_guild_elements(card, self.player, self.player_stats)
        return card, draw

    def render(self, stats, range_label):
        return [self._raids_page(stats, range_label), self._habits_page(stats, range_label)]

    def _raids_page(self, stats, range_label):
        card, draw = self._base()
        x = 492
        width = self.CARD_W - x - 38

        _heading(draw, x, 32, "TAq Raids", range_label)

        counts = [stats.counts.get(short, 0) for short in RAID_ORDER]
        unknown = stats.counts.get(UNKNOWN, 0)

        if any(counts):
            self._draw_shape(card, draw, x, width, stats, counts)
        else:
            empty = "No raid type defined yet." if unknown else "No tracked raids yet."
            draw.text((x + width // 2, 330), empty, font=_font(FONT_LABEL, 22),
                      fill=DIM, anchor="ma")

        placement, field = stats.overall_rank
        footer = [
            ("Tracked", str(stats.total)),
            ("Rank", f"#{placement} of {field}" if placement else "N/A"),
            ("Guild Share", f"{stats.guild_share * 100:.1f}%"),
        ]
        box_w = (width - 24) // 3
        for index, (label, value) in enumerate(footer):
            self._draw_stat_box(card, draw, x + index * (box_w + 12), 622, box_w, 70, label, value)
        return card

    def _draw_shape(self, card, draw, x, width, stats, counts):
        cx, cy, radius = x + width // 2, 366, 130
        peak = max(counts)
        _radar(card, cx, cy, radius, [count / peak for count in counts])

        total = sum(counts)
        for index, short in enumerate(RAID_ORDER):
            angle = math.radians(-90 + index * 360 / len(RAID_ORDER))
            across, down = math.cos(angle), math.sin(angle)
            # A block meets the disc edge on at the top, with a long side when
            # near-horizontal, and with a corner on the diagonals. One reach for
            # all three would leave the diagonals looking nearer.
            if abs(across) < 0.2:
                reach = radius + 44
            elif abs(across) > 0.8:
                reach = radius + 62
            else:
                reach = radius + 84
            px = cx + across * reach
            py = cy + down * reach

            count = counts[index]
            share = count / total * 100 if total else 0
            share_text = "<1%" if 0 < share < 0.5 else f"{share:.0f}%"
            value = f"{count}  {share_text}"
            placement, field = stats.type_ranks.get(short, (0, 0))
            rank = f"#{placement} of {field}" if placement else ""

            icon = _icon(short, 30)
            icon_w = (icon.width + 8) if icon else 0
            tag_font, value_font, rank_font = (
                _font(FONT_LABEL, 21), _font(FONT_VALUE, 18), _font(FONT_VALUE, 15))
            tag_w = draw.textbbox((0, 0), short, font=tag_font)[2]
            value_w = draw.textbbox((0, 0), value, font=value_font)[2]
            rank_w = draw.textbbox((0, 0), rank, font=rank_font)[2] if rank else 0
            content_w = max(icon_w + tag_w, value_w, rank_w)

            if abs(across) < 0.2:
                start = px - content_w / 2
                top = py - 84 if down < 0 else py + 8
            else:
                start = px if across > 0 else px - content_w
                top = py - 38

            def line_x(line_w, start=start, across=across, content_w=content_w):
                if abs(across) < 0.2:
                    return start + (content_w - line_w) / 2
                return start if across > 0 else start + content_w - line_w

            _block(card, int(start - 12), int(top - 8), int(content_w + 24), 92,
                   radius=10, fill=(0, 0, 0, 30))

            head_x = line_x(icon_w + tag_w)
            if icon:
                card.paste(icon, (int(head_x), int(top)), icon)
            draw.text((head_x + icon_w, top + 5), short, font=tag_font, fill=WHITE)
            draw.text((line_x(value_w), top + 34), value, font=value_font, fill=ACCENT)
            if rank:
                draw.text((line_x(rank_w), top + 60), rank, font=rank_font,
                          fill=ACCENT if placement <= 10 else DIM)

    def _habits_page(self, stats, range_label):
        card, draw = self._base()
        x = 492
        width = self.CARD_W - x - 38

        _heading(draw, x, 32, "Raid Habits", range_label)

        best_day_label = "N/A"
        if stats.best_day:
            day, count = stats.best_day
            best_day_label = f"{day.strftime('%d %b')} ({count})"
        boxes = [
            ("Best Streak", f"{stats.best_streak}d"),
            ("Current", f"{stats.current_streak}d"),
            ("Best Day", best_day_label),
        ]
        box_w = (width - 24) // 3
        for index, (label, value) in enumerate(boxes):
            self._draw_stat_box(card, draw, x + index * (box_w + 12), 130, box_w, 70, label, value)

        draw.text((x, 232), "BY WEEKDAY", font=_font(FONT_LABEL, 20), fill=ACCENT)
        peak = max(stats.weekdays.values() or [0])
        col_w = (width - 6 * 8) // 7
        for index, day in enumerate(WEEKDAYS):
            value = stats.weekdays[day]
            col_x = x + index * (col_w + 8)
            _vmeter(card, col_x, 264, col_w, 84, (value / peak) if peak else 0, "#5096eb")
            draw.text((col_x + col_w // 2, 356), day, font=_font(FONT_VALUE, 16), fill=ACCENT, anchor="ma")
            draw.text((col_x + col_w // 2, 378), str(value), font=_font(FONT_VALUE, 17), fill=WHITE, anchor="ma")

        draw.text((x, 414), "RAIDS WITH", font=_font(FONT_LABEL, 20), fill=ACCENT)
        if stats.partners:
            for index, (name, count) in enumerate(stats.partners):
                y = 444 + index * 34
                _block(card, x, y, width, 28, radius=8)
                name_font = self._fit_font(name, draw, FONT_VALUE, 20, width - 120, min_size=12)
                addLine(name, draw, name_font, x + 12, y + 4, drop_x=2, drop_y=2)
                count_font = self._fit_font(str(count), draw, FONT_VALUE, 20, 90, min_size=12)
                count_w = draw.textbbox((0, 0), str(count), font=count_font)[2]
                addLine(str(count), draw, count_font, x + width - 14 - count_w, y + 4, drop_x=2, drop_y=2)
        else:
            draw.text((x, 442), "No tracked partners yet.", font=_font(FONT_LABEL, 18), fill=DIM)

        draw.text((x, 620), "FIRST", font=_font(FONT_LABEL, 16), fill=ACCENT)
        draw.text((x, 644), _date(stats.first_raid), font=_font(FONT_LABEL, 18), fill=WHITE)
        draw.text((x + width, 620), "LATEST", font=_font(FONT_LABEL, 16), fill=ACCENT, anchor="ra")
        draw.text((x + width, 644), _date(stats.latest_raid), font=_font(FONT_LABEL, 18), fill=WHITE, anchor="ra")
        return card


# ── Leaderboard ──────────────────────────────────────────────────────────────

LEADERBOARD_PER_PAGE = 15


def leaderboard_pages(rows, range_label, sort_label):
    if not rows:
        return []

    width, row_h = 1000, 34
    header_h, footer_h = 132, 56
    height = header_h + LEADERBOARD_PER_PAGE * row_h + footer_h
    columns = [(560, "TOTAL")] + [(620 + i * 68, short) for i, short in enumerate(RAID_ORDER)]
    book = []

    for start in range(0, len(rows), LEADERBOARD_PER_PAGE):
        chunk = rows[start:start + LEADERBOARD_PER_PAGE]
        card, draw = _panel(width, height)
        _heading(draw, 38, 28, "Guild Raid Leaderboard", range_label)

        label_font = _font(FONT_LABEL, 18)
        draw.text((38, 100), "PLAYER", font=label_font, fill=DIM)
        for cx, header in columns:
            highlight = header.lower() == (sort_label or "").lower()
            draw.text((cx, 100), header, font=label_font, fill=ACCENT if highlight else DIM, anchor="ra")

        for index, row in enumerate(chunk):
            y = header_h + index * row_h
            _block(card, 28, y, width - 56, row_h - 6, radius=8)
            draw.text((44, y + 6), f"{row.placement}.", font=label_font,
                      fill=ACCENT if row.placement <= 3 else DIM)
            name_font = PlayerCanvas._fit_font(row.ign, draw, FONT_VALUE, 20, 420, min_size=12)
            addLine(row.ign, draw, name_font, 100, y + 3, drop_x=2, drop_y=2)

            total_font = _font(FONT_VALUE, 19)
            total_w = draw.textbbox((0, 0), str(row.total), font=total_font)[2]
            addLine(str(row.total), draw, total_font, 560 - total_w, y + 3, drop_x=2, drop_y=2)
            value_font = _font(FONT_VALUE, 17)
            for cx, short in columns[1:]:
                value = row.counts.get(short, 0)
                text = str(value) if value else "\u00b7"
                text_w = draw.textbbox((0, 0), text, font=value_font)[2]
                draw.text((cx - text_w, y + 5), text, font=value_font,
                          fill=RAID_COLORS[short] if value else "#44506b")

        page_no = start // LEADERBOARD_PER_PAGE + 1
        total_pages = (len(rows) + LEADERBOARD_PER_PAGE - 1) // LEADERBOARD_PER_PAGE
        draw.text((width - 38, height - 42), f"Page {page_no} of {total_pages}",
                  font=_font(FONT_LABEL, 17), fill=ACCENT, anchor="ra")
        book.append(card)
    return book


# ── Guild pages ──────────────────────────────────────────────────────────────

def guild_pages(stats):
    return [_guild_totals(stats), _guild_tempo(stats)]


def _guild_totals(stats):
    width, height = 1000, 566
    card, draw = _panel(width, height)
    _heading(draw, 38, 28, "Guild Raids", stats.range_label)

    boxes = [
        ("Raids", f"{stats.total_raids:,}"),
        ("Players", str(stats.unique_players)),
        ("Most Active", stats.top_players[0][0] if stats.top_players else "N/A"),
    ]
    box_w = (width - 76 - 24) // 3
    for index, (label, value) in enumerate(boxes):
        x = 38 + index * (box_w + 12)
        _block(card, x, 118, box_w, 86)
        draw.text((x + 14, 130), label, font=_font(FONT_LABEL, 18), fill=ACCENT)
        value_font = PlayerCanvas._fit_font(value, draw, FONT_VALUE, 32, box_w - 28, min_size=16)
        addLine(value, draw, value_font, x + 14, 160, drop_x=3, drop_y=3)

    draw.text((38, 236), "BY RAID", font=_font(FONT_LABEL, 20), fill=ACCENT)
    shown = [s for s in RAID_ORDER if stats.counts.get(s)] or list(RAID_ORDER)
    if stats.counts.get(UNKNOWN):
        shown = shown + [UNKNOWN]
    peak = max(stats.counts.get(s, 0) for s in shown) or 1
    for index, short in enumerate(shown):
        y = 272 + index * 40
        count = stats.counts.get(short, 0)
        icon = _icon(short, 26)
        if icon:
            card.paste(icon, (38, y - 2), icon)
        label_font = PlayerCanvas._fit_font(short, draw, FONT_LABEL, 19, 84, min_size=13)
        draw.text((74, y + 2), short, font=label_font, fill=WHITE)
        _meter(card, 166, y + 4, 226, 14, count / peak, RAID_COLORS.get(short, WHITE), count=14)
        share = (count / stats.total_raids * 100) if stats.total_raids else 0
        draw.text((410, y + 2), f"{count:,}  ({share:.0f}%)", font=_font(FONT_LABEL, 17), fill=DIM)

    draw.text((560, 236), "TOP PLAYERS", font=_font(FONT_LABEL, 20), fill=ACCENT)
    if stats.top_players:
        best = stats.top_players[0][1] or 1
        for index, (name, total) in enumerate(stats.top_players):
            y = 272 + index * 40
            _block(card, 560, y - 6, width - 598, 34, radius=8)
            draw.text((574, y + 2), f"{index + 1}.", font=_font(FONT_LABEL, 17),
                      fill=ACCENT if index == 0 else DIM)
            name_font = PlayerCanvas._fit_font(name, draw, FONT_VALUE, 19, 180, min_size=12)
            addLine(name, draw, name_font, 610, y - 1, drop_x=2, drop_y=2)
            _meter(card, 792, y + 4, 98, 14, total / best, "#4cb80f", count=7, gap=2)
            draw.text((width - 52, y + 2), str(total), font=_font(FONT_LABEL, 17), fill=WHITE, anchor="ra")
    return card


def _guild_tempo(stats):
    width, height = 1000, 620
    card, draw = _panel(width, height)
    _heading(draw, 38, 28, "Details", stats.range_label)

    # The event panel takes the right half, so the chart only gets the rest.
    chart_right = 528 if stats.event else width - 38
    draw.text((38, 112), "RAIDS PER WEEK", font=_font(FONT_LABEL, 20), fill=ACCENT)
    weeks = stats.weekly[-18:]
    if weeks:
        peak = max(count for _, count in weeks) or 1
        chart_w = chart_right - 38
        col_w = max(6, (chart_w - (len(weeks) - 1) * 6) // len(weeks))
        for index, (week, count) in enumerate(weeks):
            x = 38 + index * (col_w + 6)
            _vmeter(card, x, 152, col_w, 150, count / peak, "#5096eb", count=10)
        draw.text((38, 312), _date(weeks[0][0]), font=_font(FONT_LABEL, 15), fill=DIM)
        draw.text((chart_right, 312), _date(weeks[-1][0]), font=_font(FONT_LABEL, 15), fill=DIM, anchor="ra")
        draw.text((chart_right, 112), f"peak {peak}", font=_font(FONT_LABEL, 17), fill=DIM, anchor="ra")
    else:
        draw.text((38, 152), "No raids in this range.", font=_font(FONT_LABEL, 18), fill=DIM)

    draw.text((38, 352), "BUSIEST DAYS", font=_font(FONT_LABEL, 20), fill=ACCENT)
    peak_day = max(stats.weekdays.values() or [0])
    for index, day in enumerate(WEEKDAYS):
        y = 388 + index * 30
        value = stats.weekdays[day]
        draw.text((38, y), day, font=_font(FONT_LABEL, 17), fill=WHITE)
        _meter(card, 92, y + 2, 240, 14, value / peak_day if peak_day else 0, "#4cb80f", count=14)
        draw.text((346, y), str(value), font=_font(FONT_LABEL, 16), fill=DIM)

    draw.text((560, 352), "RAIDED TOGETHER", font=_font(FONT_LABEL, 20), fill=ACCENT)
    if stats.duos:
        best = stats.duos[0][2] or 1
        for index, (left, right, count) in enumerate(stats.duos):
            y = 384 + index * 40
            _block(card, 560, y, width - 598, 34, radius=8)
            pair = f"{left} + {right}"
            pair_font = PlayerCanvas._fit_font(pair, draw, FONT_VALUE, 18, 210, min_size=11)
            addLine(pair, draw, pair_font, 574, y + 6, drop_x=2, drop_y=2)
            _meter(card, 792, y + 11, 98, 14, count / best, "#a05cff", count=7, gap=2)
            draw.text((width - 52, y + 8), str(count), font=_font(FONT_LABEL, 17), fill=WHITE, anchor="ra")
    else:
        draw.text((560, 388), "No pairs in this range.", font=_font(FONT_LABEL, 18), fill=DIM)

    if stats.event:
        event = stats.event
        _block(card, 560, 118, width - 598, 200)
        draw.text((578, 132), "ACTIVE EVENT", font=_font(FONT_LABEL, 18), fill=ACCENT)
        title_font = PlayerCanvas._fit_font(event["title"], draw, FONT_VALUE, 24, width - 640, min_size=13)
        addLine(event["title"], draw, title_font, 578, 156, drop_x=3, drop_y=3)
        window = f"{_date(event['start'])} → {_date(event['end']) if event['end'] else 'open'}"
        draw.text((578, 192), window, font=_font(FONT_LABEL, 16), fill=DIM)
        for index, (name, total) in enumerate(event["top"]):
            y = 224 + index * 28
            draw.text((578, y), f"{index + 1}. {name}", font=_font(FONT_LABEL, 17), fill=WHITE)
            draw.text((width - 52, y), f"{total} pts", font=_font(FONT_LABEL, 17), fill=ACCENT, anchor="ra")
    return card


# ── Log browse ───────────────────────────────────────────────────────────────

LOG_PER_PAGE = 13


def log_pages(entries, range_label, filter_label):
    if not entries:
        return []

    width, row_h = 1000, 40
    header_h, footer_h = 112, 48
    height = header_h + LOG_PER_PAGE * row_h + footer_h
    book = []

    for start in range(0, len(entries), LOG_PER_PAGE):
        chunk = entries[start:start + LOG_PER_PAGE]
        card, draw = _panel(width, height)
        _heading(draw, 38, 28, "Guild Raid Log", filter_label or range_label)

        for index, entry in enumerate(chunk):
            y = header_h + index * row_h
            _block(card, 28, y, width - 56, row_h - 6, radius=8)
            draw.text((116, y + 9), f"#{entry.ordinal:,}", font=_font(FONT_VALUE, 17),
                      fill=DIM, anchor="ra")
            draw.text((134, y + 9), entry.completed_at.strftime("%d %b %H:%M"),
                      font=_font(FONT_LABEL, 17), fill=DIM)
            icon = _icon(entry.short, 22)
            if icon:
                card.paste(icon, (290, y + 5), icon)
            draw.text((320, y + 9), entry.short, font=_font(FONT_LABEL, 18),
                      fill=RAID_COLORS.get(entry.short, WHITE))
            team = ", ".join(entry.participants)
            team_font = PlayerCanvas._fit_font(team, draw, FONT_VALUE, 18, width - 440, min_size=11)
            addLine(team, draw, team_font, 400, y + 6, drop_x=2, drop_y=2)

        page_no = start // LOG_PER_PAGE + 1
        total_pages = (len(entries) + LOG_PER_PAGE - 1) // LOG_PER_PAGE
        draw.text((width - 38, height - 42), f"Page {page_no} of {total_pages}",
                  font=_font(FONT_LABEL, 17), fill=ACCENT, anchor="ra")
        book.append(card)
    return book
