"""
Centralized, safe editing of application poll messages.

Solves three problems:
1. CDN URL expiry: restores attachment:// URLs before editing
2. Stale messages: always re-fetches the message before editing
3. Race conditions: uses per-message asyncio locks
"""

import asyncio
from collections import defaultdict

import discord

from Helpers.logger import log, ERROR

# Per-message locks keyed by message ID
_poll_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

_IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.webp')


def _fix_embed_image_url(embed: discord.Embed, attachments: list[discord.Attachment]) -> discord.Embed:
    """Replace any CDN image URL with the corresponding attachment:// URL.

    When a message has an attachment and the embed's image URL points to that
    same file via CDN (possibly with expired auth tokens), this function
    replaces it with the stable ``attachment://filename`` form.
    """
    if not attachments:
        return embed

    image = getattr(embed, '_image', None) or {}
    image_url = image.get('url') if isinstance(image, dict) else getattr(image, 'url', None)
    if not image_url:
        return embed

    # Already an attachment:// URL — nothing to do
    if image_url.startswith("attachment://"):
        return embed

    # Match by filename in the CDN URL path
    for att in attachments:
        if att.filename and att.filename in image_url:
            embed.set_image(url=f"attachment://{att.filename}")
            return embed

    # Fallback: if there's exactly one image attachment, assume it's the one
    image_attachments = [a for a in attachments if any(a.filename.lower().endswith(ext) for ext in _IMAGE_EXTS)]
    if len(image_attachments) == 1:
        embed.set_image(url=f"attachment://{image_attachments[0].filename}")

    return embed


async def safe_edit_poll(
    channel: discord.TextChannel,
    message_id: int,
    *,
    modify_embed=None,
    include_view: bool = True,
) -> discord.Message | None:
    """Safely edit a poll message with proper locking, fresh fetch, and URL fix.

    Parameters
    ----------
    channel : discord.TextChannel
        The exec channel containing the poll message.
    message_id : int
        The poll message ID to edit.
    modify_embed : callable, optional
        A function ``(embed) -> None`` that modifies the embed in-place.
    include_view : bool
        Whether to re-attach the ``ApplicationVoteView``.  Default ``True``.
    """
    lock = _poll_locks[message_id]
    async with lock:
        # 1. Always fetch fresh
        try:
            poll_msg = await channel.fetch_message(message_id)
        except discord.NotFound:
            _poll_locks.pop(message_id, None)
            return None
        except Exception as e:
            log(ERROR, f"Failed to fetch poll message {message_id}: {e}", context="poll_edit")
            return None

        if not poll_msg.embeds:
            return None

        # 2. Copy embed and fix image URL
        embed = poll_msg.embeds[0].copy()
        embed = _fix_embed_image_url(embed, poll_msg.attachments)

        # 3. Apply caller's modifications
        if modify_embed:
            modify_embed(embed)

        # 4. Edit with fixed embed + original attachments
        try:
            kwargs = {
                "embed": embed,
                "attachments": poll_msg.attachments,
            }
            if include_view:
                # Lazy import to avoid circular dependency with views.py
                from Helpers.views import ApplicationVoteView
                kwargs["view"] = ApplicationVoteView()
            return await poll_msg.edit(**kwargs)
        except Exception as e:
            log(ERROR, f"Failed to edit poll message {message_id}: {e}", context="poll_edit")
            return None


def cleanup_lock(message_id: int):
    """Remove the lock for a message that no longer needs it."""
    _poll_locks.pop(message_id, None)
