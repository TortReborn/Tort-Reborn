import asyncio
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands
from discord.commands import SlashCommandGroup

from Helpers.database import DB
from Helpers.openai_helper import query as openai_query
from Helpers.variables import error_channel, meeting_announcement_channel, executive_role_id

# =============================================================================
# Constants
# =============================================================================

GMT = timezone.utc

AGENDA_SYSTEM_PROMPT = """You format meeting agendas for a gaming guild Discord server called "The Aquarium" [TAq].

Given selected topics and notes, produce a meeting agenda using this exact format:

**Meeting Agenda:**

> Business As Usual
- [Topic]: [Details if any]
- [Topic]: [Details if any]

> [Requested Topic Name]
- [Details/Description]

> Other
- [Any additional notes]

Rules:
- Use > for section headers (Discord block quotes)
- Use - for bullet points under each section
- Each requested topic that has a description gets its own > section
- Requested topics without descriptions can be grouped under a single > section
- Only include the "Other" section if there are additional notes provided
- Keep it concise - do not add filler text
- Do NOT include any @everyone or timestamp lines
- Output ONLY the agenda text, nothing else"""

CREATE_BAU_TABLE = """
CREATE TABLE IF NOT EXISTS agenda_bau_topics (
    id          SERIAL       PRIMARY KEY,
    topic       VARCHAR(100) NOT NULL UNIQUE,
    description TEXT
);
"""

CREATE_REQ_TABLE = """
CREATE TABLE IF NOT EXISTS agenda_requested_topics (
    id           SERIAL       PRIMARY KEY,
    topic        VARCHAR(100) NOT NULL,
    description  TEXT,
    submitted_by BIGINT       NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
"""

# =============================================================================
# DB Helpers (blocking — called via asyncio.to_thread)
# =============================================================================

def _db_ensure_tables():
    db = DB()
    db.connect()
    try:
        db.cursor.execute(CREATE_BAU_TABLE)
        db.cursor.execute(CREATE_REQ_TABLE)
        db.connection.commit()
    finally:
        db.close()


def _db_get_bau_topics() -> list[dict]:
    db = DB()
    db.connect()
    try:
        db.cursor.execute("SELECT id, topic, description FROM agenda_bau_topics ORDER BY id")
        return [{"id": r[0], "topic": r[1], "description": r[2]} for r in db.cursor.fetchall()]
    finally:
        db.close()


def _db_add_bau_topic(topic: str, description: str | None) -> bool:
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "INSERT INTO agenda_bau_topics (topic, description) VALUES (%s, %s) ON CONFLICT (topic) DO NOTHING",
            (topic, description),
        )
        db.connection.commit()
        return db.cursor.rowcount > 0
    finally:
        db.close()


def _db_remove_bau_topic(topic: str) -> bool:
    db = DB()
    db.connect()
    try:
        db.cursor.execute("DELETE FROM agenda_bau_topics WHERE topic = %s", (topic,))
        db.connection.commit()
        return db.cursor.rowcount > 0
    finally:
        db.close()


def _db_get_req_topics() -> list[dict]:
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "SELECT id, topic, description, submitted_by, created_at FROM agenda_requested_topics ORDER BY created_at"
        )
        return [
            {"id": r[0], "topic": r[1], "description": r[2], "submitted_by": r[3], "created_at": r[4]}
            for r in db.cursor.fetchall()
        ]
    finally:
        db.close()


def _db_add_req_topic(topic: str, description: str | None, submitted_by: int) -> bool:
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "INSERT INTO agenda_requested_topics (topic, description, submitted_by) VALUES (%s, %s, %s)",
            (topic, description, submitted_by),
        )
        db.connection.commit()
        return db.cursor.rowcount > 0
    finally:
        db.close()


def _db_remove_req_topic(topic_id: int) -> bool:
    db = DB()
    db.connect()
    try:
        db.cursor.execute("DELETE FROM agenda_requested_topics WHERE id = %s", (topic_id,))
        db.connection.commit()
        return db.cursor.rowcount > 0
    finally:
        db.close()


def _db_remove_req_topics(topic_ids: list[int]) -> None:
    if not topic_ids:
        return
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "DELETE FROM agenda_requested_topics WHERE id = ANY(%s)", (topic_ids,)
        )
        db.connection.commit()
    finally:
        db.close()

# =============================================================================
# Autocomplete helpers
# =============================================================================

