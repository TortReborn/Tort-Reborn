"""
Helpers/logger.py
Centralized logging — prints to console AND queues messages for a Discord channel.

Usage:
    from Helpers.logger import log, SYSTEM, SUCCESS, INFO, WARN, ERROR

    log(INFO, "Something happened")
    log(ERROR, "Uh oh", context="task_name")
"""

import asyncio
import datetime
from datetime import timezone
from collections import deque

from Helpers.mentions import NO_MENTIONS
from Helpers.variables import LOG_CHANNEL_ID

# ---------------------------------------------------------------------------
# Log levels (colored-square prefixes)
# ---------------------------------------------------------------------------
SYSTEM  = "🟪"   # Bot lifecycle: startup, login, extensions, shutdown
SUCCESS = "🟩"   # Operation completed
INFO    = "🟦"   # General info, loop lifecycle, task progress
WARN    = "🟨"   # Non-critical warnings
ERROR   = "🟥"   # Errors, exceptions, failures

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------
_queue: deque = deque()
_client = None
_flush_task = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init(client):
    """Store a reference to the bot client. Call once after creating it."""
    global _client
    _client = client


def start():
    """Begin the background flush loop. Call once from on_ready."""
    global _flush_task
    if _flush_task is None:
        _flush_task = asyncio.create_task(_flush_loop())


def log(level: str, message: str, *, context: str | None = None):
    """
    Log a message to the console and queue it for the Discord channel.

    Parameters
    ----------
    level : str
        One of SYSTEM, SUCCESS, INFO, WARN, ERROR.
    message : str
        The log message.
    context : str, optional
        Module or task name shown as ``[context]``.
    """
    now = datetime.datetime.now(timezone.utc).strftime("%H:%M:%S")
    ctx = f"[{context}] " if context else ""

    # Console
    print(f"{level} {ctx}{message}", flush=True)

    # Discord queue
    _queue.append(f"{level} `{now}` {ctx}{message}")


# ---------------------------------------------------------------------------
# Background flush
# ---------------------------------------------------------------------------

async def _flush_loop():
    """Periodically send queued log lines to the Discord channel."""
    while True:
        try:
            await _flush()
        except Exception:
            pass  # never crash the bot over a log failure
        await asyncio.sleep(5)


async def _flush():
    if not _client or not _queue:
        return

    channel = _client.get_channel(LOG_CHANNEL_ID)
    if not channel:
        return

    while _queue:
        batch = ""
        while _queue:
            line = _queue[0]
            # +1 for the newline
            if batch and len(batch) + len(line) + 1 > 1900:
                break
            _queue.popleft()
            if batch:
                batch += "\n"
            batch += line

        if batch:
            try:
                await channel.send(batch, allowed_mentions=NO_MENTIONS)
            except Exception:
                pass
