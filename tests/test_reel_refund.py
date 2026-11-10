"""
A reel and its card are committed together.

1. Anything that fails before the card is banked costs no reel -- the wishlist
   lookup, member mint, render and database grant all happen before or inside
   the transaction that spends it
2. A member card is minted only when its pull commits
3. Once the card is banked the pull happened
4. "Reel again" acknowledges the click before pulling, so a slow pull cannot
   outlive Discord's three-second window and lose its reply
5. If the reply still fails after the card is banked, the player is told the
   card is in their collection and the pull's rewards are still paid
"""

import asyncio
import os
import sys
import types
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from psycopg2 import extensions, sql

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import Commands.cards as cmd
from Helpers import card_copy as ctext
from Helpers import cards as cardlib

OWNER = 111111111111111111
CARD = {"slug": "charon", "name": "Charon", "tier": "rare"}
MEMBER = {"slug": "member-someign", "name": "SomeIgn", "tier": "Hydra",
          "rank": "Hydra", "member": True}


def unknown_interaction():
    """The exact error the expired "Reel again" reply raised in production."""
    return discord.NotFound(types.SimpleNamespace(status=404, reason="Not Found"),
                            {"code": 10062, "message": "Unknown interaction"})


class Wallet:
    """A stand-in for card_wallet, card_collection and card_members."""

    def __init__(self, reels=3, bait=0):
        self.reels, self.bait = reels, bait
        self.roll = CARD
        self.granted, self.minted = [], []
        self.logs = []

    def balance(self, uid):
        return self.reels + self.bait

    def prepare(self, uid, limit):
        cards = [dict(MEMBER if self.roll["tier"] == "member" else self.roll)
                 for _ in range(min(limit, self.balance(uid)))]
        return cardlib.ReelPlan(cards, [])

    def complete(self, uid, plan):
        count = len(plan.cards)
        if self.balance(uid) < count:
            return None
        spent_bait = min(count, self.bait)
        self.bait -= spent_bait
        self.reels -= count - spent_bait
        self.granted.extend(c["slug"] for c in plan.cards)
        self.minted.extend(c["slug"] for c in plan.cards if c.get("member"))
        return {"reels": self.reels, "bait_reels": self.bait,
                "total": self.reels + self.bait, "count": 1,
                "gained": 10, "pearls": count * 10}


def boom(*args, **kwargs):
    raise RuntimeError("database went away")


@pytest.fixture(autouse=True)
def block_configured_database(monkeypatch):
    def blocked():
        pytest.fail("Use a mocked DB or the explicit local test fixture")
    monkeypatch.setattr(cardlib, "DB", blocked)


@pytest.fixture
def wallet(monkeypatch):
    w = Wallet()
    monkeypatch.setattr(cardlib, "db_get_balance", w.balance)
    monkeypatch.setattr(cardlib, "db_prepare_reels", w.prepare)
    monkeypatch.setattr(cardlib, "db_complete_reels", w.complete)
    monkeypatch.setattr(cardlib, "pull_value", lambda card: 10)
    monkeypatch.setattr(cardlib, "tier_max_stars", lambda card: 3)
    monkeypatch.setattr(cmd, "card_file",
                        lambda *a, **k: types.SimpleNamespace(
                            filename="c.png", close=lambda: None))
    monkeypatch.setattr(cmd, "_card_embed", lambda *a, **k: "EMBED")
    monkeypatch.setattr(cmd, "log", lambda level, msg, **k: w.logs.append(msg))
    return w


# ── _do_reel: what a pull costs ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_good_pull_banks_the_card_and_refunds_nothing(wallet):
    embed, file, remaining, card = await cmd._do_reel(OWNER, "Player")
    assert embed == "EMBED" and card["slug"] == "charon"
    assert remaining == 2
    assert wallet.granted == ["charon"]


@pytest.mark.asyncio
async def test_no_reels_spends_nothing(wallet):
    wallet.reels = 0
    assert await cmd._do_reel(OWNER, "Player") == (None, None, "out", None)
    assert wallet.granted == []


@pytest.mark.asyncio
async def test_a_failed_render_costs_nothing(wallet, monkeypatch):
    monkeypatch.setattr(cmd, "card_file", boom)
    assert await cmd._do_reel(OWNER, "Player") == (None, None, "render", None)
    assert wallet.reels == 3
    assert wallet.granted == []


@pytest.mark.parametrize("step", ["db_prepare_reels", "db_complete_reels"])
@pytest.mark.asyncio
async def test_a_failure_before_the_atomic_bank_costs_nothing(
        wallet, monkeypatch, step):
    monkeypatch.setattr(cardlib, step, boom)
    assert await cmd._do_reel(OWNER, "Player") == (None, None, "failed", None)
    assert wallet.reels == 3
    assert wallet.granted == []


