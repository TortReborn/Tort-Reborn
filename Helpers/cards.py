"""Card collection: the card set, drop odds, reel budget, pearls and trading.

Reels refresh on a fixed six-hour clock shared by everyone. Rather than a task
rewriting every wallet on the hour, the balance is refreshed lazily whenever a
wallet is touched: the current window is derived from the clock and compared
with the one stored on the row. Unused reels bank up to the tank's cap, so
sleeping through a refresh costs nothing while a week away still only leaves a
full bank rather than a huge backlog. Pearl trickle is caught up the same way.

Pearls are entirely separate from shells: nothing converts between them in
either direction, so a jackpot in one economy can never inflate the other.

DB functions here are blocking and expect to be called via asyncio.to_thread,
matching the rest of the bot.
"""

import json
import os
import random
import time

from Helpers.database import DB

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARD_SET_PATH = os.path.join(BASE, "data", "cards.json")

# ── Reel budget ──────────────────────────────────────────────────────────────
WINDOW_SECONDS = 6 * 60 * 60
REELS_PER_WINDOW = 3

# ── Drop odds ────────────────────────────────────────────────────────────────
# Worked backwards from what a month should feel like at 12 reels a day
# (84 a week, 360 a month): an epic about weekly, a legendary about monthly,
# and a 1-in-4 chance of a member 1/1 somewhere in those thirty days.
TIER_WEIGHTS = {
    "common": 43.76,
    "uncommon": 32.82,
    "rare": 21.88,
    "epic": 1.19,
    "legendary": 0.28,
    "member": 0.08,
}

TIER_ORDER = ["member", "legendary", "epic", "rare", "uncommon", "common"]
CARD_TIERS = ["legendary", "epic", "rare", "uncommon", "common"]

TIER_COLORS = {
    "common": 0x9CA3AF,
    "uncommon": 0x34D399,
    "rare": 0x60A5FA,
    "epic": 0xC084FC,
    "legendary": 0xFBBF24,
    "member": 0xF2549A,
}

# ── Pearls ───────────────────────────────────────────────────────────────────
# Every card pays out the moment it is reeled in, duplicates included, so
# there is nothing to scrap and no decision to get wrong. At 12 reels a day
# this averages roughly 460 pearls from pulls plus 150 from the daily, which
# paces the tank ladder at about 3 days for a Reef and six months to an Abyss.
PEARLS_PER_PULL = {
    "common": 10,
    "uncommon": 25,
    "rare": 60,
    "epic": 500,
    "legendary": 2500,
    "member": 5000,
}


def pull_value(card: dict) -> int:
    """Pearls paid for reeling this card in."""
    if card.get("member"):
        return PEARLS_PER_PULL["member"]
    return PEARLS_PER_PULL.get(card.get("tier"), 0)

# Star fusion: spare copies plus pearls to go from N stars to N+1.
FUSION_COPIES = {2: 2, 3: 4, 4: 8, 5: 16}
FUSION_PEARLS = {2: 150, 3: 500, 4: 1800, 5: 6000}
FUSION_TIER_FACTOR = {
    "common": 0.5, "uncommon": 0.75, "rare": 1.0, "epic": 2.0, "legendary": 4.0,
}
MAX_STARS = 5

# ── Tank tiers ───────────────────────────────────────────────────────────────
# Upgrading raises how many reels you can bank, not how many you earn, so the
# drop odds are untouched by progression.
TANK_TIERS = {
    1: {"name": "Fishbowl", "cost": 0, "bank": 6, "trickle": 0, "wishes": 1},
    2: {"name": "Reef", "cost": 2000, "bank": 8, "trickle": 3, "wishes": 2},
    3: {"name": "Kelp Forest", "cost": 8000, "bank": 10, "trickle": 6, "wishes": 3},
    4: {"name": "Deep Sea", "cost": 25000, "bank": 12, "trickle": 10, "wishes": 4},
    5: {"name": "Abyss", "cost": 75000, "bank": 15, "trickle": 15, "wishes": 5},
}
MAX_TANK = max(TANK_TIERS)
TRICKLE_CAP_HOURS = 24  # offline pearls stop accruing after a day

# ── Daily ────────────────────────────────────────────────────────────────────
DAILY_PEARLS = 50
DAILY_STREAK_BONUS = 10      # per consecutive day
DAILY_STREAK_CAP = 10        # bonus stops growing here
DAILY_REELS = 2

