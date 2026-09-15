import asyncio

import discord
from discord.ext import commands

from Helpers import honorifics as hon
from Helpers import roster
from Helpers.database import DB
from Helpers.member_roles import removal_role_names, resolve_roles
from Helpers.variables import ERROR_CHANNEL_ID, GENERAL_CHANNEL_ID, GUILD_LOG_CHANNEL_ID, RULES_CHANNEL_ID, TAQ_GUILD_ID
from Helpers.logger import log, INFO, ERROR

WELCOME_COLOR = 0x94C1FF


def _honorifics_on_record(discord_id):
    """Blocking: (honored_fish, retired_chief, [grant dicts]) for a rejoining
    account. A current in-game member who merely rejoined Discord gets
    nothing here -- registration handles them and would strip the roles
    again -- so the roster is checked first."""
    db = DB()
    db.connect()
    try:
        if roster.is_member(db.cursor, discord_id=discord_id):
            return False, False, []
        hf, rc = hon.active_honorifics(db.cursor, discord_id=discord_id)
        grants = []
        if hf or rc:
            db.cursor.execute(
                """SELECT mh.honorific, mh.granted_by, mh.granted_at
                     FROM member_honorifics mh
                    WHERE mh.revoked_at IS NULL
                      AND (mh.discord_id = %s
                           OR mh.uuid = (SELECT uuid FROM discord_links WHERE discord_id = %s))""",
                (discord_id, discord_id),
            )
            grants = [{'honorific': r[0], 'granted_by': r[1], 'granted_at': r[2]} for r in db.cursor.fetchall()]
        return hf, rc, grants
    finally:
        db.close()


class OnMemberJoin(commands.Cog):
    def __init__(self, client):
        self.client = client

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.guild.id != TAQ_GUILD_ID:
            return

        log(INFO, f'{member.name} joined {member.guild.name}', context='on_member_join')

        ch = self.client.get_channel(GENERAL_CHANNEL_ID)
        if ch:
            await ch.send(
                f"Welcome to TAq {member.mention}! <:TAq:744256840254226553>\n"
                f"If you want to apply, head to <#1476866917854609408> and choose your application type.\n"
                f"Read <#{RULES_CHANNEL_ID}> for any immediate questions or concerns (like ally raiding) and have a wonderful stay within The Aquarium! <:partytort:975138500150165594>"
            )

        # Honorifics survive leaving Discord (member_honorifics, TAQ-76): a
        # returning Honored Fish / Retired Chief gets the roles back without
        # anyone having to remember.
        try:
            await self._restore_honorifics(member)
        except Exception as e:
            log(ERROR, f"Honorific restore failed for {member.name}: {e}", context='on_member_join')
            err_ch = self.client.get_channel(ERROR_CHANNEL_ID)
            if err_ch:
                await err_ch.send(f"## Honorific restore failed\n**Member:** {member.mention}\n```\n{str(e)[:500]}\n```")

    async def _restore_honorifics(self, member):
        hf, rc, grants = await asyncio.to_thread(_honorifics_on_record, member.id)
        if not (hf or rc):
            return
        to_add, _ = removal_role_names(hf, rc)
        roles = resolve_roles(member.guild.roles, to_add, member=member, present=False)
        if roles:
            await member.add_roles(*roles, reason='Honorifics on record (rejoin)')
        restored = ', '.join(hon.LABELS[g['honorific']] for g in grants)
        details = '; '.join(
            f"{hon.LABELS[g['honorific']]} granted {g['granted_at']:%Y-%m-%d}"
            + (f" by <@{g['granted_by']}>" if g['granted_by'] else '')
            for g in grants
        )
        log_ch = self.client.get_channel(GUILD_LOG_CHANNEL_ID)
        if log_ch:
            await log_ch.send(f"{member.mention} rejoined — restored {restored} ({details})")


def setup(client):
    client.add_cog(OnMemberJoin(client))
