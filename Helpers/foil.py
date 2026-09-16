import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from Helpers.card_render import BORDER, RADIUS

FOIL_NAMES = {0: "standard", 1: "chrome", 2: "prism", 3: "holographic", 4: "ascended"}
_NAME_TO_TIER = {v: k for k, v in FOIL_NAMES.items()}

CHROME_COLOURS = [(90, 92, 98), (225, 228, 233), (150, 152, 158),
                  (255, 255, 255), (110, 112, 118)]
RAINBOW_COLOURS = [(255, 90, 90), (255, 200, 90), (120, 255, 140),
                   (90, 200, 255), (170, 120, 255), (255, 110, 200)]


def normalize_tier(fusion_tier) -> int:
    if isinstance(fusion_tier, str):
        fusion_tier = _NAME_TO_TIER.get(fusion_tier.strip().lower(), 0)
    try:
        fusion_tier = int(fusion_tier)
    except (TypeError, ValueError):
        return 0
    return max(0, min(4, fusion_tier))


def foil_tier_for(stars: int, max_stars: int) -> int:
    if stars <= 0 or max_stars <= 0:
        return 0
    return max(0, min(4, round(4 * stars / max_stars)))


def _seeded_rng(seed) -> random.Random:
    return random.Random(seed) if seed is not None else random.Random()


def _rounded_mask(size, box, radius) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius, fill=255)
    return mask


def _card_shape(size) -> Image.Image:
    w, h = size
    return _rounded_mask(size, [0, 0, w - 1, h - 1], RADIUS)


def _border_ring_mask(size) -> Image.Image:
    w, h = size
    inner = _rounded_mask(size, [BORDER, BORDER, w - 1 - BORDER, h - 1 - BORDER],
                          max(0, RADIUS - 5))
    return ImageChops.subtract(_card_shape(size), inner)


def _angled_gradient(size, colours: list, angle_deg: float) -> Image.Image:
    w, h = size
    diag = int((w ** 2 + h ** 2) ** 0.5) + 4
    strip = Image.new("RGB", (diag, 1))
    d = ImageDraw.Draw(strip)
    n = len(colours)
    for x in range(diag):
        t = (x / max(1, diag - 1)) * (n - 1)
        a, b = colours[int(t)], colours[min(int(t) + 1, n - 1)]
        f = t - int(t)
        d.point((x, 0), tuple(int(a[c] + (b[c] - a[c]) * f) for c in range(3)))
    band = strip.resize((diag, diag)).rotate(angle_deg, resample=Image.BICUBIC)
    x0, y0 = (diag - w) // 2, (diag - h) // 2
    return band.crop((x0, y0, x0 + w, y0 + h))


def _diagonal_lines_mask(size, angle_deg: float, spacing: int = 10,
                         thickness: int = 2) -> Image.Image:
    w, h = size
    diag = int((w ** 2 + h ** 2) ** 0.5) + 4
    lines = Image.new("L", (diag, diag), 0)
    d = ImageDraw.Draw(lines)
    for x in range(0, diag, spacing):
        d.line([(x, 0), (x, diag)], fill=255, width=thickness)
    lines = lines.rotate(angle_deg, resample=Image.BICUBIC)
    x0, y0 = (diag - w) // 2, (diag - h) // 2
    return lines.crop((x0, y0, x0 + w, y0 + h))


def _sparkle_layer(size, rng: random.Random, count: int, region: Image.Image) -> Image.Image:
    w, h = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    placed = 0
    attempts = 0
    while placed < count and attempts < count * 12:
        attempts += 1
        x, y = rng.randint(0, w - 1), rng.randint(0, h - 1)
        if region.getpixel((x, y)) < 128:
            continue
        r = rng.randint(2, 4)
        fill = (255, 255, 255, rng.randint(140, 230))
        d.line([(x - r, y), (x + r, y)], fill=fill, width=1)
        d.line([(x, y - r), (x, y + r)], fill=fill, width=1)
        placed += 1
    return layer


def _glow_ring(size, color: tuple, blur: float, alpha: int) -> Image.Image:
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    glow.paste(Image.new("RGBA", size, color + (alpha,)), (0, 0), _border_ring_mask(size))
    return glow.filter(ImageFilter.GaussianBlur(blur))


def _composite(base: Image.Image, layer: Image.Image, clip: Image.Image) -> Image.Image:
    layer = layer.copy()
    layer.putalpha(ImageChops.multiply(layer.split()[-1], clip))
    return Image.alpha_composite(base, layer)


def apply_foil(card: Image.Image, fusion_tier, *, seed=None, opacity: float = 0.75,
              glow_strength: float = 0.5, sparkle_density: float = 1.0,
              angle: float = 37, animated: bool = False) -> Image.Image:
    tier = normalize_tier(fusion_tier)
    if tier == 0:
        return card.copy()

    card = card.convert("RGBA")
    size = card.size
    clip = _card_shape(size)
    rng = _seeded_rng(f"{seed}:{tier}" if seed is not None else None)
    ring = _border_ring_mask(size)
    result = card.copy()

    if tier == 1:
        chrome = _angled_gradient(size, CHROME_COLOURS, angle).convert("RGBA")
        chrome.putalpha(ring.point(lambda p: int(p * min(1.0, opacity * 0.85))))
        return _composite(result, chrome, clip)

    prism = _angled_gradient(size, RAINBOW_COLOURS, angle).convert("RGBA")
    prism.putalpha(ring.point(lambda p: int(p * min(1.0, opacity))))
    result = _composite(result, prism, clip)

    if tier == 2:
        return result

    lines = _diagonal_lines_mask(size, angle)
    band = Image.new("RGBA", size, (255, 255, 255, 0))
    band.putalpha(ImageChops.multiply(lines, ring).point(lambda p: int(p * 0.18)))
    result = _composite(result, band, clip)

    result = _composite(result, _sparkle_layer(size, rng, int(14 * sparkle_density), ring), clip)

    if glow_strength > 0:
        glow = _glow_ring(size, (255, 255, 255), blur=6, alpha=int(90 * glow_strength))
        result = _composite(result, glow, clip)

    if tier == 3:
        return result

    outer_glow = _glow_ring(size, (255, 245, 220), blur=10,
                            alpha=int(130 * max(glow_strength, 0.6)))
    result = _composite(result, outer_glow, clip)
    result = _composite(result, _sparkle_layer(size, rng, int(10 * sparkle_density), ring), clip)

    return result
