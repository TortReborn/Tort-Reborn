
import os
import random
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import Commands.cards as cmd
from Helpers import cards as cardlib
from scripts import refund_old_fusions

USER = 111111111111111111
OTHER = 222222222222222222


def card(slug, tier):
    return {"slug": slug, "name": slug, "tier": tier}


def collection(**stacks):
    return {slug: {"total": sum(levels.values()), "levels": levels}
            for slug, levels in stacks.items()}


def test_max_rating_per_rarity_matches_the_proposal():
    maxed = {t: cardlib.card_rating(card("x", t), cardlib.MAX_STARS)
             for t in cardlib.CARD_TIERS}
    assert maxed == {"normal": 5, "unique": 6, "rare": 12, "legendary": 24,
                     "fabled": 38, "mythic": 52}


def test_unstarred_rating_is_the_base_points():
    base = {t: cardlib.card_rating(card("x", t)) for t in cardlib.CARD_TIERS}
    assert base == {"normal": 1, "unique": 2, "rare": 4, "legendary": 8,
                    "fabled": 14, "mythic": 20}


def test_a_limited_card_is_flat():
    limited = {"slug": "member-x", "member": True, "tier": "Hydra"}
    assert cardlib.card_rating(limited, 0) == cardlib.LIMITED_RATING == 20
    assert cardlib.card_rating(limited, 4) == 20


def test_a_maxed_mythic_outranks_a_limited():
    assert cardlib.card_rating(card("x", "mythic"), 4) > cardlib.LIMITED_RATING


def test_only_the_best_star_of_a_card_counts(monkeypatch):
    monkeypatch.setattr(cardlib, "get_card", lambda slug: card(slug, "rare"))
    owned = collection(annihilation={0: 5, 2: 1, 3: 1})
    assert cardlib.collection_rating(owned) == 4 + 2 * 3


def test_duplicates_do_not_add_points(monkeypatch):
    monkeypatch.setattr(cardlib, "get_card", lambda slug: card(slug, "unique"))
    assert cardlib.collection_rating(collection(a={0: 1})) == \
        cardlib.collection_rating(collection(a={0: 9}))


def test_a_card_the_set_dropped_is_worth_nothing(monkeypatch):
    monkeypatch.setattr(cardlib, "get_card", lambda slug: None)
    assert cardlib.collection_rating(collection(gone={0: 1})) == 0


def test_limited_cards_count_in_a_collection():
    assert cardlib.collection_rating(collection(**{"member-x": {0: 1}})) == 20


def test_standings_order_by_rating_then_uniques_then_copies(monkeypatch):
    monkeypatch.setattr(cardlib, "get_card", lambda slug: card(slug, "normal"))
    ranked = cardlib.rank_collections({
        1: collection(a={0: 1}, b={0: 1}),
        2: collection(a={2: 1}),
        3: collection(a={0: 1}, b={0: 1}),
        4: collection(a={0: 5}),
    })
    by_user = {r["user"]: r["rank"] for r in ranked}
    assert by_user == {2: 1, 1: 2, 3: 2, 4: 4}


def test_every_rarity_fuses_to_the_same_ceiling():
    for tier in cardlib.CARD_TIERS:
        assert cardlib.tier_max_stars(card("x", tier)) == 4
    assert cardlib.tier_max_stars({"member": True}) == 0
    assert cardlib.tier_max_stars(None) == 0


def test_copies_behind_a_maxed_card():
    behind = {t: cardlib.copies_for(t, cardlib.MAX_STARS)
              for t in cardlib.CARD_TIERS}
    assert behind == {"normal": 17, "unique": 13, "rare": 13, "legendary": 13,
                      "fabled": 9, "mythic": 9}


def test_a_plain_card_is_one_copy():
    assert cardlib.copies_for("rare", 0) == 1


def test_step_pearls_by_rarity():
    steps = {t: cardlib.fusion_cost(t)[1] for t in cardlib.CARD_TIERS}
    assert steps == {"normal": 25, "unique": 50, "rare": 75, "legendary": 100,
                     "fabled": 100, "mythic": 100}


def test_a_normal_asks_for_four_copies_a_step():
    assert {t: cardlib.fusion_cost(t)[0] for t in cardlib.CARD_TIERS} == {
        "normal": 4, "unique": 3, "rare": 3, "legendary": 3,
        "fabled": 2, "mythic": 2}


