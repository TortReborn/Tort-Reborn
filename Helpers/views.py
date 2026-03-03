import asyncio

import discord

from Helpers.database import DB
from Helpers.logger import log, ERROR, INFO
from Helpers.variables import MEMBER_APP_CHANNEL_ID


def _get_vote_counts(application_id: int) -> dict:
    """Blocking: get vote counts for an application."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            """SELECT
                COUNT(*) FILTER (WHERE vote = 'accept') as accept_count,
                COUNT(*) FILTER (WHERE vote = 'deny') as deny_count,
                COUNT(*) FILTER (WHERE vote = 'abstain') as abstain_count
            FROM application_votes
            WHERE application_id = %s""",
            (application_id,)
        )
        row = db.cursor.fetchone()
        if row:
            return {"accept": row[0], "deny": row[1], "abstain": row[2]}
        return {"accept": 0, "deny": 0, "abstain": 0}
    finally:
        db.close()


def _upsert_vote(application_id: int, voter_discord_id: str, voter_username: str, vote: str):
    """Blocking: insert or update a vote."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            """INSERT INTO application_votes
                (application_id, voter_discord_id, voter_username, vote, source, voted_at)
            VALUES (%s, %s, %s, %s, 'discord', NOW())
            ON CONFLICT (application_id, voter_discord_id)
            DO UPDATE SET vote = %s, voted_at = NOW(), source = 'discord'""",
            (application_id, voter_discord_id, voter_username, vote, vote)
        )
        db.connection.commit()
    finally:
        db.close()


def _get_user_vote(application_id: int, voter_discord_id: str) -> str | None:
    """Blocking: get a user's current vote for an application."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "SELECT vote FROM application_votes WHERE application_id = %s AND voter_discord_id = %s",
            (application_id, voter_discord_id)
        )
        row = db.cursor.fetchone()
        return row[0] if row else None
    finally:
        db.close()


def _delete_vote(application_id: int, voter_discord_id: str):
    """Blocking: remove a vote."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "DELETE FROM application_votes WHERE application_id = %s AND voter_discord_id = %s",
            (application_id, voter_discord_id)
        )
        db.connection.commit()
    finally:
        db.close()


def _get_app_id_from_poll(poll_message_id: int) -> int | None:
    """Blocking: look up an application ID from its poll message ID."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "SELECT id FROM applications WHERE poll_message_id = %s",
            (poll_message_id,)
        )
        row = db.cursor.fetchone()
        return row[0] if row else None
    finally:
        db.close()


def _get_app_id_from_thread(thread_id: int) -> int | None:
    """Blocking: look up an application ID from its thread ID."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "SELECT id FROM applications WHERE thread_id = %s",
            (thread_id,)
        )
        row = db.cursor.fetchone()
        return row[0] if row else None
    finally:
        db.close()


def _get_poll_message_id(application_id: int) -> int | None:
    """Blocking: get the poll message ID for an application."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "SELECT poll_message_id FROM applications WHERE id = %s",
            (application_id,)
        )
        row = db.cursor.fetchone()
        return row[0] if row else None
    finally:
        db.close()


def _format_vote_field(counts: dict) -> str:
    """Format vote counts into a display string."""
    return f"Accept: {counts['accept']} | Deny: {counts['deny']} | Abstain: {counts['abstain']}"


async def _update_embed_votes(interaction: discord.Interaction, counts: dict):
    """Update the poll embed with current vote counts."""
    msg = interaction.message
    if not msg or not msg.embeds:
        return

    embed = msg.embeds[0].copy()
    vote_text = _format_vote_field(counts)

    # Find and update or add the Votes field
    found = False
    for i, field in enumerate(embed.fields):
        if field.name == "Votes":
            embed.set_field_at(i, name="Votes", value=vote_text, inline=False)
            found = True
            break

    if not found:
        embed.add_field(name="Votes", value=vote_text, inline=False)

    try:
        await msg.edit(embed=embed, view=ApplicationVoteView())
    except Exception as e:
        log(ERROR, f"Failed to update poll embed: {e}", context="views")


async def _update_poll_embed_by_msg(poll_msg: discord.Message, counts: dict):
    """Update the poll embed on a fetched message with current vote counts."""
    if not poll_msg.embeds:
        return

    embed = poll_msg.embeds[0].copy()
    vote_text = _format_vote_field(counts)

    found = False
    for i, field in enumerate(embed.fields):
        if field.name == "Votes":
            embed.set_field_at(i, name="Votes", value=vote_text, inline=False)
            found = True
            break

    if not found:
        embed.add_field(name="Votes", value=vote_text, inline=False)

    try:
        await poll_msg.edit(embed=embed, view=ApplicationVoteView())
    except Exception as e:
        log(ERROR, f"Failed to update poll embed: {e}", context="views")


