"""
TAQ-110: keep the two test servers laid out like the real ones.

maincord and the exec server are only ever read. Every write goes to Tort
Test or the test exec server, with the dev bot's token.

    prodctx python Tort-Reborn/scripts/server_parity.py snapshot --out snap.json
    ./venv/Scripts/python.exe scripts/server_parity.py plan  --snapshot snap.json
    ./venv/Scripts/python.exe scripts/server_parity.py apply --snapshot snap.json --yes
    ./venv/Scripts/python.exe scripts/server_parity.py check --snapshot snap.json

snapshot  GETs only, prod token: roles, categories, channels and the
          structure emojis of both real servers.
plan      dev token, read-only: every change apply would make.
apply     dev token: makes them, stage by stage (roles, categories, channels,
          order, emojis), re-reading the test server between stages.
check     plan, exiting 1 when anything has drifted.

What the sync does to a test server:
- Roles: matched by name (ignoring case, emoji and punctuation), edited to
  prod's colour, permissions, hoist and mentionable, missing ones created,
  then put in prod's order. Bot-managed roles in prod are not copied.
  Test-only roles are kept, below the prod ones; bot roles stay above.
- Channels and categories: matched the same way and renamed/moved in place,
  so their ids survive. A type change (text -> forum) creates the channel
  fresh and archives the old one. Announcement channels become text
  channels: the test servers are not Community servers.
- Overwrites are copied by role name. Overwrites for single users and for
  test-only roles are left as they are.
- Channels a bot uses (any id in either profile of Helpers/variables.py,
  plus DEV_CHANNEL_NAMES) with no prod counterpart go into a Dev category at
  the top, unless prod keeps them in its archive categories. Every other
  test-only channel goes into Archive at the bottom (Archive 2, 3, ... past Discord's 50 per category).
  Nothing is deleted unless asked: --delete-empty-categories removes
  categories with nothing left in them, --delete-test-only-roles removes
  roles the real server doesn't have (never bot roles or KEEP_TEST_ROLES).
- Emojis: only STRUCTURE_EMOJIS, into Tort Test.
- No messages are read or copied.
"""

import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests  # noqa: E402

from Helpers.variables import _ENV_CONFIG, _PROFILE_BY_APP_ID, _app_id_from_token  # noqa: E402

API = "https://discord.com/api/v10"
REASON = "TAQ-110 test server parity sync"

GUILDS = {
    "main": (_ENV_CONFIG["prod"]["TAQ_GUILD_ID"], _ENV_CONFIG["test"]["TAQ_GUILD_ID"]),
    "exec": (_ENV_CONFIG["prod"]["EXEC_GUILD_ID"], _ENV_CONFIG["test"]["EXEC_GUILD_ID"]),
}
PROD_GUILD_IDS = {p for p, _ in GUILDS.values()}
TEST_GUILD_IDS = {t for _, t in GUILDS.values()}

# Prod categories that are not part of the live layout.
SKIP_CATEGORIES = {"Archives", "Archives 2"}
# Per-member channels (tickets, applications): "ticket-0182-name", "accepted-3600".
# Only skipped inside the categories that hold them, so a "season-2025" channel
# anywhere else is still copied.
PER_MEMBER_RE = re.compile(r"(^|-)\d{4}(-|$)")
PER_MEMBER_CATEGORIES = {"Guild Applications", "Guild Queue", "Invited", "Denied", "Closed Applications",
                         "alt apps archive", "🐚 Shell Exchange", "🗡 War Ticket"}
# Emojis the server's posts and the bot actually use. The rest are for fun.
STRUCTURE_EMOJIS = ("TAq", "partytort", "graid", "shells", "aspect_warrior",
                    "notg", "nol", "tcc", "tna", "wtp")
EMOJI_GUILD = "main"
# Dev-only channels the bot does not reference by id but that belong in Dev.
DEV_CHANNEL_NAMES = {"main": set(), "exec": set()}
# Test channels whose names don't match their prod counterpart closely enough.
CHANNEL_ALIASES = {
    "main": {"guild-announcement": "📢｜guild-announcements",
             "💬｜military-chat": "🪖｜taqtics",
             "annihilation-announcement": "👹｜annihilation"},
    "exec": {"tort-taskboard": "📌｜task-board"},
}

# Test-only roles that --delete-test-only-roles keeps.
KEEP_TEST_ROLES = {"Developer", "Beta Tort Reborn (kenj)"}  # the second is a beta bot's role that is not bot-managed

DEV_CATEGORY = "Dev"
ARCHIVE_CATEGORY = "Archive"
ARCHIVE_RE = re.compile(r"archives?(\s*\d+)?")
CATEGORY_LIMIT = 50

