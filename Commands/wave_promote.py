import asyncio

import discord
from discord import ApplicationContext
from discord.ext import commands

from Helpers.database import DB
from Helpers.variables import guilds, discord_ranks, discord_rank_roles, error_channel


class WavePromote(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client

    @discord.slash_command(
        name='promo_wave',
        description='Promote multiple members by one rank each (up to 25)',
        guild_ids=[guilds[0]],
        default_member_permissions=discord.Permissions(manage_roles=True),
    )
    async def promo_wave(
        self,
        ctx: ApplicationContext,
        user1: discord.Option(discord.Member, "First user to promote"),
        user2: discord.Option(discord.Member, "Second user", required=False, default=None),
        user3: discord.Option(discord.Member, "Third user", required=False, default=None),
        user4: discord.Option(discord.Member, "Fourth user", required=False, default=None),
        user5: discord.Option(discord.Member, "Fifth user", required=False, default=None),
        user6: discord.Option(discord.Member, "Sixth user", required=False, default=None),
        user7: discord.Option(discord.Member, "Seventh user", required=False, default=None),
        user8: discord.Option(discord.Member, "Eighth user", required=False, default=None),
        user9: discord.Option(discord.Member, "Ninth user", required=False, default=None),
        user10: discord.Option(discord.Member, "Tenth user", required=False, default=None),
        user11: discord.Option(discord.Member, "Eleventh user", required=False, default=None),
        user12: discord.Option(discord.Member, "Twelfth user", required=False, default=None),
        user13: discord.Option(discord.Member, "Thirteenth user", required=False, default=None),
        user14: discord.Option(discord.Member, "Fourteenth user", required=False, default=None),
        user15: discord.Option(discord.Member, "Fifteenth user", required=False, default=None),
        user16: discord.Option(discord.Member, "Sixteenth user", required=False, default=None),
        user17: discord.Option(discord.Member, "Seventeenth user", required=False, default=None),
        user18: discord.Option(discord.Member, "Eighteenth user", required=False, default=None),
        user19: discord.Option(discord.Member, "Nineteenth user", required=False, default=None),
        user20: discord.Option(discord.Member, "Twentieth user", required=False, default=None),
        user21: discord.Option(discord.Member, "Twenty-first user", required=False, default=None),
        user22: discord.Option(discord.Member, "Twenty-second user", required=False, default=None),
        user23: discord.Option(discord.Member, "Twenty-third user", required=False, default=None),
        user24: discord.Option(discord.Member, "Twenty-fourth user", required=False, default=None),
        user25: discord.Option(discord.Member, "Twenty-fifth user", required=False, default=None),
    ):
        await ctx.defer(ephemeral=True)

        # Deduplicate and filter None values
        raw_users = [user1, user2, user3, user4, user5,
                     user6, user7, user8, user9, user10,
                     user11, user12, user13, user14, user15,
                     user16, user17, user18, user19, user20,
                     user21, user22, user23, user24, user25]
        seen_ids = set()
        users = []
        for u in raw_users:
            if u is not None and u.id not in seen_ids:
                seen_ids.add(u.id)
                users.append(u)

        if not users:
            await ctx.respond("No valid users provided.", ephemeral=True)
            return

        db = DB()
        db.connect()

        # Fetch invoker's rank once
        db.cursor.execute(
            "SELECT rank FROM discord_links WHERE discord_id = %s",
            (ctx.user.id,)
        )
        initiator_row = db.cursor.fetchone()
        if not initiator_row:
            embed = discord.Embed(
                title=':no_entry: Oops!',
                description=(
                    'You do not have a linked account.\n'
                    'Please use the `/manage link` command first.'
                ),
                color=0xe33232
            )
            await ctx.respond(embed=embed, ephemeral=True)
            db.close()
            return

        initiator_rank = initiator_row[0]
        ranks_list = list(discord_ranks)
        initiator_index = ranks_list.index(initiator_rank)
        all_roles = ctx.guild.roles

        successes = []  # (user, old_rank, new_rank)
        failures = []   # (user, error_message)

        try:
            for i, target in enumerate(users):
                # Self-promotion check
                if target.id == ctx.user.id:
                    failures.append((target, "Cannot promote yourself"))
                    continue

                # Fetch target's rank and uuid
                db.cursor.execute(
                    "SELECT rank, uuid, ign FROM discord_links WHERE discord_id = %s",
                    (target.id,)
                )
                row = db.cursor.fetchone()
                if not row:
                    failures.append((target, "No linked account"))
                    continue

                current_rank, uuid, ign = row
                current_index = ranks_list.index(current_rank)

                # Rank hierarchy check
                if current_index >= initiator_index:
                    failures.append((target, "Rank is not below yours"))
                    continue

                # Max rank check
                new_index = current_index + 1
                if new_index >= len(ranks_list):
                    failures.append((target, "Already at max rank"))
                    continue

                new_rank_key = ranks_list[new_index]
                new_rank = discord_ranks[new_rank_key]

                # Compute role changes
                roles_to_add = []
                for role_name in new_rank['roles']:
                    role = discord.utils.find(lambda r, name=role_name: r.name == name, all_roles)
                    if role and role not in target.roles:
                        roles_to_add.append(role)

                roles_to_remove = []
                for role_name in [r for r in discord_rank_roles if r not in new_rank['roles']]:
                    role = discord.utils.find(lambda r, name=role_name: r.name == name, all_roles)
                    if role and role in target.roles:
                        roles_to_remove.append(role)

                # Apply role changes
                if roles_to_add:
                    await target.add_roles(
                        *roles_to_add,
                        reason=f'Wave promotion (ran by {ctx.user.name})',
                        atomic=True
                    )
                if roles_to_remove:
                    await target.remove_roles(
                        *roles_to_remove,
                        reason=f'Wave promotion (ran by {ctx.user.name})',
                        atomic=True
                    )

                # Update nickname using IGN from database
                try:
                    await target.edit(nick=f'{new_rank_key} {ign}')
                except Exception:
                    pass

                # Persist to DB
                db.cursor.execute(
                    "UPDATE discord_links SET rank = %s WHERE discord_id = %s",
                    (new_rank_key, target.id)
                )
                db.connection.commit()

                successes.append((target, current_rank, new_rank_key))

                # Google Sheets tracking (non-fatal)
                try:
                    from Helpers.sheets import update_promo, find_by_ign, update_paid
                    from Helpers.functions import getUsernameFromUUID
                    name_result = await asyncio.to_thread(getUsernameFromUUID, uuid)
                    if name_result:
                        if new_index >= ranks_list.index("Manatee"):
                            await asyncio.to_thread(update_promo, name_result, "manateePromo")
                        if new_index >= ranks_list.index("Piranha"):
                            await asyncio.to_thread(update_promo, name_result, "piranhaPromo")
                            sheet_row = await asyncio.to_thread(find_by_ign, name_result)
                            if sheet_row.get("success") and sheet_row.get("data"):
                                if sheet_row["data"].get("paid") == "NYP":
                                    await asyncio.to_thread(update_paid, name_result, "N")
                except Exception as e:
                    err_ch = self.client.get_channel(error_channel)
                    if err_ch:
                        await err_ch.send(
                            f"## Recruiter Tracker - Wave Promo Update Error\n"
                            f"**User:** <@{target.id}> | **New rank:** `{new_rank_key}`\n"
                            f"```\n{str(e)[:500]}\n```"
                        )

                # Rate limit: sleep every 5 users
                if (i + 1) % 5 == 0 and (i + 1) < len(users):
                    await asyncio.sleep(1.0)
        finally:
            db.close()

        # Build summary embed
        if not failures:
            color = 0x3ed63e      # green — all succeeded
        elif successes:
            color = 0xebdb34      # yellow — partial success
        else:
            color = 0xe33232      # red — all failed

        embed = discord.Embed(
            title=f'Wave Promotion Results ({len(successes)}/{len(successes) + len(failures)})',
            color=color,
        )

        if successes:
            lines = [f'<@{u.id}>: **{old}** -> **{new}**' for u, old, new in successes]
            for idx, chunk in enumerate(_chunk_lines(lines)):
                name = 'Promoted' if idx == 0 else 'Promoted (cont.)'
                embed.add_field(name=name, value=chunk, inline=False)

        if failures:
            lines = [f'<@{u.id}>: {err}' for u, err in failures]
            for idx, chunk in enumerate(_chunk_lines(lines)):
                name = 'Failed' if idx == 0 else 'Failed (cont.)'
                embed.add_field(name=name, value=chunk, inline=False)

        await ctx.respond(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def _chunk_lines(lines, max_len=1024):
    chunks = []
    current = []
    current_len = 0
    for line in lines:
        add_len = len(line) + (1 if current else 0)
        if current_len + add_len > max_len:
            chunks.append('\n'.join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += add_len
    if current:
        chunks.append('\n'.join(current))
    return chunks


def setup(client):
    client.add_cog(WavePromote(client))
