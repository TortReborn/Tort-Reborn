import discord
import time
import traceback
from discord import Embed, ButtonStyle
from discord.commands import slash_command
from discord.ext import commands
import asyncio

from Helpers.database import DB, get_current_guild_data
from Helpers.guild_accounts import guild_account_uuids, uuid_key
from Helpers.member_removal import check_reset_permission, remove_member
from Helpers.stale_links import fetch_stale_taq_links, render_stale_taq_links, split_stale_report, stale_taq_links
from Helpers.variables import discord_ranks, HOME_GUILD_IDS, TAQ_GUILD_ID


class ReportPaginator(discord.ui.View):
    def __init__(self, embed_mismatch: Embed, embed_linkage: Embed, embed_usernames: Embed):
        super().__init__(timeout=None)
        self.embeds = {
            "mismatch": embed_mismatch,
            "linkage": embed_linkage,
            "usernames": embed_usernames
        }

    @discord.ui.button(label="Mismatch Issues", style=ButtonStyle.primary)
    async def show_mismatch(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.embeds["mismatch"], view=self)

    @discord.ui.button(label="Linkage Issues", style=ButtonStyle.secondary)
    async def show_linkage(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.embeds["linkage"], view=self)

    @discord.ui.button(label="Username Mismatches", style=ButtonStyle.success)
    async def show_usernames(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.embeds["usernames"], view=self)


