# TAQ-76: Leave-message manage buttons & DB-side honorific tracking

Status: **implemented on dev** 2026-09-14, on top of the linking overhaul — see [taq-76-rollout.md](taq-76-rollout.md) for what was verified and the prod cutover order.

Deviations from the spec below, decided during implementation:
- `member_honorifics.uuid` is nullable: 151 of the 187 current role holders have never linked a Minecraft account, so they are recorded by Discord id and get the uuid attached when they link (§4.1).
- `rank` stays on `discord_links` (nullable, FK to `rank_definitions`) rather than a separate table (audit §7.4 alternative) — ~60 read sites kept as-is.
- Removal never closes a membership stint; the in-game roster diff does, since a website removal usually precedes the kick.

Ticket: TAQ-76 (`tracker_tickets.id = 76`), type `feature`, system `discord_bot`, priority `medium`, status `in_progress`, due 2026-09-05.
Submitted by and assigned to Discord id `500332699928494100`. No comments, no attachments.

The ticket is filed against the bot, but the second paragraph (DB-side tracking) reaches into the website: the Promotions page queues removals and is the natural place to see and set honorifics. Both sides are specified here; the website work is the smaller half.

## 1. Ticket, verbatim

Title: **Reset Roles / Manage Button on the on member leave message**

> (INCLUDES HONORED FISH TRACKING DB SIDED)
>
> Currently, when someone leaves / gets kicked, we have to use /reset roles on them (worst case this requires us to get their UUID first too when discord is being a bitch), instead, we should make it so that the on leave message that tort sends into guild log when someone leaves spawns a button that we can press to reset their role and optionally assign honored fish (and retired chief)
> With this we should transition to tracking our honored fishes and retired chiefs DB sided, so that in case someone leaves the discord and loses the role, and they rejoin without any of us knowing what they were before, we can easily check up on that data

Two asks:

1. **Buttons on the leave message** — reset roles from the guild-log embed, with an optional honorific grant in the same click.
2. **Honorifics as data** — a record that survives the person leaving Discord, so a rejoin can be checked (and, ideally, handled) without anyone remembering.

## 2. What already exists

TAQ-51 / TAQ-67 built the *restore* half of (2):

| Piece | Where |
| ----- | ----- |
| `discord_links.was_honored_fish`, `was_retired_chief` | [schema.sql](../../schema.sql) lines 15–16 |
| Written at (re)registration by `honorific_flags()` — a snapshot of the roles held *before* the bot strips them | [Helpers/member_roles.py](../../Helpers/member_roles.py), [Tasks/update_member_data.py](../../Tasks/update_member_data.py) `_auto_register_joined_member`, [Helpers/classes.py](../../Helpers/classes.py) `PlayerLink` |
| Read at removal by `removal_role_names()` in all three removal flows | [Commands/reset_roles.py](../../Commands/reset_roles.py), [UserCommands/reset_roles.py](../../UserCommands/reset_roles.py), [Tasks/promotion_queue_processor.py](../../Tasks/promotion_queue_processor.py) `_do_remove` |
| Retired Chief implies Honored Fish on restore | `removal_role_names()` |
| End-to-end tests driving all three flows | [tests/test_member_removal_flows.py](../../tests/test_member_removal_flows.py) |

What is missing, against the ticket:

| Gap | Detail |
| --- | ------ |
| The record is a **pre-join snapshot, not a decision**. | Granting Honored Fish at removal (the new flow) would exist only as a Discord role. Leave Discord → role gone → nothing anywhere. |
| The website removal **deletes the `discord_links` row**. | `_remove_from_discord_links` in the queue processor. Flags go with it. |
| The two commands **leave the row untouched**. | `linked = TRUE`, rank intact — the stale-links problem `/rankcheck` reports on. Flags are never updated. |
| Nothing runs on **Discord rejoin**. | [Events/on_member_join.py](../../Events/on_member_join.py) only posts the welcome line. |
| No **lookup surface**. | The ticket's "easily check up on that data" has nowhere to point. |
| Website has **no notion** of honorifics. | Zero references in TAq-Website. |

### 2.1 Where the leave message comes from

