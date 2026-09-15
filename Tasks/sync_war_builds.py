"""Keep the website's war builds and the Discord war roles in agreement.

The two sides hold different amounts of detail. The database records which
*build* a member runs (``guardian`` pinned to v2.0, say); Discord only records
the coarse role that build implies. So the sync is asymmetric:

* database -> Discord is a faithful projection: a member holds ``Tank`` exactly
  when they have at least one *active* build whose definition has role
  ``TANK``. Archived builds and archived versions (TAQ-29) don't count: the
  assignment rows are kept as the record of who had them, but they produce no
  role, receive no new assignments, and are ignored when a human removes a
  role in Discord.
* Discord -> database is lossy: someone adding ``Tank`` by hand can only mean
  "give them the default tank build", and removing it means "drop their tank
  builds".

Both directions are supported, but only the reconciler writes anything. The
gateway listener never touches the database -- it just flags the member so the
next tick can re-derive both sides from authoritative state. Every role write
the reconciler makes is registered with the echo suppressor first, so the
gateway event it provokes is recognised as our own work rather than mistaken
for a human edit. Without that, the two halves treat each other's writes as
instructions and oscillate, deleting real build assignments on the way.
"""

import asyncio
import time

import discord
from discord.ext import tasks, commands

from Helpers.logger import log, INFO, WARN, ERROR
from Helpers.database import DB
from Helpers.variables import TAQ_GUILD_ID

# Discord role name <-> DB build role
DISCORD_TO_DB_ROLE = {
    'DPS':    'DPS',
    'Healer': 'HEALER',
    'Tank':   'TANK',
}
DB_TO_DISCORD_ROLE = {v: k for k, v in DISCORD_TO_DB_ROLE.items()}

WAR_ROLE_NAMES = frozenset(DISCORD_TO_DB_ROLE)

# How long a role write of ours stays "expected". Gateway echoes normally land
# in well under a second; the generous window absorbs a lagging shard without
# being long enough to swallow a genuine human edit made straight afterwards.
ECHO_TTL_SECONDS = 30


class EchoSuppressor:
    """Remembers role writes the reconciler made.

    Discord echoes every role change back over the gateway. Those echoes are
    indistinguishable from a human edit unless we record what we did first,
    which is what this class is for.
    """

    def __init__(self, ttl=ECHO_TTL_SECONDS, clock=time.monotonic):
        self._ttl = ttl
        self._clock = clock
        self._pending = {}

    def expect(self, member_id, role_names):
        """Record role names we are about to write for ``member_id``."""
        deadline = self._clock() + self._ttl
        for name in role_names:
            self._pending[(member_id, name)] = deadline

    def consume(self, member_id, role_names):
        """Return the subset of ``role_names`` we did not cause ourselves."""
        now = self._clock()
        unexplained = set()
        for name in role_names:
            deadline = self._pending.pop((member_id, name), None)
            if deadline is None or deadline < now:
                unexplained.add(name)
        return unexplained

    def purge(self):
        """Drop expired entries so the table can't grow without bound."""
        now = self._clock()
        for key, deadline in list(self._pending.items()):
            if deadline < now:
                del self._pending[key]

    def __len__(self):
        return len(self._pending)


def plan_role_changes(current_roles, desired_roles):
    """Database -> Discord. Returns (roles_to_add, roles_to_remove)."""
    return desired_roles - current_roles, current_roles - desired_roles


def plan_build_changes(discord_roles, db_roles):
    """Discord -> database.

    Both arguments are sets of *Discord* role names. Returns the roles that
    need a build assigning and the roles whose builds should be dropped.
    """
    return discord_roles - db_roles, db_roles - discord_roles


def roles_to_discord_names(db_roles):
    """Map DB role values onto Discord role names, dropping unknown values."""
    return {DB_TO_DISCORD_ROLE[r] for r in db_roles if r in DB_TO_DISCORD_ROLE}


# ── DB helpers (blocking, run via asyncio.to_thread) ─────────────────────

