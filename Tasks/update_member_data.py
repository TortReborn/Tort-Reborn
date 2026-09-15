import asyncio
import io
import json
import os
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

from Helpers.logger import log, INFO, WARN, ERROR
from Helpers.classes import Guild, DB, BasicPlayerStats
from Helpers.database import get_current_guild_data_with_db, write_hourly_activity_snapshot_with_db, prune_hourly_activity_snapshots_with_db
from Helpers.embed_updater import update_web_poll_embed
from Helpers.functions import getPlayerDatav3, getNameFromUUID, getPlayerUUID, determine_starting_rank, create_progress_bar, addLine, round_corners
from Helpers import roster as roster_db
from Helpers.leave_prompts import post_leave_prompts
from Helpers.links import LinkConflictError
from Helpers.member_roles import registration_role_names
from Helpers.registration import record_registration
from Helpers.playtime_daily import refresh_playtime_daily
from Helpers.variables import (
    RAID_LOG_CHANNEL_ID,
    BOT_LOG_CHANNEL_ID,
    GUILD_LOG_CHANNEL_ID,
    ERROR_CHANNEL_ID,
    NOTG_EMOJI,
    TCC_EMOJI,
    TNA_EMOJI,
    NOL_EMOJI,
    TWP_EMOJI,
    ASPECT_EMOJI,
    ALL_GUILD_IDS,
    TAQ_GUILD_ID,
    WELCOME_CHANNEL_ID,
    ANNOUNCEMENT_CHANNEL_ID,
    FAQ_CHANNEL_ID,
    GUILD_BANK_CHANNEL_ID,
    RANK_UP_CHANNEL_ID,
    TAQ_ROLES_CHANNEL_ID,
    BOT_COMMAND_CHANNEL_ID,
    RAID_COLLECTING_CHANNEL_ID,
    discord_ranks,
)

RAID_ANNOUNCE_CHANNEL_ID = RAID_LOG_CHANNEL_ID
LOG_CHANNEL = BOT_LOG_CHANNEL_ID
GUILD_LOG = GUILD_LOG_CHANNEL_ID
GUILD_TTL = timedelta(minutes=10)
CONTRIBUTION_THRESHOLD = 2_500_000_000
RATE_LIMIT = 100  # max calls per minute
RAID_DETECTION_ENABLED = False

EMBED_FIELD_CAP = 25  # Discord rejects embeds with more fields (error 50035)


def _build_rank_change_embeds(role_changes, now):
    """Chunk rank changes into embeds of at most EMBED_FIELD_CAP fields.

    A promotion wave or cold-start diff can exceed 25 changes; a single
    oversized embed 400s on send and kills the loop (observed in dev with a
    30-change diff)."""
    embeds = []
    for start in range(0, len(role_changes), EMBED_FIELD_CAP):
        er = discord.Embed(title='Guild Rank Changes', timestamp=now, color=0x0000FF)
        for _, name, old, new in role_changes[start:start + EMBED_FIELD_CAP]:
            er.add_field(name=discord.utils.escape_markdown(name), value=f"{old} → {new}", inline=False)
        embeds.append(er)
    return embeds

RAID_EMOJIS = {
    "Nest of the Grootslangs": NOTG_EMOJI,
    "The Canyon Colossus": TCC_EMOJI,
    "The Nameless Anomaly": TNA_EMOJI,
    "Orphion's Nexus of Light": NOL_EMOJI,
    "The Wartorn Palace": TWP_EMOJI,
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

def _refresh_playtime_daily_sync():
    """Re-derive playtime_daily right after the snapshot that feeds it.

    Without this the derived table only advances when someone runs the backfill
    script by hand, so every daily-sourced chart quietly stops moving while the
    snapshots keep arriving — a stall that looks like inactivity rather than a
    broken pipeline. Failure is logged and swallowed: the snapshot itself has
    already been written, and the next night's run recomputes from scratch.
    """
    db = None
    try:
        db = _db_connect_with_retry()
        rows, days = refresh_playtime_daily(db)
        log(INFO, f"playtime_daily refreshed: {rows} rows across {days} days",
            context="update_member_data")
    except Exception as e:
        log(ERROR, f"Failed to refresh playtime_daily: {e}", context="update_member_data")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def _floor_to_presence_bucket(dt):
    """Floor a UTC datetime to the start of its 15-minute presence bucket."""
    return dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)