@pytest.mark.asyncio
async def test_a_failed_pull_does_not_consume_bait(wallet, monkeypatch):
    wallet.bait = 1
    monkeypatch.setattr(cardlib, "db_complete_reels", boom)
    await cmd._do_reel(OWNER, "Player")
    assert (wallet.bait, wallet.reels) == (1, 3)


@pytest.mark.asyncio
async def test_a_good_pull_consumes_bait_before_the_bank(wallet):
    wallet.bait = 1
    _, _, remaining, _ = await cmd._do_reel(OWNER, "Player")
    assert remaining == 3
    assert (wallet.bait, wallet.reels) == (0, 3)


@pytest.mark.asyncio
async def test_a_failed_pull_never_mints_the_member_card(wallet, monkeypatch):
    wallet.roll = {"slug": "member", "tier": "member"}
    monkeypatch.setattr(cmd, "card_file", boom)
    assert await cmd._do_reel(OWNER, "Player") == (None, None, "render", None)
    assert wallet.minted == []
    assert wallet.reels == 3


@pytest.mark.asyncio
async def test_a_good_member_pull_keeps_its_mint(wallet):
    wallet.roll = {"slug": "member", "tier": "member"}
    *_, card = await cmd._do_reel(OWNER, "Player")
    assert card["slug"] == "member-someign"
    assert wallet.granted == wallet.minted == ["member-someign"]


@pytest.mark.asyncio
async def test_a_banked_card_remains_spent(wallet, monkeypatch):
    monkeypatch.setattr(cmd, "_card_embed", boom)
    with pytest.raises(RuntimeError):
        await cmd._do_reel(OWNER, "Player")
    assert wallet.granted == ["charon"]
    assert wallet.reels == 2


@pytest.mark.asyncio
async def test_batch_renders_only_the_final_card(wallet, monkeypatch):
    wallet.reels = 15
    cards = [dict(CARD, slug=f"card-{i}") for i in range(10)]
    monkeypatch.setattr(cardlib, "db_prepare_reels",
                        lambda user, limit: cardlib.ReelPlan(cards, []))
    render = Mock(return_value=types.SimpleNamespace(filename="last.png"))
    monkeypatch.setattr(cmd, "card_file", render)

    embed, file, remaining, pulled = await cmd._do_reels(OWNER, "Player", 10)

    assert remaining == 5
    assert pulled == cards
    assert wallet.granted == [c["slug"] for c in cards]
    render.assert_called_once_with(cards[-1], None, 0, 3)


@pytest.mark.asyncio
async def test_batch_render_failure_spends_nothing(wallet, monkeypatch):
    wallet.reels, wallet.bait = 15, 2
    wallet.roll = {"tier": "member"}
    monkeypatch.setattr(cmd, "card_file", boom)
    assert await cmd._do_reels(OWNER, "Player", 10) == (None, None, "render", None)
    assert (wallet.reels, wallet.bait) == (15, 2)
    assert wallet.granted == wallet.minted == []


