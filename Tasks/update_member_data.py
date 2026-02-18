import asyncio
import io
import json
import re
import sys
import discord
import datetime
import time
import traceback
from datetime import timezone, timedelta, time as dtime
from collections import deque
from discord.ext import tasks, commands
from discord.commands import slash_command
from discord import default_permissions
from PIL import Image, ImageDraw, ImageFont

# ensure prints flush immediately
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
else:
    import os as _os
    sys.stdout = _os.fdopen(sys.stdout.fileno(), 'w', buffering=1)

from Helpers.logger import log, INFO, ERROR
from Helpers.classes import Guild, DB, BasicPlayerStats
from Helpers.embed_updater import update_poll_embed, update_web_poll_embed
from Helpers.functions import getPlayerDatav3, getNameFromUUID, determine_starting_rank, create_progress_bar, addLine, round_corners
from Helpers.variables import (
    RAID_LOG_CHANNEL_ID,
    BOT_LOG_CHANNEL_ID,
    NOTG_EMOJI,
    TCC_EMOJI,
    TNA_EMOJI,
    NOL_EMOJI,
    ASPECT_EMOJI,
    ALL_GUILD_IDS,
    TAQ_GUILD_ID,
    WELCOME_CHANNEL_ID,
    discord_ranks,
)

RAID_ANNOUNCE_CHANNEL_ID = RAID_LOG_CHANNEL_ID
LOG_CHANNEL = BOT_LOG_CHANNEL_ID
GUILD_TTL = timedelta(minutes=10)
CONTRIBUTION_THRESHOLD = 2_500_000_000
RATE_LIMIT = 100  # max calls per minute

RAID_EMOJIS = {
    "Nest of the Grootslangs": NOTG_EMOJI,
    "The Canyon Colossus": TCC_EMOJI,
    "The Nameless Anomaly": TNA_EMOJI,
    "Orphion's Nexus of Light": NOL_EMOJI
}

# --- thread-safe DB + snapshot helpers ---

def _db_connect_with_retry(max_attempts: int = 3, backoff_first: float = 0.5):
    attempt, delay, last = 0, backoff_first, None
    while attempt < max_attempts:
        try:
            db = DB()
            db.connect()
            return db
        except Exception as e:
            last = e
            time.sleep(delay)
            delay *= 2
            attempt += 1
    raise last

def _upsert_raid_group_sync(uuid_list):
    if not uuid_list:
        return
    db = _db_connect_with_retry()
    try:
        for uid in uuid_list:
            db.cursor.execute("SELECT ign FROM discord_links WHERE uuid = %s", (uid,))
            row = db.cursor.fetchone()
            ign = row[0] if row else None
            db.cursor.execute(
                """
                INSERT INTO uncollected_raids AS ur (uuid, ign, uncollected_raids, collected_raids)
                VALUES (%s, %s, 1, 0)
                ON CONFLICT (uuid) DO UPDATE
                  SET uncollected_raids = ur.uncollected_raids + EXCLUDED.uncollected_raids,
                      ign               = EXCLUDED.ign;
                """,
                (uid, ign)
            )
        db.connection.commit()
    finally:
        db.close()

def _graid_increment_group_sync(uuid_list, raid_name: str):
    if not uuid_list: return
    db = _db_connect_with_retry()
    try:
        cur = db.cursor
        # find active event
        cur.execute("SELECT id FROM graid_events WHERE active = TRUE LIMIT 1")
        row = cur.fetchone()
        if not row:
            return
        event_id = row[0]

        # upsert totals; 1 completion per player per validated group
        for uid in uuid_list:
            cur.execute("""
                INSERT INTO graid_event_totals (event_id, uuid, total)
                VALUES (%s, %s, 1)
                ON CONFLICT (event_id, uuid) DO UPDATE
                  SET total = graid_event_totals.total + 1,
                      last_updated = NOW()
            """, (event_id, uid))
        db.connection.commit()
    finally:
        db.close()


