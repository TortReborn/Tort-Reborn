# Audit: what "linked" means — `discord_links` across bot and website

Status: **audit + recommendation**, 2026-09-14; P0–P4 implemented on dev the same day as one change set — see [taq-76-rollout.md](taq-76-rollout.md). Companion to [taq-76-leave-manage-button.md](taq-76-leave-manage-button.md); §8 there defers its `discord_links` decision to §7 here.

Prod numbers are from a read-only query on 2026-09-14 (`SELECT` only; script in the session scratchpad, not committed).

## 1. Verdict

`discord_links` is one table doing five jobs, and `linked` is the flag every job reads differently. The row is simultaneously the **identity link** (discord ↔ Minecraft), the **membership record**, the **Discord rank cache**, the **application-in-flight marker**, and a bucket for **per-stint facts** (wars on join, honorifics, colours). Nothing owns the lifecycle end-to-end, so:

- **533 of 537 rows are `linked = TRUE`, but the guild has 150 members.** 385 "linked" rows belong to people who are not in the guild. `linked` does not mean member, and every consumer that needs "member" has independently learned to re-derive it from the guild roster instead.
- **The website's exec API gate (`requireExecSession`, 56 routes) reads `rank` and nothing else** — not `linked`, not the roster. Eight of the 385 stale rows carry an exec rank. Those eight Discord accounts can log in today and call every exec API.
- **Four copies of "remove a member" exist and disagree about the row**: the website queue deletes it, the two commands and `/stale-roles` leave it linked with its rank intact.

The recommendation (§7) is to make `discord_links` mean exactly one thing — *this Discord account is this Minecraft account* — and move membership, rank, application state and per-stint facts to tables that own them. §8 sequences it so the security fix ships first and TAQ-76 lands on the new model rather than adding a sixth job to the old one.

## 2. The table today

| column | type | written by | meaning in practice |
| ------ | ---- | ---------- | ------------------- |
| `discord_id` | PK | all link paths | Discord account |
| `ign` | NOT NULL | link paths; rename sync (`update_member_data._sync_member_igns`) by uuid | name cache; canonical only when the rename sync has seen the player on the roster |
| `uuid` | nullable | link paths that resolve Mojang; `/manage shells` never sets it | Minecraft account. Prod: **every row has one** (537/537) |
| `linked` | NOT NULL default FALSE | see §3 | overloaded, see §4 |
| `rank` | NOT NULL, unconstrained | registration, promote/demote, `/manage link` (**nickname prefix**), `/register ally` | Discord rank role. Prod values: 10 member ranks, `''` ×38, `Barracuda` ×29 (retired), `Navigator` ×20 (ally), `WeaponMerchant` ×1 |
| `wars_on_join` | nullable | registration only | per-stint fact, overwritten on rejoin |
| `app_channel` | nullable | `_link_discord` (bot + website-decision task) | application ticket channel; pending-registration marker together with `linked = FALSE` |
| `was_honored_fish`, `was_retired_chief` | NOT NULL | registration snapshot (TAQ-51) | per-stint fact; lost when the queue deletes the row |
| `color_*`, `colors_synced_at` | nullable | `Helpers/discord_colors.py` | Discord role-colour cache for the website (199 rows synced) |

Constraints: `discord_id` PK; partial unique `(uuid) WHERE linked AND uuid IS NOT NULL`. No check on `rank`, no FK anywhere. Zero duplicate uuids in prod — the partial index and `Helpers/links.py` guards are doing their job.

## 3. Every write path

