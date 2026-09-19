import asyncio
import datetime
import json
import random
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands, tasks

from Helpers.database import DB
from Helpers.logger import ERROR, SUCCESS, WARN, log
from Helpers.variables import (
    ANNIHILATION_ANNOUNCEMENT_CHANNEL_ID,
    ANNIHILATION_PING_ROLE_ID,
    HOME_GUILD_IDS,
    IS_TEST_PROFILE,
)

WYNNCRAFT_WORLD_EVENTS_URL = "https://api.wynncraft.com/v3/map/world-events"
PRELUDE_EVENT_NAME = "Prelude to Annihilation"
ANNOUNCEMENT_STATE_KEY = "annihilationAnnouncements"
TIMER_CHECK_MINUTES = 1
WYNNCRAFT_POLL_SECONDS = 5 * 60
LATE_ALERT_GRACE_SECONDS = 5 * 60
ONE_HOUR_NOTICE_SECONDS = 60 * 60
STATE_RETENTION_HOURS = 24
EMBED_COLOR = 0xE00000
ICON_PATH = Path("images/annihilation/prelude_to_annihilation.png")
ICON_FILENAME = "prelude_to_annihilation.png"

TEST_CHANNEL_ID = 1523404444195225851
TEST_ROLE_ID = 1523404277043957921


def _db():
    db = DB()
    db.connect()
    return db


