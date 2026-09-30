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
6. Dungeon bosses are curated rare cards and Dungeon Keepers holds every final boss
7. Boss Altar bosses tier themselves card by card and Boss Altars names them all
8. A card the rebuild dropped is retired, and its holders are swapped out of
   it whatever --only-upgrades or --skip says
9. The Qira Hive's Division Leaders are curated rare cards, and the set holds
   all five of them plus Qira and Yansur
10. Amadel is one card, not one per name the wiki credits him under
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
    before = {"bob": "normal", "sui": "fabled"}
    after = {"bob": "fabled", "sui": "fabled", "aster": "legendary"}
    assert retier_swap.moved_cards(before, after, False) == {"bob": ("normal", "fabled")}


def test_only_upgrades_skips_cards_that_moved_down():
    before = {"bob": "normal", "efena": "legendary"}
    after = {"bob": "fabled", "efena": "unique"}
    assert retier_swap.moved_cards(before, after, True) == {"bob": ("normal", "fabled")}
    assert set(retier_swap.moved_cards(before, after, False)) == {"bob", "efena"}


def test_a_card_the_new_set_dropped_is_retired():
    before = {"bob": "normal", "gone": "rare"}
    after = {"bob": "normal"}
    assert retier_swap.moved_cards(before, after, False) == {"gone": ("rare", None)}


def test_only_upgrades_cannot_spare_a_retired_card():
    """There is no card left to leave the holder with, so the flag is moot."""
    before = {"efena": "legendary", "gone": "rare"}
    after = {"efena": "unique"}
    assert retier_swap.moved_cards(before, after, True) == {"gone": ("rare", None)}


def test_skip_leaves_a_named_card_with_its_owners():
    before = {"bob": "normal", "efena": "legendary"}
    after = {"bob": "rare", "efena": "unique"}
    assert retier_swap.moved_cards(before, after, False, {"bob"}) == {
        "efena": ("legendary", "unique")}


def test_skip_cannot_spare_a_retired_card():
    before = {"gone": "rare"}
    assert retier_swap.moved_cards(before, {}, False, {"gone"}) == {
        "gone": ("rare", None)}


def test_a_retired_card_is_replaced_from_the_tier_it_sat_at():
    rows = [(1, "angie", 0, 2), (2, "angie", 1, 1)]
    plan = retier_swap.plan_swaps(rows, {"angie": ("rare", None)}, BY_TIER,
                                  random.Random(3))
    assert len(plan) == 2
    assert all(new in {"amber", "thomas"} for _, _, new, _, _ in plan)
    assert [(u, s, c) for u, _, _, s, c in plan] == [(1, 0, 2), (2, 1, 1)]


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
    assert len(loaded) == 8


def test_dungeon_bosses_are_rare_cards_and_the_keepers_set_names_every_dungeon():
    from Helpers import cards as cardlib
    static = cardlib.load_card_set(force=True)
    with open(build_card_set.CURATED[1], encoding="utf-8") as f:
        bosses = json.load(f)
    assert bosses["tier"] == "rare"
    for c in bosses["cards"]:
        assert static["by_slug"][c["slug"]]["tier"] == "rare", c["slug"]
        assert c["dungeon"] and c["image_url"].startswith("https://wynncraft.wiki.gg/images/")
    keepers = next(s for s in static["sets"] if s["id"] == "dungeon-keepers")
    # Wynnston is a card but not a Keeper: Fallen Factory is represented by
    # its final boss, the Antikythera Supercomputer
    assert "wynnston" in static["by_slug"] and "wynnston" not in keepers["slugs"]
    finals = {"witherhead", "arakadicus", "charon", "garoth", "hashr", "theorick-twain",
              "slykaar", "captain-redbeard", "antikythera-supercomputer", "the-eye"}
    assert set(keepers["slugs"]) == finals