CATEGORY, TEXT, VOICE, ANNOUNCEMENT, STAGE, FORUM = 4, 0, 2, 5, 13, 15
# What a prod channel type becomes in a test server.
TEST_TYPE = {ANNOUNCEMENT: TEXT}
BITRATE_CAP = {0: 96000, 1: 128000, 2: 256000, 3: 384000}
EMOJI_CAP = {0: 50, 1: 100, 2: 150, 3: 250}


# ---------------------------------------------------------------- matching

def key(name):
    """Match key: case, emoji, separators and punctuation ignored."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def match_by_name(wanted, available, compatible=lambda w, a: True, prefer=lambda w, a: 0, aliases=None):
    """One-to-one match: exact names (or aliases) first, then keys. Returns {id(w): a}."""
    aliases = aliases or {}
    out, used = {}, set()
    for exact in (True, False):
        for w in wanted:
            if id(w) in out:
                continue
            cands = [a for a in available if a["id"] not in used and compatible(w, a)
                     and (aliases.get(a["name"], a["name"]) == w["name"] if exact
                          else key(a["name"]) and key(a["name"]) == key(w["name"]))]
            if cands:
                best = min(cands, key=lambda a: (prefer(w, a), int(a["id"])))  # oldest wins: stable across runs
                out[id(w)] = best
                used.add(best["id"])
    return out


def is_archive(name):
    return bool(ARCHIVE_RE.fullmatch(name.strip().lower()))


def compatible_type(want, have):
    return want == have or {want, have} <= {TEXT, ANNOUNCEMENT} or {want, have} <= {VOICE, STAGE}


# ---------------------------------------------------------------- desired layout

def prod_layout(g):
    """The parts of a real server the test server should copy."""
    roles = sorted(g["roles"], key=lambda r: (-r["position"], int(r["id"])))
    names = {r["id"]: r["name"] for r in roles if not r.get("managed")}
    names[str(g["id"])] = "@everyone"
    copy_roles = [r for r in roles if not r.get("managed") and str(r["id"]) != str(g["id"])]
    everyone = next(r for r in roles if str(r["id"]) == str(g["id"]))

    def overwrites(ch):
        return sorted((names[o["id"]], str(o["allow"]), str(o["deny"]))
                      for o in ch.get("permission_overwrites", []) if o["type"] == 0 and o["id"] in names)

    chans = g["channels"]
    cat_pos = {c["id"]: c["position"] for c in chans if c["type"] == CATEGORY}
    cats = sorted((c for c in chans if c["type"] == CATEGORY and c["name"] not in SKIP_CATEGORIES),
                  key=lambda c: (c["position"], int(c["id"])))
    live_cats = {c["id"]: c["name"] for c in cats}
    channels = []
    for c in sorted((c for c in chans if c["type"] != CATEGORY),
                    key=lambda c: (cat_pos.get(c.get("parent_id"), -1), c["type"] in (VOICE, STAGE),
                                   c["position"], int(c["id"]))):
        if c.get("parent_id") and c["parent_id"] not in live_cats:
            continue
        if live_cats.get(c.get("parent_id")) in PER_MEMBER_CATEGORIES and PER_MEMBER_RE.search(c["name"]):
            continue
        channels.append({"name": c["name"], "type": TEST_TYPE.get(c["type"], c["type"]),
                         "parent": live_cats.get(c.get("parent_id")), **channel_settings(c),
                         "overwrites": overwrites(c)})
    for kind, items in (("role", copy_roles), ("category", cats), ("channel", channels)):
        seen = [n for n in {x["name"] for x in items} if sum(x["name"] == n for x in items) > 1]
        if seen:
            raise SystemExit(f"{g.get('name', g['id'])} has more than one {kind} named {seen}; "
                             "matching is by name, so rename one before syncing")
    return {
        "roles": [{"name": r["name"], "color": r["color"], "permissions": str(r["permissions"]),
                   "hoist": r["hoist"], "mentionable": r["mentionable"]} for r in copy_roles],
        "everyone_permissions": str(everyone["permissions"]),
        "categories": [{"name": c["name"], "overwrites": overwrites(c)} for c in cats],
        "channels": channels,
        # Live channels prod keeps in its archive categories: archived in test too.
        "archived": sorted(c["name"] for c in chans if c["type"] != CATEGORY
                           and cat_name_of(chans, c) in SKIP_CATEGORIES),
    }


def cat_name_of(chans, c):
    return next((p["name"] for p in chans if p["id"] == c.get("parent_id")), None)


def channel_settings(c):
    s = {"topic": c.get("topic") or None, "nsfw": bool(c.get("nsfw")),
         "rate_limit_per_user": c.get("rate_limit_per_user") or 0}
    if c["type"] in (VOICE, STAGE):
        s.update(bitrate=c.get("bitrate"), user_limit=c.get("user_limit") or 0)
    if c["type"] == FORUM:
        s["tags"] = sorted((t["name"], bool(t.get("moderated")), t.get("emoji_name")) for t in c.get("available_tags", []))
        s["default_sort_order"] = c.get("default_sort_order")
        s["default_forum_layout"] = c.get("default_forum_layout") or 0
    return s


# ---------------------------------------------------------------- stages

def role_ops(want, test, bot_top):
    """Edits, creations and the final order of the roles."""
    gid = str(test["id"])
    roles = [r for r in test["roles"] if str(r["id"]) != gid]
    match = match_by_name(want["roles"], [r for r in roles if not r.get("managed")])
    ops = []
    everyone = next(r for r in test["roles"] if str(r["id"]) == gid)
    if str(everyone["permissions"]) != want["everyone_permissions"]:
        ops.append({"op": "role_edit", "id": gid, "name": "@everyone",
                    "changes": {"permissions": want["everyone_permissions"]}})
    for w in want["roles"]:
        r = match.get(id(w))
        if r is None:
            ops.append({"op": "role_create", "name": w["name"], "fields": w})
            continue
        changes = {k: v for k, v in w.items() if (str(r[k]) if k == "permissions" else r[k]) != v}
        if changes and r["position"] >= bot_top:
            ops.append({"op": "warn", "text": f"role {r['name']!r} is above the dev bot's role; can't edit it"})
        elif changes:
            ops.append({"op": "role_edit", "id": r["id"], "name": r["name"], "changes": changes})
    # Order: bot roles on top, then prod's roles in prod order, then test-only roles.
    matched = {r["id"]: w["name"] for w in want["roles"] if (r := match.get(id(w)))}
    order = {w["name"]: i for i, w in enumerate(want["roles"])}
    movable = [r for r in roles if r["position"] < bot_top]
    desired = sorted(movable, key=lambda r: (
        0 if r.get("managed") else 1 if r["id"] in matched else 2,
        order.get(matched.get(r["id"]), 0), -r["position"]))
    current = sorted(movable, key=lambda r: -r["position"])
    if [r["id"] for r in desired] != [r["id"] for r in current]:
        ops.append({"op": "role_order", "ids": [r["id"] for r in desired]})
    return ops, match


def role_names(want, test, match):
    """Test role id -> the name it has (or will have) in prod's layout."""
    out = {str(test["id"]): "@everyone"}
    for r in test["roles"]:
        out.setdefault(r["id"], None)
    for w in want["roles"]:
        if (r := match.get(id(w))):
            out[r["id"]] = w["name"]
    return out


