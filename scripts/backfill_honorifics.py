"""One-off backfill: record every current Honored Fish / Retired Chief role
holder in member_honorifics (TAQ-76).

The SQL migration (TAq-Website/sql/linking_overhaul_1_additive.sql) carried
over the old discord_links snapshot flags. This adds the honorifics that
only ever existed as Discord roles: walks the prod guild's members, and for
each holder opens a ledger row unless one is open already -- keyed by uuid
when they have a discord_links row, by discord_id alone otherwise (the
uuid is attached when they link).

Dry-run by default; nothing is written without --apply. Writes go to the
database the .env's DB_* variables point at (local dev unless run on prod infra).

    venv/Scripts/python scripts/backfill_honorifics.py            # report only
    venv/Scripts/python scripts/backfill_honorifics.py --apply    # write

Talks straight to the Discord REST API with the prod bot token from .env, so
the bot doesn't need to be running.
"""

import os
import sys
import time
import argparse
import datetime

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import honorifics as hon
from Helpers.database import DB
from Helpers.variables import PROD_TAQ_GUILD_ID

API = "https://discord.com/api/v10"


def request(session, method, url, **kwargs):
    while True:
        r = session.request(method, url, timeout=30, **kwargs)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 1)) + 0.2)
            continue
        r.raise_for_status()
        return r


def fetch_all_members(session, guild_id):
    members, after = [], "0"
    while True:
        r = request(session, "GET", f"{API}/guilds/{guild_id}/members",
                    params={"limit": 1000, "after": after})
        batch = r.json()
        if not batch:
            return members
        members.extend(batch)
        after = batch[-1]["user"]["id"]
        if len(batch) < 1000:
            return members


def holders_from_discord(session, guild_id):
    """[(discord_id, username, [honorific keys])] for members holding either role."""
    roles = request(session, "GET", f"{API}/guilds/{guild_id}/roles").json()
    id_by_name = {r["name"]: r["id"] for r in roles}
    role_ids = {key: id_by_name.get(name) for key, name in hon.ROLE_NAMES.items()}
    missing = [hon.ROLE_NAMES[k] for k, v in role_ids.items() if not v]
    if missing:
        sys.exit(f"roles not found in guild: {missing}")

    out = []
    for m in fetch_all_members(session, guild_id):
        held = [key for key, rid in role_ids.items() if rid in m.get("roles", [])]
        if held:
            out.append((int(m["user"]["id"]), m["user"]["username"], held))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write ledger rows (default: dry-run report)")
    args = parser.parse_args()

    load_dotenv()
    token = os.getenv("TOKEN")
    if not token:
        sys.exit("TOKEN missing from .env (prod bot token)")

    session = requests.Session()
    session.headers.update({"Authorization": f"Bot {token}"})
    holders = holders_from_discord(session, PROD_TAQ_GUILD_ID)
    print(f"{len(holders)} member(s) hold an honorific role in Discord")

    db = DB()
    db.connect()
    written, covered, unlinked = [], [], []
    try:
        note = f"backfill: role held on {datetime.date.today().isoformat()}"
        for discord_id, username, held in holders:
            db.cursor.execute("SELECT uuid::text, ign FROM discord_links WHERE discord_id = %s", (discord_id,))
            row = db.cursor.fetchone()
            uuid, ign = (row[0], row[1]) if row else (None, username)
            if not row:
                unlinked.append((discord_id, username, held))
            for key in held:
                if uuid:
                    db.cursor.execute(
                        "SELECT 1 FROM member_honorifics WHERE uuid = %s::uuid AND honorific = %s AND revoked_at IS NULL",
                        (uuid, key),
                    )
                else:
                    db.cursor.execute(
                        "SELECT 1 FROM member_honorifics WHERE discord_id = %s AND honorific = %s AND revoked_at IS NULL",
                        (discord_id, key),
                    )
                if db.cursor.fetchone():
                    covered.append((ign, key))
                    continue
                if args.apply:
                    hon.grant(db.cursor, uuid=uuid, ign=ign, honorific=key, granted_by=0,
                              discord_id=discord_id, note=note)
                written.append((ign, key))
        if args.apply:
            db.connection.commit()
    finally:
        db.close()

    label = "wrote" if args.apply else "would write"
    print(f"\n{label} {len(written)} row(s):")
    for ign, key in written:
        print(f"  {ign}: {hon.LABELS[key]}")
    print(f"\nalready on record: {len(covered)}")
    if unlinked:
        print(f"\n{len(unlinked)} holder(s) have no discord_links row and were recorded by Discord id only")
    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")


if __name__ == "__main__":
    main()
