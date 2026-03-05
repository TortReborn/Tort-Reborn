"""
Test suite for poll embed editing safety (Helpers/poll_edit.py).

Tests:
1. CDN URL fix: embed image URLs are restored to attachment:// form
2. Fresh message fetch: stale messages are not used
3. Locking: concurrent edits are serialized
4. Edge cases: no attachments, no image, multiple attachments
"""

import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helpers to build mock discord objects
# ---------------------------------------------------------------------------

def _make_attachment(filename="stats-3726-abc.png"):
    att = MagicMock()
    att.filename = filename
    att.id = 123456789
    return att


def _make_embed(image_url=None):
    embed = MagicMock()
    if image_url:
        embed._image = {"url": image_url}
        embed.image = MagicMock()
        embed.image.url = image_url
    else:
        embed._image = None
        embed.image = None
    embed.fields = []
    return embed


def _make_message(embed_image_url=None, attachment_filename="stats.png", has_attachment=True):
    embed = MagicMock()
    if embed_image_url:
        embed.image = MagicMock()
        embed.image.url = embed_image_url
        embed._image = {"url": embed_image_url}
    else:
        embed.image = None
        embed._image = None
    embed.fields = []
    embed.copy.return_value = _make_embed(embed_image_url)

    msg = MagicMock()
    msg.id = 999
    msg.embeds = [embed]
    if has_attachment:
        msg.attachments = [_make_attachment(attachment_filename)]
    else:
        msg.attachments = []
    msg.edit = AsyncMock(return_value=msg)
    return msg


# ---------------------------------------------------------------------------
# Tests for _fix_embed_image_url
# ---------------------------------------------------------------------------

class TestFixEmbedImageUrl:
    """Verify that CDN URLs are correctly replaced with attachment:// URLs."""

    def test_cdn_url_replaced_with_attachment_url(self):
        from Helpers.poll_edit import _fix_embed_image_url

        embed = _make_embed("https://cdn.discordapp.com/attachments/123/456/stats-3726-abc.png?ex=abc&is=def&hm=ghi")
        att = _make_attachment("stats-3726-abc.png")

        result = _fix_embed_image_url(embed, [att])
        embed.set_image.assert_called_once_with(url="attachment://stats-3726-abc.png")

    def test_already_attachment_url_unchanged(self):
        from Helpers.poll_edit import _fix_embed_image_url

        embed = _make_embed("attachment://stats-3726-abc.png")
        att = _make_attachment("stats-3726-abc.png")

        result = _fix_embed_image_url(embed, [att])
        embed.set_image.assert_not_called()

    def test_no_image_no_change(self):
        from Helpers.poll_edit import _fix_embed_image_url

        embed = _make_embed(None)
        att = _make_attachment("stats.png")

        result = _fix_embed_image_url(embed, [att])
        embed.set_image.assert_not_called()

    def test_no_attachments_no_change(self):
        from Helpers.poll_edit import _fix_embed_image_url

        embed = _make_embed("https://cdn.discordapp.com/attachments/123/456/stats.png")

        result = _fix_embed_image_url(embed, [])
        embed.set_image.assert_not_called()

    def test_fallback_single_image_attachment(self):
        """When filename doesn't match CDN path, fall back to single image attachment."""
        from Helpers.poll_edit import _fix_embed_image_url

        embed = _make_embed("https://cdn.discordapp.com/attachments/123/456/old-name.png?ex=abc")
        att = _make_attachment("only-image.png")

        result = _fix_embed_image_url(embed, [att])
        embed.set_image.assert_called_once_with(url="attachment://only-image.png")

    def test_no_fallback_for_non_image_attachment(self):
        """Don't fall back to non-image attachments."""
        from Helpers.poll_edit import _fix_embed_image_url

        embed = _make_embed("https://cdn.discordapp.com/attachments/123/456/old-name.png?ex=abc")
        att = _make_attachment("document.txt")

        result = _fix_embed_image_url(embed, [att])
        embed.set_image.assert_not_called()

    def test_no_fallback_multiple_image_attachments(self):
        """Don't fall back when there are multiple image attachments (ambiguous)."""
        from Helpers.poll_edit import _fix_embed_image_url

        embed = _make_embed("https://cdn.discordapp.com/attachments/123/456/unknown.png?ex=abc")
        att1 = _make_attachment("image1.png")
        att2 = _make_attachment("image2.png")

        result = _fix_embed_image_url(embed, [att1, att2])
        embed.set_image.assert_not_called()


# ---------------------------------------------------------------------------
# Tests for safe_edit_poll
# ---------------------------------------------------------------------------

