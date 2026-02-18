import os
import math
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# runtime config defaults

GRID_COLUMNS = 4
HIGHLIGHT_MODE = "outline"  # none | text | outline | both
HIGHLIGHT_TEXT_COLOR = (23, 255, 255)

# multi-point gradient (NEW logic kept)
OUTLINE_GRADIENT_POINTS = [
    (255, 225, 100),
    (255, 170, 70),
    (255, 130, 40),
    (255, 100, 25),
]

COLOR_BG = (41, 42, 46)
COLOR_ROW = (55, 56, 60)
COLOR_TEXT = (255, 255, 255)

TIER_COLORS = {
    1: (255, 255, 255),
    2: (255, 225, 100),
    3: (255, 150, 50),
}

# paths!

BASE = os.path.dirname(os.path.abspath(__file__))  # Helpers
BASE = os.path.dirname(BASE)  # project root
RES = os.path.join(BASE, "images", "shell_exchange", "resources")

INGS_DIR = os.path.join(BASE, "images", "shell_exchange", "Ings")
MATS_DIR = os.path.join(BASE, "images", "shell_exchange", "Mats")

FONT_FILE = os.path.join(BASE, "images", "profile", "game.ttf")
STAR_FONT_FILE = os.path.join(RES, "Inter-VariableFont_opsz,wght.ttf")
SHELL_ICON_FILE = os.path.join(RES, "shell.png")

# layout

FONT_SIZE = 12
ROW_H = 36
ROW_GAP = 4
COL_GAP = 24

ICON_SIZE = 32
SHELL_SIZE = 16
RIGHT_PAD = 6

# helpers

def display_name(fn):
    return os.path.splitext(fn)[0].replace("_", " ")

def norm_key(fn):
    return os.path.splitext(fn)[0].replace("_", " ").strip().casefold()

def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

def text_w(font, s):
    if not s:
        return 0
    b = font.getbbox(s)
    return b[2] - b[0]

def lerp(a, b, t):
    return int(round(a + (b - a) * t))

def lerp_color(c1, c2, t):
    return tuple(lerp(c1[i], c2[i], t) for i in range(3))

def gradient_sample(points, t):
    if len(points) < 2:
        return points[0]
    t = max(0.0, min(1.0, t))
    segs = len(points) - 1
    x = t * segs
    i = min(segs - 1, int(x))
    return lerp_color(points[i], points[i + 1], x - i)

def draw_gradient_outline(draw, x0, y0, x1, y1, width=2):
    w = x1 - x0
    for dx in range(w):
        t = dx / max(1, w - 1)
        c = gradient_sample(OUTLINE_GRADIENT_POINTS, t)
        draw.rectangle((x0 + dx, y0, x0 + dx + 1, y0 + width), fill=c)
        draw.rectangle((x0 + dx, y1 - width, x0 + dx + 1, y1), fill=c)

    draw.rectangle((x0, y0, x0 + width, y1), fill=OUTLINE_GRADIENT_POINTS[0])
    draw.rectangle((x1 - width, y0, x1, y1), fill=OUTLINE_GRADIENT_POINTS[-1])

def load_icon(path, h):
    img = Image.open(path).convert("RGBA")
    w, ih = img.size
    s = h / ih
    return img.resize((int(w * s), h), Image.NEAREST)

# Deprecated Star Drawing :c

