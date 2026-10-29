"""Warning tracker, bot side (Helpers/warnings.py, TAQ-79).

Pure: counting and tags match the website; embeds show what exec need and
say when an entry is revoked. Against the real schema (temp tables): which
profiles need work and in what order, the claim protocol, who should hold
Dishonored Fish, and hand role changes in Discord becoming entries or
revocations.
"""

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import warnings as warn

U1 = '065fc385-f9c1-4e7f-96b3-8674a65c509f'
U2 = '9aeb062a-f769-49bc-8046-4d9c8cc86e5a'
T0 = datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc)


def _entry(kind, revoked=False, **extra):
    e = {
        'id': 1, 'ign': 'Player', 'profile_ign': 'Player', 'kind': kind, 'rule': 'Rule text',
        'notes': None, 'issued_by': 42, 'issued_at': T0, 'revoked_at': T0 if revoked else None,
        'revoked_by': 7 if revoked else None, 'revoke_reason': 'Stale' if revoked else None,
        'source_url': None,
    }
    e.update(extra)
    return e


# --- pure ---------------------------------------------------------------------

def test_count_active_matches_the_website_badge():
    counts = warn.count_active([
        _entry('soft'), _entry('written'), _entry('formal'), _entry('note'), _entry('kick'),
        _entry('soft', revoked=True), _entry('dishonored_fish'),
    ])
    assert counts == {'total': 3, 'soft': 1, 'written': 1, 'formal': 1, 'dishonored': True}


def test_revoked_dishonored_fish_is_not_active():
    assert warn.count_active([_entry('dishonored_fish', revoked=True)])['dishonored'] is False


def test_tags_are_the_highest_level_plus_dishonored():
    counts = warn.count_active([_entry('soft'), _entry('written'), _entry('dishonored_fish')])
    assert warn.tag_names(counts) == ['Written', 'Dishonored Fish']
    assert warn.tag_names(warn.count_active([_entry('note')])) == []


def test_summary_lists_active_entries_and_counts_revoked():
    profile = {'uuid': U1, 'ign': 'Player'}
    embed = warn.summary_embed(profile, [
        _entry('written', source_url='https://discord.com/channels/1/2/3'),
        _entry('soft', revoked=True),
    ], 'https://the-aquarium.com')
    assert embed.title == 'Player'
    assert embed.url == f'https://the-aquarium.com/exec/warnings/{U1}'
    assert '1 active warning' in embed.description
    assert 'Written warning' in embed.description and '[original](https://discord.com/channels/1/2/3)' in embed.description
    assert '1 revoked entry not shown' in embed.description


def test_summary_stays_under_the_embed_limit():
    many = [_entry('soft', rule='x' * 200) for _ in range(60)]
    assert len(warn.summary_embed({'uuid': U1, 'ign': 'P'}, many, 'https://w').description) <= 4096


def test_entry_embed_marks_revocation_and_old_names():
    live = warn.entry_embed(_entry('formal', notes='Context', ign='OldName'))
    assert live.title == 'Formal warning'
    assert {f.name for f in live.fields} == {'Issued by', 'Name at the time', 'Notes'}

    gone = warn.entry_embed(_entry('formal', revoked=True))
    assert gone.title == '~~Formal warning~~ (revoked)'
    assert any(f.name == 'Revoked' and 'Stale' in f.value for f in gone.fields)


# --- DB -----------------------------------------------------------------------

