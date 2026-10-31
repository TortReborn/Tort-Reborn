"""
Copy a forum's posts into bot panels, so posts a webhook (or anyone else) made
become posts the bot owns and the website can edit (TAQ-115). The text never
passes through this repo: it goes from Discord to an export file you keep
outside it, and from there into the panel tables.

    # 1. read the posts (any bot that can see the forum; prod: via prodctx)
    prodctx python Tort-Reborn/scripts/copy_forum_posts.py export <forum id> <export.json>
    # 2. turn them into panels: images go to the bucket, ids are translated
    #    for the test servers under the dev bot; writes <panels.json> to review
    python scripts/copy_forum_posts.py build <export.json> <panels.json>
    # 3. queue them; the bot opens one new post per panel, oldest first
    python scripts/copy_forum_posts.py queue <panels.json> --author "Name"
    # 4. once published, point links at the old posts to the new ones
    python scripts/copy_forum_posts.py relink <panels.json> --author "Name"
    # 5. check every new post against its original, field by field
    python scripts/copy_forum_posts.py compare <export.json> <panels.json>

Reactions are not copied (a reaction menu becomes role buttons, added to
panels.json by hand before queueing); compare lists them so nothing is lost
unnoticed. The old posts are left alone: delete them once compare is clean.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import local_ids  # noqa: E402
from Helpers.bot_panels import ASSET_PREFIX, ASSET_SCHEME, BLANK_FIELD_NAME  # noqa: E402

API = "https://discord.com/api/v10"
IMAGE_TYPES = {"png", "jpg", "jpeg", "gif", "webp"}


def _get(path: str, raw_url: str | None = None):
    headers = {"Authorization": f"Bot {os.environ['TOKEN']}"} if raw_url is None else {}
    while True:
        r = requests.get(raw_url or API + path, headers=headers, timeout=30)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 1)) + 0.25)
            continue
        r.raise_for_status()
        return r.content if raw_url else r.json()


def _thread_messages(thread_id: str) -> list[dict]:
    return list(reversed(_get(f"/channels/{thread_id}/messages?limit=100")))


def _ext(url_or_name: str) -> str:
    return url_or_name.split("?")[0].rsplit(".", 1)[-1].lower()


def _forum_threads(forum_id: str) -> list[dict]:
    forum = _get(f"/channels/{forum_id}")
    active = [t for t in _get(f"/guilds/{forum['guild_id']}/threads/active")["threads"] if t["parent_id"] == forum_id]
    archived = _get(f"/channels/{forum_id}/threads/archived/public?limit=100")["threads"]
    return sorted({t["id"]: t for t in active + archived}.values(), key=lambda t: int(t["id"]))


# ---- export ----

def export(forum_id: str, out: str):
    """Every post in the forum with its messages, images downloaded (Discord's
    image links expire, so the bytes are kept)."""
    posts = []
    for t in _forum_threads(forum_id):
        messages = _thread_messages(t["id"])
        for m in messages:
            urls = [a["url"] for a in m.get("attachments", [])]
            urls += [e[k]["url"] for e in m["embeds"] for k in ("image", "thumbnail") if (e.get(k) or {}).get("url")]
            m["_images"] = {u: base64.b64encode(_get("", raw_url=u)).decode() for u in urls
                            if "cdn.discordapp.com" in u or "media.discordapp.net" in u}
        posts.append({"thread": t, "messages": messages})
        print(f"  {t['name']}: {len(messages)} message(s)")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"forum_id": forum_id, "posts": posts}, f, ensure_ascii=False)
    print(f"Exported {len(posts)} post(s) to {out}")


# ---- build ----

def slug_for(name: str) -> str:
    """TAq-Website's slugFromName, for the names a forum post can have."""
    import unicodedata
    base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return (base if len(base) >= 3 else f"{base}-panel".lstrip("-"))[:48].rstrip("-")


class _Assets:
    def __init__(self, images: dict[str, str], dry_run: bool):
        self.images, self.dry_run = images, dry_run

    def __call__(self, url: str | None) -> str | None:
        """A Discord-hosted image becomes a panel asset; other links stay links."""
        if not url or url not in self.images:
            return url
        data = base64.b64decode(self.images[url])
        ext = _ext(url)
        if ext not in IMAGE_TYPES:
            raise SystemExit(f"Can't copy image {url}: .{ext} isn't an image type panels take.")
        name = f"{hashlib.sha256(data).hexdigest()[:16]}.{ext}"
        if not self.dry_run:
            from Helpers.storage import storage
            storage.put_bytes(ASSET_PREFIX + name, data, content_type="image/jpeg" if ext == "jpg" else f"image/{ext}")
        return ASSET_SCHEME + name


