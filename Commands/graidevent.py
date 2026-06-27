# Commands/graidevent.py
import asyncio
import datetime
import math
import time
from datetime import timezone
from io import BytesIO
from typing import Optional

import discord
from discord import Option
from discord.commands import AutocompleteContext, SlashCommandGroup
from discord.ext import commands, pages
from PIL import Image, ImageDraw, ImageFont

from Helpers.classes import Page, PlaceTemplate
from Helpers.database import DB
from Helpers.functions import addLine, generate_rank_badge
from Helpers.pagination import add_paginator_buttons
from Helpers.variables import HOME_GUILD_IDS, discord_ranks, rank_map

RAID_NAMES = [
    "Nest of the Grootslangs",
    "The Canyon Colossus",
    "The Nameless Anomaly",
    "Orphion's Nexus of Light",
    "The Wartorn Palace",
]

RAID_SHORT = {
    "Nest of the Grootslangs": "NOTG",
    "The Canyon Colossus": "TCC",
    "The Nameless Anomaly": "TNA",
    "Orphion's Nexus of Light": "NOL",
    "The Wartorn Palace": "WTP",
}

RAID_SHORT_TO_FULL = {v: k for k, v in RAID_SHORT.items()}

MEDALS = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949", 4: "\U0001f3c5", 5: "\U0001f3c5"}
LE_ICON_PATH = "/home/ken/Downloads/LE.png"
STX_ICON_PATH = "/home/ken/Downloads/stx.png"

# simple TTL cache to avoid hammering DB
_EVENT_CACHE = {"items": [], "ts": 0.0}
_EVENT_TTL = 30.0  # seconds


def _db():
    db = DB()
    db.connect()
    return db


def _query_event_titles(prefix: str) -> list[str]:
    db = _db()
    try:
        if prefix:
            db.cursor.execute(
                """
                SELECT title
                FROM graid_events
                WHERE title ILIKE %s
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 25
                """,
                (f"%{prefix}%",),
            )
        else:
            db.cursor.execute(
                """
                SELECT title
                FROM graid_events
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 25
                """
            )
        return [r[0] for r in db.cursor.fetchall()]
    finally:
        db.close()


async def _graid_title_autocomplete(ctx: AutocompleteContext):
    prefix = (ctx.value or "").strip()
    now = time.time()
    if now - _EVENT_CACHE["ts"] < _EVENT_TTL and not prefix:
        return _EVENT_CACHE["items"]
    titles = await asyncio.to_thread(_query_event_titles, prefix)
    if not prefix:
        _EVENT_CACHE["items"], _EVENT_CACHE["ts"] = titles, now
    return titles


def _has_manage_roles(ctx: discord.ApplicationContext) -> bool:
    user = getattr(ctx, "user", None) or getattr(ctx, "author", None)
    perms = getattr(user, "guild_permissions", None)
    return bool(perms and perms.manage_roles)


async def _require_manage_roles(ctx: discord.ApplicationContext) -> bool:
    if _has_manage_roles(ctx):
        return True
    await ctx.respond("You need Manage Roles permission to use this command.", ephemeral=True)
    return False


def _parse_end_date(end_date_iso: str) -> datetime.datetime:
    if "T" in end_date_iso:
        return datetime.datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
    d = datetime.date.fromisoformat(end_date_iso)
    return datetime.datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc)


def _parse_bonus_config(raw: Optional[str], label: str) -> list[tuple[int, int]]:
    if not raw:
        return []

    out: list[tuple[int, int]] = []
    seen: set[int] = set()
    for chunk in raw.split(","):
        part = chunk.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"{label} must use key=value pairs, for example `50=64,100=128`.")
        left, right = [p.strip() for p in part.split("=", 1)]
        try:
            key = int(left)
            value = int(right)
        except ValueError as exc:
            raise ValueError(f"{label} values must be whole numbers.") from exc
        if key <= 0:
            raise ValueError(f"{label} keys must be positive.")
        if value < 0:
            raise ValueError(f"{label} bonus points cannot be negative.")
        if key in seen:
            raise ValueError(f"{label} contains duplicate key `{key}`.")
        seen.add(key)
        out.append((key, value))

    out.sort(key=lambda x: x[0])
    return out


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _format_stx_le(total_le: int) -> str:
    stx, le = divmod(max(0, int(total_le)), 64)
    if stx and le:
        return f"{stx} STX {le} LE"
    if stx:
        return f"{stx} STX"
    return f"{le} LE"


