"""
Discard: a plain copy goes back and the tier below rolls in its place.

1. Every yield points exactly one tier down, so discarding can only ever
   move down the list, and commons have nowhere to go
2. Member 1/1s can never be discarded
3. roll_in_tier stays inside the tier it was given, and wishes redirect
   inside it at the same rate a reel would
"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import cards as cardlib


def test_every_yield_is_exactly_one_tier_down():
    for tier, (below, n) in cardlib.DISCARD_YIELD.items():
        assert cardlib.CARD_TIERS.index(below) == cardlib.CARD_TIERS.index(tier) + 1
        assert n >= 1


def test_common_has_nowhere_to_go():
    assert "common" not in cardlib.DISCARD_YIELD
    assert cardlib.discard_yield({"tier": "common"}) is None


def test_member_cards_cannot_be_discarded():
    assert cardlib.discard_yield({"tier": "Hydra", "member": True}) is None
    assert cardlib.discard_yield(None) is None


def test_fabled_gives_two_legendaries():
    assert cardlib.discard_yield({"tier": "fabled"}) == ("legendary", 2)


def test_roll_in_tier_stays_in_tier():
    rng = random.Random(1)
    for tier in cardlib.CARD_TIERS:
        for _ in range(30):
            assert cardlib.roll_in_tier(tier, None, rng)["tier"] == tier


def test_roll_in_tier_honours_wishes_at_the_reel_rate():
    pool = cardlib.load_card_set()["by_tier"]["rare"]
    wished = pool[0]["slug"]
    rng = random.Random(2)
    hits = sum(cardlib.roll_in_tier("rare", {wished}, rng)["slug"] == wished
               for _ in range(4000))
    # 25% redirect plus the natural 1/len(pool) chance, well within noise
    expected = 4000 * (cardlib.WISH_REDIRECT_CHANCE
                       + (1 - cardlib.WISH_REDIRECT_CHANCE) / len(pool))
    assert abs(hits - expected) < 120


def test_wish_outside_the_tier_does_nothing():
    legendary = cardlib.load_card_set()["by_tier"]["legendary"][0]["slug"]
    rng = random.Random(3)
    for _ in range(50):
        assert cardlib.roll_in_tier("rare", {legendary}, rng)["tier"] == "rare"