async def _autocomplete_bau(ctx: discord.AutocompleteContext):
    topics = await asyncio.to_thread(_db_get_bau_topics)
    val = (ctx.value or "").lower()
    return [t["topic"] for t in topics if val in t["topic"].lower()][:25]


async def _autocomplete_req(ctx: discord.AutocompleteContext):
    topics = await asyncio.to_thread(_db_get_req_topics)
    val = (ctx.value or "").lower()
    return [f'{t["id"]}: {t["topic"]}' for t in topics if val in t["topic"].lower()][:25]

# =============================================================================
# Time helpers
# =============================================================================

def _next_saturday() -> datetime:
    """Return the next Saturday in GMT. If today is Saturday before 16:00 GMT, use today."""
    now = datetime.now(GMT)
    weekday = now.weekday()  # Monday=0, Saturday=5
    if weekday == 5 and now.hour < 16:
        return now
    days_ahead = (5 - weekday) % 7
    if days_ahead == 0:
        days_ahead = 7
    return now + timedelta(days=days_ahead)


def _parse_meeting_time(date_str: str, time_str: str) -> int | None:
    """Parse date + time strings as GMT, return Unix timestamp or None on error."""
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        dt = dt.replace(tzinfo=GMT)
        return int(dt.timestamp())
    except ValueError:
        return None

# =============================================================================
# Views & Modals
# =============================================================================

class BAUSelectView(discord.ui.View):
    def __init__(self, bau_topics: list[dict], invoker_id: int):
        super().__init__(timeout=300)
        self.invoker_id = invoker_id
        self.selected_bau = []
        self.bau_topics = bau_topics

        if bau_topics:
            options = [
                discord.SelectOption(label=t["topic"], description=(t["description"] or "")[:100], value=str(t["id"]))
                for t in bau_topics
            ]
            select = discord.ui.Select(
                placeholder="Select BAU topics...",
                min_values=0,
                max_values=len(options),
                options=options,
            )
            select.callback = self._select_callback
            self.add_item(select)

    async def _select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            return await interaction.response.send_message("This isn't your session.", ephemeral=True)
        self.selected_bau = interaction.data["values"]
        await interaction.response.defer()

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=4)
    async def next_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            return await interaction.response.send_message("This isn't your session.", ephemeral=True)
        await interaction.response.defer()
        self.stop()


class ReqSelectView(discord.ui.View):
    def __init__(self, req_topics: list[dict], invoker_id: int):
        super().__init__(timeout=300)
        self.invoker_id = invoker_id
        self.selected_req = []
        self.req_topics = req_topics

        if req_topics:
            options = [
                discord.SelectOption(
                    label=t["topic"][:100],
                    description=(t["description"] or "")[:100],
                    value=str(t["id"]),
                )
                for t in req_topics
            ]
            select = discord.ui.Select(
                placeholder="Select requested topics...",
                min_values=0,
                max_values=len(options),
                options=options,
            )
            select.callback = self._select_callback
            self.add_item(select)

    async def _select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            return await interaction.response.send_message("This isn't your session.", ephemeral=True)
        self.selected_req = interaction.data["values"]
        await interaction.response.defer()

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=4)
    async def next_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            return await interaction.response.send_message("This isn't your session.", ephemeral=True)
        await interaction.response.defer()
        self.stop()


class MeetingDetailsModal(discord.ui.Modal):
    def __init__(self, default_date: str, default_time: str):
        super().__init__(title="Meeting Details")
        self.date_input = discord.ui.InputText(
            label="Meeting Date (YYYY-MM-DD)",
            placeholder="2025-01-25",
            value=default_date,
            style=discord.InputTextStyle.short,
            max_length=10,
        )
        self.time_input = discord.ui.InputText(
            label="Meeting Time (HH:MM, 24h, GMT)",
            placeholder="08:00",
            value=default_time,
            style=discord.InputTextStyle.short,
            max_length=5,
        )
        self.notes_input = discord.ui.InputText(
            label="Additional Notes",
            placeholder="Any extra topics or announcements...",
            style=discord.InputTextStyle.long,
            required=False,
        )
        self.add_item(self.date_input)
        self.add_item(self.time_input)
        self.add_item(self.notes_input)
        self.result = None

    async def callback(self, interaction: discord.Interaction):
        self.result = {
            "date": self.date_input.value.strip(),
            "time": self.time_input.value.strip(),
            "notes": self.notes_input.value.strip() if self.notes_input.value else "",
        }
        await interaction.response.defer()
        self.stop()


class FeedbackModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Regeneration Feedback")
        self.feedback_input = discord.ui.InputText(
            label="What should be changed?",
            placeholder="e.g. Combine the first two sections...",
            style=discord.InputTextStyle.long,
        )
        self.add_item(self.feedback_input)
        self.result = None

    async def callback(self, interaction: discord.Interaction):
        self.result = self.feedback_input.value.strip()
        await interaction.response.defer()
        self.stop()


class AgendaPreviewView(discord.ui.View):
    def __init__(self, invoker_id: int):
        super().__init__(timeout=300)
        self.invoker_id = invoker_id
        self.action = None  # "post", "regenerate", "cancel"
        self.feedback = None

    @discord.ui.button(label="Post", style=discord.ButtonStyle.success)
    async def post_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            return await interaction.response.send_message("This isn't your session.", ephemeral=True)
        self.action = "post"
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Regenerate", style=discord.ButtonStyle.secondary)
    async def regenerate_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            return await interaction.response.send_message("This isn't your session.", ephemeral=True)
        self.action = "regenerate"
        modal = FeedbackModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        self.feedback = modal.result
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            return await interaction.response.send_message("This isn't your session.", ephemeral=True)
        self.action = "cancel"
        await interaction.response.defer()
        self.stop()

# =============================================================================
# Cog
# =============================================================================

