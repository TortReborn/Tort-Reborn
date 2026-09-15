"""Per-leaver guild-log messages with reset / honorific buttons (TAQ-76).

When the in-game diff sees a member leave (or get kicked -- the API cannot
tell), the guild log gets one message per leaver instead of the old batched
list, each with three buttons:

    [ Reset roles ] [ Reset + Honored Fish ] [ Reset + Retired Chief ]

The view is persistent and stateless, like RecruitPaidView: fixed custom_ids,
target resolved from ``member_leave_prompts`` by the message id. That table
is also the audit of who pressed what. The buttons re-check the member's
Discord state on press, so they stay right when the website queue removed
them first, or when they have already left Discord (in which case a grant is
still recorded -- the ticket's central case).
"""

import asyncio
import datetime

import discord

from Helpers import honorifics as hon
from Helpers.database import DB
from Helpers.logger import log, ERROR
from Helpers.member_removal import check_reset_permission, record_grant_only, remove_member
from Helpers.member_roles import EX_MEMBER_ROLE, removal_role_names, resolve_roles
from Helpers.variables import ERROR_CHANNEL_ID, discord_ranks

# Above this many leavers in one cycle (mass kick, API hiccup) the log gets
# the batched list with no buttons; /stale-roles handles the rest.
MAX_PROMPTS_PER_CYCLE = 10

RESOLUTION_LABELS = {
    'reset': 'Reset',
    'honored_fish': 'Reset + Honored Fish',
    'retired_chief': 'Reset + Retired Chief',
    'noop': 'Nothing to do',
}


# --- DB ----------------------------------------------------------------------

def _load_leaver_context(cursor, uuid):
    """(discord_id, rank, honored_fish, retired_chief) for a uuid, or
    (None, None, hf, rc) when unlinked."""
    cursor.execute("SELECT discord_id, rank FROM discord_links WHERE uuid = %s::uuid", (str(uuid),))
    row = cursor.fetchone()
    hf, rc = hon.active_honorifics(cursor, uuid=uuid)
    return (row[0] if row else None), (row[1] if row else None), hf, rc


def _insert_prompt(cursor, message_id, uuid, ign, discord_id, last_rank):
    cursor.execute(
        """INSERT INTO member_leave_prompts (message_id, uuid, ign, discord_id, last_rank)
           VALUES (%s, %s::uuid, %s, %s, %s)
           ON CONFLICT (message_id) DO NOTHING""",
        (int(message_id), str(uuid), ign, discord_id, last_rank),
    )


def load_prompt(cursor, message_id):
    cursor.execute(
        """SELECT message_id, uuid::text, ign, discord_id, last_rank, resolved_by, resolved_at, resolution
             FROM member_leave_prompts WHERE message_id = %s""",
        (int(message_id),),
    )
    row = cursor.fetchone()
    if not row:
        return None
    keys = ['message_id', 'uuid', 'ign', 'discord_id', 'last_rank', 'resolved_by', 'resolved_at', 'resolution']
    return dict(zip(keys, row))


def resolve_prompt(cursor, message_id, resolved_by, resolution):
    """Mark the prompt handled. Returns False if someone else got there first."""
    cursor.execute(
        """UPDATE member_leave_prompts
              SET resolved_by = %s, resolved_at = NOW(), resolution = %s
            WHERE message_id = %s AND resolved_at IS NULL""",
        (int(resolved_by), resolution, int(message_id)),
    )
    return cursor.rowcount > 0


def _with_db(fn, *args, **kwargs):
    db = DB()
    db.connect()
    try:
        result = fn(db.cursor, *args, **kwargs)
        db.connection.commit()
        return result
    finally:
        db.close()


# --- posting -------------------------------------------------------------------

