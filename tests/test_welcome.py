"""Welcome templates (TAQ-142): the welcome DM and the "Welcome Aboard!" post
are template panels; Helpers/welcome.py sends the published revision with the
member filled in, or the built-in text when there is none to send."""

import asyncio

import discord
import pytest

from Helpers import welcome
from Helpers.bot_panels import PanelError, SAMPLE_MEMBER_VALUES, check_template, member_values

AVATAR = "https://cdn.discordapp.com/avatars/1/abc.png"


class FakeMember:
    def __init__(self, member_id=170719819715313665, dms_open=True):
        self.id = member_id
        self.name = "fish"
        self.mention = f"<@{member_id}>"
        self.display_avatar = type("Avatar", (), {"url": AVATAR})()
        self.dms_open = dms_open
        self.sent = []

    async def send(self, content=None, **kw):
        if not self.dms_open:
            raise discord.Forbidden(type("R", (), {"status": 403, "reason": "Forbidden"})(), "Cannot send messages to this user")
        self.sent.append({"content": content, **kw})


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kw):
        self.sent.append({"content": content, **kw})


@pytest.fixture
def published(monkeypatch):
    """What load_published returns, per template; values come without a DB."""
    templates = {}
    monkeypatch.setattr(welcome, "load_published", lambda key: templates.get(key))
    monkeypatch.setattr(welcome, "load_panel_values", lambda: {"shells_per_aspect": "3"})
    return templates


def test_template_fills_the_member_in(published):
    published["welcome_post"] = {"messages": [{"embeds": [{
        "author": {"name": "Welcome Aboard!", "icon_url": "{{member_avatar}}"},
        "description": "Dive in, {{member}} ({{ign}}), {{shells_per_aspect}} shells",
    }]}]}
    channel, member = FakeChannel(), FakeMember()
    asyncio.run(welcome.send_welcome_post(channel, member, "Fishy"))
    [sent] = channel.sent
    [embed] = sent["embeds"]
    assert embed.description == f"Dive in, {member.mention} (Fishy), 3 shells"
    assert embed.author.icon_url == AVATAR


def test_only_the_member_can_be_pinged(published):
    published["welcome_post"] = {"messages": [{"content": "@everyone <@&123456789012345678> {{member}}"}]}
    channel, member = FakeChannel(), FakeMember()
    asyncio.run(welcome.send_welcome_post(channel, member, "Fishy"))
    mentions = channel.sent[0]["allowed_mentions"]
    assert mentions.everyone is False and mentions.roles is False
    assert [u.id for u in mentions.users] == [member.id]


def test_no_template_sends_the_built_in_post(published):
    channel, member = FakeChannel(), FakeMember()
    asyncio.run(welcome.send_welcome_post(channel, member, "Fishy"))
    [embed] = [m["embed"] for m in channel.sent]
    assert embed.author.name == "Welcome Aboard!" and member.mention in embed.description


def test_a_broken_template_falls_back_rather_than_skipping_the_welcome(published):
    published["welcome_post"] = {"messages": [{"content": "{{not_a_value}}"}]}
    channel = FakeChannel()
    asyncio.run(welcome.send_welcome_post(channel, FakeMember(), "Fishy"))
    assert channel.sent[0]["embed"].author.name == "Welcome Aboard!"


def test_dm_template_with_link_button(published):
    published["welcome_dm"] = {"messages": [{
        "content": "Welcome {{member}}",
        "rows": [[{"kind": "link", "label": "Get Verge", "url": "https://modrinth.com/project/37mIxdyU"}]],
    }]}
    member = FakeMember()
    asyncio.run(welcome.send_welcome_dm(member, "Fishy"))
    [sent] = member.sent
    assert sent["content"] == f"Welcome {member.mention}"
    [button] = sent["view"].children
    assert (button.label, button.url) == ("Get Verge", "https://modrinth.com/project/37mIxdyU")


def test_built_in_dm_mentions_verge(published):
    member = FakeMember()
    asyncio.run(welcome.send_welcome_dm(member, "Fishy"))
    [sent] = member.sent
    assert sent["content"].startswith(f"Welcome to **The Aquarium**, {member.mention}!")
    assert "<https://modrinth.com/project/37mIxdyU>" in sent["content"]


def test_closed_dms_are_not_an_error(published):
    asyncio.run(welcome.send_welcome_dm(FakeMember(dms_open=False), "Fishy"))


@pytest.mark.parametrize("message, why", [
    ({"content": "x", "rows": [[{"kind": "action", "action": "raid_claim"}]]}, "only have link buttons"),
    ({"content": "x", "rows": [[{"kind": "role", "role_id": "123456789012345678", "label": "r"}]]}, "only have link buttons"),
    ({"content": "x", "dynamic": "shell_exchange_panels"}, "generated images"),
])
def test_templates_refuse_what_a_dm_cant_carry(message, why):
    with pytest.raises(PanelError, match=why):
        check_template({"messages": [message]})


def test_posted_panels_cant_use_member_values():
    """{{member}} only means something when a template is sent to one member."""
    from Helpers.bot_panels import render_message
    with pytest.raises(PanelError, match=r"\{\{member\}\} isn't a value"):
        render_message(0, {"content": "hi {{member}}"}, actions={}, values={}, fetch_asset={}.get)


def test_publishing_a_template_checks_it_and_posts_nothing(published):
    from Tasks.publish_bot_panels import PanelJob, PublishBotPanels
    job = PanelJob(rev_id=1, rev_number=2, panel_id=3, slug="welcome-dm", channel_id=None,
                   adopt_message_ids=[], panel_channel_id=None, panel_message_ids=[], template="welcome_dm",
                   content={"messages": [{"content": "Hi {{member}}", "rows": [[{"kind": "link", "label": "Verge", "url": "https://a.b"}]]}]})
    assert asyncio.run(PublishBotPanels(None)._publish(job)) == (None, [], [])

    job.content = {"messages": [{"content": "Hi {{nope}}"}]}
    with pytest.raises(PanelError, match=r"\{\{nope\}\}"):
        asyncio.run(PublishBotPanels(None)._publish(job))


def test_sample_values_cover_every_member_value():
    assert SAMPLE_MEMBER_VALUES.keys() == member_values("", "", "").keys()


def test_seeded_welcome_dm_is_the_built_in_one_plus_verge(monkeypatch):
    import scripts.seed_bot_panels as seed
    monkeypatch.setattr(seed, "get_shell_exchange_config", lambda: {})
    panels = {p["slug"]: p for p in seed.build_panels(lambda path: "asset://0123456789abcdef.png")}
    dm = panels["welcome-dm"]
    assert dm["template"] == "welcome_dm"
    [message] = dm["content"]["messages"]
    assert message["content"].startswith("Welcome to **The Aquarium**, {{member}}!")
    assert "**Verge**" in message["content"]
    assert message["rows"] == [[{"kind": "link", "label": "Get Verge", "url": seed.VERGE_URL}]]
    assert panels["welcome-post"]["template"] == "welcome_post"