def _write_counter_sample_sync(members):
    """Keep the last cumulative playtime/wars/raids reading for the current hour.

    The loop has already fetched all three for the guildData cache, so this is
    free. Storing them per hour is what makes hour-of-day activity derivable:
    the daily snapshot in player_activity can only ever say how much happened
    on a date, never when during it.

    Counters are monotonic, so GREATEST keeps a carried-forward or out-of-order
    reading from walking a value backwards. Members whose fetch failed and had
    no cached value are skipped rather than written as zero.
    """
    now = datetime.datetime.now(timezone.utc)
    hour = now.replace(minute=0, second=0, microsecond=0)

    uuids, playtimes, wars, raids = [], [], [], []
    for m in members:
        if not m.get('uuid') or m.get('playtime') is None:
            continue
        uuids.append(m['uuid'])
        playtimes.append(float(m['playtime']))
        wars.append(int(m.get('wars') or 0))
        raids.append(int(m.get('raids') or 0))

    if not uuids:
        log(WARN, "No usable member counters this tick — skipping", context="update_member_data")
        return

    db = None
    try:
        db = _db_connect_with_retry()
        db.cursor.execute("""
            INSERT INTO member_counters_hourly (uuid, hour, playtime, wars, raids)
            SELECT m.uuid, %s, m.playtime, m.wars, m.raids
            FROM unnest(%s::uuid[], %s::real[], %s::int[], %s::int[])
                 AS m(uuid, playtime, wars, raids)
            ON CONFLICT (uuid, hour) DO UPDATE SET
                playtime = GREATEST(member_counters_hourly.playtime, EXCLUDED.playtime),
                wars     = GREATEST(member_counters_hourly.wars,     EXCLUDED.wars),
                raids    = GREATEST(member_counters_hourly.raids,    EXCLUDED.raids)
        """, (hour, uuids, playtimes, wars, raids))
        db.connection.commit()
    except Exception as e:
        log(ERROR, f"Failed to write counter sample: {e}", context="update_member_data")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def _write_presence_sample_sync(online_map, member_count, guild_online):
    """Record who is online at this tick into the 15-minute presence buckets.

    Reads the online_map the loop already builds from the guild fetch, so it
    costs no extra Wynncraft API calls. Presence history is the only source
    for hour-of-day activity — player_activity is daily-cumulative and cannot
    be re-derived at a finer grain after the fact, so this runs on every tick.

    guild_online is the endpoint's own top-level count and is the one to trust
    for "how many were on": members with restrictions.online_status set are
    included there but have their per-member flag reported false, so they are
    invisible to online_map and can never land in presence_buckets. Both
    numbers are stored so the unattributable remainder stays visible instead of
    quietly deflating every guild-wide chart.

    Takes its own connection and swallows its own errors: this is nice-to-have
    telemetry and must never take down the guildData cache write it follows.
    """
    now = datetime.datetime.now(timezone.utc).replace(microsecond=0)
    bucket_start = _floor_to_presence_bucket(now)
    online_uuids = [uuid for uuid, is_online in online_map.items() if is_online]

    # Fall back to the attributable count only if the guild payload lacked the
    # field; never let a missing value record as zero members online.
    authoritative = guild_online if isinstance(guild_online, int) else len(online_uuids)

    db = None
    try:
        db = _db_connect_with_retry()

        db.cursor.execute("SELECT MAX(tick_at) FROM presence_ticks")
        row = db.cursor.fetchone()
        last_tick = row[0] if row else None
        gap = round((now - last_tick).total_seconds()) if last_tick else None

        # The tick row is the dedup key for the whole sample: if this exact
        # second was already recorded (loop restart racing its predecessor),
        # its members were already counted and re-incrementing would inflate
        # the bucket above the ticks that back it.
        db.cursor.execute("""
            INSERT INTO presence_ticks
                (tick_at, online_count, member_count, gap_seconds, attributed_count)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (tick_at) DO NOTHING
            RETURNING tick_at
        """, (now, authoritative, member_count, gap, len(online_uuids)))
        if db.cursor.fetchone() is None:
            log(WARN, f"Duplicate presence tick at {now.isoformat()} — skipping", context="update_member_data")
            return

        if online_uuids:
            db.cursor.execute("""
                INSERT INTO presence_buckets (uuid, bucket_start, samples)
                SELECT m.uuid, %s, 1
                FROM unnest(%s::uuid[]) AS m(uuid)
                ON CONFLICT (uuid, bucket_start)
                DO UPDATE SET samples = presence_buckets.samples + 1
            """, (bucket_start, online_uuids))

        db.connection.commit()

    except Exception as e:
        log(ERROR, f"Failed to write presence sample: {e}", context="update_member_data")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


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
        # find active event (may be None — raids happen outside events too)
        cur.execute("SELECT id FROM graid_events WHERE active = TRUE LIMIT 1")
        row = cur.fetchone()
        event_id = row[0] if row else None

        # always log individual raid completion with type and participants
        cur.execute(
            "INSERT INTO graid_logs (event_id, raid_type) VALUES (%s, %s) RETURNING id",
            (event_id, raid_name)
        )
        log_id = cur.fetchone()[0]

        for uid in uuid_list:
            cur.execute("SELECT ign FROM discord_links WHERE uuid = %s", (uid,))
            ign_row = cur.fetchone()
            ign = ign_row[0] if ign_row else None
            cur.execute(
                "INSERT INTO graid_log_participants (log_id, uuid, ign) VALUES (%s, %s, %s)",
                (log_id, uid, ign)
            )

        # upsert totals only if there's an active event
        if event_id is not None:
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