class ApplicationVoteView(discord.ui.View):
    """Persistent view with Accept/Deny/Abstain voting buttons for applications."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Accept",
        style=discord.ButtonStyle.success,
        custom_id="app_vote_accept",
        emoji="\U0001F44D",
    )
    async def accept_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._handle_vote(interaction, "accept")

    @discord.ui.button(
        label="Abstain",
        style=discord.ButtonStyle.secondary,
        custom_id="app_vote_abstain",
        emoji="\U0001F937",
    )
    async def abstain_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._handle_vote(interaction, "abstain")

    @discord.ui.button(
        label="Deny",
        style=discord.ButtonStyle.danger,
        custom_id="app_vote_deny",
        emoji="\U0001F44E",
    )
    async def deny_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._handle_vote(interaction, "deny")

    async def _handle_vote(self, interaction: discord.Interaction, vote: str):
        await interaction.response.defer(ephemeral=True)

        # Look up application ID from the poll message
        app_id = await asyncio.to_thread(_get_app_id_from_poll, interaction.message.id)
        if app_id is None:
            await interaction.followup.send(
                "Could not find the application for this poll.", ephemeral=True
            )
            return

        voter_id = str(interaction.user.id)
        voter_name = interaction.user.display_name

        # Check if user already voted the same way (toggle off)
        current_vote = await asyncio.to_thread(_get_user_vote, app_id, voter_id)

        if current_vote == vote:
            # Remove the vote (toggle off)
            await asyncio.to_thread(_delete_vote, app_id, voter_id)
            counts = await asyncio.to_thread(_get_vote_counts, app_id)
            await _update_embed_votes(interaction, counts)
            await interaction.followup.send(
                f"Your **{vote}** vote has been removed.", ephemeral=True
            )
        else:
            # Upsert the vote
            await asyncio.to_thread(_upsert_vote, app_id, voter_id, voter_name, vote)
            counts = await asyncio.to_thread(_get_vote_counts, app_id)
            await _update_embed_votes(interaction, counts)
            action = "changed to" if current_vote else "recorded as"
            await interaction.followup.send(
                f"Your vote has been {action} **{vote}**.", ephemeral=True
            )


class ThreadVoteView(discord.ui.View):
    """Persistent vote buttons for use inside discussion threads."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Accept",
        style=discord.ButtonStyle.success,
        custom_id="thread_vote_accept",
        emoji="\U0001F44D",
    )
    async def accept_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._handle_vote(interaction, "accept")

    @discord.ui.button(
        label="Abstain",
        style=discord.ButtonStyle.secondary,
        custom_id="thread_vote_abstain",
        emoji="\U0001F937",
    )
    async def abstain_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._handle_vote(interaction, "abstain")

    @discord.ui.button(
        label="Deny",
        style=discord.ButtonStyle.danger,
        custom_id="thread_vote_deny",
        emoji="\U0001F44E",
    )
    async def deny_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._handle_vote(interaction, "deny")

    async def _handle_vote(self, interaction: discord.Interaction, vote: str):
        await interaction.response.defer(ephemeral=True)

        # Look up application ID from the thread
        thread_id = interaction.channel.id
        app_id = await asyncio.to_thread(_get_app_id_from_thread, thread_id)
        if app_id is None:
            await interaction.followup.send(
                "Could not find the application for this thread.", ephemeral=True
            )
            return

        voter_id = str(interaction.user.id)
        voter_name = interaction.user.display_name

        current_vote = await asyncio.to_thread(_get_user_vote, app_id, voter_id)

        if current_vote == vote:
            await asyncio.to_thread(_delete_vote, app_id, voter_id)
            counts = await asyncio.to_thread(_get_vote_counts, app_id)
            # Update the parent poll embed too
            await self._sync_poll_embed(interaction, app_id, counts)
            await interaction.followup.send(
                f"Your **{vote}** vote has been removed.", ephemeral=True
            )
        else:
            await asyncio.to_thread(_upsert_vote, app_id, voter_id, voter_name, vote)
            counts = await asyncio.to_thread(_get_vote_counts, app_id)
            await self._sync_poll_embed(interaction, app_id, counts)
            action = "changed to" if current_vote else "recorded as"
            await interaction.followup.send(
                f"Your vote has been {action} **{vote}**.", ephemeral=True
            )

    @staticmethod
    async def _sync_poll_embed(interaction: discord.Interaction, app_id: int, counts: dict):
        """Update the parent poll message embed with vote counts."""
        poll_msg_id = await asyncio.to_thread(_get_poll_message_id, app_id)
        if not poll_msg_id:
            return

        exec_chan = interaction.client.get_channel(MEMBER_APP_CHANNEL_ID)
        if not exec_chan:
            return

        try:
            poll_msg = await exec_chan.fetch_message(poll_msg_id)
            await _update_poll_embed_by_msg(poll_msg, counts)
        except Exception as e:
            log(ERROR, f"Failed to sync poll embed from thread: {e}", context="views")
