"""
Tests for the memory-makeup readers (Helpers/memory_stats.py) and the sampler
row they feed (Tasks/memory_sampler.py), TAQ-105.
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import memory_stats
from Tasks import memory_sampler

PROC_STATUS = """Name:\tpython
VmPeak:\t 1200000 kB
VmHWM:\t  900000 kB
VmRSS:\t  180000 kB
RssAnon:\t  150000 kB
RssFile:\t   30000 kB
RssShmem:\t       0 kB
Threads:\t12
"""

# Trimmed malloc_info output: two arenas, then the process-wide totals.
MALLOC_INFO = """<malloc version="1">
<heap nr="0">
<sizes></sizes>
<total type="fast" count="3" size="100"/>
<total type="rest" count="5" size="2000"/>
<system type="current" size="50000"/>
<system type="max" size="60000"/>
<aspace type="total" size="50000"/>
<aspace type="mprotect" size="50000"/>
</heap>
<heap nr="1">
<sizes></sizes>
<total type="fast" count="1" size="10"/>
<total type="rest" count="2" size="300"/>
<system type="current" size="7000"/>
<system type="max" size="8000"/>
<aspace type="total" size="7000"/>
<aspace type="mprotect" size="7000"/>
</heap>
<total type="fast" count="4" size="110"/>
<total type="rest" count="7" size="2300"/>
<total type="mmap" count="2" size="4096000"/>
<system type="current" size="57000"/>
<system type="max" size="68000"/>
<aspace type="total" size="57000"/>
<aspace type="mprotect" size="57000"/>
</malloc>
"""


def test_proc_status_converts_kb_to_bytes():
    assert memory_stats.parse_proc_status(PROC_STATUS) == {
        "rss_bytes": 180000 * 1024,
        "rss_anon_bytes": 150000 * 1024,
        "rss_file_bytes": 30000 * 1024,
        "rss_peak_bytes": 900000 * 1024,
    }


def test_malloc_info_reads_process_totals_not_an_arena():
    assert memory_stats.parse_malloc_info(MALLOC_INFO) == {
        "heap_system_bytes": 57000,
        "heap_free_bytes": 110 + 2300,
        "heap_mmap_bytes": 4096000,
    }


def test_malloc_info_garbage_is_empty():
    assert memory_stats.parse_malloc_info("not xml") == {}


def test_readers_never_raise_off_linux():
    # On Windows/macOS these find no /proc or glibc and come back empty.
    assert isinstance(memory_stats.read_proc_status(), dict)
    assert isinstance(memory_stats.read_malloc_info(), dict)


class Widget:
    pass


def test_gc_type_counts_rank_and_qualify_names():
    objs = [{}, {}, {}, [], Widget(), Widget()]
    total, top = memory_stats.count_gc_types(objs, top=2)
    assert total == 6
    assert top == {"dict": 3, f"{__name__}.Widget": 2}


def test_discord_cache_sizes():
    client = SimpleNamespace(
        guilds=[SimpleNamespace(members=[1, 2, 3]), SimpleNamespace(members=[4])],
        users=[1, 2, 3, 4, 5],
        cached_messages=[1, 2],
    )
    assert memory_stats.discord_cache_sizes(client) == {
        "guilds": 2, "cached_users": 5, "cached_members": 4, "cached_messages": 2,
    }


def test_discord_cache_sizes_survives_a_broken_client():
    assert memory_stats.discord_cache_sizes(object()) == {}


def test_insert_has_one_placeholder_per_column():
    assert memory_sampler.INSERT_SQL.count("%s") == len(memory_sampler.COLUMNS)


def test_collected_row_only_uses_known_columns():
    row = memory_sampler.collect({"guilds": 1})
    assert set(row) <= set(memory_sampler.COLUMNS)
    assert row["deployment_id"]
    assert row["gc_objects"] > 0
    assert row["guilds"] == 1
