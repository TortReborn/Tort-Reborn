"""Panels as forum posts and the forum copy tool (TAQ-115).

What matters here:

1. A forum panel needs a post title, and a field may go without a heading
   (posts copied from webhooks use that), sent as an invisible one.
2. The publisher opens a post with the panel's first message and posts the
   rest in it, and takes the post back if a later message fails.
3. Replacing a panel's old messages removes the whole post when the bot
   opened it and nobody else wrote in it; a post others wrote in stays.
4. The copy tool turns an exported post into panel content faithfully, and
   its compare treats the invisible heading as the blank one it stands for.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import bot_panels as bp  # noqa: E402
from scripts import copy_forum_posts as cfp  # noqa: E402
from Tasks.publish_bot_panels import PublishBotPanels  # noqa: E402

BOT = 42


def render(message):
    async def go():
        return bp.render_message(0, message, actions={}, values={}, fetch_asset={}.get)
    return asyncio.run(go())


def test_forum_post_title():
    assert bp.forum_post_title({"post": {"title": "  TE Roles "}}) == "TE Roles"
    for content in ({}, {"post": {}}, {"post": {"title": "  "}}):
        with pytest.raises(bp.PanelError, match="title"):
            bp.forum_post_title(content)
    with pytest.raises(bp.PanelError, match="100"):
        bp.forum_post_title({"post": {"title": "x" * 101}})


def test_a_field_without_a_heading_gets_an_invisible_one():
    [embed] = render({"embeds": [{"fields": [{"name": "", "value": "Paragraph"}]}]}).embeds
    assert embed.fields[0].name == bp.BLANK_FIELD_NAME and embed.fields[0].value == "Paragraph"
    with pytest.raises(bp.PanelError, match="needs a value"):
        render({"embeds": [{"fields": [{"name": "Heading", "value": " "}]}]})


def _msg(mid, author=BOT, system=False):
    m = MagicMock()
    m.id, m.author.id = mid, author
    m.is_system.return_value = system
    m.delete = AsyncMock()
    return m


def _rendered(n):
    return [bp.RenderedMessage(content=f"m{i}", embeds=[], files=[], view=None) for i in range(n)]


def _cog():
    return PublishBotPanels(MagicMock())


def test_open_post_puts_the_first_message_in_the_post_and_the_rest_after():
    post = MagicMock(spec=discord.Thread)
    post.id = 100
    post.send = AsyncMock(side_effect=[_msg(101), _msg(102)])
    forum = MagicMock(spec=discord.ForumChannel)
    forum.create_thread = AsyncMock(return_value=post)

    got, ids = asyncio.run(_cog()._open_post(forum, "TE 101", _rendered(3)))
    assert got is post and ids == [100, 101, 102]
    assert forum.create_thread.await_args.kwargs["name"] == "TE 101"
    assert forum.create_thread.await_args.kwargs["content"] == "m0"


def test_open_post_takes_the_post_back_when_a_later_message_fails():
    post = MagicMock(spec=discord.Thread)
    post.id = 100
    post.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=400), "no"))
    post.delete = AsyncMock()
    forum = MagicMock(spec=discord.ForumChannel)
    forum.create_thread = AsyncMock(return_value=post)
    with pytest.raises(discord.HTTPException):
        asyncio.run(_cog()._open_post(forum, "TE 101", _rendered(2)))
    post.delete.assert_awaited_once()


def _post(owner, history):
    t = MagicMock(spec=discord.Thread)
    t.id, t.owner_id, t.name = history[0].id, owner, "Old"
    t.delete = AsyncMock()

    async def hist(limit):
        for m in history[:limit]:
            yield m
    t.history = hist
    return t


def test_remove_old_deletes_a_post_that_is_only_the_panel():
    msgs = [_msg(100), _msg(101)]
    # Discord's "changed the post title" notice and the bot's own messages don't count as others
    post, notes = _post(BOT, msgs + [_msg(102, author=7, system=True), _msg(103)]), []
    asyncio.run(_cog()._remove_old(post, msgs, BOT, notes))
    post.delete.assert_awaited_once()
    assert notes == [] and not any(m.delete.await_count for m in msgs)


def test_remove_old_keeps_a_post_others_wrote_in():
    msgs = [_msg(100)]
    post, notes = _post(BOT, msgs + [_msg(200, author=7)]), []
    asyncio.run(_cog()._remove_old(post, msgs, BOT, notes))
    post.delete.assert_not_awaited()
    msgs[0].delete.assert_not_awaited()
    assert "kept" in notes[0]


def test_remove_old_in_a_plain_channel_deletes_only_bot_messages():
    mine, theirs = _msg(1), _msg(2, author=7)
    asyncio.run(_cog()._remove_old(MagicMock(spec=discord.TextChannel), [mine, theirs], BOT, []))
    mine.delete.assert_awaited_once()
    theirs.delete.assert_not_awaited()


EXPORTED = {
    "author": {"username": "Tort Reborn"}, "webhook_id": "1", "content": "",
    "attachments": [], "_images": {},
    "embeds": [
        {"type": "rich", "color": 9748735, "title": "Guide", "description": "See <#1462525805220466790>",
         "fields": [{"name": "", "value": "Paragraph", "inline": False}], "content_scan_version": 1},
        {"type": "link", "url": "https://example.com"},
    ],
}


def test_copy_tool_embeds_and_slugs():
    e = cfp._embed(EXPORTED["embeds"][0], lambda u: u)
    assert e == {"color": 9748735, "title": "Guide", "description": "See <#1462525805220466790>",
                 "fields": [{"name": "", "value": "Paragraph", "inline": False}]}
    assert cfp.slug_for("Discord and TE Rules") == "discord-and-te-rules"
    assert cfp.slug_for("TE 101") == "te-101"


def test_compare_reads_an_invisible_heading_as_blank_and_skips_link_previews():
    sent = {**EXPORTED, "embeds": [{**EXPORTED["embeds"][0],
                                    "fields": [{"name": bp.BLANK_FIELD_NAME, "value": "Paragraph"}]}]}
    assert cfp._shape(EXPORTED, lambda v: v) == cfp._shape(sent, lambda v: v)
    changed = {**sent, "embeds": [{**sent["embeds"][0], "title": "Other"}]}
    assert cfp._first_difference(cfp._shape(EXPORTED, lambda v: v), cfp._shape(changed, lambda v: v)) \
        == "embeds.0.title: was 'Guide' now 'Other'"
