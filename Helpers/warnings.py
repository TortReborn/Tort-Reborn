"""Warning tracker (TAQ-79): the bot's side.

The website (TAq-Website /exec/warnings) is the only place warnings are
written. This module reads those rows and renders them for Discord:

- One forum post per player in the TE warning forum. The starter message is
  a summary embed the bot keeps current; each warning is its own message in
  the thread, and exec discuss underneath.
- The Dishonored Fish role in the main Discord follows the tracker: a player
  with an active dishonored_fish entry holds it, revoking removes it.

Bookkeeping lives on the rows themselves (sql/create_player_warnings.sql in
TAq-Website):

  player_warnings.discord_message_id   NULL = not posted yet, -1 = claimed by
                                        the bot mid-post, 0 = backfilled and
                                        never mirrored, >0 = the posted message
  player_warnings.updated_at vs         the website bumps updated_at on every
    discord_synced_at                   edit; the bot re-renders when it is newer
  warning_profiles.needs_sync           the summary embed / tags need a re-render

All DB functions are blocking and take a cursor; callers own the checkout
and the commit.
"""

import datetime

import discord

KIND_LABELS = {
    'note': 'Note',
    'soft': 'Soft warning',
    'written': 'Written warning',
    'formal': 'Formal warning',
    'dishonored_fish': 'Dishonored Fish',
    'timeout': 'Timeout',
    'mute': 'Mute',
    'demotion': 'Demotion',
    'kick': 'Kick',
    'ban': 'Ban',
    'blacklist': 'Blacklist',
}

KIND_COLORS = {
    'note': 0x94A3B8,
    'soft': 0xEAB308,
    'written': 0xF97316,
    'formal': 0xEF4444,
    'dishonored_fish': 0xA78BFA,
    'timeout': 0x64748B,
    'mute': 0x64748B,
    'demotion': 0x64748B,
    'kick': 0x475569,
    'ban': 0x475569,
    'blacklist': 0x475569,
}

# Only these count toward the badge, same as the website (lib/warnings.ts).
COUNTED_KINDS = ('soft', 'written', 'formal')

# Forum tags the bot applies when the forum has a tag of that exact name.
# Missing tags are skipped, so admins can choose which ones exist.
TAG_DISHONORED = 'Dishonored Fish'
LEVEL_TAGS = {'formal': 'Formal', 'written': 'Written', 'soft': 'Soft'}

DISCORD_SOURCE_RULE_ADDED = 'Dishonored Fish role given in Discord'
REVOKE_REASON_ROLE_REMOVED = 'Dishonored Fish role removed in Discord'


# --- Pure helpers -------------------------------------------------------------

def count_active(entries):
    """Active warning counts for a profile's entries (dicts with kind, revoked_at)."""
    counts = {'total': 0, 'soft': 0, 'written': 0, 'formal': 0, 'dishonored': False}
    for e in entries:
        if e['revoked_at'] is not None:
            continue
        if e['kind'] in COUNTED_KINDS:
            counts[e['kind']] += 1
            counts['total'] += 1
        elif e['kind'] == 'dishonored_fish':
            counts['dishonored'] = True
    return counts


def tag_names(counts):
    """Forum tag names for a profile: its highest active warning level, plus Dishonored Fish."""
    names = []
    for kind in ('formal', 'written', 'soft'):
        if counts[kind]:
            names.append(LEVEL_TAGS[kind])
            break
    if counts['dishonored']:
        names.append(TAG_DISHONORED)
    return names


def _date(ts):
    return f"<t:{int(ts.timestamp())}:d>" if ts else 'unknown date'


def _clip(text, limit):
    text = text or ''
    return text if len(text) <= limit else text[: limit - 1] + '…'