# ── Wishlist ─────────────────────────────────────────────────────────────────
# Wishes never touch tier odds. Once a tier is chosen, this is the chance the
# card is drawn from the user's wishes in that tier instead of uniformly.
#
# Only the chase tiers are wishable. Commons through rares already turn up
# several times a day, so steering them would be busywork; epics and
# legendaries are the ones worth aiming at.
WISH_REDIRECT_CHANCE = 0.30
WISHABLE_TIERS = ("epic", "legendary")

# ── Member 1/1 cards ─────────────────────────────────────────────────────────
MEMBER_ELIGIBLE_RANKS = ["Swordfish", "Hammerhead", "Sailfish", "Dolphin",
                         "Narwhal", "Hydra"]
VISAGE_URL = "https://visage.surgeplay.com/bust/500/{uuid}"

# ── Milestones ───────────────────────────────────────────────────────────────
UNIQUE_MILESTONES = {25: 250, 50: 600, 100: 1500, 200: 4000, 300: 9000}
TIER_COMPLETE_PEARLS = {
    "common": 3000, "uncommon": 3500, "rare": 5000, "epic": 15000,
    "legendary": 40000,
}

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS card_wallet (
        "user"       BIGINT      PRIMARY KEY,
        reels        SMALLINT    NOT NULL DEFAULT 3,
        window_idx   BIGINT      NOT NULL DEFAULT 0,
        total_reeled INT         NOT NULL DEFAULT 0,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS card_collection (
        "user"   BIGINT      NOT NULL,
        card     VARCHAR(64) NOT NULL,
        count    INT         NOT NULL DEFAULT 1,
        first_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY ("user", card)
    );
    """,
    'ALTER TABLE card_wallet ADD COLUMN IF NOT EXISTS pearls BIGINT NOT NULL DEFAULT 0',
    'ALTER TABLE card_wallet ADD COLUMN IF NOT EXISTS tank_tier SMALLINT NOT NULL DEFAULT 1',
    'ALTER TABLE card_wallet ADD COLUMN IF NOT EXISTS streak INT NOT NULL DEFAULT 0',
    'ALTER TABLE card_wallet ADD COLUMN IF NOT EXISTS last_daily DATE',
    'ALTER TABLE card_wallet ADD COLUMN IF NOT EXISTS last_trickle TIMESTAMPTZ NOT NULL DEFAULT NOW()',
    'ALTER TABLE card_collection ADD COLUMN IF NOT EXISTS stars SMALLINT NOT NULL DEFAULT 1',
    """
    CREATE TABLE IF NOT EXISTS card_wishlist (
        "user"   BIGINT      NOT NULL,
        card     VARCHAR(64) NOT NULL,
        added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY ("user", card)
    );
    """,
    # One row per eligible member, created the moment their 1/1 is minted.
    # A unique index on discord_id is what guarantees "one copy, ever".
    """
    CREATE TABLE IF NOT EXISTS card_members (
        slug       VARCHAR(64) PRIMARY KEY,
        discord_id BIGINT      NOT NULL UNIQUE,
        uuid       UUID,
        ign        VARCHAR(64) NOT NULL,
        rank       VARCHAR(32) NOT NULL,
        owner      BIGINT      NOT NULL,
        retired    BOOLEAN     NOT NULL DEFAULT FALSE,
        minted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS card_awards (
        "user"     BIGINT      NOT NULL,
        award      VARCHAR(64) NOT NULL,
        pearls     INT         NOT NULL,
        awarded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY ("user", award)
    );
    """,
]

_CARD_SET = None


# =============================================================================
# Card set
# =============================================================================

def load_card_set(force: bool = False) -> dict:
    """Load data/cards.json once and index it by slug and by tier."""
    global _CARD_SET
    if _CARD_SET is not None and not force:
        return _CARD_SET

    with open(CARD_SET_PATH, encoding="utf-8") as f:
        payload = json.load(f)

    cards = payload["cards"]
    by_tier = {}
    for c in cards:
        by_tier.setdefault(c["tier"], []).append(c)

    _CARD_SET = {
        "cards": cards,
        "by_slug": {c["slug"]: c for c in cards},
        "by_tier": by_tier,
        "generated_at": payload.get("generated_at", ""),
    }
    return _CARD_SET


def tier_counts() -> dict:
    return {t: len(v) for t, v in load_card_set()["by_tier"].items()}


def get_card(slug: str) -> dict | None:
    """Static card by slug. Member 1/1s live in the DB — see get_any_card."""
    return load_card_set()["by_slug"].get(slug)


def get_any_card(slug: str) -> dict | None:
    card = get_card(slug)
    if card is not None:
        return card
    return db_get_member_card(slug)


def member_card_dict(row: tuple) -> dict:
    """Shape a card_members row like a card-set entry so renderers agree."""
    slug, discord_id, uuid, ign, rank, owner, retired = row[:7]
    return {
        "slug": slug,
        "name": ign,
        "tier": rank if not retired else rank,
        "rank": rank,
        "member": True,
        "retired": retired,
        "discord_id": discord_id,
        "owner": owner,
        "wiki_url": "",
        "image_url": VISAGE_URL.format(uuid=uuid) if uuid else "",
    }


def is_member_slug(slug: str) -> bool:
    return slug.startswith("member-")


def current_window(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // WINDOW_SECONDS)


def next_refresh_ts(now: float | None = None) -> int:
    return (current_window(now) + 1) * WINDOW_SECONDS


def bank_cap(tank_tier: int) -> int:
    return TANK_TIERS.get(tank_tier, TANK_TIERS[1])["bank"]


def wish_slots(tank_tier: int) -> int:
    return TANK_TIERS.get(tank_tier, TANK_TIERS[1])["wishes"]


def fusion_cost(tier: str, to_star: int) -> tuple[int, int]:
    """(spare copies, pearls) needed to reach to_star."""
    copies = FUSION_COPIES[to_star]
    pearls = int(round(FUSION_PEARLS[to_star] * FUSION_TIER_FACTOR.get(tier, 1.0)))
    return copies, pearls


def _bank_cap_sql(column: str = "tank_tier") -> str:
    """CASE expression so the refresh can cap against the row's own tank."""
    cases = " ".join(f"WHEN {t} THEN {c['bank']}" for t, c in TANK_TIERS.items())
    return f"(CASE {column} {cases} ELSE {TANK_TIERS[1]['bank']} END)"


def _trickle_sql(column: str = "tank_tier") -> str:
    cases = " ".join(f"WHEN {t} THEN {c['trickle']}" for t, c in TANK_TIERS.items())
    return f"(CASE {column} {cases} ELSE 0 END)"


# =============================================================================
# Rolling
# =============================================================================

def roll_tier(rng: random.Random | None = None) -> str:
    r = rng or random
    tiers = list(TIER_WEIGHTS)
    return r.choices(tiers, weights=[TIER_WEIGHTS[t] for t in tiers], k=1)[0]


def roll_card(wishes: set | None = None, rng: random.Random | None = None) -> dict:
    """Pick a tier by weight, then a card inside it.

    Wishes only bias the choice *within* a tier, so the rarity curve is
    identical whether or not anything is wishlisted.
    """
    r = rng or random
    tier = roll_tier(r)
    if tier == "member":
        return {"tier": "member"}

    pool = load_card_set()["by_tier"][tier]
    if wishes:
        wanted = [c for c in pool if c["slug"] in wishes]
        if wanted and r.random() < WISH_REDIRECT_CHANCE:
            return r.choice(wanted)
    return r.choice(pool)


# =============================================================================
# DB — schema
# =============================================================================

def db_ensure_tables():
    db = DB()
    db.connect()
    try:
        for stmt in SCHEMA:
            db.cursor.execute(stmt)
        db.connection.commit()
    finally:
        db.close()


# =============================================================================
# DB — wallet
# =============================================================================

def _ensure_wallet(db, user_id: int, window: int):
    db.cursor.execute(
        'INSERT INTO card_wallet ("user", reels, window_idx) VALUES (%s, %s, %s) '
        'ON CONFLICT ("user") DO NOTHING',
        (user_id, REELS_PER_WINDOW, window),
    )


def _refresh_sql() -> str:
    """Catch up reels and pearl trickle in one statement."""
    cap, rate = _bank_cap_sql(), _trickle_sql()
    return (
        'UPDATE card_wallet SET '
        f'  reels = LEAST({cap}, reels + GREATEST(0, %(w)s - window_idx) * %(per)s), '
        '  window_idx = %(w)s, '
        f'  pearls = pearls + {rate} * LEAST(%(cap_h)s, '
        '      FLOOR(EXTRACT(EPOCH FROM (NOW() - last_trickle)) / 3600))::bigint, '
        '  last_trickle = last_trickle + make_interval(hours => '
        '      FLOOR(EXTRACT(EPOCH FROM (NOW() - last_trickle)) / 3600)::int) '
        'WHERE "user" = %(uid)s '
        'RETURNING reels, pearls, tank_tier, streak, total_reeled'
    )


def db_get_wallet(user_id: int) -> dict:
    """Wallet with reels and pearls brought up to date."""
    window = current_window()
    db = DB()
    db.connect()
    try:
        _ensure_wallet(db, user_id, window)
        db.cursor.execute(_refresh_sql(), {
            "w": window, "per": REELS_PER_WINDOW,
            "cap_h": TRICKLE_CAP_HOURS, "uid": user_id,
        })
        row = db.cursor.fetchone()
        db.connection.commit()
        if not row:
            return {"reels": 0, "pearls": 0, "tank_tier": 1, "streak": 0,
                    "total_reeled": 0}
        return {"reels": row[0], "pearls": row[1], "tank_tier": row[2],
                "streak": row[3], "total_reeled": row[4]}
    finally:
        db.close()


def db_get_balance(user_id: int) -> int:
    return db_get_wallet(user_id)["reels"]


def db_spend_reel(user_id: int) -> int | None:
    """Refresh and consume one reel atomically.

    Returns the remaining balance, or None when the user had none. Doing the
    refresh inside the same UPDATE keeps a spammed command from double
    spending: the row is locked for the whole statement.
    """
    window = current_window()
    cap = _bank_cap_sql()
    db = DB()
    db.connect()
    try:
        _ensure_wallet(db, user_id, window)
        db.cursor.execute(
            'UPDATE card_wallet SET '
            f'  reels = LEAST({cap}, reels + GREATEST(0, %(w)s - window_idx) * %(per)s) - 1, '
            '  window_idx = %(w)s, '
            '  total_reeled = total_reeled + 1 '
            'WHERE "user" = %(uid)s '
            f'  AND LEAST({cap}, reels + GREATEST(0, %(w)s - window_idx) * %(per)s) > 0 '
            'RETURNING reels',
            {"w": window, "per": REELS_PER_WINDOW, "uid": user_id},
        )
        row = db.cursor.fetchone()
        db.connection.commit()
        return row[0] if row else None
    finally:
        db.close()


def db_refund_reel(user_id: int):
    cap = _bank_cap_sql()
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            f'UPDATE card_wallet SET reels = LEAST({cap}, reels + 1), '
            '  total_reeled = GREATEST(0, total_reeled - 1) '
            'WHERE "user" = %s',
            (user_id,),
        )
        db.connection.commit()
    finally:
        db.close()


def db_reset_all_reels() -> int:
    """Testing helper: refill everyone to a full bank for the current window.

    Sets window_idx forward too, so the next natural refresh still lands on
    schedule rather than immediately topping people up again.
    """
    window = current_window()
    cap = _bank_cap_sql()
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            f'UPDATE card_wallet SET reels = {cap}, window_idx = %s', (window,))
        n = db.cursor.rowcount
        db.connection.commit()
        return n
    finally:
        db.close()


