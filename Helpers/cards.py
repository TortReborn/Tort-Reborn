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
    "common": 43.61,
    "uncommon": 32.82,
    "rare": 21.88,
    "epic": 1.19,
    "legendary": 0.28,
    "fabled": 0.14,     # half a legendary's chance: the five raid bosses
    "member": 0.08,
}

TIER_ORDER = ["member", "fabled", "legendary", "epic", "rare", "uncommon",
              "common"]
CARD_TIERS = ["fabled", "legendary", "epic", "rare", "uncommon", "common"]

TIER_COLORS = {
    "common": 0x9CA3AF,
    "uncommon": 0x34D399,
    "rare": 0x60A5FA,
    "epic": 0xC084FC,
    "legendary": 0xFBBF24,
    "fabled": 0xFF5555,
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
    "fabled": 4000,
    "member": 5000,
}


def pull_value(card: dict) -> int:
    """Pearls paid for reeling this card in."""
    if card.get("member"):
        return PEARLS_PER_PULL["member"]
    return PEARLS_PER_PULL.get(card.get("tier"), 0)

# Star fusion merges three of a level into one of the next, so a 5★ is 81 base
# copies of the same card. The copies are the work; pearls are a light tax on
# top. Building a full 5★ costs 10,800 pearls across the whole pyramid, set
# against roughly 613 a day of income, so it never becomes the thing holding
# someone back.
# Stars count fusions, so an unfused card is 0★ and one merge makes it 1★.
FUSION_COPIES_PER_STEP = 3
FUSION_PEARLS = {1: 100, 2: 300, 3: 900, 4: 2700}
FUSION_TIER_MULT = {"fabled": 2.0}     # a fabled merge costs double
MAX_STARS = 4

# Each tier stops at its own ceiling, because three-of-a-kind compounds fast
# and the rare tiers simply do not drop often enough to feed it. Copies behind
# a maxed card: 81 for the common half of the set, 9 for an epic, 3 for a
# legendary. Every ceiling is meant to be reachable, and every one looks the
# same when you get there.
TIER_MAX_STARS = {
    "common": 4, "uncommon": 4, "rare": 4, "epic": 2,
    "legendary": 1, "fabled": 1,
}

# ── Discard ──────────────────────────────────────────────────────────────────
# A plain copy goes back and the tier below rolls in its place, so the fourth
# legendary that can never fuse anywhere still does something. The counts sit
# just under the drop weights at the top (a legendary drops 2x as often as a
# fabled, an epic 4.25x a legendary, a rare 18x an epic) so reeling stays the
# main way in, and just over them at the bottom, where dupes pile up and the
# point is a shot at something new. Commons have nowhere to go.
#
# Outputs pay no pearls: every card pays once, when it is reeled in, and a
# chain of discards paying at every step would print them. Wishes apply, the
# same way they do to a reel. Only unfused copies can be discarded, and never
# a member 1/1.
DISCARD_YIELD = {
    "fabled": ("legendary", 2),
    "legendary": ("epic", 4),
    "epic": ("rare", 10),
    "rare": ("uncommon", 2),
    "uncommon": ("common", 2),
}
# Losing one of these to a mis-click is a month of pulls, so they confirm.
DISCARD_CONFIRM_TIERS = {"fabled", "legendary"}

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
# Bait pays in bands rather than a per-day drip, so there is a rung to aim at
# instead of a number that creeps by ten. Each entry is the first day of its
# band and the last one runs forever. Reels land in the bait pocket, held
# apart from the bank -- see bait_reels.
DAILY_TIERS = [
    {"from_day": 1, "reels": 2, "pearls": 50},
    {"from_day": 4, "reels": 4, "pearls": 100},
    {"from_day": 7, "reels": 6, "pearls": 150},
]
MAX_BAIT_REELS = max(t["reels"] for t in DAILY_TIERS)

# ── Wishlist ─────────────────────────────────────────────────────────────────
# Wishes never touch tier odds, only which card is drawn once a tier has
# landed. That is what keeps them safe for the economy: pearls are paid per
# tier, so steering the pick inside a tier cannot change what a reel earns, no
# matter what is wished or how often it changes.
#
# Every rarity can be wished for. Member 1/1s cannot — a single-copy card of a
# named person should never be targetable.
WISH_REDIRECT_CHANCE = 0.25
WISHABLE_TIERS = tuple(CARD_TIERS)   # everything but a member 1/1

