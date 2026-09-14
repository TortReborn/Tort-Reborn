"""Wipe every card table so the collection starts from nothing.

Wallets, collections, wishes, minted 1/1s and milestone awards are all keyed
by user rather than by guild, so moving the system from the executive server
to the main one would otherwise carry the whole testing period along with it.
This drops all of it. The card channel setting is left alone: it is per guild
and the main guild sets its own.

Refuses to run without --yes, and says what it is about to delete first.

    TEST_MODE=false python scripts/wipe_card_data.py --yes
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Helpers.database import DB  # noqa: E402

TABLES = ["card_collection", "card_wishlist", "card_members", "card_awards",
          "card_wallet"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true",
                    help="actually delete; without it only the counts print")
    args = ap.parse_args()

    db = DB()
    db.connect()
    try:
        print(f"database: {os.getenv('TEST_MODE', '').lower() == 'true' and 'TEST' or 'PROD'}")
        for t in TABLES:
            db.cursor.execute(f"SELECT COUNT(*) FROM {t}")
            print(f"  {t:16s} {db.cursor.fetchone()[0]:6d} rows")
        if not args.yes:
            print("dry run — pass --yes to wipe")
            return 0
        db.cursor.execute("TRUNCATE " + ", ".join(TABLES))
        db.connection.commit()
        print("wiped")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
