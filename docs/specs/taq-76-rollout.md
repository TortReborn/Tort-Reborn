# TAQ-76 linking overhaul — rollout runbook

Status: **implemented and verified on dev** 2026-09-14. Prod cutover started 2026-09-15: step 1 (dump `backups/prod-pre-taq76-20260915-1531.dump`) and step 2 (migration 1, sanity + row-by-row cross-check against the dump, all passing) done. One repair on prod: 28 ex-Barracuda stints stamped `rank_at_leave = 'Piranha'` after the migration's rename ran too late; the migration file is fixed for future runs.

What shipped (branch `feat/taq-76-linking-overhaul` in both repos) is P0–P4 of
[taq-76-linking-audit.md](taq-76-linking-audit.md) plus the ticket's own
feature ([taq-76-leave-manage-button.md](taq-76-leave-manage-button.md)),
done as one change set rather than four releases, because every phase was
exercised together on a fresh copy of prod.

## What was verified on dev

Dev (`TEST_DB_*`, local PostgreSQL 18) was rebuilt from a `pg_dump` of prod
taken 2026-09-14 12:39, then both migrations ran exactly once, in order.

| check | result |
| ----- | ------ |
| `guild_roster` seeded from the cached roster | 150 rows = roster |
| open `membership_stints` | 150 (one per roster member, none missing, none extra) |
| closed stints reconstructed from `player_activity` (14-day gap tolerance) | 345 across 457 players; 267 carry `rank_at_leave` from the stale rank |
| member-ranked `discord_links` rows not on the roster | 0 (was 385; 8 of them exec-ranked) |
| roster members linked | 148 of 150 (`Woealer`, `GordLonner` have no link) |
| roster members linked but rankless | 1 (`WeaponMerchant` — was the unknown rank `WeaponMerchant`; needs a real rank via `/manage rank`) |
| `member_honorifics` after both backfills | 183 Honored Fish + 43 Retired Chief open rows; 151 holders recorded by Discord id only (never linked) |
| website pending-joins count | 3 (prod's old query says 0 — the three are a returning applicant and two who never joined, hidden by the sticky `linked` flag) |
| bot tests | 315 passed (288 before) |
| website tests | 335 passed (327 before) |
| `tsc --noEmit` | clean |

## Prod cutover

Order matters: the old code reads the columns step 2 drops, and the new
code reads tables step 1 creates.

1. **Freeze**: no `/new_member`, `/manage link`, website removals for the
   window (a few minutes).
2. **Migration step 1** — `TAq-Website/sql/linking_overhaul_1_additive.sql`
   against prod. Additive; the old code keeps working. Run the sanity
   queries at the bottom of the file and expect the dev numbers above
   (roster size may differ by the day's churn).
3. **Deploy the bot** (Tort-Reborn branch). On start it registers
   `LeavePromptView`, and the first `update_member_data` cycle takes over
   `guild_roster` maintenance.
4. **Deploy the website** (TAq-Website branch). `requireExecSession` now
   refuses anyone not on the roster — the 8 stale exec accounts lose access
   at this moment.
4b. **Deploy the Verge raid tracker** (Kenji121Tsuki/verge-raid-tracker PR #1,
   `discord-links-refactor`). It is a third consumer of the same database:
   its current `main` reads `discord_links … linked = TRUE` (breaks at
   step 6), the PR reads `current_members` / `guild_roster` (needs step 2).
   Order among 3, 4 and 4b does not matter; all three must be live before
   step 6.
5. **Honorific role backfill** — from a machine with the bot's `.env`
   pointed at prod (`TEST_MODE=False`):
   `venv/Scripts/python scripts/backfill_honorifics.py` (dry run), then
   `--apply`. Expect ~187 holders, ~225 rows.
6. **Migration step 2** — `linking_overhaul_2_drop_columns.sql`. Only now.
7. **Follow-ups the data asked for**: give `WeaponMerchant` a rank; link
   `Woealer` and `GordLonner` if they are real members; look at the three
   pending joins on the dashboard.

Rollback before step 6 is a code redeploy; step 1 leaves the old columns
untouched. After step 6 there is no rollback without a restore — that is
why it is last.

## Behaviour changes to tell the execs

- The guild-log "member left" post is now one message per leaver with
  **Reset roles / Reset + Honored Fish / Reset + Retired Chief** buttons.
  Retired Chief needs Narwhal+. More than 10 leavers in one cycle falls
  back to the old list.
- `/honorifics lookup|grant|revoke` exists. Grants survive the person
  leaving Discord; rejoining re-applies the roles automatically.
- Website removals can attach an honorific. The Promotions page shows
  `HF` / `RC` badges.
- `/manage link` no longer sets a rank from the nickname; `/manage rank`
  does ranks. `/manage unlink` is new (deletes the identity row, audited).
- `/manage shells` on an unlinked user now requires a resolvable Minecraft
  name (it used to create a uuid-less row).
- Website login refuses accounts whose Minecraft account is not on the
  roster (`not_in_guild`), with a message saying so; the Chronicle stays
  open to them.
- `/stale-roles` is now instant (one query) and resets through the same
  routine as everything else.