# ── Member 1/1 cards ─────────────────────────────────────────────────────────
MEMBER_ELIGIBLE_RANKS = ["Swordfish", "Hammerhead", "Sailfish", "Dolphin",
                         "Narwhal", "Hydra"]

# A link row survives someone leaving the guild, so rank alone would keep
# minting cards of people who are long gone. player_activity is written from
# the live roster every day, so appearing in a recent snapshot is what proves
# somebody is still here.
MEMBER_ACTIVE_DAYS = 14
ACTIVE_MEMBER_SQL = (
    "EXISTS (SELECT 1 FROM player_activity pa WHERE pa.uuid = dl.uuid "
    "AND pa.snapshot_date >= CURRENT_DATE - %s)"
)
VISAGE_URL = "https://visage.surgeplay.com/bust/500/{uuid}"

# ── Milestones ───────────────────────────────────────────────────────────────
UNIQUE_MILESTONES = {25: 250, 50: 600, 100: 1500, 200: 4000, 300: 9000}
TIER_COMPLETE_PEARLS = {
    "common": 3000, "uncommon": 3500, "rare": 5000, "epic": 15000,
    "legendary": 40000, "fabled": 25000,
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
    # Reels from bait sit outside the bank so a full bank cannot swallow them.
    # They never exceed one day's worth, because you cannot bait again while
    # any are unspent.
    'ALTER TABLE card_wallet ADD COLUMN IF NOT EXISTS bait_reels SMALLINT NOT NULL DEFAULT 0',
    'ALTER TABLE card_collection ADD COLUMN IF NOT EXISTS stars SMALLINT NOT NULL DEFAULT 0',
    # Stars used to start at 1 for an unfused card, which made every count one
    # higher than the number of merges behind it. They now count fusions, so a
    # plain card is 0★. Shift any pre-existing rows down once.
    '''
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM card_collection WHERE stars >= 1)
           AND NOT EXISTS (SELECT 1 FROM card_collection WHERE stars = 0) THEN
            UPDATE card_collection SET stars = stars - 1;
        END IF;
        ALTER TABLE card_collection ALTER COLUMN stars SET DEFAULT 0;
    END $$;
    ''',
    # Stars live on the copy, not on the card, so a 1★ and a 2★ of the same
    # character are separate stacks that can be held and traded apart.
    '''
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            JOIN pg_class t ON t.oid = i.indrelid
            WHERE t.relname = 'card_collection' AND i.indisprimary
              AND array_length(i.indkey::int2[], 1) = 2
        ) THEN
            ALTER TABLE card_collection DROP CONSTRAINT card_collection_pkey;
            ALTER TABLE card_collection ADD PRIMARY KEY ("user", card, stars);
        END IF;
    END $$;
    ''',
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

# guild id -> channel id (or None when unrestricted). Cached because the check
# runs on every card command; db_set_card_channel invalidates it.
_CHANNEL_CACHE = {}


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


def daily_tier(streak: int) -> dict:
    """What a streak of this length is worth."""
    band = DAILY_TIERS[0]
    for t in DAILY_TIERS:
        if streak >= t["from_day"]:
            band = t
    return band


def next_daily_tier(streak: int) -> dict | None:
    """The band above this streak, or None once it is at the top."""
    for t in DAILY_TIERS:
        if streak < t["from_day"]:
            return t
    return None


def _daily_case_sql(field: str, streak_expr: str) -> str:
    """A CASE over the streak bands, richest arm first.

    The streak is worked out inside the claim's UPDATE, so what it pays has
    to be worked out there too, from the same expression.
    """
    arms = " ".join(f"WHEN {streak_expr} >= {t['from_day']} THEN {t[field]}"
                    for t in reversed(DAILY_TIERS))
    return f"(CASE {arms} ELSE {DAILY_TIERS[0][field]} END)"


def fusion_cost(to_star: int, tier: str | None = None) -> tuple[int, int]:
    """(copies of the level below, pearls) needed to make one card at to_star."""
    pearls = FUSION_PEARLS[to_star] * FUSION_TIER_MULT.get(tier, 1.0)
    return FUSION_COPIES_PER_STEP, int(round(pearls))


def base_copies_for(star: int) -> int:
    """Unfused copies behind one card at this level. 0★ is a single card."""
    return FUSION_COPIES_PER_STEP ** star


def tier_max_stars(card: dict | None) -> int:
    """How far this card can be fused. Member 1/1s cannot be fused at all."""
    if not card or card.get("member"):
        return 0
    return TIER_MAX_STARS.get(card.get("tier"), MAX_STARS)


def discard_yield(card: dict | None) -> tuple[str, int] | None:
    """(tier below, how many) a plain copy of this card turns into, or None
    when it cannot be discarded at all."""
    if not card or card.get("member"):
        return None
    return DISCARD_YIELD.get(card.get("tier"))


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
    return roll_in_tier(tier, wishes, r)


def roll_in_tier(tier: str, wishes: set | None = None,
                 rng: random.Random | None = None) -> dict:
    """A card from one tier, with the wish redirect applied."""
    r = rng or random
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
# DB — card channel
# =============================================================================

def _channel_key(guild_id: int) -> str:
    return f"card_channel_{guild_id}"


def db_get_card_channel(guild_id: int) -> int | None:
    """Channel the card commands are confined to, or None for anywhere."""
    if guild_id in _CHANNEL_CACHE:
        return _CHANNEL_CACHE[guild_id]
    db = DB()
    db.connect()
    try:
        db.cursor.execute("SELECT value FROM bot_settings WHERE key = %s",
                          (_channel_key(guild_id),))
        row = db.cursor.fetchone()
    finally:
        db.close()
    value = int(row[0]) if row and str(row[0]).isdigit() else None
    _CHANNEL_CACHE[guild_id] = value
    return value


def db_set_card_channel(guild_id: int, channel_id: int | None):
    """Pin the card commands to a channel, or clear the restriction."""
    db = DB()
    db.connect()
    try:
        if channel_id is None:
            db.cursor.execute("DELETE FROM bot_settings WHERE key = %s",
                              (_channel_key(guild_id),))
        else:
            db.cursor.execute(
                "INSERT INTO bot_settings (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (_channel_key(guild_id), str(channel_id)))
        db.connection.commit()
    finally:
        db.close()
    _CHANNEL_CACHE[guild_id] = channel_id


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
        'RETURNING reels, pearls, tank_tier, streak, total_reeled, bait_reels'
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
                    "total_reeled": 0, "bait_reels": 0, "total_reels": 0}
        return {"reels": row[0], "pearls": row[1], "tank_tier": row[2],
                "streak": row[3], "total_reeled": row[4], "bait_reels": row[5],
                "total_reels": row[0] + row[5]}
    finally:
        db.close()


def db_get_balance(user_id: int) -> int:
    return db_get_wallet(user_id)["total_reels"]


def db_spend_reel(user_id: int) -> dict | None:
    """Refresh and consume one reel atomically, bait pocket first.

    Returns the remaining balances, or None when the user had none. Doing the
    refresh inside the same UPDATE keeps a spammed command from double
    spending: the row is locked for the whole statement.

    Bait reels go first so they cannot rot: they block tomorrow's bait until
    they are gone, and nothing else about them is worth hoarding. The lock is
    taken in a CTE rather than left implicit, because the caller needs to know
    which pocket it came out of to refund it correctly, and that is the one
    thing RETURNING cannot tell it -- RETURNING sees the row after the write.
    """
    window = current_window()
    cap = _bank_cap_sql()
    fresh = (f'LEAST({cap}, w.reels + '
             'GREATEST(0, %(w)s - w.window_idx) * %(per)s)')
    db = DB()
    db.connect()
    try:
        _ensure_wallet(db, user_id, window)
        db.cursor.execute(
            'WITH locked AS ('
            '    SELECT "user", bait_reels FROM card_wallet '
            '    WHERE "user" = %(uid)s FOR UPDATE'
            ') '
            'UPDATE card_wallet w SET '
            f'  reels = {fresh} - CASE WHEN l.bait_reels > 0 THEN 0 ELSE 1 END, '
            '  bait_reels = GREATEST(0, w.bait_reels - 1), '
            '  window_idx = %(w)s, '
            '  total_reeled = w.total_reeled + 1 '
            'FROM locked l '
            'WHERE w."user" = l."user" '
            f'  AND (l.bait_reels > 0 OR {fresh} > 0) '
            'RETURNING w.reels, w.bait_reels, (l.bait_reels > 0)',
            {"w": window, "per": REELS_PER_WINDOW, "uid": user_id},
        )
        row = db.cursor.fetchone()
        db.connection.commit()
        if not row:
            return None
        return {"reels": row[0], "bait_reels": row[1],
                "total": row[0] + row[1], "used_bait": row[2]}
    finally:
        db.close()


def db_refund_reel(user_id: int, bait: bool = False):
    """Put a reel back in the pocket it was taken from.

    A bait reel refunded into the bank would vanish against the cap, which is
    the whole thing the separate pocket exists to prevent.
    """
    if bait:
        restore = 'bait_reels = LEAST(%s, bait_reels + 1)' % MAX_BAIT_REELS
    else:
        restore = 'reels = LEAST(%s, reels + 1)' % _bank_cap_sql()
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            f'UPDATE card_wallet SET {restore}, '
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


def db_next_daily_reset() -> int:
    """Unix time of the next daily rollover.

    Asked of Postgres rather than worked out here, because the claim itself
    turns on CURRENT_DATE: whatever timezone the database is set to, this
    lands on the same boundary the claim will.
    """
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "SELECT EXTRACT(EPOCH FROM (CURRENT_DATE + 1)::timestamptz)::bigint")
        return int(db.cursor.fetchone()[0])
    finally:
        db.close()


