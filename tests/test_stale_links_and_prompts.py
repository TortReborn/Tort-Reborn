"""/stale-roles data (Helpers/stale_links.py) and the leave-prompt posting
(Helpers/leave_prompts.py) -- TAQ-76.

Stale = a discord_links row holding a member rank whose uuid is not on the
roster: exactly the people waiting for a roles reset. Ally ranks and
rankless rows are not stale. The leave prompt posts one message per leaver
(with buttons only when the leaver is linked), records each in
member_leave_prompts, and falls back to the batched embed past the cap.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import leave_prompts as lp
from Helpers import stale_links as sl
from Helpers import honorifics as hon

U1 = '065fc385-f9c1-4e7f-96b3-8674a65c509f'
U2 = '9aeb062a-f769-49bc-8046-4d9c8cc86e5a'
U3 = '110c11c8-d8b7-478d-8adf-b0f606d5f939'
U4 = '22222222-2222-2222-2222-222222222222'


def test_fetch_stale_links(temp_db):
    cur = temp_db.cursor
    cur.execute("INSERT INTO guild_roster (uuid, ign, in_game_rank) VALUES (%s::uuid, 'here', 'recruit')", (U1,))
    cur.execute("INSERT INTO discord_links (discord_id, ign, uuid, rank) VALUES "
                "(1, 'here', %s::uuid, 'Angler'), (2, 'gone', %s::uuid, 'Narwhal'), "
                "(3, 'ally', %s::uuid, 'Navigator'), (4, 'reset', %s::uuid, NULL)", (U1, U2, U3, U4))
    rows = sl.fetch_stale_taq_links(cur)
    assert [(r[0], r[1], r[3]) for r in rows] == [(2, 'gone', 'Narwhal')]


def test_stale_shaping_and_filtering():
    rows = [(2, 'Zed', U2, 'Narwhal'), (5, 'amy', U3, 'Starfish'), (6, 'x', U4, 'WeaponMerchant'), (7, 'left', U1, 'Angler')]
    shaped = sl.stale_taq_links(rows, discord_member_ids=[2, 5, 6])
    assert [r['ign'] for r in shaped] == ['amy', 'Zed']            # lowest rank first, unknown rank dropped, not-in-Discord dropped
    assert sl.stale_taq_links(rows)[-1]['ign'] == 'Zed'
    text = sl.render_stale_taq_links(shaped)
    assert text.startswith('Found 2 stale')
    assert sl.render_stale_taq_links([]) == 'No stale linked members found.'
    assert len(sl.split_stale_report('a\n' * 5000, limit=100)) > 1


# ── leave prompts ────────────────────────────────────────────────────────

class FakeMessage:
    def __init__(self, message_id):
        self.id = message_id


class FakeChannel:
    def __init__(self, guild):
        self.guild = guild
        self.sent = []
        self._next = 9000

    async def send(self, embed=None, view=None):
        self.sent.append((embed, view))
        self._next += 1
        return FakeMessage(self._next)


class FakeGuild:
    def __init__(self, present=()):
        self._present = set(present)

    def get_member(self, discord_id):
        return object() if discord_id in self._present else None

    async def fetch_member(self, discord_id):
        import discord
        raise discord.NotFound(type('R', (), {'status': 404, 'reason': 'x'})(), 'Unknown Member')


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_post_leave_prompts(temp_db, monkeypatch):
    cur = temp_db.cursor
    monkeypatch.setattr(lp, 'DB', lambda: temp_db)
    cur.execute("INSERT INTO discord_links (discord_id, ign, uuid, rank) VALUES (1, 'linked', %s::uuid, 'Angler'), (2, 'gone', %s::uuid, 'Piranha')", (U1, U2))
    hon.grant(cur, uuid=U2, ign='gone', honorific=hon.RETIRED_CHIEF, granted_by=0)
    channel = FakeChannel(FakeGuild(present={1}))

    async def go():
        import discord
        return await lp.post_leave_prompts(
            None, channel,
            [(U1, 'linked', 'recruit'), (U2, 'gone', 'captain'), (U3, 'unlinked', 'recruit')],
            fallback_embed=discord.Embed(title='batched'), now=None,
        )
    posted = run(go())
    assert len(posted) == 3 and len(channel.sent) == 3

    embeds = [e for e, _ in channel.sent]
    views = [v for _, v in channel.sent]
    assert [e.fields[1].value for e in embeds] == ['<@1> (linked)', '<@2> — no longer in this server', 'not linked']
    assert [e.fields[2].value for e in embeds] == ['none', 'Retired Chief', 'none']
    assert views[0] is not None and views[1] is not None and views[2] is None   # no buttons without a link

    cur.execute("SELECT message_id, uuid::text, ign, discord_id, last_rank FROM member_leave_prompts ORDER BY message_id")
    assert cur.fetchall() == [(9001, U1, 'linked', 1, 'Angler'), (9002, U2, 'gone', 2, 'Piranha'), (9003, U3, 'unlinked', None, None)]


def test_post_leave_prompts_falls_back_past_cap(temp_db, monkeypatch):
    monkeypatch.setattr(lp, 'DB', lambda: temp_db)
    channel = FakeChannel(FakeGuild())
    import discord
    fallback = discord.Embed(title='batched')
    leavers = [(U1, f'p{i}', 'recruit') for i in range(lp.MAX_PROMPTS_PER_CYCLE + 1)]

    posted = run(lp.post_leave_prompts(None, channel, leavers, fallback_embed=fallback))
    assert posted == [] and channel.sent == [(fallback, None)]
    temp_db.cursor.execute("SELECT COUNT(*) FROM member_leave_prompts")
    assert temp_db.cursor.fetchone() == (0,)


def test_outcome_branches():
    from Helpers import member_roles as mr

    class M:
        def __init__(self, names):
            self.roles = [type('R', (), {'name': n})() for n in names]

    assert lp._outcome_for({}, None, None) == 'noop'
    assert lp._outcome_for({}, None, hon.HONORED_FISH) == 'grant_only'
    assert lp._outcome_for({}, M([mr.EX_MEMBER_ROLE, 'Merman']), None) == 'noop'
    assert lp._outcome_for({}, M([mr.EX_MEMBER_ROLE]), hon.RETIRED_CHIEF) == 'grant_ex_member'
    assert lp._outcome_for({}, M([mr.EX_MEMBER_ROLE, mr.MEMBER_ROLE]), None) == 'remove'   # half-stripped: still work to do
    assert lp._outcome_for({}, M([mr.MEMBER_ROLE, 'Angler']), None) == 'remove'


# ── rejoin restore ───────────────────────────────────────────────────────

def test_rejoin_lookup_skips_current_members(temp_db, monkeypatch):
    import Events.on_member_join as omj
    monkeypatch.setattr(omj, 'DB', lambda: temp_db)
    cur = temp_db.cursor
    cur.execute("INSERT INTO discord_links (discord_id, ign, uuid, rank) VALUES (5, 'A', %s::uuid, 'Angler')", (U1,))
    hon.grant(cur, uuid=U1, ign='A', honorific=hon.HONORED_FISH, granted_by=0)

    # On the roster: rejoining Discord must not hand them Ex-Member + Honored Fish.
    cur.execute("INSERT INTO guild_roster (uuid, ign, in_game_rank) VALUES (%s::uuid, 'A', 'recruit')", (U1,))
    assert omj._honorifics_on_record(5) == (False, False, [])

    # Off the roster: restore, with the grant details for the log line.
    cur.execute("DELETE FROM guild_roster")
    hf, rc, grants = omj._honorifics_on_record(5)
    assert (hf, rc) == (True, False) and grants[0]['honorific'] == hon.HONORED_FISH

    # Discord-only grant (never linked) is found by account.
    hon.grant(cur, discord_id=6, ign='B', honorific=hon.RETIRED_CHIEF, granted_by=0)
    assert omj._honorifics_on_record(6)[:2] == (False, True)
