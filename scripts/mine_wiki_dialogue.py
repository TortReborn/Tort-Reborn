"""Re-mine the wiki for character dialogue, both formats, all page types.

The first pass missed two things, both found via Afelis:
  * it only read quest and secret-discovery pages, so dialogue living on an
    NPC's own page was invisible
  * it only understood *'''Speaker:''' text, not the {{Dialogue|colour|Speaker|
    text}} template the wiki uses on newer and non-quest pages

Both are handled here. Language subpages (/kr, /hu, /zh ...) are excluded
outright — they repeat the same dialogue and would double-count anyone whose
quest has been translated.
"""

import csv
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://wynncraft.wiki.gg/api.php"
UA = {"User-Agent": "TortRebornCards/2.0 (TAq guild bot; contact: aiden)"}
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, ".wiki_search_cache.json")

MIN_LINES = 8
TIER_CUM = [("legendary", 0.02), ("epic", 0.10), ("rare", 0.30),
            ("uncommon", 0.60), ("common", 1.00)]

# Curation carried over from the first pass, all of it verified by hand.
DROP = {"???"}                      # a generic label, not a character
BAD_RENAME = {                      # search fallback matched the wrong thing
    "The Canary Calls": "Canary", "Fishy Zombie": "Fisherman",
    "Pirate Cove": "Pirate", "Hyhet": "Gendarme Commander",
    "Sky Grapes": "Grapes", "Dramele": "Volmor", "Mellow Mango": "Mango",
    "The Maiden Tower": "Maiden", "Vagabond (Disambiguation)": "Vagabond",
    "Villagers": "Villager", "Doguns": "Dogun",
}
REJECT = {"Tunnel Dweller Chieftain", "Garoth's Journal", "Sol", "Blueberry",
          "Guard Golem", "Teleportation Mech", "??? (Wynn Plains Monument)"}

QUEST_RE = re.compile(r"^\*+\s*'''(.{1,120}?)'''", re.M)
TMPL_RE = re.compile(r"\{\{\s*Dialogue\s*\|[^|}]*\|([^|}]*)\|", re.I)
LINK_RE = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]|]*)\]\]")
TAG_RE = re.compile(r"<[^>]+>")
COUNTER_RE = re.compile(r"^\[\d+/\d+\]\s*")

JUNK_EXACT = {"dialogue", "note", "warning", "info", "objective", "reward",
              "hint", "you", "player", "player?", "narrator", "tip", "rewards",
              "old", "new", "fortune cookie", "list of supplies", "bush"}
JUNK_PAT = re.compile(
    r"^\d|^log \d|\d+ (ap|bp|eb|xp|le)$|^\[.*\] \d+ (ap|bp)$|years ago$", re.I)


def qmark_name(name: str, title: str) -> str:
    """Key a withheld-identity speaker to the page it spoke on.

    The wiki writes ??? for a speaker whose identity a quest is holding back.
    Pooling every one of them into a single name invents a character that
    speaks 382 lines across 62 unrelated quests, which is how a mob screenshot
    ended up as the third-rarest card in the set.

    Several of them do have a page of their own, disambiguated by where they
    appear -- ??? (A Hunter's Calling) is a real NPC with real art. Keying by
    page lets those resolve normally; the ones with no page behind them stay
    unresolved, pick up no art, and fall out when the playable set is built.
    """
    if name != "???":
        return name
    return title if title.startswith("???") else f"??? ({title})"


def api(params):
    params = dict(params, format="json", formatversion="2")
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    for a in range(6):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            time.sleep(20 * (a + 1) if e.code == 429 else 3 * (a + 1))
        except Exception:
            time.sleep(3 * (a + 1))
    raise RuntimeError("api failed")


def paged(params, key, listname, contkey):
    out, cont = [], {}
    while True:
        d = api({**params, **cont})
        out += [p["title"] for p in d["query"][listname]]
        if "continue" not in d:
            return out
        cont = {contkey: d["continue"][contkey]}


def category(cat):
    return paged({"action": "query", "list": "categorymembers",
                  "cmtitle": f"Category:{cat}", "cmlimit": "500",
                  "cmnamespace": "0"}, "title", "categorymembers", "cmcontinue")


def transclusions(template):
    return paged({"action": "query", "list": "embeddedin",
                  "eititle": template, "eilimit": "500", "einamespace": "0"},
                 "title", "embeddedin", "eicontinue")


def clean_speaker(raw):
    s = TAG_RE.sub("", LINK_RE.sub(r"\1", raw)).replace("''", "").strip()
    s = COUNTER_RE.sub("", s)
    if ":" in s:
        s = s.split(":", 1)[0].strip()
    s = re.sub(r"\s*\((?!1\.)[^)]*\)$", "", s).strip()
    if not s or len(s) > 45:
        return None
    low = s.lower()
    if low in JUNK_EXACT or JUNK_PAT.search(low):
        return None
    if not re.search(r"[A-Za-z?]", s):
        return None
    return s


