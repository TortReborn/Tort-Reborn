"""
The card set: hand-placed tiers and what a wish does when a card moves.

1. Lari sits in legendary, both in the shipped set and in the builder's
   override so a rebuild cannot drop her back to epic
2. Wishes are keyed by slug, so a wish for a card that moved tier follows
   the card: it redirects inside the new tier and is inert in the old one
"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import cards as cardlib
from scripts import build_card_set


def test_lari_is_legendary_in_the_shipped_set():
    assert cardlib.get_card("lari")["tier"] == "legendary"


def test_builder_pins_lari_to_legendary():
    assert build_card_set.TIER_OVERRIDES["lari"] == "legendary"
    cards = [{"slug": "lari", "tier": "epic"}, {"slug": "sui", "tier": "epic"}]
    build_card_set.apply_overrides(cards)
    assert cards[0]["tier"] == "legendary"
    assert cards[1]["tier"] == "epic"


def test_wish_for_a_moved_card_follows_it():
    rng = random.Random(5)
    pool = cardlib.load_card_set()["by_tier"]["legendary"]
    hits = sum(cardlib.roll_in_tier("legendary", {"lari"}, rng)["slug"] == "lari"
               for _ in range(4000))
    expected = 4000 * (cardlib.WISH_REDIRECT_CHANCE
                       + (1 - cardlib.WISH_REDIRECT_CHANCE) / len(pool))
    assert abs(hits - expected) < 120

    for _ in range(200):
        assert cardlib.roll_in_tier("epic", {"lari"}, rng)["slug"] != "lari"
