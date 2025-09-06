# vanity_roles.py
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, date, timezone, timedelta, time as dtime
from typing import Dict, Any, List, Optional, Tuple

import discord
from discord.ext import commands, tasks
from discord.commands import slash_command
from discord import default_permissions

from Helpers.database import DB
from Helpers.variables import guilds, announcement_channel, faq_channel, VANITY_ROLE_NAMES

START_DATE_UTC = date(2025, 8, 31)  # first run date (YYYY, M, D)

WINDOW_DAYS = 14  # bi-weekly window

CURRENT_PATH = "current_activity.json"
HISTORY_PATH = "player_activity.json"

@dataclass
class WindowedStats:
    wars: int
    raids: int


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _build_hist_index(history_mrf: List[Dict[str, Any]]) -> List[Dict[str, Dict[str, Any]]]:
    """Index snapshots (most recent first) -> {uuid -> member entry}."""
    out: List[Dict[str, Dict[str, Any]]] = []
    for snap in history_mrf:
        idx_map: Dict[str, Dict[str, Any]] = {}
        for m in snap.get("members", []):
            uid = m.get("uuid")
            if uid:
                idx_map[uid] = m
        out.append(idx_map)
    return out


def _get_current_value(cur_by_uuid: Dict[str, Dict[str, Any]], uuid: str, key: str) -> int:
    m = cur_by_uuid.get(uuid)
    if not m:
        return 0
    v = m.get(key)
    try:
        return int(v) if isinstance(v, (int, float)) else int(v or 0)
    except Exception:
        return 0


def _find_inclusive_baseline(
    hist_by_uuid_at: List[Dict[str, Dict[str, Any]]],
    uuid: str,
    key: str,
    window_days: int,
) -> int:
    """
    Inclusive baseline:
      - baseline_idx = window_days (the (W+1)-th most recent snapshot)
      - if missing at that index, walk toward newer (W-1 ... 0)
      - if never found, baseline = 0 (safe default)
    """
    num = len(hist_by_uuid_at)
    if num == 0:
        return 0

    baseline_idx = min(window_days, num - 1)
    entry = hist_by_uuid_at[baseline_idx].get(uuid)
    if entry is not None:
        try:
            return int(entry.get(key) or 0)
        except Exception:
            pass

    for i in range(baseline_idx - 1, -1, -1):
        e = hist_by_uuid_at[i].get(uuid)
        if e is not None:
            try:
                return int(e.get(key) or 0)
            except Exception:
                return 0
    return 0


def compute_windowed_stats(window_days: int = WINDOW_DAYS) -> Dict[str, WindowedStats]:
    """
    Returns { uuid -> WindowedStats(wars=Δ, raids=Δ) } for the last `window_days`.
    Uses current_activity.json (live) minus inclusive-baseline from player_activity.json (MRF).
    """
    current = _load_json(CURRENT_PATH, {})
    history_mrf: List[Dict[str, Any]] = _load_json(HISTORY_PATH, [])

    cur_members = current.get("members", []) if isinstance(current, dict) else []
    cur_by_uuid = {m["uuid"]: m for m in cur_members if isinstance(m, dict) and m.get("uuid")}

    hist_by_uuid_at = _build_hist_index(history_mrf)

    out: Dict[str, WindowedStats] = {}
    for uuid in cur_by_uuid.keys():
        curr_wars = _get_current_value(cur_by_uuid, uuid, "wars")
        curr_raids = _get_current_value(cur_by_uuid, uuid, "raids")

        base_wars = _find_inclusive_baseline(hist_by_uuid_at, uuid, "wars", window_days)
        base_raids = _find_inclusive_baseline(hist_by_uuid_at, uuid, "raids", window_days)

        wars_delta = max(curr_wars - base_wars, 0)
        raids_delta = max(curr_raids - base_raids, 0)
        out[uuid] = WindowedStats(wars=wars_delta, raids=raids_delta)
    return out

# --- NEW: tier helpers returning 't1'/'t2'/'t3' (or None) ---
def _war_tier_label(wars_14d: int) -> Optional[str]:
    if wars_14d >= 120:
        return "t3"
    if wars_14d >= 80:
        return "t2"
    if wars_14d >= 40:
        return "t1"
    return None


def _raid_tier_label(raids_14d: int) -> Optional[str]:
    if raids_14d >= 80:
        return "t3"
    if raids_14d >= 50:
        return "t2"
    if raids_14d >= 30:
        return "t1"
    return None


