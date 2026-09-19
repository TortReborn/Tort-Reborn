# TAQ-95: Remove TEST_MODE — identity-derived config, single secret set

Ticket: https://www.the-aquarium.com/exec/requests?ticket=95

## Ticket (verbatim scope)

> Remove the TEST_MODE system from Tort-Reborn.
>
> Problem: a single env boolean separates a local run from booting into prod
> Discord with prod credentials, because .env carries both secret sets
> (TOKEN/TEST_TOKEN, DB_*/TEST_DB_*). The 2026-09-17 .env BOM incident was one
> bad parse away from a local bot joining prod with prod DB. The TEST_* pairs
> also duplicate every config surface.

No comments at time of writing; scope is the ticket description.

## Why now

- The Supabase→Railway bucket migration already removed the bucket half of
  TEST_MODE (per-bucket credentials made the switch dead). This finishes the job.
- Sequenced **before** the Neon→Railway Postgres cutover (separate work), so the
  cutover flips one clean `DB_*` set with no `TEST_DB_*` ghosts.

## Design

TEST_MODE currently does two unrelated jobs. They get opposite treatments.

### Job 1: secret selection → one unprefixed set

`TOKEN`, `DB_*`, and `SHEETS_SCRIPT_URL` become the only spellings. The local
`.env` holds **dev values only** (test bot token, local Postgres, dev bucket —
`S3_*` is already single-set). Prod values exist only in the Railway worker's
service variables. `TEST_TOKEN`, `TEST_DB_*`, `TEST_SHEETS_SCRIPT_URL`, and
`TEST_MODE` are deleted from code, `.env.example`, and (post-merge) Railway.

Consequence: prod credentials no longer exist on developer machines.

### Job 2: Discord wiring → profile derived from identity

`Helpers/variables.py` keeps both `_ENV_CONFIG` dicts (channel/role/emoji IDs
belong in code, not in fifty env vars). What changes is the selector:

```python
_PROFILE_BY_APP_ID = {
    "1364828461813862441": "prod",   # Tort
    "1400600774031183982": "test",   # dev bot
}
```

A Discord bot token's first dot-segment is base64url of the application id, so
the profile is derived at import time with no network call. Resolution order:

1. `TOKEN` set → decode app id → look up profile. Unknown app id or undecodable
   token → **raise at import (fail-closed)**. Identity always wins; a
   `BOT_PROFILE` that disagrees with the token is an error.
2. No `TOKEN` → `BOT_PROFILE` env var (`prod`/`test`) — for pytest and offline
   scripts only.
3. Neither → raise.

`IS_TEST_MODE` is replaced by `PROFILE` (string) and `IS_TEST_PROFILE` (bool)
exported from `variables.py`. Consumers switch mechanically.

### Gates become capability checks where sensible

- `Helpers/sheets.py`: posts iff `SHEETS_SCRIPT_URL` is set (dev leaves it
  unset or points at a test sheet). No profile check.
- `Helpers/storage.py` missing-background fallback: keys off `IS_TEST_PROFILE`
  (unchanged semantics — prod still raises so errors surface).
- Guild-bucket lists (`TAQ_GUILD_IDS` etc.) key off `PROFILE` as today.

## Touched surface

`Helpers/variables.py` (selector + exports), `Helpers/database.py`,
`Helpers/sheets.py`, `Helpers/storage.py`, `Helpers/aspect_db.py`, `main.py`,
`Commands/aspects.py`, `Commands/shell_exchange.py`, `Commands/top_wars.py`,
`Tasks/annihilation_announcements.py`, `Helpers/functions.py`, `scripts/*`
(mechanical), `tests/conftest.py` + affected tests, `.env.example`, `README.md`.
`Archive/` is dead code and is left as-is.

## Verification

- New unit tests: token→app-id decode, profile resolution incl. fail-closed
  paths (unknown app id, disagreeing BOT_PROFILE, missing both).
- Full suite green under `BOT_PROFILE=test` with no `TEST_*` vars present.
- Local boot on the dev token joins only test guilds and selects test wiring.
- Prod deploy check: worker variables carry no `TEST_*` after cleanup;
  bot logs show profile `prod` at startup.

## Deploy / rollout

1. Merge; Railway auto-deploys (worker still has TEST_* vars — now unread).
2. Delete `TEST_MODE`, `TEST_TOKEN`, `TEST_DB_*` from the worker service.
3. Update local `.env`s: drop TEST_* spellings, move dev values into the bare
   names, remove prod credentials from the machine.

## Known workflow change

Read-only prod REST sweeps used the prod `TOKEN` from `.env`. After this, fetch
it on demand (`railway variables` CLI or the dashboard) — prod secrets are not
kept on disk.

## Out of scope

- TAq-Website's mirror TEST_MODE system (follow-up ticket).
- Neon→Railway Postgres migration (separate, sequenced after).