def _embed(e: dict, assets: _Assets) -> dict:
    out = {"color": e.get("color")}
    for key in ("title", "url", "description"):
        if e.get(key):
            out[key] = e[key]
    if e.get("author", {}).get("name"):
        out["author"] = {k: v for k, v in e["author"].items() if k in ("name", "url", "icon_url")}
    if e.get("footer", {}).get("text"):
        out["footer"] = {"text": e["footer"]["text"], **({"icon_url": e["footer"]["icon_url"]} if e["footer"].get("icon_url") else {})}
    for key in ("image", "thumbnail"):
        if (e.get(key) or {}).get("url"):
            out[key] = assets(e[key]["url"])
    if e.get("fields"):
        out["fields"] = [{"name": f["name"], "value": f["value"], "inline": bool(f.get("inline"))} for f in e["fields"]]
    return out


# A webhook post can carry :shortcode: text that Discord's chat box would have
# turned into an emoji; the copy carries the emoji itself, so it reads the same
# in Discord and in the editor's preview. Custom emojis (<:name:id>) and times
# (10:30:45) don't match.
_SHORTCODE = re.compile(r"(?<![<\w]):([a-z0-9_+\-]+):(?!\d)")
# Names Discord gives emojis that the emoji package's aliases lack
DISCORD_EMOJI_NAMES = {"tools": "\U0001F6E0️"}


def _emoji_for(m: re.Match) -> str:
    if m[1] in DISCORD_EMOJI_NAMES:
        return DISCORD_EMOJI_NAMES[m[1]]
    import emoji
    return emoji.emojize(m[0], language="alias")


def emojize(v):
    """`v` (a string or JSON-like value) with standard emoji shortcodes as the emoji."""
    if isinstance(v, str):
        return _SHORTCODE.sub(_emoji_for, v)
    if isinstance(v, list):
        return [emojize(x) for x in v]
    if isinstance(v, dict):
        return {k: emojize(x) for k, x in v.items()}
    return v


def build(export_file: str, out: str, dry_run: bool = False):
    data = json.load(open(export_file, encoding="utf-8"))
    panels = []
    for post in data["posts"]:
        t, messages = post["thread"], []
        for m in post["messages"]:
            assets = _Assets(m["_images"], dry_run)
            who = m["author"]["username"] + (" (webhook)" if m.get("webhook_id") else "")
            skipped = [e.get("type") for e in m["embeds"] if e.get("type") != "rich"]
            if skipped:
                print(f"  {t['name']}: left out {who}'s link previews {skipped}; Discord makes those itself")
            if m.get("reactions"):
                print(f"  {t['name']}: {who}'s message has reactions {[r['emoji'].get('name') for r in m['reactions']]}; "
                      "add role buttons by hand if they hand out roles")
            msg = {"content": m.get("content") or "",
                   "images": [assets(a["url"]) for a in m.get("attachments", [])],
                   "embeds": [_embed(e, assets) for e in m["embeds"] if e.get("type") == "rich"]}
            messages.append({k: v for k, v in msg.items() if v})
        content = emojize({"messages": messages, "post": {"title": t["name"]}})
        left = sorted(set(_SHORTCODE.findall(json.dumps(content, ensure_ascii=False))))
        if left:
            print(f"  {t['name']}: shortcodes with no standard emoji, left as text: {left}")
        panels.append({
            "slug": slug_for(t["name"]), "name": t["name"], "source_thread_id": t["id"],
            "channel_id": local_ids.channel(int(data["forum_id"])),
            "content": local_ids.data(content),
        })
    with open(out, "w", encoding="utf-8") as f:
        json.dump(panels, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(panels)} panel(s) to {out}: {', '.join(p['slug'] for p in panels)}")


# ---- queue / relink ----

def _db():
    from Helpers.database import DB
    db = DB()
    db.connect()
    return db