def test_pearls_leave_room_for_the_tank_ladder():
    by_tier = cardlib.load_card_set()["by_tier"]
    fusing = sum(len(by_tier[t]) * cardlib.MAX_STARS * cardlib.fusion_cost(t)[1]
                 for t in ("normal", "unique", "rare"))
    ladder = sum(spec["cost"] for spec in cardlib.TANK_TIERS.values())
    assert fusing + ladder < 300_000


def test_plan_spends_the_lowest_stars_first():
    assert cardlib.plan_fusion({0: 2, 1: 2, 3: 1}, 1, 3) == {0: 2, 1: 1}


def test_the_target_is_never_its_own_feed():
    assert cardlib.plan_fusion({0: 3}, 0, 3) is None
    assert cardlib.plan_fusion({0: 4}, 0, 3) == {0: 3}


def test_plan_needs_the_target_stack():
    assert cardlib.plan_fusion({0: 9}, 2, 3) is None


def test_a_starred_copy_feeds_as_a_single_copy():
    assert cardlib.plan_fusion({0: 1, 2: 3}, 2, 3) == {0: 1, 2: 2}


def test_simulation_runs_each_step_on_what_the_last_left():
    played = cardlib.simulate_fusion({0: 9}, 0, 2, 3)
    assert played["stars"] == 2
    assert played["spent"] == {0: 6}
    assert played["levels"] == {0: 2, 1: 0, 2: 1}


def test_simulation_fails_when_a_later_step_is_short():
    assert cardlib.simulate_fusion({0: 4}, 0, 2, 3) is None


def test_simulation_stops_at_the_ceiling():
    assert cardlib.simulate_fusion({3: 1, 0: 9}, 3, 2, 3) is None
    assert cardlib.simulate_fusion({3: 1, 0: 3}, 3, 1, 3)["stars"] == 4


def test_a_mythic_needs_two_copies_a_step():
    feed, _ = cardlib.fusion_cost("mythic")
    assert feed == cardlib.fusion_cost("fabled")[0] == 2
    played = cardlib.simulate_fusion({0: 9}, 0, 4, feed)
    assert played["stars"] == 4
    assert played["levels"] == {0: 0, 1: 0, 2: 0, 3: 0, 4: 1}


def test_every_discard_gives_two_of_the_tier_below():
    for tier, (below, n) in cardlib.DISCARD_YIELD.items():
        assert n == 2
        assert cardlib.CARD_TIERS.index(below) == cardlib.CARD_TIERS.index(tier) + 1


@pytest.mark.asyncio
async def test_a_discard_ignores_the_wishlist(monkeypatch):
    rolled = []

    def roll(tier, *wishes):
        rolled.append(wishes)
        return {"slug": "x", "name": "X", "tier": tier}

    def no_wishes(uid):
        raise AssertionError("a discard must not read the wishlist")

    monkeypatch.setattr(cardlib, "roll_in_tier", roll)
    monkeypatch.setattr(cardlib, "db_get_wishes", no_wishes)
    monkeypatch.setattr(cardlib, "db_discard",
                        lambda uid, slug, count, outs: {"left": 0, "new": set()})
    monkeypatch.setattr(cmd, "spread_file",
                        lambda cards: types.SimpleNamespace(filename="x.png"))

    embed, file, outputs = await cmd._do_discard(USER, card("big", "legendary"), 2)
    assert embed is not None and len(outputs) == 4
    assert rolled == [()] * 4


def test_a_limited_merge_takes_two_cards():
    assert cardlib.MERGE_INPUTS == 2


def test_the_draw_gives_every_ticket_equal_odds():
    rng = random.Random(7)
    candidates = [(i, f"m{i}", None, "Hydra") for i in range(8)]
    kept = sum(cardlib.draw_limited(candidates, ["a", "b"], rng)[0] == "kept"
               for _ in range(5000))
    assert abs(kept - 5000 * 2 / 10) < 150


def test_the_draw_can_return_an_input():
    kind, pick = cardlib.draw_limited([], ["a", "b"], random.Random(1))
    assert kind == "kept" and pick in ("a", "b")


def test_old_star_levels_cost_what_they_used_to():
    assert [refund_old_fusions.old_pearls(s, "rare") for s in range(1, 5)] == \
        [100, 600, 2700, 10800]


def test_a_mythic_paid_double():
    assert refund_old_fusions.old_pearls(1, "mythic") == 200


def test_refund_is_the_gap_between_old_and_new_price():
    tiers = {"a": "normal", "b": "mythic", "c": None, "d": "rare"}
    pearls = refund_old_fusions.refund_for(
        [("a", 1, 2), ("a", 2, 1), ("b", 1, 1), ("c", 1, 1), ("d", 1, 3)],
        tiers.get)
    assert pearls == 2 * 75 + (600 - 50) + (200 - 100) + 3 * 25