| path | file | inserts / sets | `linked` | `rank` |
| ---- | ---- | -------------- | -------- | ------ |
| Website guild application accepted | `Tasks/process_website_decisions.py` `_link_discord` | upsert ign, uuid, app_channel | **FALSE** (overwrites TRUE on conflict) | `''` |
| Legacy ticket application accepted | `Commands/app_commands.py` `_link_discord` | same | **FALSE** | `''` |
| Auto-registration on in-game join | `Tasks/update_member_data.py` `_complete_registration` | rank, ign, wars_on_join, honorific flags | TRUE | starting rank |
| `/new_member`, NewMember modal | `Commands/new_member.py`, `Helpers/classes.py` | upsert everything | TRUE | starting rank |
| `/manage rank` on an unlinked user (LinkAccount modal) | `Helpers/classes.py` `LinkAccount` | insert ign, uuid | **FALSE** | chosen rank |
| `/manage link` | `Commands/manage.py` `_link_user` | insert ign, uuid; or update ign, uuid | **FALSE** on insert; untouched on update | **`user.nick.split(' ')[0]`** on insert |
| `/manage shells` on a user with no row | `Commands/manage.py` `_build_shell_modal_card` | insert ign only, **no uuid** | FALSE | `''` |
| `/register ally` | `Commands/register.py` | upsert ign, uuid | **TRUE** | ally rank (`Navigator`) |
| Promote / demote (4 surfaces) | `manage.py`, `wave_promote.py`, `rank_promote.py`, `rank_demote.py`, queue `_update_rank_in_db` | rank | — | new rank |
| Application rescinded | `app_commands.py` `_db_rescind` | — | **flips FALSE → TRUE** | — |
| Website removal queue | `Tasks/promotion_queue_processor.py` `_remove_from_discord_links` | **DELETE** | — | — |
| `/reset_roles`, `Member \| Remove`, `/stale-roles` | `Commands/reset_roles.py`, `UserCommands/reset_roles.py`, `Commands/rankcheck.py` | **nothing** | stays TRUE | stays |
| Rename sync | `update_member_data._sync_member_igns` | ign by uuid | — | — |
| Colour sync | `Helpers/discord_colors.py` | color_* | — | — |
| Barracuda → Piranha restructure | `TAq-Website/sql/rank_restructure_barracuda_to_piranha.sql` | rank | — | — (29 `Barracuda` rows remain) |

Things to notice:

- Both application paths write `linked = FALSE` **through an upsert that overwrites `linked`**. A current member who submits a guild application (an ex-member still carrying a `linked = TRUE` row from a command-path removal, say) is silently unlinked until auto-registration flips them back.
- `_db_rescind` flipping a row to `linked = TRUE` when an *accepted* application is withdrawn has no comment explaining it. It reads like a repair for the previous point rather than a deliberate state.
- `/manage link` stores whatever the nickname starts with as the rank. `rank` accepts anything, so `Navigator`, `WeaponMerchant`, `Barracuda` and `''` all live next to the real ranks.
- `/manage shells` fabricates an identity row with no uuid so the shells card has a name to render. That row then passes `checkDiscordLink` on the website.

## 4. What `linked` means to each reader

| reading | consumers | true meaning they want |
| ------- | --------- | ---------------------- |
| "has joined the guild" — `linked = TRUE` ends an application | `Helpers/app_expiry.py`, `Helpers/guild_leave.py`, `Tasks/check_apps.py`, website `lib/pending-joins.ts` | membership started |
| "is a current member" | `Helpers/cards.py` (card eligibility), `Helpers/discord_colors.py`, `Tasks/guild_chat_bridge.py`, `Commands/rankcheck.py`, website `lib/activity-trends.ts` | membership — and **each one also intersects with the guild roster** because `linked` alone is wrong |
| "this uuid is claimed" | `Helpers/links.py` conflict guards, partial unique index | identity |
| "pending registration" — `linked = FALSE AND app_channel IS NOT NULL` | `update_member_data._fetch_unlinked_with_app`, `_check_pending_app` | application state |
| tie-breaker only — `ORDER BY linked DESC, (rank <> '') DESC, discord_id` | website `lib/discord-links.ts` and 11 copies of the same `BEST_LINK_ORDER` in `lib/graid*.ts`, `lib/snipe-stats.ts`, `api/exec/guild-raids/*`, `api/exec/promotions`, `api/exec/snipes` | identity, defensively |
| **ignored** | website `lib/exec-auth.ts` (`checkDiscordLink`, `checkDiscordLinkRank`), `api/members` (`discordLinks[row.uuid] = row`, last row wins), every `WHERE discord_id = %s` rank/uuid lookup in the bot (~40 sites) | identity or rank |