Not a Discord `on_member_remove` — the bot has no such listener. The "Guild Members Left" embed is the **in-game guild diff** in `update_member_data` step 3 ([Tasks/update_member_data.py](../../Tasks/update_member_data.py) ~L772–817): `previous_members` (cached `memberList`) vs the current API member list, keyed by **Minecraft uuid**. One batched embed listing every leaver goes to both `BOT_LOG_CHANNEL_ID` and `GUILD_LOG_CHANNEL_ID`.

Consequences:

- **Kicks and voluntary leaves are the same event.** The ticket's "leaves / gets kicked" is covered by one hook.
- The trigger carries a uuid, not a Discord id. The button must resolve `uuid → discord_links (linked) → discord_id → guild.get_member`. That is exactly the "get their UUID first" step the ticket complains about, automated.
- Leave-and-rejoin inside one poll window produces no event. Acceptable; nothing to reset in that case.
- A website-queued removal does **not** kick in-game. The order is usually: exec queues removal on the website → bot strips Discord roles → someone kicks in-game → leave embed fires. The button must therefore be idempotent against an already-processed member (§4.3 step 5).

## 3. Scope

| # | Change | System |
| - | ------ | ------ |
| A | `member_honorifics` ledger + backfill | bot (schema shared) |
| B | One shared removal routine used by every flow | bot |
| C | Per-leaver guild-log message with buttons | bot |
| D | Rejoin re-apply + `/honorifics` command | bot |
| E | Promotions page: honorific badge, optional grant on removal | website |

Out of scope: changing what counts as Honored Fish / Retired Chief (a human decision, stays one); the kick-list feature (it feeds removals but is upstream of this); Discord-side `on_member_remove` handling (nothing to do — the roles leave with the account, and the ledger is what survives).

## 4. Design

### 4.1 A — `member_honorifics`

A ledger keyed by the **player**, not by the `discord_links` row that removal deletes. One row per grant; revocation closes the row rather than deleting it, so history stays readable.

```sql
CREATE TABLE IF NOT EXISTS member_honorifics (
  id          SERIAL       PRIMARY KEY,
  uuid        UUID         NOT NULL,           -- stable identity across Discord accounts
  discord_id  BIGINT,                          -- account holding the role at grant time; may be NULL
  ign         VARCHAR(64)  NOT NULL,           -- name at grant time, display only
  honorific   VARCHAR(16)  NOT NULL CHECK (honorific IN ('honored_fish', 'retired_chief')),
  granted_by  BIGINT       NOT NULL,           -- Discord id; 0 for the backfill
  granted_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  revoked_by  BIGINT,
  revoked_at  TIMESTAMPTZ,
  note        TEXT
);
CREATE INDEX IF NOT EXISTS member_honorifics_active_uuid_idx
  ON member_honorifics (uuid) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS member_honorifics_active_discord_idx
  ON member_honorifics (discord_id) WHERE revoked_at IS NULL;
-- One open grant per (player, honorific).
CREATE UNIQUE INDEX IF NOT EXISTS member_honorifics_active_uq
  ON member_honorifics (uuid, honorific) WHERE revoked_at IS NULL;
```

Rules:

- **uuid is the key.** A Discord account can be replaced; the Minecraft account is who the guild honored. `discord_id` is recorded for the rejoin lookup (§4.4) and refreshed whenever the player links a new account (the registration flows already touch `discord_links`; they also `UPDATE member_honorifics SET discord_id = %s WHERE uuid = %s AND revoked_at IS NULL`).
- **Retired Chief implies Honored Fish stays a derivation**, in `removal_role_names()`. Granting Retired Chief writes one row, not two.
- **Rows are never deleted.** Revoke = set `revoked_by` / `revoked_at`. The `/honorifics` history view (§4.4) shows both.
- The existing `was_honored_fish` / `was_retired_chief` columns stay for one release as a fallback read (§4.1.2) and are dropped afterwards.

#### 4.1.1 Helper

New module `Helpers/honorifics.py`, blocking DB functions in the style of `Helpers/recruiting.py`:

```python
def active_honorifics(*, uuid=None, discord_id=None) -> tuple[bool, bool]
    # (honored_fish, retired_chief). Unions the ledger with the legacy
    # discord_links.was_* flags so nothing regresses before the backfill.
def grant(uuid, ign, honorific, granted_by, discord_id=None, note=None) -> int
    # idempotent: an open row for (uuid, honorific) is returned, not duplicated
def revoke(uuid, honorific, revoked_by, note=None) -> bool
def history(*, uuid=None, discord_id=None) -> list[dict]
```