def _write_current_snapshot_sync(contrib_map, rank_map, pf_map, online_map):
    snap = {'time': int(time.time()), 'members': []}
    uuids = list(pf_map.keys())
    if not uuids:
        # Save empty snapshot to database cache
        try:
            db = _db_connect_with_retry()

            # Set expiration to epoch time (January 1, 1970)
            epoch_time = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)

            db.cursor.execute("""
                INSERT INTO cache_entries (cache_key, data, expires_at, fetch_count)
                VALUES (%s, %s, %s, 1)
                ON CONFLICT (cache_key)
                DO UPDATE SET
                    data = EXCLUDED.data,
                    created_at = NOW(),
                    expires_at = EXCLUDED.expires_at,
                    fetch_count = cache_entries.fetch_count + 1,
                    last_error = NULL,
                    error_count = 0
            """, ('guildData', json.dumps(snap), epoch_time))

            db.connection.commit()
            db.close()

        except Exception as e:
            log(ERROR, f"Failed to save empty snapshot to cache: {e}", context="update_member_data")
            try:
                if 'db' in locals():
                    db.close()
            except:
                pass
        return

    db = _db_connect_with_retry()
    try:
        sql = """
        SELECT
        dl.uuid,
        COALESCE(s.shells, 0) AS shells,
        COALESCE(ur.uncollected_raids, 0) + COALESCE(ur.collected_raids, 0) AS raids_total
        FROM discord_links dl
        LEFT JOIN shells s ON dl.discord_id = s.user
        LEFT JOIN uncollected_raids ur ON dl.uuid = ur.uuid
        WHERE dl.uuid = ANY(%s::uuid[]);
        """
        db.cursor.execute(sql, (uuids,))
        rows = db.cursor.fetchall()
        stats_by_uuid = {row[0]: {'shells': row[1], 'raids': row[2]} for row in rows}
    finally:
        db.close()

    for uuid, pf in pf_map.items():
        if not isinstance(pf, dict):
            continue
        username  = pf.get('username') or pf.get('name')
        last_join = pf.get('lastJoin', "2020-03-22T11:11:17.810000Z")
        entry     = stats_by_uuid.get(uuid, {'shells': 0, 'raids': 0})
        snap['members'].append({
            'name':        username,
            'uuid':        uuid,
            'rank':        rank_map.get(uuid),
            'playtime':    pf.get('playtime'),
            'contributed': contrib_map.get(uuid),
            'wars':        pf.get('globalData', {}).get('wars'),
            'shells':      entry['shells'],
            'raids':       entry['raids'],
            'lastJoin':    last_join
        })

    # Create cache version with online status
    cache_snap = {'time': snap['time'], 'members': []}
    for member in snap['members']:
        cache_member = dict(member)
        cache_member['online'] = online_map.get(member['uuid'], False)
        cache_snap['members'].append(cache_member)

    # Save to database cache
    try:
        db = _db_connect_with_retry()

        # Set expiration to epoch time (January 1, 1970)
        epoch_time = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)

        # Use ON CONFLICT to either insert or update the cache entry
        db.cursor.execute("""
            INSERT INTO cache_entries (cache_key, data, expires_at, fetch_count)
            VALUES (%s, %s, %s, 1)
            ON CONFLICT (cache_key)
            DO UPDATE SET
                data = EXCLUDED.data,
                created_at = NOW(),
                expires_at = EXCLUDED.expires_at,
                fetch_count = cache_entries.fetch_count + 1,
                last_error = NULL,
                error_count = 0
        """, ('guildData', json.dumps(cache_snap), epoch_time))

        db.connection.commit()
        db.close()
        
    except Exception as e:
        log(ERROR, f"Failed to save to cache: {e}", context="update_member_data")
        # Don't let database errors prevent the file save from working
        try:
            if 'db' in locals():
                db.close()
        except:
            pass