def _graid_log_manual_sync(participants, raid_type):
    """
    Apply a manually-submitted raid log atomically.
      participants: list of dicts {uuid: str|None, ign: str}
      raid_type:    full raid name, or None for unknown
    Mirrors the combined effect of _upsert_raid_group_sync and _graid_increment_group_sync,
    but tolerates IGN-only participants (no Discord link). Players without a UUID
    appear in graid_log_participants but are skipped for graid_event_totals and
    uncollected_raids — same rule as the auto-detection path.
    Returns the new graid_logs.id.
    """
    if not participants:
        return None
    db = _db_connect_with_retry()
    try:
        cur = db.cursor

        cur.execute("SELECT id FROM graid_events WHERE active = TRUE LIMIT 1")
        row = cur.fetchone()
        event_id = row[0] if row else None

        cur.execute(
            "INSERT INTO graid_logs (event_id, raid_type) VALUES (%s, %s) RETURNING id",
            (event_id, raid_type)
        )
        log_id = cur.fetchone()[0]

        for p in participants:
            cur.execute(
                "INSERT INTO graid_log_participants (log_id, uuid, ign) VALUES (%s, %s, %s)",
                (log_id, p.get('uuid'), p.get('ign'))
            )

        if event_id is not None:
            for p in participants:
                if p.get('uuid'):
                    cur.execute("""
                        INSERT INTO graid_event_totals (event_id, uuid, total)
                        VALUES (%s, %s, 1)
                        ON CONFLICT (event_id, uuid) DO UPDATE
                          SET total = graid_event_totals.total + 1,
                              last_updated = NOW()
                    """, (event_id, p['uuid']))

        for p in participants:
            if p.get('uuid'):
                cur.execute("""
                    INSERT INTO uncollected_raids AS ur (uuid, ign, uncollected_raids, collected_raids)
                    VALUES (%s, %s, 1, 0)
                    ON CONFLICT (uuid) DO UPDATE
                      SET uncollected_raids = ur.uncollected_raids + 1,
                          ign               = EXCLUDED.ign
                """, (p['uuid'], p.get('ign')))

        db.connection.commit()
        return log_id
    finally:
        db.close()


def _write_current_snapshot_sync(contrib_map, rank_map, pf_map, online_map, guild_members,
                                 guild_online=None):
    snap = {'time': int(time.time()), 'members': []}

    if not guild_members:
        log(ERROR, "No guild members from API — skipping cache write to avoid data loss", context="update_member_data")
        return

    # Load previous cache to carry forward stats for members whose individual fetch failed
    prev_members_by_uuid = {}
    try:
        db = _db_connect_with_retry()
        db.cursor.execute("SELECT data FROM cache_entries WHERE cache_key = 'guildData'")
        row = db.cursor.fetchone()
        db.close()
        if row and row[0]:
            prev_data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            for m in prev_data.get('members', []):
                if m.get('uuid'):
                    prev_members_by_uuid[m['uuid']] = m
    except Exception as e:
        log(ERROR, f"Failed to load previous cache for carry-forward: {e}", context="update_member_data")
        try:
            if 'db' in locals():
                db.close()
        except:
            pass

    # Fetch shells/raids stats for all guild members (not just pf_map)
    all_uuids = [m['uuid'] for m in guild_members if m.get('uuid')]
    stats_by_uuid = {}
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
        db.cursor.execute(sql, (all_uuids,))
        rows = db.cursor.fetchall()
        stats_by_uuid = {row[0]: {'shells': row[1], 'raids': row[2]} for row in rows}
    finally:
        db.close()

    # Iterate over ALL guild members from the guild API (authoritative member list)
    for gm in guild_members:
        uuid = gm.get('uuid')
        if not uuid:
            continue

        pf = pf_map.get(uuid)        # individual player stats (may be None if fetch failed)
        prev = prev_members_by_uuid.get(uuid, {})  # previous cache entry for carry-forward
        entry = stats_by_uuid.get(uuid, {'shells': 0, 'raids': 0})

        if pf and isinstance(pf, dict):
            # Fresh data available — use it
            username  = pf.get('username') or pf.get('name') or gm.get('name')
            last_join = pf.get('lastJoin', prev.get('lastJoin', "2020-03-22T11:11:17.810000Z"))
            playtime  = pf.get('playtime')
            wars      = pf.get('globalData', {}).get('wars')
        else:
            # Fetch failed — carry forward previous values, fall back to guild API data
            username  = gm.get('name') or prev.get('name')
            last_join = prev.get('lastJoin', "2020-03-22T11:11:17.810000Z")
            playtime  = prev.get('playtime')
            wars      = prev.get('wars')

        snap['members'].append({
            'name':        username,
            'uuid':        uuid,
            'rank':        rank_map.get(uuid) or gm.get('rank'),
            'playtime':    playtime,
            'contributed': contrib_map.get(uuid) or gm.get('contributed'),
            'wars':        wars,
            'shells':      entry['shells'],
            'raids':       entry['raids'],
            'lastJoin':    last_join,
            'joined':      gm.get('joined'),
        })

    # Create cache version with online status
    cache_snap = {'time': snap['time'], 'members': []}
    for member in snap['members']:
        cache_member = dict(member)
        cache_member['online'] = online_map.get(member['uuid'], False)
        cache_snap['members'].append(cache_member)

    fetched = len(pf_map)
    total = len(guild_members)
    if fetched < total:
        log(INFO, f"Cache write: {fetched}/{total} members had fresh data, {total - fetched} carried forward", context="update_member_data")

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
        try:
            if 'db' in locals():
                db.close()
        except:
            pass

    # Independent of the cache write above: a failed cache write still means
    # the guild fetch succeeded, so the samples are still valid.
    _write_presence_sample_sync(online_map, len(guild_members), guild_online)
    _write_counter_sample_sync(snap['members'])


