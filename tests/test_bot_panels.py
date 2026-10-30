"""Bot panel rendering (Helpers/bot_panels.py): the website's JSON in,
Discord-ready content out, with every limit enforced before Discord sees it."""

import asyncio

import discord
import pytest

from Helpers.bot_panels import (
    DYNAMIC_SHELL_EXCHANGE,
    PanelAction,
    PanelError,
    panel_messages,
    render_message,
)

ACTIONS = {
    "apply_guild": PanelAction("apply_guild", "Guild Member Application", "Applications", "\U0001F420", "primary"),
    "role:ping_notg": PanelAction("role_info:ping_notg", "NOTG", "Ping roles", "<:notg:1316539942524031017>"),
}
VALUES = {"shells_per_aspect": "3", "max_aspects_per_conversion": "40"}
ASSET = "0123456789abcdef.png"


def render(message, assets=None, **kw):
    async def go():  # Views want a running loop
        return render_message(
            0, message, actions=ACTIONS, values=VALUES,
            fetch_asset=(assets or {ASSET: b"png-bytes"}).get, **kw,
        )
    return asyncio.run(go())


def test_embed_text_values_and_color():
    out = render({"embeds": [{"title": "Rate", "description": "  {{shells_per_aspect}} <:shells:1>, max {{ max_aspects_per_conversion }} ", "color": 0x94C1FF}]})
    [embed] = out.embeds
    assert embed.title == "Rate"
    assert embed.description == "3 <:shells:1>, max 40"
    assert embed.color.value == 0x94C1FF
    assert out.view is None and out.files == []


def test_values_fill_buttons_and_fields_too():
    out = render({"embeds": [{"fields": [{"name": "Rate", "value": "{{shells_per_aspect}} each"}]}],
                  "rows": [[{"kind": "link", "label": "Max {{max_aspects_per_conversion}}", "url": "https://a.b"}]]})
    assert out.embeds[0].fields[0].value == "3 each"
    assert out.view.children[0].label == "Max 40"


def test_unknown_value_stops_the_publish():
    with pytest.raises(PanelError, match=r"Message 1: \{\{shell_emoji\}\} isn't a value"):
        render({"content": "{{shell_emoji}} hi"})


def test_message_images_are_attached_and_embed_assets_point_at_them():
    out = render({
        "images": [f"asset://{ASSET}"],
        "embeds": [{"description": "x", "image": f"asset://{ASSET}", "thumbnail": "https://example.com/a.png"}],
    })
    assert [f.filename for f in out.files] == [ASSET]  # attached once
    assert out.embeds[0].image.url == f"attachment://{ASSET}"
    assert out.embeds[0].thumbnail.url == "https://example.com/a.png"


def test_missing_asset_is_a_clear_error():
    with pytest.raises(PanelError, match="missing from storage"):
        render({"images": [f"asset://{ASSET}"]}, assets={"x": b""})


def test_message_images_must_be_uploaded():
    with pytest.raises(PanelError, match="must be uploaded"):
        render({"images": ["https://example.com/banner.png"]})


def test_non_https_image_rejected():
    with pytest.raises(PanelError, match="https://"):
        render({"embeds": [{"description": "x", "image": "javascript:alert(1)"}]})


def test_action_buttons_use_registry_custom_ids_with_overrides():
    out = render({"content": "hi", "rows": [[
        {"kind": "action", "action": "apply_guild"},
        {"kind": "action", "action": "role:ping_notg", "label": "Night of the Grootslangs", "style": "success"},
        {"kind": "link", "label": "Site", "url": "https://the-aquarium.com"},
    ]]})
    apply_btn, role_btn, link_btn = out.view.children
    assert apply_btn.custom_id == "apply_guild" and apply_btn.label == "Guild Member Application"
    assert apply_btn.style == discord.ButtonStyle.primary
    assert role_btn.custom_id == "role_info:ping_notg" and role_btn.label == "Night of the Grootslangs"
    assert role_btn.style == discord.ButtonStyle.success and role_btn.emoji.id == 1316539942524031017
    assert link_btn.url == "https://the-aquarium.com" and link_btn.style == discord.ButtonStyle.link


