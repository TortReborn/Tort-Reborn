"""Swap out owned copies of cards whose tier moved, so nobody is handed an
upgrade (or a downgrade) they never pulled.

When data/cards.json re-tiers a card, everyone holding it keeps a card of the
tier they actually reeled: each stack is replaced by a random character that
now sits in the card's old tier, at the same star level and count. The pick
prefers a character the person does not own yet, so the swap reads as a new
pull rather than a duplicate; if they somehow own the whole tier it merges
into an existing stack instead. Pearls were paid at reel time and are left
alone, as are wishes, which are keyed by slug and simply follow the card.

Nothing is written without --apply, and the draw is seeded so the dry run
shows exactly what --apply will do.

    TEST_MODE=false python scripts/retier_swap.py --before main
    TEST_MODE=false python scripts/retier_swap.py --before main --apply

--before is the old card set: a path to a cards.json, or a git ref to take
data/cards.json from. --only-upgrades leaves people holding a card that
moved down, if the guild would rather they keep it.
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
    """slug -> tier from the old set, given a file path or a git ref."""
    if os.path.exists(ref):
        with open(ref, encoding="utf-8") as f:
            payload = json.load(f)
    else:
        raw = subprocess.check_output(
            ["git", "show", f"{ref}:data/cards.json"], cwd=BASE)
        payload = json.loads(raw.decode("utf-8"))
    return {c["slug"]: c["tier"] for c in payload["cards"]}


def moved_cards(before: dict, after: dict, only_upgrades: bool) -> dict:
    """slug -> (old tier, new tier) for every card that changed tier."""
    out = {}
    for slug, old in before.items():
        new = after.get(slug)
        if new is None or new == old:
            continue
        if only_upgrades and TIER_RANK[new] > TIER_RANK[old]:
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True,
                    help="old card set: a cards.json path or a git ref")
    ap.add_argument("--apply", action="store_true",
                    help="write the swaps; without it only the plan prints")
    ap.add_argument("--only-upgrades", action="store_true",
                    help="leave cards that moved down with their owners")
    ap.add_argument("--seed", default="retier",
                    help="draw seed, so the dry run and the apply agree")
    ap.add_argument("--log", help="write the plan as JSON here")
    args = ap.parse_args()

    static = cardlib.load_card_set()
    after = {c["slug"]: c["tier"] for c in static["cards"]}
    moved = moved_cards(load_before(args.before), after, args.only_upgrades)
    if not moved:
        print("no card changed tier")
        return 0
    print(f"{len(moved)} cards changed tier")

    db = DB(use_pool=False)
    db.connect()
    try:
        print(f"database: {os.getenv('TEST_MODE', '').lower() == 'true' and 'TEST' or 'PROD'}")
        db.cursor.execute(
            'SELECT "user", card, stars, count FROM card_collection WHERE count > 0')
        rows = db.cursor.fetchall()
        plan = plan_swaps(rows, moved, static["by_tier"], random.Random(args.seed))

        users = defaultdict(list)
        for user, old, new, stars, count in plan:
            users[user].append((old, new, stars, count))
        print(f"{len(plan)} stacks across {len(users)} people\n")
        name = lambda s: static["by_slug"][s]["name"]  # noqa: E731
        for user, swaps in users.items():
            print(f"<@{user}>")
            for old, new, stars, count in swaps:
                lvl = f" {stars}*" if stars else ""
                many = f" x{count}" if count > 1 else ""
                # plain ASCII: this prints to a Windows console as often as not
                print(f"   {name(old)} ({moved[old][0]} -> {moved[old][1]})"
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
        db.connection.commit()
        print(f"\nswapped {len(plan)} stacks")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
