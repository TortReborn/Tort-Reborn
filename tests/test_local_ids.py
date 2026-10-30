"""Tests for Helpers/local_ids.py (TAQ-110).

What matters here:

1. Under the prod bot nothing changes: every id, text and embed comes back as is.
2. Under the dev bot, role/channel mentions, custom emojis and maincord channel
   links are translated; ids without a test counterpart are left alone.
3. Every role and emoji the role buttons hand out has a test counterpart in the
   committed map, so the buttons work in Tort Test.
"""

import os
import sys

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import local_ids  # noqa: E402

FAKE = {
    "guilds": {"729147655875199017": "1369134564450107412"},
    "roles": {"111": "211"},
    "channels": {"112": "212"},
    "emojis": {"113": "213"},
}


@pytest.fixture
def test_map(monkeypatch):
    monkeypatch.setattr(local_ids, "_MAP", FAKE)


@pytest.fixture
def prod(monkeypatch):
    monkeypatch.setattr(local_ids, "_MAP", {})


def test_prod_is_a_no_op(prod):
    s = "<@&111> in <#112> :) <:notg:113> https://discord.com/channels/729147655875199017/112"
    assert local_ids.text(s) == s
    assert local_ids.role(111) == 111 and local_ids.channel(112) == 112 and local_ids.emoji(113) == 113
    e = discord.Embed(description=s)
    assert local_ids.embed(e) is e


def test_text_translates_mentions_emojis_and_links(test_map):
    s = "<@&111> in <#112> <:notg:113> <a:x:113> https://discord.com/channels/729147655875199017/112/999"
    assert local_ids.text(s) == ("<@&211> in <#212> <:notg:213> <a:x:213> "
                                 "https://discord.com/channels/1369134564450107412/212/999")


def test_unknown_ids_and_other_servers_are_left_alone(test_map):
    s = "<@&999> <#998> https://discord.com/channels/143852930036924417/1020187255480008724"
    assert local_ids.text(s) == s
    assert local_ids.role(999) == 999 and local_ids.role(None) is None


def test_embeds_and_emojis(test_map):
    e = local_ids.embed(discord.Embed(title="<#112>", description="<@&111>").add_field(name="f", value="<:a:113>"))
    assert (e.title, e.description, e.fields[0].value) == ("<#212>", "<@&211>", "<:a:213>")
    assert local_ids.partial_emoji("notg", 113).id == 213


def test_role_buttons_have_test_counterparts():
    """Under the dev bot the buttons hold test ids; each must be a test-side id in the map."""
    import json
    with open(local_ids.MAP_FILE, encoding="utf-8") as f:
        id_map = json.load(f)
    from Commands import generate
    configs = [*generate.REACTION_ROLES, *generate.PING_ROLES]
    test_roles, test_emojis = set(id_map["roles"].values()), set(id_map["emojis"].values())
    for c in configs:
        assert str(c.role_id) in test_roles, c.label
        if isinstance(c.emoji, discord.PartialEmoji) and c.emoji.id:
            assert str(c.emoji.id) in test_emojis, c.label


def test_role_button_ids_are_translated(test_map):
    button = {"kind": "role", "role_id": "111", "requires_role_id": "111", "label": "<@&111>"}
    assert local_ids.data(button) == {"kind": "role", "role_id": "211", "requires_role_id": "211", "label": "<@&211>"}


def test_role_button_ids_untouched_in_prod(prod):
    button = {"kind": "role", "role_id": "111"}
    assert local_ids.data(button) == button