def queue(panels_file: str, author: str):
    """Create each panel with a pending revision 1; refuse if a slug is taken,
    so a second run can't post the forum twice. Each revision gets its own
    timestamp, so the bot opens the posts in the forum's original order."""
    panels = json.load(open(panels_file, encoding="utf-8"))
    db = _db()
    try:
        db.cursor.execute("SELECT slug FROM bot_panels WHERE slug = ANY(%s)", ([p["slug"] for p in panels],))
        taken = [r[0] for r in db.cursor.fetchall()]
        if taken:
            raise SystemExit(f"These panels already exist; nothing queued: {', '.join(taken)}")
        for p in panels:
            db.cursor.execute(
                "INSERT INTO bot_panels (slug, name, message_ids) VALUES (%s, %s, '{}') RETURNING id",
                (p["slug"], p["name"]),
            )
            db.cursor.execute(
                """
                INSERT INTO bot_panel_revisions (panel_id, rev_number, content, channel_id, note, author_name, created_at)
                VALUES (%s, 1, %s, %s, 'Copied from the forum post the bot did not own', %s, clock_timestamp())
                """,
                (db.cursor.fetchone()[0], json.dumps(p["content"]), p["channel_id"], author),
            )
        db.connection.commit()
        print(f"Queued {len(panels)} panel(s); the bot opens their posts within a minute.")
    finally:
        db.close()


def _published(db, slugs) -> dict[str, tuple[int, int, int, dict]]:
    """slug -> (panel id, post id, latest rev number, latest content), published panels only."""
    db.cursor.execute(
        """
        SELECT p.slug, p.id, p.channel_id, r.rev_number, r.content
        FROM bot_panels p
        JOIN LATERAL (SELECT rev_number, content FROM bot_panel_revisions
                      WHERE panel_id = p.id ORDER BY rev_number DESC LIMIT 1) r ON TRUE
        WHERE p.slug = ANY(%s) AND p.published_rev IS NOT NULL
        """,
        (list(slugs),),
    )
    return {r[0]: (r[1], r[2], r[3], r[4] if isinstance(r[4], dict) else json.loads(r[4])) for r in db.cursor.fetchall()}


def relink(panels_file: str, author: str):
    """Mentions and links of the old posts, in any of these panels, now point
    at the new posts (a new revision, only where something changed)."""
    panels = json.load(open(panels_file, encoding="utf-8"))
    db = _db()
    try:
        live = _published(db, [p["slug"] for p in panels])
        missing = [p["slug"] for p in panels if p["slug"] not in live]
        if missing:
            raise SystemExit(f"Not published yet: {', '.join(missing)}")
        moved = {p["source_thread_id"]: str(live[p["slug"]][1]) for p in panels}
        old_ids = "|".join(moved)

        def swap(s):
            s = re.sub(rf"<#({old_ids})>", lambda m: f"<#{moved[m[1]]}>", s)
            return re.sub(rf"(discord\.com/channels/\d+/)({old_ids})\b", lambda m: m[1] + moved[m[2]], s)

        def walk(v):
            if isinstance(v, str):
                return swap(v)
            if isinstance(v, list):
                return [walk(x) for x in v]
            if isinstance(v, dict):
                return {k: walk(x) for k, x in v.items()}
            return v

        changed = 0
        for p in panels:
            panel_id, post_id, rev, content = live[p["slug"]]
            new = walk(content)
            if new == content:
                continue
            db.cursor.execute(
                """
                INSERT INTO bot_panel_revisions (panel_id, rev_number, content, channel_id, note, author_name)
                VALUES (%s, %s, %s, %s, 'Links to the old forum posts point at the new ones', %s)
                """,
                (panel_id, rev + 1, json.dumps(new), p["channel_id"], author),
            )
            changed += 1
            print(f"  {p['slug']}: relinked")
        db.connection.commit()
        print(f"{changed} panel(s) relinked.")
    finally:
        db.close()


# ---- compare ----

def _image_hashes(m: dict, images: dict[str, bytes] | None = None) -> dict:
    """sha256 of each attachment and embed image, in order."""
    def h(url):
        raw = images[url] if images and url in images else _get("", raw_url=url)
        return hashlib.sha256(raw).hexdigest()[:16]
    return {
        "attachments": [h(a["url"]) for a in m.get("attachments", [])],
        "embed images": [h(e[k]["url"]) for e in m["embeds"] if e.get("type") == "rich"
                         for k in ("image", "thumbnail") if (e.get(k) or {}).get("url")],
    }


def _shape(m: dict, translate) -> dict:
    """What a reader sees of a message, minus images (compared by bytes). An
    invisible field heading reads the same as none."""
    rich = [e for e in m["embeds"] if e.get("type") == "rich"]
    embeds = [translate({k: v for k, v in _embed(e, lambda u: None).items() if k not in ("image", "thumbnail")})
              for e in rich]
    for e in embeds:
        for f in e.get("fields", []):
            f["name"] = "" if f["name"] == BLANK_FIELD_NAME else f["name"]
    return {"content": translate(m.get("content") or ""), "embeds": embeds}