def _fetch_state():
    """Read everything the reconciler needs in one connection.

    ``member_builds.uuid`` is a varchar while ``discord_links.uuid`` is a real
    uuid column, so both are cast to text here. They happen to arrive as
    equal strings today, but the cast means a future ``register_uuid()``
    can't silently turn every lookup into a miss -- which would read as
    "nobody has any builds" and strip the roles off the whole guild.

    Linked rows are ordered last so that when an old unlinked row shares a
    uuid with the current one, the live Discord id wins.
    """
    with DB() as db:
        # Only active assignments count: an archived definition or an archived
        # pinned version produces no role, which is what actually retires it.
        db.cursor.execute("""
            SELECT mb.uuid::text, bd.role
            FROM member_builds mb
            JOIN build_definitions bd ON mb.build_key = bd.key
            JOIN build_versions bv
              ON bv.build_key = mb.build_key
             AND bv.major = mb.version_major
             AND bv.minor = mb.version_minor
            WHERE NOT bd.archived AND NOT bv.archived
        """)
        builds = {}
        for uuid, role in db.cursor.fetchall():
            builds.setdefault(uuid, set()).add(role)

        db.cursor.execute("""
            SELECT uuid::text, discord_id
            FROM discord_links
        """)
        uuid_to_discord = {}
        discord_to_uuid = {}
        for uuid, discord_id in db.cursor.fetchall():
            uuid_to_discord[uuid] = str(discord_id)
            discord_to_uuid[str(discord_id)] = uuid

        return builds, uuid_to_discord, discord_to_uuid


def _get_default_build_key(db_role):
    """Get the first assignable build key for a role (by sort_order).

    Archived builds, and builds whose every version is archived, are skipped:
    a human granting ``DPS`` in Discord means "give them a working build", and
    before TAQ-29 this handed out whatever sorted first -- even a dead one.
    """
    with DB() as db:
        db.cursor.execute(
            """SELECT bd.key FROM build_definitions bd
               WHERE bd.role = %s AND NOT bd.archived
                 AND EXISTS (SELECT 1 FROM build_versions bv
                             WHERE bv.build_key = bd.key AND NOT bv.archived)
               ORDER BY bd.sort_order LIMIT 1""",
            (db_role,)
        )
        row = db.cursor.fetchone()
        return row[0] if row else None


def _add_member_build(uuid, build_key, assigned_by='discord_sync'):
    """Insert a member_builds row pinned to the build's latest active version.

    member_builds.version_major/minor are NOT NULL, so we must look up the
    current latest from build_versions before inserting. If the build has no
    active versions, we skip the insert and log a warning.

    If the member already holds this build pinned to an *archived* version,
    the conflict clause upgrades the pin instead of doing nothing. A plain
    DO NOTHING would leave the archived pin in place, the projection would
    read it as "no role", and the human's role grant would be silently
    reverted on the next tick -- forever.
    """
    with DB() as db:
        db.cursor.execute(
            """SELECT major, minor FROM build_versions
               WHERE build_key = %s AND NOT archived
               ORDER BY major DESC, minor DESC
               LIMIT 1""",
            (build_key,)
        )
        row = db.cursor.fetchone()
        if not row:
            log(WARN, f"No active versions exist for build '{build_key}'; skipping auto-assign",
                context="sync_war_builds")
            return False
        major, minor = row

        db.cursor.execute(
            """INSERT INTO member_builds (uuid, build_key, version_major, version_minor, assigned_by)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (uuid, build_key) DO UPDATE
                 SET prev_version_major = member_builds.version_major,
                     prev_version_minor = member_builds.version_minor,
                     version_major = EXCLUDED.version_major,
                     version_minor = EXCLUDED.version_minor,
                     assigned_by   = EXCLUDED.assigned_by
                 WHERE EXISTS (
                     SELECT 1 FROM build_versions bv
                     WHERE bv.build_key = member_builds.build_key
                       AND bv.major = member_builds.version_major
                       AND bv.minor = member_builds.version_minor
                       AND bv.archived
                 )""",
            (uuid, build_key, major, minor, assigned_by)
        )
        db.connection.commit()
        return True