def test_unknown_action_rejected():
    with pytest.raises(PanelError, match="not a bot action"):
        render({"content": "x", "rows": [[{"kind": "action", "action": "role:admin"}]]})


def test_same_action_twice_rejected():
    with pytest.raises(PanelError, match="twice"):
        render({"content": "x", "rows": [[{"kind": "action", "action": "apply_guild"}], [{"kind": "action", "action": "apply_guild"}]]})


def test_limits_name_the_spot():
    with pytest.raises(PanelError, match=r"Message 1, embed 2: description is 4097"):
        render({"embeds": [{"description": "ok"}, {"description": "x" * 4097}]})
    with pytest.raises(PanelError, match="field 1: a field needs both"):
        render({"embeds": [{"fields": [{"name": "n", "value": ""}]}]})
    with pytest.raises(PanelError, match="6000"):
        render({"embeds": [{"description": "x" * 4000}, {"description": "y" * 4000}]})
    with pytest.raises(PanelError, match="6 buttons"):
        render({"content": "x", "rows": [[{"kind": "link", "label": str(i), "url": "https://a.b"} for i in range(6)]]})


def test_empty_message_rejected():
    with pytest.raises(PanelError, match="empty"):
        render({"content": "   "})


def test_dynamic_block_appends_generated_images():
    out = render(
        {"embeds": [{"description": "rates", "color": 0x123456}], "dynamic": DYNAMIC_SHELL_EXCHANGE},
        dynamic_files={DYNAMIC_SHELL_EXCHANGE: [("ingredient_shell_panel.png", b"a"), ("materials_shell_panel.png", b"b")]},
    )
    assert len(out.embeds) == 3
    assert out.embeds[1].image.url == "attachment://ingredient_shell_panel.png"
    assert out.embeds[2].color.value == 0x123456
    assert sorted(f.filename for f in out.files) == ["ingredient_shell_panel.png", "materials_shell_panel.png"]


def test_fresh_files_can_be_resent():
    out = render({"images": [f"asset://{ASSET}"]})
    assert out.fresh_files()[0].fp.read() == b"png-bytes"


def test_panel_messages_bounds():
    with pytest.raises(PanelError):
        panel_messages({"messages": []})
    with pytest.raises(PanelError):
        panel_messages({"messages": [{"content": "x"}] * 11})


def test_seeded_panels_render(monkeypatch):
    """Every panel the seed script writes must publish as-is."""
    import scripts.seed_bot_panels as seed
    from Commands.generate import PANEL_ACTIONS
    from Helpers.panel_values import VALUES as KNOWN
    values = {key: "1" for key in KNOWN}  # every known value, and only those

    monkeypatch.setattr(seed, "get_shell_exchange_config", lambda: {})
    uploaded = {}

    def fake_upload(path):
        name = seed.AssetUploader(dry_run=True)(path)
        uploaded[name.removeprefix("asset://")] = b"img"
        return name

    async def go():
        for panel in seed.build_panels(fake_upload):
            for i, m in enumerate(panel_messages(panel["content"])):
                render_message(
                    i, m, actions=PANEL_ACTIONS, values=values, fetch_asset=uploaded.get,
                    dynamic_files={DYNAMIC_SHELL_EXCHANGE: [("ingredient_shell_panel.png", b"a")]},
                )
    asyncio.run(go())


def test_panel_cogs_import():
    """Cog modules build their slash commands at import; a bad decorator only shows up here."""
    import importlib
    for mod in ("Commands.generate", "Commands.shell_exchange", "Tasks.publish_bot_panels"):
        importlib.import_module(mod)


def test_shell_conversion_falls_back_on_bad_values():
    """The rate is edited on the website; a bad value must never reach the conversion."""
    from Helpers.database import SHELL_CONVERSION_DEFAULTS, clean_shell_conversion
    assert clean_shell_conversion({}) == SHELL_CONVERSION_DEFAULTS
    assert clean_shell_conversion({"shells_per_aspect": 5, "max_aspects_per_conversion": 20}) == {
        "shells_per_aspect": 5, "max_aspects_per_conversion": 20}
    for bad in (0, -1, 10001, "5", 2.5, True, None):
        assert clean_shell_conversion({"shells_per_aspect": bad})["shells_per_aspect"] == 3


