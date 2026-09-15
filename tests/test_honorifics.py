"""member_honorifics ledger (Helpers/honorifics.py, TAQ-76).

Against the real schema (temp tables): grants are idempotent per player and
honorific; revoke closes rather than deletes; a holder recorded by Discord
id alone gets the uuid attached when they link, and a duplicate that would
collide with a uuid-keyed grant is merged; lookups by discord_id find both
shapes; record_held_roles turns Discord roles into grants exactly once.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import honorifics as hon

U1 = '065fc385-f9c1-4e7f-96b3-8674a65c509f'
U2 = '9aeb062a-f769-49bc-8046-4d9c8cc86e5a'


def test_grant_is_idempotent_and_revoke_closes(temp_db):
    cur = temp_db.cursor
    id1, created1 = hon.grant(cur, uuid=U1, ign='A', honorific=hon.HONORED_FISH, granted_by=1, note='first')
    id2, created2 = hon.grant(cur, uuid=U1, ign='A', honorific=hon.HONORED_FISH, granted_by=2, note='again')
    assert (created1, created2) == (True, False) and id1 == id2
    assert hon.active_honorifics(cur, uuid=U1) == (True, False)

    assert hon.revoke(cur, uuid=U1, honorific=hon.HONORED_FISH, revoked_by=3, note='left on bad terms') is True
    assert hon.revoke(cur, uuid=U1, honorific=hon.HONORED_FISH, revoked_by=3) is False
    assert hon.active_honorifics(cur, uuid=U1) == (False, False)
    rows = hon.history(cur, uuid=U1)
    assert len(rows) == 1
    assert rows[0]['revoked_by'] == 3 and rows[0]['note'] == 'first | left on bad terms'

    # A fresh grant after a revoke is a new row; history keeps both.
    _, created3 = hon.grant(cur, uuid=U1, ign='A', honorific=hon.HONORED_FISH, granted_by=4)
    assert created3 is True
    assert len(hon.history(cur, uuid=U1)) == 2


def test_retired_chief_is_a_separate_row(temp_db):
    cur = temp_db.cursor
    hon.grant(cur, uuid=U1, ign='A', honorific=hon.RETIRED_CHIEF, granted_by=1)
    assert hon.active_honorifics(cur, uuid=U1) == (False, True)
    # Implication to Honored Fish lives in member_roles, not the ledger.
    from Helpers.member_roles import removal_role_names, HONORED_FISH_ROLE, RETIRED_CHIEF_ROLE
    to_add, _ = removal_role_names(*hon.active_honorifics(cur, uuid=U1))
    assert HONORED_FISH_ROLE in to_add and RETIRED_CHIEF_ROLE in to_add


def test_bad_inputs():
    with pytest.raises(ValueError):
        hon.grant(None, uuid=U1, ign='A', honorific='chief', granted_by=1)
    with pytest.raises(ValueError):
        hon.grant(None, ign='A', honorific=hon.HONORED_FISH, granted_by=1)


def test_discord_only_grant_then_link(temp_db):
    cur = temp_db.cursor
    _, created = hon.grant(cur, discord_id=900, ign='oldtimer', honorific=hon.HONORED_FISH, granted_by=0)
    assert created
    _, created = hon.grant(cur, discord_id=900, ign='oldtimer', honorific=hon.HONORED_FISH, granted_by=0)
    assert not created                                          # idempotent by discord_id too
    assert hon.active_honorifics(cur, discord_id=900) == (True, False)
    assert hon.active_honorifics(cur, uuid=U1) == (False, False)

    hon.refresh_discord_id(cur, U1, 900)                        # what upsert_identity calls
    assert hon.active_honorifics(cur, uuid=U1) == (True, False)
    cur.execute("SELECT uuid::text, discord_id FROM member_honorifics WHERE revoked_at IS NULL")
    assert cur.fetchall() == [(U1, 900)]


def test_attach_uuid_merges_duplicates(temp_db):
    cur = temp_db.cursor
    hon.grant(cur, uuid=U1, ign='A', honorific=hon.HONORED_FISH, granted_by=1)
    hon.grant(cur, discord_id=901, ign='A', honorific=hon.HONORED_FISH, granted_by=0)
    hon.grant(cur, discord_id=901, ign='A', honorific=hon.RETIRED_CHIEF, granted_by=0)

    hon.attach_uuid(cur, 901, U1)

    active = [(r['honorific'], r['discord_id']) for r in hon.history(cur, uuid=U1) if r['revoked_at'] is None]
    assert sorted(active) == [(hon.HONORED_FISH, None), (hon.RETIRED_CHIEF, 901)]
    cur.execute("SELECT note FROM member_honorifics WHERE revoked_at IS NOT NULL")
    assert cur.fetchall() == [('merged into uuid-keyed grant',)]


def test_lookup_by_discord_id_via_link(temp_db):
    cur = temp_db.cursor
    cur.execute("INSERT INTO discord_links (discord_id, ign, uuid) VALUES (902, 'B', %s::uuid)", (U2,))
    hon.grant(cur, uuid=U2, ign='B', honorific=hon.RETIRED_CHIEF, granted_by=1)   # no discord_id on the row
    assert hon.active_honorifics(cur, discord_id=902) == (False, True)
    assert [r['honorific'] for r in hon.history(cur, discord_id=902)] == [hon.RETIRED_CHIEF]
    assert hon.active_honorifics(cur) == (False, False)


def test_record_held_roles(temp_db):
    cur = temp_db.cursor
    recorded = hon.record_held_roles(cur, uuid=U1, ign='A', discord_id=903, actor_id=5,
                                     role_names=['Member', 'Honored Fish', 'Retired Chief', 'Merman'],
                                     note='recorded at registration')
    assert sorted(recorded) == [hon.HONORED_FISH, hon.RETIRED_CHIEF]
    again = hon.record_held_roles(cur, uuid=U1, ign='A', discord_id=903, actor_id=5,
                                  role_names=['Honored Fish'], note='x')
    assert again == []
    assert hon.record_held_roles(cur, uuid=U1, ign='A', discord_id=903, actor_id=5, role_names=[], note='x') == []
