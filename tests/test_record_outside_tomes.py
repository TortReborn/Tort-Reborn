"""Recording tomes handed out outside the bot (scripts/record_outside_tomes.py)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import tomes  # noqa: E402
from scripts import record_outside_tomes as rec  # noqa: E402

UUID = "11111111-2222-3333-4444-555555555555"
DISCORD = 521063313962958860

GIVE = {"ign": "ThomasMann", "kind": "raid", "count": 5, "priority": "very_low",
        "requested_at": "2026-11-19T19:45:00Z", "given_at": "2026-11-19T22:49:00Z", "given_by": "Kenji121"}
DEBT = {"ign": "ThomasMann", "count": 5, "at": "2026-11-21T18:06:51Z", "by": "Exec",
        "note": "the same 5 tomes were given twice"}


def _member(db, shells: int, raid_received: int = 0):
    c = db.cursor
    c.execute("INSERT INTO discord_links (discord_id, ign, uuid) VALUES (%s, 'ThomasMann', %s)", (DISCORD, UUID))
    c.execute('INSERT INTO shells ("user", shells, balance, ign) VALUES (%s, %s, %s, %s)',
              (str(DISCORD), shells, shells, "ThomasMann"))
    if raid_received:
        c.execute("INSERT INTO tome_events (uuid, action, kind, count) VALUES (%s, 'given', 'raid', %s)",
                  (UUID, raid_received))


def _balance(db) -> int:
    db.cursor.execute('SELECT balance FROM shells WHERE "user" = %s', (str(DISCORD),))
    return db.cursor.fetchone()[0]


def test_a_shell_give_is_charged_at_the_members_next_price_and_counts_as_received(temp_db):
    _member(temp_db, shells=1631, raid_received=4)
    steps, problems = rec.plan(temp_db.cursor, {"gives": [GIVE]})
    assert problems == [] and steps[0]["shells"] == 100  # shell tomes 5 to 9 are 20 each

    rec.write(temp_db, steps)

    assert _balance(temp_db) == 1531
    s = tomes.standing(temp_db.cursor, UUID, DISCORD, None)
    assert (s.raid_received, s.raid_queued) == (9, 0)
    temp_db.cursor.execute("SELECT count(*) FROM tome_events WHERE announced_at IS NULL")
    assert temp_db.cursor.fetchone()[0] == 1  # only the seeded event; nothing new reaches the tome log


def test_running_it_twice_writes_nothing_the_second_time(temp_db):
    _member(temp_db, shells=1631, raid_received=4)
    rec.write(temp_db, rec.plan(temp_db.cursor, {"gives": [GIVE], "debts": [DEBT]})[0])

    steps, problems = rec.plan(temp_db.cursor, {"gives": [GIVE], "debts": [DEBT]})
    assert problems == [] and all("skip" in s for s in steps)
    rec.write(temp_db, steps)
    assert _balance(temp_db) == 1531


def test_debt_counts_as_war_tomes_received(temp_db):
    _member(temp_db, shells=0)
    rec.write(temp_db, rec.plan(temp_db.cursor, {"debts": [DEBT]})[0])
    assert tomes.standing(temp_db.cursor, UUID, DISCORD, None).war_received == 5


def test_a_give_they_cant_afford_or_an_unknown_name_is_a_problem_not_a_write(temp_db):
    _member(temp_db, shells=50, raid_received=4)
    steps, problems = rec.plan(temp_db.cursor, {"gives": [GIVE, {**GIVE, "ign": "Nobody"}]})
    assert steps == [] and len(problems) == 2