class TestSafeEditPoll:
    """End-to-end tests for safe_edit_poll."""

    @pytest.mark.asyncio
    async def test_fetches_fresh_message(self):
        """safe_edit_poll should always call fetch_message, never use cached."""
        from Helpers.poll_edit import safe_edit_poll, _poll_locks

        msg = _make_message(has_attachment=False)
        channel = MagicMock()
        channel.fetch_message = AsyncMock(return_value=msg)

        _poll_locks.pop(999, None)

        with patch("Helpers.poll_edit.log"):
            await safe_edit_poll(channel, 999, include_view=False)

        channel.fetch_message.assert_called_once_with(999)

    @pytest.mark.asyncio
    async def test_applies_modify_callback(self):
        """The modify_embed callback should be called with the copied embed."""
        from Helpers.poll_edit import safe_edit_poll, _poll_locks

        msg = _make_message(has_attachment=False)
        channel = MagicMock()
        channel.fetch_message = AsyncMock(return_value=msg)

        called_with = []

        def my_modify(embed):
            called_with.append(embed)

        _poll_locks.pop(999, None)

        with patch("Helpers.poll_edit.log"):
            await safe_edit_poll(channel, 999, modify_embed=my_modify, include_view=False)

        assert len(called_with) == 1

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        """If the message is deleted, safe_edit_poll should return None."""
        import discord
        from Helpers.poll_edit import safe_edit_poll, _poll_locks

        channel = MagicMock()
        channel.fetch_message = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "Not found")
        )

        _poll_locks.pop(2000, None)

        with patch("Helpers.poll_edit.log"):
            result = await safe_edit_poll(channel, 2000, include_view=False)

        assert result is None

    @pytest.mark.asyncio
    async def test_no_embeds_returns_none(self):
        """If the message has no embeds, return None."""
        from Helpers.poll_edit import safe_edit_poll, _poll_locks

        msg = MagicMock()
        msg.id = 3000
        msg.embeds = []
        msg.attachments = []

        channel = MagicMock()
        channel.fetch_message = AsyncMock(return_value=msg)

        _poll_locks.pop(3000, None)

        with patch("Helpers.poll_edit.log"):
            result = await safe_edit_poll(channel, 3000, include_view=False)

        assert result is None

    @pytest.mark.asyncio
    async def test_fixes_cdn_url_before_edit(self):
        """CDN image URLs should be converted to attachment:// before editing."""
        from Helpers.poll_edit import safe_edit_poll, _poll_locks

        cdn_url = "https://cdn.discordapp.com/attachments/1/2/player-stats.png?ex=abc"
        msg = _make_message(embed_image_url=cdn_url, attachment_filename="player-stats.png")
        channel = MagicMock()
        channel.fetch_message = AsyncMock(return_value=msg)

        _poll_locks.pop(999, None)

        with patch("Helpers.poll_edit.log"):
            await safe_edit_poll(channel, 999, include_view=False)

        # The copied embed should have had set_image called with attachment://
        copied_embed = msg.embeds[0].copy.return_value
        copied_embed.set_image.assert_called_with(url="attachment://player-stats.png")

    @pytest.mark.asyncio
    async def test_passes_attachments_to_edit(self):
        """The edit call should include attachments from the fresh message."""
        from Helpers.poll_edit import safe_edit_poll, _poll_locks

        msg = _make_message(has_attachment=True, attachment_filename="test.png")
        channel = MagicMock()
        channel.fetch_message = AsyncMock(return_value=msg)

        _poll_locks.pop(999, None)

        with patch("Helpers.poll_edit.log"):
            await safe_edit_poll(channel, 999, include_view=False)

        msg.edit.assert_called_once()
        call_kwargs = msg.edit.call_args[1]
        assert call_kwargs["attachments"] == msg.attachments


# ---------------------------------------------------------------------------
# Tests for locking behavior
# ---------------------------------------------------------------------------

class TestPollLocking:
    """Verify that concurrent edits to the same message are serialized."""

    @pytest.mark.asyncio
    async def test_concurrent_edits_serialized(self):
        """Two concurrent safe_edit_poll calls should not interleave."""
        from Helpers.poll_edit import safe_edit_poll, _poll_locks

        call_order = []

        msg = _make_message(has_attachment=False)
        channel = MagicMock()
        channel.fetch_message = AsyncMock(return_value=msg)

        def modify_a(embed):
            call_order.append("a")

        def modify_b(embed):
            call_order.append("b")

        _poll_locks.pop(999, None)

        with patch("Helpers.poll_edit.log"):
            await asyncio.gather(
                safe_edit_poll(channel, 999, modify_embed=modify_a, include_view=False),
                safe_edit_poll(channel, 999, modify_embed=modify_b, include_view=False),
            )

        # Both should have run (fetch_message called twice)
        assert channel.fetch_message.call_count == 2
        assert len(call_order) == 2

    @pytest.mark.asyncio
    async def test_different_messages_not_blocked(self):
        """Edits to different message IDs should not block each other."""
        from Helpers.poll_edit import safe_edit_poll, _poll_locks

        msg1 = _make_message(has_attachment=False)
        msg1.id = 1001
        msg2 = _make_message(has_attachment=False)
        msg2.id = 1002

        channel = MagicMock()
        channel.fetch_message = AsyncMock(side_effect=lambda mid: msg1 if mid == 1001 else msg2)

        _poll_locks.pop(1001, None)
        _poll_locks.pop(1002, None)

        with patch("Helpers.poll_edit.log"):
            await asyncio.gather(
                safe_edit_poll(channel, 1001, include_view=False),
                safe_edit_poll(channel, 1002, include_view=False),
            )

        assert channel.fetch_message.call_count == 2


# ---------------------------------------------------------------------------
# Tests for cleanup_lock
# ---------------------------------------------------------------------------

class TestCleanupLock:
    def test_cleanup_removes_lock(self):
        from Helpers.poll_edit import _poll_locks, cleanup_lock

        # Create a lock
        _ = _poll_locks[5000]
        assert 5000 in _poll_locks

        cleanup_lock(5000)
        assert 5000 not in _poll_locks

    def test_cleanup_nonexistent_is_safe(self):
        from Helpers.poll_edit import cleanup_lock

        # Should not raise
        cleanup_lock(99999)