The website `BEST_LINK_ORDER` idiom exists because the schema *permits* several rows per uuid (unlinked history) even though prod currently has none. It is defensive code for a state the data model should not allow.

## 5. Prod shape (2026-09-14)

| measure | value |
| ------- | ----- |
| rows | 537 |
| `linked = TRUE` | 533 |
| `linked = FALSE` | 4 (all: uuid set, `rank = ''`, `app_channel` set — genuine pending applicants) |
| guild roster (cached `guildData`) | 150 |
| `linked = TRUE` rows **not** on the roster | **385** |
| … of which with an exec rank (Hammerhead+) | **8** |
| roster members with no linked row | 2 |
| `linked = TRUE` with `rank = ''` | 34 |
| honorific flags set | 1 (`was_honored_fish`), 0 (`was_retired_chief`) |
| duplicate uuids | 0 |

So in the data, `linked = FALSE` means "pending applicant" and nothing else; the "unlinked history" the comments describe does not exist. Removal either deletes the row or leaves it linked. `linked` is, in practice, "has ever been registered by the bot".

## 6. Findings

Ordered by consequence.

**F1 — Website exec API authorises on `rank` alone.** `requireExecSession` → `checkDiscordLinkRank` → `SELECT uuid, ign, rank FROM discord_links WHERE discord_id = $1`, then `rank ∈ EXEC_RANKS`. No `linked` filter, no roster check. The login callback issues the exec cookie to anyone with a row. Only `/api/auth/exec-session` (the page-load probe) and the 12 `requireGuildSession` routes check the roster. Eight stale exec-ranked rows exist. Fix independent of everything else (§8 P0).

**F2 — `linked` is not membership, and nothing maintains it.** 385/533 linked rows are ex-members. The guild diff in `update_member_data` step 3 knows exactly who left and when, and does nothing to the row. Every membership consumer re-derives from the roster; the ones that don't (`/api/members`, exec auth, leaderboard/top_wars/activity rank lookups) are wrong for ex-members.

**F3 — Four removal implementations, three behaviours.** Queue deletes; `/reset_roles`, `Member | Remove`, `/stale-roles` leave the row. TAQ-76 adds a fifth surface. Deleting loses honorifics and colour cache; leaving it keeps a stale exec rank (F1). Neither is right because the row is identity, and identity doesn't end when membership does.

**F4 — `rank` is unconstrained and semantically mixed.** Member ranks, an ally rank (`Navigator`), a retired rank (`Barracuda` ×29 never migrated), a one-off (`WeaponMerchant`), and `''` ×38. `/manage link` writes the nickname prefix. Website `EXEC_RANKS`/`RANK_HIERARCHY` and bot `discord_ranks` each hard-code the list; the DB enforces nothing.

**F5 — Application state on the identity row.** `app_channel` + `linked = FALSE` is the pending-registration marker, while `applications.channel_id` already holds the channel. The upsert that sets it can unlink a current member (§3).

**F6 — Per-stint facts on a per-account row.** `wars_on_join`, `was_honored_fish`, `was_retired_chief` describe one membership stint and are overwritten on the next. "Old member" detection in `Helpers/recruiting.py` therefore has to guess from prior applications and Discord roles.

**F7 — Shells fabricate identity.** `/manage shells` inserts a uuid-less row so the card renders. Identity should be a precondition of a balance, not a side effect.

**F8 — Website defends against multi-row uuids that the model should forbid.** Twelve copies of `BEST_LINK_ORDER`; `api/members` picks the last row per uuid. Collapsing to one row per uuid deletes all of it.

Positive: the uuid uniqueness guard (`Helpers/links.py` + partial index) works — zero duplicates — and the rename sync keeps `ign` current for roster members. Those two are the pieces of a clean identity table already in place.

## 7. Target model

One concept per table. Names are proposals; the point is the split.

### 7.1 `discord_links` — identity only

```sql
discord_links (
  discord_id   BIGINT PRIMARY KEY,
  uuid         UUID   NOT NULL UNIQUE,     -- one Minecraft account per Discord account and vice versa
  ign          VARCHAR(64) NOT NULL,       -- cache; rename sync keeps it current
  linked_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  linked_by    BIGINT,                     -- who ran the link; NULL for self-service
  color_primary … colors_synced_at         -- unchanged; a per-account cache belongs here
)
```