def db_claim_daily(user_id: int) -> dict:
    """Claim the daily.

    Returns {"ok": True, ...} or {"ok": False, "reason": ...} where the reason
    is "claimed" (already had it today) or "unspent" (bait reels still in
    hand). Unspent bait blocks the next claim on purpose: it caps the pocket
    at one day's worth without needing a cap, and it keeps bait from becoming
    a second bank people sit on.

    The streak continues when the last claim was yesterday and resets
    otherwise, decided in SQL so two fast clicks cannot both land. What it
    pays comes off that same expression, so the streak reported back and the
    band it was paid at can never disagree.
    """
    window = current_window()
    new_streak = ('(CASE WHEN last_daily = CURRENT_DATE - 1 '
                  'THEN streak + 1 ELSE 1 END)')
    db = DB()
    db.connect()
    try:
        _ensure_wallet(db, user_id, window)
        # Bring the bank and the trickle current first, in the same
        # transaction, so the numbers reported back are the real ones.
        db.cursor.execute(_refresh_sql(), {
            "w": window, "per": REELS_PER_WINDOW,
            "cap_h": TRICKLE_CAP_HOURS, "uid": user_id,
        })
        db.cursor.execute(
            'UPDATE card_wallet SET '
            f'  streak = {new_streak}, '
            '  last_daily = CURRENT_DATE, '
            f'  pearls = pearls + {_daily_case_sql("pearls", new_streak)}, '
            f'  bait_reels = {_daily_case_sql("reels", new_streak)} '
            'WHERE "user" = %(uid)s '
            '  AND (last_daily IS NULL OR last_daily < CURRENT_DATE) '
            '  AND bait_reels = 0 '
            'RETURNING streak, pearls, reels, bait_reels',
            {"uid": user_id},
        )
        row = db.cursor.fetchone()
        if not row:
            # Two things can block a claim; say which one it was.
            db.cursor.execute(
                'SELECT last_daily >= CURRENT_DATE, bait_reels '
                'FROM card_wallet WHERE "user" = %s', (user_id,))
            state = db.cursor.fetchone()
            db.connection.commit()
            today, held = (state[0], state[1]) if state else (True, 0)
            return {"ok": False, "bait_reels": held,
                    "reason": "claimed" if today else "unspent"}

        db.connection.commit()
        streak = row[0]
        return {
            "ok": True,
            "streak": streak,
            "pearls": row[1],
            "reels": row[2],
            "bait_reels": row[3],
            "total_reels": row[2] + row[3],
            "gained": daily_tier(streak)["pearls"],
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
            'INSERT INTO card_collection ("user", card, stars) VALUES (%s, %s, 0) '
            'ON CONFLICT ("user", card, stars) '
            'DO UPDATE SET count = card_collection.count + 1 '
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
    """slug -> {"total": copies at every level, "levels": {star: count}}."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'SELECT card, stars, count FROM card_collection '
            'WHERE "user" = %s AND count > 0 ORDER BY card, stars',
            (user_id,))
        out = {}
        for card, stars, count in db.cursor.fetchall():
            e = out.setdefault(card, {"total": 0, "levels": {}})
            e["levels"][stars] = count
            e["total"] += count
        return out
    finally:
        db.close()


def db_get_entry(user_id: int, slug: str) -> dict | None:
    """One card's holdings, or None if none are held at any level."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'SELECT stars, count FROM card_collection '
            'WHERE "user" = %s AND card = %s AND count > 0 ORDER BY stars',
            (user_id, slug))
        rows = db.cursor.fetchall()
        if not rows:
            return None
        levels = {r[0]: r[1] for r in rows}
        return {"levels": levels, "total": sum(levels.values()),
                "best": max(levels)}
    finally:
        db.close()