class UpdateMemberData(commands.Cog):
    RAID_NAMES = [
        "Nest of the Grootslangs",
        "The Canyon Colossus",
        "The Nameless Anomaly",
        "Orphion's Nexus of Light",
        "The Wartorn Palace"
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

    async def _post_raid_announcement(self, raid, names, guild):
        """Build and post the raid completion embed (with guild level progress image).
        Used by both auto-detection (_announce_raid) and the queue processor."""
        bolded = [f"**{discord.utils.escape_markdown(n)}**" for n in names]
        names_str = ", ".join(bolded[:-1]) + ", and " + bolded[-1] if len(bolded) > 1 else bolded[0]

        if raid:
            emoji = RAID_EMOJIS.get(raid, "")
            title = f"{emoji} {raid} Completed!"
        else:
            title = f"{ASPECT_EMOJI} Guild Raid Completed!"

        channel = self.client.get_channel(RAID_ANNOUNCE_CHANNEL_ID)
        if not channel:
            return

        embed = discord.Embed(
            title=title,
            description=names_str,
            color=0x00FF00
        )

        # Generate image-based progress bar
        guild_level = getattr(guild, "level", None) if guild else None
        guild_xp = getattr(guild, "xpPercent", None) if guild else None
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

    async def _announce_raid(self, raid, group, guild, participant_names=None):
        log(INFO, f"Announcing raid {raid}: {group}", context="update_member_data")
        if participant_names:
            names = [participant_names.get(uid, uid) for uid in group]
        else:
            participants = self.raid_participants[raid]["validated"]
            names = [participants[uid]["name"] for uid in group]

        await self._post_raid_announcement(raid, names, guild)

        await asyncio.to_thread(_upsert_raid_group_sync, list(group))
        await asyncio.to_thread(_graid_increment_group_sync, list(group), raid)

    async def _process_graid_queue(self, guild):
        """Drain pending manually-logged raids submitted via the website.
        Each row is run through the same DB + Discord-embed flow as auto-detection
        so the website doesn't have to duplicate that logic."""
        def _claim_pending():
            db = _db_connect_with_retry()
            try:
                db.cursor.execute("""
                    SELECT id, raid_type, announce, participants
                    FROM graid_log_queue
                    WHERE status = 'pending'
                    ORDER BY created_at
                    LIMIT 50
                """)
                return db.cursor.fetchall()
            finally:
                db.close()

        try:
            rows = await asyncio.to_thread(_claim_pending)
        except Exception as e:
            log(ERROR, f"queue claim failed: {e}", context="graid_queue")
            return

        if not rows:
            return

        log(INFO, f"Processing {len(rows)} queued graid log(s)", context="graid_queue")

        for queue_id, raid_type, announce, participants_data in rows:
            try:
                # JSONB columns come back as already-parsed objects from psycopg,
                # but tolerate the string case just in case.
                if isinstance(participants_data, str):
                    participants = json.loads(participants_data)
                else:
                    participants = participants_data or []

                if not participants:
                    raise ValueError("queue row has no participants")

                # A participant may be queued without a uuid (unlinked member,
                # no discord_links row yet). The Minecraft identity is
                # resolvable from the ign regardless — a NULL uuid row would be
                # invisible to event leaderboards and duplicate the player on
                # the raids leaderboard.
                for p in participants:
                    if not p.get('uuid'):
                        ign = p.get('ign')
                        pdata = await asyncio.to_thread(getPlayerUUID, ign) if ign else None
                        if pdata:
                            p['uuid'] = pdata[1]
                        else:
                            log(WARN, f"Could not resolve uuid for '{ign}' in queued graid log #{queue_id}; writing with NULL uuid", context="graid_queue")

                # 1. DB writes (graid_logs, participants, event totals, uncollected_raids).
                await asyncio.to_thread(_graid_log_manual_sync, participants, raid_type)

                # 2. Discord announcement — only when requested and the raid type
                # is known. Unknown-type raids are silent fix-ups for missed or
                # desynced raids, whatever the party size.
                if announce and raid_type:
                    names = [p['ign'] for p in participants]
                    await self._post_raid_announcement(raid_type, names, guild)

                # 3. Mark done.
                def _mark_done(qid):
                    db = _db_connect_with_retry()
                    try:
                        db.cursor.execute(
                            "UPDATE graid_log_queue SET status='done', processed_at=NOW() WHERE id=%s",
                            (qid,)
                        )
                        db.connection.commit()
                    finally:
                        db.close()
                await asyncio.to_thread(_mark_done, queue_id)
                log(INFO, f"Processed queued graid log #{queue_id} ({raid_type or 'Unknown'}, {'announced' if announce and raid_type else 'silent'})", context="graid_queue")

            except Exception as e:
                log(ERROR, f"Failed to process queued graid log #{queue_id}: {e}", context="graid_queue")
                traceback.print_exc()
                def _mark_error(qid, msg):
                    try:
                        db = _db_connect_with_retry()
                        try:
                            db.cursor.execute(
                                "UPDATE graid_log_queue SET status='error', error_message=%s, processed_at=NOW() WHERE id=%s",
                                (msg[:500], qid)
                            )
                            db.connection.commit()
                        finally:
                            db.close()
                    except Exception:
                        pass
                await asyncio.to_thread(_mark_error, queue_id, str(e))

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
        # Guild restriction: fetches Wynncraft API data for "The Aquarium" guild,
        # sends to home guild channels only (RAID_LOG_CHANNEL_ID, BOT_LOG_CHANNEL_ID, etc.)
        now = datetime.datetime.now(timezone.utc)
        log(INFO, f"STARTING LOOP — {now.strftime('%Y-%m-%d %H:%M:%S')} UTC", context="update_member_data")

        # fetch guild over HTTP off the event loop
        guild = await asyncio.to_thread(Guild, "The Aquarium", "WYNN_LOOP_TOKEN")

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
            guild_log_ch = self.client.get_channel(GUILD_LOG)
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
                add_chunked(ej, 'Joined', [curr_map[u]['name'] for u in joined])
                if ch:
                    await ch.send(embed=ej)
                if guild_log_ch:
                    await guild_log_ch.send(embed=ej)
                # Auto-register members who have pending accepted applications
                for uuid in joined:
                    try:
                        await self._auto_register_joined_member(uuid, curr_map[uuid]['name'])
                    except Exception as e:
                        log(ERROR, f"Error for {uuid}: {e}", context="auto_register")
            if left:
                el = discord.Embed(title='Guild Members Left', timestamp=now, color=0xFF0000)
                add_chunked(el, 'Left', [prev_map[u]['name'] for u in left])
                if ch:
                    await ch.send(embed=el)
                # The guild log gets one message per leaver with the reset /
                # honorific buttons (TAQ-76); large batches fall back to the list.
                if guild_log_ch:
                    try:
                        await post_leave_prompts(
                            self.client, guild_log_ch,
                            [(u, prev_map[u]['name'], prev_map[u].get('rank')) for u in left],
                            fallback_embed=el, now=now,
                        )
                    except Exception as e:
                        log(ERROR, f"Leave prompts failed: {e}", context="guild_log")
                        await guild_log_ch.send(embed=el)
                # Void any pending recruit credit for members who left before Piranha
                for uuid in left:
                    try:
                        from Helpers.recruiting import void_pending_recruit
                        ign = prev_map[uuid]['name']
                        await asyncio.to_thread(void_pending_recruit, ign, uuid)
                    except Exception as e:
                        log(ERROR, f"Recruit credit void for {uuid}: {e}", context="guild_log")
        self.previous_members = curr_map
        self._save_to_cache("memberList", curr_map)

        # 3a: guild_roster / membership_stints are the membership record for
        # the bot and the website; keep them in step with the API every cycle.
        try:
            await asyncio.to_thread(self._sync_roster_table, guild.all_members, now)
        except Exception as e:
            log(ERROR, f"Roster sync error: {e}", context="update_member_data")

        # 3b: Sync renames. The guild API name is authoritative; discord_links.ign
        # is only ever written at link time and otherwise goes stale forever,
        # which breaks every name-based lookup downstream (graid credit,
        # recruiter matching, website uuid resolution).
        try:
            renames = await asyncio.to_thread(self._sync_member_igns, curr_map)
            for rename in renames:
                log(INFO, f"Rename sync: {rename['old']} -> {rename['new']}", context="update_member_data")
            if renames:
                await self._apply_rename_nicknames(renames)
        except Exception as e:
            log(ERROR, f"Rename sync error: {e}", context="update_member_data")

        # 3c: Sweep for unlinked members who are already in the guild
        # Safety net for race conditions where a player joined before their app was accepted
        try:
            unlinked_rows = await asyncio.to_thread(self._fetch_unlinked_with_app)
            for ul_uuid, ul_ign in unlinked_rows:
                normalized = ul_uuid.replace('-', '')
                match_uuid = None
                for cm_uuid in curr_map:
                    if cm_uuid.replace('-', '') == normalized:
                        match_uuid = cm_uuid
                        break
                if match_uuid:
                    try:
                        await self._auto_register_joined_member(match_uuid, curr_map[match_uuid]['name'])
                    except Exception as e:
                        log(ERROR, f"Sweep registration error for {ul_ign}: {e}", context="auto_register_sweep")
        except Exception as e:
            log(ERROR, f"Unlinked member sweep error: {e}", context="auto_register_sweep")

        # 4: Rank changes
        role_changes = [(u, curr_map[u]['name'], prev_map[u]['rank'], curr_map[u]['rank'])
                        for u in curr_map if u in prev_map and prev_map[u].get('rank')!=curr_map[u]['rank']]
        if role_changes and not (self.cold_start and not self.member_list_exists):
            ch = self.client.get_channel(LOG_CHANNEL)
            guild_log_ch = self.client.get_channel(GUILD_LOG)
            for er in _build_rank_change_embeds(role_changes, now):
                if ch:
                    await ch.send(embed=er)
                if guild_log_ch:
                    await guild_log_ch.send(embed=er)

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
                    if wait > 5:
                        log(INFO, f"Rate limit reached, sleeping {wait:.1f}s", context="update_member_data")
                    await asyncio.sleep(wait)
                self.request_times.append(datetime.datetime.now(timezone.utc))
                res=await asyncio.to_thread(getPlayerDatav3,m['uuid'],"WYNN_LOOP_TOKEN")
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

            # Only detect "unvalidated" if:
            #  - we have fresh data for this UID (we do),
            #  - we are not on cold start,
            #  - AND we have a previous baseline for this UID.
            if RAID_DETECTION_ENABLED and not self.cold_start and uid in prev:
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

        if RAID_DETECTION_ENABLED:
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
                        # No fresh data for this UID this tick—don't try to validate
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

        # --- 11c: Drain manually-logged raids queued by the website ---
        # Runs here so we share the freshly-fetched `guild` object (for the
        # level/xp progress image in _post_raid_announcement).
        try:
            await self._process_graid_queue(guild)
        except Exception as e:
            log(ERROR, f"_process_graid_queue crashed: {e}", context="graid_queue")
            traceback.print_exc()

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
            contrib_map, rank_map, pf_map, online_map, guild.all_members, guild.online
        )
        
        end = datetime.datetime.now(timezone.utc)
        log(INFO, f"ENDING LOOP — {end.strftime('%Y-%m-%d %H:%M:%S')} UTC", context="update_member_data")

    async def _auto_register_joined_member(self, uuid: str, ign: str):
        """Check if a joined member has a pending accepted application and auto-register them."""

        def _check_pending_app(uuid_str):
            """The accepted guild application behind this uuid whose applicant
            has not been registered yet: no Discord rank on their identity
            row, and no stint since the application that has already ended or
            been through a removal (rank_at_leave is stamped by remove_member
            even while the stint is open). So someone who joined, was
            registered and left is not pending again, and a current member
            whose roles were reset by /reset_roles is not silently
            re-registered at Starfish three minutes later. Application state
            lives on applications, not on the link."""
            db = DB()
            try:
                db.connect()
                db.cursor.execute(
                    """SELECT dl.discord_id, a.channel_id
                       FROM discord_links dl
                       JOIN applications a
                         ON a.discord_id = dl.discord_id::text
                        AND a.application_type = 'guild'
                        AND a.status = 'accepted'
                       WHERE dl.uuid = %s::uuid
                         AND dl.rank IS NULL
                         AND NOT EXISTS (
                           SELECT 1 FROM membership_stints ms
                            WHERE ms.uuid = dl.uuid
                              AND (ms.left_at IS NOT NULL OR ms.rank_at_leave IS NOT NULL)
                              AND ms.joined_at >= COALESCE(a.submitted_at, a.reviewed_at) - INTERVAL '7 days'
                         )
                       ORDER BY a.reviewed_at DESC NULLS LAST, a.id DESC
                       LIMIT 1""",
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
        held_role_names = [r.name for r in member.roles]
        to_add, to_remove = registration_role_names(starting_rank)

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

        # Identity + rank + stint + honorific record, in one place.
        def _complete_registration(did, ign_val, uuid_str, wars, rank):
            db = DB()
            try:
                db.connect()
                record_registration(
                    db.cursor, discord_id=did, ign=ign_val, uuid=uuid_str, rank=rank,
                    wars_on_join=wars, held_role_names=held_role_names, actor_id=0,
                )
                db.connection.commit()
            finally:
                db.close()

        try:
            await asyncio.to_thread(_complete_registration, discord_id, ign, uuid, wars_on_join, starting_rank)
        except LinkConflictError as e:
            log(ERROR, f"Registration link conflict for {ign}: {e}", context="auto_register")
            return

        # Update poll embed
        if app_channel_id:
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

        # Send welcome DM
        await self._send_welcome_dm(member)

        log(INFO, f"Successfully registered {ign} ({member.name}) from accepted application.", context="auto_register")

    async def _send_welcome_dm(self, member: discord.Member):
        """Send a welcome DM to a newly registered guild member."""
        try:
            with open("data/guild_welcome_dm.json", "r", encoding="utf-8") as f:
                template_data = json.load(f)
        except Exception as e:
            log(WARN, f"Could not load welcome DM template: {e}", context="auto_register")
            return

        placeholders = {
            "[user]": member.mention,
            "[welcome_channel]": f"<#{WELCOME_CHANNEL_ID}>",
            "[announcement_channel]": f"<#{ANNOUNCEMENT_CHANNEL_ID}>",
            "[faq_channel]": f"<#{FAQ_CHANNEL_ID}>",
            "[guild_bank_channel]": f"<#{GUILD_BANK_CHANNEL_ID}>",
            "[rank_up_channel]": f"<#{RANK_UP_CHANNEL_ID}>",
            "[taq_roles_channel]": f"<#{TAQ_ROLES_CHANNEL_ID}>",
            "[bot_command_channel]": f"<#{BOT_COMMAND_CHANNEL_ID}>",
            "[raid_collecting_channel]": f"<#{RAID_COLLECTING_CHANNEL_ID}>",
        }

        def replace(text):
            for key, value in placeholders.items():
                text = text.replace(key, str(value))
            return text

        header = replace(template_data.get("header", ""))
        channels_title = template_data.get("channels_title", "")
        channels = "\n".join(
            f"> {replace(ch['channel'])} - {ch['description']}"
            for ch in template_data.get("channels", [])
        )
        footer = replace(template_data.get("footer", ""))

        message = f"{header}\n\n{channels_title}\n\n{channels}\n\n{footer}"

        try:
            await member.send(message)
            log(INFO, f"Sent welcome DM to {member.name}", context="auto_register")
        except discord.Forbidden:
            log(WARN, f"Could not DM {member.name} (DMs disabled or bot blocked)", context="auto_register")
        except Exception as e:
            log(WARN, f"Failed to send welcome DM to {member.name}: {e}", context="auto_register")

    @staticmethod
    def _sync_roster_table(members, now):
        db = DB()
        try:
            db.connect()
            joined, left = roster_db.sync_roster(db.cursor, members, now)
            db.connection.commit()
            if joined or left:
                log(INFO, f"Roster sync: +{len(joined)} -{len(left)}", context="update_member_data")
            return joined, left
        finally:
            db.close()

    @staticmethod
    def _fetch_unlinked_with_app():
        """Accepted guild applicants with an identity row but no rank yet
        (pending registration), for the sweep that catches joins the diff missed."""
        db = DB()
        try:
            db.connect()
            db.cursor.execute(
                """SELECT DISTINCT dl.uuid::text, dl.ign
                   FROM discord_links dl
                   JOIN applications a
                     ON a.discord_id = dl.discord_id::text
                    AND a.application_type = 'guild'
                    AND a.status = 'accepted'
                   WHERE dl.rank IS NULL
                     AND NOT EXISTS (
                       SELECT 1 FROM membership_stints ms
                        WHERE ms.uuid = dl.uuid
                          AND (ms.left_at IS NOT NULL OR ms.rank_at_leave IS NOT NULL)
                          AND ms.joined_at >= COALESCE(a.submitted_at, a.reviewed_at) - INTERVAL '7 days'
                     )"""
            )
            return db.cursor.fetchall()
        finally:
            db.close()

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

        guild = Guild("The Aquarium", "WYNN_LOOP_TOKEN")
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
                    pf = await asyncio.to_thread(getPlayerDatav3, uuid, "WYNN_LOOP_TOKEN")
                if not isinstance(pf, dict):
                    failed_members.append(m)
                    continue

                # Short-lived checkout scoped to this member's reads only. It is
                # opened after the external API fetch above and closed before the
                # next iteration's fetch (and before any retry sleep), so no pool
                # slot is held across an API call or a retry sleep.
                db = DB()
                db.connect()
                try:
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
                finally:
                    db.close()

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

        # 3: write to player_activity database table (uses UPSERT for deduplication).
        # Own checkout for the whole write transaction: per-member carry-forward
        # reads + UPSERTs + a single commit. No API fetches or sleeps happen here,
        # so the slot is held only for DB work.
        db = DB()
        db.connect()
        try:
            db_rows_written = 0
            for member in snap['members']:
                uuid = member.get('uuid')
                playtime = member.get('playtime')
                contributed = member.get('contributed')
                wars = member.get('wars')
                raids = member.get('raids') or 0
                shells = member.get('shells') or 0

                # Carry forward previous snapshot values for private/failed API responses
                # to avoid writing 0 which corrupts baseline calculations
                if uuid and (playtime is None or wars is None or contributed is None):
                    db.cursor.execute("""
                        SELECT playtime, contributed, wars FROM player_activity
                        WHERE uuid = %s ORDER BY snapshot_date DESC LIMIT 1
                    """, (uuid,))
                    prev = db.cursor.fetchone()
                    if prev:
                        if playtime is None:
                            playtime = prev[0]
                        if contributed is None:
                            contributed = prev[1]
                        if wars is None:
                            wars = prev[2]

                playtime = playtime or 0
                contributed = contributed or 0
                wars = wars or 0
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
            return (True, db_rows_written, total_members, failed_fetches, private_profiles)
        except Exception as e:
            log(ERROR, f"Failed to write to DB: {e}", context="update_member_data")
            traceback.print_exc()
            return (False, 0, total_members, failed_fetches, 0)
        finally:
            db.close()

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
            await asyncio.to_thread(_refresh_playtime_daily_sync)
        else:
            log(ERROR, "Daily activity snapshot failed", context="update_member_data")

    @tasks.loop(minutes=60)
    async def hourly_activity_snapshot(self):
        snapshot_at = datetime.datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        db = DB(); db.connect()
        try:
            guild_data = get_current_guild_data_with_db(db)
            members = guild_data.get('members', [])
            if not members:
                log(WARN, "Hourly activity snapshot: no cached guild data, skipping", context="update_member_data")
                return
            write_hourly_activity_snapshot_with_db(db, members, snapshot_at)
            prune_hourly_activity_snapshots_with_db(db)
            db.connection.commit()
            log(INFO, f"Hourly activity snapshot written ({len(members)} members) for {snapshot_at.isoformat()}", context="update_member_data")
        except Exception as e:
            log(ERROR, f"Hourly activity snapshot failed: {e}", context="update_member_data")
            traceback.print_exc()
        finally:
            db.close()

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

    @hourly_activity_snapshot.before_loop
    async def before_hourly_snapshot(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if self._has_started: return
        self._has_started=True
        if not self.update_member_data.is_running(): self.update_member_data.start()
        if not self.daily_activity_snapshot.is_running(): self.daily_activity_snapshot.start()
        if not self.hourly_activity_snapshot.is_running(): self.hourly_activity_snapshot.start()

    @update_member_data.error
    async def on_update_member_data_error(self, error):
        log(ERROR, f"update_member_data loop raised: {error!r}", context="update_member_data")
        traceback.print_exception(type(error), error, error.__traceback__)
        # This handler is awaited INSIDE the dying loop's own task, so
        # is_running() is still True here and an inline restart is always
        # skipped (observed in dev: the loop stayed dead for 75 minutes).
        # Detach the restart so its check runs after the loop task finishes.
        asyncio.create_task(self._restart_update_member_data())

    async def _restart_update_member_data(self):
        await asyncio.sleep(5)
        if not self.update_member_data.is_running():
            log(WARN, "Restarting update_member_data loop after crash", context="update_member_data")
            self.update_member_data.start()


    @staticmethod
    def _db_get_discord_id(uuid: str):
        """Blocking: look up Discord ID by UUID in discord_links."""
        db = _db_connect_with_retry()
        try:
            db.cursor.execute("SELECT discord_id FROM discord_links WHERE uuid = %s", (uuid,))
            row = db.cursor.fetchone()
            return row[0] if row else None
        finally:
            db.close()

    @staticmethod
    def _sync_member_igns(curr_map):
        """Blocking: refresh discord_links.ign for members whose guild-API name
        changed. One row per uuid since TAQ-76. Returns dicts
        {old, new, discord_id, rank} per rename so the caller can rebuild the
        Discord nickname."""
        db = _db_connect_with_retry()
        try:
            db.cursor.execute(
                "SELECT uuid::text, ign, discord_id, rank FROM discord_links"
            )
            stored = {
                row_uuid.replace('-', ''): {'name': row_ign, 'discord_id': row_discord_id, 'rank': row_rank}
                for row_uuid, row_ign, row_discord_id, row_rank in db.cursor.fetchall()
            }

            renames = []
            for uuid, info in curr_map.items():
                name = info.get('name')
                if not name:
                    continue
                entry = stored.get(uuid.replace('-', ''))
                if entry and entry['name'] != name:
                    db.cursor.execute(
                        "UPDATE discord_links SET ign = %s WHERE uuid = %s::uuid",
                        (name, uuid)
                    )
                    renames.append({
                        'old': entry['name'],
                        'new': name,
                        'discord_id': entry['discord_id'],
                        'rank': entry['rank'],
                    })
            if renames:
                db.connection.commit()
            return renames
        finally:
            db.close()

    async def _apply_rename_nicknames(self, renames):
        """Rebuild '{rank} {ign}' nicknames for renamed linked members, matching
        what registration/promotion set. Discord can refuse (server owner, role
        hierarchy) — log and continue, never break the loop."""
        guild = self.client.get_guild(TAQ_GUILD_ID)
        if guild is None:
            return
        for rename in renames:
            discord_id = rename.get('discord_id')
            if not discord_id:
                continue
            member = guild.get_member(discord_id)
            if member is None:
                try:
                    member = await guild.fetch_member(discord_id)
                except Exception:
                    continue
            nick = f"{rename.get('rank') or ''} {rename['new']}".strip()
            try:
                await member.edit(nick=nick, reason="Minecraft name change")
            except discord.Forbidden:
                log(WARN, f"No permission to update nickname for {rename['new']}", context="update_member_data")
            except Exception as e:
                log(ERROR, f"Nickname update failed for {rename['new']}: {e}", context="update_member_data")


def setup(client):
    client.add_cog(UpdateMemberData(client))