def _parse_wynncraft_datetime(value: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _discord_timestamp(dt: datetime.datetime) -> str:
    return f"<t:{int(dt.timestamp())}:R>"


def _default_state() -> dict:
    return {"runs": {}}


def _state_run_key(schedule_dt: datetime.datetime) -> str:
    return schedule_dt.isoformat()


def _load_state() -> dict:
    db = _db()
    try:
        db.cursor.execute("SELECT data FROM cache_entries WHERE cache_key = %s", (ANNOUNCEMENT_STATE_KEY,))
        row = db.cursor.fetchone()
        if row and row[0]:
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    except Exception as exc:
        log(WARN, f"Could not load announcement state: {exc!r}", context="annihilation")
    finally:
        db.close()
    return _default_state()


def _save_state(state: dict) -> None:
    db = _db()
    try:
        epoch = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
        db.cursor.execute(
            """
            INSERT INTO cache_entries (cache_key, data, expires_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (cache_key) DO UPDATE SET
                data = EXCLUDED.data,
                created_at = NOW(),
                expires_at = EXCLUDED.expires_at
            """,
            (ANNOUNCEMENT_STATE_KEY, json.dumps(state), epoch),
        )
        db.connection.commit()
    except Exception as exc:
        log(WARN, f"Could not save announcement state: {exc!r}", context="annihilation")
    finally:
        db.close()


def _prune_state(state: dict, now: datetime.datetime) -> dict:
    runs = state.setdefault("runs", {})
    cutoff = now - datetime.timedelta(hours=STATE_RETENTION_HOURS)
    for key in list(runs):
        try:
            run_dt = _parse_wynncraft_datetime(key)
        except ValueError:
            del runs[key]
            continue
        if run_dt < cutoff:
            del runs[key]
    return state


def _record_seen_run(state: dict, schedule_dt: datetime.datetime, now: datetime.datetime) -> bool:
    run_key = _state_run_key(schedule_dt)
    runs = state.setdefault("runs", {})
    run_state = runs.setdefault(run_key, {})
    seen_at = now.isoformat()
    changed = False

    if not run_state.get("first_seen_at"):
        run_state["first_seen_at"] = run_state.get("observed_at") or seen_at
        changed = True
    if run_state.get("last_seen_at") != seen_at:
        run_state["last_seen_at"] = seen_at
        changed = True

    return changed


def _is_one_hour_due(seconds_until: float) -> bool:
    return (
        ONE_HOUR_NOTICE_SECONDS - LATE_ALERT_GRACE_SECONDS
        <= seconds_until
        <= ONE_HOUR_NOTICE_SECONDS
    )


def _is_future_or_recent(seconds_until: float) -> bool:
    return seconds_until >= -LATE_ALERT_GRACE_SECONDS


def build_annihilation_embed(schedule_dt: datetime.datetime, alert_type: str) -> discord.Embed:
    if alert_type == "one_hour":
        description = (
            "1 hour left before it starts!\n"
            f"Event starting {_discord_timestamp(schedule_dt)}\n"
            "Make sure to leave your party if you cant make it or check up / open new parties to prepare!"
        )
    else:
        description = (
            "Hateful echoes erupt from the Portal.\n"
            "The province of Wynn faces **Annihilation**.\n\n"
            f"Annihilation starts <t:{int(schedule_dt.timestamp())}:F> "
            f"({_discord_timestamp(schedule_dt)}),\n"
            "Sign-ups are open!\n\n"
            "Prepare to defend the province at the **Corruption Portal**!"
        )

    embed = discord.Embed(
        title=PRELUDE_EVENT_NAME,
        description=description,
        colour=EMBED_COLOR,
    )
    embed.set_thumbnail(url=f"attachment://{ICON_FILENAME}")
    return embed


def _build_file(path: Path, filename: str) -> discord.File | None:
    if not path.exists():
        log(WARN, f"Image asset missing at {path}", context="annihilation")
        return None
    return discord.File(str(path), filename=filename)


def _build_files() -> list[discord.File]:
    files = []
    header_image = _build_file(ICON_PATH, ICON_FILENAME)
    if header_image is not None:
        files.append(header_image)

    return files


async def send_annihilation_announcement(
    client: discord.Bot,
    channel_id: int,
    role_id: int,
    schedule_dt: datetime.datetime,
    alert_type: str,
) -> discord.Message:
    channel = client.get_channel(channel_id)
    if channel is None:
        channel = await client.fetch_channel(channel_id)

    embed = build_annihilation_embed(schedule_dt, alert_type)
    files = _build_files()
    allowed_mentions = discord.AllowedMentions(
        everyone=False,
        users=False,
        roles=[discord.Object(id=role_id)],
    )

    kwargs = {
        "content": f"<@&{role_id}>",
        "embed": embed,
        "allowed_mentions": allowed_mentions,
    }
    if files:
        kwargs["files"] = files

    return await channel.send(**kwargs)


class AnnihilationAnnouncements(commands.Cog):
    def __init__(self, client):
        self.client = client
        self._http_session: aiohttp.ClientSession | None = None
        self._memory_state = _default_state()
        self._last_api_poll_at: datetime.datetime | None = None
        self._task.start()

    def cog_unload(self):
        if self._task.is_running():
            self._task.cancel()
        if self._http_session and not self._http_session.closed:
            asyncio.create_task(self._http_session.close())

    async def _session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
            self._http_session = aiohttp.ClientSession(timeout=timeout, raise_for_status=True)
        return self._http_session

    async def _fetch_world_events(self) -> list[dict]:
        session = await self._session()
        for attempt in range(3):
            try:
                async with session.get(WYNNCRAFT_WORLD_EVENTS_URL) as response:
                    data = await response.json()
                    if isinstance(data, list):
                        return data
                    raise ValueError(f"Unexpected response type: {type(data).__name__}")
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, json.JSONDecodeError) as exc:
                if attempt == 2:
                    raise exc
                await asyncio.sleep((2 ** attempt) + random.uniform(0, 0.3))
        return []

    @staticmethod
    def _find_prelude_event(events: list[dict]) -> dict | None:
        for event in events:
            if event.get("name") == PRELUDE_EVENT_NAME:
                return event
        return None

    async def _load_state(self) -> dict:
        try:
            state = await asyncio.to_thread(_load_state)
            if isinstance(state, dict):
                self._memory_state = state
                return state
        except Exception as exc:
            log(WARN, f"Falling back to in-memory state: {exc!r}", context="annihilation")
        return self._memory_state

    async def _save_state(self, state: dict) -> None:
        self._memory_state = state
        try:
            await asyncio.to_thread(_save_state, state)
        except Exception as exc:
            log(WARN, f"Could not persist state: {exc!r}", context="annihilation")

    async def _maybe_send(self, state: dict, schedule_dt: datetime.datetime, alert_type: str) -> bool:
        run_key = _state_run_key(schedule_dt)
        run_state = state.setdefault("runs", {}).setdefault(run_key, {})
        if run_state.get(alert_type):
            return False

        message = await send_annihilation_announcement(
            self.client,
            ANNIHILATION_ANNOUNCEMENT_CHANNEL_ID,
            ANNIHILATION_PING_ROLE_ID,
            schedule_dt,
            alert_type,
        )
        run_state[alert_type] = {
            "message_id": message.id,
            "sent_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        await self._save_state(state)
        if alert_type == "detected":
            await self._ensure_party_board(schedule_dt, message=message)
        return True

    async def _ensure_party_board(
        self,
        schedule_dt: datetime.datetime,
        message: discord.Message | None = None,
    ) -> None:
        party_cog = self.client.get_cog("AnnihilationParties")
        if party_cog is None:
            log(
                WARN,
                "AnnihilationParties cog is unavailable; party board was not created",
                context="annihilation",
            )
            return
        try:
            await party_cog.ensure_board(schedule_dt, message=message)
        except Exception as exc:
            log(
                ERROR,
                f"Could not create Annihilation party board: {exc!r}",
                context="annihilation",
            )

    async def _poll_api(self, state: dict, now: datetime.datetime) -> bool:
        if (
            self._last_api_poll_at is not None
            and (now - self._last_api_poll_at).total_seconds() < WYNNCRAFT_POLL_SECONDS
        ):
            return False

        self._last_api_poll_at = now
        events = await self._fetch_world_events()
        event = self._find_prelude_event(events)
        if not event:
            log(WARN, f"{PRELUDE_EVENT_NAME} not found in world-events response", context="annihilation")
            return False

        schedule = event.get("schedule")
        if not schedule:
            return False

        schedule_dt = _parse_wynncraft_datetime(schedule)
        seconds_until = (schedule_dt - now).total_seconds()
        if not _is_future_or_recent(seconds_until):
            return False

        return _record_seen_run(state, schedule_dt, now)

    async def _send_due_alerts(self, state: dict, now: datetime.datetime) -> int:
        sent_count = 0
        runs = state.setdefault("runs", {})

        for run_key in sorted(runs):
            try:
                schedule_dt = _parse_wynncraft_datetime(run_key)
            except ValueError:
                continue

            seconds_until = (schedule_dt - now).total_seconds()
            if not _is_future_or_recent(seconds_until):
                continue

            run_state = runs.get(run_key, {})
            sent_detection = False
            if not run_state.get("detected"):
                if await self._maybe_send(state, schedule_dt, "detected"):
                    sent_count += 1
                    sent_detection = True

            if not sent_detection and run_state.get("detected"):
                await self._ensure_party_board(schedule_dt)

            if sent_detection:
                continue

            if not run_state.get("one_hour") and _is_one_hour_due(seconds_until):
                if await self._maybe_send(state, schedule_dt, "one_hour"):
                    sent_count += 1

        return sent_count

    @tasks.loop(minutes=TIMER_CHECK_MINUTES)
    async def _task(self):
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            state = _prune_state(await self._load_state(), now)

            try:
                if await self._poll_api(state, now):
                    await self._save_state(state)
            except Exception as exc:
                log(WARN, f"API poll failed; checking saved timers anyway: {exc!r}", context="annihilation")

            sent_count = await self._send_due_alerts(state, now)
            if sent_count:
                log(SUCCESS, f"Sent {sent_count} {PRELUDE_EVENT_NAME} alert(s)", context="annihilation")
        except Exception as exc:
            log(ERROR, f"Polling failed: {exc!r}", context="annihilation")

    @_task.before_loop
    async def _wait_until_ready(self):
        await self.client.wait_until_ready()

    if IS_TEST_PROFILE:
        @discord.slash_command(
            name="annihilation-smoke-test",
            description="TEMP: Send both Prelude to Annihilation test announcements",
            default_member_permissions=discord.Permissions(administrator=True),
            guild_ids=HOME_GUILD_IDS,
        )
        async def annihilation_smoke_test(self, ctx: discord.ApplicationContext):
            await ctx.defer(ephemeral=True)

            now = datetime.datetime.now(datetime.timezone.utc)
            detected_at = now + datetime.timedelta(hours=1, minutes=15)
            one_hour_at = now + datetime.timedelta(hours=1)

            await send_annihilation_announcement(
                self.client,
                TEST_CHANNEL_ID,
                TEST_ROLE_ID,
                detected_at,
                "detected",
            )
            await send_annihilation_announcement(
                self.client,
                TEST_CHANNEL_ID,
                TEST_ROLE_ID,
                one_hour_at,
                "one_hour",
            )

            await ctx.followup.send(
                f"Sent both smoke-test announcements to <#{TEST_CHANNEL_ID}>.",
                ephemeral=True,
            )


def setup(client):
    client.add_cog(AnnihilationAnnouncements(client))
