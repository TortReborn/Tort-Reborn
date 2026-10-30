"""Tests for scripts/server_parity.py (TAQ-110), on made-up servers only.

What matters here:

1. Nothing can be written to the real servers: the guard refuses them, and
   the client refuses any write unless it was opened for writing.
2. Matching ignores case, emoji and separators, is one-to-one, and renames in
   place instead of recreating (ids survive).
3. Announcement channels become text channels; a real type change recreates
   the channel and archives the old one exactly once.
4. Per-member channels, archived prod categories and bot-managed roles are
   never copied.
5. Test-only channels go to Dev when the bot uses them, Archive otherwise;
   overwrites for single users and test-only roles are left alone.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import server_parity as sp  # noqa: E402

PROD, TEST = "100", "200"


def role(i, name, pos, managed=False, perms="0", color=0):
    return {"id": str(i), "name": name, "position": pos, "managed": managed, "permissions": perms,
            "color": color, "hoist": False, "mentionable": False}


def chan(i, name, type_=sp.TEXT, parent=None, pos=0, overwrites=(), **kw):
    return {"id": str(i), "name": name, "type": type_, "parent_id": parent, "position": pos,
            "permission_overwrites": list(overwrites), **kw}


def ow(target, type_=0, allow="1024", deny="0"):
    return {"id": str(target), "type": type_, "allow": allow, "deny": deny}


def prod_guild():
    return {"id": PROD, "roles": [
        role(PROD, "@everyone", 0, perms="104"),
        role(11, "Narwhal", 3, perms="8", color=5),
        role(12, "🏆 CONTRIBUTION ROLES", 2),
        role(13, "Dyno", 4, managed=True),
        role(14, "Sailfish", 1),
    ], "channels": [
        chan(21, "🔥 Raids", sp.CATEGORY, pos=1),
        chan(22, "Archives", sp.CATEGORY, pos=9),
        chan(23, "🔥｜raid-collecting", parent="21", pos=0, overwrites=[ow(11), ow(999, type_=1), ow(13)]),
        chan(24, "📢｜guild-announcements", sp.ANNOUNCEMENT, parent="21", pos=1),
        chan(25, "🔥｜raid-guide", sp.FORUM, parent="21", pos=2, available_tags=[{"id": "1", "name": "Notg"}]),
        chan(29, "Guild Applications", sp.CATEGORY, pos=2),
        chan(26, "ticket-0182-somebody", parent="29", pos=0),
        chan(30, "season-2025", parent="21", pos=5),
        chan(27, "old-stuff", parent="22"),
        chan(28, "🔥 War", sp.VOICE, parent="21", pos=4, bitrate=256000),
    ]}


def fake_test_server():
    return {"id": TEST, "premium_tier": 0, "emojis": [], "roles": [
        role(TEST, "@everyone", 0, perms="104"),
        role(31, "Beta Tort Reborn", 6, managed=True),
        role(32, "CONTRIBUTION ROLES", 4),
        role(33, "narwhal", 3, perms="8", color=5),
        role(34, "Moner", 1),
        role(35, "Beta Tort (Thundderr)", 2, managed=True),
    ], "channels": [
        chan(41, "raids", sp.CATEGORY, pos=0),
        chan(42, "raid-collecting", parent="41", overwrites=[ow(33), ow(555, type_=1), ow(34)]),
        chan(43, "guild-announcements", parent="41"),
        chan(44, "raid-guide", parent="41"),
        chan(45, "testing-logs", parent="41"),
        chan(46, "na-2740", parent="41"),
        chan(47, "War", sp.VOICE, parent="41", bitrate=64000),
    ], "_bot_member": {"roles": ["31"]}}


@pytest.fixture
def layout():
    return sp.prod_layout(prod_guild())


def plan(test=None, dev_ids=("45",)):
    test = test or fake_test_server()
    want = sp.prod_layout(prod_guild())
    member_top = 6
    r_ops, match = sp.role_ops(want, test, member_top)
    names = sp.role_names(want, test, match)
    cls = sp.classify(want, test, set(dev_ids), set())
    return want, test, r_ops, names, cls


def test_real_servers_are_never_writable():
    for gid in sp.PROD_GUILD_IDS:
        with pytest.raises(SystemExit):
            sp.guard_target(gid)
    with pytest.raises(SystemExit):
        sp.guard_target(12345)
    for gid in sp.TEST_GUILD_IDS:
        sp.guard_target(gid)
    with pytest.raises(RuntimeError, match="read-only"):
        sp.Discord("x")("PATCH", "/channels/1", {})


def test_key_ignores_case_emoji_and_separators():
    assert sp.key("🔥｜raid-collecting") == sp.key("raid-collecting") == "raidcollecting"
    assert sp.key("🏆 CONTRIBUTION ROLES⠀⠀") == sp.key("CONTRIBUTION ROLES")
    assert sp.key("🐚 Shell Exchange") == sp.key("shell exchange")


def test_layout_skips_archives_tickets_and_bot_roles(layout):
    names = [c["name"] for c in layout["channels"]]
    assert "old-stuff" not in names and "ticket-0182-somebody" not in names
    assert "season-2025" in names  # numbered, but not in a ticket category
    assert [c["name"] for c in layout["categories"]] == ["🔥 Raids", "Guild Applications"]
    assert "Dyno" not in [r["name"] for r in layout["roles"]]
    assert [r["name"] for r in layout["roles"]] == ["Narwhal", "🏆 CONTRIBUTION ROLES", "Sailfish"]


def test_announcements_become_text_and_user_overwrites_are_dropped(layout):
    by = {c["name"]: c for c in layout["channels"]}
    assert by["📢｜guild-announcements"]["type"] == sp.TEXT
    # the user and the bot-managed role are gone; the real role stays
    assert by["🔥｜raid-collecting"]["overwrites"] == [("Narwhal", "1024", "0")]


def test_roles_are_renamed_in_place_created_and_ordered():
    want, test, r_ops, _, _ = plan()
    edits = {o["name"]: o["changes"] for o in r_ops if o["op"] == "role_edit"}
    assert edits["CONTRIBUTION ROLES"] == {"name": "🏆 CONTRIBUTION ROLES"}
    assert edits["narwhal"] == {"name": "Narwhal"}
    assert [o["name"] for o in r_ops if o["op"] == "role_create"] == ["Sailfish"]
    order = next(o for o in r_ops if o["op"] == "role_order")["ids"]
    # the dev bot's own role (31) is never moved; other bot roles, then prod's order, then test-only roles
    assert order == ["35", "33", "32", "34"]


def test_roles_above_the_bot_are_only_warned_about():
    test = fake_test_server()
    test["roles"][2]["position"] = 9  # CONTRIBUTION ROLES above the bot
    want = sp.prod_layout(prod_guild())
    ops, _ = sp.role_ops(want, test, 6)
    assert any(o["op"] == "warn" and "CONTRIBUTION" in o["text"] for o in ops)
    assert not any(o["op"] == "role_edit" and o["id"] == "32" for o in ops)


def test_channels_rename_in_place_and_forum_is_recreated():
    want, test, _, names, cls = plan()
    ops = sp.channel_ops(want, test, names, cls, tier=0)
    edit = {o["id"]: o["changes"] for o in ops if o["op"] == "chan_edit"}
    assert edit["42"]["name"] == "🔥｜raid-collecting"
    assert edit["43"]["name"] == "📢｜guild-announcements"
    # voice bitrate is capped to what an unboosted server allows
    assert edit["47"]["bitrate"] == 96000
    create = [o for o in ops if o["op"] == "chan_create"]
    assert [(o["name"], o.get("replaces")) for o in create] == [("🔥｜raid-guide", "44"), ("season-2025", None)]
    archived = [o["id"] for o in ops if o["op"] == "to_archive"]
    assert archived == ["46"]  # the old raid-guide is archived by its replacement, not twice
    assert [o["id"] for o in ops if o["op"] == "to_dev"] == ["45"]


def test_overwrites_compare_by_role_name_and_keep_the_rest():
    want, test, _, names, cls = plan()
    owned, kept = sp.test_overwrites(test["channels"][1], names)
    assert owned == [("Narwhal", "1024", "0")]
    assert sorted(o["id"] for o in kept) == ["34", "555"]  # test-only role and a single user
    ops = sp.channel_ops(want, test, names, cls, tier=0)
    assert "overwrites" not in next(o for o in ops if o["op"] == "chan_edit" and o["id"] == "42")["changes"]


def test_categories_dev_and_archive_are_created_as_needed():
    want, test, _, names, cls = plan()
    ops = sp.category_ops(want, test, names, cls)
    assert {"op": "cat_create", "name": "Dev", "overwrites": None} in ops
    assert next(o for o in ops if o["op"] == "cat_edit")["changes"] == {"name": "🔥 Raids"}


def test_existing_archive_category_is_reused_and_named_archive():
    test = fake_test_server()
    test["channels"].append(chan(48, "Archives", sp.CATEGORY, pos=5))
    test["channels"].append(chan(49, "old", parent="48"))
    want, test, _, names, cls = plan(test)
    assert [c["id"] for c in cls["archives"]] == ["48"]
    assert "49" not in [c["id"] for c in cls["archive"]]  # already archived
    ops = sp.category_ops(want, test, names, cls)
    assert {"op": "cat_edit", "id": "48", "name": "Archives", "changes": {"name": "Archive"}} in ops


def test_order_puts_dev_first_and_archive_last():
    test = fake_test_server()
    test["channels"] += [chan(50, "Dev", sp.CATEGORY, pos=3), chan(51, "Archive", sp.CATEGORY, pos=0)]
    want, test, _, _, cls = plan(test)
    op = sp.order_op(want, test, cls)[0]
    cats = [p["id"] for p in op["positions"] if p["id"] in {"41", "50", "51"}]
    assert cats == ["50", "41", "51"]


def test_emojis_only_the_missing_structure_ones_and_only_in_main():
    snap = [{"name": "notg", "animated": False, "image": "x"}, {"name": "shells", "animated": False, "image": "y"}]
    test = fake_test_server()
    test["emojis"] = [{"name": "notg"}]
    assert [o["name"] for o in sp.emoji_ops(snap, test, "main")] == ["shells"]
    assert sp.emoji_ops(snap, test, "exec") == []
    assert set(sp.STRUCTURE_EMOJIS) >= {"notg", "shells"} and len(sp.STRUCTURE_EMOJIS) <= 50


def test_config_ids_reads_nested_profile_values():
    ids = sp.config_ids({"A": 1, "B": {"c": {"d": 2}}, "E": None, "F": True, "G": "<:x:3>"})
    assert ids == {"1", "2"}


def test_aliases_rename_a_near_miss_in_place():
    test = fake_test_server()
    test["channels"][2]["name"] = "guild-announcement"  # singular: no key match
    want = sp.prod_layout(prod_guild())
    _, match = sp.role_ops(want, test, 6)
    names = sp.role_names(want, test, match)
    plain = sp.classify(want, test, set(), set())
    assert "43" in [c["id"] for c in plain["archive"]]
    cls = sp.classify(want, test, set(), set(), {"guild-announcement": "📢｜guild-announcements"})
    ops = sp.channel_ops(want, test, names, cls, tier=0)
    assert next(o for o in ops if o["op"] == "chan_edit" and o["id"] == "43")["changes"]["name"] == "📢｜guild-announcements"


def test_writes_only_reach_test_servers_and_their_channels():
    d = sp.Discord("x", writes=True)
    test_gid = next(iter(sp.TEST_GUILD_IDS))
    d.test_channels = {"42"}
    d.check_write("PATCH", f"/guilds/{test_gid}/roles")
    d.check_write("PATCH", "/channels/42")
    for gid in sp.PROD_GUILD_IDS:
        with pytest.raises(RuntimeError, match="not a test server"):
            d.check_write("POST", f"/guilds/{gid}/channels")
    with pytest.raises(RuntimeError, match="not read from a test server"):
        d.check_write("PATCH", "/channels/43")
    with pytest.raises(RuntimeError, match="not a guild or channel path"):
        d.check_write("POST", "/webhooks/1/abc")


def test_execute_refuses_ids_outside_the_test_server():
    test = fake_test_server()
    with pytest.raises(SystemExit):
        sp.execute(None, next(iter(sp.TEST_GUILD_IDS)), {"op": "to_dev", "id": "999", "name": "x"}, test, {}, {})


def test_overwrites_resolve_to_the_matched_role_when_names_repeat():
    test = fake_test_server()
    test["roles"][3]["name"] = "Narwhal"
    test["roles"].append(role(36, "Narwhal", 2))  # a second role with the same name
    want = sp.prod_layout(prod_guild())
    _, match = sp.role_ops(want, test, 6)
    names = sp.role_names(want, test, match)
    matched = next(i for i, n in names.items() if n == "Narwhal")
    test["roles"].reverse()  # a bare name lookup would now find the other one
    out = sp.resolve_overwrites([("Narwhal", "1", "0")], test, [], names)
    assert out == [{"id": matched, "type": 0, "allow": "1", "deny": "0"}]


def test_a_replaced_channel_is_archived_even_if_the_bot_uses_it():
    want, test, _, names, cls = plan(dev_ids=("44", "45"))  # 44 = old text raid-guide, replaced by a forum
    assert [c["id"] for c in cls["dev"]] == ["45"]


def test_test_only_roles_spare_bot_roles_kept_names_and_prod_roles():
    test = fake_test_server()
    test["roles"].append(role(37, "Developer", 1))
    want, test, _, names, _ = plan(test)
    assert [r["name"] for r in sp.test_only_roles(test, names)] == ["Moner"]


def test_topics_swap_custom_emojis_for_the_test_servers_or_plain_names():
    emojis = [{"name": "notg", "id": "7"}]
    assert sp.portable_text("hi <:notg:123> <a:steamhappy:456>", emojis) == "hi <:notg:7> :steamhappy:"
    assert sp.portable_text(None, emojis) is None


def test_duplicate_prod_names_stop_the_sync():
    g = prod_guild()
    g["roles"].append(role(15, "Sailfish", 0))
    with pytest.raises(SystemExit, match="more than one role"):
        sp.prod_layout(g)


def test_parents_resolve_to_the_matched_category_not_a_same_named_leftover():
    test = fake_test_server()
    test["channels"][0]["name"] = "🔥 Raids"
    test["channels"].insert(0, chan(60, "🔥 Raids", sp.CATEGORY, pos=9))  # a newer leftover with the same name
    want, test, _, names, cls = plan(test)
    assert sp.category_id(test, "🔥 Raids", cls) == "41"  # the matched (oldest) one, not the first in the list
    # a channel sitting in the leftover is reported as misplaced
    test["channels"][2]["parent_id"] = "60"
    want, test, _, names, cls = plan(test)
    ops = sp.channel_ops(want, test, names, cls, tier=0)
    assert next(o for o in ops if o.get("id") == "42")["changes"]["parent"] == "🔥 Raids"


def test_emoji_uploads_stop_at_the_free_slots():
    test = fake_test_server()
    test["emojis"] = [{"name": f"e{i}", "animated": False} for i in range(49)]
    snap = [{"name": "notg", "animated": False, "image": "x"}, {"name": "shells", "animated": False, "image": "y"}]
    ops = sp.emoji_ops(snap, test, "main")
    assert [o["name"] for o in ops if o["op"] == "emoji_create"] == ["notg"]
    assert any(o["op"] == "warn" and "shells" in o["text"] for o in ops)


def test_renumber_skips_when_no_role_below_the_bot_can_move():
    test = fake_test_server()
    for r in test["roles"]:
        if r["id"] != TEST:
            r["position"] = 7  # all tied, all above the bot's role
    test["_bot_member"] = {"roles": ["35"]}
    test["roles"][-1]["position"] = 1
    test["roles"][-1]["managed"] = True
    calls = []
    assert sp.renumber_roles(lambda *a: calls.append(a), TEST, test) is False and calls == []