`removal_role_names()` keeps its `(was_honored_fish, was_retired_chief)` signature; the callers get those booleans from `active_honorifics()` instead of a raw `SELECT` on `discord_links`.

#### 4.1.2 Backfill

One-shot `scripts/backfill_honorifics.py`, same shape as [scripts/cleanup_ex_member_roles.py](../../scripts/cleanup_ex_member_roles.py) (dry-run by default, `--apply` to write):

1. Every current holder of the `Honored Fish` / `Retired Chief` role in the prod guild → one row per role held, `granted_by = 0`, `note = 'backfill: role held on <date>'`. uuid comes from `discord_links` (any row, linked or not, by `discord_id`); holders with no link at all are printed for manual follow-up rather than skipped silently.
2. Every `discord_links` row with `was_honored_fish` or `was_retired_chief` set and no matching open ledger row → one row, `note = 'backfill: discord_links flag'`.
3. Report: rows written, holders without a uuid, flag rows already covered by step 1.

After the backfill and one release of `active_honorifics()` reading both sources without disagreement, the legacy columns and the `honorific_flags()` write path in the registration flows are removed. Until then registration keeps writing the flags **and** does nothing to the ledger — the ledger is only ever changed by an explicit grant/revoke or the backfill.

### 4.2 B — one removal routine

Today `/reset_roles`, `Member | Remove`, and `_do_remove` are three near-copies of the same twenty lines, and they disagree about `discord_links` (one deletes, two do nothing). Extract:

```python
# Helpers/member_removal.py
async def remove_member(member, guild, *, actor_id, reason,
                        grant=None):          # None | 'honored_fish' | 'retired_chief'
    """Turn a member into an ex-member.

    1. grant -> Helpers.honorifics.grant(...) (before roles, so a Discord
       failure after this point still leaves the record)
    2. hf, rc = active_honorifics(uuid=..., discord_id=member.id)
    3. to_add, to_remove = removal_role_names(hf, rc)
    4. remove_roles / add_roles via resolve_roles(...)
    5. nick = ''
    6. discord_links: linked = FALSE (see below)
    Returns RemovalResult(restored=[...], granted=..., already_ex_member=bool).
    """
```

All four callers (the two commands, the queue, the new buttons) go through it. The permission checks stay in the callers — they differ per surface.

**`discord_links` on removal — decision: `UPDATE ... SET linked = FALSE`, not `DELETE`.** The queue's delete was there because nothing else needed the row; now the row is the uuid↔discord_id bridge that the rejoin lookup (§4.4) and the website badge (§4.5) both use. The unique index `discord_links_linked_uuid_uq` is partial on `linked`, so an unlinked historical row does not block a future relink. The two commands gain the same update, which also closes their stale-link gap. Existing behaviour that depends on the row being *absent* after queue removal — none found; `_fetch_unlinked_with_app` and `Helpers/app_expiry.py` already treat `linked = FALSE` rows as historical.

Tests: [tests/test_member_removal_flows.py](../../tests/test_member_removal_flows.py) already drives all three flows against a prod role inventory; cases 1–5 keep passing unchanged (the routine is behaviour-preserving) and gain assertions on the `linked` flag. New cases: grant on removal writes a ledger row *and* applies the role; grant with the member already gone from Discord writes the ledger row only.

### 4.3 C — buttons on the leave message

**Message shape.** `GUILD_LOG_CHANNEL_ID` gets **one message per leaver** instead of the batched embed (the batched one stays as-is for `BOT_LOG_CHANNEL_ID`, which is a log, not a work queue). Each:

```
┌ Guild Member Left ─────────────────────────┐
│ **Kenji121**                                │
│ Rank: Hammerhead · Discord: @kenji (linked) │
│ Honorifics on record: none                  │
└─────────────────────────────────────────────┘
[ Reset roles ]  [ Reset + Honored Fish ]  [ Reset + Retired Chief ]
```

- The "Discord" line shows one of: `@mention (linked)`, `@mention — no longer in this server`, `not linked`. Determined at post time from `discord_links` + `guild.get_member`; the buttons re-check on press.
- "Honorifics on record" comes from `active_honorifics()`, so a returning Honored Fish leaving again shows what they will get back.
- When a leave has more than ~10 members (mass kick, API hiccup), fall back to the batched embed with no buttons and a line "use `/reset_roles`" — the cold-start guard already suppresses the first-run flood; this is for the rest.