def test_a_price_that_did_not_drop_refunds_nothing():
    assert refund_old_fusions.refund_for(
        [("d", 1, 5)], lambda slug: "legendary") == 0


def test_nothing_starred_refunds_nothing():
    assert refund_old_fusions.refund_for([], lambda slug: None) == 0


class TxDB:

    def __init__(self, connection):
        self.connection = connection
        self.cursor = connection.cursor()

    def connect(self):
        pass

    def close(self):
        pass


@pytest.fixture
def card_db(monkeypatch):
    from tests.conftest import _dev_connection
    conn = _dev_connection()
    if conn is None:
        pytest.skip("local dev database unreachable (DB_* in .env must point at loopback)")
    cur = conn.cursor()
    cur.execute("""
        CREATE TEMP TABLE card_collection (
            "user" BIGINT NOT NULL, card VARCHAR(64) NOT NULL,
            count INT NOT NULL DEFAULT 1, stars SMALLINT NOT NULL DEFAULT 0,
            first_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY ("user", card, stars));
        CREATE TEMP TABLE card_wallet (
            "user" BIGINT PRIMARY KEY, pearls BIGINT NOT NULL DEFAULT 0);
        CREATE TEMP TABLE card_awards (
            "user" BIGINT NOT NULL, award VARCHAR(64) NOT NULL,
            pearls INT NOT NULL, awarded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY ("user", award));
        CREATE TEMP TABLE card_members (
            slug VARCHAR(64) PRIMARY KEY, discord_id BIGINT NOT NULL UNIQUE,
            uuid UUID, ign VARCHAR(64) NOT NULL, rank VARCHAR(32) NOT NULL,
            owner BIGINT NOT NULL, retired BOOLEAN NOT NULL DEFAULT FALSE,
            minted_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
        CREATE TEMP TABLE discord_links (
            discord_id BIGINT, ign VARCHAR(64), uuid UUID, rank VARCHAR(32));
        CREATE TEMP TABLE guild_roster (uuid UUID);
        CREATE TEMP TABLE player_activity (uuid UUID, snapshot_date DATE);
    """)
    conn.commit()
    db = TxDB(conn)
    monkeypatch.setattr(cardlib, "DB", lambda: db)
    monkeypatch.setattr(refund_old_fusions, "DB", lambda: db, raising=False)
    yield cur
    conn.rollback()
    conn.close()


def give(cur, user, slug, count, stars=0):
    cur.execute('INSERT INTO card_collection ("user", card, stars, count) '
                'VALUES (%s, %s, %s, %s)', (user, slug, stars, count))
    cur.connection.commit()


def held(cur, user, slug):
    cur.execute('SELECT stars, count FROM card_collection '
                'WHERE "user" = %s AND card = %s ORDER BY stars', (user, slug))
    return dict(cur.fetchall())


def pearls(cur, user):
    cur.execute('SELECT pearls FROM card_wallet WHERE "user" = %s', (user,))
    return cur.fetchone()[0]


def fund(cur, user, amount):
    cur.execute('INSERT INTO card_wallet ("user", pearls) VALUES (%s, %s)',
                (user, amount))
    cur.connection.commit()


def test_fusion_raises_one_copy_and_charges_the_step(card_db):
    give(card_db, USER, "bob", 4)
    fund(card_db, USER, 100)
    result = cardlib.db_fuse(USER, "bob", 0, 1, 3, 15)
    assert result["stars"] == 1 and result["pearls"] == 85
    assert held(card_db, USER, "bob") == {1: 1}


def test_fusion_feeds_from_every_star_level(card_db):
    give(card_db, USER, "bob", 1, stars=0)
    give(card_db, USER, "bob", 1, stars=1)
    give(card_db, USER, "bob", 2, stars=2)
    fund(card_db, USER, 100)
    result = cardlib.db_fuse(USER, "bob", 2, 1, 3, 15)
    assert result["spent"] == {0: 1, 1: 1, 2: 1}
    assert held(card_db, USER, "bob") == {3: 1}


def test_a_batch_of_steps_is_charged_per_step(card_db):
    give(card_db, USER, "bob", 8)
    fund(card_db, USER, 100)
    result = cardlib.db_fuse(USER, "bob", 0, 2, 3, 15)
    assert result["stars"] == 2 and result["pearls"] == 70
    assert held(card_db, USER, "bob") == {0: 1, 2: 1}