def test_overwrites(ch, names):
    """Overwrites the sync owns (by name) and the ones it leaves alone (raw)."""
    owned, kept = [], []
    for o in ch.get("permission_overwrites", []):
        n = names.get(o["id"]) if o["type"] == 0 else None
        if n:
            owned.append((n, str(o["allow"]), str(o["deny"])))
        else:
            kept.append(o)
    return sorted(owned), kept


def classify(want, test, dev_ids, dev_names, aliases=None):
    """Match test channels to prod's; everything else goes to Dev or Archive."""
    chans = test["channels"]
    cats = [c for c in chans if c["type"] == CATEGORY]
    archives = sorted((c for c in cats if is_archive(c["name"])), key=lambda c: (c["position"], int(c["id"])))
    dev_cat = next((c for c in cats if c["name"] == DEV_CATEGORY), None)
    special = {c["id"] for c in archives} | ({dev_cat["id"]} if dev_cat else set())
    cat_match = match_by_name(want["categories"], [c for c in cats if c["id"] not in special])
    cat_name = {c["id"]: c["name"] for c in cats}
    wanted_parent = {}
    for w in want["categories"]:
        if (c := cat_match.get(id(w))):
            wanted_parent[c["id"]] = w["name"]

    others = [c for c in chans if c["type"] != CATEGORY]
    ch_match = match_by_name(
        want["channels"], others,
        compatible=lambda w, a: compatible_type(w["type"], a["type"]),
        prefer=lambda w, a: 0 if wanted_parent.get(a.get("parent_id")) == w["parent"] else 1,
        aliases=aliases)
    # Same name but the wrong type: recreated, and the old one archived.
    retype = match_by_name([w for w in want["channels"] if id(w) not in ch_match],
                           [c for c in others if c["id"] not in {a["id"] for a in ch_match.values()}], aliases=aliases)
    matched_ids = {a["id"] for a in ch_match.values()} | {a["id"] for a in retype.values()}
    prod_names = {w["name"] for w in want["channels"]}
    prod_keys = {key(n) for n in prod_names}
    dev, archive = [], []
    for c in sorted(others, key=lambda c: (c["position"], int(c["id"]))):
        if c["id"] in matched_ids:
            continue
        in_archive = c.get("parent_id") in {a["id"] for a in archives}
        replaced = key(c["name"]) in prod_keys or (aliases or {}).get(c["name"]) in prod_names
        archived_in_prod = key(c["name"]) in {key(n) for n in want.get("archived", [])}
        if not replaced and not archived_in_prod and (c["id"] in dev_ids or key(c["name"]) in {key(n) for n in dev_names}):
            dev.append(c)
        elif not in_archive:
            archive.append(c)
    # Parent label per test category: prod's name when matched, so a same-named
    # leftover category never passes for the real one.
    parent_label = {c["id"]: f"(unmatched) {c['name']}" for c in cats}
    parent_label.update(wanted_parent)
    for c in archives + ([dev_cat] if dev_cat else []):
        parent_label[c["id"]] = c["name"]
    return {"parent_label": parent_label, "cat_match": cat_match, "ch_match": ch_match, "retype": retype, "dev": dev, "archive": archive,
            "archives": archives, "dev_cat": dev_cat, "cat_name": cat_name}


