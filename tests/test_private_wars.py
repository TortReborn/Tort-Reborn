"""Private war counts are unknown, not 0 (TAQ-134).

The API returns no war count while a player hides it. Stored as 0 it reads as
a real count, and every delta built on it credits the player's whole lifetime
the moment the profile goes public. The writers now store NULL; these check
that each reader skips a NULL reading instead of using it as a baseline.
"""

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import database, roster
from Helpers.playtime_daily import build_rows

U1 = '065fc385-f9c1-4e7f-96b3-8674a65c509f'
UTC = datetime.timezone.utc


def _daily(cur, day, wars):
    cur.execute(
        "INSERT INTO player_activity (uuid, playtime, wars, snapshot_date) VALUES (%s::uuid, 10, %s, %s)",
        (U1, wars, day),
    )


def test_join_baseline_skips_private_readings(temp_db):
    cur = temp_db.cursor
    joined = datetime.datetime(2026, 11, 1, 10, 30, tzinfo=UTC)
    for hour, wars in ((10, None), (11, None), (12, 2485)):
        cur.execute(
            "INSERT INTO member_counters_hourly (uuid, hour, playtime, wars, raids) VALUES (%s::uuid, %s, 0, %s, 0)",
            (U1, joined.replace(hour=hour, minute=0), wars),
        )
    roster.open_stint(cur, U1, joined_at=joined)
    assert roster.fill_join_baselines(cur) == 1
    assert roster.latest_stint(cur, U1)['wars_on_join'] == 2485


def test_join_baseline_waits_while_private(temp_db):
    cur = temp_db.cursor
    joined = datetime.datetime(2026, 11, 1, 10, 30, tzinfo=UTC)
    cur.execute(
        "INSERT INTO member_counters_hourly (uuid, hour, playtime, wars, raids) VALUES (%s::uuid, %s, 0, NULL, 0)",
        (U1, joined.replace(minute=0)),
    )
    roster.open_stint(cur, U1, joined_at=joined)
    assert roster.fill_join_baselines(cur) == 0
    assert roster.latest_stint(cur, U1)['wars_on_join'] is None


def test_timed_baselines_skip_private_readings(temp_db):
    cur = temp_db.cursor
    today = datetime.date.today()
    _daily(cur, today - datetime.timedelta(days=10), None)
    _daily(cur, today - datetime.timedelta(days=5), 30)
    _daily(cur, today, 40)

    # Profile "Wars / N D": a NULL nearest the window start is walked past.
    base = database.get_player_activity_baselines_with_db(temp_db, U1, ['wars'], 7)
    assert base['wars'] == (30, True)
    assert database._get_baseline_from_db(temp_db, U1, 'wars', 7) == (30, True)
    # Leaderboard / vanity roles (batched).
    assert database._get_member_baselines_from_db(temp_db, 'wars', 7, {U1: None}) == {U1: (30, True)}


def test_counter_writer_stores_private_as_null(temp_db, monkeypatch):
    from Tasks import update_member_data as umd
    monkeypatch.setattr(umd, '_db_connect_with_retry', lambda: temp_db)
    cur = temp_db.cursor

    umd._write_counter_sample_sync([{'uuid': U1, 'playtime': 5.0, 'wars': None, 'raids': 0}])
    cur.execute("SELECT wars FROM member_counters_hourly WHERE uuid = %s::uuid", (U1,))
    assert cur.fetchall() == [(None,)]

    # A known reading in the same hour wins, and a later private one keeps it.
    umd._write_counter_sample_sync([{'uuid': U1, 'playtime': 5.0, 'wars': 12, 'raids': 0}])
    umd._write_counter_sample_sync([{'uuid': U1, 'playtime': 5.0, 'wars': None, 'raids': 0}])
    cur.execute("SELECT wars FROM member_counters_hourly WHERE uuid = %s::uuid", (U1,))
    assert cur.fetchall() == [(12,)]


def test_playtime_daily_does_not_credit_a_private_gap():
    d1 = datetime.date(2026, 10, 1)
    d2 = d1 + datetime.timedelta(days=1)
    rec = ('u', d2, 105.0, 2485, 0, d1, 100.0, None, 0)
    (row,) = build_rows([rec])
    assert row[2] == 5.0   # playtime still counted
    assert row[3] == 0     # wars: unknown start, so nothing credited