def summary_embed(profile, entries, website_url):
    """The forum post's starter message: counts, then one line per entry, newest first."""
    counts = count_active(entries)
    parts = []
    if counts['total']:
        levels = ', '.join(f"{counts[k]} {k}" for k in ('formal', 'written', 'soft') if counts[k])
        parts.append(f"**{counts['total']} active warning{'s' if counts['total'] != 1 else ''}** ({levels})")
    else:
        parts.append('**No active warnings**')
    if counts['dishonored']:
        parts.append('🐟 **Dishonored Fish**')

    lines = []
    revoked = 0
    for e in entries:
        if e['revoked_at'] is not None:
            revoked += 1
            continue
        line = f"{_date(e['issued_at'])} · **{KIND_LABELS[e['kind']]}** · {_clip(e['rule'], 120)}"
        if e.get('source_url'):
            line += f" ([original]({e['source_url']}))"
        lines.append(line)
    if revoked:
        lines.append(f"*{revoked} revoked entr{'ies' if revoked != 1 else 'y'} not shown*")

    description = '\n'.join(parts) + '\n\n' + '\n'.join(lines)
    if len(description) > 4000:
        description = description[:3990].rsplit('\n', 1)[0] + '\n…'

    embed = discord.Embed(
        title=profile['ign'],
        url=f"{website_url}/exec/warnings/{profile['uuid']}",
        description=description,
        color=KIND_COLORS['formal'] if counts['total'] else 0x64748B,
    )
    embed.set_thumbnail(url=f"https://mc-heads.net/avatar/{profile['uuid']}/64")
    embed.set_footer(text='Kept up to date by the bot · full record and proof on the website')
    return embed


def entry_embed(entry):
    """One warning as posted in the player's thread. Revoked entries say so."""
    label = KIND_LABELS[entry['kind']]
    revoked = entry['revoked_at'] is not None
    embed = discord.Embed(
        title=f"~~{label}~~ (revoked)" if revoked else label,
        description=_clip(entry['rule'], 1000),
        color=0x64748B if revoked else KIND_COLORS[entry['kind']],
        timestamp=entry['issued_at'],
    )
    embed.add_field(name='Issued by', value=f"<@{entry['issued_by']}>", inline=True)
    if entry.get('ign') and entry.get('profile_ign') and entry['ign'] != entry['profile_ign']:
        embed.add_field(name='Name at the time', value=entry['ign'], inline=True)
    if entry.get('notes'):
        embed.add_field(name='Notes', value=_clip(entry['notes'], 1000), inline=False)
    if revoked:
        who = f"<@{entry['revoked_by']}>" if entry.get('revoked_by') else 'unknown'
        embed.add_field(
            name='Revoked',
            value=f"{_date(entry['revoked_at'])} by {who}: {_clip(entry.get('revoke_reason') or '', 500)}",
            inline=False,
        )
    return embed


# --- DB: forum mirror ---------------------------------------------------------

def reset_stale_claims(cursor):
    """Release claims left by a crash mid-post, so those entries post again."""
    cursor.execute("UPDATE player_warnings SET discord_message_id = NULL WHERE discord_message_id = -1")
    return cursor.rowcount


def profiles_needing_work(cursor):
    """(uuid, has_thread) for every profile whose post, summary or entries need attention.
    Profiles that already have a thread come first, so the creation pace limit
    never delays updates to existing posts."""
    cursor.execute(
        """SELECT p.uuid::text, p.forum_thread_id IS NOT NULL
             FROM warning_profiles p
            WHERE p.needs_sync
               OR p.forum_thread_id IS NULL
               OR EXISTS (SELECT 1 FROM player_warnings w
                           WHERE w.uuid = p.uuid
                             AND (w.discord_message_id IS NULL
                                  OR (w.discord_message_id > 0
                                      AND w.updated_at > COALESCE(w.discord_synced_at, '-infinity'))))
            ORDER BY (p.forum_thread_id IS NULL), p.updated_at"""
    )
    return [(r[0], r[1]) for r in cursor.fetchall()]