def test_short_copies_leave_everything_as_it_was(card_db):
    give(card_db, USER, "bob", 3)
    fund(card_db, USER, 100)
    assert cardlib.db_fuse(USER, "bob", 0, 1, 3, 15) is None
    assert held(card_db, USER, "bob") == {0: 3}
    assert pearls(card_db, USER) == 100


def test_short_pearls_undo_the_copies(card_db):
    give(card_db, USER, "bob", 4)
    fund(card_db, USER, 10)
    assert cardlib.db_fuse(USER, "bob", 0, 1, 3, 15) is None
    assert held(card_db, USER, "bob") == {0: 4}
    assert pearls(card_db, USER) == 10


def test_fusion_aborts_when_the_copies_it_would_spend_changed(card_db):
    give(card_db, USER, "bob", 3)
    give(card_db, USER, "bob", 1, stars=1)
    fund(card_db, USER, 100)
    assert cardlib.db_fuse(USER, "bob", 0, 1, 3, 15, expect={0: 3}) is None
    assert held(card_db, USER, "bob") == {0: 3, 1: 1}
    assert pearls(card_db, USER) == 100


def test_fusion_goes_ahead_when_the_planned_copies_still_match(card_db):
    give(card_db, USER, "bob", 3)
    give(card_db, USER, "bob", 1, stars=1)
    fund(card_db, USER, 100)
    result = cardlib.db_fuse(USER, "bob", 0, 1, 3, 15, expect={0: 2, 1: 1})
    assert result["spent"] == {0: 2, 1: 1}


def test_fusion_never_passes_the_ceiling(card_db):
    give(card_db, USER, "bob", 1, stars=4)
    give(card_db, USER, "bob", 9)
    fund(card_db, USER, 100)
    assert cardlib.db_fuse(USER, "bob", 4, 1, 3, 15) is None


def test_merge_swaps_two_plain_cards_for_one(card_db):
    give(card_db, USER, "a", 1)
    give(card_db, USER, "b", 1)
    result = cardlib.db_merge(USER, ["a", "b"], "c")
    assert result["new"] is True
    assert held(card_db, USER, "a") == {} and held(card_db, USER, "c") == {0: 1}


def test_merge_can_hand_back_a_card_just_spent(card_db):
    give(card_db, USER, "a", 1)
    give(card_db, USER, "b", 1)
    result = cardlib.db_merge(USER, ["a", "b"], "a")
    assert result["new"] is False
    assert held(card_db, USER, "a") == {0: 1} and held(card_db, USER, "b") == {}


def test_merge_of_one_card_twice_needs_two_copies(card_db):
    give(card_db, USER, "a", 1)
    assert cardlib.db_merge(USER, ["a", "a"], "c") is None
    assert held(card_db, USER, "a") == {0: 1}


def test_merge_only_spends_plain_copies(card_db):
    give(card_db, USER, "a", 1, stars=1)
    give(card_db, USER, "b", 1)
    assert cardlib.db_merge(USER, ["a", "b"], "c") is None
    assert held(card_db, USER, "b") == {0: 1}


def eligible(cur, discord_id, ign):
    uuid = f"00000000-0000-0000-0000-{discord_id:012d}"
    cur.execute("INSERT INTO discord_links VALUES (%s, %s, %s, 'Hydra')",
                (discord_id, ign, uuid))
    cur.execute("INSERT INTO guild_roster VALUES (%s)", (uuid,))
    cur.execute("INSERT INTO player_activity VALUES (%s, CURRENT_DATE)", (uuid,))
    cur.connection.commit()


def seed_member(cur, n, holder=USER, retired=False):
    slug = f"member-m{n}"
    cur.execute("INSERT INTO card_members (slug, discord_id, ign, rank, owner, retired) "
                "VALUES (%s, %s, %s, 'Hydra', %s, %s)",
                (slug, 1000 + n, f"m{n}", holder, retired))
    give(cur, holder, slug, 1)
    if not retired:
        eligible(cur, 1000 + n, f"m{n}")
    return slug


def seed_pool(cur, n):
    for i in range(n):
        eligible(cur, 2000 + i, f"p{i}")


class Always:
    def __init__(self, kind):
        self.kind = kind

    def choice(self, tickets):
        return next(t for t in tickets if t[0] == self.kind)


def test_limited_merge_can_return_one_input_and_loses_the_other(card_db):
    a, b = seed_member(card_db, 1), seed_member(card_db, 2)
    seed_pool(card_db, 3)
    result = cardlib.db_merge_limited(USER, [a, b], Always("kept"))
    assert result["kept"] is True and result["card"]["slug"] == a
    assert result["lost"] == [b] and result["pool"] == 3
    assert held(card_db, USER, a) == {0: 1} and held(card_db, USER, b) == {}
    card_db.execute("SELECT slug FROM card_members")
    assert [r[0] for r in card_db.fetchall()] == [a]