def _remove_member_builds_by_role(uuid, db_role):
    """Delete a member's *active* builds for one role, returning what was
    removed.

    Archived assignments are left alone: they already produce no role, and
    they are the record of who had the build (TAQ-29) -- a Discord role
    removal must not erase history it wasn't expressing an opinion about.

    The rows are returned so the caller can log them: an exec's assignment is
    not recoverable from the database once it is gone, so the bot log is the
    only restore trail there is.
    """
    with DB() as db:
        db.cursor.execute(
            """DELETE FROM member_builds mb
               USING build_definitions bd, build_versions bv
               WHERE mb.uuid = %s
                 AND bd.key = mb.build_key AND bd.role = %s AND NOT bd.archived
                 AND bv.build_key = mb.build_key
                 AND bv.major = mb.version_major
                 AND bv.minor = mb.version_minor
                 AND NOT bv.archived
               RETURNING mb.build_key, mb.version_major, mb.version_minor""",
            (uuid, db_role)
        )
        removed = db.cursor.fetchall()
        db.connection.commit()
        return [(key, major, minor) for key, major, minor in removed]


class SyncWarBuilds(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.echo = EchoSuppressor()
        # Members whose war roles were changed in Discord by someone other than
        # us, awaiting a reconcile against freshly fetched state.
        self.dirty = set()

    # ── Event: notice human role changes, but don't act on them here ──

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if after.guild is None or after.guild.id != TAQ_GUILD_ID:
            return

        before_war = {r.name for r in before.roles if r.name in WAR_ROLE_NAMES}
        after_war = {r.name for r in after.roles if r.name in WAR_ROLE_NAMES}
        changed = before_war ^ after_war
        if not changed:
            return

        unexplained = self.echo.consume(after.id, changed)
        if not unexplained:
            return

        # Deliberately no database write here. This payload can race another
        # update to the same member, and a delta computed from a raced pair
        # used to delete builds that were never actually unassigned. Flag the
        # member instead and let the next tick decide from fetched state.
        self.dirty.add(after.id)
        log(INFO,
            f"{after.display_name} war roles changed outside the sync "
            f"({sorted(unexplained)}); queued for reconcile",
            context="sync_war_builds")

    # ── Reconciler: the only thing that writes to either side ────────

    @tasks.loop(seconds=60)
    async def sync_builds_to_discord(self):
        try:
            await self._reconcile()
        except Exception as e:
            log(ERROR, f"Reconcile tick failed: {e}", context="sync_war_builds")

    async def _reconcile(self):
        guild = self.client.get_guild(TAQ_GUILD_ID)
        if not guild:
            return

        # Every war role has to resolve before we touch anything. Acting on a
        # partial mapping means treating "role object missing" as "member
        # shouldn't have it" and stripping it from the whole guild.
        role_objects = {}
        for role_name in WAR_ROLE_NAMES:
            role_obj = discord.utils.get(guild.roles, name=role_name)
            if role_obj:
                role_objects[role_name] = role_obj
        missing = WAR_ROLE_NAMES - set(role_objects)
        if missing:
            log(WARN, f"War role(s) {sorted(missing)} not found; skipping tick",
                context="sync_war_builds")
            return

        # A partially populated member cache would look like a guild full of
        # people who suddenly hold no roles.
        if not guild.chunked:
            log(WARN, "Member cache not chunked yet; skipping tick",
                context="sync_war_builds")
            return

        self.echo.purge()

        state = await asyncio.to_thread(_fetch_state)

        # Phase 1: fold human edits into the database, so that the projection
        # below agrees with them instead of reverting them.
        claimed, self.dirty = self.dirty, set()
        folded = 0
        for member_id in claimed:
            try:
                folded += await self._apply_discord_intent(guild, member_id, state)
            except Exception as e:
                log(ERROR, f"Failed to apply Discord role change for {member_id}: {e}",
                    context="sync_war_builds")

        # Phase 2: project the database onto Discord. Only worth re-reading if
        # phase 1 actually changed something.
        if folded:
            state = await asyncio.to_thread(_fetch_state)
        builds, uuid_to_discord, _ = state

        desired_by_discord = {}
        for uuid, db_roles in builds.items():
            discord_id = uuid_to_discord.get(uuid)
            if discord_id:
                desired_by_discord[discord_id] = roles_to_discord_names(db_roles)

        # Only members we can resolve to a Minecraft account are in scope. For
        # anyone else the database holds no opinion, and "no opinion" must not
        # be read as "remove their roles".
        linked_discord_ids = set(uuid_to_discord.values())

        for member in guild.members:
            member_key = str(member.id)
            if member_key not in linked_discord_ids:
                continue
            if member.id in self.dirty:
                # Touched again while this tick was running; next pass has the
                # fresher view.
                continue

            current = {r.name for r in member.roles if r.name in WAR_ROLE_NAMES}
            desired = desired_by_discord.get(member_key, set())
            to_add_names, to_remove_names = plan_role_changes(current, desired)
            if not to_add_names and not to_remove_names:
                continue

            # Register before writing: the gateway event can otherwise arrive
            # before we finish, and be read as a human edit.
            self.echo.expect(member.id, to_add_names | to_remove_names)

            try:
                if to_add_names:
                    await member.add_roles(
                        *[role_objects[n] for n in to_add_names],
                        reason="War build sync (website)")
                    log(INFO, f"Added {sorted(to_add_names)} to {member.display_name}",
                        context="sync_war_builds")
                if to_remove_names:
                    await member.remove_roles(
                        *[role_objects[n] for n in to_remove_names],
                        reason="War build sync (website)")
                    log(INFO, f"Removed {sorted(to_remove_names)} from {member.display_name}",
                        context="sync_war_builds")
            except discord.Forbidden:
                log(WARN, f"Missing permissions to update roles for {member.display_name}",
                    context="sync_war_builds")
            except Exception as e:
                log(ERROR, f"Failed to update roles for {member.display_name}: {e}",
                    context="sync_war_builds")

        if folded:
            log(INFO, f"Applied {folded} Discord-side role change(s) to the database",
                context="sync_war_builds")

    async def _apply_discord_intent(self, guild, member_id, state):
        """Make the database match Discord for one member a human edited.

        The member is fetched from the API rather than read from the gateway
        cache, because this is the path that deletes build assignments and a
        raced cache entry is exactly how they got deleted spuriously before.
        """
        try:
            member = await guild.fetch_member(member_id)
        except discord.NotFound:
            return 0
        except discord.HTTPException as e:
            log(WARN, f"Could not fetch member {member_id}: {e}",
                context="sync_war_builds")
            self.dirty.add(member_id)  # try again next tick
            return 0

        builds, _, discord_to_uuid = state
        uuid = discord_to_uuid.get(str(member_id))
        if not uuid:
            log(WARN,
                f"{member.display_name} has no linked account; "
                f"ignoring their Discord war role change",
                context="sync_war_builds")
            return 0

        current = {r.name for r in member.roles if r.name in WAR_ROLE_NAMES}
        in_db = roles_to_discord_names(builds.get(uuid, set()))
        need_build, drop_build = plan_build_changes(current, in_db)

        changes = 0
        for role_name in sorted(need_build):
            db_role = DISCORD_TO_DB_ROLE[role_name]
            build_key = await asyncio.to_thread(_get_default_build_key, db_role)
            if not build_key:
                log(WARN, f"No build defined for {db_role}; cannot honour "
                          f"{role_name} on {member.display_name}",
                    context="sync_war_builds")
                continue
            if await asyncio.to_thread(_add_member_build, uuid, build_key):
                changes += 1
                log(INFO, f"{member.display_name} gained {role_name} in Discord "
                          f"-> assigned default build '{build_key}'",
                    context="sync_war_builds")

        for role_name in sorted(drop_build):
            db_role = DISCORD_TO_DB_ROLE[role_name]
            removed = await asyncio.to_thread(_remove_member_builds_by_role, uuid, db_role)
            if removed:
                changes += 1
                detail = ", ".join(f"{k} v{maj}.{minor}" for k, maj, minor in removed)
                log(INFO, f"{member.display_name} lost {role_name} in Discord "
                          f"-> removed {detail}",
                    context="sync_war_builds")

        return changes

    # ── Lifecycle ────────────────────────────────────────────────────

    @sync_builds_to_discord.before_loop
    async def before_sync(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.sync_builds_to_discord.is_running():
            self.sync_builds_to_discord.start()


def setup(client):
    client.add_cog(SyncWarBuilds(client))