@pytest.mark.asyncio
async def test_rejected_batch_does_not_report_empty_while_reels_remain(wallet, monkeypatch):
    monkeypatch.setattr(cardlib, "db_complete_reels", lambda *a: None)
    assert await cmd._do_reels(OWNER, "Player", 10) == (None, None, "failed", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("pulled", [[], [CARD], [MEMBER, CARD, MEMBER]])
async def test_batch_checks_rewards_once_and_announces_each_limited(monkeypatch, pulled):
    owned = {CARD["slug"]: {"total": 2, "levels": {0: 2}}}
    collection = Mock(return_value=owned)
    milestones = Mock(return_value=[])
    monkeypatch.setattr(cardlib, "db_get_collection", collection)
    monkeypatch.setattr(cardlib, "check_milestones", milestones)
    channel = types.SimpleNamespace(send=AsyncMock())
    user = types.SimpleNamespace(id=OWNER, mention="Player")
    await cmd._announce_and_reward(channel, user, pulled)
    collection.assert_called_once_with(OWNER)
    milestones.assert_called_once_with(OWNER, owned)
    assert channel.send.await_count == sum(bool(c.get("member")) for c in pulled)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_notice", [0, 2])
async def test_notice_failure_does_not_block_rewards_or_later_notices(monkeypatch, failed_notice):
    events = []
    owned = {CARD["slug"]: {"total": 2, "levels": {0: 2}}}
    awards = [("unique", 25, 250), ("unique", 50, 600)]

    def pay(user_id, collection):
        events.append("paid")
        return awards

    milestones = Mock(side_effect=pay)
    monkeypatch.setattr(cardlib, "db_get_collection", lambda user: owned)
    monkeypatch.setattr(cardlib, "check_milestones", milestones)
    log = Mock()
    monkeypatch.setattr(cmd, "log", log)

    async def send(*, embed):
        events.append(embed.title)
        if len(events) - 2 == failed_notice:
            raise discord.HTTPException(
                types.SimpleNamespace(status=503, reason="Service Unavailable"),
                {"message": "simulated notice failure", "code": 0})

    channel = types.SimpleNamespace(send=AsyncMock(side_effect=send))
    user = types.SimpleNamespace(id=OWNER, mention="Player")
    await cmd._announce_and_reward(channel, user, [MEMBER, CARD, MEMBER])

    milestones.assert_called_once_with(OWNER, owned)
    assert events == ["paid", "Limited card found", "Limited card found",
                      "25 unique cards", "50 unique cards"]
    assert channel.send.await_count == 4
    log.assert_called_once()


@pytest.mark.asyncio
async def test_reward_failure_does_not_block_limited_notices(monkeypatch):
    monkeypatch.setattr(cardlib, "db_get_collection", lambda user: {})
    monkeypatch.setattr(cardlib, "check_milestones", Mock(side_effect=RuntimeError("unavailable")))
    log = Mock()
    monkeypatch.setattr(cmd, "log", log)
    channel = types.SimpleNamespace(send=AsyncMock())
    user = types.SimpleNamespace(id=OWNER, mention="Player")

    await cmd._announce_and_reward(channel, user, [MEMBER, CARD, MEMBER])

    assert channel.send.await_count == 2
    log.assert_called_once()


@pytest.fixture
def pull_db(_dev_db, monkeypatch):
    connection = _dev_db._connection
    connection.autocommit = True
    cursor = connection.cursor()
    for table in ("card_wallet", "card_collection", "card_members", "card_wishlist"):
        cursor.execute(f"CREATE TEMP TABLE IF NOT EXISTS {table} "
                       f"(LIKE public.{table} INCLUDING ALL)")
        cursor.execute(f"TRUNCATE {table}")
    cursor.close()
    connection.autocommit = False

    class TransactionDB:
        def __init__(self):
            self.connection = connection
            self.cursor = connection.cursor()

        def connect(self):
            pass

        def close(self):
            self.cursor.close()
            if connection.status != extensions.STATUS_READY:
                connection.rollback()

    monkeypatch.setattr(cardlib, "DB", TransactionDB)
    yield connection
    connection.rollback()
    connection.autocommit = True


def _seed_pull_wallet(connection, reels=1, bait=0):
    with connection.cursor() as cursor:
        cursor.execute(
            'INSERT INTO card_wallet ("user", reels, bait_reels, window_idx) '
            'VALUES (%s, %s, %s, %s)',
            (OWNER, reels, bait, cardlib.current_window()))
    connection.commit()


def test_pull_spend_card_and_pearls_commit_together(pull_db):
    _seed_pull_wallet(pull_db)
    result = cardlib.db_complete_reels(OWNER, cardlib.ReelPlan([CARD], []))
    assert result["total"] == 0
    assert result["count"] == 1
    with pull_db.cursor() as cursor:
        cursor.execute(
            'SELECT reels, total_reeled, pearls FROM card_wallet '
            'WHERE "user" = %s', (OWNER,))
        assert cursor.fetchone() == (0, 1, cardlib.pull_value(CARD))
        cursor.execute(
            'SELECT count FROM card_collection '
            'WHERE "user" = %s AND card = %s AND stars = 0',
            (OWNER, CARD["slug"]))
        assert cursor.fetchone() == (1,)


def test_failed_card_grant_rolls_back_the_reel(pull_db):
    _seed_pull_wallet(pull_db)
    with pytest.raises(Exception):
        cardlib.db_complete_reels(
            OWNER, cardlib.ReelPlan([dict(CARD, slug="x" * 65)], []))
    with pull_db.cursor() as cursor:
        cursor.execute(
            'SELECT reels, total_reeled, pearls FROM card_wallet '
            'WHERE "user" = %s', (OWNER,))
        assert cursor.fetchone() == (1, 0, 0)
        cursor.execute('SELECT count(*) FROM card_collection')
        assert cursor.fetchone() == (0,)


def test_batch_grants_duplicates_and_spends_bait_before_bank(pull_db):
    _seed_pull_wallet(pull_db, reels=6, bait=5)
    cards = [CARD] * 7 + [dict(CARD, slug="other", tier="unique")] * 3
    result = cardlib.db_complete_reels(OWNER, cardlib.ReelPlan(cards, []))
    assert result["total"] == 1
    assert result["count"] == 3
    assert result["gained"] == cardlib.pull_value(cards[-1])
    with pull_db.cursor() as cursor:
        cursor.execute('SELECT reels, bait_reels, total_reeled, pearls FROM card_wallet')
        assert cursor.fetchone() == (1, 0, 10, sum(cardlib.pull_value(c) for c in cards))
        cursor.execute('SELECT card, stars, count FROM card_collection ORDER BY card')
        assert cursor.fetchall() == [(CARD["slug"], 0, 7), ("other", 0, 3)]


def test_batch_refuses_changed_balance_without_partial_grants(pull_db):
    _seed_pull_wallet(pull_db, reels=2)
    assert cardlib.db_complete_reels(OWNER, cardlib.ReelPlan([CARD] * 3, [])) is None
    with pull_db.cursor() as cursor:
        cursor.execute('SELECT reels, total_reeled, pearls FROM card_wallet')
        assert cursor.fetchone() == (2, 0, 0)
        cursor.execute('SELECT count(*) FROM card_collection')
        assert cursor.fetchone() == (0,)


def test_prepare_uses_one_wishlist_and_stops_at_available_reels(pull_db, monkeypatch):
    _seed_pull_wallet(pull_db, reels=2, bait=1)
    with pull_db.cursor() as cursor:
        cursor.execute('INSERT INTO card_wishlist ("user", card) VALUES (%s, %s)',
                       (OWNER, CARD["slug"]))
    pull_db.commit()
    seen = []

    def roll(wishes=None):
        seen.append(wishes)
        return CARD

    monkeypatch.setattr(cardlib, "roll_card", roll)
    plan = cardlib.db_prepare_reels(OWNER, 10)
    assert plan.cards == [CARD] * 3
    assert seen == [{CARD["slug"]}] * 3
    assert all(wishes is seen[0] for wishes in seen)
    with pull_db.cursor() as cursor:
        cursor.execute('SELECT reels, bait_reels, total_reeled FROM card_wallet')
        assert cursor.fetchone() == (2, 1, 0)


def test_limited_candidates_are_unique_and_mint_only_on_commit(pull_db, monkeypatch):
    _seed_pull_wallet(pull_db, reels=3)
    candidates = [(7, "One", str(uuid.uuid4()), "Hydra"),
                  (8, "Two", str(uuid.uuid4()), "Dolphin")]
    reads = []

    def unminted(db):
        reads.append(True)
        return candidates.copy()

    monkeypatch.setattr(cardlib, "_unminted_members", unminted)
    monkeypatch.setattr(cardlib, "roll_card", lambda wishes=None: {"tier": "member"})
    monkeypatch.setattr(cardlib, "roll_in_tier", lambda tier, wishes: CARD)
    plan = cardlib.db_prepare_reels(OWNER, 10)
    assert len(reads) == 1
    assert len(plan.members) == 2
    assert {c["slug"] for c in plan.cards} == {"member-one", "member-two", CARD["slug"]}
    with pull_db.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM card_members')
        assert cursor.fetchone() == (0,)
    assert cardlib.db_complete_reels(OWNER, plan)["total"] == 0
    with pull_db.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM card_members WHERE owner = %s', (OWNER,))
        assert cursor.fetchone() == (2,)


def test_limited_conflict_rolls_back_the_entire_batch(pull_db):
    _seed_pull_wallet(pull_db, reels=2)
    member = (7, "SomeIgn", str(uuid.uuid4()), "Hydra")
    with pull_db.cursor() as cursor:
        cursor.execute(
            'INSERT INTO card_members (slug, discord_id, ign, rank, owner) '
            'VALUES (%s, %s, %s, %s, %s)',
            (MEMBER["slug"], member[0], member[1], member[3], OWNER + 1))
    pull_db.commit()
    with pytest.raises(ValueError, match="minted by another pull"):
        cardlib.db_complete_reels(OWNER, cardlib.ReelPlan([MEMBER, CARD], [member]))
    with pull_db.cursor() as cursor:
        cursor.execute('SELECT reels, total_reeled, pearls FROM card_wallet')
        assert cursor.fetchone() == (2, 0, 0)
        cursor.execute('SELECT count(*) FROM card_collection')
        assert cursor.fetchone() == (0,)


def test_collection_failure_rolls_back_the_limited_mint(pull_db):
    _seed_pull_wallet(pull_db, reels=2)
    member = (7, "SomeIgn", str(uuid.uuid4()), "Hydra")
    with pytest.raises(Exception):
        cardlib.db_complete_reels(OWNER, cardlib.ReelPlan(
            [MEMBER, dict(CARD, slug="x" * 65)], [member]))
    with pull_db.cursor() as cursor:
        cursor.execute('SELECT reels, total_reeled, pearls FROM card_wallet')
        assert cursor.fetchone() == (2, 0, 0)
        cursor.execute('SELECT count(*) FROM card_members')
        assert cursor.fetchone() == (0,)


@pytest.mark.parametrize("operation,bait", [("reels", 0), ("reels", 6), ("daily", 0)])
def test_concurrent_claims_cannot_overspend_or_reuse_a_save(monkeypatch, operation, bait):
    from tests.conftest import _dev_connection

    admin = _dev_connection()
    if admin is None:
        pytest.skip("local dev database unavailable")
    admin.autocommit = True
    schema = sql.Identifier(f"reel_test_{uuid.uuid4().hex}")
    barrier = Barrier(2)

    class ConcurrentDB:
        def connect(self):
            self.connection = _dev_connection()
            self.cursor = self.connection.cursor()
            self.cursor.execute(sql.SQL('SET search_path TO {}').format(schema))
            self.connection.commit()
            barrier.wait(timeout=5)

        def close(self):
            self.connection.close()

    try:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL('CREATE SCHEMA {}').format(schema))
            for table in ("card_wallet", "card_collection"):
                cursor.execute(sql.SQL('CREATE TABLE {}.{} (LIKE public.{} INCLUDING ALL)')
                               .format(schema, sql.Identifier(table), sql.Identifier(table)))
            cursor.execute(sql.SQL(
                'INSERT INTO {}.card_wallet ("user", reels, bait_reels, tank_tier, window_idx) '
                'VALUES (%s, %s, %s, %s, %s)').format(schema),
                (OWNER, 10 - bait, bait, cardlib.MAX_TANK, cardlib.current_window()))
            if operation == "daily":
                cursor.execute(sql.SQL(
                    'UPDATE {}.card_wallet SET tank_tier = 5, streak = 8, '
                    'last_daily = CURRENT_DATE - 2').format(schema))
        monkeypatch.setattr(cardlib, "DB", ConcurrentDB)
        plan = cardlib.ReelPlan([CARD] * 6, [])
        with ThreadPoolExecutor(max_workers=2) as executor:
            if operation == "daily":
                results = list(executor.map(lambda _: cardlib.db_claim_daily(OWNER), range(2)))
            else:
                results = list(executor.map(lambda _: cardlib.db_complete_reels(OWNER, plan), range(2)))
        with admin.cursor() as cursor:
            if operation == "daily":
                assert sum(result["ok"] for result in results) == 1
                assert sum(result.get("streak_saved", False) for result in results) == 1
                cursor.execute(sql.SQL(
                    'SELECT streak, bait_reels, pearls, CURRENT_DATE - last_streak_save '
                    'FROM {}.card_wallet').format(schema))
                assert cursor.fetchone() == (9, 5, 100, 0)
            else:
                assert sum(result is not None for result in results) == 1
                cursor.execute(sql.SQL(
                    'SELECT reels + bait_reels, total_reeled FROM {}.card_wallet').format(schema))
                assert cursor.fetchone() == (4, 6)
                cursor.execute(sql.SQL('SELECT count FROM {}.card_collection').format(schema))
                assert cursor.fetchall() == [(6,)]
    finally:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(schema))
        admin.close()