def db_grant_reels(user_id: int, amount: int) -> int:
    """Add reels from guild activity. Respects the tank's bank cap."""
    window = current_window()
    cap = _bank_cap_sql()
    db = DB()
    db.connect()
    try:
        _ensure_wallet(db, user_id, window)
        db.cursor.execute(
            f'UPDATE card_wallet SET reels = LEAST({cap}, reels + %s) '
            'WHERE "user" = %s RETURNING reels',
            (amount, user_id),
        )
        row = db.cursor.fetchone()
        db.connection.commit()
        return row[0] if row else 0
    finally:
        db.close()


def db_add_pearls(user_id: int, amount: int) -> int:
    db = DB()
    db.connect()
    try:
        _ensure_wallet(db, user_id, current_window())
        db.cursor.execute(
            'UPDATE card_wallet SET pearls = pearls + %s WHERE "user" = %s '
            'RETURNING pearls', (amount, user_id))
        row = db.cursor.fetchone()
        db.connection.commit()
        return row[0] if row else 0
    finally:
        db.close()


def db_spend_pearls(user_id: int, amount: int) -> int | None:
    """Deduct pearls, or return None if the balance is short."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'UPDATE card_wallet SET pearls = pearls - %s '
            'WHERE "user" = %s AND pearls >= %s RETURNING pearls',
            (amount, user_id, amount))
        row = db.cursor.fetchone()
        db.connection.commit()
        return row[0] if row else None
    finally:
        db.close()


def db_claim_daily(user_id: int) -> dict | None:
    """Claim the daily. Returns None if already claimed today.

    The streak continues when the last claim was yesterday and resets
    otherwise, decided in SQL so two fast clicks cannot both land.
    """
    window = current_window()
    cap = _bank_cap_sql()
    db = DB()
    db.connect()
    try:
        _ensure_wallet(db, user_id, window)
        db.cursor.execute(
            'UPDATE card_wallet SET '
            '  streak = CASE WHEN last_daily = CURRENT_DATE - 1 THEN streak + 1 ELSE 1 END, '
            '  last_daily = CURRENT_DATE, '
            '  pearls = pearls + %(base)s + %(bonus)s * LEAST(%(cap_s)s, '
            '      CASE WHEN last_daily = CURRENT_DATE - 1 THEN streak + 1 ELSE 1 END), '
            f'  reels = LEAST({cap}, reels + %(reels)s) '
            'WHERE "user" = %(uid)s '
            '  AND (last_daily IS NULL OR last_daily < CURRENT_DATE) '
            'RETURNING streak, pearls, reels',
            {"base": DAILY_PEARLS, "bonus": DAILY_STREAK_BONUS,
             "cap_s": DAILY_STREAK_CAP, "reels": DAILY_REELS, "uid": user_id},
        )
        row = db.cursor.fetchone()
        db.connection.commit()
        if not row:
            return None
        streak = row[0]
        return {
            "streak": streak,
            "pearls": row[1],
            "reels": row[2],
            "gained": DAILY_PEARLS + DAILY_STREAK_BONUS * min(DAILY_STREAK_CAP, streak),
        }
    finally:
        db.close()


def db_upgrade_tank(user_id: int, to_tier: int) -> bool:
    """Buy the next tank tier. Cost and tier check happen in one statement."""
    cost = TANK_TIERS[to_tier]["cost"]
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'UPDATE card_wallet SET pearls = pearls - %s, tank_tier = %s '
            'WHERE "user" = %s AND pearls >= %s AND tank_tier = %s',
            (cost, to_tier, user_id, cost, to_tier - 1))
        ok = db.cursor.rowcount > 0
        db.connection.commit()
        return ok
    finally:
        db.close()


# =============================================================================
# DB — collection
# =============================================================================

def db_add_card(user_id: int, slug: str, pearls: int = 0) -> dict:
    """Record a pull and pay out its pearls together.

    Both writes share one transaction so a card can never be banked without
    its payout, or vice versa.
    """
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'INSERT INTO card_collection ("user", card) VALUES (%s, %s) '
            'ON CONFLICT ("user", card) DO UPDATE SET count = card_collection.count + 1 '
            'RETURNING count',
            (user_id, slug))
        row = db.cursor.fetchone()
        total = None
        if pearls:
            db.cursor.execute(
                'UPDATE card_wallet SET pearls = pearls + %s WHERE "user" = %s '
                'RETURNING pearls', (pearls, user_id))
            prow = db.cursor.fetchone()
            total = prow[0] if prow else None
        db.connection.commit()
        return {"count": row[0] if row else 1, "gained": pearls, "pearls": total}
    finally:
        db.close()


def db_get_collection(user_id: int) -> dict:
    """slug -> {count, stars}."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'SELECT card, count, stars FROM card_collection WHERE "user" = %s',
            (user_id,))
        return {r[0]: {"count": r[1], "stars": r[2]} for r in db.cursor.fetchall()}
    finally:
        db.close()