class StaleRolesView(discord.ui.View):
    def __init__(self, cog, rows):
        super().__init__(timeout=300)
        self.cog = cog
        self.rows = rows

    @discord.ui.button(label="Reset All?", style=ButtonStyle.danger)
    async def reset_all(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("You need to be a Moderator to run this.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        result = await self.cog.reset_stale_roles(interaction, self.rows)
        await interaction.followup.send(result, ephemeral=True)


class RankCheck(commands.Cog):
    def __init__(self, client):
        self.client = client

    def _fetch_stale_rows(self):
        db = DB()
        db.connect()
        try:
            return fetch_stale_taq_links(db.cursor)
        finally:
            db.close()

    async def _discord_member_ids(self, guild):
        if guild is None:
            return set()
        try:
            return {member.id async for member in guild.fetch_members(limit=None)}
        except Exception:
            return {member.id for member in guild.members}

    def _rank_for_discord(self, discord_id):
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                "SELECT rank FROM discord_links WHERE discord_id = %s",
                (discord_id,),
            )
            row = db.cursor.fetchone()
            return row[0] if row else None
        finally:
            db.close()

    async def _member_for_id(self, guild, discord_id):
        member = guild.get_member(discord_id)
        if member:
            return member
        try:
            return await guild.fetch_member(discord_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def reset_stale_roles(self, interaction, rows):
        guild = self.client.get_guild(TAQ_GUILD_ID) or interaction.guild
        if guild is None:
            return "Could not find the TAq Discord server."

        actor_rank = await asyncio.to_thread(self._rank_for_discord, interaction.user.id)
        refusal = check_reset_permission(actor_rank, None)
        if refusal:
            return refusal[1]

        done = []
        skipped = []
        failed = []

        for row in rows:
            if check_reset_permission(actor_rank, (row["rank"],)):
                skipped.append(row["ign"])
                continue

            member = await self._member_for_id(guild, row["discord_id"])
            if member is None:
                skipped.append(row["ign"])
                continue

            try:
                await remove_member(
                    member, guild,
                    actor_id=interaction.user.id,
                    reason=f"Stale role reset by {interaction.user.name}",
                )
                done.append(row["ign"])
            except (discord.Forbidden, discord.HTTPException):
                failed.append(row["ign"])

        parts = [f"Reset {len(done)} member(s)."]
        if skipped:
            parts.append(f"Skipped {len(skipped)}.")
        if failed:
            parts.append(f"Failed {len(failed)}.")
        return " ".join(parts)

    @slash_command(
        name='stale-roles',
        description='HR: List Ex Members that left the ingame guild but still have their rank and roles',
        guild_ids=HOME_GUILD_IDS,
        default_member_permissions=discord.Permissions(manage_roles=True),
        dm_permission=False
    )
    async def stale_roles(self, ctx):
        if not ctx.user.guild_permissions.manage_roles:
            await ctx.respond("You need to be Moderator to run this.", ephemeral=True)
            return

        await ctx.defer(ephemeral=True)

        discord_guild = self.client.get_guild(TAQ_GUILD_ID) or ctx.guild
        rows, discord_ids = await asyncio.gather(
            asyncio.to_thread(self._fetch_stale_rows),
            self._discord_member_ids(discord_guild),
        )
        stale = stale_taq_links(rows, discord_ids)
        text = render_stale_taq_links(stale)
        chunks = split_stale_report(text)
        view = StaleRolesView(self, stale) if stale else discord.utils.MISSING

        for i, chunk in enumerate(chunks):
            await ctx.followup.send(
                f"```text\n{chunk}\n```",
                view=view if i == 0 else discord.utils.MISSING,
                ephemeral=True,
            )

    @slash_command(
        description='ADMIN: Check for game/discord rank & nickname consistency',
        guild_ids=HOME_GUILD_IDS,
        default_member_permissions=discord.Permissions(administrator=True),
        dm_permission=False
    )
    async def rankcheck(self, interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admins only.", ephemeral=True)

        await interaction.response.defer()

        try:
            # No API calls: the update_member_data loop refreshes this cache
            # every few minutes, and discord_links already carries every ign.
            cached = await asyncio.to_thread(get_current_guild_data)
            data = cached.get('members', [])
            if not data:
                return await interaction.followup.send(
                    "⚠️ Guild cache is empty — the update loop hasn't run yet. Try again in a few minutes.",
                    ephemeral=True,
                )
            cache_age_s = max(0, int(time.time()) - int(cached.get('time') or 0))
            guild_uuids = {m['uuid'] for m in data}

            # grab Discord members once
            discord_members = {m.id: m for m in interaction.guild.members}

            # DB lookup for links
            db = DB(); db.connect()
            db.cursor.execute("SELECT uuid, discord_id, rank, ign FROM discord_links")
            all_links = db.cursor.fetchall()
            guild_accounts = guild_account_uuids(db.cursor)
            db.close()

            links_map = {row[0]: (row[1], row[2], row[3]) for row in all_links}
            linked_uuids = set(links_map)

            mismatch, linkage, usernames = [], [], []

            for member in data:
                uuid = member['uuid']
                ign = member.get('name') or uuid

                linked = links_map.get(uuid)
                # A rename shows up as the registered ign drifting from
                # the guild API's current name for the same uuid.
                registered = linked[2] if linked else None
                if registered and registered != ign:
                    usernames.append(f'\u001b[0;36m {registered:16} → {ign}')

                if linked and linked[1]:   # rank is NULL for linked non-members (TAQ-76)
                    discord_id, role = linked[0], linked[1]

                    try:
                        expected = discord_ranks[role]['in_game_rank']
                    except KeyError:
                        mismatch.append(
                            f'\u001b[0;31m {ign:16} \u001b[1;37mrole   \u001b[0;0mno mapping "{role}"'
                        )
                        continue

                    if member['rank'].upper() != expected:
                        mismatch.append(
                            f'\u001b[0;31m {ign:16} \u001b[1;37mrank   '
                            f'\u001b[0;0m{member["rank"].upper():10.10} \u001b[1;37m≠ \u001b[0;0m{expected}'
                        )

                    disc_mem = discord_members.get(discord_id)
                    if disc_mem:
                        nick = disc_mem.nick or disc_mem.name
                        parts = nick.split()
                        prefix = parts[0]
                        second = parts[1] if len(parts) > 1 else None

                        if prefix.lower() != role.lower():
                            mismatch.append(
                                f'\u001b[0;33m {ign:16} \u001b[1;37mprefix '
                                f'\u001b[0;0m{prefix:10.10} \u001b[1;37m≠ \u001b[0;0m{role}'
                            )
                        if second and second != ign:
                            mismatch.append(
                                f'\u001b[0;33m {ign:16} \u001b[1;37mnick   '
                                f'\u001b[0;0m{second:10.10} \u001b[1;37m≠ \u001b[0;0m{ign}'
                            )
                elif uuid_key(uuid) in guild_accounts:
                    # Guild-owned storage account (TAQ-88): unlinked by design,
                    # so it is not a linkage issue and is not reported.
                    pass
                else:
                    linkage.append(
                        f'\u001b[0;31m {ign:16} \u001b[0;0m{member["rank"].upper():12} NOT LINKED'
                    )

            # Member-ranked links pointing outside the guild are stale and
            # actionable. Rank-NULL ex-member links are normal (TAQ-76 keeps
            # them) and are not reported at all.
            orphans = linked_uuids - guild_uuids
            ranked_orphans = sorted(
                ((links_map[u][2] or u) for u in orphans if links_map[u][1]),
                key=str.lower,
            )
            if ranked_orphans:
                linkage.append('')
                linkage.append('\u001b[0;31mMember-ranked but not in guild (stale links):')
                for ign in ranked_orphans:
                    linkage.append(f'  {ign}')

            hdr_mismatch = (
                '```ansi\n'
                ' \u001b[1;37m{:^16s} {:^6s} {:^24s}\n'
                '╘═════════════════╪════════╪═════════════════════════╛\n'
            ).format('Player', 'Issue', 'Found ≠ Expected')
            hdr_linkage = (
                '```ansi\n'
                ' \u001b[1;37m{:^16s} {:^12s} {:^10s}\n'
                '╘═════════════════╪══════════════╪═══════════╛\n'
            ).format('Player', 'In-Game Rank', 'Status')
            hdr_usernames = (
                '```ansi\n'
                ' \u001b[1;37m{:^16s} → {:^16s}\n'
                '╘═════════════════╪════════════════════╛\n'
            ).format('Linked IGN', 'Guild Name')

            embed_mismatch = Embed(
                title='Mismatch Issues',
                description=(hdr_mismatch + '\n'.join(mismatch) + '```') if mismatch
                else '✅ All ranks, name prefixes and nicknames match.'
            )
            embed_linkage = Embed(
                title='Linkage Issues',
                description=(hdr_linkage + '\n'.join(linkage) + '```') if linkage
                else '✅ Every guild member is linked and there are no stale links.'
            )
            embed_usernames = Embed(
                title='Username Mismatches',
                description=(hdr_usernames + '\n'.join(usernames) + '```') if usernames
                else '✅ No renames — every linked ign matches the guild roster.'
            )

            footer = f'From cached guild data ({cache_age_s}s old)'
            for e in (embed_mismatch, embed_linkage, embed_usernames):
                e.set_footer(text=footer)

            view = ReportPaginator(embed_mismatch, embed_linkage, embed_usernames)
            await interaction.followup.send(embed=embed_mismatch, view=view)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Something blew up: ```{e}```", ephemeral=True)
            traceback.print_exc()

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(RankCheck(client))
