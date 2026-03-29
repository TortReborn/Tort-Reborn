import asyncio

import discord
from discord.ext import tasks, commands

from Helpers.logger import log, INFO, ERROR
from Helpers.database import DB
from Helpers.variables import (
    TAQ_GUILD_ID,
    ERROR_CHANNEL_ID,
    PROMOTION_CHANNEL_ID,
    discord_ranks,
    discord_rank_roles,
)

MAX_ENTRIES_PER_CYCLE = 10
RATE_LIMIT_SLEEP_EVERY = 5
RATE_LIMIT_SLEEP_SECONDS = 0.5

# Minimum rank index required to queue actions (Hammerhead = index 5)
MIN_QUEUER_RANK_INDEX = 5

# Full role list to strip on 'remove' action (mirrors Commands/reset_roles.py)
REMOVE_ROLES = [
    'Member', 'The Aquarium [TAq]', '☆Reef', 'Starfish', 'Manatee', '★Coastal Waters', 'Piranha',
    'Barracuda', '★★ Azure Ocean', 'Angler', '★☆☆ Blue Sea', 'Hammerhead', '★★☆Deep Sea',
    'Sailfish', '★★★Dark Sea', 'Dolphin', 'Trial-Narwhal', 'Narwhal', '★★★★Abyss Waters',
    '🛡️MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀', '🛡️SR. MODERATOR⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '🥇 RANKS⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀', '🛠️ PROFESSIONS⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
    '✨ COSMETIC ROLES⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀', '🎖️MILITARY⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀', '🏹Spearhead',
    '⚠️Standby', '🗡️FFA', 'DPS', 'Tank', 'Healer', 'Orca', 'War News', 'EcoFish',
    '🏆 CONTRIBUTION ROLES⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
]


def _row_to_dict(row):
    return {
        'id': row[0],
        'uuid': str(row[1]),
        'ign': row[2],
        'current_rank': row[3],
        'new_rank': row[4],
        'action_type': row[5],
        'queued_by_discord_id': row[6],
        'queued_by_ign': row[7],
    }