@pytest.mark.parametrize("tank_tier", range(1, cardlib.MAX_TANK + 1))
@pytest.mark.parametrize("old_streak,days_ago,expected_streak,reward", [
    (0, None, 1, 3),
    (1, 1, 2, 4),
    (2, 1, 3, 5),
    (20, 1, 21, 5),
    (20, 2, 1, 3),
])
def test_daily_bait_bonus_at_max_tank(pull_db, tank_tier, old_streak,
                                    days_ago, expected_streak, reward):
    cap = cardlib.bank_cap(tank_tier)
    _seed_pull_wallet(pull_db, reels=cap)
    with pull_db.cursor() as cursor:
        cursor.execute(
            'UPDATE card_wallet SET tank_tier = %s, streak = %s, '
            'last_daily = CURRENT_DATE - %s::integer WHERE "user" = %s',
            (tank_tier, old_streak, days_ago, OWNER))
    pull_db.commit()

    result = cardlib.db_claim_daily(OWNER)

    saved = days_ago == 2 and tank_tier >= cardlib.STREAK_SAVE_TIER
    if saved:
        expected_streak, reward = 21, 5
    expected_bait = reward + (tank_tier == cardlib.MAX_TANK)
    assert result["ok"] is True
    assert result["streak"] == expected_streak
    assert result["bait_reels"] == expected_bait
    assert result["reels"] == cap
    assert result["total_reels"] == cap + expected_bait
    assert result["gained"] == cardlib.daily_tier(expected_streak)["pearls"]
    assert result["streak_saved"] is saved
    assert cardlib.db_claim_daily(OWNER)["reason"] == "claimed"
    with pull_db.cursor() as cursor:
        cursor.execute('SELECT bait_reels, pearls FROM card_wallet WHERE "user" = %s',
                       (OWNER,))
        assert cursor.fetchone() == (expected_bait, result["pearls"])


