import discord
import traceback
from discord import Embed, ButtonStyle
from discord.commands import slash_command
from discord.ext import commands
import asyncio

from Helpers.classes import Guild
from Helpers.database import DB
from Helpers.functions import getNameFromUUID
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
        # cache for UUID→IGN, and a semaphore to limit concurrency
        self._name_cache = {}
        self._sem = asyncio.Semaphore(5)

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
            data = Guild('The%20Aquarium').all_members
            guild_uuids = {m['uuid'] for m in data}

            # grab Discord members once
            discord_members = {m.id: m for m in interaction.guild.members}

            # DB lookup for links
            db = DB(); db.connect()
            db.cursor.execute("SELECT uuid, discord_id, rank FROM discord_links")
            all_links = db.cursor.fetchall()
            guild_accounts = guild_account_uuids(db.cursor)
            db.close()

            links_map = {row[0]: (row[1], row[2]) for row in all_links}
            linked_uuids = set(links_map)

            hdr = (
                '```ansi\n'
                ' \u001b[1;37m{:^16s}   {:^12s}   {:^23s}\n'
                '╘═════════════════╪══════════════╪════════════════════════╛\n'
            ).format('Player', 'In-Game Rank', 'Discord Rank')

            mismatch, linkage, usernames = [], [], []

            # helper to fetch & cache IGN
            async def fetch_ign(uuid):
                if uuid in self._name_cache:
                    return self._name_cache[uuid]
                async with self._sem:
                    raw = await asyncio.get_event_loop().run_in_executor(
                        None, getNameFromUUID, uuid
                    )
                    # slight pause to avoid hammering API
                    await asyncio.sleep(0.2)
                ign = raw[0] if isinstance(raw, list) and raw else str(raw)
                self._name_cache[uuid] = ign
                return ign

            for member in data:
                uuid = member['uuid']
                # if the API already has a name, use it; otherwise fetch
                ign = member.get('name') or await fetch_ign(uuid)
                stale_api = member.get('name', '')

                if stale_api and stale_api != ign:
                    usernames.append(f'\u001b[0;36m {ign:16} → {stale_api}')

                linked = links_map.get(uuid)
                if linked and linked[1]:   # rank is NULL for linked non-members (TAQ-76)
                    discord_id, role = linked

                    try:
                        expected = discord_ranks[role]['in_game_rank']
                    except KeyError:
                        mismatch.append(
                            f'\u001b[0;31m ERROR: {ign:16} no mapping for role "{role}"'
                        )
                        continue

                    if member['rank'].upper() != expected:
                        dr = f'{role} ({expected})'
                        mismatch.append(
                            f'\u001b[0;0m {ign:16} \u001b[1;37m│ \u001b[0;0m'
                            f'{member["rank"].upper():12} \u001b[1;37m│ \u001b[0;0m{dr:23}'
                        )

                    disc_mem = discord_members.get(discord_id)
                    if disc_mem:
                        nick = disc_mem.nick or disc_mem.name
                        parts = nick.split()
                        prefix = parts[0]
                        second = parts[1] if len(parts) > 1 else None

                        if prefix.lower() != role.lower():
                            mismatch.append(
                                f'\u001b[0;33m PREFIX MISMATCH: "{prefix}" ≠ "{role}" for {ign}'
                            )
                        if second and second != ign:
                            mismatch.append(
                                f'\u001b[0;33m NICKNAME MISMATCH: "{second}" ≠ "{ign}"'
                            )
                elif uuid_key(uuid) in guild_accounts:
                    # Guild-owned storage account: in the guild, nobody to link (TAQ-88).
                    linkage.append(
                        f'[0;0m {ign:16} [1;37m│ [0;0m'
                        f'{member["rank"].upper():12} [1;37m│ [0;35mGUILD ACCOUNT'
                    )
                else:
                    linkage.append(
                        f'\u001b[0;0m {ign:16} \u001b[1;37m│ \u001b[0;0m'
                        f'{member["rank"].upper():12} \u001b[1;37m│ \u001b[0;31mNOT LINKED'
                    )

            orphans = linked_uuids - guild_uuids
            if orphans:
                linkage.append('')
                linkage.append('\u001b[0;35mLinked but not in guild:')
                for uuid in orphans:
                    ign = await fetch_ign(uuid)
                    linkage.append(f'  {ign}')

            embed_mismatch = Embed(
                title='Mismatch Issues',
                description=hdr + '\n'.join(mismatch) + '```'
            )
            embed_linkage = Embed(
                title='Linkage Issues',
                description=hdr + '\n'.join(linkage) + '```'
            )

            hdr3 = (
                '```ansi\n'
                ' \u001b[1;37m{:^16s} → {:^16s}\n'
                '╘═════════════════╪════════════════════╛\n'
            ).format('Official IGN', 'Guild API Name')
            embed_usernames = Embed(
                title='Username Mismatches',
                description=hdr3 + '\n'.join(usernames) + '```'
            )

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