def db_get_entry(user_id: int, slug: str) -> dict | None:
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'SELECT count, stars FROM card_collection WHERE "user" = %s AND card = %s',
            (user_id, slug))
        row = db.cursor.fetchone()
        return {"count": row[0], "stars": row[1]} if row else None
    finally:
        db.close()


def db_fuse(user_id: int, slug: str, to_star: int, copies: int, pearls: int) -> dict | None:
    """Spend spare copies and pearls to add a star.

    All three checks — enough copies, enough pearls, still at the expected
    star — are part of the write, so two clicks can't fuse twice.
    """
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'UPDATE card_collection SET count = count - %s, stars = %s '
            'WHERE "user" = %s AND card = %s AND stars = %s AND count - %s >= 1 '
            'RETURNING count, stars',
            (copies, to_star, user_id, slug, to_star - 1, copies))
        row = db.cursor.fetchone()
        if not row:
            db.connection.rollback()
            return None
        db.cursor.execute(
            'UPDATE card_wallet SET pearls = pearls - %s '
            'WHERE "user" = %s AND pearls >= %s RETURNING pearls',
            (pearls, user_id, pearls))
        prow = db.cursor.fetchone()
        if not prow:
            db.connection.rollback()
            return None
        db.connection.commit()
        return {"count": row[0], "stars": row[1], "pearls": prow[0]}
    finally:
        db.close()