def test_max_tank_bonus_does_not_bypass_unspent_bait(pull_db):
    _seed_pull_wallet(pull_db, bait=1)
    with pull_db.cursor() as cursor:
        cursor.execute(
            'UPDATE card_wallet SET tank_tier = %s, last_daily = CURRENT_DATE - 1 '
            'WHERE "user" = %s', (cardlib.MAX_TANK, OWNER))
    pull_db.commit()

    assert cardlib.db_claim_daily(OWNER) == {
        "ok": False, "bait_reels": 1, "reason": "unspent"}


@pytest.mark.parametrize("tier,days_ago,old_streak,save_days_ago,expected,saved", [
    (4, 2, 8, None, 1, False),
    (5, 2, 8, None, 9, True),
    (5, 2, 1, None, 2, True),
    (5, 2, 2, None, 3, True),
    (6, 2, 8, None, 9, True),
    (7, 2, 8, None, 9, True),
    (5, 2, 8, 6, 1, False),
    (5, 2, 8, 7, 9, True),
    (5, 2, 8, 8, 9, True),
    (5, 3, 8, None, 1, False),
    (5, 1, 8, None, 9, False),
    (5, 1, 8, 1, 9, False),
    (5, None, 0, None, 1, False),
    (5, 2, 0, None, 1, False),
])
def test_daily_streak_save(pull_db, tier, days_ago, old_streak,
                          save_days_ago, expected, saved):
    _seed_pull_wallet(pull_db)
    with pull_db.cursor() as cursor:
        cursor.execute(
            'UPDATE card_wallet SET tank_tier = %s, streak = %s, '
            'last_daily = CURRENT_DATE - %s::integer, '
            'last_streak_save = CURRENT_DATE - %s::integer',
            (tier, old_streak, days_ago, save_days_ago))
    pull_db.commit()

    result = cardlib.db_claim_daily(OWNER)

    assert result["ok"] is True
    assert result["streak"] == expected
    assert result["streak_saved"] is saved
    reward = cardlib.daily_tier(expected)
    assert result["bait_reels"] == reward["reels"] + (tier == cardlib.MAX_TANK)
    assert result["pearls"] == result["gained"] == reward["pearls"]
    with pull_db.cursor() as cursor:
        cursor.execute('SELECT CURRENT_DATE - last_streak_save FROM card_wallet')
        assert cursor.fetchone()[0] == (0 if saved else save_days_ago)