def category_ops(want, test, names, cls):
    ops = []
    for w in want["categories"]:
        c = cls["cat_match"].get(id(w))
        if c is None:
            ops.append({"op": "cat_create", "name": w["name"], "overwrites": w["overwrites"]})
            continue
        owned, _ = test_overwrites(c, names)
        changes = {}
        if c["name"] != w["name"]:
            changes["name"] = w["name"]
        if owned != w["overwrites"]:
            changes["overwrites"] = w["overwrites"]
        if changes:
            ops.append({"op": "cat_edit", "id": c["id"], "name": c["name"], "changes": changes})
    if cls["dev_cat"] is None and cls["dev"]:
        ops.append({"op": "cat_create", "name": DEV_CATEGORY, "overwrites": None})
    if cls["archives"] and cls["archives"][0]["name"] != ARCHIVE_CATEGORY:
        ops.append({"op": "cat_edit", "id": cls["archives"][0]["id"], "name": cls["archives"][0]["name"],
                    "changes": {"name": ARCHIVE_CATEGORY}})
    return ops


EMOJI_RE = re.compile(r"<(a?):(\w+):(\d+)>")


def portable_text(text, emojis):
    """Custom emojis from the real server become the test server's emoji of
    the same name, or plain :name: (Discord refuses emojis the bot can't use)."""
    if not text:
        return text
    have = {e["name"]: f"<{'a' if e.get('animated') else ''}:{e['name']}:{e['id']}>" for e in emojis}
    return EMOJI_RE.sub(lambda m: have.get(m.group(2), f":{m.group(2)}:"), text)


def channel_ops(want, test, names, cls, tier):
    ops = []
    cap = BITRATE_CAP.get(tier, 96000)
    for w in want["channels"]:
        spec = dict(w)
        spec["topic"] = portable_text(spec["topic"], test.get("emojis", []))
        if "bitrate" in spec and spec["bitrate"]:
            spec["bitrate"] = min(spec["bitrate"], cap)
        c = cls["ch_match"].get(id(w))
        if c is None:
            old = cls["retype"].get(id(w))
            ops.append({"op": "chan_create", "name": w["name"], "spec": spec,
                        **({"replaces": old["id"], "replaces_name": old["name"]} if old else {})})
            continue
        have = {"name": c["name"], "type": c["type"], "parent": cls["parent_label"].get(c.get("parent_id")),
                **channel_settings(c), "overwrites": test_overwrites(c, names)[0]}
        if c["type"] in (VOICE, STAGE) and spec.get("bitrate") is None:
            have.pop("bitrate", None)
        changes = {k: v for k, v in spec.items() if k != "type" and have.get(k) != v}
        if changes:
            ops.append({"op": "chan_edit", "id": c["id"], "name": c["name"], "changes": changes})
    for c in cls["dev"]:
        if cls["dev_cat"] is None or c.get("parent_id") != cls["dev_cat"]["id"]:
            ops.append({"op": "to_dev", "id": c["id"], "name": c["name"]})
    for c in cls["archive"]:
        ops.append({"op": "to_archive", "id": c["id"], "name": c["name"]})
    return ops


