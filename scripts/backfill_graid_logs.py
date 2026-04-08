"""
One-time backfill script: parse raid-log channel history into graid_logs + graid_log_participants.

Expected embed format:
  Title:  "<emoji> <RaidName> Completed!"  OR  "<emoji> Guild Raid Completed!"
  Description:  "**Player1**, **Player2**, **Player3**, and **Player4**"

Embeds that don't match this format (e.g. raid name as participant, <2 players) are skipped.

Run as standalone:
  python scripts/backfill_graid_logs.py           (dry run)
  python scripts/backfill_graid_logs.py --execute  (actually insert)
"""

import asyncio
import datetime
import re
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import discord
from Helpers.classes import DB
from Helpers.variables import RAID_LOG_CHANNEL_ID

RAID_NAMES = [
    "Nest of the Grootslangs",
    "The Canyon Colossus",
    "The Nameless Anomaly",
    "Orphion's Nexus of Light",
]

# Set of raid names lowercased, used to detect faulty "participant" entries
RAID_NAMES_LOWER = {n.lower() for n in RAID_NAMES}

BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
# Pattern 1: "**Raid Name** completed by: Player1, Player2, Player3, Player4"
COMPLETED_BY_RE = re.compile(r"completed by:\s*(.+)", re.IGNORECASE)


def unescape_markdown(name: str) -> str:
    """Strip Discord markdown escape backslashes: \\_qtw -> _qtw"""
    return name.replace("\\_", "_").replace("\\*", "*").replace("\\~", "~").replace("\\`", "`").replace("\\|", "|")


def parse_embed(title: str, description: str) -> tuple[str | None, list[str]]:
    """
    Parse raid type and participant list from an embed.

    Returns (raid_type, [ign, ...]).

    Handles two formats:
      Current: title has raid name, description has **bold** player names
      Old:     title is generic, description has **RaidName** completed by: comma-separated names
    """
    # Try current format first: bold names in description
    bold_names = BOLD_RE.findall(description)
    real_names = [n for n in bold_names if n.lower() not in RAID_NAMES_LOWER]

    if len(real_names) >= 2:
        # Current format — raid type from title
        raid_type = None
        for name in RAID_NAMES:
            if name in title:
                raid_type = name
                break
        return raid_type, [unescape_markdown(n) for n in real_names]

    # Old format: "**RaidName** completed by: Player1, Player2, ..."
    completed_by_match = COMPLETED_BY_RE.search(description)
    if completed_by_match:
        # Extract raid type from the bold text in description
        raid_type = None
        for bn in bold_names:
            if bn.lower() in RAID_NAMES_LOWER:
                for name in RAID_NAMES:
                    if name.lower() == bn.lower():
                        raid_type = name
                        break
                break

        # Parse comma/and-separated player names after "completed by:"
        raw = completed_by_match.group(1).strip()
        # Split on ", " and " and " then clean up
        parts = re.split(r",\s*|\s+and\s+", raw)
        igns = [unescape_markdown(p.strip()) for p in parts if p.strip()]
        if len(igns) >= 2:
            return raid_type, igns

    # Neither format matched
    return None, []


def build_ign_to_uuid_map(db) -> dict[str, str]:
    db.cursor.execute("SELECT ign, uuid FROM discord_links WHERE uuid IS NOT NULL")
    mapping = {}
    for ign, uuid_val in db.cursor.fetchall():
        if ign:
            mapping[ign.lower()] = str(uuid_val)
    return mapping


def get_event_windows(db) -> list[dict]:
    db.cursor.execute("""
        SELECT id, title, start_ts, end_ts FROM graid_events ORDER BY start_ts
    """)
    return [{"id": r[0], "title": r[1], "start_ts": r[2], "end_ts": r[3]} for r in db.cursor.fetchall()]


def find_event_for_timestamp(events, ts) -> int | None:
    for ev in events:
        if ev["start_ts"] and ts >= ev["start_ts"]:
            if ev["end_ts"] is None or ts <= ev["end_ts"]:
                return ev["id"]
    return None


