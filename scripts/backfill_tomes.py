"""
TAQ-116: backfill the tome tables from the hand-kept records in #📓｜tome-list.

Two messages hold everything. The ledger in the thread "Previously
Received/Already Requested" lists every member's tomes received, split into
shells (raid tomes), wars (war tomes), debt and the odd extra. The channel's
own message holds the queue (give when full, give when available, give if
no other option), owed tomes of members no longer in the guild, a debt list
and special cases. The text never passes through this repo: it goes from
Discord to an export file you keep outside it.

    # 1. read the two messages (prod token)
    prodctx python Tort-Reborn/scripts/backfill_tomes.py export <export.json>
    # 2. dry run: who each name is, what would be written, what needs a person
    prodctx python Tort-Reborn/scripts/backfill_tomes.py plan <export.json> [--aliases aliases.json]
    # 3. write it, once exec has checked the plan
    prodctx python Tort-Reborn/scripts/backfill_tomes.py apply <export.json> [--aliases aliases.json]

aliases.json maps a name as written to a current IGN or a UUID, for renames
and typos the lookups can't place ({"Kenji": "Kenji121"}). Names that still
can't be placed are listed and left out. apply refuses to run twice.
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import tomes  # noqa: E402

CHANNEL_ID = "906256972263882812"   # #📓｜tome-list
MESSAGE_ID = "907394431173423175"   # its message, which also starts the ledger thread
ACTOR = "Backfill (#tome-list records)"


# ---- export ----

def export(out: str):
    h = {"Authorization": f"Bot {os.environ['TOKEN']}"}
    api = "https://discord.com/api/v10"
    main = requests.get(f"{api}/channels/{CHANNEL_ID}/messages/{MESSAGE_ID}", headers=h, timeout=30).json()
    thread = requests.get(f"{api}/channels/{MESSAGE_ID}/messages?limit=100", headers=h, timeout=30).json()
    ledger = max((m for m in thread if "Shells:" in (m.get("content") or "")), key=lambda m: len(m["content"]))
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"list": {"id": main["id"], "edited": main.get("edited_timestamp"), "content": main["content"]},
                   "ledger": {"id": ledger["id"], "edited": ledger.get("edited_timestamp"), "content": ledger["content"]}},
                  f, ensure_ascii=False)
    print(f"Exported the list message (edited {main.get('edited_timestamp')}) and the ledger "
          f"(edited {ledger.get('edited_timestamp')}) to {out}")


# ---- parsing ----

@dataclass
class LedgerEntry:
    name: str
    total: int
    raid: int = 0
    war: int = 0
    debt: int = 0
    extras: dict = field(default_factory=dict)   # other numeric lines ("Bot Transition: 1")
    notes: list = field(default_factory=list)    # lines with no number


def _clean(text: str) -> str:
    return text.replace("*", "").replace("\r", "")


def parse_ledger(text: str) -> list[LedgerEntry]:
    """`__Name: N__` headers, each followed by `Key: value` lines."""
    entries, cur = [], None
    for line in _clean(text).split("\n"):
        line = line.strip()
        head = re.fullmatch(r"__\s*(.+?)\s*:\s*(\d+)\s*__", line)
        if head:
            cur = LedgerEntry(head[1], int(head[2]))
            entries.append(cur)
            continue
        kv = re.fullmatch(r"(.+?)\s*:\s*(.*)", line)
        if cur is None or not kv:
            continue
        key, value = kv[1].strip().lower(), kv[2].strip()
        num = re.match(r"[+-]?\d+", value)
        if key == "shells" and num:
            cur.raid = int(num[0])
        elif key == "wars" and num:
            cur.war = int(num[0])
        elif key == "tome debt" and num:
            cur.debt = int(num[0])
        elif num and value == num[0]:
            cur.extras[kv[1].strip()] = int(num[0])
        else:
            cur.notes.append(line)
    return entries


# Section headings of the list message, and what each becomes
SECTIONS = {
    "no longer in the guild": "owed",
    "give if no other option": "last_resort",
    "give when full": "give_when_full",
    "give when tome is availble": "give_when_available",
    "give when tome is available": "give_when_available",
    "tome debt": "debt",
    "special cases": "special",
}


def parse_list(text: str) -> dict[str, list[tuple[str, int, str]]]:
    """{section: [(name, count, note)]}; counts keep their sign (debt is negative)."""
    out, section = defaultdict(list), None
    for line in _clean(text).split("\n"):
        line = line.strip()
        head = re.fullmatch(r"__(.+?):?__\s*:?", line)
        if head:
            section = SECTIONS.get(head[1].strip().lower().rstrip(":"))
            continue
        if not section or not line:
            continue
        # "<emoji> Name: 12 (note)" or "<emoji> Name -3 (note)"
        m = re.fullmatch(r"(?:<a?:\w+:\d+>|\S+)\s+(.+?)\s*:?\s+(-?\d+)\s*(?:\((.*)\))?", line)
        if m:
            out[section].append((m[1].strip(), int(m[2]), (m[3] or "").strip()))
        else:
            out["unread"].append((line, 0, section))
    return dict(out)


# ---- who is who ----

def squash(name: str) -> str:
    """A name with case, underscores and doubled letters ignored, the usual
    slips in a hand-typed name ("Thunderrrr" and "Thundderr" both "thunder")."""
    return re.sub(r"(.)\1+", r"\1", name.lower().replace("_", ""))


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


class Resolver:
    """A written name -> (uuid, current ign, how it was placed), or None.

    Names were typed by hand, so after an exact match against every name the
    bot knows (roster, Discord links, guild raid logs, which keep older names)
    it tries a close one: the same once doubled letters and underscores are
    ignored, or within two edits (one for short names), of exactly one known
    player. Only then does it ask Mojang, so a typo for a member
    can't land on a stranger who owns that exact name. Anything not placed
    exactly is listed for a person to confirm.
    """

    def __init__(self, cursor, aliases: dict):
        self.c, self.aliases, self.cache, self.suggest = cursor, {k.lower(): v for k, v in aliases.items()}, {}, {}
        cursor.execute("SELECT uuid::text, ign FROM guild_roster")
        roster = cursor.fetchall()
        self.in_guild = {u for u, _ in roster}
        cursor.execute("SELECT uuid::text, ign FROM discord_links WHERE uuid IS NOT NULL")
        links = cursor.fetchall()
        cursor.execute("SELECT DISTINCT uuid::text, ign FROM graid_log_participants WHERE uuid IS NOT NULL AND ign IS NOT NULL")
        raids = cursor.fetchall()
        # The newest name we hold for each player, roster first
        self.current = {}
        for rows in (raids, links, roster):
            self.current.update({u: ign for u, ign in rows})
        self.known = {}  # lower name -> {uuid: source}
        for source, rows in (("roster", roster), ("discord link", links), ("raid log", raids)):
            for u, ign in rows:
                self.known.setdefault(ign.lower(), {}).setdefault(u, source)

    def _mojang(self, name):
        if not re.fullmatch(r"\w{3,16}", name):
            return None
        r = requests.get(f"https://api.minecraftservices.com/minecraft/profile/lookup/name/{name}", timeout=15)
        time.sleep(0.1)
        if r.status_code != 200:
            return None
        u, ign = r.json()["id"], r.json()["name"]
        return f"{u[:8]}-{u[8:12]}-{u[12:16]}-{u[16:20]}-{u[20:]}", ign

    def _close(self, key):
        limit = 2 if len(key) >= 6 else 1
        near, flat = {}, squash(key)
        for name, players in self.known.items():
            if squash(name) == flat or (abs(len(name) - len(key)) <= limit and edit_distance(name, key) <= limit):
                for u in players:
                    near.setdefault(u, name)
        return near

    def _one(self, name):
        key = name.lower()
        if key in self.aliases:
            target = self.aliases[key]
            if re.fullmatch(r"[0-9a-fA-F-]{32,36}", target):
                u = target.lower()
                return u, self.current.get(u, name), "alias"
            name, key = target, target.lower()
        if key in self.known:
            players = self.known[key]
            if len(players) == 1:
                u, source = next(iter(players.items()))
                return u, self.current.get(u, name), source if source != "raid log" else "raid log (an older name)"
            self.suggest[name] = [self.current.get(u, u) for u in players]
            return None
        near = self._close(key)
        if len(near) == 1:
            u, matched = next(iter(near.items()))
            return u, self.current.get(u, matched), f"close match to '{matched}'"
        if near:
            self.suggest[name] = [self.current.get(u, n) for u, n in near.items()]
            return None
        found = self._mojang(name)
        if found:
            self.c.execute("SELECT 1 FROM membership_stints WHERE uuid = %s LIMIT 1", (found[0],))
            return found[0], found[1], "name lookup" + ("" if self.c.fetchone() else ", never seen in the guild")
        return None

    def __call__(self, written: str):
        if written not in self.cache:
            hit = None
            # "sodalita(nicklesmart)": try the whole thing, then each part
            for name in [written] + [p for p in re.split(r"[()]", written) if p.strip()]:
                hit = self._one(name.strip())
                if hit:
                    break
            self.cache[written] = hit
        return self.cache[written]

    def why_not(self, written: str) -> str:
        names = [n for part, n in self.suggest.items() if part.lower() in written.lower()]
        return f"no player found; did you mean {', '.join(sorted({x for n in names for x in n}))}?" if names else "no player found"


def latest_wars(cursor, uuid):
    return tomes.snapshot_wars(cursor, uuid)


# ---- plan ----

def build_plan(data: dict, cursor, aliases: dict) -> tuple[list[dict], list[dict], list[str]]:
    """(events, requests, review) to write; nothing is written here."""
    who = Resolver(cursor, aliases)
    events, reqs, review = [], [], []
    ledger = parse_ledger(data["ledger"]["content"])
    lists = parse_list(data["list"]["content"])
    debt_in_ledger = {}

    for e in ledger:
        if e.total != e.raid + e.war + e.debt + sum(e.extras.values()):
            review.append(f"ledger {e.name}: total {e.total} doesn't equal its parts; written as the parts")
        hit = who(e.name)
        if not hit:
            review.append(f"ledger {e.name}: {who.why_not(e.name)} (total {e.total}); left out - add an alias")
            continue
        uuid, ign, how = hit
        if how.endswith("never seen in the guild"):
            # Someone owns that name now, but nothing ties them to the guild:
            # most likely the member renamed and the name went to a stranger.
            review.append(f"ledger {e.name}: the name now belongs to {ign}, who was never in the guild "
                          f"(total {e.total}); left out - add an alias")
            continue
        if uuid in debt_in_ledger:
            review.append(f"ledger {e.name}: same player ({ign}) as another ledger entry; both are written")
        debt_in_ledger[uuid] = debt_in_ledger.get(uuid, 0) + e.debt
        for kind, n, action in (("raid", e.raid, "given"), ("war", e.war, "given"), ("war", e.debt, "debt")):
            if n:
                events.append(dict(uuid=uuid, ign=ign, action=action, kind=kind, count=n, note="from the ledger"))
        for k, n in e.extras.items():
            events.append(dict(uuid=uuid, ign=ign, action="adjusted", kind=None, count=n, note=f"ledger: {k}"))
            review.append(f"ledger {e.name}: '{k}: {n}' recorded as {n} tome(s) of no kind")
        for note in e.notes:
            review.append(f"ledger {e.name}: note kept for reference only: {note}")
        if how not in ("roster", "discord link"):
            review.append(f"ledger {e.name}: placed by {how} as {ign}")
        if e.war:
            wars = latest_wars(cursor, uuid)
            if wars is not None and tomes.war_tomes_earned(wars) < e.war + e.debt:
                review.append(f"ledger {e.name}: {e.war + e.debt} war tomes (incl. debt) but {wars} lifetime wars earns "
                              f"{tomes.war_tomes_earned(wars)}")

    for section, rows in lists.items():
        for name, n, note in rows:
            if section == "unread":
                review.append(f"list: couldn't read line in {note or '?'}: {name}")
                continue
            hit = who(name)
            if section == "debt":
                # The ledger's debt wins where it has an entry; a debt only the
                # list knows about is written from the list.
                if not hit:
                    review.append(f"list debt {name} {n}: {who.why_not(name)}; left out - add an alias")
                elif hit[0] not in debt_in_ledger:
                    events.append(dict(uuid=hit[0], ign=hit[1], action="debt", kind="war", count=-n, note="from the list's debt section"))
                    review.append(f"list debt {name} {n}: not in the ledger, so written from the list as {-n} war tome debt for {hit[1]}")
                elif debt_in_ledger[hit[0]] != -n:
                    review.append(f"list debt {name} {n}: the ledger says {debt_in_ledger[hit[0]]}; the ledger's figure is used")
                continue
            if not hit:
                review.append(f"list {section} {name}: {n}: {who.why_not(name)}; left out - add an alias")
                continue
            uuid, ign, how = hit
            here = uuid in who.in_guild
            if how not in ("roster", "discord link"):
                review.append(f"list {section} {name}: placed by {how} as {ign}")
            if section == "special":
                events.append(dict(uuid=uuid, ign=ign, action="adjusted", kind=None, count=n, note=f"special case: {note}"))
                review.append(f"list special {name}: {n} recorded as received, no kind ({note})")
                continue
            bucket = {"owed": "dormant", "last_resort": "give_when_full"}.get(section, section)
            if bucket != "dormant" and not here:
                review.append(f"list {section} {name}: {n}: not in the guild now, so dormant")
                bucket = "dormant"
            if bucket == "dormant" and here:
                review.append(f"list {section} {name}: {n}: back in the guild, so queued at low priority")
                bucket = "give_when_full" if section == "last_resort" else "queue"
            reqs.append(dict(uuid=uuid, ign=ign, bucket=bucket, count=n, priority="low",
                             note=f"from the list: {section.replace('_', ' ')}" + (f" ({note})" if note else "")))
    return events, reqs, review


def show(events, reqs, review):
    per = defaultdict(Counter)
    for e in events:
        per[e["ign"]][f"{e['action']} {e['kind'] or 'any'}"] += e["count"]
    print(f"== Received: {len(per)} members, {sum(e['count'] for e in events)} tomes")
    for ign in sorted(per, key=str.lower):
        print(f"  {ign}: " + ", ".join(f"{k} {v}" for k, v in sorted(per[ign].items())))
    print(f"\n== Queue: {len(reqs)} request(s)")
    for r in reqs:
        print(f"  {r['ign']}: {r['count']} -> {r['bucket']} | {r['note']}")
    print(f"\n== Needs a person ({len(review)})")
    for line in review:
        print(f"  - {line}")


def apply(db, events, reqs):
    c = db.cursor
    c.execute("SELECT count(*) FROM tome_events WHERE actor_name = %s", (ACTOR,))
    if c.fetchone()[0]:
        raise SystemExit("The backfill has already been written; nothing done.")
    for e in events:
        c.execute(
            "INSERT INTO tome_events (uuid, action, kind, count, actor_name, note, announced_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, NOW())",
            (e["uuid"], e["action"], e["kind"], e["count"], ACTOR, e["note"]),
        )
    for r in reqs:
        c.execute(
            "INSERT INTO tome_requests (uuid, ign, kind, count, priority, bucket, source, note) "
            "VALUES (%s, %s, NULL, %s, %s, %s, 'backfill', %s) RETURNING id",
            (r["uuid"], r["ign"], r["count"], r["priority"], r["bucket"], r["note"]),
        )
        rid = c.fetchone()[0]
        c.execute(
            "INSERT INTO tome_events (uuid, request_id, action, count, actor_name, note) VALUES (%s, %s, 'requested', %s, %s, %s)",
            (r["uuid"], rid, r["count"], ACTOR, r["note"]),
        )
    c.execute(
        "INSERT INTO audit_log (log_type, actor_name, action) VALUES ('tome', %s, %s)",
        (ACTOR, f"Backfilled {sum(e['count'] for e in events)} tomes received and {len(reqs)} queued request(s)"),
    )
    db.connection.commit()
    print(f"Wrote {len(events)} event(s) and {len(reqs)} request(s).")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("export"); s.add_argument("out")
    for name in ("plan", "apply"):
        s = sub.add_parser(name); s.add_argument("export"); s.add_argument("--aliases")
    a = ap.parse_args()
    if a.cmd == "export":
        return export(a.out)
    from Helpers.database import DB
    data = json.load(open(a.export, encoding="utf-8"))
    aliases = json.load(open(a.aliases, encoding="utf-8")) if a.aliases else {}
    db = DB(); db.connect()
    try:
        events, reqs, review = build_plan(data, db.cursor, aliases)
        show(events, reqs, review)
        if a.cmd == "apply":
            apply(db, events, reqs)
    finally:
        db.close()


if __name__ == "__main__":
    main()