def order_op(want, test, cls):
    """One op putting categories and channels in the desired order, if they aren't."""
    chans = test["channels"]
    by_id = {c["id"]: c for c in chans}
    cat_of = {c["id"]: c.get("parent_id") for c in chans}
    prod_cats = [cls["cat_match"][id(w)]["id"] for w in want["categories"] if id(w) in cls["cat_match"]]
    archives = [c["id"] for c in cls["archives"]]
    dev = [cls["dev_cat"]["id"]] if cls["dev_cat"] else []
    rest = sorted((c["id"] for c in chans if c["type"] == CATEGORY and c["id"] not in set(prod_cats + archives + dev)),
                  key=lambda i: (by_id[i]["position"], int(i)))
    cat_order = dev + prod_cats + rest + archives
    rank = {w["name"]: i for i, w in enumerate(want["channels"])}
    prod_name = {c["id"]: w["name"] for w in want["channels"] if (c := cls["ch_match"].get(id(w)))}

    def children(parent):
        kids = [c for c in chans if c["type"] != CATEGORY and c.get("parent_id") == parent]
        return sorted(kids, key=lambda c: (c["type"] in (VOICE, STAGE), rank.get(prod_name.get(c["id"]), len(rank)),
                                           c["position"], int(c["id"])))

    desired = [c["id"] for c in children(None)]
    for cat in cat_order:
        desired += [cat] + [c["id"] for c in children(cat)]
    current = [c["id"] for c in sorted((c for c in chans if c["type"] != CATEGORY and not cat_of[c["id"]]),
                                        key=lambda c: (c["type"] in (VOICE, STAGE), c["position"], int(c["id"])))]
    for cat in sorted((c for c in chans if c["type"] == CATEGORY), key=lambda c: (c["position"], int(c["id"]))):
        current += [cat["id"]] + [c["id"] for c in sorted(
            (c for c in chans if c.get("parent_id") == cat["id"] and c["type"] != CATEGORY),
            key=lambda c: (c["type"] in (VOICE, STAGE), c["position"], int(c["id"])))]
    if desired == current:
        return []
    return [{"op": "order", "positions": [{"id": i, "position": n} for n, i in enumerate(desired)]}]


def emoji_ops(snapshot_emojis, test, which):
    if which != EMOJI_GUILD:
        return []
    have = {e["name"] for e in test.get("emojis", [])}
    missing = [e for e in snapshot_emojis if e["name"] not in have]
    cap = EMOJI_CAP.get(test.get("premium_tier", 0), 50)
    free = {a: cap - sum(bool(e.get("animated")) == a for e in test.get("emojis", [])) for a in (False, True)}
    ops, skipped = [], []
    for e in missing:
        if free[e["animated"]] > 0:
            free[e["animated"]] -= 1
            ops.append({"op": "emoji_create", "name": e["name"], "image": e["image"]})
        else:
            skipped.append(e["name"])
    if skipped:
        ops.insert(0, {"op": "warn", "text": f"no emoji slots left for {skipped} (this server has {cap})"})
    return ops


def bot_top_position(test):
    member = test.get("_bot_member") or {"roles": []}
    return max((r["position"] for r in test["roles"] if r["id"] in member["roles"]), default=0)


def test_only_roles(test, names, keep=KEEP_TEST_ROLES):
    """Roles the real server doesn't have and the bot can delete."""
    top = bot_top_position(test)
    return [r for r in sorted(test["roles"], key=lambda r: -r["position"])
            if names.get(r["id"]) is None and str(r["id"]) != str(test["id"])
            and not r.get("managed") and r["name"] not in keep and r["position"] < top]


def empty_categories(test, cls):
    keep = {c["id"] for c in cls["archives"]} | ({cls["dev_cat"]["id"]} if cls["dev_cat"] else set())
    keep |= {c["id"] for c in cls["cat_match"].values()}
    used = {c.get("parent_id") for c in test["channels"] if c["type"] != CATEGORY}
    return [c for c in test["channels"] if c["type"] == CATEGORY and c["id"] not in keep and c["id"] not in used]


def config_ids(cfg):
    """Every id in a profile of Helpers/variables.py, as strings."""
    out = set()
    for v in cfg.values():
        if isinstance(v, dict):
            out |= config_ids(v)
        elif isinstance(v, int) and not isinstance(v, bool):
            out.add(str(v))
    return out


# ---------------------------------------------------------------- Discord

class Discord:
    def __init__(self, token, writes=False):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bot {token}", "X-Audit-Log-Reason": REASON})
        self.writes = writes
        self.test_channels = set()  # channels read from a test server; the only ones writable

    def check_write(self, method, path):
        """Every write must target a test server or one of its channels."""
        if not self.writes:
            raise RuntimeError(f"refusing {method} {path}: this client is read-only")
        m = re.fullmatch(r"/(guilds|channels)/(\d+)(/.*)?", path)
        if not m:
            raise RuntimeError(f"refusing {method} {path}: not a guild or channel path")
        kind, target = m.group(1), m.group(2)
        if kind == "guilds" and int(target) not in TEST_GUILD_IDS:
            raise RuntimeError(f"refusing {method} {path}: {target} is not a test server")
        if kind == "channels" and target not in self.test_channels:
            raise RuntimeError(f"refusing {method} {path}: channel {target} was not read from a test server")

    def __call__(self, method, path, body=None):
        if method != "GET":
            self.check_write(method, path)
        while True:
            r = self.s.request(method, API + path, json=body, timeout=30)
            if r.status_code == 429:
                time.sleep(float(r.json().get("retry_after", 1)) + 0.25)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:300]}")
            out = r.json() if r.content else None
            if method == "POST" and path.endswith("/channels") and int(path.split("/")[2]) in TEST_GUILD_IDS:
                self.test_channels.add(out["id"])
            return out

    def guild(self, gid):
        g = self("GET", f"/guilds/{gid}")
        g["channels"] = self("GET", f"/guilds/{gid}/channels")
        if int(gid) in TEST_GUILD_IDS:
            self.test_channels |= {c["id"] for c in g["channels"]}
        return g


