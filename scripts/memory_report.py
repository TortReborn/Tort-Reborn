"""
scripts/memory_report.py
Read back bot_memory_samples (TAQ-105): what the bot's memory is made of, per run.

    prodctx python Tort-Reborn/scripts/memory_report.py              # last 24h, prod
    prodctx python Tort-Reborn/scripts/memory_report.py --hours 72
    prodctx python Tort-Reborn/scripts/memory_report.py --run <deployment id prefix>
    python scripts/memory_report.py                                  # dev DB

For each run in the window it prints the memory curve (one line per hour of
uptime), then compares the run's first and latest sample by object type, which
is where growth shows up. For one-off questions, query the table directly.
"""

import argparse
import os
import sys
from datetime import timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))  # never overrides prodctx

from Helpers.database import DB

MB = 1024 * 1024


def mb(n):
    return "     -" if n is None else f"{n / MB:6.0f}"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hours", type=int, default=24, help="window to report (default 24)")
    p.add_argument("--run", help="only the run whose deployment id starts with this")
    p.add_argument("--top", type=int, default=12, help="object types to compare (default 12)")
    args = p.parse_args()

    db = DB(use_pool=False)
    db.connect()
    try:
        db.cursor.execute(
            """SELECT deployment_id, MIN(sampled_at), MAX(sampled_at), COUNT(*)
                 FROM bot_memory_samples
                WHERE sampled_at > NOW() - make_interval(hours => %s)
                  AND deployment_id LIKE %s
                GROUP BY deployment_id
                ORDER BY MIN(sampled_at)""",
            (args.hours, (args.run or "") + "%"),
        )
        runs = db.cursor.fetchall()
        if not runs:
            print(f"No samples in the last {args.hours}h.")
            return

        for deployment_id, first_at, last_at, n in runs:
            print(f"\n=== run {deployment_id[:8]}  {first_at.astimezone(timezone.utc):%Y-%m-%d %H:%M} → "
                  f"{last_at.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC  ({n} samples)")
            print("  uptime     rss   anon   file  heapOS  heapFree  mmap   gc_objs  members  users  msgs")
            # The first sample, then the first sample of each later hour of uptime.
            db.cursor.execute(
                """SELECT DISTINCT ON (uptime_s / 3600)
                          uptime_s, rss_bytes, rss_anon_bytes, rss_file_bytes,
                          heap_system_bytes, heap_free_bytes, heap_mmap_bytes,
                          gc_objects, cached_members, cached_users, cached_messages
                     FROM bot_memory_samples
                    WHERE deployment_id = %s
                    ORDER BY uptime_s / 3600, uptime_s""",
                (deployment_id,),
            )
            for (up, rss, anon, file_, hsys, hfree, hmmap,
                 gco, members, users, msgs) in db.cursor.fetchall():
                print(f"  {up // 3600:3d}h{up % 3600 // 60:02d}m {mb(rss)} {mb(anon)} {mb(file_)}"
                      f"  {mb(hsys)}    {mb(hfree)} {mb(hmmap)}  {gco or 0:8d}  {members or 0:7d}"
                      f"  {users or 0:5d}  {msgs or 0:4d}")

            db.cursor.execute(
                """(SELECT top_types FROM bot_memory_samples WHERE deployment_id = %s
                     ORDER BY sampled_at ASC LIMIT 1)
                   UNION ALL
                   (SELECT top_types FROM bot_memory_samples WHERE deployment_id = %s
                     ORDER BY sampled_at DESC LIMIT 1)""",
                (deployment_id, deployment_id),
            )
            rows = db.cursor.fetchall()
            first, latest = (rows[0][0] or {}), (rows[-1][0] or {})
            print("\n  objects by type    first → latest   (change)")
            for name, count in sorted(latest.items(), key=lambda kv: -kv[1])[:args.top]:
                before = first.get(name)
                change = "new" if before is None else f"{count - before:+d}"
                print(f"  {name[:40]:40s} {before if before is not None else '-':>8} → {count:<8d} ({change})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
