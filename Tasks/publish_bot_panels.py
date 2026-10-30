"""
Publishes bot panel revisions written by the website (/exec/bot-panels).

Every POLL_SECONDS the newest pending revision of one panel is claimed and
sent: the panel's live messages are edited in place when the message count
still matches, otherwise the new messages are posted first and the old ones
deleted after, so a failed publish never leaves the channel empty. The
outcome (published / failed + the reason) is written back to the revision,
which is what the website shows under the panel's history.

The task also keeps a directory of the home guild's channels, roles and
emoji plus the bot's button registry in cache_entries ('botPanelRegistry'),
so the editor can offer pickers and preview mentions without a bot token.
"""

import asyncio
import contextlib
import datetime
import json
from dataclasses import dataclass

import discord
from discord.ext import commands, tasks

from Commands.generate import GENERATE_EMBED_COLOR, PANEL_ACTIONS
from Helpers.bot_panels import (
    ASSET_PREFIX,
    DYNAMIC_SHELL_EXCHANGE,
    PanelError,
    asset_name,
    panel_messages,
    render_message,
    uses_dynamic,
)
from Helpers.database import DB
from Helpers.logger import log, INFO, ERROR
from Helpers.panel_values import load_panel_values
from Helpers.storage import storage
from Helpers.variables import HOME_GUILD_IDS, TAQ_GUILD_ID

POLL_SECONDS = 10
DIRECTORY_EVERY_LOOPS = 360  # ~1 hour
JOBS_PER_TICK = 10
REGISTRY_CACHE_KEY = "botPanelRegistry"
NO_MENTIONS = discord.AllowedMentions.none()


@dataclass
class PanelJob:
    rev_id: int
    rev_number: int
    panel_id: int
    slug: str
    content: dict
    channel_id: int | None
    adopt_message_ids: list[int]
    panel_channel_id: int | None
    panel_message_ids: list[int]


# ---- DB ----

def _requeue_interrupted():
    """A revision left 'publishing' was cut off by a restart; try it again."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute("UPDATE bot_panel_revisions SET status = 'pending' WHERE status = 'publishing'")
        db.connection.commit()
    finally:
        db.close()


def _claim_next() -> PanelJob | None:
    """Claim the newest pending revision of the panel waiting longest; older
    pending revisions of that panel are superseded, not published in turn."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            """
            SELECT panel_id FROM bot_panel_revisions
            WHERE status = 'pending'
            ORDER BY created_at
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """
        )
        row = db.cursor.fetchone()
        if row is None:
            db.connection.rollback()
            return None
        panel_id = row[0]

        db.cursor.execute(
            """
            SELECT id FROM bot_panel_revisions
            WHERE panel_id = %s AND status = 'pending'
            ORDER BY rev_number DESC
            FOR UPDATE
            """,
            (panel_id,),
        )
        ids = [r[0] for r in db.cursor.fetchall()]
        latest, older = ids[0], ids[1:]
        if older:
            db.cursor.execute(
                "UPDATE bot_panel_revisions SET status = 'superseded' WHERE id = ANY(%s)",
                (older,),
            )
        db.cursor.execute("UPDATE bot_panel_revisions SET status = 'publishing' WHERE id = %s", (latest,))
        db.cursor.execute(
            """
            SELECT r.id, r.rev_number, r.content, r.channel_id, r.adopt_message_ids,
                   p.id, p.slug, p.channel_id, p.message_ids
            FROM bot_panel_revisions r JOIN bot_panels p ON p.id = r.panel_id
            WHERE r.id = %s
            """,
            (latest,),
        )
        r = db.cursor.fetchone()
        db.connection.commit()
        content = r[2] if isinstance(r[2], dict) else json.loads(r[2])
        return PanelJob(
            rev_id=r[0], rev_number=r[1], content=content, channel_id=r[3],
            adopt_message_ids=list(r[4] or []), panel_id=r[5], slug=r[6],
            panel_channel_id=r[7], panel_message_ids=list(r[8] or []),
        )
    except Exception:
        db.connection.rollback()
        raise
    finally:
        db.close()


def _finish(job: PanelJob, *, channel_id: int | None = None, message_ids: list[int] | None = None,
            error: str | None = None, notes: list[str] | None = None):
    db = DB()
    db.connect()
    try:
        if error is None:
            db.cursor.execute(
                """
                UPDATE bot_panels
                SET channel_id = %s, message_ids = %s::bigint[], published_rev = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (channel_id, message_ids, job.rev_number, job.panel_id),
            )
            # A message belongs to one panel. The website refuses to link one that
            # another panel owns; if two saves race past that, the newest owner wins.
            db.cursor.execute(
                """
                UPDATE bot_panels
                SET message_ids = ARRAY(
                        SELECT m FROM unnest(message_ids) WITH ORDINALITY AS t(m, i)
                        WHERE m <> ALL(%s::bigint[]) ORDER BY i
                    ), updated_at = NOW()
                WHERE id <> %s AND message_ids && %s::bigint[]
                """,
                (message_ids, job.panel_id, message_ids),
            )
            db.cursor.execute(
                """
                UPDATE bot_panel_revisions
                SET status = 'published', published_at = NOW(), error = %s
                WHERE id = %s
                """,
                ("\n".join(notes) if notes else None, job.rev_id),
            )
        else:
            db.cursor.execute(
                "UPDATE bot_panel_revisions SET status = 'failed', error = %s WHERE id = %s",
                (error[:1500], job.rev_id),
            )
        db.connection.commit()
    finally:
        db.close()


