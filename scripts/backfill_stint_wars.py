"""One-off backfill: war baselines on membership_stints (TAQ-133).

Fills, from the bot's own samples (member_counters_hourly, player_activity):

- wars_on_join wherever it is missing -- the live stints opened since
  2026-09-17, which only got one if the member registered in Discord -- with
  the first sample at or after the stint opened;
- wars_on_leave on every closed stint (the column is new) with the last
  sample before it closed.

Existing values are never overwritten. Run after the schema.sql ALTER.

Dry-run by default: the updates run inside a transaction that is rolled
back. Nothing is written without --apply. Writes go to the database the
.env's DB_* variables point at (local dev unless run under prodctx).

    venv/Scripts/python scripts/backfill_stint_wars.py            # report only
    venv/Scripts/python scripts/backfill_stint_wars.py --apply    # write
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers.database import DB
from Helpers import roster


def _missing(cur):
    cur.execute(
        """SELECT count(*) FILTER (WHERE wars_on_join IS NULL),
                  count(*) FILTER (WHERE left_at IS NOT NULL AND wars_on_leave IS NULL)
             FROM membership_stints"""
    )
    return cur.fetchone()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="commit the updates (default: dry-run report)")
    args = parser.parse_args()

    db = DB()
    db.connect()
    try:
        cur = db.cursor
        no_join, no_leave = _missing(cur)
        print(f"Before: {no_join} stint(s) without wars_on_join, {no_leave} closed without wars_on_leave")

        joins = roster.fill_join_baselines(cur)
        leaves = roster.fill_leave_counts(cur)
        print(f"Filled: wars_on_join on {joins}, wars_on_leave on {leaves}")

        cur.execute(
            """SELECT gr.ign, ms.joined_at, ms.wars_on_join
                 FROM membership_stints ms LEFT JOIN guild_roster gr ON gr.uuid = ms.uuid
                WHERE ms.left_at IS NULL AND ms.source = 'live'
                ORDER BY ms.joined_at"""
        )
        for ign, joined_at, wars in cur.fetchall():
            print(f"  open live stint  {ign or '?':<18} joined {joined_at:%Y-%m-%d %H:%M}  wars_on_join={wars}")

        no_join, no_leave = _missing(cur)
        print(f"After:  {no_join} stint(s) without wars_on_join, {no_leave} closed without wars_on_leave"
              " (no sample in range; they stay uncounted)")

        if args.apply:
            db.connection.commit()
            print("\nCommitted.")
        else:
            db.connection.rollback()
            print("\nDry run, rolled back. Re-run with --apply to write.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
