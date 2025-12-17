"""
Helpers/aspect_db.py
Database operations for aspect distribution queue.
Replaces the JSON file-based approach.
"""

import datetime
from datetime import timezone, timedelta
from typing import List, Optional, Tuple
import json

from Helpers.database import DB
from Helpers.classes import Guild


WEEKLY_THRESHOLD = 5  # hours of playtime required


def get_weekly_playtime_from_db(db: DB, uuid: str) -> float:
    """Get player's playtime in the last 7 days from player_activity table."""
    try:
        # Get most recent playtime
        db.cursor.execute("""
            SELECT playtime FROM player_activity
            WHERE uuid = %s
            ORDER BY snapshot_date DESC
            LIMIT 1
        """, (uuid,))
        recent_row = db.cursor.fetchone()

        if not recent_row:
            return 0.0
        recent = recent_row[0] or 0

        # Get playtime from ~7 days ago
        db.cursor.execute("""
            SELECT playtime FROM player_activity
            WHERE uuid = %s
            ORDER BY snapshot_date DESC
            OFFSET 7
            LIMIT 1
        """, (uuid,))
        older_row = db.cursor.fetchone()

        if not older_row:
            # If no data 7 days ago, try to get earliest available
            db.cursor.execute("""
                SELECT playtime FROM player_activity
                WHERE uuid = %s
                ORDER BY snapshot_date ASC
                LIMIT 1
            """, (uuid,))
            older_row = db.cursor.fetchone()

        older = older_row[0] if older_row else 0

        if recent and older:
            return max(0.0, float(recent) - float(older))
        return 0.0
    except Exception as e:
        print(f"[aspect_db] Error getting weekly playtime for {uuid}: {e}")
        return 0.0


def get_weekly_playtime(uuid: str) -> float:
    """Get player's playtime in the last 7 days from player_activity table.
    Creates its own DB connection for standalone use."""
    db = DB()
    try:
        db.connect()
        return get_weekly_playtime_from_db(db, uuid)
    except Exception as e:
        print(f"[aspect_db] Error in get_weekly_playtime: {e}")
        return 0.0
    finally:
        try:
            db.close()
        except:
            pass


def get_blacklist(db: DB) -> set:
    """Get all blacklisted UUIDs from the database."""
    db.cursor.execute("SELECT uuid FROM aspect_blacklist")
    return {str(row[0]) for row in db.cursor.fetchall()}


def add_to_blacklist(db: DB, uuid: str, added_by: int) -> bool:
    """Add a UUID to the blacklist. Returns True if added, False if already exists."""
    try:
        db.cursor.execute(
            "INSERT INTO aspect_blacklist (uuid, added_by) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (uuid, added_by)
        )
        db.connection.commit()
        return db.cursor.rowcount > 0
    except Exception:
        return False


def remove_from_blacklist(db: DB, uuid: str) -> bool:
    """Remove a UUID from the blacklist. Returns True if removed."""
    db.cursor.execute("DELETE FROM aspect_blacklist WHERE uuid = %s", (uuid,))
    db.connection.commit()
    return db.cursor.rowcount > 0


def get_queue_state(db: DB) -> Tuple[List[str], int]:
    """Get current queue and marker position."""
    db.cursor.execute("SELECT queue, marker FROM aspect_queue WHERE id = 1")
    row = db.cursor.fetchone()
    if row:
        queue_data = row[0]
        if isinstance(queue_data, str):
            queue_data = json.loads(queue_data)
        elif queue_data is None:
            queue_data = []
        return queue_data, row[1] or 0
    return [], 0


def save_queue_state(db: DB, queue: List[str], marker: int) -> None:
    """Save queue and marker to database."""
    db.cursor.execute(
        """
        INSERT INTO aspect_queue (id, queue, marker, updated_at)
        VALUES (1, %s, %s, NOW())
        ON CONFLICT (id) DO UPDATE SET
            queue = EXCLUDED.queue,
            marker = EXCLUDED.marker,
            updated_at = NOW()
        """,
        (json.dumps(queue), marker)
    )
    db.connection.commit()


def rebuild_queue(db: DB) -> Tuple[List[str], int]:
    """
    Rebuild the eligible member queue and preserve marker position.
    Returns (queue, marker).
    """
    # Get current state
    old_queue, old_marker = get_queue_state(db)
    old_uuid = old_queue[old_marker] if 0 <= old_marker < len(old_queue) else None
    
    # Get blacklist
    blacklist = get_blacklist(db)
    
    # Get guild members
    guild = Guild("The Aquarium")
    cutoff = datetime.datetime.now(timezone.utc) - timedelta(days=7)
    
    # Build eligible list in guild member order
    eligible = []
    for m in guild.all_members:
        uuid = m["uuid"]
        joined = m.get("joined")
        
        if not joined or uuid in blacklist:
            continue
        
        try:
            dt = datetime.datetime.fromisoformat(joined.replace("Z", "+00:00"))
            dt = dt.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            continue
        
        # Must be in guild 7+ days and have 5+ hours weekly playtime
        if dt > cutoff or get_weekly_playtime_from_db(db, uuid) < WEEKLY_THRESHOLD:
            continue
        
        eligible.append(uuid)
    
    # Preserve marker position by UUID
    new_marker = 0
    if old_uuid:
        if old_uuid in eligible:
            new_marker = eligible.index(old_uuid)
        else:
            # Find next eligible after old position
            all_ids = [m["uuid"] for m in guild.all_members]
            try:
                old_idx = all_ids.index(old_uuid)
                for idx, u in enumerate(eligible):
                    if all_ids.index(u) > old_idx:
                        new_marker = idx
                        break
            except ValueError:
                pass
    
    # Save to DB
    save_queue_state(db, eligible, new_marker)
    
    return eligible, new_marker


def get_uncollected_aspects(db: DB) -> List[Tuple[str, int]]:
    """Get all players with uncollected aspects (earned from raids)."""
    db.cursor.execute(
        "SELECT uuid, uncollected_aspects FROM uncollected_raids WHERE uncollected_aspects > 0"
    )
    return db.cursor.fetchall()


def deduct_uncollected_aspects(db: DB, uuid: str, amount: int) -> None:
    """Deduct aspects from a player's uncollected pool."""
    db.cursor.execute(
        "UPDATE uncollected_raids SET uncollected_aspects = uncollected_aspects - %s WHERE uuid = %s",
        (amount, uuid)
    )


def log_distribution(db: DB, distributed_by: int, distributions: list, total_aspects: int, total_emeralds: int = 0) -> None:
    """Log a distribution event for audit purposes."""
    db.cursor.execute(
        """
        INSERT INTO distribution_log (distributed_by, distributions, total_aspects, total_emeralds)
        VALUES (%s, %s, %s, %s)
        """,
        (distributed_by, json.dumps(distributions), total_aspects, total_emeralds)
    )
    db.connection.commit()