def _save_registry(data: dict):
    db = DB()
    db.connect()
    try:
        epoch = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
        db.cursor.execute(
            """
            INSERT INTO cache_entries (cache_key, data, expires_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (cache_key) DO UPDATE SET data = EXCLUDED.data, created_at = NOW()
            """,
            (REGISTRY_CACHE_KEY, json.dumps(data), epoch),
        )
        db.connection.commit()
    finally:
        db.close()


# ---- Assets ----

def _referenced_assets(content: dict) -> set[str]:
    found = set()

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            name = asset_name(node)
            if name:
                found.add(name)

    walk(content)
    return found


def _fetch_assets(names: set[str]) -> dict[str, bytes]:
    out = {}
    for name in names:
        data = storage.get_bytes(ASSET_PREFIX + name)
        if data is not None:
            out[name] = data
    return out


class PublishBotPanels(commands.Cog):
    def __init__(self, client):
        self.client = client
        self._loops = 0

    # ---- Loop ----

    @tasks.loop(seconds=POLL_SECONDS)
    async def publish_panels(self):
        if self._loops % DIRECTORY_EVERY_LOOPS == 0:
            try:
                await self._sync_registry()
            except Exception as e:
                log(ERROR, f"Bot panel registry sync failed: {e}", context="publish_bot_panels")
        self._loops += 1

        # Work through everything waiting (a value change can queue several
        # panels at once), capped so one tick can't run away.
        for _ in range(JOBS_PER_TICK):
            job = await asyncio.to_thread(_claim_next)
            if job is None:
                return
            await self._run(job)

    async def _run(self, job: PanelJob):
        try:
            channel_id, message_ids, notes = await self._publish(job)
        except PanelError as e:
            await asyncio.to_thread(_finish, job, error=str(e))
            return
        except discord.HTTPException as e:
            await asyncio.to_thread(_finish, job, error=f"Discord refused the message: {e.text or e}")
            return
        except Exception as e:
            log(ERROR, f"Publishing bot panel {job.slug} rev {job.rev_number} failed: {e}",
                context="publish_bot_panels")
            await asyncio.to_thread(_finish, job, error=f"Unexpected error: {type(e).__name__}: {e}")
            return
        await asyncio.to_thread(_finish, job, channel_id=channel_id, message_ids=message_ids, notes=notes)
        log(INFO, f"Published bot panel {job.slug} rev {job.rev_number}", context="publish_bot_panels")

    @publish_panels.before_loop
    async def before_publish_panels(self):
        await self.client.wait_until_ready()
        await asyncio.to_thread(_requeue_interrupted)

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.publish_panels.is_running():
            self.publish_panels.start()

    # ---- Publishing ----

    async def _resolve_channel(self, channel_id: int | None):
        if not channel_id:
            raise PanelError("Pick a channel for this panel before publishing.")
        channel = self.client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.client.fetch_channel(channel_id)
            except discord.HTTPException:
                raise PanelError(f"The bot can't see channel {channel_id}.")
        if getattr(channel, "guild", None) is None or channel.guild.id not in HOME_GUILD_IDS:
            raise PanelError("Panels can only be posted in the guild's own servers.")
        if not isinstance(channel, discord.abc.Messageable):
            raise PanelError(f"#{channel} is not a channel the bot can post in.")
        return channel

    @contextlib.asynccontextmanager
    async def _open_threads(self, *channels):
        """Discord refuses to edit or post in an archived thread (forum posts
        archive themselves after a few days), so open the panel's threads for
        the publish and archive them again afterwards. A locked one is left to
        a human: unlocking is not the bot's call."""
        reopened = []
        try:
            for ch in {c.id: c for c in channels if isinstance(c, discord.Thread)}.values():
                fresh = await self.client.fetch_channel(ch.id)
                if fresh.locked:
                    raise PanelError(f"The thread {fresh.name!r} is locked; unlock it to publish this panel.")
                if fresh.archived:
                    await fresh.edit(archived=False)
                    reopened.append(fresh)
            yield
        finally:
            for thread in reopened:
                try:
                    await thread.edit(archived=True)
                except discord.HTTPException:
                    pass

    async def _fetch_messages(self, channel, ids: list[int]) -> list[discord.Message | None]:
        out = []
        for mid in ids:
            try:
                out.append(await channel.fetch_message(mid))
            except discord.NotFound:
                out.append(None)
        return out

    async def _publish(self, job: PanelJob) -> tuple[int, list[int], list[str]]:
        messages = panel_messages(job.content)
        notes: list[str] = []

        dynamic_files = {}
        shell_cog = None
        if uses_dynamic(job.content, DYNAMIC_SHELL_EXCHANGE):
            shell_cog = self.client.get_cog("ShellExchange")
            if shell_cog is None:
                raise PanelError("The shell exchange module isn't loaded on the bot.")
            files, skipped = await asyncio.to_thread(shell_cog.generate_panel_files)
            if not files:
                raise PanelError("The shell exchange produced no rate images (no items toggled on?).")
            dynamic_files[DYNAMIC_SHELL_EXCHANGE] = files
            if skipped:
                notes.append("Missing icons left off the rate images: " + ", ".join(sorted(set(skipped))))

        assets = await asyncio.to_thread(_fetch_assets, _referenced_assets(job.content))
        values = await asyncio.to_thread(load_panel_values)
        rendered = [
            render_message(
                i, m,
                actions=PANEL_ACTIONS, values=values, fetch_asset=assets.get,
                dynamic_files=dynamic_files, default_color=GENERATE_EMBED_COLOR,
            )
            for i, m in enumerate(messages)
        ]

        channel = await self._resolve_channel(job.channel_id)
        bot_id = self.client.user.id

        if job.adopt_message_ids:
            current = await self._fetch_messages(channel, job.adopt_message_ids)
            for mid, msg in zip(job.adopt_message_ids, current):
                if msg is None:
                    raise PanelError(f"Linked message {mid} isn't in #{channel}.")
                if msg.author.id != bot_id:
                    raise PanelError(f"Linked message {mid} was posted by {msg.author}, not the bot, so it can't be edited.")
            old_channel = channel
        elif job.panel_channel_id == channel.id:
            current = await self._fetch_messages(channel, job.panel_message_ids)
            old_channel = channel
        else:
            old_channel = None
            if job.panel_channel_id and job.panel_message_ids:
                try:
                    old_channel = await self._resolve_channel(job.panel_channel_id)
                    current = await self._fetch_messages(old_channel, job.panel_message_ids)
                except PanelError:
                    current = []
            else:
                current = []
        current = [m for m in current if m is not None]

        # Discord can't turn an embed message into a Components V2 one or back,
        # so edit in place only when every message keeps its kind.
        in_place = (
            old_channel is channel
            and len(current) == len(rendered)
            and all(m.author.id == bot_id and m.flags.is_components_v2 == r.box for m, r in zip(current, rendered))
        )

        async with self._open_threads(channel, *([old_channel] if old_channel else [])):
            new_ids = await self._write(channel, current, rendered, in_place, bot_id, notes)

        if shell_cog is not None:
            dynamic_index = next(i for i, m in enumerate(messages) if m.get("dynamic") == DYNAMIC_SHELL_EXCHANGE)
            r = rendered[dynamic_index]
            text_embed = r.copy_embed if r.box else (r.embeds[0] if r.embeds else None)
            try:
                await shell_cog.after_panel_publish(text_embed, dynamic_files[DYNAMIC_SHELL_EXCHANGE])
            except Exception as e:
                notes.append(f"Posted, but the rates announcement / legacy copy failed: {e}")

        return channel.id, new_ids, notes

    async def _write(self, channel, current, rendered, in_place, bot_id, notes) -> list[int]:
        """Edit the panel's messages in place, or post fresh ones and delete the old."""
        if in_place:
            for msg, r in zip(current, rendered):
                if r.box:
                    await msg.edit(view=r.view, attachments=[], files=r.files, allowed_mentions=NO_MENTIONS)
                else:
                    await msg.edit(
                        content=r.content, embeds=r.embeds, attachments=[], files=r.files,
                        view=r.view, allowed_mentions=NO_MENTIONS,
                    )
            new_ids = [m.id for m in current]
        else:
            sent = []
            try:
                for r in rendered:
                    if r.box:
                        sent.append(await channel.send(view=r.view, files=r.files, allowed_mentions=NO_MENTIONS))
                    else:
                        sent.append(await channel.send(
                            content=r.content, embeds=r.embeds, files=r.files,
                            view=r.view, allowed_mentions=NO_MENTIONS,
                        ))
            except Exception:
                for msg in sent:
                    try:
                        await msg.delete()
                    except discord.HTTPException:
                        pass
                raise
            new_ids = [m.id for m in sent]
            for msg in current:
                if msg.author.id != bot_id:
                    continue
                try:
                    await msg.delete()
                except discord.HTTPException:
                    notes.append(f"Couldn't delete the old message {msg.id}; remove it by hand.")
        return new_ids

    # ---- Registry for the editor ----

    async def _sync_registry(self):
        guild = self.client.get_guild(TAQ_GUILD_ID)
        channels, roles, emojis = [], [], []
        if guild is not None:
            for ch in guild.channels:
                if not isinstance(ch, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel)):
                    continue
                channels.append({
                    "id": str(ch.id), "name": ch.name, "type": ch.type.name,
                    "category": ch.category.name if ch.category else None,
                    "postable": isinstance(ch, discord.TextChannel),
                    "sort": [ch.category.position if ch.category else -1, ch.position],
                })
            channels.sort(key=lambda c: c.pop("sort"))
            roles = [
                {"id": str(r.id), "name": r.name, "color": r.color.value}
                for r in sorted(guild.roles, key=lambda r: -r.position)
                if not r.is_default()
            ]
        # The bot can post custom emojis from every server it is in, so offer the
        # main server's first, then the other home servers', then its app emojis.
        # One entry per name, so a :name: in the editor always means one emoji.
        sources = [guild] + [self.client.get_guild(g) for g in HOME_GUILD_IDS if g != TAQ_GUILD_ID]
        seen = set()
        for source, pool in [(g.name, g.emojis) for g in sources if g] + [(None, list(self.client.app_emojis))]:
            for e in pool:
                if e.name.lower() in seen:
                    continue
                seen.add(e.name.lower())
                emojis.append({"id": str(e.id), "name": e.name, "animated": e.animated,
                               "server": source if source != (guild.name if guild else None) else None})

        await asyncio.to_thread(_save_registry, {
            "actions": [
                {"key": key, "label": a.label, "group": a.group, "emoji": a.emoji, "style": a.style}
                for key, a in PANEL_ACTIONS.items()
            ],
            "dynamic": [{"key": DYNAMIC_SHELL_EXCHANGE, "label": "Shell exchange rate images"}],
            "default_color": GENERATE_EMBED_COLOR,
            "guild": {"id": str(guild.id), "name": guild.name} if guild else None,
            "channels": channels,
            "roles": roles,
            "emojis": emojis,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })


def setup(client):
    client.add_cog(PublishBotPanels(client))
