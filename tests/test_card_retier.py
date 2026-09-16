"""
Re-tiering cards without handing anyone a free upgrade.

1. The builder's pins come from data/tier_overrides.json, so the guild's
   proposal lands there and Lari stays fabled through a rebuild
2. A stack of a moved card is swapped for a random card of the tier it was
   pulled at, keeping its stars and count
3. The replacement is one the person does not own when the tier has any,
   and the same across all their stacks of that card
4. --only-upgrades leaves cards that moved down alone
5. data/card_sets.json loads with load_card_set() and only names real cards
"""

import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import build_card_set, retier_swap

BY_TIER = {
    "legendary": [{"slug": "ankou"}, {"slug": "junes"}, {"slug": "tasim"}],
    "rare": [{"slug": "angie"}, {"slug": "amber"}, {"slug": "thomas"}],
}


def test_overrides_come_from_the_data_file():
    with open(build_card_set.OVERRIDES_PATH, encoding="utf-8") as f:
        on_disk = json.load(f)["overrides"]
    assert build_card_set.TIER_OVERRIDES == on_disk
    assert on_disk["lari"] == "fabled"
    assert on_disk["bob"] == "fabled"


def test_moved_cards_pairs_old_and_new_tier():
    before = {"bob": "normal", "sui": "fabled", "gone": "rare"}
    after = {"bob": "fabled", "sui": "fabled", "aster": "legendary"}
    assert retier_swap.moved_cards(before, after, False) == {"bob": ("normal", "fabled")}


def test_only_upgrades_skips_cards_that_moved_down():
    before = {"bob": "normal", "efena": "legendary"}
    after = {"bob": "fabled", "efena": "unique"}
    assert retier_swap.moved_cards(before, after, True) == {"bob": ("normal", "fabled")}
    assert set(retier_swap.moved_cards(before, after, False)) == {"bob", "efena"}


def test_swap_keeps_stars_and_count_and_stays_in_the_old_tier():
    rows = [(1, "angie", 0, 2), (1, "angie", 1, 1), (1, "ankou", 0, 1)]
    moved = {"angie": ("rare", "unique")}
    plan = retier_swap.plan_swaps(rows, moved, BY_TIER, random.Random(1))
    assert len(plan) == 2
    (u1, old1, new1, s1, c1), (u2, old2, new2, s2, c2) = plan
    assert (u1, old1, s1, c1) == (1, "angie", 0, 2)
    assert (u2, old2, s2, c2) == (1, "angie", 1, 1)
    assert new1 == new2, "both stacks of one card go to one replacement"
    assert new1 in {"amber", "thomas"}


def test_swap_prefers_a_card_the_person_does_not_own():
    rows = [(1, "angie", 0, 1), (1, "amber", 0, 1)]
    moved = {"angie": ("rare", "unique")}
    for seed in range(20):
        plan = retier_swap.plan_swaps(rows, moved, BY_TIER, random.Random(seed))
        assert plan[0][2] == "thomas"


def test_two_moved_cards_get_two_different_replacements():
    rows = [(1, "angie", 0, 1), (1, "amber", 0, 1)]
    moved = {"angie": ("rare", "unique"), "amber": ("rare", "normal")}
    for seed in range(20):
        plan = retier_swap.plan_swaps(rows, moved, BY_TIER, random.Random(seed))
        picks = {new for _, _, new, _, _ in plan}
        assert len(picks) == 2
        assert "thomas" in picks, "the one unowned rare always goes to someone"


def test_swap_falls_back_to_an_owned_card_when_the_tier_is_exhausted():
    rows = [(1, "angie", 0, 1), (1, "amber", 0, 1), (1, "thomas", 0, 1)]
    moved = {"angie": ("rare", "unique")}
    plan = retier_swap.plan_swaps(rows, moved, BY_TIER, random.Random(0))
    assert plan[0][2] in {"amber", "thomas"}


def test_swap_is_deterministic_for_a_seed():
    rows = [(u, "angie", 0, 1) for u in range(1, 8)] + [(3, "ankou", 0, 1)]
    moved = {"angie": ("rare", "unique"), "ankou": ("legendary", "unique")}
    a = retier_swap.plan_swaps(rows, moved, BY_TIER, random.Random("retier"))
    b = retier_swap.plan_swaps(rows, moved, BY_TIER, random.Random("retier"))
    assert a == b and len(a) == 8


def test_card_sets_load_and_every_member_exists():
    from Helpers import cards as cardlib
    with open(cardlib.CARD_SETS_PATH, encoding="utf-8") as f:
        on_disk = json.load(f)["sets"]
    loaded = cardlib.load_card_set(force=True)["sets"]
    assert [s["id"] for s in loaded] == [s["id"] for s in on_disk]
    for disk, live in zip(on_disk, loaded):
        assert live["slugs"] == disk["slugs"], f"{disk['id']} names a slug that is not a card"
        assert len(set(disk["slugs"])) == len(disk["slugs"])
        assert disk["name"] and disk["description"]
    assert len(loaded) == 6