_ENTRY_COLUMNS = """w.id, w.ign, w.kind, w.rule, w.notes, w.issued_by, w.issued_at,
                    w.revoked_at, w.revoked_by, w.revoke_reason, w.source_url,
                    w.discord_message_id, w.discord_synced_at, w.updated_at"""
_ENTRY_KEYS = ('id', 'ign', 'kind', 'rule', 'notes', 'issued_by', 'issued_at',
               'revoked_at', 'revoked_by', 'revoke_reason', 'source_url',
               'discord_message_id', 'discord_synced_at', 'updated_at')


def load_profile(cursor, uuid):
    """Profile dict with its entries (newest first), or None if it is gone."""
    cursor.execute(
        """SELECT uuid::text, ign, forum_thread_id, needs_sync, updated_at
             FROM warning_profiles WHERE uuid = %s""",
        (uuid,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    profile = dict(zip(('uuid', 'ign', 'forum_thread_id', 'needs_sync', 'updated_at'), row))
    cursor.execute(
        f"SELECT {_ENTRY_COLUMNS} FROM player_warnings w WHERE w.uuid = %s ORDER BY w.issued_at DESC, w.id DESC",
        (uuid,),
    )
    profile['entries'] = [dict(zip(_ENTRY_KEYS, r), profile_ign=profile['ign']) for r in cursor.fetchall()]
    return profile


def attachments_for(cursor, warning_id, since=None):
    """(filename, content_type, s3_key) for an entry's uploaded screenshots, optionally only newer ones."""
    cursor.execute(
        """SELECT filename, content_type, s3_key FROM player_warning_attachments
            WHERE warning_id = %s AND s3_key <> ''
              AND created_at > COALESCE(%s, '-infinity'::timestamptz)
            ORDER BY id""",
        (warning_id, since),
    )
    return cursor.fetchall()


def set_thread(cursor, uuid, thread_id):
    cursor.execute("UPDATE warning_profiles SET forum_thread_id = %s WHERE uuid = %s", (thread_id, uuid))


def claim_entry(cursor, warning_id):
    """Claim an unposted entry. False if someone else already did."""
    cursor.execute(
        "UPDATE player_warnings SET discord_message_id = -1 WHERE id = %s AND discord_message_id IS NULL",
        (warning_id,),
    )
    return cursor.rowcount == 1


def release_claim(cursor, warning_id):
    cursor.execute(
        "UPDATE player_warnings SET discord_message_id = NULL WHERE id = %s AND discord_message_id = -1",
        (warning_id,),
    )


def mark_posted(cursor, warning_id, message_id, rendered_at):
    cursor.execute(
        "UPDATE player_warnings SET discord_message_id = %s, discord_synced_at = %s WHERE id = %s",
        (message_id, rendered_at, warning_id),
    )


def mark_synced(cursor, warning_id, rendered_at):
    cursor.execute(
        "UPDATE player_warnings SET discord_synced_at = %s WHERE id = %s",
        (rendered_at, warning_id),
    )


def clear_needs_sync(cursor, uuid, rendered_at):
    """Clear the flag only if nothing changed since the render started."""
    cursor.execute(
        "UPDATE warning_profiles SET needs_sync = FALSE WHERE uuid = %s AND updated_at <= %s",
        (uuid, rendered_at),
    )


# --- DB: Dishonored Fish role -------------------------------------------------

def dishonored_holders(cursor):
    """{discord_id: uuid} for every player with an active dishonored_fish entry
    and a known Discord account. The current link by uuid wins; the id cached on
    the profile only covers players who have since unlinked."""
    cursor.execute(
        """SELECT DISTINCT COALESCE(dl.discord_id, p.discord_id), p.uuid::text
             FROM warning_profiles p
             JOIN player_warnings w ON w.uuid = p.uuid
             LEFT JOIN discord_links dl ON dl.uuid = p.uuid
            WHERE w.kind = 'dishonored_fish' AND w.revoked_at IS NULL
              AND COALESCE(dl.discord_id, p.discord_id) IS NOT NULL"""
    )
    return {int(r[0]): r[1] for r in cursor.fetchall()}


def dishonored_changes_since(cursor, since):
    """Profiles whose dishonored_fish entries changed after ``since``:
    [(discord_id or None, uuid, still_active, changed_at)]."""
    cursor.execute(
        """SELECT COALESCE(dl.discord_id, p.discord_id), p.uuid::text,
                  BOOL_OR(w2.revoked_at IS NULL), MAX(w.updated_at)
             FROM player_warnings w
             JOIN warning_profiles p ON p.uuid = w.uuid
             JOIN player_warnings w2 ON w2.uuid = w.uuid AND w2.kind = 'dishonored_fish'
             LEFT JOIN discord_links dl ON dl.uuid = p.uuid
            WHERE w.kind = 'dishonored_fish' AND w.updated_at > COALESCE(%s, '-infinity'::timestamptz)
            GROUP BY 1, 2""",
        (since,),
    )
    return [(int(r[0]) if r[0] else None, r[1], r[2], r[3]) for r in cursor.fetchall()]


def uuid_for_discord(cursor, discord_id):
    """(uuid, ign) for a Discord account: its current link, else a warning profile that
    still names it (the player has since unlinked)."""
    cursor.execute("SELECT uuid::text, ign FROM discord_links WHERE discord_id = %s LIMIT 1", (discord_id,))
    row = cursor.fetchone()
    if row:
        return row
    cursor.execute(
        """SELECT uuid::text, ign FROM warning_profiles WHERE discord_id = %s
            ORDER BY updated_at DESC LIMIT 1""",
        (discord_id,),
    )
    return cursor.fetchone()


def record_role_added(cursor, discord_id, actor_id):
    """The role was given by hand in Discord. Adds a dishonored_fish entry unless one is
    already active. Returns 'recorded', 'already_active' or 'unlinked'."""
    found = uuid_for_discord(cursor, discord_id)
    if not found:
        return 'unlinked'
    uuid, ign = found
    cursor.execute(
        """SELECT 1 FROM player_warnings
            WHERE uuid = %s AND kind = 'dishonored_fish' AND revoked_at IS NULL LIMIT 1""",
        (uuid,),
    )
    if cursor.fetchone():
        return 'already_active'
    cursor.execute(
        """INSERT INTO warning_profiles (uuid, ign, discord_id) VALUES (%s, %s, %s)
           ON CONFLICT (uuid) DO UPDATE
             SET discord_id = EXCLUDED.discord_id,
                 needs_sync = TRUE, updated_at = NOW()""",
        (uuid, ign, discord_id),
    )
    cursor.execute(
        """INSERT INTO player_warnings (uuid, ign, kind, rule, issued_by, source)
           VALUES (%s, %s, 'dishonored_fish', %s, %s, 'discord')""",
        (uuid, ign, DISCORD_SOURCE_RULE_ADDED, actor_id),
    )
    return 'recorded'


def record_role_removed(cursor, discord_id, actor_id):
    """The role was removed by hand in Discord: revoke the active dishonored_fish
    entries. Returns how many were revoked (0 if none or the player is unknown)."""
    found = uuid_for_discord(cursor, discord_id)
    if not found:
        return 0
    uuid = found[0]
    cursor.execute(
        """UPDATE player_warnings
              SET revoked_at = NOW(), revoked_by = %s, revoke_reason = %s, updated_at = NOW()
            WHERE uuid = %s AND kind = 'dishonored_fish' AND revoked_at IS NULL""",
        (actor_id, REVOKE_REASON_ROLE_REMOVED, uuid),
    )
    revoked = cursor.rowcount
    if revoked:
        cursor.execute(
            "UPDATE warning_profiles SET needs_sync = TRUE, updated_at = NOW() WHERE uuid = %s", (uuid,)
        )
    return revoked


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)
