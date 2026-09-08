"""Build the runtime card set (data/cards.json) from the mined corpus.

The corpus in data/card_corpus.json is the full mining result and keeps every
character that cleared the dialogue-line cutoff, including ones the wiki has no
picture of. The playable set only takes characters with usable art, so rarity
tiers are recomputed over that subset rather than inherited from the corpus.

Run after re-mining the wiki or after curating the corpus:

    python scripts/build_card_set.py [--download]

--download pre-fetches every card image into the images/cards cache so the
first /reel of a card is not waiting on the wiki.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(BASE, "data", "card_corpus.json")
OUT = os.path.join(BASE, "data", "cards.json")
ART_CACHE = os.path.join(BASE, "images", "cards")

# Share of the set each tier occupies, top down. Kept equal to the corpus
# shares the tiers were originally cut at so the ladder shape survives the
# art-only filter.
TIER_CUM = [
    ("legendary", 0.02),
    ("epic", 0.10),
    ("rare", 0.30),
    ("uncommon", 0.60),
    ("common", 1.00),
]

UA = {"User-Agent": "TortRebornCards/1.0 (TAq guild bot)"}


def slugify(name: str, taken: set) -> str:
    """Stable id for the database. Collisions get a numeric suffix."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "card"
    s = s[:56]
    slug, n = s, 2
    while slug in taken:
        slug = f"{s}-{n}"
        n += 1
    taken.add(slug)
    return slug


def assign_tiers(cards: list) -> None:
    """Tier by line-count rank. Equal line counts never split across a tier."""
    cards.sort(key=lambda c: -c["lines"])
    n, idx = len(cards), 0
    for tier, cum in TIER_CUM:
        limit = round(cum * n)
        while idx < limit and idx < n:
            lc = cards[idx]["lines"]
            while idx < n and cards[idx]["lines"] == lc:
                cards[idx]["tier"] = tier
                idx += 1
            if idx >= limit and tier != "common":
                break


def download_art(cards: list) -> None:
    os.makedirs(ART_CACHE, exist_ok=True)
    done = 0
    for c in cards:
        path = os.path.join(ART_CACHE, c["slug"] + ".png")
        if os.path.exists(path):
            continue
        req = urllib.request.Request(c["image_url"], headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                data = r.read()
            with open(path, "wb") as f:
                f.write(data)
            done += 1
            time.sleep(0.25)
        except Exception as e:
            print(f"  ! {c['name']}: {e}")
    print(f"downloaded {done} new images into images/cards")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true",
                    help="pre-fetch card art into the image cache")
    args = ap.parse_args()

    with open(CORPUS, encoding="utf-8") as f:
        corpus = json.load(f)

    playable = [c for c in corpus if c.get("image_url")]
    if not playable:
        print("no cards with art in the corpus", file=sys.stderr)
        return 1

    taken = set()
    cards = []
    for c in playable:
        cards.append({
            "slug": slugify(c["name"], taken),
            "name": c["name"],
            "lines": c["lines"],
            "wiki_url": c.get("wiki_url", ""),
            "image_url": c["image_url"],
        })
    assign_tiers(cards)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "data/card_corpus.json (art-only)",
        "count": len(cards),
        "cards": cards,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print(f"wrote {OUT} — {len(cards)} cards "
          f"(dropped {len(corpus) - len(cards)} without art)")
    for tier, _ in TIER_CUM:
        rs = [c for c in cards if c["tier"] == tier]
        print(f"  {tier:10s} {len(rs):4d} ({len(rs) / len(cards) * 100:4.1f}%)  "
              f"lines {min(c['lines'] for c in rs)}-{max(c['lines'] for c in rs)}")

    if args.download:
        download_art(cards)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
