"""
Tank list pages: how stacks become rows, and that a page draws.

1. Each level of a card becomes its own row, so a split stack is two rows
2. The default order is rarity, then fusion level, then name
3. Sorting by copies or name still orders every stack
4. Pages of a multi-page list are all one height; a single page is trimmed
5. A page renders with Max, Limited and plain rows in it
6. Alt text names every row on the page
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import card_list_render as cardlist


def card(slug, tier, name=None):
    return {"slug": slug, "name": name or slug.title(), "tier": tier}


def entry(**levels):
    return {"total": sum(levels.values()),
            "levels": {int(k): v for k, v in levels.items()}}


MEMBER = {"slug": "member-1", "name": "Kenji", "tier": "Hydra",
          "rank": "Hydra", "member": True}


def test_every_level_becomes_its_own_row():
    stacks = cardlist.build_stacks([(card("bob", "rare"), entry(**{"0": 5, "2": 1}))])
    assert [(s.star, s.count) for s in stacks] == [(2, 1), (0, 5)]


def test_default_order_is_rarity_then_stars_then_name():
    stacks = cardlist.build_stacks([
        (card("zeph", "rare"), entry(**{"0": 1})),
        (card("anni", "mythic"), entry(**{"0": 1, "3": 1})),
        (card("abe", "rare"), entry(**{"0": 1})),
        (MEMBER, entry(**{"0": 1})),
    ])
    assert [(s.card["slug"], s.star) for s in stacks] == [
        ("member-1", 0), ("anni", 3), ("anni", 0), ("abe", 0), ("zeph", 0)]


def test_copies_and_name_sorts_cover_every_stack():
    entries = [(card("bob", "rare"), entry(**{"0": 2, "1": 1})),
               (card("ava", "normal"), entry(**{"0": 7}))]
    by_copies = cardlist.build_stacks(entries, "copies")
    assert [s.count for s in by_copies] == [7, 2, 1]
    by_name = cardlist.build_stacks(entries, "name")
    assert [(s.card["name"], s.star) for s in by_name] == [
        ("Ava", 0), ("Bob", 1), ("Bob", 0)]


def test_a_multi_page_list_keeps_one_page_height():
    entries = [(card(f"c{i}", "rare"), entry(**{"0": 1}))
               for i in range(cardlist.ROWS_PER_PAGE + 3)]
    stacks = cardlist.build_stacks(entries)
    assert cardlist.page_count(stacks) == 2
    assert cardlist.page_count([]) == 1

    full = cardlist.render_page(stacks, 0, "T", [])
    tail = cardlist.render_page(stacks, 1, "T", [])
    assert full.size == tail.size, "paging must not resize the message"

    alone = cardlist.render_page(stacks[:3], 0, "T", [])
    assert alone.height < full.height, "a lone page is trimmed to its rows"


def test_a_page_draws_max_limited_and_plain_rows():
    stacks = cardlist.build_stacks([
        (card("anni", "mythic"), entry(**{"4": 1})),
        (card("bob", "fabled"), entry(**{"2": 1})),
        (card("ava", "normal"), entry(**{"0": 3})),
        (MEMBER, entry(**{"0": 1})),
    ])
    page = cardlist.render_page(stacks, 0, "Ken's tank",
                                [("4/569", "Cards"), ("6", "copies")])
    assert page.size == (cardlist.WIDTH,
                         cardlist.HEAD_H + 4 * cardlist.ROW_H + cardlist.FOOT_H)
    assert len(page.getcolors(maxcolors=200000)) > 50, "the page is not blank"


def test_alt_text_names_every_row():
    stacks = cardlist.build_stacks([
        (card("anni", "mythic"), entry(**{"4": 1})),
        (MEMBER, entry(**{"0": 1})),
    ])
    alt = cardlist.alt_text(stacks, 0, "Ken's tank")
    assert "Limited Kenji ×1" in alt
    assert "Mythic Anni 4★ ×1" in alt