def db_trade(from_user: int, to_user: int, give: str, want: str) -> bool:
    """Swap one copy each way inside a single transaction.

    Either both sides move or neither does, so a trade can never half-apply.
    """
    db = DB()
    db.connect()
    try:
        for owner, slug in ((from_user, give), (to_user, want)):
            db.cursor.execute(
                'UPDATE card_collection SET count = count - 1 '
                'WHERE "user" = %s AND card = %s AND count >= 1 AND stars = 1',
                (owner, slug))
            if db.cursor.rowcount == 0:
                db.connection.rollback()
                return False
        for owner, slug in ((to_user, give), (from_user, want)):
            db.cursor.execute(
                'INSERT INTO card_collection ("user", card) VALUES (%s, %s) '
                'ON CONFLICT ("user", card) DO UPDATE '
                'SET count = card_collection.count + 1',
                (owner, slug))
        db.cursor.execute(
            'DELETE FROM card_collection WHERE count <= 0 AND "user" IN (%s, %s)',
            (from_user, to_user))
        db.connection.commit()
        return True
    except Exception:
        db.connection.rollback()
        raise
    finally:
        db.close()


# =============================================================================
# DB — wishlist
# =============================================================================

def db_get_wishes(user_id: int) -> list:
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'SELECT card FROM card_wishlist WHERE "user" = %s ORDER BY added_at',
            (user_id,))
        return [r[0] for r in db.cursor.fetchall()]
    finally:
        db.close()