Dropped: `linked`, `rank`, `wars_on_join`, `app_channel`, `was_honored_fish`, `was_retired_chief`. A row exists iff the identity is established; there is no "unlinked row". Unlinking is a delete (with an audit row — see `audit_log`, which `/manage shells` already writes to). `uuid UNIQUE` replaces the partial index and the `LinkConflictError` guards become a plain constraint violation with the same user message.

### 7.2 `guild_roster` — who is in the in-game guild right now

```sql
guild_roster (
  uuid          UUID PRIMARY KEY,
  ign           VARCHAR(64) NOT NULL,
  in_game_rank  VARCHAR(16) NOT NULL,      -- RECRUIT … OWNER, from the API
  joined_at     TIMESTAMPTZ,               -- API 'joined' if present
  first_seen    TIMESTAMPTZ NOT NULL,
  last_seen     TIMESTAMPTZ NOT NULL
)
```

Maintained by `update_member_data` step 3 from the same diff that posts the join/leave embeds — insert on join, delete on leave. Replaces `previous_members` (`cache_entries.memberList`) as the diff baseline and the `jsonb_array_elements(data->'members')` scan in the website's `checkGuildMembership`. Indexed, joinable, one line of SQL for "is a member".

### 7.3 `membership_stints` — history

```sql
membership_stints (
  id              SERIAL PRIMARY KEY,
  uuid            UUID NOT NULL,
  discord_id      BIGINT,                  -- identity at the time, if linked
  joined_at       TIMESTAMPTZ NOT NULL,
  left_at         TIMESTAMPTZ,             -- NULL = current stint
  wars_on_join    INT,
  rank_at_leave   VARCHAR(32),
  left_via        VARCHAR(16)              -- 'api_diff' | 'website_queue' | 'command' | 'button' …
)
```

One row per stint, opened by the roster insert, closed by the roster delete (`update_member_data` again). Gives "old member?" as `EXISTS (closed stint)`, `wars_on_join` per stint, and the leave-time rank the TAQ-76 permission gate needs. Backfill: `player_activity` daily snapshots can reconstruct stints back to whenever that table starts; before that, one open stint per current roster member with `joined_at` from the API.

### 7.4 `discord_ranks` — the Discord-side standing

```sql
discord_ranks (
  discord_id   BIGINT PRIMARY KEY REFERENCES discord_links,
  rank         VARCHAR(32) NOT NULL REFERENCES rank_definitions(name),
  set_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  set_by       BIGINT
)
rank_definitions (name PK, kind CHECK (kind IN ('member','ally')), sort_order INT)
```

The rank is a fact about a Discord account (it is literally which role they hold), so it stays keyed by `discord_id`, but it moves out of the identity row: a row here means "has a standing", no row means "none" — no more `''`. `rank_definitions` is the single list both `discord_ranks` (bot) and `RANK_HIERARCHY` (website) are generated from or validated against; ally ranks are in the same list with `kind = 'ally'` so `/register ally` stops being a special case. Removal deletes the row. Promote/demote update it. A periodic reconcile (roles → row) replaces `/rankcheck`'s manual mismatch report.

*Pragmatic alternative:* keep `rank` on `discord_links` but nullable with the FK. Fewer joins, same semantics. The separate table is preferred only because it makes "removal = delete the rank row" impossible to get wrong.

### 7.5 Everything else already has a home

- **Application state** → `applications` (`channel_id`, `status`, `guild_leave_pending`). `_fetch_unlinked_with_app` becomes "accepted guild applications whose uuid is on the roster and has no open stint".
- **Honorifics** → `member_honorifics` ([TAQ-76 §4.1](taq-76-leave-manage-button.md)), unchanged by this audit; keyed by uuid, which §7.1 now guarantees is unique per account.
- **Shells** → `shells` keyed by discord_id, as now; `/manage shells` requires a `discord_links` row and refuses otherwise.

### 7.6 Views for the common questions

