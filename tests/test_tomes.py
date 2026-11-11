"""Tome requests (TAQ-116, Helpers/tomes.py).

What matters here:

1. The rules match the old pinned post: war milestones 10/25/50/100 then
   every 50 more TAq wars; raid tomes 10 shells for the first four, 20
   after, counting raid tomes only.
2. A request can't take more than the member has earned or can afford, and
   counts what they have received, what is queued and any debt.
3. A raid request takes the shells in the same transaction, recorded as a
   shell transaction; however many are asked for, they all join the queue.
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


def test_log_posts_keep_a_give_and_its_screenshots_together():
    from Commands.tomes import LINES_PER_POST, log_posts as grant_posts
    row = lambda i, keys: (i, "given", 1, "war", "Exec", f"M{i}", None, keys, "low", None, 0)
    big = [row(i, ["a.png"]) for i in range(LINES_PER_POST + 1)]
    plain = [row(100, []), row(101, [])]
    other = [row(200, ["b.png", "c.png"])]
    posts = grant_posts(big + plain + other)
    assert [(len(ids), keys) for ids, _, keys in posts] == [
        (LINES_PER_POST, ["a.png"]), (1, []), (2, []), (1, ["b.png", "c.png"])]
    assert sorted(i for ids, _, _ in posts for i in ids) == [r[0] for r in big + plain + other]


def test_log_lines_ping_the_member():
    from Commands.tomes import log_line
    row = lambda action, n, kind, shells, note: (1, action, n, kind, "Exec", "Mem", DISCORD, [], "medium", note, shells)
    assert log_line(row("requested", 3, "raid", 30, None)) == \
        f"\U0001F4DD <@{DISCORD}> (Mem) requested **3 shell tomes** at Medium priority (30 shells)."
    assert log_line(row("requested", 1, "war", 0, None)) == \
        f"\U0001F4DD <@{DISCORD}> (Mem) requested **1 war tome** at Medium priority."
    assert log_line(row("given", 1, "raid", 0, None)) == f"\U0001F4D8 <@{DISCORD}> (Mem) was given **1 shell tome** by Exec."
    assert log_line(row("cancelled", 2, "raid", 20, "by mistake")) == \
        f"\u274C <@{DISCORD}> (Mem): request for **2 shell tomes** was cancelled by Exec, 20 shells refunded. Reason: by mistake"
    assert log_line(row("cancelled", 2, "war", 0, None)) == \
        f"\u274C <@{DISCORD}> (Mem): request for **2 war tomes** was cancelled by Exec."


# The same table as queueText in TAq-Website/lib/tomes.test.ts: the two must agree,
# but for the preview writing a mention as @IGN.
QUEUE_ROWS = [
    ("Ann_1", 3, "very_high", "queue", 101),
    ("Bob", 2, "very_high", "queue", 102),
    ("Bob", 1, "very_high", "queue", 102),
    ("Cy", 5, "high", "queue", None),
    ("Dee", 4, "low", "give_when_available", 104),
    ("Eve", 20, "high", "give_when_full", 105),
]
QUEUE_TEXT = """\u2764\ufe0f **Very High**
<@101>: 3
<@102>: 3

\U0001F9E1 **High**
`Cy`: 5

**Give when available**
<@104>: 4