def test_a_lost_limited_goes_back_in_the_pool(card_db):
    a, b = seed_member(card_db, 1), seed_member(card_db, 2)
    seed_pool(card_db, 2)
    cardlib.db_merge_limited(USER, [a, b], Always("kept"))
    assert len(cardlib._unminted_members(cardlib.DB())) == 2 + 1


def test_limited_merge_can_mint_a_new_member(card_db):
    a, b = seed_member(card_db, 1), seed_member(card_db, 2)
    seed_pool(card_db, 3)
    result = cardlib.db_merge_limited(USER, [a, b], Always("new"))
    assert result["kept"] is False and set(result["lost"]) == {a, b}
    assert held(card_db, USER, result["card"]["slug"]) == {0: 1}
    assert held(card_db, USER, a) == {} and held(card_db, USER, b) == {}
    card_db.execute("SELECT owner FROM card_members")
    assert [r[0] for r in card_db.fetchall()] == [USER]


def test_limited_merge_needs_somebody_left_to_find(card_db):
    a, b = seed_member(card_db, 1), seed_member(card_db, 2)
    assert cardlib.db_merge_limited(USER, [a, b]) == {"reason": "empty"}
    assert held(card_db, USER, a) == {0: 1}


def test_limited_merge_needs_both_cards_in_hand(card_db):
    a = seed_member(card_db, 1)
    b = seed_member(card_db, 2, holder=OTHER)
    seed_pool(card_db, 3)
    assert cardlib.db_merge_limited(USER, [a, b]) == {"reason": "changed"}
    card_db.execute("SELECT COUNT(*) FROM card_members")
    assert card_db.fetchone()[0] == 2


def test_a_retired_limited_can_be_merged_and_lost_for_good(card_db):
    a = seed_member(card_db, 1, retired=True)
    b = seed_member(card_db, 2)
    seed_pool(card_db, 3)
    result = cardlib.db_merge_limited(USER, [a, b], Always("kept"))
    assert result["card"]["slug"] == a
    card_db.execute("SELECT retired FROM card_members WHERE slug = %s", (a,))
    assert card_db.fetchone()[0] is True


def test_a_trade_hands_a_limited_card_to_its_new_holder(card_db):
    a = seed_member(card_db, 1)
    give(card_db, OTHER, "bob", 1)
    assert cardlib.db_trade(USER, OTHER, [(a, 0, 1)], [("bob", 0, 1)]) is True
    card_db.execute("SELECT owner FROM card_members WHERE slug = %s", (a,))
    assert card_db.fetchone()[0] == OTHER


def test_one_card_can_trade_for_three(card_db):
    give(card_db, USER, "a", 2)
    give(card_db, OTHER, "b", 5)
    assert cardlib.db_trade(USER, OTHER, [("a", 0, 1)], [("b", 0, 3)]) is True
    assert held(card_db, USER, "a") == {0: 1}
    assert held(card_db, USER, "b") == {0: 3}
    assert held(card_db, OTHER, "a") == {0: 1}
    assert held(card_db, OTHER, "b") == {0: 2}


def test_four_cards_can_trade_for_two_and_empty_stacks_vanish(card_db):
    give(card_db, USER, "a", 4)
    give(card_db, OTHER, "b", 2, stars=1)
    assert cardlib.db_trade(USER, OTHER, [("a", 0, 4)], [("b", 1, 2)]) is True
    assert held(card_db, USER, "a") == {}
    assert held(card_db, USER, "b") == {1: 2}
    assert held(card_db, OTHER, "a") == {0: 4}
    assert held(card_db, OTHER, "b") == {}


def test_a_trade_short_on_either_side_moves_nothing(card_db):
    give(card_db, USER, "a", 3)
    give(card_db, OTHER, "b", 1)
    assert cardlib.db_trade(USER, OTHER, [("a", 0, 2)], [("b", 0, 2)]) is False
    assert cardlib.db_trade(USER, OTHER, [("a", 0, 4)], [("b", 0, 1)]) is False
    assert held(card_db, USER, "a") == {0: 3}
    assert held(card_db, OTHER, "b") == {0: 1}
    assert held(card_db, USER, "b") == {}
    assert held(card_db, OTHER, "a") == {}