def _profile(cur, uuid, ign, *, thread=None, needs_sync=False, discord_id=None, updated_at=T0):
    cur.execute(
        """INSERT INTO warning_profiles (uuid, ign, forum_thread_id, needs_sync, discord_id, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (uuid, ign, thread, needs_sync, discord_id, updated_at),
    )


def _warning(cur, uuid, kind='soft', *, message_id=None, synced=None, updated=T0, revoked=False):
    cur.execute(
        """INSERT INTO player_warnings (uuid, ign, kind, rule, issued_by, discord_message_id,
                                        discord_synced_at, updated_at, revoked_at)
           VALUES (%s, 'P', %s, 'r', 1, %s, %s, %s, %s) RETURNING id""",
        (uuid, kind, message_id, synced, updated, T0 if revoked else None),
    )
    return cur.fetchone()[0]


def test_profiles_needing_work(temp_db):
    cur = temp_db.cursor
    _profile(cur, U1, 'Settled', thread=10)
    _warning(cur, U1, message_id=500, synced=T0, updated=T0)          # posted, unchanged
    _profile(cur, U2, 'Fresh')                                        # no post yet
    _warning(cur, U2, message_id=0)                                   # backfilled
    assert warn.profiles_needing_work(cur) == [(U2, False)]

    # An edit after the last render makes the settled profile due, ahead of new posts.
    cur.execute("UPDATE player_warnings SET updated_at = %s WHERE uuid = %s", (T0 + datetime.timedelta(hours=1), U1))
    assert warn.profiles_needing_work(cur) == [(U1, True), (U2, False)]


def test_claim_protocol(temp_db):
    cur = temp_db.cursor
    _profile(cur, U1, 'P', thread=10)
    wid = _warning(cur, U1)
    assert warn.claim_entry(cur, wid) is True
    assert warn.claim_entry(cur, wid) is False                        # someone already has it
    warn.release_claim(cur, wid)
    assert warn.claim_entry(cur, wid) is True
    assert warn.reset_stale_claims(cur) == 1                         # a restart frees it
    warn.mark_posted(cur, wid, 999, T0)
    cur.execute("SELECT discord_message_id, discord_synced_at FROM player_warnings WHERE id = %s", (wid,))
    assert cur.fetchone() == (999, T0)


def test_clear_needs_sync_keeps_a_newer_change(temp_db):
    cur = temp_db.cursor
    _profile(cur, U1, 'P', needs_sync=True, updated_at=T0 + datetime.timedelta(minutes=5))
    warn.clear_needs_sync(cur, U1, T0)                                # render started before the change
    cur.execute("SELECT needs_sync FROM warning_profiles WHERE uuid = %s", (U1,))
    assert cur.fetchone()[0] is True
    warn.clear_needs_sync(cur, U1, T0 + datetime.timedelta(minutes=10))
    cur.execute("SELECT needs_sync FROM warning_profiles WHERE uuid = %s", (U1,))
    assert cur.fetchone()[0] is False


def test_dishonored_holders_use_discord_links_then_profile(temp_db):
    cur = temp_db.cursor
    _profile(cur, U1, 'Unlinked', discord_id=111)
    _warning(cur, U1, 'dishonored_fish')
    _profile(cur, U2, 'Relinked', discord_id=999)                     # cached id from an old account
    _warning(cur, U2, 'dishonored_fish')
    cur.execute("INSERT INTO discord_links (discord_id, ign, uuid) VALUES (222, 'Relinked', %s)", (U2,))
    assert warn.dishonored_holders(cur) == {111: U1, 222: U2}

    cur.execute("UPDATE player_warnings SET revoked_at = NOW() WHERE uuid = %s", (U2,))
    assert warn.dishonored_holders(cur) == {111: U1}


def test_uuid_for_discord_prefers_the_current_link(temp_db):
    cur = temp_db.cursor
    _profile(cur, U1, 'OldOwner', discord_id=333)                     # stale: 333 now links to U2
    cur.execute("INSERT INTO discord_links (discord_id, ign, uuid) VALUES (333, 'NewOwner', %s)", (U2,))
    assert warn.uuid_for_discord(cur, 333) == (U2, 'NewOwner')
    assert warn.uuid_for_discord(cur, 444) is None


def test_dishonored_changes_since_reports_whether_any_stay_active(temp_db):
    cur = temp_db.cursor
    _profile(cur, U1, 'P', discord_id=111)
    _warning(cur, U1, 'dishonored_fish', updated=T0, revoked=True)
    _warning(cur, U1, 'soft', updated=T0)                             # not a dishonored change
    changes = warn.dishonored_changes_since(cur, None)
    assert changes == [(111, U1, False, T0)]
    assert warn.dishonored_changes_since(cur, T0) == []


def test_hand_added_role_becomes_an_entry_once(temp_db):
    cur = temp_db.cursor
    assert warn.record_role_added(cur, 333, actor_id=9) == 'unlinked'
    cur.execute("INSERT INTO discord_links (discord_id, ign, uuid) VALUES (333, 'Linked', %s)", (U1,))
    assert warn.record_role_added(cur, 333, actor_id=9) == 'recorded'
    assert warn.record_role_added(cur, 333, actor_id=9) == 'already_active'
    cur.execute("SELECT kind, source, issued_by, discord_message_id FROM player_warnings WHERE uuid = %s", (U1,))
    assert cur.fetchall() == [('dishonored_fish', 'discord', 9, None)]   # queued for the forum
    cur.execute("SELECT discord_id, needs_sync FROM warning_profiles WHERE uuid = %s", (U1,))
    assert cur.fetchone() == (333, True)


def test_hand_removed_role_revokes_active_entries(temp_db):
    cur = temp_db.cursor
    _profile(cur, U1, 'P', discord_id=111)
    _warning(cur, U1, 'dishonored_fish')
    assert warn.record_role_removed(cur, 111, actor_id=9) == 1
    assert warn.record_role_removed(cur, 111, actor_id=9) == 0
    assert warn.record_role_removed(cur, 999, actor_id=9) == 0     # unknown account
    cur.execute("SELECT revoked_by, revoke_reason FROM player_warnings WHERE uuid = %s", (U1,))
    assert cur.fetchone() == (9, warn.REVOKE_REASON_ROLE_REMOVED)