def _format_bonus_details(details: list[str]) -> str:
    return f" [{', '.join(details)}]" if details else ""


def _load_currency_icon(path: str) -> Image.Image | None:
    try:
        icon = Image.open(path).convert("RGBA")
        icon.thumbnail((16, 16))
        return icon
    except Exception:
        return None


def _draw_currency_payout(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    x: int,
    y: int,
    total_le: int,
    stx_icon: Image.Image | None,
    le_icon: Image.Image | None,
) -> None:
    if not stx_icon or not le_icon:
        addLine(f"&f{_format_stx_le(total_le)}", draw, font, x, y)
        return

    stx, le = divmod(max(0, int(total_le)), 64)
    parts = []
    if stx:
        parts.append((stx, stx_icon))
    if le or not parts:
        parts.append((le, le_icon))

    cursor_x = x
    for amount, icon in parts:
        cursor_x = addLine(f"&f{amount}", draw, font, cursor_x, y)
        img.paste(icon, (cursor_x + 3, y + 1), icon)
        cursor_x += icon.width + 10


def _default_raid_points() -> dict[str, int]:
    return {raid_name: 0 for raid_name in RAID_NAMES}


def _load_event_config(cur, *, event_id: int | None = None, title: str | None = None, active_only: bool = False):
    where = []
    params = []
    if event_id is not None:
        where.append("id = %s")
        params.append(event_id)
    if title is not None:
        where.append("title = %s")
        params.append(title)
    if active_only:
        where.append("active = TRUE")
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    cur.execute(
        f"""
        SELECT id, title, start_ts, end_ts, active, min_points, le_per_point
        FROM graid_events
        {where_sql}
        ORDER BY id DESC
        LIMIT 1
        """,
        params,
    )
    row = cur.fetchone()
    if not row:
        return None

    event = {
        "id": row[0],
        "title": row[1],
        "start_ts": row[2],
        "end_ts": row[3],
        "active": row[4],
        "min_points": int(row[5] or 0),
        "le_per_point": int(row[6] or 1),
        "raid_points": _default_raid_points(),
        "milestones": [],
        "placement_bonuses": {},
    }

    cur.execute(
        "SELECT raid_type, points FROM graid_event_raid_points WHERE event_id = %s",
        (event["id"],),
    )
    for raid_type, points in cur.fetchall():
        if raid_type in event["raid_points"]:
            event["raid_points"][raid_type] = int(points or 0)

    cur.execute(
        """
        SELECT threshold_points, bonus_points
        FROM graid_event_milestones
        WHERE event_id = %s
        ORDER BY threshold_points ASC
        """,
        (event["id"],),
    )
    event["milestones"] = [(int(t), int(b)) for t, b in cur.fetchall()]

    cur.execute(
        """
        SELECT placement, bonus_points
        FROM graid_event_placement_bonuses
        WHERE event_id = %s
        ORDER BY placement ASC
        """,
        (event["id"],),
    )
    event["placement_bonuses"] = {int(p): int(b) for p, b in cur.fetchall()}
    return event