def test_box_layout_is_one_v2_container():
    """layout "box": text and one full-width gallery per image in a coloured
    container, buttons after it; the way /shell_exchange generate posted."""
    out = render(
        {"layout": "box", "content": "Rates: {{shells_per_aspect}} each", "color": 0x123456,
         "images": [f"asset://{ASSET}"], "dynamic": DYNAMIC_SHELL_EXCHANGE,
         "rows": [[{"kind": "action", "action": "apply_guild"}]]},
        dynamic_files={DYNAMIC_SHELL_EXCHANGE: [("ingredient_shell_panel.png", b"a")]},
    )
    assert out.box and out.content is None and out.embeds == []
    container, row = out.view.to_components()
    assert container["type"] == 17 and container["accent_color"] == 0x123456
    assert [c["type"] for c in container["components"]] == [10, 12, 12]  # text, gallery, gallery
    assert container["components"][0]["content"] == "Rates: 3 each"
    assert container["components"][2]["items"][0]["media"]["url"] == "attachment://ingredient_shell_panel.png"
    assert row["type"] == 1 and row["components"][0]["custom_id"] == "apply_guild"
    assert sorted(f.filename for f in out.files) == [ASSET, "ingredient_shell_panel.png"]
    assert out.copy_embed.description == "Rates: 3 each"  # for the legacy webhook


def test_box_layout_limits():
    with pytest.raises(PanelError, match="can't also have embeds"):
        render({"layout": "box", "content": "x", "embeds": [{"description": "y"}]})
    with pytest.raises(PanelError, match="text is 4001"):
        render({"layout": "box", "content": "x" * 4001})
    with pytest.raises(PanelError, match="empty"):
        render({"layout": "box", "content": "  "})


def test_seeded_shell_exchange_is_a_box(monkeypatch):
    import scripts.seed_bot_panels as seed
    monkeypatch.setattr(seed, "get_shell_exchange_config", lambda: {})
    [panel] = [p for p in seed.build_panels(seed.AssetUploader(dry_run=True)) if p["slug"] == "shell-exchange"]
    assert panel["content"]["messages"][0]["layout"] == "box"


# ---- Archived forum threads (the Shells -> Aspects panel lives in one) ----

def _thread(tid, archived=False, locked=False):
    from unittest.mock import AsyncMock, MagicMock
    t = MagicMock(spec=discord.Thread)
    t.id, t.name, t.archived, t.locked = tid, f"thread {tid}", archived, locked
    t.edit = AsyncMock()
    return t


def _publisher(fresh):
    from unittest.mock import AsyncMock, MagicMock
    from Tasks.publish_bot_panels import PublishBotPanels
    cog = PublishBotPanels.__new__(PublishBotPanels)
    cog.client = MagicMock()
    cog.client.fetch_channel = AsyncMock(side_effect=lambda i: fresh[i])
    return cog


def test_archived_thread_is_opened_for_the_publish_and_archived_again():
    fresh = {1: _thread(1, archived=True)}
    cog = _publisher(fresh)

    async def go():
        async with cog._open_threads(_thread(1)):  # the cached copy may say it's open
            fresh[1].edit.assert_awaited_once_with(archived=False)
    asyncio.run(go())
    assert [c.kwargs for c in fresh[1].edit.await_args_list] == [{"archived": False}, {"archived": True}]


def test_open_threads_and_plain_channels_are_left_alone():
    fresh = {2: _thread(2)}
    cog = _publisher(fresh)

    async def go():
        async with cog._open_threads(_thread(2), object()):
            pass
    asyncio.run(go())
    fresh[2].edit.assert_not_awaited()


def test_a_locked_thread_stops_the_publish():
    from Helpers.bot_panels import PanelError
    fresh = {3: _thread(3, archived=True, locked=True)}
    cog = _publisher(fresh)

    async def go():
        async with cog._open_threads(_thread(3)):
            pass
    with pytest.raises(PanelError, match="locked"):
        asyncio.run(go())
    fresh[3].edit.assert_not_awaited()


