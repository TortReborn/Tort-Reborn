"""respond_paginator must never touch the channel: in user-install contexts the
bot can't read it, which is what made every /snipe leaderboard 403 after the
card had already been sent."""
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import pages

from Helpers.pagination import respond_paginator


def _paginator():
    return pages.Paginator(pages=[pages.Page(content="one"), pages.Page(content="two")])


def _interaction(done: bool):
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.is_done.return_value = done
    sent = MagicMock(spec=discord.WebhookMessage)
    sent.channel.fetch_message = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "Missing Access"))
    interaction.followup.send = AsyncMock(return_value=sent)
    return interaction, sent


@pytest.mark.asyncio
async def test_deferred_response_keeps_webhook_message_without_channel_fetch():
    paginator = _paginator()
    interaction, sent = _interaction(done=True)

    result = await respond_paginator(paginator, interaction)

    assert result is sent
    assert paginator.message is sent
    assert paginator.user is interaction.user
    sent.channel.fetch_message.assert_not_called()
    kwargs = interaction.followup.send.await_args.kwargs
    assert kwargs["content"] == "one"
    assert kwargs["view"] is paginator


@pytest.mark.asyncio
async def test_undeferred_interaction_falls_through_to_pycord(monkeypatch):
    paginator = _paginator()
    interaction, _ = _interaction(done=False)
    monkeypatch.setattr(pages.Paginator, "respond", AsyncMock(return_value="pycord"))

    assert await respond_paginator(paginator, interaction) == "pycord"
    interaction.followup.send.assert_not_called()
