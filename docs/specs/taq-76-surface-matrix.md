# TAQ-76 — surface-by-surface review matrix

The change set is large; the surfaces it touches are not. Sign off one row at a time. For each surface: who can invoke it (Discord's declared permission, then the runtime gate the code applies), what it did before, what it does now, what the tests prove, and what still needs a human on the test guild.

"Rank rule" means `check_reset_permission` / the equivalent: the actor needs a linked account with a recognised rank **at or above the floor (Hammerhead)** and may only act on members ranked strictly below them. "Narwhal+" means `discord_ranks` index ≥ Narwhal. Every such rule sits *behind* Discord's `manage_roles` check — see §G. Where a row says *unchanged*, the gate is byte-for-byte the same code path as `main`.

Legend for the last two columns — **T** = covered by an automated test on this branch; **H** = needs a human pass on the test guild (`docs/specs/taq-76-rollout.md` step 5 checklist).

## A. Bot — slash commands

| Surface | Invoke: Discord → runtime | Before | After | Proof | Human |
| --- | --- | --- | --- | --- | --- |
| `/reset_roles user` | `manage_roles` → `manage_roles` + rank rule (**Hammerhead floor is new**; target-below unchanged) | Own copy of removal; read `was_*` flags; left `discord_links` untouched (rank stayed) | `remove_member()`: honorifics from ledger, roles stripped, nick cleared, **rank → NULL**, `rank_at_leave` stamped. Unlinked target still allowed (plain Ex-Member). | T: round-trips, equal-rank refusal, unlinked target | H: real role edit on test guild |
| `/new_member user ign` | `manage_roles` → `manage_roles` (unchanged) | Wrote `linked=TRUE`, rank, `wars_on_join`, honorific snapshot | `record_registration()`: identity upsert (refuses a uuid another account holds), rank, stint attach, honorific roles held → ledger rows, clears `guild_leave_pending`. Same roles added/removed. | T: registration path in removal round-trips | H: one real registration |
| `/manage rank user rank` | `manage_roles` (group) → linked initiator w/ recognised rank, target below, chosen rank below (unchanged) | Unlinked target → `LinkAccount` modal wrote `linked=FALSE` + rank; uuid optional | Same gates; NULL-rank target now allowed (treated as unranked). Modal writes identity + rank; **requires a resolvable Minecraft name**. | T: `_rank_lookup` None-safety via removal tests | H: modal path once |
| `/manage link user ign` | `manage_roles` (group) → none beyond Discord (unchanged) | Insert with `rank = nickname prefix`, `linked=FALSE` | Identity only (`upsert_identity`); **rank untouched**. Conflict → same `LinkConflictError` message. | T: `upsert_identity` conflict | H: link an unlinked user; confirm no rank |
| `/manage unlink user` **(new)** | `manage_roles` (group) → linked initiator, target below | — | Deletes the identity row, writes `audit_log`. Roles not touched (message says use `/reset_roles`). | — | H: unlink + relink |
| `/manage shells` | `manage_roles` (group) → none beyond Discord (unchanged) | Modal path for unlinked user inserted a uuid-less row | Modal path establishes a real identity (uuid required); conflict reported | — | H: shells on an unlinked user |
| `/register ally user ign guild` | `manage_roles` (group) → Moderator role+ (unchanged) | Upsert with `linked=TRUE`, ally rank | Identity + ally rank (`Navigator`, FK-valid). Allies are not on the roster → never "members". | — | H: one ally registration |
| `/stale-roles` | `manage_roles` → `manage_roles` + rank rule (**Hammerhead floor is new**) | Guild API + Discord member fetch, per-row removal copy | One query (ranked link not on roster) + `remove_member()` per row. Rank gate per row unchanged. | T: `fetch_stale_taq_links`, shaping | H: run once, expect 0 |
| `/rankcheck` | `administrator` → `administrator` (unchanged) | Compared in-game vs `discord_links.rank` | Same; skips NULL-rank links instead of erroring | — | H: run once, compare counts to `main` |
| `/promo_wave` | `manage_roles` (unchanged) | `.index(rank)` would raise on `''`/unknown | Refuses cleanly ("no rank on record"); otherwise unchanged | — | — |
| `/honorifics lookup` **(new)** | `manage_roles` → `manage_roles` + **Hammerhead+** | — | Read-only; resolves user → link → Mojang; shows open + revoked grants | T: ledger history | H: lookup a known Honored Fish |
| `/honorifics grant` **(new)** | `manage_roles` → `manage_roles` + **Hammerhead+**; **Retired Chief: Narwhal+** | — | Ledger row (idempotent) + role if present | T: `can_manage`, grant idempotency | H: grant + see role |
| `/honorifics revoke` **(new)** | `manage_roles` → `manage_roles` + **Narwhal+** | — | Closes row + removes role if present | T: revoke closes, never deletes | H: revoke + see role gone |

## B. Bot — user (right-click) commands

| Surface | Invoke | Before | After | Proof | Human |
| --- | --- | --- | --- | --- | --- |
| `Member \| Remove` | `manage_roles` → `manage_roles` + rank rule (**Hammerhead floor is new**); **unlinked target now allowed** (was refused) | Own removal copy | `remove_member()` | T: Retired Chief round-trip, initiator-unlinked refusal | H: one real removal |
| `Member \| Register` | unchanged | unchanged (`NewMember` modal) | Modal writes via `record_registration()` | T: shared path | H: once |
| `Rank \| Promote` / `Demote` | unchanged | unchanged | Unchanged code; NULL rank reads as "not recognised" (same message shape as before) | — | — |

## C. Bot — buttons, events, tasks

| Surface | Invoke | Before | After | Proof | Human |
| --- | --- | --- | --- | --- | --- |
| Guild-log **Reset roles** button | anyone who sees the channel → rank rule (**Hammerhead+**, target's `last_rank` strictly below) | — | Member present → `remove_member()`; already Ex-Member → noop; gone from Discord → noop. Second press reports the first resolver. | T: all four branches, race | H: press on a real leave |
| **Reset + Honored Fish** button | rank rule, Hammerhead+ | — | As above with grant; gone from Discord → **ledger row only** (the ticket's case) | T | H |
| **Reset + Retired Chief** button | rank rule + **Narwhal+** | — | As above | T: Hammerhead refused | H |
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

## E. Second-pass review findings (2026-09-15)

Each row above was re-read against the code, not the diff. Three defects found and fixed on the branch, one pre-existing quirk noted:

| # | Surface | Finding | Fix |
| --- | --- | --- | --- |
| 1 | Auto-registration sweep | `/reset_roles` on a **current** member cleared their rank, and 3 minutes later the sweep saw "accepted app + identity + rank NULL + open stint" and would have **re-registered them at Starfish** with a welcome post. Impossible on `main` (`linked` stayed TRUE). | Pending registration now also excludes any stint since the application that has `rank_at_leave` stamped (which `remove_member` sets on the open stint). `tests/test_roster.py::test_pending_registration_predicate` walks the whole lifecycle. |
| 2 | `on_member_join` | A current in-game member who left and rejoined Discord would have been handed `Ex-Member` + their honorific roles. | Restore is skipped when the account is on the roster; registration handles them. Tested. |
| 3 | `/api/members` | `discordRank` became `null` for rankless links where it used to be `''`. | Coerced to `''`; every other client-side `.rank` consumer checked and already null-safe. |
| — | `/manage rank` on an unlinked user | The `LinkAccount` modal recorded identity + rank but never applied the rank roles — the "Added Roles:" text it returned was only a header. **Pre-existing on `main`**; fixed as TAQ-86: both branches now plan and apply roles through `Helpers.member_roles.apply_rank_roles`. | T: `test_member_roles.py` rank-change block |
| 4 | Removal / honorific gates | The grid presented the rank rule alone, which read as "a Piranha can `/reset_roles`". The code relied on Discord's `manage_roles` for *who* and only checked *whom*; the website queue had an explicit Hammerhead floor, the bot did not. | **Policy decision 2026-09-15:** Hammerhead floor for removals (all four surfaces + the buttons), Honored Fish grants and honorific lookups; Narwhal for Retired Chief and revokes. `REMOVAL_FLOOR_RANK` in `member_removal.py`; grid tests updated; §G rewritten to show both gates. |
| — | `/new_member` before the in-game join | `wars_on_join` has nowhere to live until the stint opens, so it is NULL for members registered before they appear on the roster (the website flow registers after the join, so this is rare). | Accepted; noted. |

## F. Things that are deliberately *not* changed

- `Rank | Promote/Demote`, `/manage rank`'s role edits, `promotion_queue` promote/demote paths, `determine_starting_rank`, every role-name list in `Helpers/member_roles.py`.
- Every reader of the `guildData` cache blob for stats (members page, kick-list, shells, backgrounds).
- The Chronicle's own auth (`lib/wiki-auth.ts`).
- What counts as Honored Fish / Retired Chief — still a human decision.

## G. Permission grid

Every destructive or honorific action has **two gates, both required**:

1. **Discord permission** — `manage_roles` on the actor's Discord roles. Declared on the command (so people without it don't see it) and re-checked in code at runtime. Whether a given rank's role carries `manage_roles` is guild configuration, not code; in practice it is the exec roles.
2. **Rank rule in code** — the actor's `discord_links.rank` must be a recognised member rank **at or above the floor**, and for removals the target must be strictly below the actor.

Floors (`Helpers/member_removal.REMOVAL_FLOOR_RANK`, decided 2026-09-15): **Hammerhead** for removals, Honored Fish grants and honorific lookups; **Narwhal** for Retired Chief grants and any revoke. The website queue already had the Hammerhead floor (`MIN_QUEUER_RANK_INDEX`); the bot relied on `manage_roles` alone before this branch.

The table below assumes gate 1 passed — it shows only what the code's rank rule then decides. An actor without `manage_roles` never reaches it. ✔ allowed · ✘ refused · — not applicable. "Target" is a Manatee unless stated.

| Action | Starfish–Swordfish | Hammerhead | Sailfish / Dolphin | Narwhal | Hydra |
| --- | --- | --- | --- | --- | --- |
| `/reset_roles`, `Member \| Remove`, `/stale-roles`, leave button *Reset* | ✘ below floor | ✔ | ✔ | ✔ | ✔ |
| …on a Hammerhead target | ✘ | ✘ not below | ✔ | ✔ | ✔ |
| …on a Narwhal target | ✘ | ✘ | ✘ | ✘ | ✔ |
| Leave button *+Honored Fish* | ✘ | ✔ | ✔ | ✔ | ✔ |
| Leave button *+Retired Chief* | ✘ | ✘ Narwhal+ | ✘ Narwhal+ | ✔ | ✔ |
| `/honorifics lookup` | ✘ | ✔ | ✔ | ✔ | ✔ |
| `/honorifics grant` Honored Fish | ✘ | ✔ | ✔ | ✔ | ✔ |
| `/honorifics grant` Retired Chief | ✘ | ✘ | ✘ | ✔ | ✔ |
| `/honorifics revoke` (either) | ✘ | ✘ | ✘ | ✔ | ✔ |
| Website removal + Honored Fish | — no exec session | ✔ | ✔ | ✔ | ✔ |
| Website removal + Retired Chief | — | ✘ 403 | ✘ 403 | ✔ | ✔ |
| Bot processes a queued Retired Chief | — | ✘ rejected | ✘ rejected | ✔ | ✔ |
| Website exec API, on roster | ✘ | ✔ | ✔ | ✔ | ✔ |
| Website exec API, **off roster** (former exec) | ✘ | **✘ not_in_guild** | **✘** | **✘** | **✘** |

Unlinked actors, actors with an unrecognised rank (`Navigator`, legacy values) and actors with `rank IS NULL` are refused everywhere with a "link your account" / "not recognized" message.

The full grid is asserted across all ten ranks in `tests/test_permission_grid.py`; if the floors change, that file and this section change together.