def _first_difference(a, b, path="") -> str:
    """Where two shapes first part ways, and both values there."""
    if type(a) is not type(b) or not isinstance(a, (dict, list)):
        return f"{path or 'value'}: was {a!r:.200} now {b!r:.200}"
    keys = sorted(set(a) | set(b)) if isinstance(a, dict) else range(max(len(a), len(b)))
    for k in keys:
        x = a.get(k) if isinstance(a, dict) else (a[k] if k < len(a) else None)
        y = b.get(k) if isinstance(b, dict) else (b[k] if k < len(b) else None)
        if x != y:
            return _first_difference(x, y, f"{path}.{k}" if path else str(k))
    return path


def compare(export_file: str, panels_file: str) -> bool:
    data = json.load(open(export_file, encoding="utf-8"))
    panels = {p["source_thread_id"]: p for p in json.load(open(panels_file, encoding="utf-8"))}
    db = _db()
    try:
        live = _published(db, [p["slug"] for p in panels.values()])
    finally:
        db.close()
    moved = {tid: str(live[p["slug"]][1]) for tid, p in panels.items() if p["slug"] in live}

    def translate(v):
        v = local_ids.data(emojize(v))
        s = json.dumps(v, ensure_ascii=False)
        for old, new in moved.items():
            s = s.replace(f"<#{old}>", f"<#{new}>").replace(f"/{old}", f"/{new}")
        return json.loads(s)

    clean = True
    for post in data["posts"]:
        t = post["thread"]
        p = panels.get(t["id"])
        if p is None or p["slug"] not in live:
            print(f"\n✗ {t['name']}: no published panel")
            clean = False
            continue
        new_post = _get(f"/channels/{moved[t['id']]}")
        new_msgs = _thread_messages(new_post["id"])
        problems, extras = [], []
        if new_post["name"] != t["name"]:
            problems.append(f"title {new_post['name']!r} (was {t['name']!r})")
        old_msgs = post["messages"]
        for i in range(max(len(old_msgs), len(new_msgs))):
            o = old_msgs[i] if i < len(old_msgs) else None
            n = new_msgs[i] if i < len(new_msgs) else None
            where = f"message {i + 1}"
            if o is None:
                extras.append(f"{where} is new ({'box' if n.get('flags', 0) & 32768 else 'embeds'})")
                continue
            if n is None:
                problems.append(f"{where} is missing")
                continue
            if n.get("flags", 0) & 32768:
                extras.append(f"{where} is now a box (was {o['author']['username']}'s message); compare it by eye")
                continue
            was, now = _shape(o, translate), _shape(n, lambda v: v)
            if was != now:
                problems.append(f"{where}: {_first_difference(was, now)}")
            old_images = {u: base64.b64decode(b) for u, b in o["_images"].items()}
            if _image_hashes(o, old_images) != _image_hashes(n):
                problems.append(f"{where}: images differ {_image_hashes(o, old_images)} vs {_image_hashes(n)}")
            buttons = [c.get("label") or c.get("emoji", {}).get("name") for row in n.get("components", []) for c in row.get("components", [])]
            if buttons:
                extras.append(f"{where} has buttons {buttons}")
            if o.get("reactions"):
                extras.append(f"{where} had reactions {[r['emoji'].get('name') for r in o['reactions']]} (not copied)")
        mark = "✗" if problems else "✓"
        clean &= not problems
        print(f"\n{mark} {t['name']}: {len(new_msgs)} message(s) in post {new_post['id']}")
        for line in problems:
            print(f"    differs: {line}")
        for line in extras:
            print(f"    on purpose? {line}")
    print("\nAll posts match their originals." if clean else "\nSome posts differ; see above.")
    return clean


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("export"); s.add_argument("forum_id"); s.add_argument("out")
    s = sub.add_parser("build"); s.add_argument("export"); s.add_argument("out"); s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("queue"); s.add_argument("panels"); s.add_argument("--author", required=True)
    s = sub.add_parser("relink"); s.add_argument("panels"); s.add_argument("--author", required=True)
    s = sub.add_parser("compare"); s.add_argument("export"); s.add_argument("panels")
    a = ap.parse_args()
    if a.cmd == "export":
        export(a.forum_id, a.out)
    elif a.cmd == "build":
        build(a.export, a.out, a.dry_run)
    elif a.cmd == "queue":
        queue(a.panels, a.author)
    elif a.cmd == "relink":
        relink(a.panels, a.author)
    else:
        sys.exit(0 if compare(a.export, a.panels) else 1)


if __name__ == "__main__":
    main()
