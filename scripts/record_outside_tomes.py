"""
Record tomes handed out outside the bot: given by hand from a post in the
old request channel, or given twice. Each is read from a JSON file you keep
outside the repo:

    {"gives": [{"ign": "Name", "kind": "raid", "count": 5, "priority": "very_low",
                "requested_at": "2026-11-19T19:45:00Z",
                "given_at": "2026-11-19T22:49:00Z", "given_by": "IGN"}],
     "debts": [{"ign": "Name", "count": 5, "at": "2026-11-21T18:06:51Z",
                "by": "IGN", "note": "why"}]}

    prodctx python Tort-Reborn/scripts/record_outside_tomes.py plan <file.json>
    prodctx python Tort-Reborn/scripts/record_outside_tomes.py apply <file.json>

A give becomes a finished request with its requested and given events, and
shell tomes are charged at the member's shell tome price. A debt is war-tome
debt, as the exec Distribution page's Record debt writes it. Nothing here is
announced in the tome log. An entry already recorded is skipped, so apply can
run twice.
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import tomes  # noqa: E402
from Helpers.database import DB, apply_shell_delta  # noqa: E402


def _when(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _member(c, ign: str):
    c.execute("SELECT uuid::text, discord_id, ign FROM discord_links WHERE lower(ign) = lower(%s)", (ign,))
    return c.fetchone()


def _recorded(c, uuid, action: str, at: datetime) -> bool:
    c.execute("SELECT 1 FROM tome_events WHERE uuid = %s AND action = %s AND created_at = %s", (uuid, action, at))
    return c.fetchone() is not None


def plan(c, data: dict) -> tuple[list[dict], list[str]]:
    """(steps to write, problems). Any problem stops apply."""
    steps, problems = [], []
    for g in data.get("gives", []):
        m = _member(c, g["ign"])
        if m is None:
            problems.append(f"{g['ign']}: no linked Discord account with that IGN")
            continue
        if g["kind"] not in tomes.KINDS or g["priority"] not in tomes.PRIORITIES or g["count"] < 1:
            problems.append(f"{g['ign']}: kind, priority or count isn't one the tome tables take")
            continue
        uuid, discord_id, ign = m
        given_at = _when(g["given_at"])
        if _recorded(c, uuid, "given", given_at):
            steps.append({"skip": f"{ign}: the give at {g['given_at']} is already recorded"})
            continue
        shells = 0
        if g["kind"] == "raid":
            s = tomes.standing(c, uuid, discord_id, None)
            shells = tomes.raid_tomes_cost(s.raid_bought, g["count"])
            if shells > s.shells:
                problems.append(f"{ign}: {g['count']} shell tome(s) cost {shells} shells and they have {s.shells}")
                continue
        steps.append({"give": g, "uuid": uuid, "discord_id": discord_id, "ign": ign, "shells": shells,
                      "requested_at": _when(g["requested_at"]), "given_at": given_at})
    for d in data.get("debts", []):
        m = _member(c, d["ign"])
        if m is None:
            problems.append(f"{d['ign']}: no linked Discord account with that IGN")
            continue
        if d["count"] < 1 or not d.get("note"):
            problems.append(f"{d['ign']}: debt needs a positive count and a note")
            continue
        uuid, _, ign = m
        at = _when(d["at"])
        if _recorded(c, uuid, "debt", at):
            steps.append({"skip": f"{ign}: the debt at {d['at']} is already recorded"})
            continue
        steps.append({"debt": d, "uuid": uuid, "ign": ign, "at": at})
    return steps, problems


def describe(step: dict) -> str:
    if "skip" in step:
        return f"skip  {step['skip']}"
    if "give" in step:
        g = step["give"]
        cost = f", {step['shells']} shells charged" if step["shells"] else ""
        return (f"give  {step['ign']}: {g['count']} {tomes.KIND_WORDS[g['kind']]} tome(s) by {g['given_by']} "
                f"at {g['given_at']}{cost}")
    d = step["debt"]
    return f"debt  {step['ign']}: {d['count']} tome(s) at {d['at']}: {d['note']}"


def write(db, steps: list[dict]) -> None:
    c = db.cursor
    for s in steps:
        if "give" in s:
            g = s["give"]
            c.execute(
                "INSERT INTO tome_requests (uuid, discord_id, ign, kind, count, given, priority, status, "
                "shells_spent, source, note, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'done', %s, 'adjustment', %s, %s, %s) RETURNING id",
                (s["uuid"], s["discord_id"], s["ign"], g["kind"], g["count"], g["count"], g["priority"],
                 s["shells"], "Given outside the bot", s["requested_at"], s["given_at"]),
            )
            rid = c.fetchone()[0]
            # announced_at is set so the tome log doesn't post a give from days ago.
            c.execute(
                "INSERT INTO tome_events (uuid, request_id, action, kind, count, actor_id, actor_name, shells, "
                "created_at, announced_at) VALUES (%s, %s, 'requested', %s, %s, %s, %s, %s, %s, NOW())",
                (s["uuid"], rid, g["kind"], g["count"], s["discord_id"], s["ign"], s["shells"], s["requested_at"]),
            )
            c.execute(
                "INSERT INTO tome_events (uuid, request_id, action, kind, count, actor_name, note, created_at, "
                "announced_at) VALUES (%s, %s, 'given', %s, %s, %s, 'Given in-game', %s, NOW())",
                (s["uuid"], rid, g["kind"], g["count"], g["given_by"], s["given_at"]),
            )
            if s["shells"]:
                apply_shell_delta(db, s["discord_id"], -s["shells"], source="tome_request", ign=s["ign"],
                                  reason=f"{g['count']} shell tome(s), request #{rid}")
            action = (f"Recorded {g['count']} {tomes.KIND_WORDS[g['kind']]} tome(s) given to {s['ign']} "
                      f"by {g['given_by']} outside the bot" + (f" for {s['shells']} shells" if s["shells"] else ""))
            actor = g["given_by"]
        elif "debt" in s:
            d = s["debt"]
            c.execute(
                "INSERT INTO tome_events (uuid, action, kind, count, actor_name, note, created_at) "
                "VALUES (%s, 'debt', 'war', %s, %s, %s, %s)",
                (s["uuid"], d["count"], d.get("by"), d["note"], s["at"]),
            )
            action = f"Recorded {d['count']} war tome(s) of debt for {s['ign']}: {d['note']}"
            actor = d.get("by")
        else:
            continue
        c.execute("INSERT INTO audit_log (log_type, actor_name, action) VALUES ('tome', %s, %s)", (actor, action))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("plan", "apply"))
    ap.add_argument("file")
    args = ap.parse_args()
    with open(args.file, encoding="utf-8") as f:
        data = json.load(f)

    db = DB(); db.connect()
    try:
        steps, problems = plan(db.cursor, data)
        for s in steps:
            print(describe(s))
        for p in problems:
            print(f"PROBLEM  {p}")
        if args.cmd == "apply":
            if problems:
                raise SystemExit("Fix the problems above first; nothing written.")
            write(db, steps)
            db.connection.commit()
            print(f"Wrote {sum('skip' not in s for s in steps)} entr(ies).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
