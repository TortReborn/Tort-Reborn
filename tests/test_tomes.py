"""Tome requests (TAQ-116, Helpers/tomes.py).

What matters here:

1. The rules match the old pinned post: war milestones 10/25/50/100 then
   every 50 more lifetime wars; raid tomes 10 shells for the first four, 20
   after, counting raid tomes only.
2. A request can't take more than the member has earned or can afford, and
   counts what they have received, what is queued and any debt.
3. A raid request takes the shells in the same transaction, recorded as a
   shell transaction; past 100 queued tomes the rest waits in 'give when
   available'.
"""

import pytest

from Helpers import tomes

UUID = "11111111-2222-3333-4444-555555555555"
DISCORD = 170719819715313665


@pytest.mark.parametrize("wars, earned", [
    (0, 0), (9, 0), (10, 1), (24, 1), (25, 2), (50, 3), (99, 3), (100, 4),
    (149, 4), (150, 5), (3300, 68), (1050, 23), (17800, 358),
])
def test_war_milestones(wars, earned):
    assert tomes.war_tomes_earned(wars) == earned


def test_wars_needed_is_the_inverse_of_earned():
    for k in range(1, 400):
        need = tomes.wars_needed(k)
        assert tomes.war_tomes_earned(need) == k and tomes.war_tomes_earned(need - 1) == k - 1


def test_raid_prices_count_raid_tomes_only():
    assert [tomes.raid_tome_price(k) for k in range(1, 7)] == [10, 10, 10, 10, 20, 20]
    assert tomes.raid_tomes_cost(0, 4) == 40
    assert tomes.raid_tomes_cost(3, 2) == 30  # the 4th at 10, the 5th at 20
    assert tomes.raid_tomes_cost(10, 1) == 20


def test_standing_maths():
    s = tomes.Standing(wars=160, war_received=3, war_queued=1, raid_received=3, raid_queued=0, shells=45, queued_total=1)
    assert s.war_earned == 5 and s.war_available == 1
    assert s.next_war_tome_at == 200
    assert s.next_raid_price == 10
    assert s.raid_affordable == 2  # the 4th at 10 and the 5th at 20; a 6th would make 50
    debt = tomes.Standing(wars=60, war_received=5, war_queued=0, raid_received=0, raid_queued=0, shells=0, queued_total=0)
    assert debt.war_available == 0 and debt.next_war_tome_at == 200  # 5 received, so the 6th milestone


def _shells(db, balance):
    db.cursor.execute('INSERT INTO shells ("user", shells, balance, ign) VALUES (%s, %s, %s, %s)',
                      (str(DISCORD), balance, balance, "Thundderr"))


def _ask(db, kind, count, wars=None, priority="medium"):
    return tomes.request_tomes(db, uuid=UUID, discord_id=DISCORD, ign="Thundderr",
                               kind=kind, count=count, priority=priority, wars=wars)


def test_war_request_respects_what_is_earned_received_and_queued(temp_db):
    temp_db.cursor.execute(
        "INSERT INTO tome_events (uuid, action, kind, count) VALUES (%s, 'given', 'war', 2), (%s, 'debt', 'war', 1)",
        (UUID, UUID))
    _shells(temp_db, 0)
    with pytest.raises(tomes.TomeError, match="You can request 1 war tome"):
        _ask(temp_db, "war", 2, wars=100)  # 4 earned - 3 received
    [made] = _ask(temp_db, "war", 1, wars=100)
    assert made["bucket"] == "queue" and made["shells"] == 0
    with pytest.raises(tomes.TomeError, match="You can request 0"):
        _ask(temp_db, "war", 1, wars=100)
    with pytest.raises(tomes.TomeError, match="hidden"):
        _ask(temp_db, "war", 1, wars=None)


def test_raid_request_takes_the_shells_and_records_them(temp_db):
    _shells(temp_db, 60)
    temp_db.cursor.execute("INSERT INTO tome_events (uuid, action, kind, count) VALUES (%s, 'given', 'raid', 3)", (UUID,))
    with pytest.raises(tomes.TomeError, match="cost 70 shells and you have 60"):
        _ask(temp_db, "raid", 4)  # 10 + 20 + 20 + 20
    [made] = _ask(temp_db, "raid", 3)  # 10 + 20 + 20
    assert made["shells"] == 50
    temp_db.cursor.execute('SELECT balance FROM shells WHERE "user" = %s', (str(DISCORD),))
    assert temp_db.cursor.fetchone()[0] == 10
    temp_db.cursor.execute("SELECT delta_balance, source FROM shell_transactions")
    assert temp_db.cursor.fetchall() == [(-50, "tome_request")]
    temp_db.cursor.execute("SELECT action, kind, count FROM tome_events WHERE action = 'requested'")
    assert temp_db.cursor.fetchall() == [("requested", "raid", 3)]
    temp_db.cursor.execute("SELECT log_type FROM audit_log")
    assert temp_db.cursor.fetchall() == [("tome",)]


def test_past_the_queue_limit_waits_for_when_available(temp_db):
    _shells(temp_db, 0)
    wars = tomes.wars_needed(125)
    made = _ask(temp_db, "war", 120, wars=wars)
    assert [(m["bucket"], m["count"]) for m in made] == [("queue", 100), ("give_when_available", 20)]
    [more] = _ask(temp_db, "war", 5, wars=wars)
    assert (more["bucket"], more["count"]) == ("give_when_available", 5)


def test_bad_input_is_refused(temp_db):
    _shells(temp_db, 100)
    for kind, count, priority, msg in (("aspect", 1, "low", "war tomes or raid"), ("war", 0, "low", "at least one"),
                                       ("raid", 1, "urgent", "priority")):
        with pytest.raises(tomes.TomeError, match=msg):
            _ask(temp_db, kind, count, wars=500, priority=priority)