def db_fuse(user_id: int, slug: str, to_star: int, copies: int,
            pearls: int, made: int = 1) -> dict | None:
    """Merge `copies` cards at to_star-1 into `made` at to_star.

    Every check — enough copies at that exact level, enough pearls — is part
    of the write, so two fast clicks cannot fuse the same copies twice.

    A batch is one transaction rather than a loop of single merges: nine
    merges that half-fail would leave a collection nobody asked for, and the
    arithmetic is the same either way.
    """
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'UPDATE card_collection SET count = count - %s '
            'WHERE "user" = %s AND card = %s AND stars = %s AND count >= %s '
            'RETURNING count',
            (copies, user_id, slug, to_star - 1, copies))
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

        db.cursor.execute(
            'INSERT INTO card_collection ("user", card, stars, count) '
            'VALUES (%s, %s, %s, %s) '
            'ON CONFLICT ("user", card, stars) '
            'DO UPDATE SET count = card_collection.count + %s '
            'RETURNING count',
            (user_id, slug, to_star, made, made))
        now = db.cursor.fetchone()
        db.cursor.execute(
            'DELETE FROM card_collection WHERE "user" = %s AND count <= 0',
            (user_id,))
        db.connection.commit()
        return {"left": row[0], "now": now[0] if now else made,
                "stars": to_star, "pearls": prow[0], "made": made}
    finally:
        db.close()


