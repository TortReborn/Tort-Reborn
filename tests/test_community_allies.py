import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Helpers import ally_registration as ar
from Helpers.alliances import Alliance
from Helpers.links import LinkConflictError

UUID = '065fc385-f9c1-4e7f-96b3-8674a65c509f'
EDEN = Alliance(2, 'Eden', 'EDN', 123, 'Horizon', 'ally')
NIA = Alliance(1, 'Nerfuria', 'NIA', 456, 'Navigator', 'ally')


def profile(name='Eden', prefix='EDN'):
    return {'uuid': UUID, 'guild': {'name': name, 'prefix': prefix}}


@pytest.mark.parametrize('declared', ['Eden', 'eden', 'EDN', 'Eden [EDN]', 'Eden (EDN)', ''])
def test_current_guild_matches_name_or_prefix(declared):
    assert ar.community_alliance([EDEN, NIA], declared, profile(), UUID) == EDEN


def test_current_guild_works_without_a_known_declared_guild():
    assert ar.community_alliance([EDEN], 'a friendly guild', profile(), UUID) == EDEN


@pytest.mark.parametrize('current', [profile('Nerfuria', 'NIA'), profile('Other', 'OTHER'), {'uuid': UUID, 'guild': None}])
def test_disagreement_needs_review(current):
    with pytest.raises(ar.AllyRegistrationError, match='review'):
        ar.community_alliance([EDEN, NIA], 'Eden', current, UUID)


def test_guildless_or_non_allied_player_stays_community():
    assert ar.community_alliance([EDEN], 'Other', profile('Other', 'OTHER'), UUID) is None
    assert ar.community_alliance([EDEN], 'None', {'uuid': UUID, 'guild': None}, UUID) is None


def test_api_failure_uses_exact_declared_guild():
    assert ar.community_alliance([EDEN], 'edn', False, UUID) == EDEN
    assert ar.community_alliance([EDEN], 'maybe Eden?', False, UUID) is None


def test_ambiguous_prefix_needs_review():
    duplicate = Alliance(3, 'Different', 'EDN', 789, 'Horizon', 'ally')
    with pytest.raises(ar.AllyRegistrationError, match='more than one'):
        ar.community_alliance([EDEN, duplicate], 'EDN', False, UUID)


def test_wrong_player_profile_cannot_grant_a_role():
    with pytest.raises(ar.AllyRegistrationError, match='different account'):
        ar.community_alliance([EDEN], 'EDN', {**profile(), 'uuid': 'different'}, UUID)


def setup_community(monkeypatch, player_profile=None):
    monkeypatch.setattr(ar, 'getPlayerUUID', lambda _: ('Canonical', UUID))
    monkeypatch.setattr(ar, 'getPlayerDatav3', lambda _: profile() if player_profile is None else player_profile)
    monkeypatch.setattr(ar, 'enabled_alliances', lambda: [EDEN, NIA])
    link = Mock()
    monkeypatch.setattr(ar, '_link_identity', link)
    register = AsyncMock(return_value=ar.RegisteredAlly(EDEN, 'Canonical', UUID))
    monkeypatch.setattr(ar, 'register_ally', register)
    member = SimpleNamespace(id=99, edit=AsyncMock())
    return member, register, link


def test_community_registers_ally_without_overwriting_ranked_nickname(monkeypatch):
    member, register, link = setup_community(monkeypatch)
    result = asyncio.run(ar.register_community_member(member, 99, 'Player', 'edn', actor_name='HR'))
    register.assert_awaited_once_with(member, 'Canonical', '2', actor_name='HR', identity=('Canonical', UUID))
    link.assert_called_once_with(99, 'Canonical', UUID)
    member.edit.assert_not_awaited()
    assert result == 'Registered as Horizon for Eden.'


def test_community_api_fallback_is_reported(monkeypatch):
    member, register, _ = setup_community(monkeypatch, False)
    result = asyncio.run(ar.register_community_member(member, 99, 'Player', 'edn', actor_name='HR'))
    register.assert_awaited_once()
    assert 'application guild' in result