def fetch_wikitext(titles):
    out = {}
    for i in range(0, len(titles), 50):
        d = api({"action": "query", "prop": "revisions", "rvprop": "content",
                 "rvslots": "main", "titles": "|".join(titles[i:i + 50])})
        for p in d["query"]["pages"]:
            rv = p.get("revisions")
            if rv:
                out[p["title"]] = rv[0]["slots"]["main"]["content"]
        time.sleep(0.25)
        print(f"    {min(i + 50, len(titles))}/{len(titles)}")
    return out


BAD_TITLE = re.compile(r"[#<>\[\]|{}]")


def resolve_pages(names):
    """Map a spoken name to its wiki page. Names MediaWiki cannot accept as a
    title are skipped rather than failing the batch they sit in."""
    resolved = {n: None for n in names if BAD_TITLE.search(n)}
    names = [n for n in names if not BAD_TITLE.search(n)]
    for i in range(0, len(names), 50):
        batch = names[i:i + 50]
        d = api({"action": "query", "titles": "|".join(batch), "redirects": "1"})
        q = d.get("query")
        if not q:
            for n in batch:
                resolved[n] = None
            continue
        norm = {n["from"]: n["to"] for n in q.get("normalized", [])}
        redir = {n["from"]: n["to"] for n in q.get("redirects", [])}
        exists = {p["title"] for p in q["pages"] if "missing" not in p}
        for n in batch:
            t = redir.get(norm.get(n, n), norm.get(n, n))
            resolved[n] = t if t in exists else None
        time.sleep(0.2)
        print(f"    resolved {min(i + 50, len(names))}/{len(names)}")
    return resolved


def search_fallback(name, cache):
    if name in cache:
        return cache[name]
    d = api({"action": "query", "list": "search", "srsearch": name,
             "srlimit": "3", "srnamespace": "0"})
    hit = None
    nl = name.lower()
    for h in d["query"]["search"]:
        t = h["title"]
        if nl == t.lower() or nl in re.split(r"[\s'\-]+", t.lower()):
            hit = t
            break
    cache[name] = hit
    json.dump(cache, open(CACHE, "w", encoding="utf-8"))
    time.sleep(0.7)
    return hit


def fetch_images(titles):
    """Lead image, falling back to a File:<Name>.png upload."""
    out = {}
    for i in range(0, len(titles), 50):
        d = api({"action": "query", "prop": "pageimages",
                 "piprop": "original|thumbnail", "pithumbsize": "400",
                 "titles": "|".join(titles[i:i + 50])})
        for p in d["query"]["pages"]:
            img = (p.get("original") or {}).get("source") \
                or (p.get("thumbnail") or {}).get("source")
            if img:
                out[p["title"]] = img
        time.sleep(0.25)
        print(f"    images {min(i + 50, len(titles))}/{len(titles)}")
    return out


def fetch_file_images(names):
    out = {}
    cands = []
    for n in names:
        cands += [(n, f"File:{n}.png"), (n, f"File:{n.replace(' ', '')}.png")]
    for i in range(0, len(cands), 50):
        batch = cands[i:i + 50]
        d = api({"action": "query", "prop": "imageinfo", "iiprop": "url",
                 "titles": "|".join(t for _, t in batch)})
        q = d["query"]
        norm = {x["from"]: x["to"] for x in q.get("normalized", [])}
        by = {p["title"]: p for p in q["pages"]}
        for name, t in batch:
            p = by.get(norm.get(t, t))
            if p and "missing" not in p and p.get("imageinfo"):
                out.setdefault(name, p["imageinfo"][0]["url"])
        time.sleep(0.25)
    return out


def assign_tiers(cards):
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