def build_leave_embed(ign, last_rank, discord_state, honorifics, now=None):
    """discord_state: 'linked' | 'gone' | 'unlinked'; honorifics: (hf, rc)."""
    hf, rc = honorifics
    on_record = ', '.join(n for n, held in ((hon.LABELS[hon.HONORED_FISH], hf),
                                             (hon.LABELS[hon.RETIRED_CHIEF], rc)) if held) or 'none'
    embed = discord.Embed(title='Guild Member Left', timestamp=now, color=0xFF0000)
    embed.description = f"**{discord.utils.escape_markdown(ign)}**"
    embed.add_field(name='Rank', value=last_rank or '—', inline=True)
    embed.add_field(name='Discord', value=discord_state, inline=True)
    embed.add_field(name='Honorifics on record', value=on_record, inline=False)
    return embed


def describe_discord_state(member, discord_id):
    if discord_id is None:
        return 'not linked'
    if member is None:
        return f'<@{discord_id}> — no longer in this server'
    return f'<@{discord_id}> (linked)'


async def post_leave_prompts(client, channel, leavers, *, fallback_embed, now=None):
    """leavers: iterable of (uuid, ign, in_game_rank). Posts one prompt each,
    or the batched embed when there are too many."""
    leavers = list(leavers)
    if not leavers or len(leavers) > MAX_PROMPTS_PER_CYCLE:
        await channel.send(embed=fallback_embed)
        return []

    guild = channel.guild
    posted = []
    for uuid, ign, _in_game_rank in leavers:
        discord_id, last_rank, hf, rc = await asyncio.to_thread(_with_db, _load_leaver_context, uuid)
        member = None
        if discord_id is not None:
            member = guild.get_member(discord_id)
            if member is None:
                try:
                    member = await guild.fetch_member(discord_id)
                except discord.HTTPException:
                    member = None
        embed = build_leave_embed(ign, last_rank, describe_discord_state(member, discord_id), (hf, rc), now)
        view = LeavePromptView() if discord_id is not None else None
        message = await channel.send(embed=embed, view=view)
        await asyncio.to_thread(_with_db, _insert_prompt, message.id, uuid, ign, discord_id, last_rank)
        posted.append(message.id)
    return posted


# --- the view -------------------------------------------------------------------

def _outcome_for(prompt, member, grant):
    """Decide the branch before touching anything. Returns a tag:
    'remove' | 'grant_only' | 'noop' | 'grant_ex_member'."""
    if member is None:
        return 'grant_only' if grant else 'noop'
    held = {r.name for r in member.roles}
    _, to_remove = removal_role_names()
    if EX_MEMBER_ROLE in held and not any(n in held for n in to_remove):
        return 'grant_ex_member' if grant else 'noop'
    return 'remove'


