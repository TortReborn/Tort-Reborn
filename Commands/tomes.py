"""
Tome requests (TAQ-116): the "Request tomes" panel button and the tome log.

The button starts a private exchange: war tomes or shell tomes, how many,
which priority, then a confirmation showing the cost. Confirming queues the
request (Helpers/tomes.request_tomes), taking the shells for shell tomes.

Exec gives tomes out on the website's Distribution page, which writes a
'given' event per request, with any screenshots of the in-game grant. The
loop here posts requests, gives (with their screenshots) and cancellations to
the tome log channel, pinging the member, and stamps them announced. The channel is the 'tome_logs_channel_id' bot setting; until it is
set nothing is posted and the events wait. The list of what is waiting is the
panels' {{tome_queue}} value, not this.
"""

import asyncio
import io
from itertools import groupby

import discord
from discord.ext import commands, tasks

from Helpers import tomes
from Helpers.bot_panels import PanelAction
from Helpers.database import DB
from Helpers.functions import getPlayerDatav3
from Helpers.logger import log, ERROR
from Helpers.storage import storage

TOME_REQUEST_BUTTON_ID = "tome_request"
TOME_PANEL_ACTIONS = {
    TOME_REQUEST_BUTTON_ID: PanelAction(TOME_REQUEST_BUTTON_ID, "Request tomes", "Tomes", "\U0001F4D8", "primary"),
}
CHANNEL_SETTING = "tome_logs_channel_id"
ANNOUNCE_SECONDS = 30
LINES_PER_POST = 20  # keeps a post inside Discord's 2000 characters
STEP_TIMEOUT = 300


# ---- data (run in a thread) ----

def _member(discord_id: int):
    db = DB(); db.connect()
    try:
        db.cursor.execute("SELECT uuid::text, ign FROM discord_links WHERE discord_id = %s AND uuid IS NOT NULL", (discord_id,))
        return db.cursor.fetchone()
    finally:
        db.close()


def _lifetime_wars(uuid: str) -> tuple[int | None, bool]:
    """(wars, from a snapshot). The API's count, or the bot's latest snapshot
    when the member hides it."""
    data = getPlayerDatav3(uuid)
    wars = (data.get("globalData") or {}).get("wars") if data else None
    if wars is not None:
        return int(wars), False
    db = DB(); db.connect()
    try:
        return tomes.snapshot_wars(db.cursor, uuid), True
    finally:
        db.close()


def _standing(uuid: str, discord_id: int, wars: int | None) -> tomes.Standing:
    db = DB(); db.connect()
    try:
        return tomes.standing(db.cursor, uuid, discord_id, wars)
    finally:
        db.close()


def _request(uuid, discord_id, ign, kind, count, priority, wars) -> dict:
    db = DB(); db.connect()
    try:
        made = tomes.request_tomes(db, uuid=uuid, discord_id=discord_id, ign=ign, kind=kind,
                                   count=count, priority=priority, wars=wars)
        db.connection.commit()
        return made
    except Exception:
        db.connection.rollback()
        raise
    finally:
        db.close()


# ---- the exchange ----

def _summary(ign: str, s: tomes.Standing, from_snapshot: bool) -> discord.Embed:
    if s.wars is None:
        war = "Your war count is hidden. Make your stats public in Wynncraft's privacy settings to request war tomes."
    else:
        war = (f"{s.war_earned} earned from {s.wars:,} lifetime wars{' (last count the bot saw)' if from_snapshot else ''}, "
               f"{s.war_received} received, {s.war_queued} queued.\n"
               f"**You can request {s.war_available}.** The next needs {s.next_war_tome_at:,} wars.")
    raid = (f"{s.raid_received} received, {s.raid_queued} queued. The next costs {s.next_raid_price} shells "
            f"and you have {s.shells}.\n**You can afford {s.raid_affordable}.**")
    e = discord.Embed(title=f"\U0001F4D8 Tomes for {ign}", color=0x94C1FF)
    e.add_field(name="War tomes", value=war, inline=False)
    e.add_field(name="Shell tomes", value=raid, inline=False)
    return e


class _Request:
    """What the member has picked so far."""

    def __init__(self, uuid, discord_id, ign, wars, standing):
        self.uuid, self.discord_id, self.ign, self.wars, self.standing = uuid, discord_id, ign, wars, standing
        self.kind = self.count = self.priority = None

    def what(self) -> str:
        return f"**{self.count} {tomes.KIND_WORDS[self.kind]} tome{'s' if self.count != 1 else ''}**"

    def queued_line(self) -> str:
        """The reply once the request is in."""
        return (f"\u2705 Queued {self.cost_line() if self.kind == 'raid' else self.what()}. "
                "When you are given the tomes in-game, you will be pinged in the tome log channel. "
                "Claim them from the guild rewards menu.")

    def cost_line(self) -> str:
        what = self.what()
        if self.kind == "raid":
            return f"{what} for **{tomes.raid_tomes_cost(self.standing.raid_bought, self.count)} shells**"
        return f"{what} (no shells; war tomes come from your wars)"


