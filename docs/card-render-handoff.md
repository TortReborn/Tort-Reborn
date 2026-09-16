# Card Render Handoff

Repo: `/home/ken/Dokumente/projects/TAq/Tort-Reborn`

Date: 2026-09-16

## Current Worktree

Modified files:

- `Helpers/card_render.py`
- `Helpers/cards.py`

New tracked assets:

- `images/profile/tiers/tier_0.png` .. `tier_3.png` (7x7 pixel-art stars,
  copied from `/home/ken/Downloads/tiers/tier_N_clean.png`). Put here rather
  than under `images/cards/` because that directory is gitignored as a
  runtime art-download cache; these are static assets that need to ship.

## Round 2: tier indicator, dropped fusion border/star text

Follow-up pass on top of the "Implemented" section below.

- Removed the fusion ring (`FUSION_RING`, `_ring_colour`, `_fusion_progress`)
  and the prismatic maxed-out border (`_prismatic_border`, `PRISMATIC`)
  entirely, per the user: drop those border lines "for now." Nothing else
  in the codebase referenced them (confirmed via grep), so they were deleted
  rather than left dead.
- Removed the star-row / "MAX" text under the badges (`_readable` also went
  with it since it had no other caller).
- Added a "Tier" indicator: 3 stars in the top-right corner of the art
  panel, drawn from `images/profile/tiers/tier_{0..3}.png` scaled 4x with
  nearest-neighbour resampling (matches the pixel style `generate_badge`
  uses elsewhere).
- New `_tier_level(stars, max_stars)` maps the existing fusion state onto a
  0-3 level: `min(3, stars + max(0, 3 - max_stars))`. Rarities with a
  shorter fusion ladder (epic max_stars=2, legendary/fabled max_stars=1)
  start from a higher base level so reaching *their own* max always lands
  on tier 3, rather than everything being measured against common's 4-star
  ladder. Member/mythic cards have no ladder (`max_stars=0`) and always
  render tier 3. This mapping was worked out interactively with the user —
  see the conversation for the reasoning if it needs revisiting.
- `_draw_tier_indicator` always draws all 3 icons: the leftmost `3 - level`
  are `tier_0.png`, the rightmost `level` are `tier_{level}.png`. So tier 0
  is 3x tier_0, tier 1 is 2x tier_0 + 1x tier_1, tier 2 is 1x tier_0 + 2x
  tier_2, tier 3 is 3x tier_3.
- Name plate (and rarity/rank badges under it) moved from vertically
  centred in the leftover space below the art to anchored near the top of
  that space (`top = ny + 8` instead of a centred `block` calculation) —
  the centring was leaving a large empty gap above the name.

Not yet done / worth checking next:

- No live Discord render verified yet for this round; only local PNG
  previews (see below).
- `render_spread` still calls `render_card` without `stars`/`max_stars`, so
  non-member cards default to `stars=0` with `max_stars` computed from the
  rarity ladder (tier 0, three tier_0 stars). Member cards have no ladder
  entry (`max_stars_for` returns 0 for a rank name), so they still show
  tier 3 there. This mirrors an existing shortcut (spreads never carried
  fusion state before this round either); not touched.

## User Goal

Update the fishing/card graphics:

- Rarity display should use the existing TAq pixel badge style from profile/raid card rendering.
- 1/1 member cards should present as a new Mythic rarity.
- Mythic color should be dark purple.
- Member-card rank should become a secondary badge, not the primary rarity.
- Card border should resemble profile-style gradient borders with stronger rarity color.
- Remove the old divider line.
- Remove the extra pale/white edge line from the border.
- Keep command behavior and card economy unchanged.

## Implemented

### `Helpers/card_render.py`

