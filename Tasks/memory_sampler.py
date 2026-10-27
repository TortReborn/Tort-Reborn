"""
Tasks/memory_sampler.py
Writes a snapshot of what the bot's memory is made of to bot_memory_samples
every 10 minutes (TAQ-105), so a run's growth can be read back by query
instead of guessed from Railway's single total line.

Rows carry the Railway deployment id and the process uptime, which keeps runs
apart (a deploy changeover briefly has two processes writing) and lets runs
of different ages be compared hour for hour. scripts/memory_report.py reads
them back.

The first sample is taken as soon as the client is ready, so every run
records its starting point.
"""

import asyncio
import os
import threading
import time

from discord.ext import commands, tasks
from psycopg2.extras import Json

from Helpers import memory_stats
from Helpers.database import DB
from Helpers.logger import log, ERROR

INTERVAL_MINUTES = 10
RETENTION_DAYS = 30

# Extensions load during boot, so import time stands in for process start.
_STARTED = time.monotonic()

COLUMNS = (
    "deployment_id", "uptime_s",
    "rss_bytes", "rss_anon_bytes", "rss_file_bytes", "rss_peak_bytes",
    "heap_system_bytes", "heap_free_bytes", "heap_mmap_bytes",
    "gc_objects", "threads",
    "guilds", "cached_users", "cached_members", "cached_messages",
    "bg_cache_entries", "bg_cache_bytes",
    "top_types", "sample_ms",
)

INSERT_SQL = (
    f"INSERT INTO bot_memory_samples (sampled_at, {', '.join(COLUMNS)}) "
    f"VALUES (NOW(), {', '.join(['%s'] * len(COLUMNS))}) "
    "ON CONFLICT DO NOTHING"
)
PRUNE_SQL = "DELETE FROM bot_memory_samples WHERE sampled_at < NOW() - make_interval(days => %s)"


def collect(discord_sizes: dict) -> dict:
    """Everything except the discord caches, which the caller reads on the loop."""
    started = time.perf_counter()
    row = {
        "deployment_id": os.getenv("RAILWAY_DEPLOYMENT_ID") or "local",
        "uptime_s": int(time.monotonic() - _STARTED),
        "threads": threading.active_count(),
    }
    row.update(memory_stats.read_proc_status())
    # malloc_info goes through ctypes into libc, where a mistake is a segfault
    # rather than an exception, and it has not yet run on the Railway image.
    # Opt in with MEMORY_SAMPLER_MALLOC_INFO=1; unset it again if the bot dies.
    if os.getenv("MEMORY_SAMPLER_MALLOC_INFO") == "1":
        row.update(memory_stats.read_malloc_info())
    gc_objects, top_types = memory_stats.count_gc_types()
    row["gc_objects"] = gc_objects
    row["top_types"] = Json(top_types)
    row.update(memory_stats.background_cache_size())
    row.update(discord_sizes)
    row["sample_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
    return row


def _sample_sync(discord_sizes: dict) -> None:
    row = collect(discord_sizes)
    db = DB()
    db.connect()
    try:
        db.cursor.execute(INSERT_SQL, tuple(row.get(c) for c in COLUMNS))
        db.cursor.execute(PRUNE_SQL, (RETENTION_DAYS,))
        db.connection.commit()
    finally:
        db.close()


class MemorySampler(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.memory_sampler.start()

    def cog_unload(self):
        self.memory_sampler.cancel()

    @tasks.loop(minutes=INTERVAL_MINUTES)
    async def memory_sampler(self):
        try:
            sizes = memory_stats.discord_cache_sizes(self.client)
            await asyncio.to_thread(_sample_sync, sizes)
        except Exception as e:
            log(ERROR, f"Memory sample failed: {e}", context="memory_sampler")

    @memory_sampler.before_loop
    async def before_memory_sampler(self):
        await self.client.wait_until_ready()


def setup(client):
    client.add_cog(MemorySampler(client))
