"""
The tank ladder: seven rungs named after the guild's rank buckets.

A wallet stores the rung *number*, not its name, so the bottom three rungs
are a contract with everyone who already bought one. These tests are what
stops a later reshuffle from silently handing out or taking back a tank
somebody paid pearls for.

1. The rungs are the seven rank buckets, bottom to top, spelled the way the
   Discord roles and the role-info embed spell them
2. Rungs 1-3 are frozen at what they cost when the ladder had five rungs
3. Every rung up is worth buying, and the ladder only ever climbs
4. The SQL the wallet refresh builds covers every rung
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import cards as cardlib

# The buckets as data/embeds/guild_info/role_info/guild_ranks.json lists
# them, lowest first. Hydra is the leader, not a bucket, so it is not here.
BUCKETS = ["Reef", "Coastal Waters", "Azure Ocean", "Blue Sea", "Deep Sea",
           "Dark Sea", "Abyss Waters"]

# What rungs 1-3 cost before the ladder grew to seven. Everyone on prod sat
# on one of these when the extra buckets went in.
FROZEN = {
    1: {"name": "Reef", "cost": 0, "bank": 6, "trickle": 0, "wishes": 1},
    2: {"name": "Coastal Waters", "cost": 2000, "bank": 8, "trickle": 3, "wishes": 2},
    3: {"name": "Azure Ocean", "cost": 8000, "bank": 10, "trickle": 6, "wishes": 3},
}


def test_the_rungs_are_the_seven_rank_buckets_in_order():
    assert list(cardlib.TANK_TIERS) == list(range(1, 8))
    assert [s["name"] for s in cardlib.TANK_TIERS.values()] == BUCKETS
    assert cardlib.MAX_TANK == 7


def test_every_bucket_is_a_real_discord_role():
    """The bucket names are borrowed, not invented, so they have to keep
    matching the roles the bot actually hands out. discord_rank_roles is the
    source of truth for the spelling; the stars in front vary (some roles put
    a space after them, some do not) and are not part of the name."""
    from Helpers.variables import discord_rank_roles
    live = {r.lstrip("★☆ ") for r in discord_rank_roles}
    for name in BUCKETS:
        assert name in live, f"{name} is not a rank role"


def test_the_role_info_embed_spells_the_buckets_the_same_way():
    """The embed members read is prose, so nothing stops it drifting from the
    role it sits next to — it called Blue Sea "Blue Ocean" for a while."""
    path = os.path.join(os.path.dirname(__file__), "..", "data", "embeds",
                        "guild_info", "role_info", "guild_ranks.json")
    with open(path, encoding="utf-8") as f:
        blob = f.read()
    for name in BUCKETS:
        assert f"**{name}**" in blob, f"{name} is not in the rank embed"


def test_the_bottom_three_rungs_never_move():
    """Renumbering these would regrade a tank somebody already bought."""
    for tier, spec in FROZEN.items():
        assert cardlib.TANK_TIERS[tier] == spec, tier
    assert cardlib.TANK_TIERS[1]["cost"] == 0, "the first tank is free"


def test_every_rung_climbs_and_pays_for_itself():
    rungs = [cardlib.TANK_TIERS[t] for t in sorted(cardlib.TANK_TIERS)]
    for low, high in zip(rungs, rungs[1:]):
        assert high["cost"] > low["cost"]
        assert high["bank"] > low["bank"]
        assert high["trickle"] > low["trickle"]
        assert high["wishes"] >= low["wishes"]
        # A rung that granted nothing but a bigger bill would be a trap.
        assert (high["bank"], high["trickle"], high["wishes"]) != \
               (low["bank"], low["trickle"], low["wishes"])


def test_bank_cap_and_wish_slots_answer_for_every_rung():
    for tier, spec in cardlib.TANK_TIERS.items():
        assert cardlib.bank_cap(tier) == spec["bank"]
        assert cardlib.wish_slots(tier) == spec["wishes"]
    # An unknown rung falls back to the first rather than raising at a player.
    assert cardlib.bank_cap(99) == cardlib.TANK_TIERS[1]["bank"]
    assert cardlib.wish_slots(0) == cardlib.TANK_TIERS[1]["wishes"]


def test_the_refresh_sql_cases_cover_every_rung():
    """The lazy refresh caps against the row's own tank, so a rung missing
    from the CASE would quietly cap that player at the first rung."""
    for sql, field in ((cardlib._bank_cap_sql(), "bank"),
                       (cardlib._trickle_sql(), "trickle")):
        arms = dict(re.findall(r"WHEN (\d+) THEN (\d+)", sql))
        assert {int(k) for k in arms} == set(cardlib.TANK_TIERS)
        for tier, spec in cardlib.TANK_TIERS.items():
            assert int(arms[str(tier)]) == spec[field], (tier, field)