def db_trade(from_user: int, to_user: int, give: str, give_star: int,
             want: str, want_star: int) -> bool:
    """Swap one copy each way, at the star level each side named.

    Any copy can be traded, fused or not — a 2★ and a 1★ of the same card are
    separate stacks and move independently. Either both sides move or neither
    does, so a trade can never half-apply.
    """
    db = DB()
    db.connect()
    try:
        for owner, slug, star in ((from_user, give, give_star),
                                  (to_user, want, want_star)):
            db.cursor.execute(
                'UPDATE card_collection SET count = count - 1 '
                'WHERE "user" = %s AND card = %s AND stars = %s AND count >= 1',
                (owner, slug, star))
            if db.cursor.rowcount == 0:
                db.connection.rollback()
                return False
        for owner, slug, star in ((to_user, give, give_star),
                                  (from_user, want, want_star)):
            db.cursor.execute(
                'INSERT INTO card_collection ("user", card, stars) '
                'VALUES (%s, %s, %s) ON CONFLICT ("user", card, stars) '
                'DO UPDATE SET count = card_collection.count + 1',
                (owner, slug, star))
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


def db_discard(user_id: int, slug: str, count: int, outputs: list) -> dict | None:
    """Give up `count` plain copies of `slug` and bank `outputs` in their place.

    The outputs are rolled by the caller; this only makes the swap hold
    together. The copies leave and the outputs arrive in one transaction, and
    the decrement is guarded on the count, so a double-click cannot discard
    the same copies twice or bank two sets of outputs for one.

    Returns {"left": plain copies remaining, "new": slugs first seen here},
    or None when the copies were not there.
    """
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'UPDATE card_collection SET count = count - %s '
            'WHERE "user" = %s AND card = %s AND stars = 0 AND count >= %s '
            'RETURNING count',
            (count, user_id, slug, count))
        row = db.cursor.fetchone()
        if not row:
            db.connection.rollback()
            return None

        added = {}
        for out in outputs:
            added[out] = added.get(out, 0) + 1
        new = set()
        for out, n in added.items():
            db.cursor.execute(
                'INSERT INTO card_collection ("user", card, stars, count) '
                'VALUES (%s, %s, 0, %s) '
                'ON CONFLICT ("user", card, stars) '
                'DO UPDATE SET count = card_collection.count + %s '
                'RETURNING count',
                (user_id, out, n, n))
            if db.cursor.fetchone()[0] == n:
                new.add(out)
        db.cursor.execute(
            'DELETE FROM card_collection WHERE "user" = %s AND count <= 0',
            (user_id,))
        db.connection.commit()
        return {"left": row[0], "new": new}
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
    if not card or card.get("member"):
        return False
    return card.get("tier") in WISHABLE_TIERS


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
            'SELECT dl.discord_id, dl.ign, dl.uuid::text, dl.rank '
            'FROM discord_links dl '
            'WHERE dl.linked AND dl.uuid IS NOT NULL AND dl.rank = ANY(%s) '
            f'  AND {ACTIVE_MEMBER_SQL} '
            '  AND dl.discord_id NOT IN (SELECT discord_id FROM card_members) '
            'ORDER BY RANDOM() LIMIT 1',
            (MEMBER_ELIGIBLE_RANKS, MEMBER_ACTIVE_DAYS))
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