class KindView(discord.ui.View):
    def __init__(self, req: _Request):
        super().__init__(timeout=STEP_TIMEOUT)
        self.req = req
        self.war.disabled = req.standing.war_available < 1
        self.raid.disabled = req.standing.raid_affordable < 1

    async def _pick(self, interaction, kind):
        self.req.kind = kind
        await interaction.response.send_modal(CountModal(self.req))

    @discord.ui.button(label="War tomes", style=discord.ButtonStyle.primary, emoji="⚔️")
    async def war(self, _, interaction):
        await self._pick(interaction, "war")

    @discord.ui.button(label="Shell tomes", style=discord.ButtonStyle.success, emoji="\U0001F41A")
    async def raid(self, _, interaction):
        await self._pick(interaction, "raid")


class CountModal(discord.ui.Modal):
    def __init__(self, req: _Request):
        most = req.standing.available(req.kind)
        super().__init__(title=f"How many {tomes.KIND_WORDS[req.kind]} tomes?")
        self.req, self.most = req, most
        self.add_item(discord.ui.InputText(label=f"Number of tomes (up to {most})", placeholder="1", max_length=4))

    async def callback(self, interaction: discord.Interaction):
        raw = (self.children[0].value or "").strip()
        if not raw.isdigit() or not 1 <= int(raw) <= self.most:
            return await interaction.response.send_message(f"Enter a whole number from 1 to {self.most}.", ephemeral=True)
        self.req.count = int(raw)
        await interaction.response.send_message(
            f"{self.req.cost_line()}. Pick a priority, then confirm.", view=ConfirmView(self.req), ephemeral=True)


class ConfirmView(discord.ui.View):
    def __init__(self, req: _Request):
        super().__init__(timeout=STEP_TIMEOUT)
        self.req = req
        self.priority.options = [
            discord.SelectOption(label=label, value=key, emoji=emoji, description=desc)
            for key, (label, emoji, desc) in tomes.PRIORITIES.items()
        ]

    @discord.ui.select(placeholder="Priority", min_values=1, max_values=1, options=[discord.SelectOption(label="-")])
    async def priority(self, select, interaction):
        self.req.priority = select.values[0]
        for option in select.options:  # the dropdown keeps showing the pick after the edit
            option.default = option.value == self.req.priority
        self.confirm.disabled = False
        label = tomes.PRIORITIES[self.req.priority][0]
        await interaction.response.edit_message(content=f"{self.req.cost_line()} at **{label}** priority. Confirm?", view=self)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, disabled=True)
    async def confirm(self, _, interaction):
        await interaction.response.edit_message(content="Queueing your request…", view=None)
        r = self.req
        try:
            await asyncio.to_thread(_request, r.uuid, r.discord_id, r.ign, r.kind, r.count, r.priority, r.wars)
        except tomes.TomeError as e:
            return await interaction.edit_original_response(content=f"❌ {e}")
        except Exception as e:
            log(ERROR, f"Tome request for {r.ign} failed: {e}", context="tomes")
            return await interaction.edit_original_response(content="❌ Something went wrong; nothing was taken. Try again later.")
        await interaction.edit_original_response(content=r.queued_line())


