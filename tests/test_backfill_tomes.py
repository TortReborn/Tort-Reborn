"""The tome backfill's reading of the hand-kept records (TAQ-116).

The real records are never in the repo, so these use made-up names in the
same shapes: the ledger's per-member blocks, the list message's sections,
and names typed slightly wrong.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import backfill_tomes as bf  # noqa: E402

LEDGER = """**
__Alpha: 7__
Shells: 3
Wars: 4(100)

__Bravo_: 4__
Shells: 0
Wars: 0
Tome debt: 4

__charlie(oldname): 11__
Shells: 10
Wars: 0
Bot Transition : 1

__Delta: 2__
Shells: 0
Wars: 2 (25)
Information: +175 Wars through alts**"""

LIST = """**__No Longer In The Guild:__
🖤 Echo: 2 (not in guild)

__Give if no other option:__
<:prideHeart:855514019053043752> Foxtrot: 895

__Give when full:__
💚 Alpha: 18 (give when full)

__Give when Tome is availble:__

__Tome Debt:__
🩶 Bravo_: -4 (tome debt)
🩶 Golf -1 (tome debt)

__Special Cases:__
<:ERNheart:854017060166565909> Hotel: 5 (has received (missclick))**"""


def test_ledger_blocks():
    e = {x.name: x for x in bf.parse_ledger(LEDGER)}
    assert (e["Alpha"].total, e["Alpha"].raid, e["Alpha"].war, e["Alpha"].debt) == (7, 3, 4, 0)
    assert e["Bravo_"].debt == 4
    assert e["charlie(oldname)"].extras == {"Bot Transition": 1}
    assert e["Delta"].war == 2 and e["Delta"].notes == ["Information: +175 Wars through alts"]


def test_list_sections():
    s = bf.parse_list(LIST)
    assert s["owed"] == [("Echo", 2, "not in guild")]
    assert s["last_resort"] == [("Foxtrot", 895, "")]
    assert s["give_when_full"] == [("Alpha", 18, "give when full")]
    assert s["debt"] == [("Bravo_", -4, "tome debt"), ("Golf", -1, "tome debt")]
    assert s["special"] == [("Hotel", 5, "has received (missclick)")]
    assert "unread" not in s


def test_edit_distance():
    assert bf.edit_distance("thundderr", "thunderrrr") == 3  # too far by edits alone...
    assert bf.squash("Thundderr") == bf.squash("Thunderrrr") == bf.squash("thun_derr") == "thunder"  # ...same once squashed
    assert bf.edit_distance("abc", "abc") == 0
    assert bf.edit_distance("kenji", "kenji121") == 3


class FakeCursor:
    """Answers the resolver's three name queries in order, then stints."""

    def __init__(self, roster, links, raids):
        self.answers = [roster, links, raids]

    def execute(self, sql, args=None):
        self.last = sql

    def fetchall(self):
        return self.answers.pop(0)

    def fetchone(self):
        return None


def _resolver():
    r = bf.Resolver(FakeCursor(
        roster=[("u-thund", "Thundderr"), ("u-amy", "Amy")],
        links=[("u-kenji", "Kenji121")],
        raids=[("u-thund", "OldThund"), ("u-ghost", "ghostsquid")],
    ), aliases={"Kenji": "Kenji121"})
    r._mojang = lambda name: ("u-stranger", name)  # a stranger owns every exact name
    return r


def test_exact_alias_and_older_names():
    r = _resolver()
    assert r("Thundderr") == ("u-thund", "Thundderr", "roster")
    assert r("Kenji") == ("u-kenji", "Kenji121", "discord link")
    assert r("ghostsquid")[:2] == ("u-ghost", "ghostsquid")


def test_a_typo_lands_on_the_member_not_a_stranger():
    for typed in ("Thundderrr", "Thunderrrr", "Thundder"):
        uuid, ign, how = _resolver()(typed)
        assert (uuid, ign) == ("u-thund", "Thundderr") and how.startswith("close match"), typed


def test_short_names_only_tolerate_one_edit():
    r = _resolver()
    assert r("Amy_")[0] == "u-amy"
    assert r("Ann")[0] == "u-stranger"  # two edits from Amy: not close, so the name lookup decides


def test_two_close_players_are_suggested_not_picked():
    r = bf.Resolver(FakeCursor(roster=[("u-1", "Waffle1"), ("u-2", "Waffle2")], links=[], raids=[]), aliases={})
    r._mojang = lambda name: None
    assert r("Waffle3") is None
    assert "Waffle1" in r.why_not("Waffle3") and "Waffle2" in r.why_not("Waffle3")
