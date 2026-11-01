"""Derive playtime_daily from the player_activity snapshot history.

player_activity stores a cumulative counter once a day, so the playtime spent
on a given day is the difference between that day's snapshot and the previous
one. This module owns that derivation so the nightly task and the CLI backfill
cannot drift apart on the rules.

Three things make it more than a LAG():

  * Missing snapshots. The daily task can skip a run, and a member who leaves
    and rejoins has no rows in between — spans of up to six months exist. The
    delta is spread evenly across the span and marked 'interpolated', because
    the shape inside the span is unknowable.
  * Negative deltas. Rows go backwards on uuid reuse or an API correction, and
    on 2026-04-25 Wynncraft revised ~95 members' playtime downward at once.
    Those are floored to zero and marked 'clamped' rather than subtracting
    from a member's total.
  * Impossible deltas. A day cannot hold more than 24 hours, but the counter
    sometimes says otherwise. Those are clipped and marked 'capped', so a
    truncated day is never mistaken for a merely busy one.
"""
from datetime import timedelta

from psycopg2.extras import execute_values

# A day cannot hold more than 24 hours of playtime. Two members have breached
# this on real data (32.2h and 27.5h on the same date), so the guard fires.
MAX_HOURS_PER_DAY = 24.0

SOURCE_ROWS = """
    SELECT uuid, snapshot_date, playtime, wars, raids,
           LAG(snapshot_date) OVER w AS prev_date,
           LAG(playtime)      OVER w AS prev_playtime,
           LAG(wars)          OVER w AS prev_wars,
           LAG(raids)         OVER w AS prev_raids
    FROM player_activity
    WINDOW w AS (PARTITION BY uuid ORDER BY snapshot_date)
    ORDER BY uuid, snapshot_date
"""

# VALUES %s is the execute_values template: psycopg2 substitutes one rendered
# tuple per row, so a page of rows costs a single round trip. executemany would
# send one statement per row, which over a managed endpoint means tens of
# thousands of round trips for a full backfill.
UPSERT = """
    INSERT INTO playtime_daily (uuid, day, hours, wars, raids, span_days, source)
    VALUES %s
    ON CONFLICT (uuid, day) DO UPDATE SET
        hours     = EXCLUDED.hours,
        wars      = EXCLUDED.wars,
        raids     = EXCLUDED.raids,
        span_days = EXCLUDED.span_days,
        source    = EXCLUDED.source
"""


def upsert_rows(db, rows, batch=5000):
    """Upsert derived rows, one round trip per batch. Caller commits."""
    execute_values(db.cursor, UPSERT, rows, page_size=batch)


def build_rows(records):
    """Turn consecutive snapshot pairs into per-day rows.

    Yields (uuid, day, hours, wars, raids, span_days, source) tuples. Kept free
    of database access so it can be unit tested directly.
    """
    for (uuid, day, playtime, wars, raids,
         prev_date, prev_playtime, prev_wars, prev_raids) in records:
        if prev_date is None:
            continue  # first snapshot for this member: no baseline to diff

        span = (day - prev_date).days
        if span <= 0:
            continue  # defensive: the primary key already forbids this

        delta = playtime - prev_playtime
        # NULL wars were private (TAQ-134): with either end unknown the
        # span's wars are unknown, and 0 beats crediting a lifetime count.
        war_delta = wars - prev_wars if wars is not None and prev_wars is not None else 0
        raid_delta = (raids or 0) - (prev_raids or 0)

        if delta < 0:
            source, delta, war_delta, raid_delta = "clamped", 0.0, 0, 0
        elif delta / span > MAX_HOURS_PER_DAY:
            source = "capped"
        else:
            source = "exact" if span == 1 else "interpolated"

        # Counters can also regress on their own (a war count correction with
        # a healthy playtime delta), so floor them independently.
        war_delta = max(0, war_delta)
        raid_delta = max(0, raid_delta)

        hours_per_day = min(delta / span, MAX_HOURS_PER_DAY)

        # Spread whole counters across the span without inventing or losing
        # events: the remainder lands on the final days of the span.
        for i in range(span):
            covered = prev_date + timedelta(days=1 + i)
            yield (
                str(uuid), covered, round(hours_per_day, 4),
                war_delta // span + (1 if i >= span - war_delta % span else 0),
                raid_delta // span + (1 if i >= span - raid_delta % span else 0),
                span, source,
            )


def refresh_playtime_daily(db, since_day=None, batch=5000):
    """Re-derive playtime_daily and upsert it. Returns (rows_written, days).

    Reads the whole snapshot history because a member returning after months
    away needs their pre-gap row to difference against; filtering the source by
    date would silently drop exactly those spans. The upsert makes a full pass
    idempotent, so this is safe to run nightly.

    since_day limits only what is written, not what is read.
    """
    db.cursor.execute(SOURCE_ROWS)
    rows = list(build_rows(db.cursor.fetchall()))
    if since_day is not None:
        rows = [r for r in rows if r[1] >= since_day]
    if not rows:
        return 0, 0

    upsert_rows(db, rows, batch)
    db.connection.commit()
    return len(rows), len({r[1] for r in rows})
