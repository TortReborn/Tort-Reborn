import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Commands import register
from Helpers import alliances, ally_registration
from Helpers.alliances import Alliance


EDEN = Alliance(2, 'Eden', 'EDN', 123, 'Horizon', 'ally')
NIA = Alliance(1, 'Nerfuria', 'NIA', 456, 'Navigator', 'ally')


def test_autocomplete_reads_current_alliances_and_filters_prefix(monkeypatch):
    current = [NIA]
    monkeypatch.setattr(register, 'enabled_alliances', lambda: current[:])
    ctx = SimpleNamespace(value='')
    assert [c.value for c in asyncio.run(register._alliance_autocomplete(ctx))] == ['1']
    current.append(EDEN)
    ctx.value = ' edn '
    choices = asyncio.run(register._alliance_autocomplete(ctx))
    assert [(c.name, c.value) for c in choices] == [('Eden [EDN]', '2')]
    current.remove(EDEN)
    assert asyncio.run(register._alliance_autocomplete(ctx)) == []


def test_autocomplete_limits_choices(monkeypatch):
    monkeypatch.setattr(register, 'enabled_alliances', lambda: [EDEN] * 30)
    assert len(asyncio.run(register._alliance_autocomplete(SimpleNamespace(value='')))) == 25
    option = next(o for o in register.Register.ally.options if o.name == 'guild')
    assert option.autocomplete is register._alliance_autocomplete
    assert not option.choices


def setup_registration(monkeypatch, current):
    monkeypatch.setattr(ally_registration, 'enabled_alliances', lambda: current)
    monkeypatch.setattr(register.Register, '_is_moderator_or_higher', lambda *_: True)
    cog = register.Register(None)
    cog._check_link_conflict = Mock()
    cog._upsert_ally_link = Mock()
    monkeypatch.setattr(ally_registration, '_check_registration', cog._check_link_conflict)
    monkeypatch.setattr(ally_registration, '_save_registration', cog._upsert_ally_link)
    role = object()
    ctx = SimpleNamespace(
        defer=AsyncMock(), followup=SimpleNamespace(send=AsyncMock()),
        user=SimpleNamespace(name='HR'), guild=SimpleNamespace(get_role=Mock(return_value=role)),
    )
    member = SimpleNamespace(id=99, mention='<@99>', guild=ctx.guild, roles=[], add_roles=AsyncMock(), edit=AsyncMock())
    return cog, ctx, member, role


@pytest.mark.parametrize('current,selected', [([], '2'), ([EDEN], 'Eden'), ([EDEN], '999')])
def test_registration_rejects_disabled_removed_or_unselected_guild(monkeypatch, current, selected):
    cog, ctx, member, _ = setup_registration(monkeypatch, current)
    asyncio.run(register.Register.ally.callback(cog, ctx, member, 'Player', selected))
    member.add_roles.assert_not_awaited()
    member.edit.assert_not_awaited()
    cog._upsert_ally_link.assert_not_called()
    assert 'enabled guild' in ctx.followup.send.call_args.args[0]


@pytest.mark.parametrize('rank_kind', [None, 'member'])
def test_registration_rejects_invalid_rank_before_discord_changes(monkeypatch, rank_kind):
    invalid = Alliance(2, 'Eden', 'EDN', 123, 'Horizon', rank_kind)
    cog, ctx, member, _ = setup_registration(monkeypatch, [invalid])
    asyncio.run(register.Register.ally.callback(cog, ctx, member, 'Player', '2'))
    member.add_roles.assert_not_awaited()
    member.edit.assert_not_awaited()
    cog._upsert_ally_link.assert_not_called()
    assert 'valid ally rank' in ctx.followup.send.call_args.args[0]


@pytest.mark.parametrize('alliance', [EDEN, NIA])
def test_registration_uses_configured_role_and_rank(monkeypatch, alliance):
    cog, ctx, member, role = setup_registration(monkeypatch, [alliance])
    monkeypatch.setattr(ally_registration, 'getPlayerUUID', lambda _: ('Canonical', 'uuid'))
    asyncio.run(register.Register.ally.callback(cog, ctx, member, 'Player', str(alliance.id)))
    ctx.guild.get_role.assert_called_once_with(alliance.role_id)
    member.add_roles.assert_awaited_once_with(
        role, reason=f'Ally registration for {alliance.name} (ran by HR)', atomic=True,
    )
    member.edit.assert_awaited_once_with(nick=f'{alliance.rank} Canonical')
    cog._upsert_ally_link.assert_called_once_with(99, 'Canonical', 'uuid', alliance)


def test_enabled_alliances_query_refreshes_enabled_and_rank_state(temp_db, monkeypatch):
    cur = temp_db.cursor
    cur.execute('''CREATE TEMP TABLE IF NOT EXISTS guild_alliances (
        id BIGINT, guild_name TEXT, guild_prefix TEXT, discord_role_id BIGINT,
        display_rank TEXT, enabled BOOLEAN)''')
    cur.execute('TRUNCATE guild_alliances')
    cur.execute('''INSERT INTO guild_alliances (id, guild_name, guild_prefix, discord_role_id, display_rank, enabled) VALUES
        (1, 'Nerfuria', 'NIA', 456, 'Navigator', TRUE),
        (2, 'Eden', 'EDN', 123, 'Missing Rank', TRUE),
        (3, 'Disabled', 'OFF', 789, 'Navigator', FALSE)''')
    monkeypatch.setattr(alliances, 'DB', lambda: temp_db)
    result = alliances.enabled_alliances()
    assert [a.name for a in result] == ['Eden', 'Nerfuria']
    assert result[0].rank_kind is None
    assert result[1] == NIA
    cur.execute('UPDATE guild_alliances SET enabled = FALSE WHERE id = 2')
    assert alliances.enabled_alliances() == [NIA]