def test_boss_altars_carry_their_own_tiers_and_the_set_names_every_altar_boss():
    """The altar bosses span the level curve, so the file tiers each card
    itself instead of the whole file; the builder honours that."""
    from Helpers import cards as cardlib
    static = cardlib.load_card_set(force=True)
    with open(build_card_set.CURATED[2], encoding="utf-8") as f:
        altars = json.load(f)
    assert "tier" not in altars, "tiers live on the cards"
    wanted = {
        "durum-protector": "unique", "haros": "unique", "rymek-luke": "unique",
        "revenant-of-skien": "rare", "adamastor": "rare",
        "orange-wybel": "fabled", "panic-zealot": "legendary",
        "hyhet": "legendary",
    }
    assert {c["slug"]: c["tier"] for c in altars["cards"]} == wanted
    for c in altars["cards"]:
        assert static["by_slug"][c["slug"]]["tier"] == c["tier"], c["slug"]
        assert c["altar"] and c["image_url"].startswith("https://wynncraft.wiki.gg/images/")
    boss_set = next(s for s in static["sets"] if s["id"] == "boss-altars")
    assert boss_set["name"] == "Boss Altars"
    assert set(boss_set["slugs"]) == set(wanted)


def test_hive_leaders_are_curated_rare_cards_and_the_set_names_the_whole_hive():
    """Four of the five Division Leaders are curated; Gale is the fifth and is
    mined instead, because unlike them she talks the player through her fight."""
    from Helpers import cards as cardlib
    static = cardlib.load_card_set(force=True)
    with open(build_card_set.CURATED[3], encoding="utf-8") as f:
        hive = json.load(f)
    assert hive["tier"] == "rare"
    divisions = {c["division"] for c in hive["cards"]}
    assert divisions == {"Thunder", "Earth", "Water", "Fire"}, "Air is Gale's"
    for c in hive["cards"]:
        assert static["by_slug"][c["slug"]]["tier"] == "rare", c["slug"]
        assert c["level"] and c["image_url"].startswith("https://wynncraft.wiki.gg/images/")
    hive_set = next(s for s in static["sets"] if s["id"] == "the-qira-hive")
    leaders = {"psychomancer", "gale", "genesis-revorse", "oceanic-judge",
               "solar-vanguard"}
    assert set(hive_set["slugs"]) == leaders | {"qira-mistress-of-the-hive", "yansur"}
    assert all(static["by_slug"][s]["tier"] == "rare" for s in leaders | {"yansur"})
    # Qira is not a Division Leader and sits a tier above the five
    assert static["by_slug"]["qira-mistress-of-the-hive"]["tier"] == "legendary"


def test_amadel_is_one_card_under_every_name_the_wiki_gives_him():
    """The disguise, the job title and the two boss forms are one character,
    so the miner folds them together and only Amadel ships."""
    from Helpers import cards as cardlib
    from scripts import mine_wiki_dialogue as miner
    static = cardlib.load_card_set(force=True)
    assert set(miner.SAME_AS.values()) == {"Amadel"}
    assert "Traitor Amadel" in miner.SAME_AS
    for alias in miner.SAME_AS:
        slug = build_card_set.slugify(alias, set())
        assert slug not in static["by_slug"], alias
    assert static["by_slug"]["amadel"]["tier"] == "rare", "keeps the traitor's pin"
    assert build_card_set.TIER_OVERRIDES["amadel"] == "rare"
    assert "traitor-amadel" not in build_card_set.TIER_OVERRIDES


def test_curated_tier_prefers_the_card_and_rejects_nothing():
    assert build_card_set.curated_tier({"tier": "fabled"}, {"tier": "rare"}) == "fabled"
    assert build_card_set.curated_tier({}, {"tier": "rare"}) == "rare"
    import pytest
    with pytest.raises(ValueError):
        build_card_set.curated_tier({"slug": "x"}, {})
    with pytest.raises(ValueError):
        build_card_set.curated_tier({"slug": "x", "tier": "epic"}, {})