def _load_event_contributions(cur, event_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT gl.id,
               gl.completed_at,
               gl.raid_type,
               glp.uuid::text,
               COALESCE(dl.ign, glp.ign, glp.uuid::text) AS display_name,
               dl.discord_id
        FROM graid_logs gl
        JOIN graid_log_participants glp ON glp.log_id = gl.id
        LEFT JOIN discord_links dl ON glp.uuid = dl.uuid
        WHERE gl.event_id = %s
          AND glp.uuid IS NOT NULL
        ORDER BY gl.completed_at ASC, gl.id ASC
        """,
        (event_id,),
    )

    contributions = []
    seen: set[tuple[int, str]] = set()
    for log_id, completed_at, raid_type, uuid, display_name, discord_id in cur.fetchall():
        key = (int(log_id), str(uuid))
        if key in seen:
            continue
        seen.add(key)
        contributions.append(
            {
                "log_id": int(log_id),
                "completed_at": completed_at,
                "raid_type": raid_type,
                "uuid": str(uuid),
                "display_name": display_name or str(uuid)[:8],
                "discord_id": discord_id,
            }
        )
    return contributions


def build_reward_rows(
    contributions: list[dict],
    raid_points: dict[str, int],
    milestones: list[tuple[int, int]],
    placement_bonuses: dict[int, int],
    min_points: int,
    le_per_point: int,
    *,
    include_below_threshold: bool = False,
) -> list[dict]:
    players: dict[str, dict] = {}

    ordered_contributions = sorted(
        contributions,
        key=lambda item: (
            item.get("completed_at") or datetime.datetime.max.replace(tzinfo=timezone.utc),
            item.get("log_id") or 0,
            (item.get("display_name") or "").casefold(),
        ),
    )

    for item in ordered_contributions:
        points = int(raid_points.get(item.get("raid_type"), 0) or 0)
        if points <= 0:
            continue

        uuid = str(item["uuid"])
        player = players.setdefault(
            uuid,
            {
                "uuid": uuid,
                "display_name": item.get("display_name") or uuid[:8],
                "discord_id": item.get("discord_id"),
                "ranking_points": 0,
                "reached_at": item.get("completed_at"),
            },
        )
        if item.get("display_name"):
            player["display_name"] = item["display_name"]
        if item.get("discord_id"):
            player["discord_id"] = item["discord_id"]
        player["ranking_points"] += points
        player["reached_at"] = item.get("completed_at")

    rows = [p for p in players.values() if include_below_threshold or p["ranking_points"] >= min_points]
    rows.sort(key=lambda p: (-p["ranking_points"], p["reached_at"], (p["display_name"] or "").casefold()))

    for index, row in enumerate(rows, 1):
        milestone_bonus = 0
        bonus_details: list[str] = []
        for threshold, bonus in milestones:
            if row["ranking_points"] >= threshold and bonus > 0:
                milestone_bonus += bonus
                bonus_details.append(f"+{bonus} from {threshold} point milestone")

        placement_bonus = int(placement_bonuses.get(index, 0) or 0)
        if placement_bonus > 0:
            bonus_details.append(f"+{placement_bonus} from {_ordinal(index)} place")

        reward_points = row["ranking_points"] + milestone_bonus + placement_bonus
        row["placement"] = index
        row["milestone_bonus_points"] = milestone_bonus
        row["placement_bonus_points"] = placement_bonus
        row["bonus_details"] = bonus_details
        row["reward_points"] = reward_points
        row["total_le"] = reward_points * int(le_per_point)
        row["payout"] = _format_stx_le(row["total_le"])

    return rows


def _mention(row: dict) -> str:
    if row.get("discord_id"):
        return f"<@{row['discord_id']}>"
    return f"**{row.get('display_name') or row.get('uuid', '')[:8]}**"


def _reward_line(row: dict) -> str:
    medal = MEDALS.get(row["placement"], "")
    prefix = f"{medal} " if medal else ""
    details = _format_bonus_details(row.get("bonus_details", []))
    return f"- {prefix}{_mention(row)} - {row['ranking_points']} points{details} - {row['payout']}"


def _event_rules_text(event: dict) -> str:
    raid_parts = [
        f"{RAID_SHORT[raid]}={event['raid_points'].get(raid, 0)}"
        for raid in RAID_NAMES
    ]
    lines = [
        f"Minimum points: **{event['min_points']}**",
        f"LE per point: **{event['le_per_point']}**",
        "Raid points: " + ", ".join(raid_parts),
    ]
    if event["milestones"]:
        lines.append("Milestones: " + ", ".join(f"{t}=+{b}" for t, b in event["milestones"]))
    if event["placement_bonuses"]:
        lines.append("Placement bonuses: " + ", ".join(f"{_ordinal(p)}=+{b}" for p, b in event["placement_bonuses"].items()))
    return "\n".join(lines)


def _create_event_leaderboard(event: dict, rows: list[dict]) -> pages.Paginator:
    if not rows:
        return pages.Paginator(pages=[Page(content="No event points yet.")])

    bg1 = PlaceTemplate("images/profile/first.png")
    bg2 = PlaceTemplate("images/profile/second.png")
    bg3 = PlaceTemplate("images/profile/third.png")
    bg_other = PlaceTemplate("images/profile/other.png")
    rank_star = Image.open("images/profile/rank_star.png")
    icon = Image.open("images/profile/raid_icon.png")
    icon.thumbnail((16, 16))
    le_icon = _load_currency_icon(LE_ICON_PATH)
    stx_icon = _load_currency_icon(STX_ICON_PATH)
    game_font = ImageFont.truetype("images/profile/game.ttf", 18)
    small_font = ImageFont.truetype("images/profile/game.ttf", 18)

    rows_per_page = 10
    row_h = 40
    header_h = 120
    footer_h = 20
    width = 760
    body_h = rows_per_page * row_h
    height = header_h + body_h + footer_h
    total_pages = math.ceil(len(rows) / rows_per_page)
    rank_counter = 1
    book: list[Page] = []

    for page_index in range(total_pages):
        img = Image.new("RGBA", (width, height), color="#00000000")
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"

        title_img = Image.open("images/profile/raids_title.png")
        img.paste(title_img, (img.width // 2 - title_img.width // 2, 10), title_img)

        badge_text = event["title"][:32]
        badge = generate_rank_badge(badge_text, "#0477c9", scale=1)
        img.paste(badge, (img.width // 2 - badge.width // 2, 98), badge)

        page_chunk = rows[page_index * rows_per_page:(page_index + 1) * rows_per_page]
        for row_idx, player in enumerate(page_chunk):
            bg_color = [bg1, bg2, bg3][rank_counter - 1] if rank_counter <= 3 else bg_other

            left_pad = 15
            row_top = header_h + row_idx * row_h
            y = row_top + 3
            bg_color.add(img, 730, (left_pad, y), start=True)
            img.paste(bg_color.divider, (left_pad + 55, y), bg_color.divider)
            addLine(f"&f{rank_counter}.", draw, game_font, left_pad + 10, row_top + 11)

            rank_key = (player.get("rank") or "").lower()
            general_rank = None
            for rname, info in discord_ranks.items():
                if rname.lower() == rank_key:
                    general_rank = info["in_game_rank"].lower()
                    break
            stars = rank_map.get(general_rank, "")
            for s in range(len(stars)):
                img.paste(rank_star, (left_pad + 65 + (s * 12), row_top + 16), rank_star)

            img.paste(bg_color.divider, (left_pad + 133, y), bg_color.divider)
            addLine(f"&f{player['display_name']}", draw, game_font, left_pad + 143, row_top + 11)

            value_str = f"{player['ranking_points']:,} pts"
            addLine(f"&f{value_str}", draw, game_font, 480, row_top + 11)
            img.paste(icon, (455, row_top + 13), icon)
            img.paste(bg_color.divider, (445, y), bg_color.divider)

            if player["ranking_points"] >= event["min_points"]:
                _draw_currency_payout(img, draw, small_font, 585, row_top + 12, player["total_le"], stx_icon, le_icon)
            else:
                reward_str = f"{event['min_points'] - player['ranking_points']} pts to qualify"
                addLine(f"&f{reward_str}", draw, small_font, 585, row_top + 12)
            img.paste(bg_color.divider, (570, y), bg_color.divider)
            rank_counter += 1

        background = Image.new("RGBA", (img.width, img.height), color="#00000000")
        bg_img = Image.open("images/profile/leaderboard_bg.png")
        background.paste(bg_img, (img.width // 2 - bg_img.width // 2, img.height // 2 - bg_img.height // 2))
        background.paste(img, (0, 0), img)

        buf = BytesIO()
        background.save(buf, format="PNG")
        buf.seek(0)
        t = int(time.time())
        leaderboard_img = discord.File(buf, filename=f"graid_event_leaderboard{t}_{page_index}.png")
        book.append(Page(content="", files=[leaderboard_img]))

    paginator = pages.Paginator(pages=book)
    add_paginator_buttons(paginator)
    return paginator


def _get_rank_map(cur, uuids: list[str]) -> dict[str, str]:
    if not uuids:
        return {}
    cur.execute("SELECT uuid::text, rank FROM discord_links WHERE uuid = ANY(%s::uuid[])", (uuids,))
    return {str(uuid): rank for uuid, rank in cur.fetchall()}


def _load_reward_rows(cur, event: dict, *, include_below_threshold: bool = False) -> list[dict]:
    contributions = _load_event_contributions(cur, event["id"])
    rows = build_reward_rows(
        contributions,
        event["raid_points"],
        event["milestones"],
        event["placement_bonuses"],
        event["min_points"],
        event["le_per_point"],
        include_below_threshold=include_below_threshold,
    )
    ranks = _get_rank_map(cur, [row["uuid"] for row in rows])
    for row in rows:
        row["rank"] = ranks.get(row["uuid"])
    return rows


class GraidEvent(commands.Cog):
    def __init__(self, client):
        self.client = client

    graid_event = SlashCommandGroup("graid-event", "GRAID event commands", guild_ids=HOME_GUILD_IDS)

    @graid_event.command(name="start", description="ADMIN: Start a new GRAID point event")
    async def graid_start(
        self,
        ctx: discord.ApplicationContext,
        title: str,
        end_date_iso: str,
        min_points: Option(int, "Minimum ranking points required for rewards", min_value=0),
        le_per_point: Option(int, "LE paid per final reward point", min_value=1),
        notg_points: Option(int, "Points for Nest of the Grootslangs", min_value=0),
        tcc_points: Option(int, "Points for The Canyon Colossus", min_value=0),
        tna_points: Option(int, "Points for The Nameless Anomaly", min_value=0),
        nol_points: Option(int, "Points for Orphion's Nexus of Light", min_value=0),
        wtp_points: Option(int, "Points for The Wartorn Palace", min_value=0),
        milestones: Option(str, "Optional: 50=64,100=128", required=False, default=None),
        placement_bonuses: Option(str, "Optional: 1=192,2=128,3=64", required=False, default=None),
    ):
        if not await _require_manage_roles(ctx):
            return

        try:
            end_ts = _parse_end_date(end_date_iso)
            parsed_milestones = _parse_bonus_config(milestones, "milestones")
            parsed_placements = _parse_bonus_config(placement_bonuses, "placement_bonuses")
        except Exception as exc:
            await ctx.respond(f"Invalid GRAID event config: {exc}", ephemeral=True)
            return

        raid_points = {
            RAID_SHORT_TO_FULL["NOTG"]: int(notg_points),
            RAID_SHORT_TO_FULL["TCC"]: int(tcc_points),
            RAID_SHORT_TO_FULL["TNA"]: int(tna_points),
            RAID_SHORT_TO_FULL["NOL"]: int(nol_points),
            RAID_SHORT_TO_FULL["WTP"]: int(wtp_points),
        }

        db = _db()
        try:
            cur = db.cursor
            if _load_event_config(cur, active_only=True):
                await ctx.respond("A GRAID event is already active.", ephemeral=True)
                return

            cur.execute(
                """
                INSERT INTO graid_events (
                    title, start_ts, end_ts, active,
                    low_rank_reward, high_rank_reward, min_completions,
                    bonus_threshold, bonus_amount,
                    min_points, le_per_point, created_by_discord
                )
                VALUES (%s, NOW(), %s, TRUE, 0, 0, 0, NULL, NULL, %s, %s, %s)
                RETURNING id
                """,
                (title, end_ts, min_points, le_per_point, ctx.user.id),
            )
            event_id = cur.fetchone()[0]

            for raid_type, points in raid_points.items():
                cur.execute(
                    """
                    INSERT INTO graid_event_raid_points (event_id, raid_type, points)
                    VALUES (%s, %s, %s)
                    """,
                    (event_id, raid_type, points),
                )

            for threshold, bonus in parsed_milestones:
                cur.execute(
                    """
                    INSERT INTO graid_event_milestones (event_id, threshold_points, bonus_points)
                    VALUES (%s, %s, %s)
                    """,
                    (event_id, threshold, bonus),
                )

            for placement, bonus in parsed_placements:
                cur.execute(
                    """
                    INSERT INTO graid_event_placement_bonuses (event_id, placement, bonus_points)
                    VALUES (%s, %s, %s)
                    """,
                    (event_id, placement, bonus),
                )

            db.connection.commit()

            event = {
                "title": title,
                "min_points": min_points,
                "le_per_point": le_per_point,
                "raid_points": raid_points,
                "milestones": parsed_milestones,
                "placement_bonuses": dict(parsed_placements),
            }
            await ctx.respond(
                f"Started **{title}**\n"
                f"Start: now\n"
                f"End: {end_ts.isoformat()}\n"
                f"{_event_rules_text(event)}\n"
                f"(id={event_id})",
                ephemeral=True,
            )
        finally:
            db.close()

    @graid_event.command(name="stop", description="ADMIN: Stop the current GRAID event")
    async def graid_stop(self, ctx: discord.ApplicationContext):
        if not await _require_manage_roles(ctx):
            return

        db = _db()
        try:
            cur = db.cursor
            event = _load_event_config(cur, active_only=True)
            if not event:
                await ctx.respond("No active GRAID event.", ephemeral=True)
                return

            rows = _load_reward_rows(cur, event)
            cur.execute(
                "UPDATE graid_events SET active = FALSE, end_ts = COALESCE(end_ts, NOW()), updated_at = NOW() WHERE id = %s",
                (event["id"],),
            )
            db.connection.commit()

            lines = [
                f"`{row['placement']:>2}.` **{row['display_name']}** - {row['ranking_points']} points"
                for row in rows[:10]
            ]
            desc = "\n".join(lines) if lines else "_No qualifying participants._"
            embed = discord.Embed(
                title=f"GRAID Ended: {event['title']}",
                description=desc,
                color=discord.Color.blurple(),
            )
            embed.add_field(name="Settings", value=_event_rules_text(event), inline=False)
            await ctx.respond(embed=embed, ephemeral=True)
        finally:
            db.close()

    @graid_event.command(name="info", description="Show the active GRAID event")
    async def graid_info(self, ctx: discord.ApplicationContext):
        db = _db()
        try:
            cur = db.cursor
            event = _load_event_config(cur, active_only=True)
            if not event:
                await ctx.respond("No active GRAID event.", ephemeral=True)
                return

            rows = _load_reward_rows(cur, event, include_below_threshold=True)
            lines = [
                f"`{row['placement']:>2}.` **{row['display_name']}** - {row['ranking_points']} points"
                for row in rows[:5]
            ]
            desc = "\n".join(lines) if lines else "_No one on the board yet._"
            embed = discord.Embed(
                title=f"Active GRAID: {event['title']}",
                description=desc,
                color=discord.Color.green(),
            )
            embed.add_field(
                name="Window",
                value=f"Start: {event['start_ts'].isoformat()}\nEnd: {event['end_ts'].isoformat() if event['end_ts'] else '-'}",
                inline=False,
            )
            embed.add_field(name="Rules", value=_event_rules_text(event), inline=False)
            await ctx.respond(embed=embed, ephemeral=True)
        finally:
            db.close()

    @graid_event.command(name="leaderboard", description="Show the active graid event leaderboard")
    async def graid_leaderboard(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        db = _db()
        try:
            cur = db.cursor
            event = _load_event_config(cur, active_only=True)
            if not event:
                await ctx.followup.send("No active GRAID event.")
                return
            rows = _load_reward_rows(cur, event, include_below_threshold=True)
        finally:
            db.close()

        paginator = _create_event_leaderboard(event, rows)
        await paginator.respond(ctx.interaction)

    @graid_event.command(name="rewards", description="ADMIN: Print a live GRAID reward list")
    async def graid_rewards(
        self,
        ctx: discord.ApplicationContext,
        title: Option(str, "Pick an event", autocomplete=_graid_title_autocomplete),
    ):
        if not await _require_manage_roles(ctx):
            return
        await ctx.defer(ephemeral=True)

        db = _db()
        try:
            cur = db.cursor
            event = _load_event_config(cur, title=title)
            if not event:
                await ctx.followup.send("No event with that title.", ephemeral=True)
                return
            rows = _load_reward_rows(cur, event)
        finally:
            db.close()

        if not rows:
            await ctx.followup.send("No qualifying participants for this event.", ephemeral=True)
            return

        header = f"**GRAID rewards: {event['title']}**\n"
        chunks: list[str] = []
        current = header
        for row in rows:
            line = _reward_line(row)
            if len(current) + len(line) + 1 > 1900:
                chunks.append(current)
                current = ""
            current += line + "\n"
        if current:
            chunks.append(current)

        for idx, chunk in enumerate(chunks):
            await ctx.followup.send(chunk, ephemeral=True)

    @graid_event.command(name="set", description="ADMIN: Activate an existing GRAID by title")
    async def graid_set(
        self,
        ctx: discord.ApplicationContext,
        title: Option(str, "Pick an existing event", autocomplete=_graid_title_autocomplete),
        reset_counters: bool = False,
    ):
        if not await _require_manage_roles(ctx):
            return

        db = _db()
        try:
            cur = db.cursor
            if _load_event_config(cur, active_only=True):
                await ctx.respond("A GRAID event is already active.", ephemeral=True)
                return

            event = _load_event_config(cur, title=title)
            if not event:
                await ctx.respond("No event with that title.", ephemeral=True)
                return

            if reset_counters:
                cur.execute("DELETE FROM graid_event_totals WHERE event_id = %s", (event["id"],))
                cur.execute("UPDATE graid_logs SET event_id = NULL WHERE event_id = %s", (event["id"],))
                cur.execute("UPDATE graid_events SET start_ts = NOW(), end_ts = NULL WHERE id = %s", (event["id"],))
            cur.execute("UPDATE graid_events SET active = TRUE, updated_at = NOW() WHERE id = %s", (event["id"],))
            db.connection.commit()
            await ctx.respond(f"Activated **{title}** (id={event['id']})", ephemeral=True)
        finally:
            db.close()


def setup(client):
    client.add_cog(GraidEvent(client))
