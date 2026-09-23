"""
A reel is only ever spent on a card the player gets and can see.

1. Anything that fails before the card is banked refunds the reel -- the
   wishlist lookup, the member mint, the render, the grant itself -- and a
   bait reel goes back to the bait pocket it came from
2. A member card minted for a pull that then failed is released, so the
   member does not end up claimed by a card nobody holds
3. Once the card is banked nothing is refunded: the pull happened
4. "Reel again" acknowledges the click before pulling, so a slow pull cannot
   outlive Discord's three-second window and lose its reply
5. If the reply still fails after the card is banked, the player is told the
   card is in their collection and the pull's rewards are still paid
"""

import os
import sys
import types

import discord
import pytest

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
        self.granted, self.refunds, self.minted, self.released = [], [], [], []
        self.logs = []

    def spend(self, uid):
        if self.reels + self.bait == 0:
            return None
        used_bait = self.bait > 0
        if used_bait:
            self.bait -= 1
        else:
            self.reels -= 1
        return {"reels": self.reels, "bait_reels": self.bait,
                "total": self.reels + self.bait, "used_bait": used_bait}

    def refund(self, uid, bait=False):
        self.refunds.append(bait)
        if bait:
            self.bait += 1
        else:
            self.reels += 1

    def mint(self, uid):
        self.minted.append(MEMBER["slug"])
        return dict(MEMBER)

    def release(self, slug, uid):
        self.released.append(slug)
        return True

    def grant(self, uid, slug, pearls=0):
        self.granted.append(slug)
        return {"count": 1, "gained": pearls, "pearls": pearls}


def boom(*args, **kwargs):
    raise RuntimeError("database went away")


@pytest.fixture
def wallet(monkeypatch):
    w = Wallet()
    monkeypatch.setattr(cardlib, "db_spend_reel", w.spend)
    monkeypatch.setattr(cardlib, "db_refund_reel", w.refund)
    monkeypatch.setattr(cardlib, "db_get_wishes", lambda uid: [])
    monkeypatch.setattr(cardlib, "roll_card",
                        lambda wishes=None, rng=None: dict(w.roll))
    monkeypatch.setattr(cardlib, "db_mint_member_card", w.mint)
    monkeypatch.setattr(cardlib, "db_release_member_card", w.release)
    monkeypatch.setattr(cardlib, "pull_value", lambda card: 10)
    monkeypatch.setattr(cardlib, "tier_max_stars", lambda card: 3)
    monkeypatch.setattr(cardlib, "db_add_card", w.grant)
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
    assert wallet.refunds == []


@pytest.mark.asyncio
async def test_no_reels_spends_nothing(wallet):
    wallet.reels = 0
    assert await cmd._do_reel(OWNER, "Player") == (None, None, "out", None)
    assert wallet.refunds == [] and wallet.granted == []


@pytest.mark.asyncio
async def test_a_failed_render_refunds(wallet, monkeypatch):
    monkeypatch.setattr(cmd, "card_file", boom)
    assert await cmd._do_reel(OWNER, "Player") == (None, None, "render", None)
    assert wallet.refunds == [False] and wallet.reels == 3
    assert wallet.granted == []


@pytest.mark.parametrize("step", ["db_get_wishes", "db_add_card"])
@pytest.mark.asyncio
async def test_a_failure_before_the_bank_refunds(wallet, monkeypatch, step):
    monkeypatch.setattr(cardlib, step, boom)
    assert await cmd._do_reel(OWNER, "Player") == (None, None, "failed", None)
    assert wallet.refunds == [False] and wallet.reels == 3
    assert wallet.granted == []


@pytest.mark.asyncio
async def test_a_bait_reel_goes_back_to_the_bait_pocket(wallet, monkeypatch):
    wallet.bait = 1
    monkeypatch.setattr(cardlib, "db_add_card", boom)
    await cmd._do_reel(OWNER, "Player")
    assert wallet.refunds == [True]
    assert (wallet.bait, wallet.reels) == (1, 3)