class TomeRequestView(discord.ui.View):
    """The persistent panel button."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Request tomes", style=discord.ButtonStyle.primary, emoji="\U0001F4D8",
                       custom_id=TOME_REQUEST_BUTTON_ID)
    async def request(self, _, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        row = await asyncio.to_thread(_member, interaction.user.id)
        if not row:
            return await interaction.followup.send("❌ You don't have a linked game account. Please use `/link` first.", ephemeral=True)
        uuid, ign = row
        wars, from_snapshot = await asyncio.to_thread(_lifetime_wars, uuid)
        s = await asyncio.to_thread(_standing, uuid, interaction.user.id, wars)
        req = _Request(uuid, interaction.user.id, ign, wars, s)
        await interaction.followup.send(embed=_summary(ign, s, from_snapshot), view=KindView(req), ephemeral=True)


# ---- tome log ----

def _unannounced():
    db = DB(); db.connect()
    try:
        db.cursor.execute("SELECT value FROM bot_settings WHERE key = %s", (CHANNEL_SETTING,))
        row = db.cursor.fetchone()
        if not row:
            return None, []
        db.cursor.execute(
            """
            SELECT e.id, e.action, e.count, e.kind, e.actor_name, COALESCE(r.ign, l.ign), COALESCE(r.discord_id, l.discord_id),
                   e.screenshots, r.priority, e.note, e.shells
            FROM tome_events e
            LEFT JOIN tome_requests r ON r.id = e.request_id
            LEFT JOIN discord_links l ON l.uuid = e.uuid
            WHERE e.action IN ('requested', 'given', 'cancelled') AND e.announced_at IS NULL
            ORDER BY e.created_at, e.id
            """
        )
        return int(row[0]), db.cursor.fetchall()
    finally:
        db.close()


def _stamp(ids):
    db = DB(); db.connect()
    try:
        db.cursor.execute("UPDATE tome_events SET announced_at = NOW() WHERE id = ANY(%s)", (ids,))
        db.connection.commit()
    finally:
        db.close()


def _tomes(count, kind) -> str:
    return f"{count} {tomes.KIND_WORDS[kind] + ' ' if kind else ''}tome{'s' if count != 1 else ''}"


def grant_line(count, kind, ign, discord_id, actor) -> str:
    who = f"<@{discord_id}> ({ign})" if discord_id else ign
    return f"\U0001F4D8 {who} was given **{_tomes(count, kind)}**" + (f" by {actor}" if actor else "") + "."


def request_line(count, kind, ign, discord_id, priority, shells) -> str:
    who = f"<@{discord_id}> ({ign})" if discord_id else ign
    at = f" at {tomes.PRIORITIES[priority][0]} priority" if priority in tomes.PRIORITIES else ""
    return f"\U0001F4DD {who} requested **{_tomes(count, kind)}**{at}" + (f" ({shells} shells)" if shells else "") + "."


def cancel_line(count, kind, ign, discord_id, actor, shells, reason) -> str:
    who = f"<@{discord_id}> ({ign})" if discord_id else ign
    return (f"\u274C {who}: request for **{_tomes(count, kind)}** was cancelled" + (f" by {actor}" if actor else "")
            + (f", {shells} shells refunded" if shells else "") + "." + (f" Reason: {reason}" if reason else ""))


def log_line(row) -> str:
    _, action, n, kind, actor, ign, discord_id, _, priority, note, shells = row
    if action == "requested":
        return request_line(n, kind, ign, discord_id, priority, shells)
    if action == "cancelled":
        return cancel_line(n, kind, ign, discord_id, actor, shells, note)
    return grant_line(n, kind, ign, discord_id, actor)


def log_posts(rows) -> list[tuple[list[int], str, list[str]]]:
    """Unannounced events as posts: (event ids, text, screenshot keys). A give's
    rows share their screenshots, so they stay together and the screenshots go
    on its first post only."""
    posts = []
    for keys, group in groupby(rows, key=lambda r: r[7] or []):
        group = list(group)
        for i in range(0, len(group), LINES_PER_POST):
            part = group[i:i + LINES_PER_POST]
            posts.append(([r[0] for r in part],
                          "\n".join(log_line(r) for r in part),
                          keys if i == 0 else []))
    return posts


def _screenshot_files(keys) -> list[discord.File]:
    files = []
    for key in keys:
        data = storage.get_bytes(key)
        if data:
            files.append(discord.File(io.BytesIO(data), filename=key.rsplit("/", 1)[-1]))
    return files


class Tomes(commands.Cog):
    def __init__(self, client):
        self.client = client

    @tasks.loop(seconds=ANNOUNCE_SECONDS)
    async def announce(self):
        try:
            channel_id, rows = await asyncio.to_thread(_unannounced)
            if not channel_id or not rows:
                return
            channel = self.client.get_channel(channel_id) or await self.client.fetch_channel(channel_id)
            mentions = discord.AllowedMentions(users=True, roles=False, everyone=False)
            for ids, text, keys in log_posts(rows):
                files = await asyncio.to_thread(_screenshot_files, keys)
                try:
                    await channel.send(text, files=files, allowed_mentions=mentions)
                except discord.HTTPException as e:
                    if e.status != 413:
                        raise
                    # Too big for this server's upload limit: the grant still
                    # gets posted, and the page keeps the screenshots.
                    await channel.send(text + "\nScreenshots are on the Distribution page.", allowed_mentions=mentions)
                await asyncio.to_thread(_stamp, ids)
        except Exception as e:
            log(ERROR, f"Tome log failed: {e}", context="tomes")

    @announce.before_loop
    async def before_announce(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        self.client.add_view(TomeRequestView())
        if not self.announce.is_running():
            self.announce.start()


def setup(client):
    client.add_cog(Tomes(client))
