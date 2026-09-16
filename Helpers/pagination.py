"""Centralised paginator button helper with emoji fallback."""

import discord
from discord.ext import pages

# Standard button configuration: (action, custom_emoji, style, unicode_fallback)
_BUTTON_CONFIG = [
    ('first', '<:first_arrows:1198703152204103760>', discord.ButtonStyle.blurple, '\u23ea'),
    ('prev', '<:left_arrow:1198703157501509682>', discord.ButtonStyle.red, '\u25c0\ufe0f'),
    ('next', '<:right_arrow:1198703156088021112>', discord.ButtonStyle.green, '\u25b6\ufe0f'),
    ('last', '<:last_arrows:1198703153726627880>', discord.ButtonStyle.blurple, '\u23e9'),
]


def add_paginator_buttons(paginator):
    """Add standard navigation buttons to a paginator with emoji fallback.

    Uses custom guild emojis when available, falling back to unicode emoji
    if the custom emoji cannot be resolved (e.g. bot is in an external guild
    without access to the home guild emoji).
    """
    for action, emoji, style, fallback in _BUTTON_CONFIG:
        try:
            paginator.add_button(pages.PaginatorButton(action, emoji=emoji, style=style))
        except Exception:
            paginator.add_button(pages.PaginatorButton(action, emoji=fallback, style=style))


async def respond_paginator(paginator, interaction: discord.Interaction):
    """Send a paginator as the followup to an already-deferred interaction.

    ``Paginator.respond()`` re-fetches the followup message through its channel
    so the view can outlive the 15-minute webhook token. In user-install
    contexts (a guild the bot isn't in, a DM between other users) the bot has no
    channel access and that fetch raises Forbidden *after* the pages were sent,
    so the user sees the card and then an error. Our paginators time out in
    three minutes, well inside the token's life, so the webhook message is all
    the buttons need.
    """
    if not interaction.response.is_done():
        return await paginator.respond(interaction)

    paginator.update_buttons()
    page = paginator.get_page_content(paginator.pages[paginator.current_page])
    if page.custom_view:
        paginator.update_custom_view(page.custom_view)
    paginator.user = interaction.user
    paginator.message = await interaction.followup.send(
        content=page.content,
        embeds=page.embeds,
        files=page.files,
        view=paginator,
    )
    return paginator.message