async def backfill(channel: discord.TextChannel, dry_run: bool = False):
    db = DB()
    db.connect()

    try:
        ign_map = build_ign_to_uuid_map(db)
        events = get_event_windows(db)

        # Load existing timestamps to avoid duplicates
        db.cursor.execute("SELECT completed_at FROM graid_logs")
        existing_timestamps = {row[0] for row in db.cursor.fetchall()}

        inserted = 0
        skipped = 0
        bad_format = 0
        msg_count = 0
        first_date = None
        last_date = None

        print("Scanning channel history...", flush=True)

        start_after = datetime.datetime(2025, 5, 12, tzinfo=datetime.timezone.utc)

        async for message in channel.history(limit=None, oldest_first=True, after=start_after):
            msg_count += 1
            last_date = message.created_at.strftime('%Y-%m-%d')
            if first_date is None:
                first_date = last_date
                print(f"  First message: {first_date}", flush=True)
            if msg_count % 200 == 0:
                print(f"  ...scanned {msg_count} messages, up to {last_date} ({inserted} inserted, {skipped} dup, {bad_format} bad)", flush=True)

            if not message.embeds:
                continue

            for embed in message.embeds:
                title = embed.title or ""
                desc = embed.description or ""

                if "Completed!" not in title:
                    continue

                ts = message.created_at
                raid_type, igns = parse_embed(title, desc)

                # Must have at least 2 real player names to be a valid raid group
                if len(igns) < 2:
                    bad_format += 1
                    continue

                # Dedup check
                is_dup = any(abs((ts - existing_ts).total_seconds()) < 2 for existing_ts in existing_timestamps)
                if is_dup:
                    skipped += 1
                    continue

                event_id = find_event_for_timestamp(events, ts)

                if dry_run:
                    ev_label = f"event {event_id}" if event_id else "no event"
                    print(f"  + {raid_type or 'Unknown'} {ts.strftime('%Y-%m-%d %H:%M')} "
                          f"- {len(igns)}p ({', '.join(igns[:4])}) - {ev_label}", flush=True)
                    inserted += 1
                    continue

                db.cursor.execute(
                    "INSERT INTO graid_logs (event_id, raid_type, completed_at) VALUES (%s, %s, %s) RETURNING id",
                    (event_id, raid_type, ts)
                )
                log_id = db.cursor.fetchone()[0]

                for ign in igns:
                    uuid_val = ign_map.get(ign.lower())
                    db.cursor.execute(
                        "INSERT INTO graid_log_participants (log_id, uuid, ign) VALUES (%s, %s, %s)",
                        (log_id, uuid_val, ign)
                    )

                existing_timestamps.add(ts)
                inserted += 1

                if inserted % 50 == 0:
                    print(f"  ...inserted {inserted} raids so far", flush=True)

        print(f"Scan complete. {msg_count} messages scanned ({first_date} to {last_date}).", flush=True)

        if not dry_run:
            db.connection.commit()
            print("Committed to database.", flush=True)

        return inserted, skipped, bad_format

    finally:
        db.close()


# --- Bot command integration ---

def setup_backfill_command(bot: discord.Bot):
    from Helpers.variables import ALL_GUILD_IDS

    @bot.slash_command(
        name="backfill-graid-logs",
        description="Backfill graid_logs from raid-log channel history (one-time)",
        guild_ids=ALL_GUILD_IDS,
    )
    @discord.default_permissions(manage_roles=True)
    async def backfill_graid_logs(
        ctx: discord.ApplicationContext,
        dry_run: discord.Option(bool, "Preview without inserting", default=True),
    ):
        await ctx.defer()
        channel = bot.get_channel(RAID_LOG_CHANNEL_ID)
        if not channel:
            await ctx.followup.send("Could not find raid-log channel.")
            return

        await ctx.followup.send(
            f"{'[DRY RUN] ' if dry_run else ''}Scanning raid-log channel history..."
        )

        inserted, skipped, bad_format = await backfill(channel, dry_run=dry_run)

        await ctx.followup.send(
            f"**Backfill {'preview' if dry_run else 'complete'}:**\n"
            f"- Inserted: **{inserted}**\n"
            f"- Skipped (duplicate): **{skipped}**\n"
            f"- Skipped (bad format): **{bad_format}**"
        )


# --- Standalone execution ---

if __name__ == "__main__":
    import dotenv
    dotenv.load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"Logged in as {client.user}")
        channel = client.get_channel(RAID_LOG_CHANNEL_ID)
        if not channel:
            print(f"Could not find channel {RAID_LOG_CHANNEL_ID}")
            await client.close()
            return

        dry = "--execute" not in sys.argv
        if dry:
            print("Running in DRY RUN mode. Pass --execute to actually insert.\n")

        inserted, skipped, bad_format = await backfill(channel, dry_run=dry)
        print(f"\nBackfill {'preview' if dry else 'complete'}:")
        print(f"  Inserted:            {inserted}")
        print(f"  Skipped (duplicate): {skipped}")
        print(f"  Skipped (bad format):{bad_format}")
        await client.close()

    token = os.getenv("TOKEN") if os.getenv("TEST_MODE", "").lower() != "true" else os.getenv("TEST_TOKEN")
    client.run(token)
