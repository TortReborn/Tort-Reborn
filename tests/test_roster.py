"""guild_roster / membership_stints (Helpers/roster.py, TAQ-76).

Pure: diff_roster keys by dashless uuid, so API and DB spellings match.
DB (temp tables, see conftest): sync_roster upserts the roster, opens a
stint on join (picking up the linked account), closes it on leave with the
Discord rank stamped; registration attaches identity + wars to the open
stint; removal stamps rank_at_leave without closing; latest_stint prefers
the open one.
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import roster

U1 = '065fc385-f9c1-4e7f-96b3-8674a65c509f'
U2 = '9aeb062a-f769-49bc-8046-4d9c8cc86e5a'
U3 = '110c11c8-d8b7-478d-8adf-b0f606d5f939'


def api(uuid, name, rank='recruit', joined='2026-08-01T12:00:00.000000Z'):
    return {'uuid': uuid, 'name': name, 'rank': rank, 'joined': joined}


def test_diff_roster_ignores_dash_format():
    joined, left = roster.diff_roster([U1.replace('-', '')], [api(U1, 'a'), api(U2, 'b')])
    assert joined == {roster.uuid_key(U2)}
    assert left == set()
    joined, left = roster.diff_roster([roster.uuid_key(U1), roster.uuid_key(U2)], [api(U1, 'a')])
    assert (joined, left) == (set(), {roster.uuid_key(U2)})


def test_uuid_key():
    assert roster.uuid_key(None) is None
    assert roster.uuid_key('') is None
    assert roster.uuid_key(U1.upper()) == U1.replace('-', '')


def test_sync_opens_and_closes_stints(temp_db):
    cur = temp_db.cursor
    cur.execute("INSERT INTO discord_links (discord_id, ign, uuid, rank) VALUES (500, 'A', %s::uuid, 'Angler')", (U1,))
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)

    joined, left = roster.sync_roster(cur, [api(U1, 'A'), api(U2, 'B', 'chief')], now)
    assert joined == {roster.uuid_key(U1), roster.uuid_key(U2)} and left == set()
    cur.execute("SELECT uuid::text, ign, in_game_rank FROM guild_roster ORDER BY ign")
    assert cur.fetchall() == [(U1, 'A', 'recruit'), (U2, 'B', 'chief')]
    # The stint for a linked player carries the account; the API join date wins.
    s1 = roster.latest_stint(cur, U1)
    assert s1['discord_id'] == 500 and s1['left_at'] is None
    assert s1['joined_at'].astimezone(timezone.utc).isoformat().startswith('2026-08-01')
    assert roster.latest_stint(cur, U2)['discord_id'] is None

    # Second cycle: B renamed, A left.
    later = datetime(2026, 9, 2, tzinfo=timezone.utc)
    joined, left = roster.sync_roster(cur, [api(U2, 'Bee', 'chief')], later)
    assert joined == set() and left == {roster.uuid_key(U1)}
    cur.execute("SELECT ign FROM guild_roster")
    assert cur.fetchall() == [('Bee',)]
    s1 = roster.latest_stint(cur, U1)
    assert s1['left_at'] == later and s1['left_via'] == 'api_diff'
    assert s1['rank_at_leave'] == 'Angler'   # the Discord rank they still held
    assert roster.is_member(cur, uuid=U1) is False
    assert roster.is_member(cur, discord_id=500) is False
    assert roster.is_member(cur, uuid=U2) is True
    assert roster.has_prior_stint(cur, U1) is True
    assert roster.has_prior_stint(cur, U2) is False


def test_rejoin_opens_a_second_stint(temp_db):
    cur = temp_db.cursor
    t1, t2, t3 = (datetime(2026, 9, d, tzinfo=timezone.utc) for d in (1, 2, 3))
    roster.sync_roster(cur, [api(U1, 'A')], t1)
    roster.sync_roster(cur, [], t2)
    roster.sync_roster(cur, [api(U1, 'A', joined=None)], t3)
    cur.execute("SELECT joined_at, left_at FROM membership_stints WHERE uuid = %s::uuid ORDER BY joined_at", (U1,))
    rows = cur.fetchall()
    assert len(rows) == 2
    assert rows[0][1] == t2
    assert rows[1] == (t3, None)   # no API join date -> now


def test_open_stint_is_idempotent_and_fills_blanks(temp_db):
    cur = temp_db.cursor
    a = roster.open_stint(cur, U1, joined_at='2026-01-01T00:00:00Z')
    b = roster.open_stint(cur, U1, joined_at='2026-02-02T00:00:00Z', discord_id=7, wars_on_join=12)
    assert a == b
    s = roster.latest_stint(cur, U1)
    assert (s['discord_id'], s['wars_on_join']) == (7, 12)
    assert s['joined_at'].astimezone(timezone.utc).isoformat().startswith('2026-01-01')


def test_attach_identity_and_stamp_rank(temp_db):
    cur = temp_db.cursor
    roster.open_stint(cur, U1, joined_at='2026-01-01T00:00:00Z')
    assert roster.attach_identity(cur, U1, 42, wars_on_join=3) is True
    assert roster.attach_identity(cur, U2, 43) is False        # not on roster: nothing to attach
    s = roster.latest_stint(cur, U1)
    assert (s['discord_id'], s['wars_on_join']) == (42, 3)

    assert roster.stamp_rank_at_leave(cur, U1, 'Piranha') is True
    assert roster.stamp_rank_at_leave(cur, U1, 'Narwhal') is False   # first stamp wins
    assert roster.stamp_rank_at_leave(cur, U1, None) is False
    s = roster.latest_stint(cur, U1)
    assert s['rank_at_leave'] == 'Piranha' and s['left_at'] is None   # still open

    # Closing later keeps the stamped rank rather than the current one.
    cur.execute("INSERT INTO discord_links (discord_id, ign, uuid, rank) VALUES (42, 'A', %s::uuid, 'Angler')", (U1,))
    roster.close_stint(cur, U1, datetime(2026, 9, 9, tzinfo=timezone.utc))
    assert roster.latest_stint(cur, U1)['rank_at_leave'] == 'Piranha'


def test_latest_stint_prefers_open(temp_db):
    cur = temp_db.cursor
    cur.execute("INSERT INTO membership_stints (uuid, joined_at, left_at) VALUES (%s::uuid, '2025-01-01', '2025-06-01')", (U1,))
    cur.execute("INSERT INTO membership_stints (uuid, joined_at) VALUES (%s::uuid, '2024-01-01')", (U1,))
    s = roster.latest_stint(cur, U1)
    assert s['left_at'] is None and s['joined_at'].year == 2024
    assert roster.latest_stint(cur, U3) is None


def test_applicant_sql_fragment_shape():
    assert "JOIN guild_roster gr ON gr.uuid = dl.uuid" in roster.APPLICANT_IS_MEMBER_SQL
    assert "CAST(a.discord_id AS BIGINT)" in roster.APPLICANT_IS_MEMBER_SQL


def test_applicant_has_joined_sql_shape():
    # Sticky "joined for this application": a stint active at or after the
    # application, so an applicant who joined and later left is not pending again.
    assert "JOIN membership_stints ms ON ms.uuid = dl.uuid" in roster.APPLICANT_HAS_JOINED_SQL
    assert "ms.left_at >= COALESCE(a.submitted_at, a.reviewed_at)" in roster.APPLICANT_HAS_JOINED_SQL
    assert "INTERVAL" not in roster.APPLICANT_HAS_JOINED_SQL


# ── pending registration (update_member_data) ─────────────────────────────

def _pending(temp_db, monkeypatch, uuid):
    import Tasks.update_member_data as umd
    monkeypatch.setattr(umd, 'DB', lambda: temp_db)
    sweep = {u for u, _ in umd.UpdateMemberData._fetch_unlinked_with_app()}
    # _check_pending_app is a closure inside _auto_register_joined_member;
    # the sweep query carries the identical predicate, so assert on that.
    return uuid in sweep


def test_pending_registration_predicate(temp_db, monkeypatch):
    cur = temp_db.cursor
    cur.execute("INSERT INTO discord_links (discord_id, ign, uuid, rank) VALUES (7, 'A', %s::uuid, NULL)", (U1,))
    cur.execute("INSERT INTO applications (application_type, discord_id, discord_username, status, answers, submitted_at, reviewed_at) "
                "VALUES ('guild', '7', 'a', 'accepted', '{}', '2026-09-01', '2026-09-02')")

    # Accepted, identity row, no rank, no stint yet: pending (they may join any minute).
    assert _pending(temp_db, monkeypatch, U1) is True

    # Joined for this application: open stint, not yet registered -> still pending.
    roster.open_stint(cur, U1, joined_at='2026-09-03T00:00:00Z')
    assert _pending(temp_db, monkeypatch, U1) is True

    # Registered (rank set) -> not pending.
    cur.execute("UPDATE discord_links SET rank = 'Starfish' WHERE discord_id = 7")
    assert _pending(temp_db, monkeypatch, U1) is False

    # /reset_roles on a current member: rank cleared but the open stint is
    # stamped -> must NOT be auto-registered again at Starfish.
    cur.execute("UPDATE discord_links SET rank = NULL WHERE discord_id = 7")
    roster.stamp_rank_at_leave(cur, U1, 'Piranha')
    assert _pending(temp_db, monkeypatch, U1) is False

    # Left in-game (stint closed) -> not pending on the old application.
    cur.execute("UPDATE membership_stints SET rank_at_leave = NULL WHERE uuid = %s::uuid", (U1,))
    roster.close_stint(cur, U1)
    assert _pending(temp_db, monkeypatch, U1) is False

    # A new application after that stint makes them pending again.
    cur.execute("INSERT INTO applications (application_type, discord_id, discord_username, status, answers, submitted_at, reviewed_at) "
                "VALUES ('guild', '7', 'a', 'accepted', '{}', NOW(), NOW())")
    assert _pending(temp_db, monkeypatch, U1) is True