def test_two_cards_can_trade_for_three_different_ones(card_db):
    give(card_db, USER, "a", 2)
    give(card_db, USER, "c", 1, stars=2)
    give(card_db, OTHER, "b", 1)
    give(card_db, OTHER, "d", 4)
    give(card_db, OTHER, "e", 1)
    assert cardlib.db_trade(
        USER, OTHER,
        [("a", 0, 2), ("c", 2, 1)],
        [("b", 0, 1), ("d", 0, 2), ("e", 0, 1)]) is True
    assert held(card_db, USER, "a") == {}
    assert held(card_db, USER, "c") == {}
    assert held(card_db, USER, "b") == {0: 1}
    assert held(card_db, USER, "d") == {0: 2}
    assert held(card_db, USER, "e") == {0: 1}
    assert held(card_db, OTHER, "a") == {0: 2}
    assert held(card_db, OTHER, "c") == {2: 1}
    assert held(card_db, OTHER, "d") == {0: 2}


def test_a_bundle_with_one_missing_card_moves_nothing(card_db):
    give(card_db, USER, "a", 1)
    give(card_db, OTHER, "b", 1)
    assert cardlib.db_trade(
        USER, OTHER, [("a", 0, 1)], [("b", 0, 1), ("gone", 0, 1)]) is False
    assert held(card_db, USER, "a") == {0: 1}
    assert held(card_db, OTHER, "b") == {0: 1}
    assert held(card_db, USER, "b") == {}


def test_standings_read_the_live_collection(card_db, monkeypatch):
    monkeypatch.setattr(cardlib, "get_card", lambda slug: card(slug, "normal"))
    give(card_db, USER, "a", 1, stars=2)
    give(card_db, OTHER, "a", 3)
    give(card_db, OTHER, "b", 1)
    ranked = cardlib.db_standings()
    assert [(r["user"], r["rating"], r["rank"]) for r in ranked] == [
        (USER, 3, 1), (OTHER, 2, 2)]


def test_the_refund_sweep_keeps_the_stars_and_pays_once(card_db, monkeypatch):
    monkeypatch.setattr(refund_old_fusions, "tier_of", lambda slug: "normal")
    give(card_db, USER, "a", 1, stars=1)
    give(card_db, USER, "a", 1, stars=0)
    give(card_db, USER, "b", 1, stars=2)
    fund(card_db, USER, 50)
    db = cardlib.DB()
    assert refund_old_fusions.refund_user(db, USER) == 75 + (600 - 50)
    assert held(card_db, USER, "a") == {0: 1, 1: 1}
    assert held(card_db, USER, "b") == {2: 1}
    assert pearls(card_db, USER) == 675
    assert refund_old_fusions.refund_user(db, USER) is None
    assert pearls(card_db, USER) == 675


def test_the_sweep_gives_a_user_without_a_wallet_one(card_db, monkeypatch):
    monkeypatch.setattr(refund_old_fusions, "tier_of", lambda slug: "normal")
    give(card_db, USER, "a", 1, stars=1)
    refund_old_fusions.refund_user(cardlib.DB(), USER)
    assert pearls(card_db, USER) == 75


class Click:
    def __init__(self, events):
        self.events = events
        self.user = types.SimpleNamespace(id=USER)
        self.channel = None
        self.edits = []
        self.response = types.SimpleNamespace(defer=self.defer)

    async def defer(self):
        self.events.append("defer")

    async def edit_original_response(self, **kwargs):
        self.events.append("edit")
        self.edits.append(kwargs)


async def noop(*args, **kwargs):
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize("name, returns, build", [
    ("_do_fuse", (None, None),
     lambda: cmd.FuseView(USER, card("bob", "normal"), 0, 1, {0: 3})),
    ("_do_discard", (None, None, None),
     lambda: cmd.DiscardView(USER, card("big", "mythic"), 1)),
    ("_do_merge_limited", (object(), None, None),
     lambda: cmd.LimitedMergeView(USER, [card("a", "Hydra"), card("b", "Hydra")])),
])
async def test_confirm_buttons_acknowledge_before_they_work(
        monkeypatch, name, returns, build):
    events = []

    async def work(*args, **kwargs):
        events.append("work")
        return returns

    monkeypatch.setattr(cmd, name, work)
    monkeypatch.setattr(cmd, "_announce_and_reward", noop)
    await build().confirm.callback(Click(events))
    assert events[:2] == ["defer", "work"]