class LeavePromptView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Reset roles', style=discord.ButtonStyle.primary, custom_id='leave_reset')
    async def reset_button(self, button, interaction):
        await self._handle(interaction, grant=None)

    @discord.ui.button(label='Reset + Honored Fish', style=discord.ButtonStyle.secondary,
                       custom_id='leave_reset_hf', emoji='\U0001F41F')
    async def honored_fish_button(self, button, interaction):
        await self._handle(interaction, grant=hon.HONORED_FISH)

    @discord.ui.button(label='Reset + Retired Chief', style=discord.ButtonStyle.secondary,
                       custom_id='leave_reset_rc', emoji='\U0001F451')
    async def retired_chief_button(self, button, interaction):
        await self._handle(interaction, grant=hon.RETIRED_CHIEF)

    async def _handle(self, interaction, *, grant):
        await interaction.response.defer(ephemeral=True)
        prompt = await asyncio.to_thread(_with_db, load_prompt, interaction.message.id)
        if prompt is None:
            await interaction.followup.send('Could not find this leave record.', ephemeral=True)
            return
        if prompt['resolved_at'] is not None:
            await interaction.followup.send(
                f"Already handled by <@{prompt['resolved_by']}> ({RESOLUTION_LABELS.get(prompt['resolution'], prompt['resolution'])}).",
                ephemeral=True)
            return

        # Permission: the /reset_roles rule, against the rank they had when they left.
        presser_rank = await asyncio.to_thread(_with_db, _rank_of, interaction.user.id)
        refusal = check_reset_permission(presser_rank, (prompt['last_rank'],) if prompt['last_rank'] else None)
        if refusal is None and grant == hon.RETIRED_CHIEF:
            ranks = list(discord_ranks)
            if ranks.index(presser_rank) < ranks.index('Narwhal'):
                refusal = (':no_entry: Permission denied', 'Retired Chief can only be granted by Narwhal or higher.')
        if refusal:
            await interaction.followup.send(f"{refusal[0]} {refusal[1]}", ephemeral=True)
            return

        if prompt['discord_id'] is None:
            await interaction.followup.send('No linked Discord account — use `/reset_roles` on the member directly.', ephemeral=True)
            return

        guild = interaction.guild
        member = guild.get_member(prompt['discord_id'])
        if member is None:
            try:
                member = await guild.fetch_member(prompt['discord_id'])
            except discord.HTTPException:
                member = None

        outcome = _outcome_for(prompt, member, grant)
        note = f'leave prompt {prompt["message_id"]}'
        summary = None
        try:
            if outcome == 'remove':
                result = await remove_member(
                    member, guild, actor_id=interaction.user.id,
                    reason=f'Guild leave (handled by {interaction.user.name})', grant=grant, note=note,
                )
                summary = f"Reset by {interaction.user.mention}"
                if grant:
                    summary += f" — {hon.LABELS[grant]} granted"
                if result.restored:
                    summary += f"\nRestored: " + ', '.join(f'`{r}`' for r in result.restored)
            elif outcome == 'grant_only':
                await asyncio.to_thread(
                    _with_db, record_grant_only, discord_id=prompt['discord_id'], uuid=prompt['uuid'],
                    ign=prompt['ign'], grant=grant, actor_id=interaction.user.id, note=note,
                )
                summary = f"Recorded {hon.LABELS[grant]} by {interaction.user.mention} — not in Discord, roles apply on rejoin"
            elif outcome == 'grant_ex_member':
                await asyncio.to_thread(
                    _with_db, record_grant_only, discord_id=prompt['discord_id'], uuid=prompt['uuid'],
                    ign=prompt['ign'], grant=grant, actor_id=interaction.user.id, note=note,
                )
                to_add, _ = removal_role_names(grant == hon.HONORED_FISH or grant == hon.RETIRED_CHIEF,
                                               grant == hon.RETIRED_CHIEF)
                roles = resolve_roles(guild.roles, to_add, member=member, present=False)
                if roles:
                    await member.add_roles(*roles, reason=f'Honorific granted by {interaction.user.name}')
                summary = f"Already removed; {hon.LABELS[grant]} granted by {interaction.user.mention}"
            else:
                summary = (f"Nothing to reset ({'not in Discord' if member is None else 'already an ex-member'}) "
                           f"— closed by {interaction.user.mention}")
        except (discord.Forbidden, discord.HTTPException) as e:
            err_ch = interaction.client.get_channel(ERROR_CHANNEL_ID)
            if err_ch:
                await err_ch.send(f"## Leave prompt — Discord error\n**Member:** {prompt['ign']} (<@{prompt['discord_id']}>)\n```\n{str(e)[:500]}\n```")
            await interaction.followup.send(f'Discord refused the change: `{e}`. Buttons stay active; try again.', ephemeral=True)
            return

        resolution = grant or ('reset' if outcome == 'remove' else 'noop')
        claimed = await asyncio.to_thread(_with_db, resolve_prompt, prompt['message_id'], interaction.user.id, resolution)
        if not claimed:
            await interaction.followup.send('Someone else handled this at the same time.', ephemeral=True)
            return

        embed = interaction.message.embeds[0]
        embed.color = 0x808080
        embed.add_field(name='Resolved', value=summary, inline=False)
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(embed=embed, view=self)
        await interaction.followup.send(summary, ephemeral=True)


def _rank_of(cursor, discord_id):
    cursor.execute("SELECT rank FROM discord_links WHERE discord_id = %s", (int(discord_id),))
    row = cursor.fetchone()
    return row[0] if row else None