class UpdateMemberData(commands.Cog):
    RAID_NAMES = [
        "Nest of the Grootslangs",
        "The Canyon Colossus",
        "The Nameless Anomaly",
        "Orphion's Nexus of Light"
    ]

    def __init__(self, client):
        self.client = client
        self._has_started = False
        self.previous_data = self._load_from_cache("previousMemberData", {})
        self.previous_members = self._load_from_cache("memberList", {})
        self.member_list_exists = bool(self.previous_members)
        self.raid_participants = {raid: {"unvalidated": {}, "validated": {}} for raid in self.RAID_NAMES}
        self.xp_only_validated = {}  # uuid -> {"name": str, "first_seen": datetime} for players with XP jump but no detected raid type
        self.cold_start = True
        self.request_times = deque()
        self._semaphore = asyncio.Semaphore(5)

    def _load_from_cache(self, key, default):
        try:
            db = _db_connect_with_retry()
            db.cursor.execute("SELECT data FROM cache_entries WHERE cache_key = %s", (key,))
            row = db.cursor.fetchone()
            db.close()
            if row and row[0]:
                return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        except Exception:
            traceback.print_exc()
        return default

    def _save_to_cache(self, key, data):
        try:
            db = _db_connect_with_retry()
            epoch = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
            db.cursor.execute("""
                INSERT INTO cache_entries (cache_key, data, expires_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (cache_key) DO UPDATE SET
                    data = EXCLUDED.data, created_at = NOW(),
                    expires_at = EXCLUDED.expires_at,
                    fetch_count = cache_entries.fetch_count + 1
            """, (key, json.dumps(data), epoch))
            db.connection.commit()
            db.close()
        except Exception:
            traceback.print_exc()

    async def _announce_raid(self, raid, group, guild, participant_names=None):
        log(INFO, f"Announcing raid {raid}: {group}", context="update_member_data")
        if participant_names:
            names = [participant_names.get(uid, uid) for uid in group]
        else:
            participants = self.raid_participants[raid]["validated"]
            names = [participants[uid]["name"] for uid in group]
        bolded = [f"**{discord.utils.escape_markdown(n)}**" for n in names]
        names_str = ", ".join(bolded[:-1]) + ", and " + bolded[-1] if len(bolded) > 1 else bolded[0]

        if raid:
            emoji = RAID_EMOJIS.get(raid, "")
            title = f"{emoji} {raid} Completed!"
        else:
            title = f"{ASPECT_EMOJI} Guild Raid Completed!"

        channel = self.client.get_channel(RAID_ANNOUNCE_CHANNEL_ID)
        if channel:
            embed = discord.Embed(
                title=title,
                description=names_str,
                color=0x00FF00
            )

            # Generate image-based progress bar
            guild_level = getattr(guild, "level", None)
            guild_xp = getattr(guild, "xpPercent", None)
            if guild_level is not None and guild_xp is not None:
                progress_img = self._render_guild_progress(guild_level, guild_xp)
            else:
                progress_img = self._render_guild_progress(0, 100)

            buf = io.BytesIO()
            progress_img.save(buf, format='PNG')
            buf.seek(0)
            file = discord.File(buf, filename="raid_progress.png")
            embed.set_image(url="attachment://raid_progress.png")

            await channel.send(embed=embed, file=file)

        await asyncio.to_thread(_upsert_raid_group_sync, list(group))
        await asyncio.to_thread(_graid_increment_group_sync, list(group), raid)

    def _render_guild_progress(self, level, xp_percent):
        """Render a styled guild level progress bar image."""
        width = 400
        img = Image.new('RGBA', (width, 32), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Centered text: gray "LV. {level} - " + cyan "THE AQUARIUM" + gray " - {xp}% XP"
        font = ImageFont.truetype('images/profile/5x5.ttf', 20)
        text = f"&7LV. {level} - &bTHE AQUARIUM &7- {xp_percent}% XP"

        # Measure width on a temp draw to center
        temp = Image.new('RGBA', (1, 1))
        text_width = addLine(text, ImageDraw.Draw(temp), font, 0, 0, drop_x=0, drop_y=0)
        x_start = (width - text_width) // 2

        addLine(text, draw, font, x_start, 0, drop_x=1, drop_y=1)

        # Light blue bar with rounded ends
        bar = create_progress_bar(width, xp_percent, color='#5599dd', scale=1)
        bar = round_corners(bar, radius=5)
        img.paste(bar, (0, 22), bar)

        return img

    @tasks.loop(minutes=3)
    async def update_member_data(self):
        now = datetime.datetime.now(timezone.utc)
        log(INFO, "STARTING LOOP", context="update_member_data")

        # fetch guild over HTTP off the event loop
        guild = await asyncio.to_thread(Guild, "The Aquarium")

        # 1 Pull latest guild contributions
        contrib_map = {member['uuid']: member.get('contributed', 0) for member in guild.all_members}

        # 2: Prune stale unvalidated entries
        cutoff = now - GUILD_TTL
        for raid, queues in self.raid_participants.items():
            for uid, info in list(queues['unvalidated'].items()):
                first = info['first_seen']
                if isinstance(first, str): first = datetime.datetime.fromisoformat(first)
                if first < cutoff:
                    log(INFO, f"PRUNE unvalidated {info['name']} in {raid}", context="update_member_data")
                    queues['unvalidated'].pop(uid)

        # 2b: Prune stale xp_only_validated entries
        for uid, info in list(self.xp_only_validated.items()):
            first = info['first_seen']
            if isinstance(first, str):
                first = datetime.datetime.fromisoformat(first)
            if first < cutoff:
                log(INFO, f"PRUNE xp_only_validated {info['name']}", context="update_member_data")
                self.xp_only_validated.pop(uid)

        # 3: Member join/leave
        prev_map = self.previous_members
        curr_map = {m['uuid']: {'name': m['name'], 'rank': m.get('rank')} for m in guild.all_members}
        joined, left = set(curr_map) - set(prev_map), set(prev_map) - set(curr_map)
        if (joined or left) and not (self.cold_start and not self.member_list_exists):
            ch = self.client.get_channel(LOG_CHANNEL)
            def add_chunked(embed, title, items):
                chunk = ''
                for ign in items:
                    safe = discord.utils.escape_markdown(ign)
                    piece = f"**{safe}**" if not chunk else f", **{safe}**"
                    if len(chunk)+len(piece)>1024:
                        embed.add_field(name=title, value=chunk, inline=False)
                        chunk=f"**{safe}**"; title+=' cont.'
                    else:
                        chunk+=piece
                if chunk: embed.add_field(name=title, value=chunk, inline=False)
            if joined:
                ej = discord.Embed(title='Guild Members Joined', timestamp=now, color=0x00FF00)
                add_chunked(ej, 'Joined', [getNameFromUUID(u)[0] for u in joined])
                await ch.send(embed=ej)
                # Auto-register members who have pending accepted applications
                for uuid in joined:
                    try:
                        await self._auto_register_joined_member(uuid, curr_map[uuid]['name'])
                    except Exception as e:
                        log(ERROR, f"Error for {uuid}: {e}", context="auto_register")
            if left:
                el = discord.Embed(title='Guild Members Left', timestamp=now, color=0xFF0000)
                add_chunked(el, 'Left', [prev_map[u]['name'] for u in left])
                await ch.send(embed=el)
        self.previous_members = curr_map
        self._save_to_cache("memberList", curr_map)

        # 4: Rank changes
        role_changes = [(u, curr_map[u]['name'], prev_map[u]['rank'], curr_map[u]['rank'])
                        for u in curr_map if u in prev_map and prev_map[u].get('rank')!=curr_map[u]['rank']]
        if role_changes and not (self.cold_start and not self.member_list_exists):
            ch = self.client.get_channel(LOG_CHANNEL)
            er = discord.Embed(title='Guild Rank Changes', timestamp=now, color=0x0000FF)
            for _,name,old,new in role_changes:
                er.add_field(name=discord.utils.escape_markdown(name),value=f"{old} → {new}",inline=False)
            await ch.send(embed=er)

        # 5: Update presence
        await self.client.change_presence(activity=discord.CustomActivity(name=f"{guild.online} members online"))

        # 6: Fetch individual raid data
        results=[]
        for m in guild.all_members:
            async with self._semaphore:
                cutoff_ts=datetime.datetime.now(timezone.utc)-timedelta(minutes=1)
                while self.request_times and self.request_times[0]<cutoff_ts:
                    self.request_times.popleft()
                if len(self.request_times)>=RATE_LIMIT:
                    wait=(self.request_times[0]+timedelta(minutes=1)-datetime.datetime.now(timezone.utc)).total_seconds()
                    log(INFO, f"Rate limit reached, sleeping {wait:.1f}s", context="update_member_data")
                    await asyncio.sleep(wait)
                self.request_times.append(datetime.datetime.now(timezone.utc))
                res=await asyncio.to_thread(getPlayerDatav3,m['uuid'])
            results.append(res)

        prev = self.previous_data

        # --- 7: Build new snapshot (carry forward) & detect unvalidated only with fresh data ---
        new_data = dict(prev)      # carry forward last-known values for members we didn't fetch
        fresh = set()              # UUIDs we successfully fetched this iteration
        name_map = {}              # uuid -> username, built from fresh API results

        for m in results:
            if not isinstance(m, dict):
                continue

            uid, uname = m['uuid'], m['username']
            fresh.add(uid)
            name_map[uid] = uname

            raids = m.get('globalData', {}).get('raids', {}).get('list', {})
            counts = {r: raids.get(r, 0) for r in self.RAID_NAMES}

            # Use current contrib if present; otherwise fall back to last-known (carry-forward)
            carried_contrib = new_data.get(uid, {}).get('contributed', 0)
            contributed = contrib_map.get(uid, carried_contrib)

            new_data[uid] = {
                'raids': counts,
                'contributed': contributed
            }

            # Only detect “unvalidated” if:
            #  - we have fresh data for this UID (we do),
            #  - we are not on cold start,
            #  - AND we have a previous baseline for this UID.
            if not self.cold_start and uid in prev:
                old_counts = prev.get(uid, {}).get('raids', {r: 0 for r in self.RAID_NAMES})
                for raid in self.RAID_NAMES:
                    diff = counts[raid] - old_counts.get(raid, 0)
                    if (
                        0 < diff < 3 and
                        uid not in self.raid_participants[raid]['unvalidated'] and
                        uid not in self.raid_participants[raid]['validated']
                    ):
                        self.raid_participants[raid]['unvalidated'][uid] = {
                            'name': uname,
                            'first_seen': now,
                            'baseline_contrib': prev.get(uid, {}).get('contributed', 0)
                        }
                        log(INFO, f"DETECT unvalidated {uname} in {raid}", context="update_member_data")

        # --- 8: XP jumps (only consider fresh data + existing baseline) ---
        xp_jumps = set()
        for uid in fresh:
            if uid not in prev:   # no baseline => skip
                continue
            old_c = prev[uid].get('contributed', 0)
            new_c = new_data[uid].get('contributed', 0)
            if new_c - old_c >= CONTRIBUTION_THRESHOLD:
                xp_jumps.add(uid)
                log(INFO, f"XP threshold met for {uid} (diff: {new_c-old_c} >= {CONTRIBUTION_THRESHOLD})", context="update_member_data")

        # --- 9: Validate via XP jump ---
        for raid, queues in self.raid_participants.items():
            for uid in list(queues['unvalidated']):
                if uid in xp_jumps:
                    info = queues['unvalidated'].pop(uid)
                    queues['validated'][uid] = info
                    log(INFO, f"VALIDATED {info['name']} for {raid} via XP jump", context="update_member_data")

        # --- 9b: Cross-validate — if a player entered unvalidated for a raid
        #     but was already in xp_only from a previous tick, validate immediately ---
        for raid, queues in self.raid_participants.items():
            for uid in list(queues['unvalidated']):
                if uid in self.xp_only_validated:
                    info = queues['unvalidated'].pop(uid)
                    queues['validated'][uid] = info
                    self.xp_only_validated.pop(uid)
                    log(INFO, f"CROSS-VALIDATED {info['name']} for {raid} (was in xp_only pool)", context="update_member_data")

        # --- 10: Validate via contrib diff (only if we have fresh data this tick) ---
        for raid, queues in self.raid_participants.items():
            for uid, info in list(queues['unvalidated'].items()):
                if uid not in fresh:
                    # No fresh data for this UID this tick—don’t try to validate
                    log(INFO, f"SKIP contrib validation for {uid}: no fresh data", context="update_member_data")
                    continue
                base = info['baseline_contrib']
                curr = new_data.get(uid, {}).get('contributed', 0)
                if curr - base >= CONTRIBUTION_THRESHOLD:
                    queues['validated'][uid] = info
                    queues['unvalidated'].pop(uid)
                    log(INFO, f"VALIDATED {info['name']} for {raid} (contrib diff: {curr-base} >= {CONTRIBUTION_THRESHOLD})", context="update_member_data")

        # --- 10b: Add XP-jump players with no raid detection to xp_only pool ---
        for uid in xp_jumps:
            in_any_raid_queue = any(
                uid in self.raid_participants[r]['unvalidated'] or uid in self.raid_participants[r]['validated']
                for r in self.RAID_NAMES
            )
            if not in_any_raid_queue and uid not in self.xp_only_validated:
                uname = name_map.get(uid, uid)
                self.xp_only_validated[uid] = {
                    "name": uname,
                    "first_seen": now
                }
                log(INFO, f"XP-ONLY validated {uname} (no raid type detected)", context="update_member_data")

        # --- 11: Announce raids (with xp_only backfill) ---
        for raid in self.RAID_NAMES:
            vals = self.raid_participants[raid]['validated']
            raid_validated_count = len(vals)

            if raid_validated_count == 0:
                continue

            needed = 4 - raid_validated_count
            if needed <= 0:
                # 4+ raid-specific validated — form group normally
                group = set(list(vals)[:4])
                await self._announce_raid(raid, group, guild)
                for uid in group:
                    vals.pop(uid)
            elif len(self.xp_only_validated) >= needed:
                # 1-3 raid-specific validated + enough xp_only to fill to 4
                xp_only_uids = list(self.xp_only_validated.keys())[:needed]
                for uid in xp_only_uids:
                    info = self.xp_only_validated.pop(uid)
                    vals[uid] = info
                    log(INFO, f"BACKFILL {info['name']} from xp_only pool into {raid}", context="update_member_data")

                group = set(list(vals)[:4])
                await self._announce_raid(raid, group, guild)
                for uid in group:
                    vals.pop(uid)

        # --- 11b: All-private group (no raid type detected for anyone) ---
        # Grace period: only announce xp_only players from previous ticks,
        # giving raid-specific detection a chance to catch up next iteration.
        eligible_xp_only = {uid: info for uid, info in self.xp_only_validated.items()
                           if info['first_seen'] < now}
        while len(eligible_xp_only) >= 4:
            xp_only_uids = list(eligible_xp_only.keys())[:4]
            group = set(xp_only_uids)
            participant_names = {uid: self.xp_only_validated[uid]["name"] for uid in xp_only_uids}
            for uid in xp_only_uids:
                self.xp_only_validated.pop(uid)
                eligible_xp_only.pop(uid)
            log(INFO, f"ALL-PRIVATE group formed: {participant_names}", context="update_member_data")
            await self._announce_raid(None, group, guild, participant_names=participant_names)

        # --- 12: Persist (unchanged, but now includes carry-forward) ---
        self.previous_data = new_data
        self._save_to_cache("previousMemberData", new_data)
        self.cold_start = False

        # Build pf_map ONLY from fresh results for the current snapshot write
        pf_map = {m['uuid']: m for m in results if isinstance(m, dict)}
        rank_map = {u: info.get('rank') for u, info in curr_map.items()}
        online_map = {m['uuid']: m.get('online', False) for m in guild.all_members}
        await asyncio.to_thread(
            _write_current_snapshot_sync,
            contrib_map, rank_map, pf_map, online_map
        )
        
        log(INFO, "ENDING LOOP", context="update_member_data")

    async def _auto_register_joined_member(self, uuid: str, ign: str):
        """Check if a joined member has a pending accepted application and auto-register them."""

        def _check_pending_app(uuid_str):
            db = DB()
            try:
                db.connect()
                db.cursor.execute(
                    """SELECT dl.discord_id, dl.app_channel
                       FROM discord_links dl
                       WHERE dl.uuid = %s
                         AND dl.linked = FALSE
                         AND dl.app_channel IS NOT NULL""",
                    (uuid_str,)
                )
                return db.cursor.fetchone()
            finally:
                db.close()

        row = await asyncio.to_thread(_check_pending_app, uuid)
        if not row:
            return  # No pending application for this UUID

        discord_id, app_channel_id = row

        discord_guild = self.client.get_guild(TAQ_GUILD_ID)
        if not discord_guild:
            return

        member = discord_guild.get_member(discord_id)
        if not member:
            return

        # Fetch player stats for wars_on_join
        pdata = await asyncio.to_thread(BasicPlayerStats, ign)
        wars_on_join = 0
        if not pdata.error:
            wars_on_join = pdata.wars

        # Determine starting rank based on existing Discord roles
        starting_rank = determine_starting_rank(member)
        rank_roles = discord_ranks[starting_rank]['roles']

        # Assign roles (mirrors NewMember modal from Helpers/classes.py)
        to_add = [
            'Member', 'The Aquarium [TAq]', *rank_roles,
            '\U0001F947 RANKS\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800',
            '\U0001F6E0\uFE0F PROFESSIONS\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800',
            '\u2728 COSMETIC ROLES\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800',
            'CONTRIBUTION ROLES\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800',
        ]
        to_remove = ['Land Crab', 'Honored Fish', 'Retired Chief', 'Ex-Member']

        all_roles = discord_guild.roles
        roles_to_add = [discord.utils.get(all_roles, name=r) for r in to_add]
        roles_to_add = [r for r in roles_to_add if r is not None]
        roles_to_remove = [discord.utils.get(all_roles, name=r) for r in to_remove]
        roles_to_remove = [r for r in roles_to_remove if r is not None]

        try:
            if roles_to_add:
                await member.add_roles(*roles_to_add, reason="Auto-registration from accepted application")
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="Auto-registration from accepted application")
            await member.edit(nick=f"{starting_rank} {ign}")
        except discord.Forbidden:
            log(ERROR, f"Missing permissions to modify roles/nick for {member.name}", context="auto_register")
            return
        except Exception as e:
            log(ERROR, f"Error modifying {member.name}: {e}", context="auto_register")
            return

        # Update discord_links: mark as linked, set rank
        def _complete_registration(did, ign_val, uuid_str, wars, rank):
            db = DB()
            try:
                db.connect()
                db.cursor.execute(
                    """UPDATE discord_links
                       SET linked = TRUE, rank = %s, ign = %s, wars_on_join = %s
                       WHERE discord_id = %s""",
                    (rank, ign_val, wars, did)
                )
                # Clear guild_leave_pending if this user had a pending-leave application
                db.cursor.execute(
                    """UPDATE new_app SET guild_leave_pending = FALSE
                       WHERE applicant_discord_id = %s AND guild_leave_pending = TRUE""",
                    (did,)
                )
                # Also clear for website applications
                db.cursor.execute(
                    """UPDATE applications SET guild_leave_pending = FALSE
                       WHERE discord_id = %s::TEXT AND guild_leave_pending = TRUE""",
                    (did,)
                )
                db.connection.commit()
            finally:
                db.close()

        await asyncio.to_thread(_complete_registration, discord_id, ign, uuid, wars_on_join, starting_rank)

        # Update poll embed (try legacy new_app first, then website applications)
        if app_channel_id:
            await update_poll_embed(self.client, app_channel_id, ":orange_circle: Registered", 0xFFE019)
            await update_web_poll_embed(self.client, app_channel_id, ":orange_circle: Registered", 0xFFE019)

        # Send welcome embed
        welcome_ch = self.client.get_channel(WELCOME_CHANNEL_ID)
        if welcome_ch:
            welcome_embed = discord.Embed(
                description=f":ocean: Dive right in, {member.mention}! The water's fine.",
                color=discord.Color.blue()
            )
            welcome_embed.set_author(name="Welcome Aboard!", icon_url=member.display_avatar.url)
            await welcome_ch.send(embed=welcome_embed)

        log(INFO, f"Successfully registered {ign} ({member.name}) from accepted application.", context="auto_register")

    async def _run_snapshot(self, target_date=None):
        """
        Core snapshot logic. Fetches all guild members and writes to player_activity table.
        Uses UPSERT (ON CONFLICT DO UPDATE) for deduplication - safe to run multiple times.

        Args:
            target_date: The date to use for the snapshot. Defaults to today UTC.

        Returns:
            Tuple of (success: bool, members_written: int, total_members: int)
        """
        if target_date is None:
            target_date = datetime.datetime.now(timezone.utc).date()

        log(INFO, f"Running snapshot for date: {target_date}", context="update_member_data")

        db = DB()
        db.connect()
        guild = Guild("The Aquarium")
        snap = {'time': int(time.time()), 'members': []}

        total_members = len(guild.all_members)

        # 1: build contrib and rank maps from recent guild state
        contrib_map = {m['uuid']: m.get('contributed', 0) for m in guild.all_members}
        rank_map    = {m['uuid']: m.get('rank')       for m in guild.all_members}

        # 2: snapshot each member currently in guild (with retry logic for rate limits)
        pending_members = list(guild.all_members)
        max_retries = 3
        retry_delay = 30  # seconds

        for attempt in range(max_retries + 1):
            failed_members = []

            for m in pending_members:
                uuid = m['uuid']
                username = m['name']

                async with self._semaphore:
                    pf = await asyncio.to_thread(getPlayerDatav3, uuid)
                if not isinstance(pf, dict):
                    failed_members.append(m)
                    continue

                # shells
                db.cursor.execute(
                    "SELECT COALESCE(s.shells, 0) "
                    "FROM discord_links dl "
                    "LEFT JOIN shells s ON dl.discord_id = s.user "
                    "WHERE dl.uuid = %s",
                    (uuid,)
                )
                row = db.cursor.fetchone()
                sh = row[0] if row else 0

                # raids
                db.cursor.execute(
                    "SELECT COALESCE(ur.uncollected_raids, 0) + COALESCE(ur.collected_raids, 0) "
                    "FROM discord_links dl "
                    "LEFT JOIN uncollected_raids ur ON dl.uuid = ur.uuid "
                    "WHERE dl.uuid = %s",
                    (uuid,)
                )
                row = db.cursor.fetchone()
                rd = row[0] if row else 0

                snap['members'].append({
                    'name': username,
                    'uuid': uuid,
                    'rank': rank_map.get(uuid),
                    'playtime': pf.get('playtime'),
                    'contributed': contrib_map.get(uuid),
                    'wars': pf.get('globalData', {}).get('wars'),
                    'shells': sh,
                    'raids': rd
                })

            # If no failures or we've exhausted retries, break
            if not failed_members or attempt >= max_retries:
                if failed_members:
                    failed_names = [m['name'] for m in failed_members]
                    log(ERROR, f"Final failures after {max_retries} retries: {', '.join(failed_names)}", context="update_member_data")
                break

            # Wait and retry failed members
            failed_names = [m['name'] for m in failed_members]
            log(INFO, f"{len(failed_members)} failed fetches: {', '.join(failed_names)}", context="update_member_data")
            log(INFO, f"Waiting {retry_delay}s before retry {attempt + 1}/{max_retries}...", context="update_member_data")
            await asyncio.sleep(retry_delay)
            pending_members = failed_members

        failed_fetches = len(failed_members) if failed_members else 0

        # 3: write to player_activity database table (uses UPSERT for deduplication)
        try:
            db_rows_written = 0
            for member in snap['members']:
                uuid = member.get('uuid')
                playtime = member.get('playtime') or 0
                contributed = member.get('contributed') or 0
                wars = member.get('wars') or 0
                raids = member.get('raids') or 0
                shells = member.get('shells') or 0
                if uuid:
                    db.cursor.execute("""
                        INSERT INTO player_activity (uuid, playtime, contributed, wars, raids, shells, snapshot_date)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (uuid, snapshot_date)
                        DO UPDATE SET
                            playtime = EXCLUDED.playtime,
                            contributed = EXCLUDED.contributed,
                            wars = EXCLUDED.wars,
                            raids = EXCLUDED.raids,
                            shells = EXCLUDED.shells
                    """, (uuid, playtime, contributed, wars, raids, shells, target_date))
                    db_rows_written += 1
            db.connection.commit()
            private_profiles = sum(1 for m in snap['members'] if m.get('playtime') is None)
            log(INFO, f"Snapshot written to DB ({db_rows_written} rows, {failed_fetches} failed API, {private_profiles} private profiles)", context="update_member_data")
            db.close()
            return (True, db_rows_written, total_members, failed_fetches, private_profiles)
        except Exception as e:
            log(ERROR, f"Failed to write to DB: {e}", context="update_member_data")
            traceback.print_exc()
            db.close()
            return (False, 0, total_members, failed_fetches, 0)

    @tasks.loop(time=dtime(hour=0, minute=1, tzinfo=timezone.utc))
    async def daily_activity_snapshot(self):
        log(INFO, "Starting daily activity snapshot", context="update_member_data")

        # --- Guard: ensure only one snapshot per UTC day (check database) ---
        today_utc = datetime.datetime.now(timezone.utc).date()
        try:
            db_check = DB()
            db_check.connect()
            db_check.cursor.execute("""
                SELECT COUNT(*) FROM player_activity
                WHERE snapshot_date = %s
            """, (today_utc,))
            existing_count = db_check.cursor.fetchone()[0]
            db_check.close()
            if existing_count > 0:
                log(INFO, f"Daily snapshot already exists for today ({existing_count} rows); skipping.", context="update_member_data")
                return
        except Exception:
            traceback.print_exc()
            # If the check fails, proceed to write a fresh snapshot below.

        success, written, total, failed_api, private = await self._run_snapshot(today_utc)
        if success:
            log(INFO, f"Daily activity snapshot complete ({written}/{total} members, {failed_api} failed API, {private} private)", context="update_member_data")
        else:
            log(ERROR, "Daily activity snapshot failed", context="update_member_data")

    @slash_command(name="force_snapshot", description="Force retry the daily activity snapshot (admin only)", guild_ids=ALL_GUILD_IDS)
    @default_permissions(administrator=True)
    async def force_snapshot(self, ctx: discord.ApplicationContext):
        """
        Manually trigger a snapshot retry. Uses UPSERT so it's safe to run multiple times -
        existing entries for today will be updated, not duplicated.
        """
        await ctx.defer(ephemeral=True)

        today_utc = datetime.datetime.now(timezone.utc).date()

        # Check current state before running
        try:
            db_check = DB()
            db_check.connect()
            db_check.cursor.execute("""
                SELECT COUNT(*) FROM player_activity
                WHERE snapshot_date = %s
            """, (today_utc,))
            existing_count = db_check.cursor.fetchone()[0]
            db_check.close()
        except Exception:
            existing_count = 0

        await ctx.respond(
            f"Starting forced snapshot for {today_utc}...\n"
            f"Current entries for today: {existing_count}\n"
            f"This may take a minute.",
            ephemeral=True
        )

        success, written, total, failed_api, private = await self._run_snapshot(today_utc)

        # Check new state after running
        try:
            db_check = DB()
            db_check.connect()
            db_check.cursor.execute("""
                SELECT COUNT(*) FROM player_activity
                WHERE snapshot_date = %s
            """, (today_utc,))
            new_count = db_check.cursor.fetchone()[0]
            db_check.close()
        except Exception:
            new_count = written

        if success:
            await ctx.followup.send(
                f"✅ Snapshot complete!\n"
                f"• Members in guild: {total}\n"
                f"• Successfully written: {written}\n"
                f"• Failed API fetches: {failed_api}\n"
                f"• Private profiles (null playtime): {private}\n"
                f"• Total entries for {today_utc}: {new_count}",
                ephemeral=True
            )
        else:
            await ctx.followup.send(
                f"❌ Snapshot failed. Check logs for details.",
                ephemeral=True
            )
    
    @update_member_data.before_loop
    async def before_update(self):
        await self.client.wait_until_ready()

    @daily_activity_snapshot.before_loop
    async def before_snapshot(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if self._has_started: return
        self._has_started=True
        if not self.update_member_data.is_running(): self.update_member_data.start()
        if not self.daily_activity_snapshot.is_running(): self.daily_activity_snapshot.start()

    @update_member_data.error
    async def on_update_member_data_error(self, error):
        log(ERROR, "update_member_data loop raised an exception", context="update_member_data")
        traceback.print_exc()
        # restart the loop after a short pause
        await asyncio.sleep(5)
        if not self.update_member_data.is_running():
            self.update_member_data.start()


def setup(client):
    client.add_cog(UpdateMemberData(client))
