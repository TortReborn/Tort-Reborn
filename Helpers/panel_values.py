"""
Helpers/panel_values.py
Values a bot panel can show by writing {{key}} (the editor shows {Label}):
settings that live in the database and are edited on the website, which
republishes every posted panel that uses one when it is saved. LIVE_VALUES
change on their own instead: the publisher keeps the panels using one current,
without writing a revision (Tasks/publish_bot_panels.py).

Keep VALUES in sync with TAq-Website/lib/panel-values.ts. Adding one means a
key here with its loader, and the same key there with its label.
"""

from collections.abc import Callable

from Helpers.database import get_shell_conversion
from Helpers.tomes import load_queue_text

VALUES: dict[str, Callable[[dict], str]] = {
    "shells_per_aspect": lambda ctx: str(ctx["conversion"]["shells_per_aspect"]),
    "max_aspects_per_conversion": lambda ctx: str(ctx["conversion"]["max_aspects_per_conversion"]),
    "tome_queue": lambda ctx: ctx["tome_queue"],
}
LIVE_VALUES = ("tome_queue",)


def load_panel_values() -> dict[str, str]:
    """Current value of every key (reads the database; blocking, run in a thread)."""
    ctx = {"conversion": get_shell_conversion(), "tome_queue": load_queue_text()}
    return {key: load(ctx) for key, load in VALUES.items()}
