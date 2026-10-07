REEL = "Pull a card"
BAIT = "Claim daily Reels and Pearls"
HELP = "View card commands"

TANK = "Your tank"
CARD = "Cards"
WISH = "Aim your luck"
ADMIN = "Card settings"

# One accent for every embed that is about the system rather than a card;
# card-bearing embeds take their tier colour instead.
ACCENT = 0x38C9BD

# The one word for a member card in every list, filter and footer. It matches
# the badge printed on the art.
LIMITED = "Limited"


def plural(n: int, one: str, many: str | None = None) -> str:
    return one if n == 1 else many or f"{one}s"


def count(n: int, one: str, many: str | None = None) -> str:
    """'1 reel', '3 reels'."""
    return f"{n} {plural(n, one, many)}"


def ordinal(n: int) -> str:
    """1st, 2nd, 3rd, 4th ... 11th, 12th, 13th ... 21st."""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def tier_label(tier: str) -> str:
    return LIMITED if tier == "member" else tier.capitalize()


def credit(*bits: str | None) -> str:
    return " · ".join(str(b) for b in bits if b)


def wiki(card: dict) -> str | None:
    url = card.get("wiki_url")
    if not url or card.get("member"):
        return None
    return f"[Wiki]({url})"


def next_line(label: str, ts: int) -> str:
    """A countdown, no colon: Discord renders the stamp as "in an hour", so
    the label runs straight into it as a phrase -- "Next in an hour"."""
    return f"{label} <t:{ts}:R>"


def no_reels(ts: int) -> str:
    return f"Out of reels. {next_line('Next', ts)}"


def render_failed() -> str:
    return "Render failed\nReel refunded"


def reel_failed() -> str:
    return "Reel failed\nReel refunded"


def pull_unshown(name: str) -> str:
    return f"**{name}** couldn't be shown\nIt's in your collection"


def copy_label(copies: int) -> str:
    """How a pull reads in the reel footer: New, then 2nd Copy, 3rd Copy ..."""
    return "New" if copies == 1 else f"{ordinal(copies)} Copy"


def per_hour(rate: int) -> str:
    """Passive Pearls as a rate. A tank that earns nothing says so."""
    return f"{rate} per hour" if rate else "None"


def passive_value(rate: int) -> str:
    """The profile field. Passive Pearls never stop, so the rate is all of it."""
    return per_hour(rate)


def level_name(label: str) -> str:
    return label or "Plain"


def stack_name(card: dict, level: str) -> str:
    return f"{card['name']} {level}".strip()