class Agenda(commands.Cog):
    agenda = SlashCommandGroup(name="agenda", description="Meeting agenda commands")
    bau = agenda.create_subgroup(name="bau", description="Business-as-usual topics")
    req = agenda.create_subgroup(name="req", description="Requested topics")

    def __init__(self, client):
        self.client = client

    @commands.Cog.listener()
    async def on_ready(self):
        await asyncio.to_thread(_db_ensure_tables)

    # ── BAU commands ──────────────────────────────────────────────────────

    @bau.command(name="add", description="Add a BAU topic")
    @commands.has_permissions(administrator=True)
    async def bau_add(
        self,
        ctx: discord.ApplicationContext,
        topic: discord.Option(str, description="Topic name", max_length=100),
        description: discord.Option(str, description="Optional description", max_length=500, required=False, default=None),
    ):
        await ctx.defer(ephemeral=True)
        existing = await asyncio.to_thread(_db_get_bau_topics)
        if len(existing) >= 25:
            return await ctx.followup.send("Maximum of 25 BAU topics reached. Remove one first.")

        added = await asyncio.to_thread(_db_add_bau_topic, topic, description)
        if added:
            await ctx.followup.send(f"Added BAU topic: **{topic}**")
        else:
            await ctx.followup.send(f"A BAU topic named **{topic}** already exists.")

    @bau.command(name="remove", description="Remove a BAU topic")
    @commands.has_permissions(administrator=True)
    async def bau_remove(
        self,
        ctx: discord.ApplicationContext,
        topic: discord.Option(str, description="Topic to remove", autocomplete=_autocomplete_bau),
    ):
        await ctx.defer(ephemeral=True)
        removed = await asyncio.to_thread(_db_remove_bau_topic, topic)
        if removed:
            await ctx.followup.send(f"Removed BAU topic: **{topic}**")
        else:
            await ctx.followup.send(f"BAU topic **{topic}** not found.")

    @bau.command(name="list", description="List all BAU topics")
    async def bau_list(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        topics = await asyncio.to_thread(_db_get_bau_topics)
        if not topics:
            return await ctx.followup.send("No BAU topics configured.")

        lines = []
        for t in topics:
            line = f"- **{t['topic']}**"
            if t["description"]:
                line += f": {t['description']}"
            lines.append(line)

        embed = discord.Embed(title="BAU Topics", description="\n".join(lines), color=0x2b82d4)
        await ctx.followup.send(embed=embed)

    # ── Requested topic commands ──────────────────────────────────────────

    @req.command(name="add", description="Request a topic for the next meeting")
    async def req_add(
        self,
        ctx: discord.ApplicationContext,
        topic: discord.Option(str, description="Topic name", max_length=100),
        description: discord.Option(str, description="Optional description", max_length=500, required=False, default=None),
    ):
        # Runtime role check for executive_role_id
        role = ctx.guild.get_role(executive_role_id)
        if role not in ctx.author.roles:
            return await ctx.respond("You need the Executive role to request topics.", ephemeral=True)

        await ctx.defer(ephemeral=True)
        existing = await asyncio.to_thread(_db_get_req_topics)
        if len(existing) >= 25:
            return await ctx.followup.send("Maximum of 25 requested topics reached. Remove one first.")

        await asyncio.to_thread(_db_add_req_topic, topic, description, ctx.author.id)
        await ctx.followup.send(f"Requested topic added: **{topic}**")

    @req.command(name="remove", description="Remove a requested topic")
    async def req_remove(
        self,
        ctx: discord.ApplicationContext,
        topic: discord.Option(str, description="Topic to remove", autocomplete=_autocomplete_req),
    ):
        await ctx.defer(ephemeral=True)
        # Parse id from "id: topic" format
        try:
            topic_id = int(topic.split(":")[0].strip())
        except (ValueError, IndexError):
            return await ctx.followup.send("Invalid topic selection.")

        # Check ownership or admin
        topics = await asyncio.to_thread(_db_get_req_topics)
        target = next((t for t in topics if t["id"] == topic_id), None)
        if not target:
            return await ctx.followup.send("Topic not found.")

        is_admin = ctx.author.guild_permissions.administrator
        is_owner = target["submitted_by"] == ctx.author.id
        if not is_admin and not is_owner:
            return await ctx.followup.send("You can only remove your own topics (or be an admin).")

        removed = await asyncio.to_thread(_db_remove_req_topic, topic_id)
        if removed:
            await ctx.followup.send(f"Removed requested topic: **{target['topic']}**")
        else:
            await ctx.followup.send("Topic not found.")

    @req.command(name="list", description="List all requested topics")
    async def req_list(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        topics = await asyncio.to_thread(_db_get_req_topics)
        if not topics:
            return await ctx.followup.send("No requested topics.")

        lines = []
        for t in topics:
            line = f"- **{t['topic']}**"
            if t["description"]:
                line += f": {t['description']}"
            line += f" (by <@{t['submitted_by']}>)"
            lines.append(line)

        embed = discord.Embed(title="Requested Topics", description="\n".join(lines), color=0x2b82d4)
        await ctx.followup.send(embed=embed)

    # ── Interactive post flow ─────────────────────────────────────────────

    @agenda.command(name="post", description="Create and post a meeting agenda")
    @commands.has_permissions(manage_roles=True)
    async def post(self, ctx: discord.ApplicationContext):
        invoker_id = ctx.author.id

        # Step 1: BAU topic selection
        bau_topics = await asyncio.to_thread(_db_get_bau_topics)
        if bau_topics:
            bau_view = BAUSelectView(bau_topics, invoker_id)
            await ctx.respond("**Step 1/3:** Select BAU topics to include:", view=bau_view, ephemeral=True)
            timed_out = await bau_view.wait()
            if timed_out:
                return await ctx.edit(content="Session timed out.", view=None)
            selected_bau_ids = bau_view.selected_bau
        else:
            await ctx.respond("**Step 1/3:** No BAU topics configured. Continuing...", ephemeral=True)
            selected_bau_ids = []

        # Resolve selected BAU topics
        chosen_bau = [t for t in bau_topics if str(t["id"]) in selected_bau_ids]

        # Step 2: Requested topic selection
        req_topics = await asyncio.to_thread(_db_get_req_topics)
        if req_topics:
            req_view = ReqSelectView(req_topics, invoker_id)
            await ctx.edit(content="**Step 2/3:** Select requested topics to include:", view=req_view)
            timed_out = await req_view.wait()
            if timed_out:
                return await ctx.edit(content="Session timed out.", view=None)
            selected_req_ids = req_view.selected_req
        else:
            await ctx.edit(content="**Step 2/3:** No requested topics. Continuing...", view=None)
            selected_req_ids = []

        # Resolve selected requested topics
        chosen_req = [t for t in req_topics if str(t["id"]) in selected_req_ids]

        # Step 3: Meeting details modal
        sat = _next_saturday()
        modal = MeetingDetailsModal(sat.strftime("%Y-%m-%d"), "16:00")
        # Need an interaction to send modal — use a button
        detail_view = _ModalTriggerView(modal, invoker_id)
        await ctx.edit(content="**Step 3/3:** Click the button to enter meeting details.", view=detail_view)
        timed_out = await detail_view.wait()
        if timed_out or modal.result is None:
            return await ctx.edit(content="Session timed out.", view=None)

        meeting_date = modal.result["date"]
        meeting_time = modal.result["time"]
        notes = modal.result["notes"]

        unix_ts = _parse_meeting_time(meeting_date, meeting_time)
        if unix_ts is None:
            return await ctx.edit(content="Invalid date/time format. Use YYYY-MM-DD and HH:MM.", view=None)

        # Build OpenAI input
        openai_input = self._build_openai_input(chosen_bau, chosen_req, notes)

        # Step 4: Generate and preview
        await ctx.edit(content="Generating agenda...", view=None)
        agenda_text = await self._generate_agenda(openai_input)
        if agenda_text is None:
            return await ctx.edit(content="Failed to generate agenda. Check error channel for details.")

        # Preview loop (supports regeneration)
        while True:
            full_message = f"@everyone Meeting at <t:{unix_ts}:f>\n\n{agenda_text}"
            embed = discord.Embed(title="Agenda Preview", description=full_message, color=0x2b82d4)
            preview_view = AgendaPreviewView(invoker_id)
            await ctx.edit(content=None, embed=embed, view=preview_view)
            timed_out = await preview_view.wait()
            if timed_out:
                return await ctx.edit(content="Session timed out.", embed=None, view=None)

            if preview_view.action == "post":
                # Post to announcement channel
                channel = self.client.get_channel(meeting_announcement_channel)
                if channel is None:
                    return await ctx.edit(content="Announcement channel not found.", embed=None, view=None)
                await channel.send(full_message)
                # Delete selected requested topics from DB
                ids_to_delete = [int(tid) for tid in selected_req_ids]
                await asyncio.to_thread(_db_remove_req_topics, ids_to_delete)
                await ctx.edit(content="Agenda posted!", embed=None, view=None)
                break

            elif preview_view.action == "regenerate":
                feedback = getattr(preview_view, "feedback", None) or ""
                regen_input = f"{openai_input}\n\n## Previous Output\n{agenda_text}\n\n## Feedback\n{feedback}"
                await ctx.edit(content="Regenerating agenda...", embed=None, view=None)
                agenda_text = await self._generate_agenda(regen_input)
                if agenda_text is None:
                    return await ctx.edit(content="Failed to regenerate agenda. Check error channel for details.")
                continue

            else:  # cancel
                await ctx.edit(content="Agenda creation cancelled.", embed=None, view=None)
                break

    def _build_openai_input(self, bau: list[dict], req: list[dict], notes: str) -> str:
        parts = []
        if bau:
            parts.append("## BAU Topics")
            for t in bau:
                line = f"- {t['topic']}"
                if t["description"]:
                    line += f": {t['description']}"
                parts.append(line)

        if req:
            parts.append("\n## Requested Topics")
            for t in req:
                line = f"- {t['topic']}"
                if t["description"]:
                    line += f": {t['description']}"
                parts.append(line)

        if notes:
            parts.append(f"\n## Additional Notes\n{notes}")

        return "\n".join(parts) if parts else "No topics selected."

    async def _generate_agenda(self, input_text: str) -> str | None:
        try:
            result = await asyncio.to_thread(
                openai_query,
                instructions=AGENDA_SYSTEM_PROMPT,
                input_text=input_text,
                model="gpt-4.1-nano",
                temperature=0.3,
                max_tokens=1500,
            )
            if result["error"]:
                ch = self.client.get_channel(error_channel)
                if ch:
                    await ch.send(f"## Agenda generation error\n```\n{result['error']}\n```")
                return None
            return result["content"]
        except Exception as e:
            ch = self.client.get_channel(error_channel)
            if ch:
                await ch.send(f"## Agenda generation exception\n```\n{e}\n```")
            return None


class _ModalTriggerView(discord.ui.View):
    """Simple view with a button that triggers a modal."""
    def __init__(self, modal: discord.ui.Modal, invoker_id: int):
        super().__init__(timeout=300)
        self.modal = modal
        self.invoker_id = invoker_id

    @discord.ui.button(label="Enter Details", style=discord.ButtonStyle.primary)
    async def trigger(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            return await interaction.response.send_message("This isn't your session.", ephemeral=True)
        await interaction.response.send_modal(self.modal)
        await self.modal.wait()
        self.stop()


def setup(client):
    client.add_cog(Agenda(client))
