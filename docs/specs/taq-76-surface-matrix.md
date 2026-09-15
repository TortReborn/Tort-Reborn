# TAQ-76 — surface-by-surface review matrix

The change set is large; the surfaces it touches are not. Sign off one row at a time. For each surface: who can invoke it (Discord's declared permission, then the runtime gate the code applies), what it did before, what it does now, what the tests prove, and what still needs a human on the test guild.

"Rank gate" means `check_reset_permission` / the equivalent: the actor needs a linked account with a recognised rank and may only act on members ranked strictly below them. "Narwhal+" means `discord_ranks` index ≥ Narwhal. Where a row says *unchanged*, the gate is byte-for-byte the same code path as `main`.

Legend for the last two columns — **T** = covered by an automated test on this branch; **H** = needs a human pass on the test guild (`docs/specs/taq-76-rollout.md` step 5 checklist).

## A. Bot — slash commands

| Surface | Invoke: Discord → runtime | Before | After | Proof | Human |
| --- | --- | --- | --- | --- | --- |
| `/reset_roles user` | `manage_roles` → `manage_roles` + rank gate (unchanged) | Own copy of removal; read `was_*` flags; left `discord_links` untouched (rank stayed) | `remove_member()`: honorifics from ledger, roles stripped, nick cleared, **rank → NULL**, `rank_at_leave` stamped. Unlinked target still allowed (plain Ex-Member). | T: round-trips, equal-rank refusal, unlinked target | H: real role edit on test guild |
| `/new_member user ign` | `manage_roles` → `manage_roles` (unchanged) | Wrote `linked=TRUE`, rank, `wars_on_join`, honorific snapshot | `record_registration()`: identity upsert (refuses a uuid another account holds), rank, stint attach, honorific roles held → ledger rows, clears `guild_leave_pending`. Same roles added/removed. | T: registration path in removal round-trips | H: one real registration |
| `/manage rank user rank` | `manage_roles` (group) → linked initiator w/ recognised rank, target below, chosen rank below (unchanged) | Unlinked target → `LinkAccount` modal wrote `linked=FALSE` + rank; uuid optional | Same gates; NULL-rank target now allowed (treated as unranked). Modal writes identity + rank; **requires a resolvable Minecraft name**. | T: `_rank_lookup` None-safety via removal tests | H: modal path once |
| `/manage link user ign` | `manage_roles` (group) → none beyond Discord (unchanged) | Insert with `rank = nickname prefix`, `linked=FALSE` | Identity only (`upsert_identity`); **rank untouched**. Conflict → same `LinkConflictError` message. | T: `upsert_identity` conflict | H: link an unlinked user; confirm no rank |
| `/manage unlink user` **(new)** | `manage_roles` (group) → linked initiator, target below | — | Deletes the identity row, writes `audit_log`. Roles not touched (message says use `/reset_roles`). | — | H: unlink + relink |
| `/manage shells` | `manage_roles` (group) → none beyond Discord (unchanged) | Modal path for unlinked user inserted a uuid-less row | Modal path establishes a real identity (uuid required); conflict reported | — | H: shells on an unlinked user |
| `/register ally user ign guild` | `manage_roles` (group) → Moderator role+ (unchanged) | Upsert with `linked=TRUE`, ally rank | Identity + ally rank (`Navigator`, FK-valid). Allies are not on the roster → never "members". | — | H: one ally registration |
| `/stale-roles` | `manage_roles` → `manage_roles` (unchanged) | Guild API + Discord member fetch, per-row removal copy | One query (ranked link not on roster) + `remove_member()` per row. Rank gate per row unchanged. | T: `fetch_stale_taq_links`, shaping | H: run once, expect 0 |
| `/rankcheck` | `administrator` → `administrator` (unchanged) | Compared in-game vs `discord_links.rank` | Same; skips NULL-rank links instead of erroring | — | H: run once, compare counts to `main` |
| `/promo_wave` | `manage_roles` (unchanged) | `.index(rank)` would raise on `''`/unknown | Refuses cleanly ("no rank on record"); otherwise unchanged | — | — |
| `/honorifics lookup` **(new)** | `manage_roles` → `manage_roles` | — | Read-only; resolves user → link → Mojang; shows open + revoked grants | T: ledger history | H: lookup a known Honored Fish |
| `/honorifics grant` **(new)** | `manage_roles` → `manage_roles` + linked rank; **Retired Chief: Narwhal+** | — | Ledger row (idempotent) + role if present | T: `can_manage`, grant idempotency | H: grant + see role |
| `/honorifics revoke` **(new)** | `manage_roles` → `manage_roles` + **Narwhal+** | — | Closes row + removes role if present | T: revoke closes, never deletes | H: revoke + see role gone |

## B. Bot — user (right-click) commands

| Surface | Invoke | Before | After | Proof | Human |
| --- | --- | --- | --- | --- | --- |
| `Member \| Remove` | `manage_roles` → `manage_roles` + rank gate (unchanged); **unlinked target now allowed** (was refused) | Own removal copy | `remove_member()` | T: Retired Chief round-trip, initiator-unlinked refusal | H: one real removal |
| `Member \| Register` | unchanged | unchanged (`NewMember` modal) | Modal writes via `record_registration()` | T: shared path | H: once |
| `Rank \| Promote` / `Demote` | unchanged | unchanged | Unchanged code; NULL rank reads as "not recognised" (same message shape as before) | — | — |

## C. Bot — buttons, events, tasks

| Surface | Invoke | Before | After | Proof | Human |
| --- | --- | --- | --- | --- | --- |
| Guild-log **Reset roles** button | anyone who sees the channel → `manage_roles`-equivalent via rank gate against `last_rank` | — | Member present → `remove_member()`; already Ex-Member → noop; gone from Discord → noop. Second press reports the first resolver. | T: all four branches, race | H: press on a real leave |
| **Reset + Honored Fish** button | rank gate | — | As above with grant; gone from Discord → **ledger row only** (the ticket's case) | T | H |
| **Reset + Retired Chief** button | rank gate + **Narwhal+** | — | As above | T: Hammerhead refused | H |
| Per-leaver guild-log post | `update_member_data` step 3 | One batched "Guild Members Left" embed to both log channels | `BOT_LOG`: batched embed (unchanged). `GUILD_LOG`: one message per leaver with buttons (no buttons if unlinked); > 10 leavers → batched embed | T: post + fallback | H: one real leave |
| Roster sync (`step 3a`) | every 3-min cycle | — | Upserts `guild_roster` (150 rows), opens/closes stints. Embeds still driven by the old cache diff, so no duplicate posts. | T: `sync_roster` open/close/rename; 150-s live run | — |
| Auto-registration (`_auto_register_joined_member`) | on in-game join + sweep | Pending = `linked=FALSE AND app_channel` | Pending = accepted guild app + identity row + `rank IS NULL` + no completed stint since the app. **Existing member who re-applies is no longer re-registered.** | T: predicate shape | H: accept a test app, join |
| `on_member_join` | Discord join | Welcome line | Welcome line + honorifics on record → roles + guild-log line. Failure → error channel, welcome unaffected. | — | H: rejoin with a grant on record |
| Promotion queue `remove` | website queues; bot re-verifies queuer rank ≥ Hammerhead, outranks target (unchanged) | `_do_remove` own copy + **DELETE** identity row | `remove_member(grant=grant_honorific)`; Retired Chief from a sub-Narwhal queuer rejected | T: full stack, grant, idempotent | H: one queued removal |
| Promotion queue `promote`/`demote` | unchanged | unchanged | unchanged | — | — |
| Application accept (`app_commands`, `process_website_decisions`) | unchanged | `_link_discord` wrote `linked=FALSE` + `app_channel` (could unlink a member) | Identity only | — | H: accept one test app end-to-end |
| `check_apps` auto-close, `app_expiry`, guild-leave monitor | tasks | `linked = TRUE` = joined | `APPLICANT_HAS_JOINED_SQL` (on roster, or stint since app − 7 d) | T: SQL shape | — |
| Cards eligibility, colour sync, chat bridge, snipe name→uuid | tasks / commands | `linked` + roster by hand | roster | smoke on prod copy: 42 eligible, 148 colours | — |

## D. Website

| Surface | Gate before | Gate after | Proof | Human |
| --- | --- | --- | --- | --- |
| Login (`/api/auth/discord/callback`) | any `discord_links` row → cookie | row **and on roster**; else `not_in_guild` → `/unauthorized` (Chronicle-only session still minted) | T: `memberCheckFromLookup` | H: log in as a member, as an ex-member (expect refusal) |
| `requireExecSession` (56 routes) | rank ∈ EXEC_RANKS, **nothing else** | on roster **and** rank ∈ EXEC_RANKS | T: `rankCheckFromLookup` incl. the 8-former-execs regression | H: exec logs in and uses one page |
| `requireGuildSession` (12 routes) | row + roster (jsonb scan) | row + roster (`guild_roster` probe) — same answer, cheaper | T | — |
| `/api/auth/exec-session` probe | as above | as above; logs the real reason | — | — |
| Promotions page — member list | — | `HF` / `RC` badges from open ledger rows | tsc | H: eyeball |
| Promotions — stage removal | rank above target (unchanged) | + optional honorific; **Retired Chief option only rendered/accepted for Narwhal+** (checked in `POST` and `POST /bulk`, re-checked by the bot) | tsc | H: stage one with HF |
| Kick-list / Activity "pending joins" | `linked = TRUE` sticky | roster-or-stint-since-app (sticky) | T: 11 cases incl. sticky-after-leave, returning applicant, member re-applying | H: number should read ~3 |
| Activity Trends cohorts | `linked` + rank test | `rank IS NOT NULL` + rank test → **former members drop out of their stale cohort** (visible) | — | H: decide if acceptable (see rollout doc) |
| Graid / snipe / raid name lookups | `BEST_LINK_ORDER` tie-break | one row per uuid; roster-then-recency tie-break for names | T: discord-links | — |

## E. Things that are deliberately *not* changed

- `Rank | Promote/Demote`, `/manage rank`'s role edits, `promotion_queue` promote/demote paths, `determine_starting_rank`, every role-name list in `Helpers/member_roles.py`.
- Every reader of the `guildData` cache blob for stats (members page, kick-list, shells, backgrounds).
- The Chronicle's own auth (`lib/wiki-auth.ts`).
- What counts as Honored Fish / Retired Chief — still a human decision.

## F. Permission grid (the part to turn into tests)

Actor rank × action → expected outcome. ✔ allowed, ✘ refused (message), — n/a. "Target" is a Manatee unless stated.

| Action | Unlinked | Piranha | Hammerhead | Narwhal | Hydra |
| --- | --- | --- | --- | --- | --- |
| `/reset_roles` (needs `manage_roles`) | ✘ no linked account | ✔ if target below | ✔ | ✔ | ✔ |
| `/reset_roles` on a Narwhal target | ✘ | ✘ rank ≥ | ✘ | ✘ equal | ✔ |
| Leave button *Reset* | ✘ | ✔ target below | ✔ | ✔ | ✔ |
| Leave button *+Honored Fish* | ✘ | ✔ | ✔ | ✔ | ✔ |
| Leave button *+Retired Chief* | ✘ | ✘ Narwhal+ | ✘ Narwhal+ | ✔ | ✔ |
| `/honorifics grant` Honored Fish | ✘ link first | ✔ | ✔ | ✔ | ✔ |
| `/honorifics grant` Retired Chief | ✘ | ✘ | ✘ | ✔ | ✔ |
| `/honorifics revoke` (either) | ✘ | ✘ | ✘ | ✔ | ✔ |
| Website removal + Honored Fish | — | — (no exec) | ✔ | ✔ | ✔ |
| Website removal + Retired Chief | — | — | ✘ 403 | ✔ | ✔ |
| Queue processes Retired Chief from Hammerhead queuer | — | — | ✘ rejected | ✔ | ✔ |
| Website exec API, on roster | ✘ | ✘ rank | ✔ | ✔ | ✔ |
| Website exec API, **off roster** (former exec) | ✘ | ✘ | **✘ not_in_guild** | **✘** | **✘** |

Rows already asserted by tests: `/reset_roles` × {unlinked, equal, below}, all three buttons × {Narwhal, Hammerhead-on-RC}, exec API × {off roster, Piranha, Hammerhead, NULL rank}. The full grid is asserted across all ten ranks in `tests/test_permission_grid.py`.
