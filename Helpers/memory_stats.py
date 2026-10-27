"""
Helpers/memory_stats.py
What the bot's resident memory is made of, for Tasks/memory_sampler.py (TAQ-105).

Railway only reports the container total. These readers split it three ways:

- the kernel's view (/proc/self/status): resident total, anonymous memory
  (the heap: everything the bot allocated) vs file-backed (loaded code and
  shared libraries), and the run's peak;
- glibc's view (malloc_info): what the allocator has taken from the OS, and
  how much of that is freed-but-not-returned. That gap is the fragmentation
  MALLOC_ARENA_MAX=2 targets (TAQ-104);
- Python's view (gc): live container objects by type.

gc only tracks containers (dicts, lists, class instances), never str, bytes or
int, and nothing here sees native buffers such as Pillow pixel data or SSL
state. Those show up only as anonymous memory the other two views do not
explain.

Every reader returns None (or an empty dict) off Linux or on any failure: a
sample with holes beats a sampler that raises into the bot.
"""

import ctypes
import gc
import re
from collections import Counter

_STATUS_FIELDS = {
    "VmRSS": "rss_bytes",
    "RssAnon": "rss_anon_bytes",
    "RssFile": "rss_file_bytes",
    "VmHWM": "rss_peak_bytes",
}


def parse_proc_status(text: str) -> dict:
    """Pull the resident-memory fields (reported in kB) out of /proc/self/status."""
    out = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        key = _STATUS_FIELDS.get(name)
        if key:
            parts = rest.split()
            if parts and parts[0].isdigit():
                out[key] = int(parts[0]) * 1024
    return out


def read_proc_status() -> dict:
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            return parse_proc_status(f.read())
    except Exception:
        return {}


_TOTAL_RE = re.compile(r'<total type="(fast|rest|mmap)" count="\d+" size="(\d+)"/>')
_SYSTEM_RE = re.compile(r'<system type="current" size="(\d+)"/>')


def parse_malloc_info(xml: str) -> dict:
    """Process-wide totals from glibc's malloc_info XML.

    Each arena's <heap> block repeats the same tags, so only the tail after the
    last </heap> is read: that is where glibc writes the sums across arenas.
    heap_system_bytes is what the arenas hold from the OS; heap_free_bytes is
    the part of it sitting free (fast + rest bins); heap_mmap_bytes is large
    allocations mapped separately, outside the arenas.
    """
    cut = xml.rfind("</heap>")
    tail = xml[cut:] if cut != -1 else xml
    sizes = {kind: int(size) for kind, size in _TOTAL_RE.findall(tail)}
    system = _SYSTEM_RE.search(tail)
    if system is None:
        return {}
    return {
        "heap_system_bytes": int(system.group(1)),
        "heap_free_bytes": sizes.get("fast", 0) + sizes.get("rest", 0),
        "heap_mmap_bytes": sizes.get("mmap", 0),
    }


def read_malloc_info() -> dict:
    """malloc_info(0, fp) into an in-memory stream, then parse it. glibc only."""
    try:
        libc = ctypes.CDLL("libc.so.6")
        libc.open_memstream.restype = ctypes.c_void_p
        libc.open_memstream.argtypes = [ctypes.POINTER(ctypes.c_void_p),
                                        ctypes.POINTER(ctypes.c_size_t)]
        libc.malloc_info.argtypes = [ctypes.c_int, ctypes.c_void_p]
        libc.fclose.argtypes = [ctypes.c_void_p]
        libc.free.argtypes = [ctypes.c_void_p]

        buf = ctypes.c_void_p()
        size = ctypes.c_size_t()
        fp = libc.open_memstream(ctypes.byref(buf), ctypes.byref(size))
        if not fp:
            return {}
        try:
            libc.malloc_info(0, fp)
        finally:
            libc.fclose(fp)  # flushes; buf and size are valid only after this
        try:
            xml = ctypes.string_at(buf, size.value).decode("utf-8", "replace")
        finally:
            libc.free(buf)
        return parse_malloc_info(xml)
    except Exception:
        return {}


def _type_name(obj) -> str:
    t = type(obj)
    module = getattr(t, "__module__", "")
    name = getattr(t, "__qualname__", t.__name__)
    return name if module in ("builtins", "") else f"{module}.{name}"


def count_gc_types(objects=None, top: int = 25) -> tuple[int, dict]:
    """(total gc-tracked objects, {type name: count} for the `top` most common)."""
    try:
        if objects is None:
            objects = gc.get_objects()
        counts = Counter(_type_name(o) for o in objects)
        return len(objects), dict(counts.most_common(top))
    except Exception:
        return 0, {}


def discord_cache_sizes(client) -> dict:
    """Sizes of the discord client's own caches. Call on the event-loop thread:
    these dicts mutate under the gateway, and iterating them from a worker
    thread can raise mid-change."""
    try:
        guilds = list(client.guilds)
        return {
            "guilds": len(guilds),
            "cached_users": len(client.users),
            "cached_members": sum(len(g.members) for g in guilds),
            "cached_messages": len(client.cached_messages),
        }
    except Exception:
        return {}


def background_cache_size() -> dict:
    try:
        from Helpers.storage import _bg_cache
        values = list(_bg_cache.values())
        return {"bg_cache_entries": len(values),
                "bg_cache_bytes": sum(len(v) for v in values)}
    except Exception:
        return {}
