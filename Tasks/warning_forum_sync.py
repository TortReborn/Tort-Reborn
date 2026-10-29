"""Mirror the website's warning tracker into Discord (TAQ-79).

Every minute:
  1. Forum (WARNING_FORUM_CHANNEL_ID, TE): one post per player. New profiles
     get a post (at most NEW_POSTS_PER_TICK per tick, so the first run after
     the backfill doesn't burst ~35 posts), new entries are posted into their
     thread, edited entries and summaries are re-rendered, tags kept current.
  2. Role (DISHONORED_FISH_ROLE_ID, main Discord): everyone with an active
     dishonored_fish entry holds the role, which also covers rejoins; a profile
     whose last dishonored_fish entry was revoked loses it.

A listener records the role being changed by hand: given -> a new entry,
removed -> the active entries are revoked. Changes the bot made itself are
ignored. Holders the tracker doesn't know about are reported once, never
removed.

Either feature is off while its id is None in Helpers/variables.py.
"""

import asyncio
import datetime
import io
import json

import discord
from discord.ext import commands, tasks

from Helpers import warnings as warn
from Helpers.database import DB
from Helpers.logger import ERROR, INFO, WARN, log
from Helpers.storage import storage
from Helpers.variables import (
    BOT_LOG_CHANNEL_ID,
    DISHONORED_FISH_ROLE_ID,
    EXEC_GUILD_ID,
    TAQ_GUILD_ID,
    WARNING_FORUM_CHANNEL_ID,
    WEBSITE_URL,
    is_home_guild,
)

NEW_POSTS_PER_TICK = 5
MESSAGES_PER_TICK = 20
ROLE_MARKER_KEY = 'warning_role_synced_until'
REPORTED_KEY = 'warning_role_reported_holders'


# --- blocking DB wrappers (run via asyncio.to_thread) -------------------------

def _with_cursor(fn, *args, commit=False):
    db = DB()
    db.connect()
    try:
        result = fn(db.cursor, *args)
        if commit:
            db.connection.commit()
        return result
    finally:
        db.close()


def _get_setting(cursor, key):
    cursor.execute("SELECT value FROM bot_settings WHERE key = %s", (key,))
    row = cursor.fetchone()
    return row[0] if row else None


def _set_setting(cursor, key, value):
    cursor.execute(
        "INSERT INTO bot_settings (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (key, value),
    )


