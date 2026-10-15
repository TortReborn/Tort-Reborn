"""
A reel and its card are committed together.

1. Anything that fails before the card is banked costs no reel -- the wishlist
   lookup, member mint, render and database grant all happen before or inside
   the transaction that spends it
2. A member card minted for a pull that then failed is released, so the
   member does not end up claimed by a card nobody holds
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

import discord
import pytest
from psycopg2 import extensions

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import Commands.cards as cmd
from Helpers import card_copy as ctext
from Helpers import cards as cardlib

OWNER = 111111111111111111
CARD = {"slug": "charon", "name": "Charon", "tier": "rare"}
MEMBER = {"slug": "member-someign", "name": "SomeIgn", "tier": "Hydra",
          "member": True}


def unknown_interaction():
    """The exact error the expired "Reel again" reply raised in production."""
    return discord.NotFound(types.SimpleNamespace(status=404, reason="Not Found"),
                            {"code": 10062, "message": "Unknown interaction"})


class Wallet:
    """A stand-in for card_wallet, card_collection and card_members."""

    def __init__(self, reels=3, bait=0):
        self.reels, self.bait = reels, bait
        self.roll = CARD
        self.granted, self.minted, self.released = [], [], []
        self.logs = []

    def balance(self, uid):
        return self.reels + self.bait

    def complete(self, uid, slug, pearls=0):
        if self.reels + self.bait == 0:
            return None
        if self.bait > 0:
            self.bait -= 1
        else:
            self.reels -= 1
        self.granted.append(slug)
        return {"reels": self.reels, "bait_reels": self.bait,
                "total": self.reels + self.bait, "count": 1,
                "gained": pearls, "pearls": pearls}

    def mint(self, uid):
        self.minted.append(MEMBER["slug"])
        return dict(MEMBER)

    def release(self, slug, uid):
        self.released.append(slug)
        return True

def boom(*args, **kwargs):
    raise RuntimeError("database went away")


@pytest.fixture
def wallet(monkeypatch):
    w = Wallet()
    monkeypatch.setattr(cardlib, "db_get_balance", w.balance)
    monkeypatch.setattr(cardlib, "db_complete_pull", w.complete)
    monkeypatch.setattr(cardlib, "db_get_wishes", lambda uid: [])
    monkeypatch.setattr(cardlib, "roll_card",
                        lambda wishes=None, rng=None: dict(w.roll))
    monkeypatch.setattr(cardlib, "db_mint_member_card", w.mint)
    monkeypatch.setattr(cardlib, "db_release_member_card", w.release)
    monkeypatch.setattr(cardlib, "pull_value", lambda card: 10)
    monkeypatch.setattr(cardlib, "tier_max_stars", lambda card: 3)
    monkeypatch.setattr(cmd, "card_file",
                        lambda *a, **k: types.SimpleNamespace(filename="c.png"))
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


@pytest.mark.parametrize("step", ["db_get_wishes", "db_complete_pull"])
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
    monkeypatch.setattr(cardlib, "db_complete_pull", boom)
    await cmd._do_reel(OWNER, "Player")
    assert (wallet.bait, wallet.reels) == (1, 3)


@pytest.mark.asyncio
async def test_a_good_pull_consumes_bait_before_the_bank(wallet):
    wallet.bait = 1
    _, _, remaining, _ = await cmd._do_reel(OWNER, "Player")
    assert remaining == 3
    assert (wallet.bait, wallet.reels) == (0, 3)


@pytest.mark.asyncio
async def test_a_failed_pull_releases_the_member_card_it_minted(wallet, monkeypatch):
    wallet.roll = {"slug": "member", "tier": "member"}
    monkeypatch.setattr(cmd, "card_file", boom)
    assert await cmd._do_reel(OWNER, "Player") == (None, None, "render", None)
    assert wallet.minted == ["member-someign"]
    assert wallet.released == ["member-someign"]
    assert wallet.reels == 3


@pytest.mark.asyncio
async def test_a_good_member_pull_keeps_its_mint(wallet):
    wallet.roll = {"slug": "member", "tier": "member"}
    *_, card = await cmd._do_reel(OWNER, "Player")
    assert card["slug"] == "member-someign"
    assert wallet.granted == ["member-someign"] and wallet.released == []


@pytest.mark.asyncio
async def test_a_banked_card_remains_spent(wallet, monkeypatch):
    monkeypatch.setattr(cmd, "_card_embed", boom)
    with pytest.raises(RuntimeError):
        await cmd._do_reel(OWNER, "Player")
    assert wallet.granted == ["charon"]
    assert wallet.reels == 2


# ── db_complete_pull: the real transaction, on TEMP tables ──────────────────

@pytest.fixture
def pull_db(_dev_db, monkeypatch):
    connection = _dev_db._connection
    connection.autocommit = True
    cursor = connection.cursor()
    for table in ("card_wallet", "card_collection"):
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
    result = cardlib.db_complete_pull(OWNER, CARD["slug"], 10)
    assert result["total"] == 0
    assert result["count"] == 1
    with pull_db.cursor() as cursor:
        cursor.execute(
            'SELECT reels, total_reeled, pearls FROM card_wallet '
            'WHERE "user" = %s', (OWNER,))
        assert cursor.fetchone() == (0, 1, 10)
        cursor.execute(
            'SELECT count FROM card_collection '
            'WHERE "user" = %s AND card = %s AND stars = 0',
            (OWNER, CARD["slug"]))
        assert cursor.fetchone() == (1,)


def test_failed_card_grant_rolls_back_the_reel(pull_db):
    _seed_pull_wallet(pull_db)
    with pytest.raises(Exception):
        cardlib.db_complete_pull(OWNER, "x" * 65, 10)
    with pull_db.cursor() as cursor:
        cursor.execute(
            'SELECT reels, total_reeled, pearls FROM card_wallet '
            'WHERE "user" = %s', (OWNER,))
        assert cursor.fetchone() == (1, 0, 0)
        cursor.execute('SELECT count(*) FROM card_collection')
        assert cursor.fetchone() == (0,)


# ── db_release_member_card: the real SQL, on TEMP tables ─────────────────────
@pytest.fixture
def card_db(_dev_db, monkeypatch):
    """TEMP card tables shadowing the real ones on the local dev connection, so
    the release runs its real DELETE and nothing in the database is written."""
    cur = _dev_db.cursor
    for table in ("card_members", "card_collection"):
        cur.execute(f"CREATE TEMP TABLE IF NOT EXISTS {table} "
                    f"(LIKE public.{table} INCLUDING ALL)")
        cur.execute(f"TRUNCATE {table}")
    monkeypatch.setattr(cardlib, "DB", lambda: _dev_db)
    return cur


def _mint(cur, slug="member-someign", owner=OWNER):
    cur.execute("INSERT INTO card_members (slug, discord_id, ign, rank, owner) "
                "VALUES (%s, 7, 'SomeIgn', 'Hydra', %s)", (slug, owner))


def _members(cur):
    cur.execute("SELECT slug FROM card_members")
    return [r[0] for r in cur.fetchall()]


def test_release_frees_a_mint_that_never_landed(card_db):
    _mint(card_db)
    assert cardlib.db_release_member_card("member-someign", OWNER) is True
    assert _members(card_db) == []


def test_release_keeps_a_mint_that_landed_in_a_collection(card_db):
    _mint(card_db)
    card_db.execute('INSERT INTO card_collection ("user", card) VALUES (%s, %s)',
                    (OWNER, "member-someign"))
    assert cardlib.db_release_member_card("member-someign", OWNER) is False
    assert _members(card_db) == ["member-someign"]


def test_release_never_touches_another_owners_card(card_db):
    _mint(card_db, owner=999)
    assert cardlib.db_release_member_card("member-someign", OWNER) is False
    assert _members(card_db) == ["member-someign"]


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
    cards_to_pull = iter(cards)
    remaining = iter(range(11, 1, -1))

    async def fake_do_reel(user_id, who):
        card = next(cards_to_pull)
        events.append("pull")
        return (f"EMBED-{card['slug']}", f"FILE-{card['slug']}",
                next(remaining), card)

    monkeypatch.setattr(cmd, "_do_reel", fake_do_reel)
    view = reel_view()
    click = Click(events)
    await view.reel_ten.callback(click)

    assert events.count("pull") == 10
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

    async def fake_do_reel(user_id, who):
        card = cards[len([event for event in events if event == "pull"])]
        events.append("pull")
        return "EMBED", "FILE", len(cards) - events.count("pull"), card

    monkeypatch.setattr(cmd, "_do_reel", fake_do_reel)
    view = reel_view()
    click = Click(events)
    await view.reel_ten.callback(click)

    assert events.count("pull") == 3
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