```sql
CREATE VIEW current_members AS
  SELECT dl.discord_id, dl.uuid, dl.ign, dr.rank, gr.in_game_rank, ms.joined_at
    FROM guild_roster gr
    JOIN discord_links dl USING (uuid)
    LEFT JOIN discord_ranks dr USING (discord_id)
    LEFT JOIN membership_stints ms ON ms.uuid = gr.uuid AND ms.left_at IS NULL;
```

Website `requireExecSession` becomes one query against this view (`rank ∈ EXEC_RANKS`), and F1 is closed structurally rather than by remembering to call a second function. `checkDiscordLink` (any member) is the same view without the rank filter; the Chronicle keeps its own, wider rule.

## 8. Migration, in shippable phases

Each phase leaves prod consistent. Nothing renames `discord_links`, so the ~80 read sites keep compiling throughout.

**P0 — close F1 (website, one PR, this week).**
`requireExecSession` awaits `checkGuildMembership(session.uuid)` alongside the rank check (60-second cache already exists). The OAuth callback refuses the exec cookie when not on the roster unless the `/chronicle` redirect applies. Run `/stale-roles` on the 8 exec-ranked stale rows. No schema change.

**P1 — roster and stints (bot + website).**
Create `guild_roster`, `membership_stints`, `current_members`. `update_member_data` step 3 writes them. Backfill stints from `player_activity`. Switch every roster-deriving reader (§4 rows 1–2, website `checkGuildMembership`, `activity-trends`) to the table/view. `linked` is still written but no longer read for membership.

**P2 — one removal routine (bot; this is TAQ-76 §4.2).**
`Helpers/member_removal.remove_member()` used by all five surfaces. It closes the stint, deletes the `discord_ranks` row (or nulls `rank`, per §7.4 choice), applies roles, writes honorifics — and **does not touch identity**. The queue's `DELETE FROM discord_links` goes away. `/manage link` stops writing the nickname as rank; `/manage shells` stops fabricating rows.

**P3 — move the per-stint and application columns.**
`wars_on_join` → stints; `app_channel` → `applications`; `was_*` → `member_honorifics` (TAQ-76 backfill). Registration flows stop writing them. `rank_definitions` created; `Barracuda` rows migrated; website `RANK_HIERARCHY` validated against it in a test.

**P4 — drop `linked`.**
By now no reader depends on it. Drop the column and the partial index, add `uuid UNIQUE`, delete the 12 `BEST_LINK_ORDER` copies and the `api/members` last-row-wins loop. `Helpers/links.py` shrinks to translating the constraint violation.

P0 is independent. P1 → P2 → P3 → P4 are ordered. TAQ-76 as specced is P2 plus its own honorific ledger; it should be built on top of P1, not before it, so the leave prompt and the rejoin lookup read `membership_stints` instead of inventing a sixth meaning for `linked`.

## 9. What changes in the TAQ-76 spec

- §4.2 "`linked = FALSE` vs delete": **neither**. Removal leaves `discord_links` alone, closes the open `membership_stints` row and removes the rank. §7 open question closed.
- `member_leave_prompts.last_rank` is still needed (the rank is gone from the row after removal); source it from `discord_ranks` at post time, or read `membership_stints.rank_at_leave`.
- `member_honorifics.discord_id` refresh-on-relink (§4.1) becomes automatic: `discord_links.uuid` is unique, so the join is always current.
- "Not linked" in the button handler means "no `discord_links` row for this uuid" — a clean question once `linked` is gone.

## 10. Open questions

- **`rank` placement** (§7.4): separate `discord_ranks` table vs nullable column with FK. The audit prefers the table; the column is acceptable if the join cost bothers the website.
- **Stint backfill depth**: how far back does `player_activity` go, and is reconstructing pre-website stints worth it, or is "one open stint per current member" enough to start?
- **Ally accounts**: with `guild_roster` as the membership source, allies are correctly not members; they keep an identity row and an ally rank. Does anything today rely on allies passing a "member" check (guild chat bridge, cards)? `_linked_member` in the bridge filters `rank in discord_ranks`, which excludes them already — confirm nothing else lets them through by accident.
- **`_db_rescind` flipping `linked = TRUE`**: intent unknown; needs the author. It disappears in P4 either way.