def is_wishable(card: dict | None) -> bool:
    return bool(card) and card.get("tier") in WISHABLE_TIERS


def db_add_wish(user_id: int, slug: str, limit: int) -> str:
    """'added', 'full', or 'duplicate'."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'SELECT COUNT(*), COUNT(*) FILTER (WHERE card = %s) '
            'FROM card_wishlist WHERE "user" = %s', (slug, user_id))
        total, already = db.cursor.fetchone()
        if already:
            return "duplicate"
        if total >= limit:
            return "full"
        db.cursor.execute(
            'INSERT INTO card_wishlist ("user", card) VALUES (%s, %s) '
            'ON CONFLICT DO NOTHING', (user_id, slug))
        db.connection.commit()
        return "added"
    finally:
        db.close()


def db_remove_wish(user_id: int, slug: str) -> bool:
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'DELETE FROM card_wishlist WHERE "user" = %s AND card = %s',
            (user_id, slug))
        ok = db.cursor.rowcount > 0
        db.connection.commit()
        return ok
    finally:
        db.close()


# =============================================================================
# DB — member 1/1 cards
# =============================================================================

def db_get_member_card(slug: str) -> dict | None:
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'SELECT slug, discord_id, uuid::text, ign, rank, owner, retired '
            'FROM card_members WHERE slug = %s', (slug,))
        row = db.cursor.fetchone()
        return member_card_dict(row) if row else None
    finally:
        db.close()


def db_get_member_cards(slugs: list | None = None) -> dict:
    db = DB()
    db.connect()
    try:
        if slugs is not None:
            if not slugs:
                return {}
            db.cursor.execute(
                'SELECT slug, discord_id, uuid::text, ign, rank, owner, retired '
                'FROM card_members WHERE slug = ANY(%s)', (list(slugs),))
        else:
            db.cursor.execute(
                'SELECT slug, discord_id, uuid::text, ign, rank, owner, retired '
                'FROM card_members')
        return {r[0]: member_card_dict(r) for r in db.cursor.fetchall()}
    finally:
        db.close()


def db_mint_member_card(owner_id: int) -> dict | None:
    """Mint the 1/1 of a random eligible member who doesn't have one yet.

    Returns None when every eligible member already has a card in circulation,
    in which case the caller should fall back to a normal card.
    """
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'SELECT discord_id, ign, uuid::text, rank FROM discord_links '
            'WHERE linked AND uuid IS NOT NULL AND rank = ANY(%s) '
            '  AND discord_id NOT IN (SELECT discord_id FROM card_members) '
            'ORDER BY RANDOM() LIMIT 1',
            (MEMBER_ELIGIBLE_RANKS,))
        row = db.cursor.fetchone()
        if not row:
            return None

        discord_id, ign, uuid, rank = row
        slug = "member-" + "".join(
            ch if ch.isalnum() else "-" for ch in ign.lower())[:50]

        db.cursor.execute(
            'INSERT INTO card_members (slug, discord_id, uuid, ign, rank, owner) '
            'VALUES (%s, %s, %s, %s, %s, %s) '
            'ON CONFLICT (discord_id) DO NOTHING '
            'RETURNING slug, discord_id, uuid::text, ign, rank, owner, retired',
            (slug, discord_id, uuid, ign, rank, owner_id))
        minted = db.cursor.fetchone()
        if not minted:
            db.connection.rollback()
            return None
        db.connection.commit()
        return member_card_dict(minted)
    finally:
        db.close()


def db_count_eligible_members() -> tuple[int, int]:
    """(minted, eligible) — how much of the 1/1 pool is already out there."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute('SELECT COUNT(*) FROM card_members')
        minted = db.cursor.fetchone()[0]
        db.cursor.execute(
            'SELECT COUNT(*) FROM discord_links WHERE linked AND uuid IS NOT NULL '
            'AND rank = ANY(%s)', (MEMBER_ELIGIBLE_RANKS,))
        return minted, db.cursor.fetchone()[0]
    finally:
        db.close()