- Imports `generate_badge` from `Helpers.functions`.
- Imports `discord_ranks` from `Helpers.variables`.
- Adds `MYTHIC = "mythic"` and `MYTHIC_COLOR = "#6b1fa2"`.
- Extends `TIERS` entries with `badge` colors for rarity badge rendering.
- Removes rank-as-rarity styling from the old `RANK_TIERS` map.
- Adds `_mix`, `_badge`, and `_rank_badge` helpers.
- Uses `generate_badge(...)` for the rarity badge.
- Treats member cards as `MYTHIC` for visual rendering when a rank badge is supplied.
- Uses the member's TAq rank color for the secondary rank badge.
- Replaces the old line-outline border with a full-card gradient border.
- Removes the old divider line below the art.
- Removes the pale one-pixel rounded rectangle edge that looked like a white border.
- Keeps the fusion ring and max prismatic border behavior.
- In `render_spread`, passes member rank through so member cards render as Mythic there too.
- In `card_file`, defaults member-card `badge` to `card.get("rank")`.

### `Helpers/cards.py`

- Changes member embed color from pink to Mythic purple:

```python
"member": 0x6B1FA2
```

## Visual Preview Files

Generated previews were copied to:

```text
/home/ken/Downloads/card-previews
```

Files:

```text
kansard_0star.png
kansard_1star.png
kansard_2star.png
kansard_3star.png
kansard_4star.png
```

The max card preview was visually checked. It shows `MAX` and the prismatic max border.

Earlier temporary preview paths, if still present:

```text
/tmp/card_static_sample.png
/tmp/card_member_sample.png
/tmp/kansard_0star.png
/tmp/kansard_1star.png
/tmp/kansard_2star.png
/tmp/kansard_3star.png
/tmp/kansard_4star.png
```

## Verification Already Run

```bash
.venv/bin/python -m py_compile Helpers/card_render.py Helpers/cards.py Commands/cards.py Tasks/card_reel_refresh.py Helpers/card_copy.py
.venv/bin/python -m pytest tests/test_card_set.py tests/test_card_discard.py
git diff --check
```

Results:

- Compile passed.
- Focused card tests passed: `10 passed`.
- `git diff --check` passed.

## Local Preview Command

To regenerate a full common-card fusion ladder:

```bash
.venv/bin/python - <<'PY'
from Helpers.card_render import render_card
from Helpers import cards

card = next(c for c in cards.load_card_set()["cards"] if c["tier"] == "common")
max_stars = cards.tier_max_stars(card)

for stars in range(max_stars + 1):
    img = render_card(card["name"], card["tier"], card["slug"], card["image_url"], stars=stars, max_stars=max_stars)
    path = f"/home/ken/Downloads/card-previews/{card['slug']}_{stars}star.png"
    img.save(path)
    print(path)
PY
```

To generate one Mythic/member-style preview:

```bash
.venv/bin/python - <<'PY'
from Helpers.card_render import render_card

member = {
    "name": "KenSample",
    "tier": "Hydra",
    "slug": "member-kensample",
    "image_url": "",
    "rank": "Hydra",
    "member": True,
}

img = render_card(member["name"], member["tier"], member["slug"], member["image_url"], badge=member["rank"], stars=0, max_stars=0)
path = "/home/ken/Downloads/card-previews/member_mythic_sample.png"
img.save(path)
print(path)
PY
```

## Important Diagnosis From Logs

The user pasted startup/test-server logs with `Unknown interaction` errors for `/pool view`.

Diagnosis:

- Not caused by the card graphics change.
- The failures occurred at `await ctx.defer()` in `Commands/cards.py` before DB lookup or rendering.
- Telemetry showed high command queue times, for example `queue_ms` around 15-44 seconds.
- Discord interactions expire if not acknowledged quickly, so this was startup/event-loop pressure.
- Supporting signs: loop lag spikes, DB pool exhaustion retry, startup tasks, background warmup.
- A later `/pool view` succeeded once queue time dropped, though DB connect/render/upload made it slow.

## Current Caveats

- No live Discord render was verified after the latest pale-edge removal.
- Local PNG rendering was verified visually.
- If another agent continues, first run `git status --short` to confirm no new user edits appeared.