**View.** Persistent, stateless, resolved from the message — the `RecruitPaidView` pattern in [Helpers/views.py](../../Helpers/views.py): three `discord.ui.Button`s with fixed `custom_id`s `leave_reset`, `leave_reset_hf`, `leave_reset_rc`, `timeout=None`, registered in `main.py` next to the others. The target comes from a small table written when the message is posted:

```sql
CREATE TABLE IF NOT EXISTS member_leave_prompts (
  message_id   BIGINT       PRIMARY KEY,
  uuid         UUID         NOT NULL,
  ign          VARCHAR(64)  NOT NULL,
  discord_id   BIGINT,                     -- resolved at post time, may be NULL
  last_rank    VARCHAR(32),                -- discord_links.rank at post time, for the permission gate
  posted_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  resolved_by  BIGINT,
  resolved_at  TIMESTAMPTZ,
  resolution   VARCHAR(16)                 -- 'reset' | 'honored_fish' | 'retired_chief' | 'noop'
);
```

This doubles as the audit trail of who pressed what, and `last_rank` is what the permission check compares against once the `discord_links` row has been unlinked.

**Handler** — all three buttons share it, with `grant` differing:

1. `defer(ephemeral=True)`.
2. Load the prompt row; if `resolved_at` is set, say who resolved it and return (race between two execs).
3. **Permission gate** — the `/reset_roles` rules verbatim: `manage_roles`, a linked `discord_links` row for the presser, presser's rank index strictly above `last_rank`'s. Additionally `leave_reset_rc` requires the presser to be **Narwhal or above** (`discord_ranks` index ≥ Narwhal): Retired Chief is the higher honor and a misclick should not be able to hand it out. Honored Fish keeps the base rule (Hammerhead+ in practice, since that is who has `manage_roles`).
4. Resolve the member: `discord_id` from the prompt row, or re-look-up by uuid if NULL; `guild.get_member`, falling back to `fetch_member` (cache misses on large guilds are the "discord being a bitch" case).
5. Branches:
   - **Member present** → `remove_member(..., grant=...)`. Edit the embed: colour grey, add field `Resolved — Reset by @x (+ Honored Fish)`, disable the row.
   - **Member not in Discord** and a grant was requested → `Helpers.honorifics.grant(...)` only; embed field `Recorded Honored Fish — not in Discord, roles apply on rejoin`. This is the ticket's central case.
   - **Member not in Discord**, plain reset → nothing to do; field `Nothing to reset — not in Discord`, `resolution = 'noop'`.
   - **Member present but already Ex-Member** (website removal ran first) → skip the role work; if a grant was requested, write it and add the honorific role(s); field says `Already removed via website; Honored Fish granted`.
   - **Not linked at all** → the buttons cannot know who to act on; field `No linked Discord account — use /reset_roles`, buttons disabled.
6. Write `resolved_*` on the prompt row inside the same transaction as any ledger write.

Errors (`Forbidden`, API failure) go to `ERROR_CHANNEL_ID` in the existing `_post_error` style and leave the buttons enabled so the press can be retried.

**Removed from the existing flows:** nothing. `/reset_roles` and `Member | Remove` stay for members who leave Discord first or were never in the log.

### 4.4 D — rejoin and lookup

**`on_member_join`** ([Events/on_member_join.py](../../Events/on_member_join.py)): after the welcome line, `active_honorifics(discord_id=member.id)`; if either is set, add `Ex-Member` plus the honorific role(s) via `removal_role_names()` / `resolve_roles()` (reuse, do not re-list the names), and post one line to `GUILD_LOG_CHANNEL_ID`: `@x rejoined — restored Honored Fish (granted 2025-03-02 by @y)`. Failures log to `ERROR_CHANNEL_ID` and do not block the welcome.

If the person's Discord account is new (no ledger row by `discord_id`), nothing happens automatically — the link is re-established when they register or an exec runs `/honorifics`, at which point `discord_id` on the open rows is refreshed (§4.1) and the roles are applied.

**`/honorifics`** — new cog `Commands/honorifics.py`, `guild_ids=HOME_GUILD_IDS`, `default_permissions(manage_roles=True)`:

| Subcommand | Args | Does |
| ---------- | ---- | ---- |
| `lookup` | `user: Member` **or** `ign: str` | Ephemeral embed: active honorifics with grant date/by, then revoked history. Resolves ign → uuid via `discord_links` first, Mojang second. |
| `grant` | `user`/`ign`, `honorific: Honored Fish \| Retired Chief`, `note?` | Ledger row + role if the member is present. Retired Chief needs Narwhal+. Covers people honored outside a leave event. |
| `revoke` | same | Closes the row, removes the role(s) if present. Narwhal+ for both. |

`/rankcheck`'s stale-link report ([Commands/rankcheck.py](../../Commands/rankcheck.py)) already surfaces `was_*`; switch it to `active_honorifics()` so it keeps showing the same column after the legacy flags go.

### 4.5 E — website

The Promotions page ([app/exec/promotions/page.tsx](../../../TAq-Website/app/exec/promotions/page.tsx)) is where removals are queued, so it is where honorifics should be visible and settable.

1. **Badge.** `GET /api/exec/promotions` already batch-loads `discord_links` by uuid; extend the same query with a `LEFT JOIN` on open `member_honorifics` rows and return `honorifics: ('honored_fish' | 'retired_chief')[]` per member. Render as a small tag after the rank (`HF` / `RC`, in the role colour) in the member list and the history table.
2. **Grant on removal.** The stage-removal action gets an optional select: *No honorific* (default) / *Honored Fish* / *Retired Chief* (the last only rendered for Narwhal+, `RANK_HIERARCHY` index check next to the existing "cannot manage at or above your rank" guard). Stored as a new nullable column:

   ```sql
   ALTER TABLE promotion_queue ADD COLUMN IF NOT EXISTS
     grant_honorific VARCHAR(16) CHECK (grant_honorific IN ('honored_fish', 'retired_chief'));
   ```

   The bot's `_do_remove` passes it through as `remove_member(..., grant=entry['grant_honorific'])`. The processor's existing queuer re-verification is extended: `retired_chief` with a queuer below Narwhal is rejected with the usual `_post_error`.
3. **History**, optional: a read-only `/exec/honorifics` list (active + revoked, sortable by date) — only if the badge turns out not to be enough. Not in the first PR.

Rank threshold constants come from `lib/rank-constants.ts`; do not restate the rank list.

## 5. Permission summary

| Action | Who |
| ------ | --- |
| Reset roles (any surface) | `manage_roles` + linked + rank strictly above target — unchanged |
| Grant / revoke Honored Fish | same as reset |
| Grant / revoke Retired Chief | Narwhal+ |
| Website: queue removal with honorific | same thresholds, checked in the API route and re-checked by the bot |

## 6. Rollout

1. Schema (`member_honorifics`, `member_leave_prompts`, `promotion_queue.grant_honorific`) — additive, safe to apply first. Add to [schema.sql](../../schema.sql) with the usual `DO $$ … END $$` migration block for the column; mirror the table DDL under `TAq-Website/sql/` as `create_member_honorifics.sql` since the website reads it.
2. Bot: helper + `remove_member()` + callers switched (behaviour-preserving; existing tests must pass). Deploy.
3. Backfill script, dry-run reviewed, then `--apply`.
4. Bot: leave-message buttons, `on_member_join`, `/honorifics`. Deploy.
5. Website: badge + grant-on-removal.
6. One release later: drop `was_honored_fish` / `was_retired_chief`, the `honorific_flags()` write path, and the legacy union in `active_honorifics()`.

Steps 2–4 are one bot PR (`feat/taq-76-leave-manage-button`); step 5 is one website PR referencing the same ticket.

## 7. Open questions

- **Batched-message threshold** (§4.3): 10 is a guess. The largest real leave batch in the guild log history would set it properly.
- **Retired Chief threshold**: Narwhal+ is proposed. If Hydra-only is the intent, it is a one-constant change; the spec assumes Narwhal.
- ~~**`linked = FALSE` vs delete** (§4.2)~~ — superseded by [taq-76-linking-audit.md](taq-76-linking-audit.md) §9: removal touches neither; it closes the membership stint and removes the rank, and `discord_links` becomes identity-only. Build this ticket on top of that audit's P1 (roster + stints) so the leave prompt and rejoin lookup read `membership_stints` rather than adding another meaning to `linked`.
