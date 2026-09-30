"""Swap out owned copies of cards whose tier moved, so nobody is handed an
upgrade (or a downgrade) they never pulled.

When data/cards.json re-tiers a card, everyone holding it keeps a card of the
tier they actually reeled: each stack is replaced by a random character that
now sits in the card's old tier, at the same star level and count. The pick
prefers a character the person does not own yet, so the swap reads as a new
pull rather than a duplicate; if they somehow own the whole tier it merges
into an existing stack instead. Pearls were paid at reel time and are left
alone, as are wishes, which are keyed by slug and simply follow the card.

A card the rebuild dropped from the set entirely is retired rather than
moved, and its holders are reimbursed the same way: a card of the tier the
retired one sat at. Retirement is the one case --only-upgrades cannot spare
anyone, since there is no longer a card to leave them holding. Wishes for a
retired card are deleted instead of followed: the character it pointed at is
gone, so the row would sit in one of the wisher's slots forever, matching
nothing.

Nothing is written without --apply, and the draw is seeded so the dry run
shows exactly what --apply will do.

    python scripts/retier_swap.py --before main   # runs against DB_* in .env
    python scripts/retier_swap.py --before main --apply

--before is the old card set: a path to a cards.json, or a git ref to take
data/cards.json from. --only-upgrades leaves people holding a card that
moved down, if the guild would rather they keep it, and --skip names
individual cards to leave alone whichever way they went. A retired card
cannot be skipped: there is nothing left to leave anyone holding.
"""

import argparse
import json
import os
import random
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Helpers import cards as cardlib  # noqa: E402
from Helpers.database import DB  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIER_RANK = {t: i for i, t in enumerate(cardlib.CARD_TIERS)}


def load_before(ref: str) -> dict:
    """slug -> card from the old set, given a file path or a git ref.

    The whole card is kept, not just its tier, so a card the new set has
    dropped can still be printed under the name it was reeled as.
    """
    if os.path.exists(ref):
        with open(ref, encoding="utf-8") as f:
            payload = json.load(f)
    else:
        raw = subprocess.check_output(
            ["git", "show", f"{ref}:data/cards.json"], cwd=BASE)
        payload = json.loads(raw.decode("utf-8"))
    return {c["slug"]: c for c in payload["cards"]}


def moved_cards(before: dict, after: dict, only_upgrades: bool,
                skip: set | None = None) -> dict:
    """slug -> (old tier, new tier) for every card that changed tier.

    A card the new set no longer has is retired and pairs with None. Neither
    --only-upgrades nor --skip can spare a retirement: there is nothing left
    to leave its holders.
    """
    out = {}
    for slug, old in before.items():
        new = after.get(slug)
        if new == old:
            continue
        if new and (slug in (skip or ()) or
                    (only_upgrades and TIER_RANK[new] > TIER_RANK[old])):
            continue
        out[slug] = (old, new)
    return out


def plan_swaps(rows: list, moved: dict, by_tier: dict, rng: random.Random) -> list:
    """One entry per stack to swap: (user, old slug, new slug, stars, count).

    A person's stacks of the same card all go to the same replacement so a
    1★ and a 0★ of one character stay one character.
    """
    owned = defaultdict(set)
    for user, card, _stars, _count in rows:
        owned[user].add(card)
    picked = defaultdict(set)

    choice = {}
    plan = []
    for user, card, stars, count in sorted(rows):
        if card not in moved:
            continue
        key = (user, card)
        if key not in choice:
            old_tier = moved[card][0]
            pool = [c["slug"] for c in by_tier.get(old_tier, []) if c["slug"] != card]
            # a new character first; failing that at least not one already
            # dealt in this pass, so two moved cards never share a replacement
            fresh = ([s for s in pool if s not in owned[user] and s not in picked[user]]
                     or [s for s in pool if s not in picked[user]]
                     or pool)
            choice[key] = rng.choice(fresh)
            picked[user].add(choice[key])
        plan.append((user, card, choice[key], stars, count))
    return plan