def pool_entry(ign: str, uuid: str, rank: str, discord_id: int,
               owner: int | None, retired: bool) -> dict:
    """A pool member shaped like a card so the renderer can draw them."""
    slug = "member-" + "".join(
        ch if ch.isalnum() else "-" for ch in ign.lower())[:50]
    return {
        "slug": slug, "name": ign, "tier": rank, "rank": rank,
        "member": True, "retired": bool(retired),
        "discord_id": discord_id, "owner": owner,
        "minted": owner is not None,
        "wiki_url": "",
        "image_url": VISAGE_URL.format(uuid=uuid) if uuid else "",
    }


def db_get_pool() -> list:
    """Everyone eligible for a 1/1, ranked highest first.

    Left joins the minted cards so the list can show which are still up for
    grabs and who holds the rest.
    """
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            'SELECT dl.ign, dl.uuid::text, dl.rank, dl.discord_id, '
            '       cm.owner, COALESCE(cm.retired, FALSE) '
            'FROM discord_links dl '
            'LEFT JOIN card_members cm ON cm.discord_id = dl.discord_id '
            'WHERE dl.linked AND dl.uuid IS NOT NULL AND dl.rank = ANY(%s) '
            f'  AND {ACTIVE_MEMBER_SQL} '
            'ORDER BY array_position(%s::text[], dl.rank) DESC, lower(dl.ign)',
            (MEMBER_ELIGIBLE_RANKS, MEMBER_ACTIVE_DAYS, MEMBER_ELIGIBLE_RANKS))
        return [pool_entry(*r) for r in db.cursor.fetchall()]
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
            'SELECT COUNT(*) FROM discord_links dl '
            'WHERE dl.linked AND dl.uuid IS NOT NULL AND dl.rank = ANY(%s) '
            f'  AND {ACTIVE_MEMBER_SQL}',
            (MEMBER_ELIGIBLE_RANKS, MEMBER_ACTIVE_DAYS))
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
            '  SELECT dl.discord_id FROM discord_links dl '
            '  WHERE dl.linked AND dl.rank = ANY(%s) '
            f'    AND {ACTIVE_MEMBER_SQL})',
            (MEMBER_ELIGIBLE_RANKS, MEMBER_ACTIVE_DAYS))
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
            'SELECT c."user", COUNT(DISTINCT c.card) AS uniques, '
            '       SUM(c.count) AS copies, '
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