@pytest.mark.parametrize("reason", ["claimed", "unspent"])
def test_blocked_daily_does_not_spend_a_streak_save(pull_db, reason):
    _seed_pull_wallet(pull_db, bait=1 if reason == "unspent" else 0)
    days_ago = 2 if reason == "unspent" else 0
    with pull_db.cursor() as cursor:
        cursor.execute(
            'UPDATE card_wallet SET tank_tier = 5, streak = 8, '
            'last_daily = CURRENT_DATE - %s, '
            'last_streak_save = CURRENT_DATE - 7', (days_ago,))
    pull_db.commit()

    assert cardlib.db_claim_daily(OWNER)["reason"] == reason
    with pull_db.cursor() as cursor:
        cursor.execute('SELECT streak, CURRENT_DATE - last_streak_save FROM card_wallet')
        assert cursor.fetchone() == (8, 7)


def test_streak_save_migration_is_additive_and_idempotent(pull_db):
    _seed_pull_wallet(pull_db, reels=3, bait=1)
    migration = next(stmt for stmt in cardlib.SCHEMA
                     if stmt.startswith('ALTER TABLE card_wallet ADD COLUMN IF NOT EXISTS last_streak_save'))
    with pull_db.cursor() as cursor:
        cursor.execute('ALTER TABLE card_wallet DROP COLUMN last_streak_save')
        cursor.execute(migration)
        cursor.execute(migration)
        cursor.execute('SELECT reels, bait_reels, last_streak_save FROM card_wallet')
        assert cursor.fetchone() == (3, 1, None)


@pytest.mark.asyncio
async def test_bait_reports_when_the_streak_was_saved(monkeypatch):
    monkeypatch.setattr(cardlib, "db_claim_daily", lambda user: {
        "ok": True, "streak": 9, "bait_reels": 5, "gained": 100,
        "streak_saved": True})
    monkeypatch.setattr(cardlib, "db_next_daily_reset", lambda: 0)
    ctx = types.SimpleNamespace(author=types.SimpleNamespace(id=OWNER),
                                defer=AsyncMock(),
                                followup=types.SimpleNamespace(send=AsyncMock()))
    await cmd.Cards.bait.callback(cmd.Cards.__new__(cmd.Cards), ctx)
    assert ctx.followup.send.call_args.kwargs["embed"].footer.text == "Max Streak (saved)"


