"""
What the wishlist commands offer before anything is typed.

1. Adding puts the cards that finish a set first, nearest set first
2. Below those come cards you lack, then the ones you hold fewest of
3. A card already wished for is not offered again, and typing still narrows
4. Removing offers only what is wished, most copies held first
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import Commands.cards as cmd
from Helpers import cards as cardlib

SETS = [
    {"id": "near", "name": "Nearly There", "description": "", "pearls": 100,
     "slugs": ["have-one", "need-one"]},
    {"id": "far", "name": "Long Way", "description": "", "pearls": 100,
     "slugs": ["have-one", "need-two", "need-three"]},
]
SLUGS = ["have-one", "need-one", "need-two", "need-three", "spare", "hoard"]


def _static(monkeypatch):
    cards = [{"slug": s, "name": s.replace("-", " ").title(), "tier": "rare",
              "wiki_url": "", "image_url": ""} for s in SLUGS]
    static = {"cards": cards, "by_slug": {c["slug"]: c for c in cards},
              "by_tier": {"rare": cards}, "sets": SETS, "generated_at": ""}
    monkeypatch.setattr(cardlib, "load_card_set", lambda force=False: static)
    monkeypatch.setattr(cardlib, "get_card", lambda slug: static["by_slug"].get(slug))
    return static


def _owned(**totals):
    return {slug: {"total": n, "levels": {0: n}} for slug, n in totals.items()}


def test_set_finishers_come_first_nearest_set_first(monkeypatch):
    _static(monkeypatch)
    owned = _owned(**{"have-one": 1, "spare": 1, "hoard": 4})
    order = [name for _, name in cmd._wish_suggestions(owned, set(), "")]
    assert order[0] == "Need One", "one card from a set beats two"
    assert set(order[1:3]) == {"Need Two", "Need Three"}


def test_unowned_then_thinnest_stacks(monkeypatch):
    _static(monkeypatch)
    # No set is in play once have-one is gone, so only copy counts matter.
    monkeypatch.setattr(cardlib, "set_progress", lambda c: [])
    owned = _owned(spare=1, hoard=4, **{"have-one": 2})
    order = [name for _, name in cmd._wish_suggestions(owned, set(), "")]
    assert order[:3] == ["Need One", "Need Three", "Need Two"], "unowned first"
    assert order[3:] == ["Spare", "Have One", "Hoard"], "then fewest copies"


def test_labels_say_why_a_card_is_offered(monkeypatch):
    _static(monkeypatch)
    owned = _owned(**{"have-one": 1, "hoard": 4})
    labels = dict((name, label)
                  for label, name in cmd._wish_suggestions(owned, set(), ""))
    assert labels["Need One"] == "Need One (completes Nearly There)"
    assert labels["Need Two"] == "Need Two (2 left in Long Way)"
    assert labels["Hoard"] == "Hoard ×4"
    assert labels["Spare"] == "Spare", "nothing to say about it"


def test_a_wished_card_is_not_offered_again(monkeypatch):
    _static(monkeypatch)
    owned = _owned(**{"have-one": 1})
    offered = [n for _, n in cmd._wish_suggestions(owned, {"need-one"}, "")]
    assert "Need One" not in offered
    assert "Need Two" in offered


def test_typing_narrows_the_offer(monkeypatch):
    _static(monkeypatch)
    offered = [n for _, n in cmd._wish_suggestions({}, set(), "hoa")]
    assert offered == ["Hoard"]


def test_removing_offers_only_wishes_most_held_first(monkeypatch):
    _static(monkeypatch)
    owned = _owned(hoard=4, spare=1)
    rows = cmd._wished_choices(owned, ["need-one", "spare", "hoard"], "")
    assert [n for _, n in rows] == ["Hoard", "Spare", "Need One"]
    assert [l for l, _ in rows] == ["Hoard ×4", "Spare ×1", "Need One"]


def test_removing_ignores_cards_that_are_not_wished(monkeypatch):
    _static(monkeypatch)
    rows = cmd._wished_choices(_owned(hoard=4), [], "")
    assert rows == []