**Give when full**
<@105>: 20"""


def test_queue_text_reads_in_serving_order_with_one_line_a_member():
    assert tomes.queue_text(QUEUE_ROWS) == QUEUE_TEXT
    assert tomes.queue_text([]) == "Nothing is waiting."


def test_queue_text_counts_what_would_not_fit():
    rows = [(f"Member{i:04}", 2, "low", "queue", None) for i in range(400)]
    text = tomes.queue_text(rows)
    assert len(text) <= tomes.QUEUE_TEXT_LIMIT
    shown = text.count("`") // 2
    assert text.endswith(f"\u2026and {400 - shown} more members ({(400 - shown) * 2} tomes)")
    assert text.startswith("\U0001F49A **Low**")


def test_open_queue_serves_the_order_a_give_does_and_skips_who_a_give_skips(temp_db):
    c = temp_db.cursor
    other = "11111111-2222-3333-4444-666666666666"
    c.execute("INSERT INTO guild_roster (uuid, ign, in_game_rank) VALUES (%s, 'x', 'recruit')", (UUID,))
    for ign, uuid, priority, bucket, status, count, given, age in (
        ("late", UUID, "low", "give_when_full", "open", 1, 0, 1),
        ("low", UUID, "low", "queue", "open", 1, 0, 2),
        ("newer", UUID, "high", "queue", "open", 3, 1, 3),
        ("older", UUID, "high", "queue", "open", 1, 0, 4),
        ("sleeping", UUID, "high", "dormant", "open", 1, 0, 5),
        ("left", other, "high", "queue", "open", 1, 0, 6),
        ("done", UUID, "high", "queue", "done", 1, 1, 7),
    ):
        c.execute(
            """INSERT INTO tome_requests (uuid, ign, kind, count, given, priority, bucket, status, created_at)
               VALUES (%s, %s, 'war', %s, %s, %s, %s, %s, NOW() - %s * INTERVAL '1 hour')""",
            (uuid, ign, count, given, priority, bucket, status, age))
    assert [(r[0], r[1]) for r in tomes.open_queue(c)] == [("older", 1), ("newer", 2), ("low", 1), ("late", 1)]


def _live_job(rev=1, ids=(5,)):
    from Tasks.publish_bot_panels import PanelJob
    return PanelJob(rev_id=9, rev_number=rev, panel_id=1, slug="tomes", content={"messages": []},
                    channel_id=7, adopt_message_ids=[], panel_channel_id=7, panel_message_ids=list(ids))


def test_a_live_panel_is_redrawn_only_when_its_value_changes(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from Tasks import publish_bot_panels as pub

    shown = {"tome_queue": "a"}
    monkeypatch.setattr(pub, "_live_panels", lambda: [_live_job()])
    monkeypatch.setattr(pub, "load_panel_values", lambda: dict(shown))
    saved = []
    monkeypatch.setattr(pub, "_save_live_messages", lambda *a: saved.append(a))
    cog = pub.PublishBotPanels(None)
    cog._publish = AsyncMock(return_value=(7, [5], []))

    asyncio.run(cog._refresh_live())
    asyncio.run(cog._refresh_live())
    assert cog._publish.await_count == 1 and saved == []

    shown["tome_queue"] = "b"
    cog._publish = AsyncMock(return_value=(7, [6], []))  # a message was deleted, so it posted afresh
    asyncio.run(cog._refresh_live())
    assert cog._publish.await_count == 1 and saved == [(1, 7, [6])]


def test_a_failed_redraw_is_tried_again_and_logged_once(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from Tasks import publish_bot_panels as pub

    monkeypatch.setattr(pub, "_live_panels", lambda: [_live_job()])
    monkeypatch.setattr(pub, "load_panel_values", lambda: {"tome_queue": "a"})
    logged = []
    monkeypatch.setattr(pub, "log", lambda *a, **k: logged.append(a))
    cog = pub.PublishBotPanels(None)
    cog._publish = AsyncMock(side_effect=pub.PanelError("nope"))
    asyncio.run(cog._refresh_live())
    asyncio.run(cog._refresh_live())
    assert cog._publish.await_count == 2 and len(logged) == 1

    cog._publish = AsyncMock(return_value=(7, [5], []))
    asyncio.run(cog._refresh_live())
    asyncio.run(cog._refresh_live())
    assert cog._publish.await_count == 1 and cog._live_errors == {}


def test_picked_priority_stays_showing_in_the_dropdown():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from Commands.tomes import ConfirmView, _Request

    async def pick():
        req = _Request(UUID, 1, "x", 500, None)
        req.kind, req.count = "war", 1
        view = ConfirmView(req)
        interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        select = SimpleNamespace(values=["medium"], options=view.priority.options)
        await ConfirmView.priority(view, select, interaction)
        await ConfirmView.priority(view, SimpleNamespace(values=["high"], options=view.priority.options), interaction)
        return [o.value for o in view.priority.options if o.default], view.confirm.disabled

    assert asyncio.run(pick()) == (["high"], False)


def test_shell_tomes_are_called_that_where_members_see_them():
    from Commands.tomes import _Request, grant_line
    assert "2 shell tomes" in grant_line(2, "raid", "x", None, None)
    req = _Request(UUID, 1, "x", 500, tomes.Standing(500, 0, 0, 0, 0, 100))
    req.kind, req.count = "raid", 1
    assert req.cost_line() == "**1 shell tome** for **10 shells**"
    assert req.queued_line().startswith("\u2705 Queued **1 shell tome** for **10 shells**. When you are given")
    req.kind, req.count = "war", 3
    assert req.queued_line() == ("\u2705 Queued **3 war tomes**. When you are given the tomes in-game, you will be pinged "
                                 "in the tome log channel. Claim them from the guild rewards menu.")


def test_raid_prices_count_raid_tomes_only():
    assert [tomes.raid_tome_price(k) for k in range(1, 7)] == [10, 10, 10, 10, 20, 20]
    assert tomes.raid_tomes_cost(0, 4) == 40
    assert tomes.raid_tomes_cost(3, 2) == 30  # the 4th at 10, the 5th at 20
    assert tomes.raid_tomes_cost(10, 1) == 20


def test_standing_maths():
    s = tomes.Standing(wars=160, war_received=3, war_queued=1, raid_received=3, raid_queued=0, shells=45)
    assert s.war_earned == 5 and s.war_available == 1
    assert s.next_war_tome_at == 200
    assert s.next_raid_price == 10
    assert s.raid_affordable == 2  # the 4th at 10 and the 5th at 20; a 6th would make 50
    debt = tomes.Standing(wars=60, war_received=5, war_queued=0, raid_received=0, raid_queued=0, shells=0)
    assert debt.war_available == 0 and debt.next_war_tome_at == 200  # 5 received, so the 6th milestone


def _shells(db, balance):
    db.cursor.execute('INSERT INTO shells ("user", shells, balance, ign) VALUES (%s, %s, %s, %s)',
                      (str(DISCORD), balance, balance, "Thundderr"))


def _in_guild(db, uuid=UUID):
    db.cursor.execute("INSERT INTO guild_roster (uuid, ign, in_game_rank) VALUES (%s, 'x', 'recruit') ON CONFLICT DO NOTHING", (uuid,))


def _ask(db, kind, count, wars=None, priority="medium"):
    _in_guild(db)
    return tomes.request_tomes(db, uuid=UUID, discord_id=DISCORD, ign="Thundderr",
                               kind=kind, count=count, priority=priority, wars=wars)


def test_war_request_respects_what_is_earned_received_and_queued(temp_db):
    temp_db.cursor.execute(
        "INSERT INTO tome_events (uuid, action, kind, count) VALUES (%s, 'given', 'war', 2), (%s, 'debt', 'war', 1)",
        (UUID, UUID))
    _shells(temp_db, 0)
    with pytest.raises(tomes.TomeError, match="You can request 1 war tome"):
        _ask(temp_db, "war", 2, wars=100)  # 4 earned - 3 received
    made = _ask(temp_db, "war", 1, wars=100)
    assert made["shells"] == 0
    with pytest.raises(tomes.TomeError, match="You can request 0"):
        _ask(temp_db, "war", 1, wars=100)
    with pytest.raises(tomes.TomeError, match="hidden"):
        _ask(temp_db, "war", 1, wars=None)


def test_raid_request_takes_the_shells_and_records_them(temp_db):
    _shells(temp_db, 60)
    temp_db.cursor.execute("INSERT INTO tome_events (uuid, action, kind, count) VALUES (%s, 'given', 'raid', 3)", (UUID,))
    with pytest.raises(tomes.TomeError, match="cost 70 shells and you have 60"):
        _ask(temp_db, "raid", 4)  # 10 + 20 + 20 + 20
    made = _ask(temp_db, "raid", 3)  # 10 + 20 + 20
    assert made["shells"] == 50
    temp_db.cursor.execute('SELECT balance FROM shells WHERE "user" = %s', (str(DISCORD),))
    assert temp_db.cursor.fetchone()[0] == 10
    temp_db.cursor.execute("SELECT delta_balance, source FROM shell_transactions")
    assert temp_db.cursor.fetchall() == [(-50, "tome_request")]
    temp_db.cursor.execute("SELECT action, kind, count FROM tome_events WHERE action = 'requested'")
    assert temp_db.cursor.fetchall() == [("requested", "raid", 3)]
    temp_db.cursor.execute("SELECT log_type FROM audit_log")
    assert temp_db.cursor.fetchall() == [("tome",)]


def test_a_request_of_any_size_joins_the_queue_whole(temp_db):
    _shells(temp_db, 0)
    made = _ask(temp_db, "war", 120, wars=tomes.wars_needed(125))
    assert made["count"] == 120
    temp_db.cursor.execute("SELECT bucket, count FROM tome_requests WHERE id = %s", (made["id"],))
    assert temp_db.cursor.fetchone() == ("queue", 120)


def test_war_tomes_count_taq_wars_only(temp_db):
    c = temp_db.cursor
    # 1000 wars elsewhere first, 40 in a first stint, 500 elsewhere, then the current stint from 1540.
    c.execute(
        "INSERT INTO membership_stints (uuid, joined_at, left_at, wars_on_join, wars_on_leave, source) VALUES"
        " (%s::uuid, '2025-08-01T00:00:00Z', '2025-09-01T00:00:00Z', 1000, 1040, 'backfill'),"
        " (%s::uuid, '2026-01-01T00:00:00Z', NULL, 1540, NULL, 'live')",
        (UUID, UUID),
    )
    c.execute("INSERT INTO player_activity (uuid, playtime, wars, snapshot_date) VALUES (%s::uuid, 0, 1590, '2026-03-01')", (UUID,))
    assert tomes.taq_wars_for(c, UUID, 1600) == 100
    # Hidden now: the open stint runs to the bot's last reading.
    assert tomes.taq_wars_for(c, UUID, None) == 90
    assert tomes.war_tomes_earned(tomes.taq_wars_for(c, UUID, 1600)) == 4
    assert tomes.taq_wars_for(c, "11111111-2222-3333-4444-666666666666", 1600) is None


def test_a_member_who_is_not_in_the_guild_cant_request(temp_db):
    _shells(temp_db, 100)
    with pytest.raises(tomes.TomeError, match="in the guild"):
        tomes.request_tomes(temp_db, uuid=UUID, discord_id=DISCORD, ign="x", kind="raid", count=1, priority="low", wars=None)
    temp_db.cursor.execute('SELECT balance FROM shells WHERE "user" = %s', (str(DISCORD),))
    assert temp_db.cursor.fetchone()[0] == 100


def _buckets(db):
    db.cursor.execute("SELECT ign, bucket, left_from FROM tome_requests ORDER BY id")
    return db.cursor.fetchall()


def test_leaving_makes_requests_dormant_and_returning_puts_them_back(temp_db):
    c, other = temp_db.cursor, "11111111-2222-3333-4444-666666666666"
    _in_guild(temp_db, other)
    for ign, uuid, bucket in (("a", UUID, "queue"), ("b", UUID, "give_when_full"), ("stays", other, "queue"), ("by_hand", other, "dormant")):
        c.execute("INSERT INTO tome_requests (uuid, ign, kind, count, priority, bucket) VALUES (%s, %s, 'war', 1, 'low', %s)", (uuid, ign, bucket))
    c.execute("INSERT INTO tome_requests (uuid, ign, kind, count, priority, bucket, status) VALUES (%s, 'done', 'war', 1, 'low', 'queue', 'done')", (UUID,))

    assert tomes.sync_membership(temp_db) == (2, 0)  # UUID was never in the roster
    assert _buckets(temp_db)[:2] == [("a", "dormant", "queue"), ("b", "dormant", "give_when_full")]
    assert tomes.sync_membership(temp_db) == (0, 0)
    c.execute("SELECT note FROM tome_events WHERE action = 'moved'")
    assert {r[0] for r in c.fetchall()} == {"queue -> dormant (left the guild)", "give_when_full -> dormant (left the guild)"}

    _in_guild(temp_db)
    assert tomes.sync_membership(temp_db) == (0, 2)
    assert _buckets(temp_db) == [("a", "queue", None), ("b", "give_when_full", None), ("stays", "queue", None),
                                 ("by_hand", "dormant", None), ("done", "queue", None)]


def test_an_empty_roster_moves_nobody(temp_db):
    temp_db.cursor.execute("INSERT INTO tome_requests (uuid, ign, kind, count, priority) VALUES (%s, 'a', 'war', 1, 'low')", (UUID,))
    assert tomes.sync_membership(temp_db) == (0, 0)
    assert _buckets(temp_db) == [("a", "queue", None)]


def test_bad_input_is_refused(temp_db):
    _shells(temp_db, 100)
    for kind, count, priority, msg in (("aspect", 1, "low", "war tomes or shell"), ("war", 0, "low", "at least one"),
                                       ("raid", 1, "urgent", "priority")):
        with pytest.raises(tomes.TomeError, match=msg):
            _ask(temp_db, kind, count, wars=500, priority=priority)


def test_request_button_is_a_panel_action_and_the_grant_log_reads_well():
    from Commands.generate import PANEL_ACTIONS
    from Commands.tomes import TOME_REQUEST_BUTTON_ID, TomeRequestView, grant_line
    assert PANEL_ACTIONS[TOME_REQUEST_BUTTON_ID].label == "Request tomes"
    assert TomeRequestView.__view_children_items__  # the persistent button exists
    assert grant_line(3, "war", "Thundderr", 170719819715313665, "Kenji121") == \
        "\U0001F4D8 <@170719819715313665> (Thundderr) was given **3 war tomes** by Kenji121."
    assert grant_line(1, None, "OldMember", None, None) == "\U0001F4D8 OldMember was given **1 tome**."
