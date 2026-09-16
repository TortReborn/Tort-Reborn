REEL = "Pull a card"
BAIT = "Daily bait"
HELP = "Card commands"

TANK = "Your tank"
WISH = "Aim your luck"
ADMIN = "Card settings"
POOL = "Drop pool"


def plural(n: int, one: str, many: str | None = None) -> str:
    return one if n == 1 else many or f"{one}s"


def tier_label(tier: str) -> str:
    return tier.capitalize()


def credit(*bits: str | None) -> str:
    return " | ".join(str(b) for b in bits if b)


def wiki(card: dict) -> str | None:
    url = card.get("wiki_url")
    if not url or card.get("member"):
        return None
    return f"[Wiki]({url})"


def next_line(label: str, ts: int) -> str:
    return f"{label} <t:{ts}:R>"


def no_reels(ts: int) -> str:
    return f"No reels left\n{next_line('next', ts)}"


def render_failed() -> str:
    return "Render failed\nreel refunded"


def level_name(label: str) -> str:
    return label or "plain"


def stack_name(card: dict, level: str) -> str:
    return f"{card['name']} {level}".strip()