@pytest.mark.asyncio
async def test_a_failed_pull_releases_the_member_card_it_minted(wallet, monkeypatch):
    wallet.roll = {"slug": "member", "tier": "member"}
    monkeypatch.setattr(cmd, "card_file", boom)
    assert await cmd._do_reel(OWNER, "Player") == (None, None, "render", None)
    assert wallet.minted == ["member-someign"]
    assert wallet.released == ["member-someign"]
    assert wallet.refunds == [False]


@pytest.mark.asyncio
async def test_a_good_member_pull_keeps_its_mint(wallet):
    wallet.roll = {"slug": "member", "tier": "member"}
    *_, card = await cmd._do_reel(OWNER, "Player")
    assert card["slug"] == "member-someign"
    assert wallet.granted == ["member-someign"] and wallet.released == []


@pytest.mark.asyncio
async def test_a_banked_card_is_never_refunded(wallet, monkeypatch):
    monkeypatch.setattr(cmd, "_card_embed", boom)
    with pytest.raises(RuntimeError):
        await cmd._do_reel(OWNER, "Player")
    assert wallet.granted == ["charon"]
    assert wallet.refunds == []


@pytest.mark.asyncio
async def test_a_refund_that_fails_is_logged_with_what_is_owed(wallet, monkeypatch):
    monkeypatch.setattr(cardlib, "db_add_card", boom)
    monkeypatch.setattr(cardlib, "db_refund_reel", boom)
    assert await cmd._do_reel(OWNER, "Player") == (None, None, "failed", None)
    assert any("owed one reel" in m and str(OWNER) in m for m in wallet.logs)


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
    assert "refunded" in ctext.reel_failed()


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
        if self.edit_error is not None:
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


@pytest.mark.asyncio
async def test_reel_again_acknowledges_before_it_pulls(monkeypatch, events):
    pulling(monkeypatch, events, ("EMBED", "FILE", 2, CARD))
    view = cmd.ReelView(OWNER, "Player", CARD)
    await view.reel_again.callback(Click(events))
    assert events.index("defer") < events.index("pull")
    assert events[0] == "defer"


@pytest.mark.asyncio
async def test_reel_again_shows_the_card_through_the_deferred_reply(monkeypatch, events):
    pulling(monkeypatch, events, ("EMBED", "FILE", 2, CARD))
    view = cmd.ReelView(OWNER, "Player", {"slug": "gale", "name": "Gale"})
    click = Click(events)
    await view.reel_again.callback(click)
    [edit] = click.edits
    assert edit["embed"] == "EMBED" and edit["file"] == "FILE"
    assert edit["attachments"] == []
    assert view.current == CARD and view.history[-1]["slug"] == "gale"
    assert events[-1] == "reward"


@pytest.mark.asyncio
async def test_a_reply_lost_after_the_bank_still_reaches_the_player(monkeypatch, events):
    pulling(monkeypatch, events, ("EMBED", "FILE", 0, CARD))
    view = cmd.ReelView(OWNER, "Player", CARD)
    click = Click(events, edit_error=unknown_interaction())
    await view.reel_again.callback(click)
    [told] = click.followups
    assert told["ephemeral"] is True
    assert told["embed"].description == ctext.pull_unshown("Charon")
    assert "reward" in events


@pytest.mark.asyncio
async def test_a_refunded_pull_says_so_without_touching_the_spent_response(monkeypatch, events):
    pulling(monkeypatch, events, (None, None, "failed", None))
    click = Click(events)
    await cmd.ReelView(OWNER, "Player", CARD).reel_again.callback(click)
    [told] = click.followups
    assert told["embed"].description == ctext.reel_failed()
    assert "reward" not in events


@pytest.mark.asyncio
async def test_out_of_reels_disables_the_button(monkeypatch, events):
    pulling(monkeypatch, events, (None, None, "out", None))
    view = cmd.ReelView(OWNER, "Player", CARD)
    click = Click(events)
    await view.reel_again.callback(click)
    assert view.reel_again.disabled is True
    assert events[:3] == ["defer", "pull", "edit"]
    assert click.followups[0]["embed"].description == ctext.no_reels(0)