class PromotionQueueProcessor(commands.Cog):
    def __init__(self, client):
        self.client = client

    @tasks.loop(minutes=1)
    async def _task(self):
        # Guild restriction: operates exclusively on TAQ_GUILD_ID (home guild)
        try:
            rows = await asyncio.to_thread(self._fetch_pending_entries)
        except Exception as e:
            log(ERROR, f"Failed to fetch pending entries: {e}", context="promotion_queue")
            return
        if not rows:
            return

        guild = self.client.get_guild(TAQ_GUILD_ID)
        if not guild:
            log(ERROR, f"Could not find guild {TAQ_GUILD_ID} — skipping {len(rows)} pending entries",
                context="promotion_queue")
            return

        successes = []
        failures = []

        for i, row in enumerate(rows):
            entry = _row_to_dict(row)
            try:
                await self._process_entry(entry, guild)
                await asyncio.to_thread(self._mark_completed, entry['id'])
                successes.append((entry['ign'], entry['action_type'], entry.get('new_rank')))
                log(INFO, f"Processed {entry['action_type']} for {entry['ign']} (id={entry['id']})",
                    context="promotion_queue")
            except Exception as e:
                err_msg = str(e)[:500]
                await asyncio.to_thread(self._mark_failed, entry['id'], err_msg)
                failures.append((entry['ign'], entry['action_type'], err_msg))
                await self._post_error(entry, err_msg)
                log(ERROR, f"Failed {entry['action_type']} for {entry['ign']}: {err_msg}",
                    context="promotion_queue")

            if (i + 1) % RATE_LIMIT_SLEEP_EVERY == 0 and (i + 1) < len(rows):
                await asyncio.sleep(RATE_LIMIT_SLEEP_SECONDS)

        if successes or failures:
            await self._post_summary(successes, failures)

    @_task.error
    async def _task_error(self, error):
        log(ERROR, f"Promotion queue task crashed and stopped: {error}", context="promotion_queue")

    @_task.before_loop
    async def _before(self):
        await self.client.wait_until_ready()
        log(INFO, "Promotion queue processor started", context="promotion_queue")

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._task.is_running():
            self._task.start()

    def cog_unload(self):
        if self._task.is_running():
            self._task.cancel()

    # ---- DB helpers (blocking, called via asyncio.to_thread) ----

    @staticmethod
    def _fetch_pending_entries():
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                """
                WITH claimed AS (
                    UPDATE promotion_queue
                    SET status = 'processing'
                    WHERE id IN (
                        SELECT id FROM promotion_queue
                        WHERE status = 'pending'
                        ORDER BY created_at ASC
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING id, uuid, ign, current_rank, new_rank,
                              action_type, queued_by_discord_id, queued_by_ign
                )
                SELECT * FROM claimed ORDER BY id ASC
                """,
                (MAX_ENTRIES_PER_CYCLE,)
            )
            rows = db.cursor.fetchall()
            db.connection.commit()
            return rows
        finally:
            db.close()

    @staticmethod
    def _mark_completed(entry_id):
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                "UPDATE promotion_queue SET status = 'completed', completed_at = NOW() "
                "WHERE id = %s AND status = 'processing'",
                (entry_id,)
            )
            db.connection.commit()
        finally:
            db.close()

    @staticmethod
    def _mark_failed(entry_id, error_message):
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                "UPDATE promotion_queue SET status = 'failed', completed_at = NOW(), "
                "error_message = %s WHERE id = %s AND status = 'processing'",
                (error_message, entry_id)
            )
            db.connection.commit()
        finally:
            db.close()

    @staticmethod
    def _lookup_member_data(uuid_str):
        """Returns (discord_id, rank) or None. Blocking."""
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                "SELECT discord_id, rank FROM discord_links WHERE uuid = %s",
                (uuid_str,)
            )
            row = db.cursor.fetchone()
            return (int(row[0]), row[1]) if row else None
        finally:
            db.close()

    @staticmethod
    def _lookup_queuer_rank(discord_id):
        """Returns the queuer's current rank from discord_links, or None. Blocking."""
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                "SELECT rank FROM discord_links WHERE discord_id = %s",
                (discord_id,)
            )
            row = db.cursor.fetchone()
            return row[0] if row else None
        finally:
            db.close()

    @staticmethod
    def _update_rank_in_db(discord_id, new_rank):
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                "UPDATE discord_links SET rank = %s WHERE discord_id = %s",
                (new_rank, discord_id)
            )
            db.connection.commit()
        finally:
            db.close()

    @staticmethod
    def _remove_from_discord_links(discord_id):
        db = DB()
        db.connect()
        try:
            db.cursor.execute(
                "DELETE FROM discord_links WHERE discord_id = %s",
                (discord_id,)
            )
            db.connection.commit()
        finally:
            db.close()

    # ---- Processing logic ----

    async def _process_entry(self, entry, guild):
        ranks_list = list(discord_ranks)

        # --- Security: re-verify the queuer's current rank authorizes this action ---
        queuer_rank = await asyncio.to_thread(self._lookup_queuer_rank, entry['queued_by_discord_id'])
        if queuer_rank is None or queuer_rank not in discord_ranks:
            raise ValueError(
                f"Queuer <@{entry['queued_by_discord_id']}> ({entry['queued_by_ign']}) "
                f"no longer has a valid linked account — action rejected"
            )
        queuer_index = ranks_list.index(queuer_rank)
        if queuer_index < MIN_QUEUER_RANK_INDEX:
            raise ValueError(
                f"Queuer <@{entry['queued_by_discord_id']}> ({entry['queued_by_ign']}) "
                f"rank '{queuer_rank}' is below the minimum required to queue actions — action rejected"
            )

        # --- Resolve the target member ---
        member_data = await asyncio.to_thread(self._lookup_member_data, entry['uuid'])
        if member_data is None:
            raise ValueError(
                f"No linked Discord account found for UUID {entry['uuid']} (IGN: {entry['ign']})"
            )
        discord_id, actual_rank = member_data

        # --- Security: verify the queuer outranks the target ---
        if actual_rank in discord_ranks:
            target_index = ranks_list.index(actual_rank)
            if target_index >= queuer_index:
                raise ValueError(
                    f"Queuer '{entry['queued_by_ign']}' (rank {queuer_rank}) cannot manage "
                    f"'{entry['ign']}' (rank {actual_rank}) — target rank is not below queuer"
                )

        # --- Security: verify current_rank matches reality ---
        if actual_rank != entry['current_rank']:
            raise ValueError(
                f"Rank mismatch for {entry['ign']}: queue says '{entry['current_rank']}' "
                f"but discord_links says '{actual_rank}' — action rejected for safety"
            )

        action = entry['action_type']

        # --- Security: validate rank direction ---
        if action == 'promote' and entry['new_rank']:
            if entry['new_rank'] not in discord_ranks:
                raise ValueError(f"new_rank '{entry['new_rank']}' is not a valid rank")
            new_index = ranks_list.index(entry['new_rank'])
            current_index = ranks_list.index(actual_rank)
            if new_index <= current_index:
                raise ValueError(
                    f"Promote action but new_rank '{entry['new_rank']}' (idx {new_index}) "
                    f"is not above current rank '{actual_rank}' (idx {current_index})"
                )
        elif action == 'demote' and entry['new_rank']:
            if entry['new_rank'] not in discord_ranks:
                raise ValueError(f"new_rank '{entry['new_rank']}' is not a valid rank")
            new_index = ranks_list.index(entry['new_rank'])
            current_index = ranks_list.index(actual_rank)
            if new_index >= current_index:
                raise ValueError(
                    f"Demote action but new_rank '{entry['new_rank']}' (idx {new_index}) "
                    f"is not below current rank '{actual_rank}' (idx {current_index})"
                )

        member = guild.get_member(discord_id)
        if member is None:
            try:
                member = await guild.fetch_member(discord_id)
            except Exception:
                member = None
        if member is None:
            raise ValueError(
                f"Discord member {discord_id} (IGN: {entry['ign']}) not found in guild"
            )

        if action == 'promote':
            await self._do_promote(entry, member, guild)
        elif action == 'demote':
            await self._do_demote(entry, member, guild)
        elif action == 'remove':
            await self._do_remove(entry, member, guild)
        else:
            raise ValueError(f"Unknown action_type: {action!r}")

    async def _do_promote(self, entry, member, guild):
        new_rank_key = entry['new_rank']
        if new_rank_key not in discord_ranks:
            raise ValueError(f"new_rank '{new_rank_key}' is not a valid rank")

        reason = f"Website promotion queue (queued by {entry['queued_by_ign']})"
        await self._apply_rank_roles(member, new_rank_key, guild, reason)

        try:
            await member.edit(nick=f'{new_rank_key} {entry["ign"]}')
        except Exception:
            pass

        await asyncio.to_thread(self._update_rank_in_db, member.id, new_rank_key)

        # Google Sheets tracking (non-fatal)
        try:
            from Helpers.sheets import update_promo, find_by_ign, update_paid
            ranks_list = list(discord_ranks)
            new_index = ranks_list.index(new_rank_key)
            ign = entry['ign']
            if new_index >= ranks_list.index("Manatee"):
                await asyncio.to_thread(update_promo, ign, "manateePromo")
            if new_index >= ranks_list.index("Piranha"):
                await asyncio.to_thread(update_promo, ign, "piranhaPromo")
                sheet_row = await asyncio.to_thread(find_by_ign, ign)
                if sheet_row.get("success") and sheet_row.get("data"):
                    if sheet_row["data"].get("paid") == "NYP":
                        await asyncio.to_thread(update_paid, ign, "N")
        except Exception as e:
            err_ch = self.client.get_channel(ERROR_CHANNEL_ID)
            if err_ch:
                await err_ch.send(
                    f"## Promotion Queue - Sheets Update Error\n"
                    f"**User:** <@{member.id}> | **New rank:** `{new_rank_key}`\n"
                    f"```\n{str(e)[:500]}\n```"
                )

    async def _do_demote(self, entry, member, guild):
        new_rank_key = entry['new_rank']
        if new_rank_key not in discord_ranks:
            raise ValueError(f"new_rank '{new_rank_key}' is not a valid rank")

        reason = f"Website demotion queue (queued by {entry['queued_by_ign']})"
        await self._apply_rank_roles(member, new_rank_key, guild, reason)

        try:
            await member.edit(nick=f'{new_rank_key} {entry["ign"]}')
        except Exception:
            pass

        await asyncio.to_thread(self._update_rank_in_db, member.id, new_rank_key)

    async def _do_remove(self, entry, member, guild):
        reason = f"Website removal queue (queued by {entry['queued_by_ign']})"
        all_roles = guild.roles

        # Strip all roles (same list as reset_roles command)
        roles_to_remove = []
        for role_name in REMOVE_ROLES:
            role = discord.utils.find(lambda r, n=role_name: r.name == n, all_roles)
            if role and role in member.roles:
                roles_to_remove.append(role)

        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason=reason, atomic=True)

        # Add Ex-Member role
        ex_member_role = discord.utils.find(lambda r: r.name == 'Ex-Member', all_roles)
        if ex_member_role and ex_member_role not in member.roles:
            await member.add_roles(ex_member_role, reason=reason)

        # Clear nickname
        try:
            await member.edit(nick='')
        except Exception:
            pass

        await asyncio.to_thread(self._remove_from_discord_links, member.id)

    @staticmethod
    async def _apply_rank_roles(member, new_rank_key, guild, reason):
        new_rank = discord_ranks[new_rank_key]
        all_roles = guild.roles

        roles_to_add = []
        for role_name in new_rank['roles']:
            role = discord.utils.find(lambda r, n=role_name: r.name == n, all_roles)
            if role and role not in member.roles:
                roles_to_add.append(role)

        roles_to_remove = []
        for role_name in [r for r in discord_rank_roles if r not in new_rank['roles']]:
            role = discord.utils.find(lambda r, n=role_name: r.name == n, all_roles)
            if role and role in member.roles:
                roles_to_remove.append(role)

        if roles_to_add:
            await member.add_roles(*roles_to_add, reason=reason, atomic=True)
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason=reason, atomic=True)

    # ---- Notifications ----

    async def _post_error(self, entry, error_msg):
        err_ch = self.client.get_channel(ERROR_CHANNEL_ID)
        if not err_ch:
            return
        embed = discord.Embed(
            title="Promotion Queue - Processing Failed",
            color=0xe33232,
        )
        embed.add_field(name="IGN", value=entry['ign'], inline=True)
        embed.add_field(name="Action", value=entry['action_type'], inline=True)
        embed.add_field(name="New Rank", value=entry.get('new_rank') or "N/A", inline=True)
        embed.add_field(
            name="Queued By",
            value=f"<@{entry['queued_by_discord_id']}> ({entry['queued_by_ign']})",
            inline=False,
        )
        embed.add_field(name="Error", value=f"```{error_msg[:900]}```", inline=False)
        await err_ch.send(embed=embed)

    async def _post_summary(self, successes, failures):
        promo_ch = self.client.get_channel(PROMOTION_CHANNEL_ID)
        if not promo_ch:
            return

        if successes and not failures:
            color = 0x3ed63e
        elif successes:
            color = 0xebdb34
        else:
            color = 0xe33232

        embed = discord.Embed(
            title=f"Promotion Queue Results ({len(successes)}/{len(successes) + len(failures)})",
            color=color,
        )

        if successes:
            lines = []
            for ign, action_type, new_rank in successes:
                if new_rank:
                    lines.append(f"**{ign}**: {action_type} -> **{new_rank}**")
                else:
                    lines.append(f"**{ign}**: {action_type}")
            embed.add_field(name="Completed", value="\n".join(lines)[:1024], inline=False)

        if failures:
            lines = [f"**{ign}** ({action_type}): {err[:100]}" for ign, action_type, err in failures]
            embed.add_field(name="Failed", value="\n".join(lines)[:1024], inline=False)

        await promo_ch.send(embed=embed)


def setup(client):
    client.add_cog(PromotionQueueProcessor(client))
