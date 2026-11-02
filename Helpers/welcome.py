"""
Helpers/welcome.py
What a new guild member gets on registration (TAQ-142): the "Welcome Aboard!"
post in the welcome channel and the welcome DM.

Both are template panels, edited on the website (/exec/bot-panels) like any
bot panel; the bot sends the published revision with the member's values
filled in (Helpers/bot_panels.member_values). When a template is missing or
won't render, the built-in text below goes out instead, so a broken edit
never leaves someone unwelcomed.
"""

import asyncio
import json

import discord

from Helpers.bot_panels import (
    ASSET_PREFIX,
    TEMPLATE_WELCOME_DM,
    TEMPLATE_WELCOME_POST,
    RenderedMessage,
    check_template,
    member_values,
    panel_messages,
    referenced_assets,
    render_message,
)
from Helpers.database import DB
from Helpers.logger import log, INFO, WARN
from Helpers.panel_values import load_panel_values
from Helpers.storage import storage
from Helpers.variables import (
    ANNOUNCEMENT_CHANNEL_ID,
    BOT_COMMAND_CHANNEL_ID,
    FAQ_CHANNEL_ID,
    GUILD_BANK_CHANNEL_ID,
    RAID_COLLECTING_CHANNEL_ID,
    TAQ_ROLES_CHANNEL_ID,
    WELCOME_CHANNEL_ID,
)

WELCOME_COLOR = 0x94C1FF
FALLBACK_DM_PATH = "data/guild_welcome_dm.json"

# Asset names are content hashes, so a cached image is never stale.
_assets: dict[str, bytes] = {}


def _fetch_asset(name: str) -> bytes | None:
    if name not in _assets:
        data = storage.get_bytes(ASSET_PREFIX + name)
        if data is None:
            return None
        _assets[name] = data
    return _assets[name]


def load_published(template: str) -> dict | None:
    """The live revision of a template, or None when none is published."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            """
            SELECT r.content FROM bot_panels p
            JOIN bot_panel_revisions r ON r.panel_id = p.id AND r.rev_number = p.published_rev
            WHERE p.template = %s
            """,
            (template,),
        )
        row = db.cursor.fetchone()
    finally:
        db.close()
    if row is None:
        return None
    return row[0] if isinstance(row[0], dict) else json.loads(row[0])


def _load_inputs(content: dict) -> dict[str, str]:
    """Blocking: the panel values, with the content's images fetched into the cache."""
    for name in referenced_assets(content):
        _fetch_asset(name)
    return load_panel_values()


async def render_template(content: dict, member: dict[str, str]) -> list[RenderedMessage]:
    """Raises PanelError when the content can't be sent as written. Renders on
    the event loop: discord.ui views can't be built in a worker thread."""
    check_template(content)
    values = {**await asyncio.to_thread(_load_inputs, content), **member}
    return [
        render_message(i, m, actions={}, values=values, fetch_asset=_assets.get, default_color=WELCOME_COLOR)
        for i, m in enumerate(panel_messages(content))
    ]


async def send_rendered(destination: discord.abc.Messageable, rendered: list[RenderedMessage], member_id: int):
    """Send in order. Only the member can be pinged, whatever the text says."""
    mentions = discord.AllowedMentions(everyone=False, roles=False, users=[discord.Object(member_id)])
    for r in rendered:
        if r.box:
            await destination.send(view=r.view, files=r.fresh_files(), allowed_mentions=mentions)
        else:
            await destination.send(
                content=r.content, embeds=r.embeds, files=r.fresh_files(),
                view=r.view, allowed_mentions=mentions,
            )


async def _render_published(template: str, member: discord.Member, ign: str) -> list[RenderedMessage] | None:
    """The template ready to send, or None to use the built-in text."""
    values = member_values(member.mention, ign, member.display_avatar.url)
    try:
        content = await asyncio.to_thread(load_published, template)
        if content is None:
            return None
        return await render_template(content, values)
    except Exception as e:
        log(WARN, f"Welcome template {template} unusable, sending the built-in one: {e}", context="welcome")
        return None


# ---- Built-in fallbacks ----

def _fallback_post(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        description=f":ocean: Dive right in, {member.mention}! The water's fine.",
        color=discord.Color.blue(),
    )
    embed.set_author(name="Welcome Aboard!", icon_url=member.display_avatar.url)
    return embed


def _fallback_dm(member: discord.Member) -> str | None:
    try:
        with open(FALLBACK_DM_PATH, "r", encoding="utf-8") as f:
            template_data = json.load(f)
    except Exception as e:
        log(WARN, f"Could not load welcome DM template: {e}", context="welcome")
        return None

    placeholders = {
        "[user]": member.mention,
        "[welcome_channel]": f"<#{WELCOME_CHANNEL_ID}>",
        "[announcement_channel]": f"<#{ANNOUNCEMENT_CHANNEL_ID}>",
        "[faq_channel]": f"<#{FAQ_CHANNEL_ID}>",
        "[guild_bank_channel]": f"<#{GUILD_BANK_CHANNEL_ID}>",
        "[taq_roles_channel]": f"<#{TAQ_ROLES_CHANNEL_ID}>",
        "[bot_command_channel]": f"<#{BOT_COMMAND_CHANNEL_ID}>",
        "[raid_collecting_channel]": f"<#{RAID_COLLECTING_CHANNEL_ID}>",
    }

    def replace(text):
        for key, value in placeholders.items():
            text = text.replace(key, str(value))
        return text

    header = replace(template_data.get("header", ""))
    channels_title = template_data.get("channels_title", "")
    channels = "\n".join(
        f"> {replace(ch['channel'])} - {ch['description']}"
        for ch in template_data.get("channels", [])
    )
    mod = replace(template_data.get("mod", ""))
    footer = replace(template_data.get("footer", ""))

    return "\n\n".join(part for part in (header, channels_title, channels, mod, footer) if part)


# ---- Sending ----

async def send_welcome_post(channel: discord.abc.Messageable, member: discord.Member, ign: str):
    rendered = await _render_published(TEMPLATE_WELCOME_POST, member, ign)
    if rendered is None:
        await channel.send(embed=_fallback_post(member))
        return
    await send_rendered(channel, rendered, member.id)


async def send_welcome_dm(member: discord.Member, ign: str):
    """Never raises: a member with DMs closed just doesn't get one."""
    try:
        rendered = await _render_published(TEMPLATE_WELCOME_DM, member, ign)
        if rendered is None:
            text = _fallback_dm(member)
            if text is None:
                return
            await member.send(text, allowed_mentions=discord.AllowedMentions.none())
        else:
            await send_rendered(member, rendered, member.id)
        log(INFO, f"Sent welcome DM to {member.name}", context="welcome")
    except discord.Forbidden:
        log(WARN, f"Could not DM {member.name} (DMs disabled or bot blocked)", context="welcome")
    except Exception as e:
        log(WARN, f"Failed to send welcome DM to {member.name}: {e}", context="welcome")