def test_non_allied_community_gets_plain_nickname(monkeypatch):
    member, register, _ = setup_community(monkeypatch, profile('Other', 'OTHER'))
    result = asyncio.run(ar.register_community_member(member, 99, 'Player', 'Other', actor_name='HR'))
    register.assert_not_awaited()
    member.edit.assert_awaited_once_with(nick='Canonical')
    assert result == ''


def test_conflict_does_not_touch_discord_or_run_ally_registration(monkeypatch):
    member, register, link = setup_community(monkeypatch)
    link.side_effect = LinkConflictError(UUID, 100, 'Other')
    result = asyncio.run(ar.register_community_member(member, 99, 'Player', 'edn', actor_name='HR'))
    register.assert_not_awaited()
    member.edit.assert_not_awaited()
    assert 'already linked' in result


def test_unresolved_ign_does_not_link_or_grant_roles(monkeypatch):
    member, register, link = setup_community(monkeypatch)
    monkeypatch.setattr(ar, 'getPlayerUUID', lambda _: False)
    result = asyncio.run(ar.register_community_member(member, 99, 'Player', 'edn', actor_name='HR'))
    link.assert_not_called()
    register.assert_not_awaited()
    assert 'could not be resolved' in result


def test_missing_member_keeps_identity_but_reports_skipped_role(monkeypatch):
    _, register, link = setup_community(monkeypatch)
    result = asyncio.run(ar.register_community_member(None, 99, 'Player', 'edn', actor_name='HR'))
    link.assert_called_once()
    register.assert_not_awaited()
    assert 'not in the Discord server' in result


def test_ally_failure_is_reported_without_plain_nickname_overwrite(monkeypatch):
    member, register, _ = setup_community(monkeypatch)
    register.side_effect = ar.AllyRegistrationError('Needs review')
    assert asyncio.run(ar.register_community_member(member, 99, 'Player', 'edn', actor_name='HR')) == 'Needs review'
    member.edit.assert_not_awaited()


def test_save_persists_rank_and_alliance_in_one_transaction(monkeypatch):
    cursor = Mock()
    cursor.fetchone.side_effect = [(EDEN.rank, EDEN.role_id), (False,)]
    db = SimpleNamespace(cursor=cursor, connect=Mock(), close=Mock(), connection=SimpleNamespace(commit=Mock()))
    monkeypatch.setattr(ar, 'DB', lambda: db)
    monkeypatch.setattr(ar, 'assert_uuid_free', Mock())
    identity = Mock()
    rank = Mock()
    monkeypatch.setattr(ar, 'upsert_identity', identity)
    monkeypatch.setattr(ar, 'set_rank', rank)
    ar._save_registration(99, 'Canonical', UUID, EDEN)
    identity.assert_called_once_with(cursor, discord_id=99, ign='Canonical', uuid=UUID)
    rank.assert_called_once_with(cursor, 99, 'Horizon', alliance_id=2)
    db.connection.commit.assert_called_once()
    db.close.assert_called_once()


def test_save_rejects_disabled_or_changed_configuration(monkeypatch):
    db = Mock()
    db.cursor.fetchone.return_value = None
    monkeypatch.setattr(ar, 'DB', lambda: db)
    with pytest.raises(ar.AllyRegistrationError, match='configuration changed'):
        ar._save_registration(99, 'Canonical', UUID, EDEN)
    db.connection.commit.assert_not_called()
    db.close.assert_called_once()


def test_taq_members_cannot_be_overwritten_as_allies(monkeypatch):
    cursor = Mock()
    cursor.fetchone.return_value = (True,)
    monkeypatch.setattr(ar, 'assert_uuid_free', Mock())
    with pytest.raises(ar.AllyRegistrationError, match='TAq member'):
        ar._check_identity(cursor, 99, UUID)


