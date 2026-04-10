import asyncio

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

WAR_ROLE_NAMES = set(DISCORD_TO_DB_ROLE.keys())


# ── DB helpers (blocking, run via asyncio.to_thread) ─────────────────────

def _get_desired_roles():
    """Return {uuid: set(db_role)} from member_builds + build_definitions."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute("""
            SELECT mb.uuid, bd.role
            FROM member_builds mb
            JOIN build_definitions bd ON mb.build_key = bd.key
        """)
        result = {}
        for uuid, role in db.cursor.fetchall():
            result.setdefault(uuid, set()).add(role)
        return result
    finally:
        db.close()


def _get_discord_links():
    """Return {uuid: discord_id} and {discord_id: uuid} mappings."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute("SELECT uuid, discord_id FROM discord_links")
        uuid_to_discord = {}
        discord_to_uuid = {}
        for uuid, discord_id in db.cursor.fetchall():
            uuid_to_discord[uuid] = str(discord_id)
            discord_to_uuid[str(discord_id)] = uuid
        return uuid_to_discord, discord_to_uuid
    finally:
        db.close()


def _get_default_build_key(db_role):
    """Get the first build key for a role (by sort_order)."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "SELECT key FROM build_definitions WHERE role = %s ORDER BY sort_order LIMIT 1",
            (db_role,)
        )
        row = db.cursor.fetchone()
        return row[0] if row else None
    finally:
        db.close()


def _add_member_build(uuid, build_key, assigned_by='discord_sync'):
    """Insert a member_builds row pinned to the build's latest version.

    member_builds.version_major/minor are NOT NULL, so we must look up the
    current latest from build_versions before inserting. If the build has no
    versions yet, we skip the insert and log a warning.
    """
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            """SELECT major, minor FROM build_versions
               WHERE build_key = %s
               ORDER BY major DESC, minor DESC
               LIMIT 1""",
            (build_key,)
        )
        row = db.cursor.fetchone()
        if not row:
            log(WARN, f"No versions exist for build '{build_key}'; skipping auto-assign",
                context="sync_war_builds")
            return
        major, minor = row

        db.cursor.execute(
            """INSERT INTO member_builds (uuid, build_key, version_major, version_minor, assigned_by)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (uuid, build_key) DO NOTHING""",
            (uuid, build_key, major, minor, assigned_by)
        )
        db.connection.commit()
    finally:
        db.close()


def _remove_member_builds_by_role(uuid, db_role):
    """Remove all builds for a member that match a given role."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            """DELETE FROM member_builds
               WHERE uuid = %s AND build_key IN (
                   SELECT key FROM build_definitions WHERE role = %s
               )""",
            (uuid, db_role)
        )
        db.connection.commit()
    finally:
        db.close()


def _get_member_roles_for_uuid(uuid):
    """Return set of DB roles a member currently has builds for."""
    db = DB()
    db.connect()
    try:
        db.cursor.execute("""
            SELECT DISTINCT bd.role
            FROM member_builds mb
            JOIN build_definitions bd ON mb.build_key = bd.key
            WHERE mb.uuid = %s
        """, (uuid,))
        return {row[0] for row in db.cursor.fetchall()}
    finally:
        db.close()


class SyncWarBuilds(commands.Cog):
    def __init__(self, client):
        self.client = client

    # ── Polling: Website DB -> Discord roles ─────────────────────────

    @tasks.loop(seconds=60)
    async def sync_builds_to_discord(self):
        """Compare member_builds against Discord roles and reconcile."""
        guild = self.client.get_guild(TAQ_GUILD_ID)
        if not guild:
            return

        # Resolve Discord role objects by name
        role_objects = {}
        for role_name in WAR_ROLE_NAMES:
            role_obj = discord.utils.get(guild.roles, name=role_name)
            if role_obj:
                role_objects[role_name] = role_obj

        if not role_objects:
            return

        # Get desired state from DB
        desired = await asyncio.to_thread(_get_desired_roles)
        uuid_to_discord, discord_to_uuid = await asyncio.to_thread(_get_discord_links)

        # Build desired Discord roles per discord_id
        # desired_discord[discord_id] = set of Discord role names they should have
        desired_discord = {}
        for uuid, db_roles in desired.items():
            discord_id = uuid_to_discord.get(uuid)
            if not discord_id:
                continue
            discord_role_names = set()
            for db_role in db_roles:
                discord_name = DB_TO_DISCORD_ROLE.get(db_role)
                if discord_name:
                    discord_role_names.add(discord_name)
            desired_discord[discord_id] = discord_role_names

        # Also track members who have war roles but shouldn't (builds removed via website)
        for member in guild.members:
            member_id = str(member.id)
            current_war_roles = {r.name for r in member.roles if r.name in WAR_ROLE_NAMES}
            desired_war_roles = desired_discord.get(member_id, set())

            if current_war_roles == desired_war_roles:
                continue

            # Roles to add
            to_add = desired_war_roles - current_war_roles
            # Roles to remove
            to_remove = current_war_roles - desired_war_roles

            roles_to_add = [role_objects[name] for name in to_add if name in role_objects]
            roles_to_remove = [role_objects[name] for name in to_remove if name in role_objects]

            try:
                if roles_to_add:
                    await member.add_roles(*roles_to_add, reason="War build sync (website)")
                    log(INFO, f"Added {[r.name for r in roles_to_add]} to {member.display_name}",
                        context="sync_war_builds")
                if roles_to_remove:
                    await member.remove_roles(*roles_to_remove, reason="War build sync (website)")
                    log(INFO, f"Removed {[r.name for r in roles_to_remove]} from {member.display_name}",
                        context="sync_war_builds")
            except discord.Forbidden:
                log(WARN, f"Missing permissions to update roles for {member.display_name}",
                    context="sync_war_builds")
            except Exception as e:
                log(ERROR, f"Failed to update roles for {member.display_name}: {e}",
                    context="sync_war_builds")

    # ── Event: Discord role changes -> Website DB ────────────────────

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles == after.roles:
            return

        # Only care about war role changes
        before_war = {r.name for r in before.roles if r.name in WAR_ROLE_NAMES}
        after_war = {r.name for r in after.roles if r.name in WAR_ROLE_NAMES}

        if before_war == after_war:
            return

        added = after_war - before_war
        removed = before_war - after_war

        if not added and not removed:
            return

        # Look up UUID for this Discord user
        _, discord_to_uuid = await asyncio.to_thread(_get_discord_links)
        uuid = discord_to_uuid.get(str(after.id))
        if not uuid:
            return

        # Handle added roles
        for role_name in added:
            db_role = DISCORD_TO_DB_ROLE.get(role_name)
            if not db_role:
                continue

            # Check if they already have a build for this role
            current_roles = await asyncio.to_thread(_get_member_roles_for_uuid, uuid)
            if db_role in current_roles:
                continue

            # Add the default build for this role
            build_key = await asyncio.to_thread(_get_default_build_key, db_role)
            if build_key:
                await asyncio.to_thread(_add_member_build, uuid, build_key)
                log(INFO, f"Added build '{build_key}' for {after.display_name} (Discord role: {role_name})",
                    context="sync_war_builds")

        # Handle removed roles
        for role_name in removed:
            db_role = DISCORD_TO_DB_ROLE.get(role_name)
            if not db_role:
                continue

            await asyncio.to_thread(_remove_member_builds_by_role, uuid, db_role)
            log(INFO, f"Removed {db_role} builds for {after.display_name} (Discord role: {role_name})",
                context="sync_war_builds")

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