def guard_target(gid):
    gid = int(gid)
    if gid in PROD_GUILD_IDS or gid not in TEST_GUILD_IDS:
        raise SystemExit(f"refusing to write to guild {gid}: only the two test servers are writable")


def token_profile():
    token = os.getenv("TOKEN", "").strip()
    return token, _PROFILE_BY_APP_ID.get(_app_id_from_token(token) or "")


# ---------------------------------------------------------------- commands

def cmd_snapshot(args):
    token, profile = token_profile()
    if profile != "prod":
        raise SystemExit("snapshot reads the real servers: run it under prodctx")
    d = Discord(token)
    out = {"taken_at": datetime.now(timezone.utc).isoformat(), "guilds": {}, "emojis": []}
    found = {}
    for which, (prod_id, _) in GUILDS.items():
        g = d.guild(prod_id)
        out["guilds"][which] = {"id": str(prod_id), "name": g["name"], "roles": g["roles"], "channels": g["channels"]}
        for e in g.get("emojis", []):
            if e["name"] in STRUCTURE_EMOJIS and e["name"] not in found:
                found[e["name"]] = e
    for name in STRUCTURE_EMOJIS:
        e = found.get(name)
        if e is None:
            print(f"  ! emoji {name} not found in either real server")
            continue
        ext = "gif" if e.get("animated") else "png"
        r = requests.get(f"https://cdn.discordapp.com/emojis/{e['id']}.{ext}", timeout=30)
        r.raise_for_status()
        img = r.content
        out["emojis"].append({"name": name, "animated": bool(e.get("animated")),
                              "image": f"data:image/{ext};base64,{base64.b64encode(img).decode()}"})
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"snapshot of {', '.join(g['name'] for g in out['guilds'].values())} -> {args.out}")


def stages(snap, which, test):
    """All ops for one test server, computed from its current state."""
    want = prod_layout(snap["guilds"][which])
    r_ops, match = role_ops(want, test, bot_top_position(test))
    names = role_names(want, test, match)
    # Either profile: the prod bot logs errors to a channel in the test exec server.
    used = config_ids(_ENV_CONFIG["test"]) | config_ids(_ENV_CONFIG["prod"])
    cls = classify(want, test, used, DEV_CHANNEL_NAMES.get(which, set()),
                   CHANNEL_ALIASES.get(which))
    return {
        "roles": [o for o in r_ops if o["op"] != "role_order"],
        "role_order": [o for o in r_ops if o["op"] == "role_order"],
        "categories": category_ops(want, test, names, cls),
        "channels": channel_ops(want, test, names, cls, test.get("premium_tier", 0)),
        "order": order_op(want, test, cls),
        "emojis": emoji_ops(snap["emojis"], test, which),
    }, cls, names


def describe(op):
    o = op["op"]
    if o == "warn":
        return f"! {op['text']}"
    if o in ("role_edit", "cat_edit", "chan_edit"):
        parts = ", ".join("overwrites" if k == "overwrites" else f"{k} -> {v!r}" for k, v in op["changes"].items())
        return f"{o.split('_')[0]} {op['name']!r}: {parts}"
    if o == "role_create":
        return f"new role {op['name']!r}"
    if o == "cat_create":
        return f"new category {op['name']!r}"
    if o == "chan_create":
        extra = f" (replaces {op['replaces_name']!r}, which goes to Archive)" if op.get("replaces") else ""
        return f"new channel {op['name']!r} in {op['spec']['parent']!r}{extra}"
    if o == "to_dev":
        return f"move {op['name']!r} to {DEV_CATEGORY}"
    if o == "to_archive":
        return f"move {op['name']!r} to {ARCHIVE_CATEGORY}"
    if o == "role_order":
        return f"reorder {len(op['ids'])} roles to prod's order"
    if o == "order":
        return f"reorder categories and channels ({len(op['positions'])} positions)"
    if o == "emoji_create":
        return f"upload emoji :{op['name']}:"
    return o