def draw_tier_stars(draw, ix, iy, iw, tier, star_font):
    if tier is None:
        return

    stars = "⭐" * tier
    sw = text_w(star_font, stars)
    color = TIER_COLORS[tier]

    # 3 stars centered, 1–2 right aligned
    if tier == 3:
        x = ix + (iw - sw) // 2
    else:
        x = ix + iw - sw - 1

    y = iy + 1

    shadow = Image.new("RGBA", (sw + 2, 16), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.text((1, 1), stars, font=star_font, fill=(0, 0, 0, 140))
    shadow = shadow.filter(ImageFilter.GaussianBlur(0.5))

    draw.bitmap((x - 3, y - 3), shadow, fill=None)
    draw.text((x, y), stars, font=star_font, fill=color)

# Trade Block

def draw_trade_block(img, draw, shells, per, shell_icon, font, xr, yt, reserve_shell_str, reserve_per_str):
    """
    Draw: shells -> shell icon -> slash -> per
    Anchored to xr (right edge). Uses reserved widths so the shell value + icon
    stay aligned even if per becomes 2 digits.
    """
    shells_s = str(shells)
    per_s = str(per)
    slash_s = " / "

    shells_res_w = text_w(font, reserve_shell_str)
    per_res_w = text_w(font, reserve_per_str)
    shells_w = text_w(font, shells_s)
    per_w = text_w(font, per_s)
    slash_w = text_w(font, slash_s)

    gap = 4
    icon_w = shell_icon.width

    total = shells_res_w + icon_w + gap + slash_w + per_res_w
    x = xr - total

    # shells (right-aligned inside reserved width)
    shells_x = x + (shells_res_w - shells_w)
    draw.text((shells_x, yt + 1), shells_s, font=font, fill=COLOR_TEXT)

    # icon
    icon_x = x + shells_res_w
    img.paste(shell_icon, (icon_x, yt), shell_icon)

    # slash
    slash_x = icon_x + icon_w + gap
    draw.text((slash_x, yt + 1), slash_s, font=font, fill=COLOR_TEXT)

    # per (right-aligned inside reserved width)
    per_box_x = slash_x + slash_w
    per_x = per_box_x + (per_res_w - per_w)
    draw.text((per_x, yt + 1), per_s, font=font, fill=COLOR_TEXT)

# Entry Loading

def build_entry(name, icon, iw, ih, tier, cfg):
    return {
        "name": name,
        "icon": icon,
        "iw": iw,
        "ih": ih,
        "tier": tier,
        "shells": int(cfg.get("shells", 1)),
        "per": int(cfg.get("per", 1)),
        "highlight": bool(cfg.get("highlight", False)),
    }

def load_entries(folder, cfg_data, material):
    cfg = cfg_data
    entries = []

    for fn in sorted(os.listdir(folder)):
        if not fn.lower().endswith(".png"):
            continue

        key = norm_key(fn)
        data = cfg.get(key, {})
        icon = load_icon(os.path.join(folder, fn), ICON_SIZE)
        iw, ih = icon.size
        name = display_name(fn)

        if material:
            for t in (1, 2, 3):
                td = data.get(f"t{t}", {})
                if td.get("toggled", True):
                    entries.append(build_entry(name, icon, iw, ih, t, td))
        else:
            if data.get("toggled", True):
                entries.append(build_entry(name, icon, iw, ih, None, data))

    return entries

# Render

def render_panel(material_mode=False, ings_data=None, mats_data=None):
    folder = MATS_DIR if material_mode else INGS_DIR
    cfg_data = mats_data if material_mode else ings_data

    font = load_font(FONT_FILE, FONT_SIZE)
    star_font = load_font(STAR_FONT_FILE, 14)
    shell_icon = load_icon(SHELL_ICON_FILE, SHELL_SIZE)

    entries = load_entries(folder, cfg_data or {}, material_mode)
    if not entries:
        return None

    # funny width stuff for alignment
    max_shells = max(e["shells"] for e in entries)
    max_per = max(e["per"] for e in entries)
    reserve_shell_str = "9" * max(1, len(str(max_shells)))
    reserve_per_str = "9" * max(1, len(str(max_per)))

    rows = math.ceil(len(entries) / GRID_COLUMNS)
    col_w = max(e["iw"] for e in entries) + 260

    w = GRID_COLUMNS * col_w + (GRID_COLUMNS + 1) * COL_GAP
    h = rows * (ROW_H + ROW_GAP) + 40

    img = Image.new("RGBA", (w, h), COLOR_BG)
    draw = ImageDraw.Draw(img)

    col = row = 0
    for e in entries:
        x0 = COL_GAP + col * (col_w + COL_GAP)
        y0 = 20 + row * (ROW_H + ROW_GAP)
        x1 = x0 + col_w
        y1 = y0 + ROW_H

        if row % 2 == 0:
            draw.rounded_rectangle((x0, y0, x1, y1), 6, fill=COLOR_ROW)

        if e["highlight"] and HIGHLIGHT_MODE in ("outline", "both"):
            draw_gradient_outline(draw, x0, y0, x1, y1)

        ix = x0 + 6
        iy = y0 + (ROW_H - e["ih"]) // 2
        img.paste(e["icon"], (ix, iy), e["icon"])

        name_color = (
            HIGHLIGHT_TEXT_COLOR if e["highlight"] and HIGHLIGHT_MODE in ("text", "both")
            else TIER_COLORS.get(e["tier"], COLOR_TEXT)
        )

        draw.text((ix + e["iw"] + 6, y0 + 10), e["name"], fill=name_color, font=font)

        # Trade block: shells -> icon -> slash -> per (anchored + reserved widths)
        draw_trade_block(
            img,
            draw,
            e["shells"],
            e["per"],
            shell_icon,
            font,
            xr=x1 - RIGHT_PAD,
            yt=y0 + 10,
            reserve_shell_str=reserve_shell_str,
            reserve_per_str=reserve_per_str,
        )

        row += 1
        if row >= rows:
            row = 0
            col += 1

    return img

def generate_images(output_mode, config, ings_data=None, mats_data=None):
    apply_config(config)
    images = {}
    if output_mode in ("ingredients", "both"):
        global GRID_COLUMNS
        GRID_COLUMNS = config.get("cols_ings", 4)
        img = render_panel(material_mode=False, ings_data=ings_data, mats_data=mats_data)
        if img:
            images["ingredients"] = img
    if output_mode in ("materials", "both"):
        GRID_COLUMNS = config.get("cols_mats", 4)
        img = render_panel(material_mode=True, ings_data=ings_data, mats_data=mats_data)
        if img:
            images["materials"] = img
    return images

def apply_config(config):
    global GRID_COLUMNS, HIGHLIGHT_MODE, COLOR_BG, COLOR_ROW, COLOR_TEXT, HIGHLIGHT_TEXT_COLOR, OUTLINE_GRADIENT_POINTS, TIER_COLORS
    GRID_COLUMNS = config.get("cols_mats", 4) if "materials" in config.get("output_mode", "both") else config.get("cols_ings", 4)
    HIGHLIGHT_MODE = config.get("highlight_mode", "outline")
    COLOR_BG = tuple(config.get("color_bg", [41, 42, 46]))
    COLOR_ROW = tuple(config.get("color_row", [55, 56, 60]))
    COLOR_TEXT = tuple(config.get("color_text", [255, 255, 255]))
    HIGHLIGHT_TEXT_COLOR = tuple(config.get("highlight_text", [23, 255, 255]))
    OUTLINE_GRADIENT_POINTS = [tuple(p) for p in config.get("gradient_points", [[255, 225, 100], [255, 170, 70], [255, 130, 40], [255, 100, 25]])]
    TIER_COLORS = {int(k): tuple(v) for k, v in config.get("tier_colors", {"1": [255, 255, 255], "2": [255, 225, 100], "3": [255, 150, 50]}).items()}
