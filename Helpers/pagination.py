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