@pytest.mark.asyncio
async def test_a_stale_fusion_confirm_is_told_so(monkeypatch):
    seen = {}

    async def stale(user_id, card_, from_star, steps, expect=None):
        seen["expect"] = expect
        return None, None

    monkeypatch.setattr(cmd, "_do_fuse", stale)
    click = Click([])
    await cmd.FuseView(USER, card("bob", "normal"), 0, 1, {0: 2, 1: 1}) \
        .confirm.callback(click)
    assert seen["expect"] == {0: 2, 1: 1}
    assert "Copies changed" in click.edits[0]["embed"].description


@pytest.mark.asyncio
async def test_the_leaderboard_shows_shared_ranks_from_one_scan(monkeypatch):
    scans = []
    standings = [{"user": u, "rank": rank, "rating": 5, "uniques": 1, "copies": 1}
                 for u, rank in ((1, 1), (2, 2), (3, 2), (4, 4))]
    monkeypatch.setattr(cardlib, "db_standings",
                        lambda: scans.append(1) or standings)
    sent = []

    async def send(**kwargs):
        sent.append(kwargs)

    ctx = types.SimpleNamespace(
        defer=noop, guild=None, author=types.SimpleNamespace(id=4),
        followup=types.SimpleNamespace(send=send))
    await cmd.Cards.tank_leaderboard.callback(cmd.Cards.__new__(cmd.Cards), ctx)
    lines = sent[0]["embed"].description.splitlines()
    assert [line.split("`")[1].strip() for line in lines] == ["1", "2", "2", "4"]
    assert len(scans) == 1


@pytest.mark.asyncio
async def test_a_limited_merge_shows_the_odds_and_waits_for_a_click(monkeypatch):
    members = {s: {"slug": s, "name": s.upper(), "tier": "Hydra", "member": True}
               for s in ("member-a", "member-b")}
    monkeypatch.setattr(cardlib, "db_get_member_card", members.get)
    monkeypatch.setattr(cardlib, "db_get_collection", lambda uid: {
        s: {"levels": {0: 1}, "total": 1} for s in members})
    monkeypatch.setattr(cardlib, "db_count_unminted", lambda: 8)

    def forbidden(*args, **kwargs):
        raise AssertionError("the merge must wait for the button")

    monkeypatch.setattr(cardlib, "db_merge_limited", forbidden)
    sent = []

    async def send(**kwargs):
        sent.append(kwargs)
        return object()

    ctx = types.SimpleNamespace(
        defer=noop, author=types.SimpleNamespace(id=USER), channel=None,
        followup=types.SimpleNamespace(send=send))
    await cmd.Cards.card_merge.callback(
        cmd.Cards.__new__(cmd.Cards), ctx, "member-a:0", "member-b:0")
    [shown] = sent
    assert "80%" in shown["embed"].description
    assert isinstance(shown["view"], cmd.LimitedMergeView)


@pytest.mark.asyncio
async def test_a_limited_merge_with_nobody_left_to_find_says_so(monkeypatch):
    async def refused(user_id, cards):
        return cmd._notice("No unminted Limited cards left"), None, None

    monkeypatch.setattr(cmd, "_do_merge_limited", refused)
    monkeypatch.setattr(cmd, "_announce_and_reward", noop)
    click = Click([])
    await cmd.LimitedMergeView(USER, [card("a", "Hydra"), card("b", "Hydra")]) \
        .confirm.callback(click)
    assert "No unminted" in click.edits[0]["embed"].description

def stacks(*entries):
    return [(card(slug, "normal"), star, count) for slug, star, count in entries]


class Reply:
    def __init__(self, values=()):
        self.data = {"values": list(values)}
        self.user = types.SimpleNamespace(id=USER)
        self.sent = []
        self.edits = []
        self.modals = []
        self.response = types.SimpleNamespace(
            edit_message=self.edit_message, send_message=self.send_message,
            send_modal=self.send_modal)

    async def edit_message(self, **kwargs):
        self.edits.append(kwargs)

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def send_modal(self, modal):
        self.modals.append(modal)


def builder(mine=None, theirs=None):
    target = types.SimpleNamespace(id=OTHER, display_name="Bob", mention="<@2>")
    proposer = types.SimpleNamespace(id=USER, display_name="Ann", mention="<@1>")
    return cmd.TradeBuilderView(
        proposer, target,
        mine if mine is not None else stacks(("a", 0, 3), ("c", 1, 1)),
        theirs if theirs is not None else stacks(("b", 0, 5)),
        interaction=None)


def buttons(view):
    return {c.label: c for c in view.children if isinstance(c, cmd.discord.ui.Button)}