def load_test(d, gid, bot_id):
    g = d.guild(gid)
    g["_bot_member"] = d("GET", f"/guilds/{gid}/members/{bot_id}")
    return g


def resolve_overwrites(pairs, test, kept, names):
    """Prod role names -> the test role matched to each (duplicate names in the
    test server can't pull an overwrite onto the wrong role)."""
    by_name = {n: i for i, n in names.items() if n}
    out = [{"id": by_name[n], "type": 0, "allow": a, "deny": d} for n, a, d in pairs if n in by_name]
    return out + [{"id": o["id"], "type": o["type"], "allow": str(o["allow"]), "deny": str(o["deny"])} for o in kept]


def archive_parent(d, gid, test, cls):
    """An Archive category with room, creating Archive N when the last is full."""
    count = {}
    for c in test["channels"]:
        if c["type"] != CATEGORY:
            count[c.get("parent_id")] = count.get(c.get("parent_id"), 0) + 1
    for a in cls["archives"]:
        if count.get(a["id"], 0) < CATEGORY_LIMIT:
            return a["id"]
    n = len(cls["archives"]) + 1
    c = d("POST", f"/guilds/{gid}/channels", {"name": ARCHIVE_CATEGORY if n == 1 else f"{ARCHIVE_CATEGORY} {n}", "type": CATEGORY})
    test["channels"].append(c)
    cls["archives"].append(c)
    return c["id"]


def execute(d, gid, op, test, cls, names):
    guard_target(gid)
    if "id" in op and op["id"] not in {c["id"] for c in test["channels"]} | {r["id"] for r in test["roles"]}:
        raise SystemExit(f"refusing {op['op']}: {op['id']} is not in test server {gid}")
    o = op["op"]
    if o == "warn":
        return
    if o == "role_edit":
        d("PATCH", f"/guilds/{gid}/roles/{op['id']}", op["changes"])
    elif o == "role_create":
        d("POST", f"/guilds/{gid}/roles", op["fields"])
    elif o == "role_order":
        n = len(op["ids"])
        d("PATCH", f"/guilds/{gid}/roles", [{"id": i, "position": n - k} for k, i in enumerate(op["ids"])])
    elif o == "cat_create":
        body = {"name": op["name"], "type": CATEGORY}
        if op["overwrites"]:
            body["permission_overwrites"] = resolve_overwrites(op["overwrites"], test, [], names)
        test["channels"].append(d("POST", f"/guilds/{gid}/channels", body))
    elif o in ("cat_edit", "chan_edit"):
        ch = next(c for c in test["channels"] if c["id"] == op["id"])
        body = {}
        for k, v in op["changes"].items():
            if k == "overwrites":
                kept = test_overwrites(ch, names)[1]
                body["permission_overwrites"] = resolve_overwrites(v, test, kept, names)
            elif k == "parent":
                body["parent_id"] = category_id(test, v, cls)
            elif k == "tags":
                old = {t["name"]: t["id"] for t in ch.get("available_tags", [])}
                body["available_tags"] = [{**({"id": old[n]} if n in old else {}), "name": n, "moderated": m, "emoji_name": e}
                                          for n, m, e in v]
            else:
                body[k] = v
        d("PATCH", f"/channels/{op['id']}", body)
    elif o == "chan_create":
        s = op["spec"]
        body = {"name": s["name"], "type": s["type"], "parent_id": category_id(test, s["parent"], cls),
                "permission_overwrites": resolve_overwrites(s["overwrites"], test, [], names),
                "topic": s["topic"], "nsfw": s["nsfw"], "rate_limit_per_user": s["rate_limit_per_user"]}
        for k in ("bitrate", "user_limit"):
            if s.get(k):
                body[k] = s[k]
        for k in ("default_sort_order", "default_forum_layout"):
            if s.get(k) is not None:
                body[k] = s[k]
        if s.get("tags"):
            body["available_tags"] = [{"name": n, "moderated": m, "emoji_name": e} for n, m, e in s["tags"]]
        d("POST", f"/guilds/{gid}/channels", body)
        if op.get("replaces"):
            parent = archive_parent(d, gid, test, cls)
            d("PATCH", f"/channels/{op['replaces']}", {"parent_id": parent})
            next(c for c in test["channels"] if c["id"] == op["replaces"])["parent_id"] = parent
    elif o == "to_dev":
        d("PATCH", f"/channels/{op['id']}", {"parent_id": category_id(test, DEV_CATEGORY, cls)})
    elif o == "to_archive":
        parent = archive_parent(d, gid, test, cls)
        d("PATCH", f"/channels/{op['id']}", {"parent_id": parent})
        next(c for c in test["channels"] if c["id"] == op["id"])["parent_id"] = parent
    elif o == "order":
        d("PATCH", f"/guilds/{gid}/channels", op["positions"])
    elif o == "emoji_create":
        d("POST", f"/guilds/{gid}/emojis", {"name": op["name"], "image": op["image"]})