def test_each_failure_has_its_own_words():
    assert cmd._failure_text("render") == ctext.render_failed()
    assert cmd._failure_text("failed") == ctext.reel_failed()
    assert "No Reel spent" in ctext.reel_failed()


# ── "Reel again": the reply ──────────────────────────────────────────────────

class Click:
    """A button interaction that records what the handler did, in order.

    After defer() the original response is spent, so any response.* call is a
    bug: the real library would raise InteractionResponded.
    """

    def __init__(self, events, edit_error=None):
        self.events = events
        self.edit_error = edit_error
        self.user = types.SimpleNamespace(id=OWNER, mention=f"<@{OWNER}>")
        self.channel = object()
        self.edits, self.followups = [], []
        self.response = types.SimpleNamespace(
            defer=self._defer, edit_message=self._spent, send_message=self._spent)
        self.followup = types.SimpleNamespace(send=self._followup)

    async def _defer(self, **kwargs):
        self.events.append("defer")

    async def _spent(self, *args, **kwargs):
        raise AssertionError("response.* used after the click was deferred")

    async def edit_original_response(self, **kwargs):
        self.events.append("edit")
        if self.edit_error is not None and "embed" in kwargs:
            raise self.edit_error
        self.edits.append(kwargs)

    async def _followup(self, *args, **kwargs):
        self.events.append("followup")
        self.followups.append(kwargs)


@pytest.fixture
def events(monkeypatch):
    log = []
    monkeypatch.setattr(cmd, "_history_content", lambda history: "history")
    monkeypatch.setattr(cardlib, "next_refresh_ts", lambda: 0)
    monkeypatch.setattr(cmd, "log", lambda *a, **k: log.append("log"))

    async def reward(channel, user, card):
        log.append("reward")
    monkeypatch.setattr(cmd, "_announce_and_reward", reward)
    return log


def pulling(monkeypatch, events, result):
    async def fake_do_reel(user_id, who):
        events.append("pull")
        return result
    monkeypatch.setattr(cmd, "_do_reel", fake_do_reel)


def reel_view(card=CARD):
    return cmd.ReelView(cmd.ReelCoordinator(), OWNER, "Player", card)


def test_history_shows_the_last_twelve_and_counts_earlier_pulls(monkeypatch):
    monkeypatch.setattr(cardlib, "next_refresh_ts", lambda: 0)
    history = [dict(CARD, name=f"Card {i:02d}") for i in range(35)]
    content = cmd._history_content(history)
    assert content.splitlines()[:-1] == ["-# +23 earlier", *[
        f"-# Rare: Card {i:02d}" for i in range(23, 35)]]


def test_short_history_does_not_need_an_earlier_count(monkeypatch):
    monkeypatch.setattr(cardlib, "next_refresh_ts", lambda: 0)
    history = [dict(CARD, name=f"Card {i:02d}") for i in range(12)]
    assert cmd._history_content(history).splitlines()[:-1] == [
        f"-# Rare: Card {i:02d}" for i in range(12)]


@pytest.mark.asyncio
async def test_reel_again_acknowledges_before_it_pulls(monkeypatch, events):
    pulling(monkeypatch, events, ("EMBED", "FILE", 2, CARD))
    view = reel_view()
    await view.reel_again.callback(Click(events))
    assert events.index("defer") < events.index("pull")
    assert events[0] == "defer"


@pytest.mark.asyncio
async def test_reel_again_shows_the_card_through_the_deferred_reply(monkeypatch, events):
    pulling(monkeypatch, events, ("EMBED", "FILE", 2, CARD))
    view = reel_view({"slug": "gale", "name": "Gale"})
    click = Click(events)
    await view.reel_again.callback(click)
    edit = click.edits[-1]
    assert edit["embed"] == "EMBED" and edit["file"] == "FILE"
    assert edit["attachments"] == []
    assert view.current == CARD and view.history[-1]["slug"] == "gale"
    assert events[-1] == "reward"


@pytest.mark.asyncio
async def test_a_reply_lost_after_the_bank_still_reaches_the_player(monkeypatch, events):
    pulling(monkeypatch, events, ("EMBED", "FILE", 0, CARD))
    view = reel_view()
    click = Click(events, edit_error=unknown_interaction())
    await view.reel_again.callback(click)
    [told] = click.followups
    assert told["ephemeral"] is True
    assert told["embed"].description == ctext.pull_unshown("Charon")
    assert "reward" in events