def apply(db, plan: list) -> None:
    for user, old, new, stars, count in plan:
        db.cursor.execute(
            'SELECT first_at FROM card_collection '
            'WHERE "user" = %s AND card = %s AND stars = %s', (user, old, stars))
        first_at = db.cursor.fetchone()[0]
        db.cursor.execute(
            'DELETE FROM card_collection WHERE "user" = %s AND card = %s AND stars = %s',
            (user, old, stars))
        db.cursor.execute(
            'INSERT INTO card_collection ("user", card, stars, count, first_at) '
            'VALUES (%s, %s, %s, %s, %s) '
            'ON CONFLICT ("user", card, stars) DO UPDATE SET '
            'count = card_collection.count + EXCLUDED.count, '
            'first_at = LEAST(card_collection.first_at, EXCLUDED.first_at)',
            (user, new, stars, count, first_at))


def drop_wishes(db, retired: list) -> int:
    """Delete wishes for retired cards and say how many rows went.

    A wish for a card that still exists follows it, so only retirements are
    touched here; the row would otherwise sit in one of the wisher's slots
    forever, pointing at a character the set no longer has.
    """
    if not retired:
        return 0
    db.cursor.execute(
        'DELETE FROM card_wishlist WHERE card = ANY(%s)', (retired,))
    return db.cursor.rowcount


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True,
                    help="old card set: a cards.json path or a git ref")
    ap.add_argument("--apply", action="store_true",
                    help="write the swaps; without it only the plan prints")
    ap.add_argument("--only-upgrades", action="store_true",
                    help="leave cards that moved down with their owners")
    ap.add_argument("--skip", nargs="*", default=[], metavar="SLUG",
                    help="cards to leave with their owners whichever way they "
                         "moved; a retired card cannot be skipped")
    ap.add_argument("--seed", default="retier",
                    help="draw seed, so the dry run and the apply agree")
    ap.add_argument("--log", help="write the plan as JSON here")
    args = ap.parse_args()

    static = cardlib.load_card_set()
    after = {c["slug"]: c["tier"] for c in static["cards"]}
    before = load_before(args.before)
    moved = moved_cards({s: c["tier"] for s, c in before.items()},
                        after, args.only_upgrades, set(args.skip))
    if not moved:
        print("no card changed tier")
        return 0
    retired = sorted(s for s, (_was, now) in moved.items() if now is None)
    print(f"{len(moved) - len(retired)} cards changed tier, "
          f"{len(retired)} retired")

    db = DB(use_pool=False)
    db.connect()
    try:
        print(f"database: {os.getenv('DB_HOST')}")
        db.cursor.execute(
            'SELECT "user", card, stars, count FROM card_collection WHERE count > 0')
        rows = db.cursor.fetchall()
        plan = plan_swaps(rows, moved, static["by_tier"], random.Random(args.seed))

        users = defaultdict(list)
        for user, old, new, stars, count in plan:
            users[user].append((old, new, stars, count))
        print(f"{len(plan)} stacks across {len(users)} people\n")
        # a retired card has left by_slug, so its name comes from the old set
        name = lambda s: (static["by_slug"].get(s) or before[s])["name"]  # noqa: E731
        for user, swaps in users.items():
            print(f"<@{user}>")
            for old, new, stars, count in swaps:
                lvl = f" {stars}*" if stars else ""
                many = f" x{count}" if count > 1 else ""
                was, now = moved[old]
                # plain ASCII: this prints to a Windows console as often as not
                print(f"   {name(old)} ({was} -> {now or 'retired'})"
                      f"  =>  {name(new)}{lvl}{many}")
        if args.log:
            with open(args.log, "w", encoding="utf-8") as f:
                json.dump([{"user": u, "old": o, "new": n, "stars": s, "count": c}
                           for u, o, n, s, c in plan], f, indent=1)
            print(f"\nplan written to {args.log}")

        if not args.apply:
            print("\ndry run — pass --apply to swap")
            return 0
        apply(db, plan)
        wishes = drop_wishes(db, retired)
        db.connection.commit()
        print(f"\nswapped {len(plan)} stacks")
        if wishes:
            print(f"cleared {wishes} wishes for retired cards")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