def renumber_roles(d, gid, test):
    """New roles all land at position 1 and Discord doesn't renumber the rest,
    so the bot's top role keeps its old number while more roles sit below it
    and a reorder is refused. A no-op move makes Discord renumber them."""
    positions = [r["position"] for r in test["roles"]]
    if len(positions) == len(set(positions)):
        return False
    top = bot_top_position(test)
    movable = [r for r in test["roles"] if 0 < r["position"] < top and not r.get("managed")]
    if not movable:
        return False
    low = min(movable, key=lambda r: r["position"])
    d("PATCH", f"/guilds/{gid}/roles", [{"id": low["id"], "position": low["position"]}])
    return True


def category_id(test, name, cls=None):
    """The test category standing for prod's category `name`: the matched one
    when there is one, else the (just created) category of that name."""
    if name is None:
        return None
    for i, label in ((cls or {}).get("parent_label") or {}).items():
        if label == name:
            return i
    found = [c["id"] for c in test["channels"] if c["type"] == CATEGORY and c["name"] == name]
    if not found:
        raise SystemExit(f"no category {name!r} in the test server; run apply again from the categories stage")
    return found[0]


def cmd_sync(args):
    token, profile = token_profile()
    if profile != "test":
        raise SystemExit("plan/apply/check write to the test servers: use the dev bot's token, never prodctx")
    with open(args.snapshot, encoding="utf-8") as f:
        snap = json.load(f)
    writes = args.command == "apply" and args.yes
    d = Discord(token, writes=writes)
    bot_id = d("GET", "/users/@me")["id"]
    drift = 0
    for which in (["main", "exec"] if args.guild == "both" else [args.guild]):
        gid = GUILDS[which][1]
        guard_target(gid)
        test = load_test(d, gid, bot_id)
        print(f"\n== {test['name']} (test {which}) <- {snap['guilds'][which]['name']}")
        if not writes:
            ops, cls, names = stages(snap, which, test)
            for stage, items in ops.items():
                if items:
                    print(f"  {stage}:")
                    for op in items:
                        print(f"    {describe(op)}")
            drift += sum(1 for v in ops.values() for o in v if o["op"] != "warn")
            extra = empty_categories(test, cls) + test_only_roles(test, names)
            for c in extra:
                kind = "role" if "color" in c else "empty category"
                flag = "--delete-test-only-roles" if kind == "role" else "--delete-empty-categories"
                print(f"  {kind} {c['name']!r} (deleted only with {flag})")
            drift += len(extra)
            top = bot_top_position(test)
            stuck = [r for r in test["roles"] if names.get(r["id"]) is None and str(r["id"]) != str(test["id"])
                     and not r.get("managed") and r["name"] not in KEEP_TEST_ROLES and r["position"] >= top]
            for r in stuck:
                print(f"  ! test-only role {r['name']!r} is above the dev bot's role; move the bot's role up to delete it")
            drift += len(stuck)
            continue
        for stage in ("roles", "role_order", "categories", "channels", "order", "emojis"):
            if stage == "role_order" and renumber_roles(d, gid, test):
                test = load_test(d, gid, bot_id)
            ops, cls, names = stages(snap, which, test)
            for op in ops[stage]:
                print(f"  {describe(op)}")
                execute(d, gid, op, test, cls, names)
            if any(o["op"] != "warn" for o in ops[stage]):
                test = load_test(d, gid, bot_id)
        _, cls, names = stages(snap, which, test)
        if args.delete_empty_categories:
            for c in empty_categories(test, cls):
                print(f"  delete empty category {c['name']!r}")
                d("DELETE", f"/channels/{c['id']}")
        if args.delete_test_only_roles:
            for r in test_only_roles(test, names):
                print(f"  delete role {r['name']!r}")
                d("DELETE", f"/guilds/{gid}/roles/{r['id']}")
    if args.command == "apply" and not args.yes:
        print("\nNothing was changed. Run again with --yes to apply.")
    if args.command == "check":
        print("\nin sync" if not drift else f"\n{drift} differences")
        sys.exit(1 if drift else 0)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--out", required=True)
    for name in ("plan", "apply", "check"):
        s = sub.add_parser(name)
        s.add_argument("--snapshot", required=True)
        s.add_argument("--guild", choices=["main", "exec", "both"], default="both")
        if name == "apply":
            s.add_argument("--yes", action="store_true", help="actually make the changes")
            s.add_argument("--delete-empty-categories", action="store_true")
            s.add_argument("--delete-test-only-roles", action="store_true")
    args = p.parse_args()
    (cmd_snapshot if args.command == "snapshot" else cmd_sync)(args)


if __name__ == "__main__":
    main()
