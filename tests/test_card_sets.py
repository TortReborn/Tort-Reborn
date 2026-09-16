"""
Card sets: progress, the one-time completion reward, and the set sheet.

1. set_progress counts one copy of each card at any level and lists what
   is missing; a card in two sets counts toward both
2. check_milestones pays a set exactly once, and only when it is complete
3. Every shipped set has a name, a description, a positive reward and
   real cards; an empty set can never pay out
4. A spread with an owned filter dims the cards that are not owned
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PIL import Image

from Helpers import card_render, cards as cardlib


def _static(monkeypatch, sets):
    base = {"slug": "x", "name": "X", "tier": "rare", "wiki_url": "", "image_url": ""}
    slugs = {x for s in sets for x in s["slugs"]}
    cards = [{**base, "slug": x, "name": x.title()} for x in sorted(slugs)]
    static = {"cards": cards, "by_slug": {c["slug"]: c for c in cards},
              "by_tier": {"rare": cards}, "sets": sets, "generated_at": ""}
    monkeypatch.setattr(cardlib, "load_card_set", lambda force=False: static)
    return static


TWO_SETS = [
    {"id": "a", "name": "Set A", "description": "a", "pearls": 100, "slugs": ["bob", "sui"]},
    {"id": "b", "name": "Set B", "description": "b", "pearls": 200, "slugs": ["sui", "rex", "zeph"]},
    {"id": "empty", "name": "Nothing", "description": "", "pearls": 999, "slugs": []},
]


def test_progress_counts_any_level_and_lists_missing(monkeypatch):
    _static(monkeypatch, TWO_SETS)
    collection = {"bob": {"total": 1, "levels": {2: 1}}, "sui": {"total": 3, "levels": {0: 3}}}
    progress = {p["set"]["id"]: p for p in cardlib.set_progress(collection)}
    assert set(progress) == {"a", "b"}, "the empty set is skipped"
    assert progress["a"]["complete"] and progress["a"]["missing"] == []
    assert progress["b"] == {"set": TWO_SETS[1], "owned": 1, "total": 3,
                             "missing": ["rex", "zeph"], "complete": False}


def test_a_card_can_sit_in_two_sets(monkeypatch):
    _static(monkeypatch, TWO_SETS)
    assert [s["id"] for s in cardlib.sets_of("sui")] == ["a", "b"]
    assert cardlib.sets_of("nobody") == []


def test_set_reward_pays_once_and_only_when_complete(monkeypatch):
    _static(monkeypatch, TWO_SETS)
    monkeypatch.setattr(cardlib, "UNIQUE_MILESTONES", {})
    monkeypatch.setattr(cardlib, "TIER_COMPLETE_PEARLS", {})
    given = set()

    def award_once(user, award, pearls):
        if (user, award) in given:
            return False
        given.add((user, award))
        return True
    monkeypatch.setattr(cardlib, "db_award_once", award_once)

    partial = {"sui": {"total": 1, "levels": {0: 1}}}
    assert cardlib.check_milestones(7, partial) == []
    full_a = {"bob": {"total": 1, "levels": {0: 1}}, "sui": {"total": 1, "levels": {0: 1}}}
    assert cardlib.check_milestones(7, full_a) == [("set: Set A", 100)]
    assert cardlib.check_milestones(7, full_a) == [], "paid once"
    assert ("7", "set-empty") not in {(str(u), a) for u, a in given}


def test_shipped_sets_are_well_formed():
    static = cardlib.load_card_set(force=True)
    with open(cardlib.CARD_SETS_PATH, encoding="utf-8") as f:
        on_disk = json.load(f)["sets"]
    assert len(on_disk) == 6
    for s in on_disk:
        assert s["name"] and s["description"]
        assert isinstance(s["pearls"], int) and s["pearls"] > 0
        assert s["slugs"] and len(set(s["slugs"])) == len(s["slugs"])
        assert all(x in static["by_slug"] for x in s["slugs"]), s["id"]


def test_spread_dims_cards_the_viewer_lacks(monkeypatch):
    # Skip the art fetch: every card renders as the '?' panel.
    monkeypatch.setattr(card_render, "get_art", lambda slug, url: None)
    cards = [{"slug": "have", "name": "Have", "tier": "rare"},
             {"slug": "lack", "name": "Lack", "tier": "rare"}]
    plain = card_render.render_spread(cards)
    dimmed = card_render.render_spread(cards, owned={"have"})
    cw = int(card_render.W * card_render.SPREAD_SCALE)
    gap = card_render.SPREAD_GAP
    left = (gap, gap, gap + cw, dimmed.height - gap)
    right = (2 * gap + cw, gap, 2 * gap + 2 * cw, dimmed.height - gap)
    assert plain.crop(left).tobytes() == dimmed.crop(left).tobytes()
    assert plain.crop(right).tobytes() != dimmed.crop(right).tobytes()


def test_dim_card_is_grey_and_faded():
    img = Image.new("RGBA", (4, 4), (200, 50, 50, 255))
    r, g, b, a = card_render.dim_card(img).getpixel((0, 0))
    assert r == g == b
    assert a == int(255 * card_render.DIM_ALPHA)
