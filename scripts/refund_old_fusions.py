"""Pay back the pearls old fusions cost over the new price; stars and copies stay.

    python scripts/refund_old_fusions.py [--verbose] [--yes]   # dry run without --yes

Run it with the bot stopped, before the reworked commands go live.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Helpers import cards as cardlib  # noqa: E402
from Helpers.database import DB  # noqa: E402

AWARD = "fusion-refund-1"
OLD_COPIES_PER_STEP = 3
OLD_STEP_PEARLS = {1: 100, 2: 300, 3: 900, 4: 2700}
OLD_TIER_MULT = {"mythic": 2}


def old_pearls(stars: int, tier: str | None) -> int:
    total = sum(OLD_COPIES_PER_STEP ** (stars - step) * OLD_STEP_PEARLS[step]
                for step in range(1, stars + 1))
    return total * OLD_TIER_MULT.get(tier, 1)


def new_pearls(stars: int, tier: str) -> int:
    return stars * cardlib.fusion_cost(tier)[1]


def refund_for(stacks: list, tier_of) -> int:
    total = 0
    for card, stars, count in stacks:
        tier = tier_of(card)
        if tier is None:
            continue
        total += count * max(0, old_pearls(stars, tier) - new_pearls(stars, tier))
    return total


def tier_of(slug: str) -> str | None:
    card = cardlib.get_card(slug)
    return card["tier"] if card else None


def starred_stacks(db, user_id: int | None = None) -> dict:
    query = ('SELECT "user", card, stars, count FROM card_collection '
             'WHERE stars > 0 AND count > 0')
    params = ()
    if user_id is not None:
        query += ' AND "user" = %s FOR UPDATE'
        params = (user_id,)
    db.cursor.execute(query, params)
    out = {}
    for user, card, stars, count in db.cursor.fetchall():
        out.setdefault(user, []).append((card, stars, count))
    return out


def refund_user(db, user_id: int) -> int | None:
    db.cursor.execute(
        'INSERT INTO card_awards ("user", award, pearls) VALUES (%s, %s, 0) '
        'ON CONFLICT DO NOTHING', (user_id, AWARD))
    if db.cursor.rowcount == 0:
        return None

    pearls = refund_for(starred_stacks(db, user_id).get(user_id, []), tier_of)
    db.cursor.execute(
        'INSERT INTO card_wallet ("user") VALUES (%s) ON CONFLICT DO NOTHING',
        (user_id,))
    db.cursor.execute(
        'UPDATE card_wallet SET pearls = pearls + %s WHERE "user" = %s',
        (pearls, user_id))
    db.cursor.execute(
        'UPDATE card_awards SET pearls = %s WHERE "user" = %s AND award = %s',
        (pearls, user_id, AWARD))
    return pearls


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true",
                    help="apply the refund; without it only totals print")
    ap.add_argument("--verbose", action="store_true",
                    help="list every user's refund")
    args = ap.parse_args()

    db = DB()
    db.connect()
    try:
        print(f"database: {os.getenv('DB_HOST')}")
        db.cursor.execute('SELECT "user" FROM card_awards WHERE award = %s',
                          (AWARD,))
        paid = {r[0] for r in db.cursor.fetchall()}
        pending = {u: s for u, s in starred_stacks(db).items() if u not in paid}

        total_pearls = total_stacks = 0
        for user, stacks in sorted(pending.items()):
            pearls = refund_for(stacks, tier_of)
            total_pearls += pearls
            total_stacks += len(stacks)
            if args.verbose:
                print(f"  {user}: {len(stacks)} stacks, {pearls:,} pearls")
        print(f"{len(pending)} users, {total_stacks} starred stacks, "
              f"{total_pearls:,} pearls ({len(paid)} already paid)")

        highest = max((st for stacks in pending.values() for _, st, _ in stacks),
                      default=0)
        print(f"highest star level: {highest}")
        if not args.yes:
            print("dry run, pass --yes to apply")
            return 0

        for user in pending:
            try:
                refund_user(db, user)
                db.connection.commit()
            except Exception:
                db.connection.rollback()
                raise
        print("refunded")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