@pytest.mark.asyncio
async def test_a_single_copy_goes_straight_into_the_offer():
    view = builder()
    reply = Reply(["c:1"])
    await view._picked(reply)
    assert [line.entry() for line in view.lines["give"].values()] == [("c", 1, 1)]
    assert reply.modals == []


@pytest.mark.asyncio
async def test_a_stack_of_several_asks_how_many():
    view = builder()
    reply = Reply(["a:0"])
    await view._picked(reply)
    [modal] = reply.modals
    assert isinstance(modal, cmd.TradeAmountModal)
    assert modal.held == 3
    assert view.lines["give"] == {}


@pytest.mark.asyncio
async def test_the_amount_modal_sets_the_line_and_refuses_too_many():
    view = builder()
    modal = cmd.TradeAmountModal(view, "give", card("a", "normal"), 0, 3, 1)
    modal.amount.value = "9"
    reply = Reply()
    await modal.callback(reply)
    assert view.lines["give"] == {}
    assert "1 to 3" in reply.sent[0]["embed"].description
    modal.amount.value = "2"
    await modal.callback(reply)
    assert [line.entry() for line in view.lines["give"].values()] == [("a", 0, 2)]


@pytest.mark.asyncio
async def test_send_waits_for_cards_on_both_sides():
    view = builder(theirs=stacks(("b", 0, 1)))
    assert buttons(view)["Send"].disabled is True
    await view._picked(Reply(["c:1"]))
    assert buttons(view)["Send"].disabled is True
    view.side = "want"
    view._layout()
    await view._picked(Reply(["b:0"]))
    assert buttons(view)["Send"].disabled is False


@pytest.mark.asyncio
async def test_a_card_can_be_taken_back_out():
    view = builder()
    await view._picked(Reply(["c:1"]))
    await view._removed(Reply(["c:1"]))
    assert view.lines["give"] == {}


@pytest.mark.asyncio
async def test_each_side_holds_a_limited_number_of_cards():
    many = stacks(*[(f"card{i}", 0, 1) for i in range(cmd.TradeBuilderView.LINES_PER_SIDE + 1)])
    view = builder(mine=many)
    for i in range(cmd.TradeBuilderView.LINES_PER_SIDE):
        await view._picked(Reply([f"card{i}:0"]))
    reply = Reply([f"card{cmd.TradeBuilderView.LINES_PER_SIDE}:0"])
    await view._picked(reply)
    assert len(view.lines["give"]) == cmd.TradeBuilderView.LINES_PER_SIDE
    remover = next(c for c in view.children
                   if isinstance(c, cmd.discord.ui.Select) and c.placeholder.startswith("Remove"))
    assert len(remover.options) <= 25
    assert "per side" in reply.sent[0]["embed"].description


@pytest.mark.asyncio
async def test_a_long_collection_pages_through_the_picker():
    many = stacks(*[(f"card{i:02d}", 0, 1) for i in range(30)])
    view = builder(mine=many)
    picker = view.children[0]
    assert len(picker.options) == cmd.TradeBuilderView.PICKER_SIZE
    assert buttons(view)["Previous"].disabled is True
    await view._next(Reply())
    assert len(view.children[0].options) == 5
    assert buttons(view)["Next"].disabled is True


@pytest.mark.asyncio
async def test_browsing_the_other_side_switches_the_collection():
    view = builder()
    await view._flip(Reply())
    assert [o.value for o in view.children[0].options] == ["b:0"]
    assert "Bob's cards" in view.embed().footer.text


@pytest.mark.asyncio
async def test_accepting_hands_the_whole_bundle_to_the_database(monkeypatch):
    seen = {}

    def fake_trade(from_user, to_user, give, want):
        seen.update(from_user=from_user, to_user=to_user, give=give, want=want)
        return True

    monkeypatch.setattr(cardlib, "db_trade", fake_trade)
    monkeypatch.setattr(cmd, "_announce_and_reward", noop)
    view = cmd.TradeView(
        types.SimpleNamespace(id=USER, mention="<@1>", display_name="Ann"),
        types.SimpleNamespace(id=OTHER, mention="<@2>", display_name="Bob"),
        [cmd.TradeLine(card("a", "normal"), 0, 2), cmd.TradeLine(card("c", "normal"), 1, 1)],
        [cmd.TradeLine(card("b", "normal"), 0, 3)])
    reply = Reply()
    reply.channel = None
    await view.accept.callback(reply)
    assert seen == {"from_user": USER, "to_user": OTHER,
                    "give": [("a", 0, 2), ("c", 1, 1)], "want": [("b", 0, 3)]}
    assert "b ×3" in reply.edits[0]["embed"].description
