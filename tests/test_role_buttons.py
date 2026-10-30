"""Tests for panel role buttons (TAQ-114).

What matters here:

1. A role button carries its role (and optional required role) in its
   custom_id, round-trips through parse_role_button, and nothing else parses.
2. The safety rule refuses @everyone, bot-managed roles, roles with any
   permissions, and roles at or above Tort's own top role, everywhere it runs:
   the registry flag, the publish check and the click.
3. The toggle honours the required role and replies privately.
4. The seeded panels use role buttons: Role Info keeps its 25 buttons as they
   look today, and Military Roles gets Territory Munching and Soloer.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import bot_panels as bp  # noqa: E402
from Helpers import role_buttons as rb  # noqa: E402

ROLE, GATE = 1311125265153265795, 1154999995741769728


class FakeRole:
    def __init__(self, rid, position=5, perms=0, managed=False, default=False, name="Role"):
        self.id, self.position, self.managed, self.name = rid, position, managed, name
        self.permissions = discord.Permissions(perms)
        self._default = default

    def is_default(self):
        return self._default

    def __ge__(self, other):
        return self.position >= other.position


def render(rows):
    async def go():
        return bp.render_message(0, {"embeds": [{"description": "x"}], "rows": rows},
                                 actions={}, values={}, fetch_asset={}.get)
    return asyncio.run(go())


def ids(view):
    return [c["custom_id"] for row in view.to_components() for c in row["components"]]


def test_custom_id_round_trip():
    assert bp.role_button_custom_id(ROLE) == f"role:{ROLE}"
    assert bp.role_button_custom_id(ROLE, GATE) == f"role:{ROLE}:{GATE}"
    assert bp.parse_role_button(f"role:{ROLE}") == (ROLE, None)
    assert bp.parse_role_button(f"role:{ROLE}:{GATE}") == (ROLE, GATE)
    for other in ("role_info:reaction_crafter", "ticket_open:war", "role:abc", f"role:{ROLE}:{GATE}:1", "role:", "roles:1"):
        assert bp.parse_role_button(other) is None, other


def test_role_buttons_render_with_their_role_in_the_custom_id():
    r = render([[{"kind": "role", "role_id": str(ROLE), "label": "Territory Munching", "emoji": "🍃"},
                 {"kind": "role", "role_id": str(GATE), "requires_role_id": str(ROLE), "label": "Soloer"}]])
    assert ids(r.view) == [f"role:{ROLE}", f"role:{GATE}:{ROLE}"]


@pytest.mark.parametrize("button, error", [
    ({"kind": "role", "label": "x"}, "pick the role"),
    ({"kind": "role", "role_id": "nope", "label": "x"}, "pick the role"),
    ({"kind": "role", "role_id": str(ROLE)}, "label or an emoji"),
    ({"kind": "role", "role_id": str(ROLE), "requires_role_id": "x", "label": "x"}, "required role"),
])
def test_bad_role_buttons_are_refused(button, error):
    with pytest.raises(bp.PanelError, match=error):
        render([[button]])


def test_the_same_role_twice_in_one_message_is_refused():
    with pytest.raises(bp.PanelError, match="twice"):
        render([[{"kind": "role", "role_id": str(ROLE), "label": "a"}, {"kind": "role", "role_id": str(ROLE), "label": "b"}]])


def test_safety_rule():
    top = FakeRole(1, position=10)
    assert rb.unassignable_reason(FakeRole(2), top) is None
    assert "everyone" in rb.unassignable_reason(FakeRole(2, default=True), top)
    assert "manages" in rb.unassignable_reason(FakeRole(2, managed=True), top)
    assert "permissions" in rb.unassignable_reason(FakeRole(2, perms=8), top)
    assert "above" in rb.unassignable_reason(FakeRole(2, position=10), top)
    assert "above" in rb.unassignable_reason(FakeRole(2), None)


def _guild(*roles, top=10):
    g = MagicMock()
    g.name = "Test"
    by_id = {r.id: r for r in roles}
    g.get_role = lambda i: by_id.get(i)
    g.me.top_role = FakeRole(1, position=top)
    return g


def test_publish_check_refuses_unsafe_or_missing_roles():
    from Tasks.publish_bot_panels import check_role_buttons
    ok, admin = FakeRole(ROLE, name="Territory Munching"), FakeRole(GATE, perms=8, name="Soloer")
    content = lambda *b: {"messages": [{"rows": [list(b)]}]}
    check_role_buttons(content({"kind": "role", "role_id": str(ROLE)}), _guild(ok))
    with pytest.raises(bp.PanelError, match="grants permissions"):
        check_role_buttons(content({"kind": "role", "role_id": str(GATE)}), _guild(ok, admin))
    with pytest.raises(bp.PanelError, match="isn't in"):
        check_role_buttons(content({"kind": "role", "role_id": "999999999999999999"}), _guild(ok))
    with pytest.raises(bp.PanelError, match="requires role"):
        check_role_buttons(content({"kind": "role", "role_id": str(ROLE), "requires_role_id": "999999999999999999"}), _guild(ok))


def _interaction(guild, member_roles):
    i = MagicMock()
    i.guild = guild
    i.response.defer = AsyncMock()
    i.followup.send = AsyncMock()
    member = MagicMock(spec=discord.Member)
    member.roles = member_roles
    member.add_roles, member.remove_roles = AsyncMock(), AsyncMock()
    i.user = member
    guild.me.guild_permissions.manage_roles = True
    return i, member


def _reply(i):
    return i.followup.send.await_args.args[0]


def test_toggle_adds_removes_and_honours_the_gate():
    role, gate = FakeRole(ROLE, name="Territory Munching"), FakeRole(GATE, name="Soloer")
    g = _guild(role, gate)

    i, m = _interaction(g, [])
    asyncio.run(rb.toggle_role(i, ROLE))
    m.add_roles.assert_awaited_once()
    assert _reply(i) == "Added Territory Munching."

    i, m = _interaction(g, [role])
    asyncio.run(rb.toggle_role(i, ROLE))
    m.remove_roles.assert_awaited_once()
    assert _reply(i) == "Removed Territory Munching."

    i, m = _interaction(g, [])
    asyncio.run(rb.toggle_role(i, ROLE, GATE))
    m.add_roles.assert_not_awaited()
    assert _reply(i) == "You need the Soloer role for this."


def test_toggle_refuses_an_unsafe_role_even_if_a_button_names_it():
    g = _guild(FakeRole(ROLE, perms=8))
    i, m = _interaction(g, [])
    asyncio.run(rb.toggle_role(i, ROLE))
    m.add_roles.assert_not_awaited()
    assert _reply(i) == "I can't hand out that role."


def test_listener_only_answers_role_buttons(monkeypatch):
    from Commands import role_buttons as cog_mod
    calls = []
    monkeypatch.setattr(cog_mod, "toggle_role", AsyncMock(side_effect=lambda *a: calls.append(a[1:])))
    cog = cog_mod.RoleButtons(None)
    for custom_id in (f"role:{ROLE}:{GATE}", "ticket_open:war", "role_info:reaction_crafter"):
        i = MagicMock(type=discord.InteractionType.component, data={"custom_id": custom_id})
        asyncio.run(cog.on_interaction(i))
    assert calls == [(ROLE, GATE)]


def _seeded(monkeypatch, slug):
    import scripts.seed_bot_panels as seed
    monkeypatch.setattr(seed, "get_shell_exchange_config", lambda: {})
    [panel] = [p for p in seed.build_panels(seed.AssetUploader(dry_run=True)) if p["slug"] == slug]
    return panel


def test_role_info_buttons_are_role_buttons_that_look_the_same(monkeypatch):
    from Helpers import local_ids
    from scripts.seed_bot_panels import ROLE_INFO_BUTTONS
    data = [*ROLE_INFO_BUTTONS["reaction_roles.json"], *ROLE_INFO_BUTTONS["ping_roles.json"]]
    panel = _seeded(monkeypatch, "role-info")
    buttons = bp.role_buttons_in(panel["content"])
    assert len(buttons) == len(data) == 25
    assert [b["label"] for b in buttons] == [label for label, *_ in data]
    assert [b["role_id"] for b in buttons] == [str(local_ids.role(role_id)) for _, role_id, *_ in data]
    assert not any(b.get("kind") == "action" and b.get("action", "").startswith("role:")
                   for m in panel["content"]["messages"] for row in m.get("rows") or [] for b in row)


def test_military_roles_panel(monkeypatch):
    from Helpers import local_ids
    panel = _seeded(monkeypatch, "military-roles")
    [msg] = panel["content"]["messages"]
    assert [e.get("title") for e in msg["embeds"]][:2] == ["𓆉  __Class roles__", "𓆉  __Military roles__"]
    assert "Use the buttons below" in msg["embeds"][2]["footer"]["text"]
    assert [(b["label"], b["role_id"]) for b in bp.role_buttons_in(panel["content"])] == [
        ("Territory Munching", str(local_ids.role(ROLE))), ("Soloer", str(local_ids.role(GATE)))]