def db_retire_departed_members() -> int:
    """Mark cards of members who are no longer eligible as Retired."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'UPDATE card_members SET retired = TRUE '
            'WHERE NOT retired AND discord_id NOT IN ('
            '  SELECT discord_id FROM discord_links '
            '  WHERE linked AND rank = ANY(%s))',
            (MEMBER_ELIGIBLE_RANKS,))
        n = db.cursor.rowcount
        db.connection.commit()
        return n
    finally:
        db.close()


# =============================================================================
# DB — milestones and leaderboard
# =============================================================================

def db_award_once(user_id: int, award: str, pearls: int) -> bool:
    """Grant a one-time award. False if it was already given."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'INSERT INTO card_awards ("user", award, pearls) VALUES (%s, %s, %s) '
            'ON CONFLICT DO NOTHING', (user_id, award, pearls))
        if db.cursor.rowcount == 0:
            db.connection.rollback()
            return False
        db.cursor.execute(
            'UPDATE card_wallet SET pearls = pearls + %s WHERE "user" = %s',
            (pearls, user_id))
        db.connection.commit()
        return True
    finally:
        db.close()


def db_leaderboard(limit: int = 15) -> list:
    """Unique cards, total copies and pearls per user."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'SELECT c."user", COUNT(*) AS uniques, SUM(c.count) AS copies, '
            '       COALESCE(MAX(w.pearls), 0) AS pearls '
            'FROM card_collection c '
            'LEFT JOIN card_wallet w ON w."user" = c."user" '
            'GROUP BY c."user" ORDER BY uniques DESC, copies DESC LIMIT %s',
            (limit,))
        return [{"user": r[0], "uniques": r[1], "copies": r[2], "pearls": r[3]}
                for r in db.cursor.fetchall()]
    finally:
        db.close()


def check_milestones(user_id: int, collection: dict) -> list:
    """Award unique-count and tier-completion milestones. Returns new awards."""
    earned = []
    static = load_card_set()
    owned_static = {s for s in collection if not is_member_slug(s)}

    for threshold, pearls in sorted(UNIQUE_MILESTONES.items()):
        if len(owned_static) >= threshold:
            if db_award_once(user_id, f"unique-{threshold}", pearls):
                earned.append((f"{threshold} unique cards", pearls))

    for tier, pearls in TIER_COMPLETE_PEARLS.items():
        pool = static["by_tier"].get(tier, [])
        if pool and all(c["slug"] in owned_static for c in pool):
            if db_award_once(user_id, f"tier-{tier}", pearls):
                earned.append((f"every {tier} card", pearls))

    return earned