def test_the_thread_is_archived_again_even_when_the_publish_fails():
    fresh = {4: _thread(4, archived=True)}
    cog = _publisher(fresh)

    async def go():
        async with cog._open_threads(_thread(4)):
            raise RuntimeError("Discord said no")
    with pytest.raises(RuntimeError):
        asyncio.run(go())
    assert fresh[4].edit.await_args_list[-1].kwargs == {"archived": True}


# ---- Ticket posts as panels (TAQ-113) ----

def _seeded(monkeypatch, slug):
    import scripts.seed_bot_panels as seed
    monkeypatch.setattr(seed, "get_shell_exchange_config", lambda: {})
    [panel] = [p for p in seed.build_panels(seed.AssetUploader(dry_run=True)) if p["slug"] == slug]
    return panel


def _buttons(view):
    return [(c["custom_id"], c.get("label"), c.get("style"), (c.get("emoji") or {}).get("name"))
            for row in view.to_components() for c in row.get("components", []) if c.get("type") == 2]


def test_ticket_buttons_are_panel_actions_with_the_old_look():
    """Same custom_id, label, style and emoji /tickets post used, so adopting
    the live posts changes nothing and TicketOpenView still answers them."""
    from Commands.generate import PANEL_ACTIONS
    from Commands.tickets import TicketOpenView

    war, shell = PANEL_ACTIONS["ticket_open:war"], PANEL_ACTIONS["ticket_open:shell"]
    assert (war.label, war.style, war.emoji) == ("Create ticket", "secondary", "\U0001F4E9")
    assert (shell.label, shell.style, shell.emoji) == ("Create Ticket", "primary", "\U0001F4E9")

    async def go():
        return {i.custom_id for i in TicketOpenView().children}
    assert asyncio.run(go()) >= {"ticket_open:war", "ticket_open:shell"}


def test_seeded_war_ticket_is_the_old_post(monkeypatch):
    from Commands.generate import PANEL_ACTIONS
    from Commands.tickets import TICKET_EMBED_COLOR
    panel = _seeded(monkeypatch, "war-ticket")

    async def go():
        return render_message(0, panel_messages(panel["content"])[0], actions=PANEL_ACTIONS,
                              values=VALUES, fetch_asset={}.get)
    r = asyncio.run(go())
    [embed] = r.embeds
    assert (embed.title, embed.color.value) == ("War Ticket", TICKET_EMBED_COLOR)
    assert [f.name for f in embed.fields] == ["War Training", "Role Request", "War Build Request"]
    assert _buttons(r.view) == [("ticket_open:war", "Create ticket", 2, "\U0001F4E9")]


def test_shell_ticket_button_is_on_the_shell_exchange_box(monkeypatch):
    from Commands.generate import PANEL_ACTIONS
    panel = _seeded(monkeypatch, "shell-exchange")
    assert panel["content"]["messages"][0]["rows"] == [[{"kind": "action", "action": "ticket_open:shell"}]]

    async def go():
        return render_message(0, panel_messages(panel["content"])[0], actions=PANEL_ACTIONS, values=VALUES,
                              fetch_asset={}.get, dynamic_files={DYNAMIC_SHELL_EXCHANGE: [("i.png", b"a")]})
    r = asyncio.run(go())
    assert r.box and ("ticket_open:shell", "Create Ticket", 1, "\U0001F4E9") in _buttons(r.view)


def test_tickets_post_is_retired_but_the_counters_stay():
    from Commands.tickets import Tickets
    names = {c.name for c in Tickets.tickets.subcommands}
    assert "post" not in names and {"counters", "set-counter"} <= names


def test_ticket_staff_roles_follow_the_test_servers():
    """Under the dev bot the maincord staff roles translate to Tort Test's copies."""
    import json
    from Commands import tickets
    from Helpers import local_ids
    with open(local_ids.MAP_FILE, encoding="utf-8") as f:
        roles = json.load(f)["roles"]
    for prod_id, current in ((741298208470466592, tickets.MODERATOR_ROLE_ID),
                             (754735279692054628, tickets.SR_MODERATOR_ROLE_ID),
                             (1033420156690513970, tickets.WAR_TRAINER_ROLE_ID),
                             (1332814674869096498, tickets.SHELL_MANAGER_ROLE_ID)):
        assert current == int(roles[str(prod_id)])