@pytest.mark.asyncio
async def test_a_failed_pull_says_nothing_was_spent(monkeypatch, events):
    pulling(monkeypatch, events, (None, None, "failed", None))
    click = Click(events)
    await reel_view().reel_again.callback(click)
    [told] = click.followups
    assert told["embed"].description == ctext.reel_failed()
    assert "reward" not in events


@pytest.mark.asyncio
async def test_out_of_reels_disables_the_button(monkeypatch, events):
    pulling(monkeypatch, events, (None, None, "out", None))
    view = reel_view()
    click = Click(events)
    await view.reel_again.callback(click)
    assert view.reel_again.disabled is True
    assert view.reel_ten.disabled is True
    assert events[:3] == ["defer", "edit", "pull"]
    assert click.followups[0]["embed"].description == ctext.no_reels(0)


@pytest.mark.asyncio
async def test_reel_ten_pulls_ten_and_shows_the_last(monkeypatch, events):
    cards = [{"slug": f"card-{n}", "name": f"Card {n}", "tier": "rare"}
             for n in range(10)]
    async def fake_do_reels(user_id, who, limit):
        assert limit == 10
        events.append("pull")
        return ("EMBED-card-9", "FILE-card-9", 2, cards)

    monkeypatch.setattr(cmd, "_do_reels", fake_do_reels)
    view = reel_view()
    click = Click(events)
    await view.reel_ten.callback(click)

    assert events.count("pull") == 1
    assert events.count("reward") == 1
    edit = click.edits[-1]
    assert edit["embed"] == "EMBED-card-9"
    assert edit["file"] == "FILE-card-9"
    assert view.current == cards[-1]
    assert view.history == [CARD, *cards[:-1]]
    assert view.reel_ten.disabled is False


@pytest.mark.asyncio
async def test_reel_ten_uses_every_reel_when_fewer_than_ten_remain(
        monkeypatch, events):
    cards = [dict(CARD, slug=f"card-{n}") for n in range(3)]

    async def fake_do_reels(user_id, who, limit):
        events.append("pull")
        return "EMBED", "FILE", 0, cards

    monkeypatch.setattr(cmd, "_do_reels", fake_do_reels)
    view = reel_view()
    click = Click(events)
    await view.reel_ten.callback(click)

    assert events.count("pull") == 1
    assert view.current == cards[-1]
    assert view.reel_again.disabled is True
    assert view.reel_ten.disabled is True


@pytest.mark.asyncio
async def test_overlapping_reel_clicks_run_in_order(monkeypatch, events):
    coordinator = cmd.ReelCoordinator()
    view = cmd.ReelView(coordinator, OWNER, "Player", CARD)
    active = 0
    most_active = 0
    calls = 0

    async def fake_do_reel(user_id, who):
        nonlocal active, most_active, calls
        assert view.reel_again.disabled is True
        active += 1
        most_active = max(most_active, active)
        await asyncio.sleep(0.01)
        card = dict(CARD, slug=f"card-{calls}")
        remaining = 1 - calls
        calls += 1
        active -= 1
        return f"EMBED-{card['slug']}", "FILE", remaining, card

    monkeypatch.setattr(cmd, "_do_reel", fake_do_reel)
    first, second = Click(events), Click(events)
    await asyncio.gather(
        view.reel_again.callback(first),
        view.reel_again.callback(second))

    assert calls == 2
    assert most_active == 1
    assert view.current["slug"] == "card-1"
    assert view.reel_again.disabled is True
    assert view.reel_ten.disabled is True


@pytest.mark.asyncio
async def test_announcements_do_not_hold_the_reel_lock(monkeypatch, events):
    view = reel_view()
    pulling(monkeypatch, events, ("EMBED", "FILE", 1, CARD))

    async def reward(channel, user, cards):
        assert not view.coordinator.lock(OWNER).locked()
        assert cards == [CARD]

    monkeypatch.setattr(cmd, "_announce_and_reward", reward)
    await view.reel_again.callback(Click(events))


@pytest.mark.asyncio
async def test_last_reel_disables_every_live_view(monkeypatch, events):
    coordinator = cmd.ReelCoordinator()
    current = cmd.ReelView(coordinator, OWNER, "Player", CARD)
    other = cmd.ReelView(coordinator, OWNER, "Player", CARD)
    pulling(monkeypatch, events, ("EMBED", "FILE", 0, CARD))

    await current.reel_again.callback(Click(events))

    assert current.reel_again.disabled is True
    assert current.reel_ten.disabled is True
    assert other.reel_again.disabled is True
    assert other.reel_ten.disabled is True