@pytest.mark.parametrize('source', ['discord', 'website'])
def test_both_acceptance_handlers_keep_community_role_and_use_shared_registration(monkeypatch, source):
    from datetime import datetime, timezone
    from Commands import app_commands
    from Tasks import process_website_decisions

    community_role = SimpleNamespace(name='Tortoise - Community')
    guild = SimpleNamespace(id=1, roles=[community_role])
    channel = SimpleNamespace(id=10, guild=guild, send=AsyncMock(), edit=AsyncMock())
    member = SimpleNamespace(id=99, mention='<@99>', add_roles=AsyncMock(), edit=AsyncMock())
    client = SimpleNamespace(get_guild=lambda _: guild)
    shared = AsyncMock(return_value='Registered as Horizon for Eden.')
    if source == 'discord':
        monkeypatch.setattr(app_commands, 'register_community_member', shared)
        monkeypatch.setattr(app_commands, 'update_web_poll_embed', AsyncMock())
        cog = app_commands.WebAppCommands(client)
        cog._db_accept = Mock()
        cog._update_exec_thread = AsyncMock()
        ctx = SimpleNamespace(user=SimpleNamespace(name='HR'), followup=SimpleNamespace(send=AsyncMock()))
        app = {'id': 3, 'discord_id': '99', 'thread_id': 20, 'answers': {'ign': 'Player', 'guild': 'EDN'}}
        asyncio.run(cog._accept_community(ctx, channel, app, member, 'Player', datetime.now(timezone.utc)))
        shared.assert_awaited_once_with(member, 99, 'Player', 'EDN', actor_name='HR')
    else:
        monkeypatch.setattr(process_website_decisions, 'register_community_member', shared)
        monkeypatch.setattr(process_website_decisions, 'update_web_poll_embed', AsyncMock())
        cog = process_website_decisions.ProcessWebsiteDecisions(client)
        cog._update_exec_thread = AsyncMock()
        asyncio.run(cog._accept_community(3, channel, member, '99', 'Applicant', 'Player', 20,
                                         answers={'guild': 'EDN'}, display_number=4))
        shared.assert_awaited_once_with(member, 99, 'Player', 'EDN', actor_name='website acceptance')
    assert member.add_roles.await_args.args[0] is community_role
    member.edit.assert_not_awaited()


@pytest.fixture
def linked_ally_db(temp_db, monkeypatch):
    temp_db.cursor.execute('''INSERT INTO guild_alliances
        (id, guild_name, guild_prefix, discord_role_id, display_rank, enabled)
        VALUES (2, 'Eden', 'EDN', 123, 'Horizon', TRUE)''')
    monkeypatch.setattr(ar, 'DB', lambda: temp_db)
    return temp_db


def test_database_registration_records_guild_and_rank(linked_ally_db):
    ar._save_registration(99, 'Canonical', UUID, EDEN)
    cur = linked_ally_db.cursor
    cur.execute('SELECT ign, uuid::text, rank, alliance_id FROM discord_links WHERE discord_id = 99')
    assert cur.fetchone() == ('Canonical', UUID, 'Horizon', 2)
    ar._save_registration(99, 'Canonical', UUID, EDEN)
    cur.execute('SELECT count(*) FROM discord_links')
    assert cur.fetchone()[0] == 1


def test_database_identity_rename_preserves_guild_but_relink_clears_it(linked_ally_db):
    from Helpers.registration import upsert_identity

    ar._save_registration(99, 'Canonical', UUID, EDEN)
    cur = linked_ally_db.cursor
    upsert_identity(cur, discord_id=99, ign='Renamed', uuid=UUID)
    cur.execute('SELECT alliance_id FROM discord_links WHERE discord_id = 99')
    assert cur.fetchone() == (2,)
    upsert_identity(cur, discord_id=99, ign='Different', uuid='9aeb062a-f769-49bc-8046-4d9c8cc86e5a')
    cur.execute('SELECT alliance_id FROM discord_links WHERE discord_id = 99')
    assert cur.fetchone() == (None,)


def test_database_member_rank_clears_guild_and_prevents_ally_overwrite(linked_ally_db):
    from Helpers.registration import set_rank

    ar._save_registration(99, 'Canonical', UUID, EDEN)
    cur = linked_ally_db.cursor
    set_rank(cur, 99, 'Starfish')
    cur.execute('SELECT rank, alliance_id FROM discord_links WHERE discord_id = 99')
    assert cur.fetchone() == ('Starfish', None)
    with pytest.raises(ar.AllyRegistrationError, match='TAq member'):
        ar._save_registration(99, 'Canonical', UUID, EDEN)
