# Command latency investigation

**Status:** Phase 0 is deployed and analysed — see [Findings](#findings--first-two-days-of-telemetry). Phase 1 (threading + de-cold) and Phase 1.5 (the follow-up sweep: default timeout, full `http.*` coverage, remaining loop-hygiene sites, per-phase snapshot checkouts, pool observability) are **implemented and awaiting deploy measurement**. Still open: task-loop staggering, the parked fleet split, and the [Still open](#still-open) items. After deploy: capture, run `scripts/analyze_telemetry.py`, diff against the Findings baseline.

## Symptom

After a quiet period, the first slash command takes a very long time to return. Subsequent
commands are noticeably faster for a while, then the slowness returns. The pattern reads as
something "going to sleep" and needing to wake up.

## Architecture snapshot

Single Railway **worker** service — one process, one event loop, one persistent Discord gateway
socket (see `Procfile`, `main.py`). No public port.

| Dependency | Access pattern | Entry point |
|---|---|---|
| Neon Postgres (pooler endpoint) | psycopg2, one connect+close **per helper call** | `DB` in `Helpers/database.py` |
| Supabase Storage (S3 API) | boto3, lazily-created singleton client | `S3Storage` in `Helpers/storage.py` |
| Wynncraft v3 / Mojang / visage / Athena | blocking `requests.get`, new session per call | `Helpers/functions.py`, `Guild` in `Helpers/classes.py` |
| OpenAI | blocking | `Helpers/openai_helper.py` |

Sharing that one event loop: the command cogs in `Commands/`, the task loops in `Tasks/`
(intervals from 10s to daily), and Pillow card rendering.

## Ruled out

The "something is asleep" model does not survive the evidence:

- **Neon autosuspend.** `Tasks/territory_tracker.py` runs on a 10-second loop and opens two fresh
  Neon connections per pass (`_read_territories_sync`, `saveTerritoryData`). Autosuspend requires
  several minutes of zero compute activity. The database never gets there.
- **Railway app sleep.** Sleep applies to HTTP-triggered services. This is a worker with no public
  port and a permanently open gateway socket. If sleep were active the bot would disconnect
  outright rather than merely slow down.
- **In-process caches expiring.** The `lru_cache` font cache and UUID/name cache in
  `Helpers/functions.py`, and the Athena guild-colour cache in `Commands/snipe.py`, live for the
  process lifetime or hours. None of them cycle on the timescale of the reported symptom.

## Hypotheses, ranked

*This was the ranking before any measurement. The [Findings](#findings--first-two-days-of-telemetry)
section has the measured result, which reordered these: H2 is the dominant cost and H1 is
secondary.*

**H1 — Event-loop contention with the background task fleet.** *(pre-measurement: best guess)*
The task loops in `Tasks/` run on 1/2/3/5/10-minute cycles and several perform blocking work
directly on the event loop. A command arriving mid-cycle queues behind that work; one arriving in
a gap returns immediately. The apparent sleep/wake periodicity is the task schedule.
`Tasks/update_member_data.py` is the prime suspect — it runs every three minutes and iterates the
full guild roster.

**H2 — Cold connection paths after idle.** *(pre-measurement: contributing; measured: dominant)*
Every database helper opens a fresh TLS connection to Neon. Every outbound API call builds a new
`requests` session, so nothing reuses a socket. botocore's connection pool idles out. Under
sustained traffic some of this amortises; after a quiet spell everything re-handshakes at once. A
single `/profile` invocation can open several Neon connections and several TLS sessions.

**H3 — Upstream edge-cache misses.** *(external)*
A cold Wynncraft API cache for a given player accounts for part of the first-call cost. Not
fixable here; worth measuring so it can be subtracted from the budget.

## Structural problems found

These are worth fixing regardless of which hypothesis wins.

1. **No database connection pool.** Every helper in `Helpers/database.py` constructs a `DB`,
   connects, and closes. One command can pay that cost five or more times. A process-lifetime
   `ThreadedConnectionPool` (or a move to `psycopg_pool`) removes it.
2. **Blocking I/O on the event loop.** Many `requests.get` call sites live in `Commands/`.
   `Commands/profile.py` is representative: it correctly moves `PlayerStats` to a thread, then
   fetches the avatar synchronously, hits S3 synchronously, and renders the Pillow card inline.
   This stalls the gateway heartbeat, not just other commands.
3. **No shared HTTP session.** There is no keep-alive anywhere in the outbound path.
4. **No latency instrumentation.** Nothing measured where time goes, so everything above is
   inference rather than measurement. This is the gap Phase 0 closes.

## Plan

### Phase 0 — measure (implemented)

Instrumentation is in place. It is measurement-only and behaviourally neutral: it wraps existing
code and times it, changing no runtime behaviour, so the numbers form a valid baseline to judge
Phase 1 against. Toggle with the `LATENCY_TELEMETRY` env var — any of `0` / `false` / `no`
(case-insensitive) disables it; default on.

What it emits — one JSON line per record to **stdout** (deliberately not the Discord log channel,
so it never spams `#logs`):

- `command` — one per slash-command invocation: `queue_ms` (the gap between Discord creating the
  interaction and the bot starting to run it), `total_ms`, `ok`, and `buckets` splitting time by
  `db.connect` / `db.query` / `db.commit` / `http.<host>` / `s3.get` / `s3.put` / `render`.
- `loop_lag` — emitted whenever a single one-second tick drifts more than 250 ms.
- `loop_lag_summary` — once a minute: p50 / p95 / max drift.
- `probe` — only when `scripts/latency_probe.py` is run by hand (idle → cold timings → warm
  timings → delta). Read-only, and not part of the deployed bot.

Scope limits to keep in mind when reading the data:

- Timing records are **command-scoped**. Background task loops call the same DB and HTTP layers but
  run outside any command, so their own calls emit no bucket record. Their impact shows up
  indirectly, through `loop_lag` and through `queue_ms` on commands that land mid-cycle.
- All outbound GETs — shared helpers and every command file — route through the timed wrapper, so
  the `http.*` picture is complete. (`Commands/aspects.py` uses aiohttp, by design.)
- A command cancelled mid-flight (shutdown, gateway disconnect) records `ok: true`, because
  py-cord swallows the cancellation before the timing hook runs. Documented, not fixed.

### Reading the telemetry

Read stdout in this order:

1. `loop_lag_summary` p95 first. Seconds-scale drift means the event loop is being blocked — direct
   support for H1.
2. `queue_ms` outliers on `command` records, cross-referenced against task-loop start times
   (`update_member_data` logs a `STARTING LOOP` marker).
3. Only if those look clean, the `buckets` split on the slowest `command` records.
4. `scripts/latency_probe.py --idle-minutes 10` (or more), run separately, to settle H2 on its own
   terms: it runs with no event loop and no task loops, so a cold/warm delta there is attributable
   to connections going cold rather than to loop contention.

The decision that falls out: whether Phase 1 needs the task fleet split off the event loop (H1), or
whether pooling and HTTP session reuse are enough (H2).

### Capturing the data (Railway retention)

Railway keeps logs for a limited window by plan — 7 days on Hobby, 30 on Pro. Telemetry lands on
the same stdout stream as the normal logs, interleaved; filter with
`railway logs | grep '"type":' | jq`.

Two consequences:

- The measurement window must fit inside retention. This is an active, bounded measurement (cycles
  are minutes-to-hours apart, so a couple of days suffices), so 7 days is enough — but don't deploy
  and leave it; the early data ages out.
- Anything worth keeping past the window — in particular the baseline run to diff Phase 1 against —
  must be pulled down deliberately: `railway logs > phase0-baseline.jsonl` during the run, or
  forward logs to an external sink (Railway suggests Vector / Fluent Bit / OTEL). For a one-off
  investigation, teeing to a file is enough; a forwarder is overkill.

### Findings — first two days of telemetry

Measured over roughly the first day and a half after deployment (~1,800 one-minute drift windows,
~70 drift excursions, 15 commands). Low command traffic, which is itself part of the story.

- **The event loop is healthy at baseline.** p95 drift is ~2 ms (median across all windows), p90
  ~2.5 ms. A congested loop is ruled out. Brief single-tick bumps occur (about a quarter of minutes
  touch >100 ms once) but nothing sustained.
- **Every command is slow — 2 to 6.5 seconds — and the cost is cold external I/O.** Supabase S3
  reads dominate: `s3.get` ranged 0.7–4.0 s. Then outbound HTTP (visage avatar, Wynncraft) and
  fresh DB connects (0.1–0.7 s each, no pool).
- **H2 (cold connections) is confirmed on real traffic.** Two `profile` calls a minute apart showed
  `s3.get` drop from ~3.95 s to ~0.73 s — a ~5× cold penalty. With traffic this sparse, paths are
  almost always cold, so nearly every command is a first-after-idle. That is the reported symptom.
- **Commands block the loop themselves.** The largest drift excursions (up to ~5.1 s) coincide with
  commands, because the card-render path runs S3, HTTP and Pillow inline on the event loop. A slow
  command stalls the loop for everyone during it.
- **H1 (task contention) is real but secondary.** About half the excursions are
  `update_member_data`'s recurring ~300 ms stalls; the rest are other task loops. None of this is
  the main driver of command latency.

The data redirects Phase 1: the event loop is not congested, so **splitting the task fleet into its
own service — the largest planned change — is not justified.** The win is getting command I/O off
the loop and making it not-cold.

#### Component benchmark (pre-deploy, real prod services)

| Component | Current path | Proposed path | Gain |
|---|---|---|---|
| DB access | fresh connect+query 436 ms | pooled query 153 ms | 2.8× |
| Wynncraft GET | fresh session 456 ms | shared session 141 ms | 3.2× |
| Visage GET | fresh session 252 ms (max 1611) | shared session 71 ms | 3.5× |
| Background read | S3 293 ms | memory hit ~0 ms | — |

Pipeline: sequential-cold ≈ 2648 ms measured → ~700–900 ms expected warm. (Sequential-threaded:
the avatar URL needs `player.UUID`, which only exists after `PlayerStats` returns, so the avatar
fetch runs after it rather than in parallel; with keep-alive that costs ~70 ms and avoids a
duplicate UUID lookup.)

### Phase 1 — fixes, evidence-ranked

1. **Get command I/O off the loop and de-cold it** — **implemented.** What shipped:
   - Shared keep-alive `requests.Session` behind `timed_get` (stateless: block-all cookie policy,
     so the three Wynncraft token identities share nothing but sockets).
   - Connection pool behind `DB` (`ThreadedConnectionPool`, keyed per config with a
     `use_pool=False` opt-out; max `DB_POOL_MAX`, default 10; checkout
     timed in the same `db.connect` bucket; rollback-on-return; broken connections discarded;
     bounded retry on pool exhaustion, fast-fail on real connection errors). `PlayerStats` and the
     daily snapshot task check out only after their external HTTP completes, so slots are never
     held across slow fetches.
   - Background memory cache with write-through invalidation in `save_background` — a background
     change shows on the very next render; reads are ~0 ms.
   - **S3 avatar cache deleted.** Its reads (0.97–1.6 s in prod) cost more than the fresh visage
     fetch they were avoiding (~71 ms with keep-alive). Skins are now fetched fresh per render —
     a skin change shows on the next render, bounded only by visage's own CDN.
   - `/profile`'s card build (Pillow + avatar fetch) moved into a worker thread — renders no
     longer stall the event loop for everyone else.
2. **Stagger task-loop start offsets** so `update_member_data` and siblings do not all fire on the
   same minute boundary — cheap mitigation for the ~300 ms task stalls. Not started.
3. **Split the task fleet into its own service** — parked. Not justified while the loop is healthy;
   revisit only if traffic grows enough to congest it.

#### Phase 1.5 — follow-ups (implemented)

- `timed_get` applies a **default 15 s timeout** (explicit timeouts win). A hung upstream now
  fails loudly instead of pinning a session socket and its worker thread forever. Every swept
  call site's exception handling was verified to treat the new `Timeout` like any other request
  failure.
- **Every `requests.get` under `Commands/` is swept** through `timed_get` — direct calls and the
  `to_thread(requests.get, …)` callable form (the callable form evaded the call-pattern grep
  twice; three sites in snipe/lootpool and one in worlds were caught on the second and third
  passes).
- **Loop hygiene:** `manage`'s shell modal and shells render path, and `new_member`, now defer
  first and run their blocking work (HTTP → then DB checkout) in worker threads via module-level
  sync helpers — the `/profile` pattern. The shell modal also reports helper failures instead of
  stranding the deferred interaction at "thinking…" (modal errors bypass the command error
  handler), and the snipe log posts without its image rather than erroring after the success
  embed was already sent.
- **`daily_activity_snapshot` takes per-phase checkouts** — no pool slot is held across a
  per-member API fetch or a retry sleep; the write phase keeps its original single commit.
- **Pool exhaustion is observable:** `DB.connect` logs a WARN (`"DB pool exhausted, retrying"`)
  whenever the bounded retry fires — the direct signal that `DB_POOL_MAX` needs raising.

#### Still open

- Task-loop start-offset staggering (Phase 1 item 2) — decide after the post-deploy capture
  shows how much task-loop stall remains.
- The task-fleet split (item 3) — parked; revisit only if traffic grows enough to congest the
  loop.

(`manage rank`/`link` loop hygiene and the `BasicPlayerStats` error path — previously listed
here — are fixed: rank batches its permission reads into one brief checkout and persists via a
second, link resolves the UUID before connecting and reports an unresolvable ign instead of
crashing, and `BasicPlayerStats` sets `error=True` when the player-data fetch fails.)

`queue_ms` recorded null for every command in the first window because `discord.Interaction`
(py-cord 2.6) has no `created_at`; it is now derived from the interaction snowflake id, so the
queue-delay discriminator works from the next deploy onward.

## Picking this back up

Phase 1 item 1 is implemented; the next action is deploy-and-measure, not code. Capture a window
(`railway logs --service worker --since 48h --lines 5000 --json > after.json`), run
`scripts/analyze_telemetry.py after.json`, and diff against the Findings baseline. Success bar:
profile `total_ms` median under ~1 s warm, `db.connect` near-zero after the first sample (which
includes one-time pool construction — use the median, not the max), and no command-coincident loop
excursions from the render path. Note `http.visage.surgeplay.com` gains events it did not have in
Phase 0 (raids' avatar fetch is newly routed through `timed_get`), so compare that bucket on
latency-per-event, not count. Bucket names were kept stable throughout, so before/after captures
are directly comparable. Then work the follow-ups list above, starting with the `timed_get`
default-timeout decision.