class VanityRoles(commands.Cog):
    """
    Bi-weekly vanity roles:
      - Runs at 00:10 UTC each day; executes only when (today - START_DATE).days % 14 == 0
      - On run: remove undesired vanity roles, then assign based on 14-day window
      - Sends an announcement embed listing recipients per tier in the configured channel.
    """

    def __init__(self, client: discord.Client):
        self.client = client

        # Daily at 00:10 UTC.
        self._running = asyncio.Lock()
        self.biweekly_roles.start()
        

    def cog_unload(self):
        if self.biweekly_roles.is_running():
            self.biweekly_roles.cancel()

    # ---- scheduler (00:10 UTC daily; gate to every 14 days) ----
    @tasks.loop(time=dtime(hour=0, minute=10, tzinfo=timezone.utc))
    async def biweekly_roles(self):
        if not self.client.is_ready():
            return

        today_utc = datetime.now(timezone.utc).date()
        if (today_utc - START_DATE_UTC).days % 7 != 0:
            return

        await self._run_biweekly_and_announce()

    @biweekly_roles.before_loop
    async def _before(self):
        await self.client.wait_until_ready()

    # -----------------------------
    # Role Utilities
    # -----------------------------
    async def _precheck_permissions(self, guild: discord.Guild, resolved_roles: Dict[str, Dict[str, discord.Role]]) -> bool:
        me = guild.me
        ok = True
        if not me.guild_permissions.manage_roles:
            print("[vanity_roles] PRECHECK: Missing 'Manage Roles' permission.")
            ok = False
        # Bot top role must be above every vanity role
        for sec in resolved_roles.values():
            for role in sec.values():
                if role >= me.top_role:
                    print(f"[vanity_roles] PRECHECK: Bot's top role must be above '{role.name}' ({role.id}).")
                    ok = False
        return ok

    async def _get_member_anyhow(self, guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
        m = guild.get_member(user_id)
        if m is not None:
            return m
        try:
            return await guild.fetch_member(user_id)
        except discord.NotFound:
            return None

    async def _resolve_roles_by_exact_name(self, guild: discord.Guild) -> Dict[str, Dict[str, discord.Role]]:
        """Resolve vanity roles strictly by exact name; abort (raise) if any missing."""
        roles = await guild.fetch_roles()
        name_index = {r.name: r for r in roles}  # case-sensitive on purpose

        resolved: Dict[str, Dict[str, discord.Role]] = {"wars": {}, "raids": {}}
        missing = []
        for section in ("wars", "raids"):
            for tier, name in VANITY_ROLE_NAMES[section].items():
                role = name_index.get(name)
                if role is None:
                    missing.append((section, tier, name))
                else:
                    resolved[section][tier] = role

        if missing:
            print("[vanity_roles] ERROR: The following vanity roles were not found by exact name:")
            for section, tier, name in missing:
                print(f"  - {section} {tier}: '{name}'")
            print("[vanity_roles] Aborting. Fix the role names or create the roles exactly as listed.")
            raise RuntimeError("Missing vanity roles by name")

        return resolved

    async def _strip_all_vanity_roles(self, guild: discord.Guild, resolved: Dict[str, Dict[str, discord.Role]]) -> int:
        """Remove all vanity roles (from resolved) from everyone."""
        vanity_ids = {role.id for sec in resolved.values() for role in sec.values()}
        changes = 0
        async for member in guild.fetch_members(limit=None):
            to_remove = [r for r in member.roles if r.id in vanity_ids]
            if not to_remove:
                continue
            try:
                await member.remove_roles(*to_remove, reason="Bi-weekly vanity role full reset")
                changes += 1
                if changes % 25 == 0:
                    await asyncio.sleep(1.0)
            except Exception as e:
                print(f"[vanity_roles] strip: remove_roles failed for {member.id}: {e}")
        print(f"[vanity_roles] strip: removed vanity roles from ~{changes} members")
        return changes

    async def _assign_roles_to_winners(self, guild: discord.Guild, winners_ids: Dict[str, Dict[str, List[int]]],
                                    resolved: Dict[str, Dict[str, discord.Role]]) -> int:
        """Grant winners their roles using resolved Role objects.
        Also ensures the contribution bucket role is present for any winner."""
        BUCKET_ROLE_NAME = "🏆 CONTRIBUTION ROLES⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀"
        bucket_role = discord.utils.get(guild.roles, name=BUCKET_ROLE_NAME)

        if bucket_role is None:
            print(f"[vanity_roles] WARNING: Contribution bucket role not found: '{BUCKET_ROLE_NAME}'")

        grants = 0
        for section in ("wars", "raids"):
            for tier in ("t3", "t2", "t1"):
                role = resolved[section][tier]
                for did in winners_ids[section][tier]:
                    member = await self._get_member_anyhow(guild, did)
                    if member is None:
                        continue

                    # Build exactly what we need to add (role + bucket if missing)
                    roles_to_add: List[discord.Role] = []
                    if role not in member.roles:
                        roles_to_add.append(role)
                    if bucket_role and bucket_role not in member.roles:
                        roles_to_add.append(bucket_role)

                    if not roles_to_add:
                        continue

                    try:
                        await member.add_roles(
                            *roles_to_add,
                            reason=f"Bi-weekly vanity role assignment: {section.upper()} {tier.upper()}",
                        )
                        grants += 1
                        if grants % 25 == 0:
                            await asyncio.sleep(1.0)
                    except Exception as e:
                        print(f"[vanity_roles] assign: add_roles failed for {member.id}: {e}")
        print(f"[vanity_roles] assign: granted roles to ~{grants} members")
        return grants

    # -----------------------------
    # Internal runner + announcer
    # -----------------------------
    async def _run_biweekly_and_announce(self) -> None:
        if getattr(self, "_running", None) is None:
            self._running = asyncio.Lock()

        if self._running.locked():
            print("[vanity_roles] Run skipped: job already in progress")
            return

        async with self._running:
            guild = self.client.get_guild(guilds[0])
            if guild is None:
                print("[vanity_roles] Guild not found; check Helpers.variables.guilds[0].")
                return

            print(f"[vanity_roles] Running for guild: {guild.name} ({guild.id})")

            # 1) Resolve roles strictly by exact name
            try:
                resolved = await self._resolve_roles_by_exact_name(guild)
            except RuntimeError:
                return  # stop if names don't match
            
            # 2) Permissions / hierarchy check against resolved roles
            if not await self._precheck_permissions(guild, resolved):
                print("[vanity_roles] Aborting: fix permissions / role order.")
                return

            # 3) FULL RESET: remove all vanity roles from everyone
            await self._strip_all_vanity_roles(guild, resolved)

            # 4) Compute winners (unchanged logic)
            stats_by_uuid = await asyncio.to_thread(compute_windowed_stats, WINDOW_DAYS)

            def _fetch_links() -> List[Tuple[str, int]]:
                db = DB(); db.connect()
                db.cursor.execute("SELECT uuid, discord_id FROM discord_links")
                rows = db.cursor.fetchall()
                db.close()
                return [(str(uid), int(did)) for uid, did in rows if uid and did]

            mapping = await asyncio.to_thread(_fetch_links)
            uuid_to_discord: Dict[str, int] = dict(mapping)

            winners_ids: Dict[str, Dict[str, List[int]]] = {
                "wars": {"t1": [], "t2": [], "t3": []},
                "raids": {"t1": [], "t2": [], "t3": []},
            }
            for uuid, stats in stats_by_uuid.items():
                did = uuid_to_discord.get(uuid)
                if not did:
                    continue
                wtier = _war_tier_label(stats.wars)
                if wtier:
                    winners_ids["wars"][wtier].append(did)
                rtier = _raid_tier_label(stats.raids)
                if rtier:
                    winners_ids["raids"][rtier].append(did)

            # 5) Assign winners using resolved Role objects
            await self._assign_roles_to_winners(guild, winners_ids, resolved)

            # 6) Announce (pass resolved so we can @role by object)
            await self._send_announcement_embed(guild, winners_ids, resolved)
            print(f"[vanity_roles] Completed bi-weekly pass at {datetime.now(timezone.utc)}")

    # -----------------------------
    # Announcement embed helper
    # -----------------------------
    async def _send_announcement_embed(self, guild: discord.Guild,
                                    winners: Dict[str, Dict[str, List[int]]],
                                    resolved: Dict[str, Dict[str, discord.Role]]):
        channel = self.client.get_channel(announcement_channel) or guild.system_channel
        if channel is None:
            print("[vanity_roles] Announcement channel not found.")
            return

        title = "🎉 Vanity Roles Awarded"
        faq_hint = f"\nFor more information see <#{faq_channel}>"
        desc = f"Congrats to our top contributors over the last **{WINDOW_DAYS} days**!{faq_hint}"
        embed = discord.Embed(title=title, description=desc, color=discord.Color.blurple())

        def chunk_by_length(lines: List[str], max_len: int = 1000) -> List[str]:
            chunks, buf, cur_len = [], [], 0
            for line in lines:
                add_len = (1 if buf else 0) + len(line)
                if cur_len + add_len > max_len:
                    chunks.append("\n".join(buf))
                    buf, cur_len = [line], len(line)
                else:
                    buf.append(line)
                    cur_len += add_len
            if buf:
                chunks.append("\n".join(buf))
            return chunks

        def tier_lines(section_key: str, tier: str) -> List[str]:
            ids = winners[section_key][tier]
            if not ids:
                return []
            role_obj = resolved[section_key][tier]
            header = f"{role_obj.mention}"
            mention_lines = [f"<@{i}>" for i in ids]
            return [header, *mention_lines]

        sections = [
            ("Wars", "wars", ["t3", "t2", "t1"]),
            ("Raids", "raids", ["t3", "t2", "t1"]),
        ]

        any_section_added = False
        for heading, key, tiers in sections:
            section_lines: List[str] = []
            for tier in tiers:
                t_lines = tier_lines(key, tier)
                if not t_lines:
                    continue
                if section_lines:
                    section_lines.append("")  # blank line between non-empty tiers
                section_lines.extend(t_lines)

            if not section_lines:
                continue

            any_section_added = True
            chunks = chunk_by_length(section_lines)
            for idx, block in enumerate(chunks):
                name = f"__{heading}__" if idx == 0 else f"__{heading}__ (cont.)"
                embed.add_field(name=name, value=block, inline=False)

        if not any_section_added:
            # Optional: post a “no awards” embed instead of silent skip
            empty = discord.Embed(
                title="🎉 Vanity Roles Awarded",
                description=f"No vanity roles awarded this cycle.\nFor more information see <#{faq_channel}>",
                color=discord.Color.blurple(),
            )
            try:
                await channel.send(embed=empty)
            except Exception as e:
                print(f"[vanity_roles] Failed to send empty announcement: {e}")
            return

        try:
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=True, everyone=False),
            )
        except Exception as e:
            print(f"[vanity_roles] Failed to send announcement: {e}")

    # -----------------------------
    # Slash command: preview/run
    # -----------------------------
    @slash_command(name="vanityroles", guild_ids=guilds, description="Admin: preview or run the bi-weekly vanity role job")
    @default_permissions(administrator=True)
    async def vanityroles(self, ctx: discord.ApplicationContext, action: discord.Option(str, choices=["run", "preview"])):
        """Admin helper: 'run' applies changes now + announces; 'preview' prints counts only."""
        await ctx.defer(ephemeral=True)
        guild = self.client.get_guild(guilds[0])
        if guild is None:
            await ctx.respond("Guild not found. Fix guild ID.", ephemeral=True)
            return

        if action == "preview":
            # Compute preview counts only
            stats_by_uuid = await asyncio.to_thread(compute_windowed_stats, WINDOW_DAYS)

            def _fetch_links() -> List[Tuple[str, int]]:
                db = DB(); db.connect()
                db.cursor.execute("SELECT uuid, discord_id FROM discord_links")
                rows = db.cursor.fetchall()
                db.close()
                return [(str(uid), int(did)) for uid, did in rows if uid and did]

            mapping = await asyncio.to_thread(_fetch_links)
            uuid_to_discord = {uid: did for uid, did in mapping}
            counts = {"wars_t1": 0, "wars_t2": 0, "wars_t3": 0, "raids_t1": 0, "raids_t2": 0, "raids_t3": 0}
            assignments = 0
            for uuid, stats in stats_by_uuid.items():
                if uuid not in uuid_to_discord:
                    continue
                w = stats.wars
                r = stats.raids
                if w >= 120: counts["wars_t3"] += 1
                elif w >= 80: counts["wars_t2"] += 1
                elif w >= 40: counts["wars_t1"] += 1
                if r >= 80: counts["raids_t3"] += 1
                elif r >= 50: counts["raids_t2"] += 1
                elif r >= 30: counts["raids_t1"] += 1
                if w >= 40 or r >= 30:
                    assignments += 1

            msg = (
                f"**Preview (last {WINDOW_DAYS} days)**\n"
                f"Wars: T1={counts['wars_t1']}, T2={counts['wars_t2']}, T3={counts['wars_t3']}\n"
                f"Raids: T1={counts['raids_t1']}, T2={counts['raids_t2']}, T3={counts['raids_t3']}\n"
                f"Members with ≥1 vanity role: ~{assignments}"
            )
            await ctx.respond(msg, ephemeral=True)
        else:
            # Run immediately (no date gate) and announce
            await self._run_biweekly_and_announce()
            await ctx.respond("Bi-weekly vanity role job ran and announcement sent.", ephemeral=True)


def setup(client: discord.Client):
    client.add_cog(VanityRoles(client))