def main():
    print("gathering pages...")
    quests = category("Quests")
    discoveries = category("Secret Discoveries")
    npcs = category("NPCs")
    tmpl = transclusions("Template:Dialogue")

    # Language subpages repeat the same dialogue; every legitimate page here
    # is a bare title, so any "/" is a translation or a list page.
    def ok(t):
        return "/" not in t
    pages = sorted({p for p in quests + discoveries + npcs + tmpl if ok(p)})
    print(f"  quests {len(quests)}  discoveries {len(discoveries)}  "
          f"npcs {len(npcs)}  template {len(tmpl)}")
    print(f"  unique pages after dropping translations: {len(pages)}")

    print("fetching wikitext...")
    cache_file = os.path.join(HERE, ".wikitext_cache.json")
    if os.path.exists(cache_file):
        texts = json.load(open(cache_file, encoding="utf-8"))
        if set(texts) >= set(pages) - {p for p in pages if p not in texts}:
            print(f"  reusing cached wikitext for {len(texts)} pages")
        else:
            texts = fetch_wikitext(pages)
            json.dump(texts, open(cache_file, "w", encoding="utf-8"))
    else:
        texts = fetch_wikitext(pages)
        json.dump(texts, open(cache_file, "w", encoding="utf-8"))

    counts, sources, removed_only, fmt = {}, {}, {}, {}
    for title, text in texts.items():
        is_removed = "{{removed" in text[:400].lower()
        found = [(m, "quest") for m in QUEST_RE.findall(text)]
        found += [(m, "template") for m in TMPL_RE.findall(text)]
        for raw, kind in found:
            name = clean_speaker(raw)
            if not name:
                continue
            name = qmark_name(name, title)
            counts[name] = counts.get(name, 0) + 1
            sources.setdefault(name, set()).add(title)
            fmt.setdefault(name, set()).add(kind)
            removed_only[name] = removed_only.get(name, True) and is_removed

    print(f"\ndistinct speakers: {len(counts)}  "
          f"lines: {sum(counts.values()):,}")

    cands = [n for n, c in counts.items() if c >= 2]
    print(f"resolving pages for {len(cands)} candidates...")
    resolved = resolve_pages(cands)

    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    unresolved = [n for n in cands if resolved[n] is None and counts[n] >= MIN_LINES]
    print(f"search fallback for {len(unresolved)}...")
    for j, n in enumerate(unresolved, 1):
        resolved[n] = search_fallback(n, cache)
        if j % 40 == 0:
            print(f"    {j}/{len(unresolved)}")

    # merge speakers that resolve to the same page
    merged = {}
    for n in cands:
        page = resolved.get(n)
        key = BAD_RENAME.get(page, page) if page else n
        if page and page in BAD_RENAME:
            page = None                      # bogus match, keep the spoken name
        e = merged.setdefault(key, {"lines": 0, "names": [], "page": page,
                                    "sources": set(), "removed": True,
                                    "fmt": set()})
        e["lines"] += counts[n]
        e["names"].append(n)
        e["sources"] |= sources[n]
        e["fmt"] |= fmt[n]
        e["removed"] = e["removed"] and removed_only[n]

    chars = [{"name": k, "aliases": sorted(v["names"]), "page": v["page"],
              "lines": v["lines"], "quests": sorted(v["sources"]),
              "removed_only": v["removed"], "formats": sorted(v["fmt"])}
             for k, v in merged.items()
             if k not in DROP and k not in REJECT]
    chars.sort(key=lambda c: -c["lines"])

    corpus = [c for c in chars if c["lines"] >= MIN_LINES]
    print(f"\ncharacters at >= {MIN_LINES} lines: {len(corpus)}")

    pages_with = sorted({c["page"] for c in corpus if c["page"]})
    print(f"fetching images for {len(pages_with)} pages...")
    images = fetch_images(pages_with)
    need = [c["name"] for c in corpus
            if not (c["page"] and images.get(c["page"]))]
    print(f"file-namespace fallback for {len(need)}...")
    images_by_name = fetch_file_images(need)
    print(f"  recovered {len(images_by_name)}")

    rows = []
    for c in corpus:
        page = c["page"]
        img = (images.get(page) if page else None) or images_by_name.get(c["name"], "")
        rows.append({
            "name": c["name"],
            "tier": "",
            "lines": c["lines"],
            "quests": len(c["quests"]),
            "aliases": "; ".join(a for a in c["aliases"] if a != c["name"]),
            "removed_content_only": c["removed_only"],
            "formats": ",".join(c["formats"]),
            "wiki_url": ("https://wynncraft.wiki.gg/wiki/"
                         + urllib.parse.quote(page.replace(" ", "_"))) if page else "",
            "image_url": img,
            "appears_in": " | ".join(c["quests"][:12]),
        })

    assign_tiers(rows)
    rows.sort(key=lambda r: -r["lines"])

    with open(os.path.join(REPO, "data", "card_corpus.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    with open(os.path.join(REPO, "data", "card_corpus.csv"), "w", encoding="utf-8-sig",
              newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    art = sum(1 for r in rows if r["image_url"])
    print(f"\ncorpus: {len(rows)} characters, {art} with art")
    for tier, _ in TIER_CUM:
        rs = [r for r in rows if r["tier"] == tier]
        a = sum(1 for r in rs if r["image_url"])
        print(f"  {tier:10s} {len(rs):4d}  lines {min(r['lines'] for r in rs)}"
              f"-{max(r['lines'] for r in rs)}  art {a}")
    print("\ntop 15:")
    for r in rows[:15]:
        print(f"  {r['lines']:5d}  {r['name']:26s} {r['formats']}")


if __name__ == "__main__":
    main()