class WarningForumSync(commands.Cog):
    def __init__(self, client: discord.Bot):
        self.client = client
        self._started = False

    # -- lifecycle ------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self):
        if self._started or not (WARNING_FORUM_CHANNEL_ID or DISHONORED_FISH_ROLE_ID):
            return
        self._started = True
        released = await asyncio.to_thread(_with_cursor, warn.reset_stale_claims, commit=True)
        if released:
            log(WARN, f"Released {released} warning claim(s) left by a restart", context='warning_sync')
        self.sync_loop.start()

    @tasks.loop(minutes=1)
    async def sync_loop(self):
        if WARNING_FORUM_CHANNEL_ID:
            try:
                await self._sync_forum()
            except Exception as e:
                log(ERROR, f"forum sync failed: {e!r}", context='warning_sync')
        if DISHONORED_FISH_ROLE_ID:
            try:
                await self._sync_role()
            except Exception as e:
                log(ERROR, f"role sync failed: {e!r}", context='warning_sync')

    @sync_loop.before_loop
    async def _before(self):
        await self.client.wait_until_ready()

    # -- forum ----------------------------------------------------------------

    async def _forum(self) -> discord.ForumChannel | None:
        forum = self.client.get_channel(WARNING_FORUM_CHANNEL_ID)
        if not isinstance(forum, discord.ForumChannel):
            log(ERROR, f"Warning forum {WARNING_FORUM_CHANNEL_ID} not found or not a forum", context='warning_sync')
            return None
        if not forum.guild or not is_home_guild(forum.guild.id) or forum.guild.id != EXEC_GUILD_ID:
            log(ERROR, "Warning forum is not in the exec guild - skipping", context='warning_sync')
            return None
        return forum

    async def _sync_forum(self):
        forum = await self._forum()
        if forum is None:
            return
        work = await asyncio.to_thread(_with_cursor, warn.profiles_needing_work)
        new_posts = 0
        budget = [MESSAGES_PER_TICK]
        for uuid, has_thread in work:
            if budget[0] <= 0:
                break
            if not has_thread:
                if new_posts >= NEW_POSTS_PER_TICK:
                    continue
                new_posts += 1
            try:
                await self._sync_profile(forum, uuid, budget)
            except Exception as e:
                log(ERROR, f"profile {uuid}: {e!r}", context='warning_sync')

    async def _thread_for(self, forum, profile):
        """The profile's thread, created if missing (or deleted in Discord). Unarchived."""
        thread = None
        if profile['forum_thread_id']:
            thread = forum.guild.get_thread(profile['forum_thread_id'])
            if thread is None:
                try:
                    thread = await self.client.fetch_channel(profile['forum_thread_id'])
                except discord.NotFound:
                    thread = None
        if thread is None:
            thread = await forum.create_thread(
                name=profile['ign'][:100],
                embed=warn.summary_embed(profile, profile['entries'], WEBSITE_URL),
                applied_tags=self._tags(forum, profile),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await asyncio.to_thread(_with_cursor, warn.set_thread, profile['uuid'], thread.id, commit=True)
            profile['forum_thread_id'] = thread.id
            # A recreated post has none of the old entry messages: post them all again.
            for e in profile['entries']:
                if e['discord_message_id'] and e['discord_message_id'] > 0:
                    e['discord_message_id'] = None
                    e['repost'] = True
        elif thread.archived:
            await thread.edit(archived=False)
        return thread

    @staticmethod
    def _tags(forum, profile):
        wanted = set(warn.tag_names(warn.count_active(profile['entries'])))
        return [t for t in forum.available_tags if t.name in wanted][:5]

    async def _files(self, rows):
        files = []
        for filename, _ct, key in rows:
            data = await asyncio.to_thread(storage.get_bytes, key)
            if data:
                files.append(discord.File(io.BytesIO(data), filename=filename))
        return files

    async def _sync_profile(self, forum, uuid, budget):
        rendered_at = warn.utcnow()
        profile = await asyncio.to_thread(_with_cursor, warn.load_profile, uuid)
        if profile is None:
            return
        thread = await self._thread_for(forum, profile)

        # New entries (and, after a recreated post, every mirrored entry), oldest first.
        for e in reversed(profile['entries']):
            if budget[0] <= 0:
                return
            if e['discord_message_id'] is None:
                if not e.get('repost'):
                    claimed = await asyncio.to_thread(_with_cursor, warn.claim_entry, e['id'], commit=True)
                    if not claimed:
                        continue
                try:
                    files = await self._files(await asyncio.to_thread(_with_cursor, warn.attachments_for, e['id']))
                    msg = await thread.send(embed=warn.entry_embed(e), files=files or None,
                                            allowed_mentions=discord.AllowedMentions.none())
                except Exception:
                    if not e.get('repost'):
                        await asyncio.to_thread(_with_cursor, warn.release_claim, e['id'], commit=True)
                    raise
                await asyncio.to_thread(_with_cursor, warn.mark_posted, e['id'], msg.id, rendered_at, commit=True)
                budget[0] -= 1
            elif (e['discord_message_id'] > 0
                  and (e['discord_synced_at'] is None or e['updated_at'] > e['discord_synced_at'])):
                # Edited on the website: re-render, and post screenshots added since.
                try:
                    msg = await thread.fetch_message(e['discord_message_id'])
                    await msg.edit(embed=warn.entry_embed(e), allowed_mentions=discord.AllowedMentions.none())
                except discord.NotFound:
                    pass
                new_files = await asyncio.to_thread(
                    _with_cursor, warn.attachments_for, e['id'], e['discord_synced_at'])
                if new_files:
                    files = await self._files(new_files)
                    if files:
                        await thread.send(content=f"More proof for the {warn.KIND_LABELS[e['kind']].lower()} above:",
                                          files=files, allowed_mentions=discord.AllowedMentions.none())
                await asyncio.to_thread(_with_cursor, warn.mark_synced, e['id'], rendered_at, commit=True)
                budget[0] -= 1

        # Summary, name and tags.
        if profile['needs_sync']:
            starter = getattr(thread, 'starting_message', None)
            if starter is None:
                try:
                    starter = await thread.fetch_message(thread.id)
                except discord.NotFound:
                    starter = None
            if starter is not None:
                await starter.edit(embed=warn.summary_embed(profile, profile['entries'], WEBSITE_URL))
            edits = {'applied_tags': self._tags(forum, profile)}
            if thread.name != profile['ign'][:100]:
                edits['name'] = profile['ign'][:100]
            await thread.edit(**edits)
            await asyncio.to_thread(_with_cursor, warn.clear_needs_sync, uuid, rendered_at, commit=True)

    # -- Dishonored Fish role ---------------------------------------------------

    def _role(self):
        """The role and its guild. The main Discord in prod; role ids are global,
        so a test setup may keep it in any home guild."""
        guilds = [self.client.get_guild(TAQ_GUILD_ID)] + [g for g in self.client.guilds if is_home_guild(g.id)]
        for guild in guilds:
            role = guild.get_role(DISHONORED_FISH_ROLE_ID) if guild else None
            if role is not None:
                return guild, role
        return None, None

    async def _sync_role(self):
        guild, role = self._role()
        if role is None:
            log(ERROR, f"Dishonored Fish role {DISHONORED_FISH_ROLE_ID} not found", context='warning_sync')
            return

        def read(cursor):
            return (warn.dishonored_holders(cursor),
                    _get_setting(cursor, ROLE_MARKER_KEY),
                    _get_setting(cursor, REPORTED_KEY))
        holders, marker, reported = await asyncio.to_thread(_with_cursor, read)
        since = datetime.datetime.fromisoformat(marker) if marker else None
        changes = await asyncio.to_thread(_with_cursor, warn.dishonored_changes_since, since)

        # Revoked on the website: remove the role from profiles with nothing active left.
        newest = since
        for discord_id, _uuid, still_active, changed_at in changes:
            newest = max(newest, changed_at) if newest else changed_at
            if still_active or not discord_id:
                continue
            member = guild.get_member(discord_id)
            if member and role in member.roles:
                await member.remove_roles(role, reason='Dishonored Fish revoked in the warning tracker')

        # Everyone active holds it. Also covers rejoins and entries just added.
        for discord_id in holders:
            member = guild.get_member(discord_id)
            if member and role not in member.roles:
                await member.add_roles(role, reason='Dishonored Fish in the warning tracker')

        # Holders the tracker doesn't know about: report each once, never remove.
        reported_ids = set(json.loads(reported)) if reported else set()
        unknown = [m for m in role.members if m.id not in holders and m.id not in reported_ids]
        if unknown:
            ch = self.client.get_channel(BOT_LOG_CHANNEL_ID)
            if ch:
                await ch.send(
                    "## Dishonored Fish holders not in the warning tracker\n"
                    + '\n'.join(f"- {m.mention} ({m.name})" for m in unknown)
                    + "\nAdd an entry on the website if they should keep it. The bot won't remove the role.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            reported_ids.update(m.id for m in unknown)

        def write(cursor):
            if newest:
                _set_setting(cursor, ROLE_MARKER_KEY, newest.isoformat())
            if unknown:
                _set_setting(cursor, REPORTED_KEY, json.dumps(sorted(reported_ids)))
        if (newest and newest != since) or unknown:
            await asyncio.to_thread(_with_cursor, write, commit=True)

    async def _role_actor(self, member, role, added):
        """Who changed the role, from the audit log. None if it can't be read."""
        try:
            async for entry in member.guild.audit_logs(limit=10, action=discord.AuditLogAction.member_role_update):
                if entry.target and entry.target.id == member.id:
                    roles = getattr(entry.after, 'roles', []) if added else getattr(entry.before, 'roles', [])
                    if any(r.id == role.id for r in roles or []):
                        return entry.user
        except discord.Forbidden:
            log(WARN, "No audit log access; hand role changes are recorded without who did it", context='warning_sync')
        return None

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not DISHONORED_FISH_ROLE_ID or not is_home_guild(after.guild.id):
            return
        had = any(r.id == DISHONORED_FISH_ROLE_ID for r in before.roles)
        has = any(r.id == DISHONORED_FISH_ROLE_ID for r in after.roles)
        if had == has:
            return
        role = after.guild.get_role(DISHONORED_FISH_ROLE_ID)
        actor = await self._role_actor(after, role, added=has)
        if actor is not None and actor.id == self.client.user.id:
            return  # our own sync
        actor_id = actor.id if actor else self.client.user.id

        try:
            if has:
                result = await asyncio.to_thread(
                    _with_cursor, warn.record_role_added, after.id, actor_id, commit=True)
                if result == 'unlinked':
                    ch = self.client.get_channel(BOT_LOG_CHANNEL_ID)
                    if ch:
                        await ch.send(
                            f"{after.mention} was given Dishonored Fish"
                            + (f" by {actor.mention}" if actor else '')
                            + ", but they have no linked Minecraft account, so it isn't in the warning tracker. "
                              "Add it on the website once you know their name.",
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                else:
                    log(INFO, f"Dishonored Fish given by hand to {after.name}: {result}", context='warning_sync')
            else:
                revoked = await asyncio.to_thread(
                    _with_cursor, warn.record_role_removed, after.id, actor_id, commit=True)
                log(INFO, f"Dishonored Fish removed by hand from {after.name}: revoked {revoked}", context='warning_sync')
        except Exception as e:
            log(ERROR, f"recording hand role change for {after.name} failed: {e!r}", context='warning_sync')


def setup(client):
    client.add_cog(WarningForumSync(client))
