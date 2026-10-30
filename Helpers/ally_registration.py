import asyncio
from dataclasses import dataclass
from uuid import UUID

import discord
import psycopg2

from Helpers.alliances import Alliance, enabled_alliances
from Helpers.database import DB
from Helpers.functions import getPlayerDatav3, getPlayerUUID
from Helpers.links import LinkConflictError, assert_uuid_free
from Helpers.logger import ERROR, log
from Helpers.registration import set_rank, upsert_identity


class AllyRegistrationError(ValueError):
    pass


@dataclass(frozen=True)
class RegisteredAlly:
    alliance: Alliance
    ign: str
    uuid: str


def match_alliance(alliances: list[Alliance], name: str, prefix: str = '') -> Alliance | None:
    keys = {value.strip().casefold() for value in (name, prefix) if value.strip()}
    matches = [ally for ally in alliances if keys.intersection({
        ally.name.casefold(), ally.prefix.casefold(),
        f'{ally.name} [{ally.prefix}]'.casefold(),
        f'{ally.name} ({ally.prefix})'.casefold(),
    })]
    if len(matches) > 1:
        raise AllyRegistrationError('Ally registration needs review: more than one guild matches.')
    return matches[0] if matches else None


def community_alliance(alliances: list[Alliance], declared_guild: str, profile, uuid: str) -> Alliance | None:
    declared = match_alliance(alliances, declared_guild)
    if not isinstance(profile, dict) or 'guild' not in profile:
        return declared
    try:
        same_player = UUID(str(profile.get('uuid'))) == UUID(str(uuid))
    except ValueError:
        same_player = False
    if not same_player:
        raise AllyRegistrationError('Ally registration needs review: Wynncraft returned a different account.')
    guild = profile['guild']
    if guild is None:
        if declared:
            raise AllyRegistrationError('Ally registration needs review: Wynncraft lists no current guild.')
        return None
    if not isinstance(guild, dict) or not guild.get('name'):
        raise AllyRegistrationError('Ally registration needs review: Wynncraft guild data is incomplete.')
    current = match_alliance(alliances, guild['name'], guild.get('prefix') or '')
    if declared and (current is None or declared.id != current.id):
        raise AllyRegistrationError('Ally registration needs review: the application and current guild disagree.')
    return current


def _check_identity(cursor, discord_id: int, uuid: str):
    assert_uuid_free(cursor, uuid, discord_id)
    cursor.execute(
        """SELECT EXISTS (SELECT 1 FROM guild_roster WHERE uuid = %s::uuid)
                  OR EXISTS (SELECT 1 FROM discord_links d
                             JOIN rank_definitions r ON r.name = d.rank
                             WHERE d.discord_id = %s AND r.kind = 'member')""",
        (uuid, discord_id),
    )
    if cursor.fetchone()[0]:
        raise AllyRegistrationError('Ally registration needs review: this account is registered as a TAq member.')


def _check_registration(discord_id: int, uuid: str):
    db = DB()
    db.connect()
    try:
        _check_identity(db.cursor, discord_id, uuid)
    finally:
        db.close()


def _save_registration(discord_id: int, ign: str, uuid: str, alliance: Alliance):
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            """SELECT a.display_rank, a.discord_role_id FROM guild_alliances a
               JOIN rank_definitions r ON r.name = a.display_rank AND r.kind = 'ally'
               WHERE a.id = %s AND a.enabled = TRUE FOR SHARE OF a, r""",
            (alliance.id,),
        )
        if db.cursor.fetchone() != (alliance.rank, alliance.role_id):
            raise AllyRegistrationError('The alliance configuration changed. Run registration again.')
        _check_identity(db.cursor, discord_id, uuid)
        upsert_identity(db.cursor, discord_id=discord_id, ign=ign, uuid=uuid)
        set_rank(db.cursor, discord_id, alliance.rank, alliance_id=alliance.id)
        db.connection.commit()
    finally:
        db.close()


async def register_ally(member: discord.Member, ign: str, alliance_id: str, *, actor_name: str,
                        identity: tuple[str, str] | None = None) -> RegisteredAlly:
    alliances = await asyncio.to_thread(enabled_alliances)
    alliance = next((ally for ally in alliances if str(ally.id) == str(alliance_id)), None)
    if alliance is None:
        raise AllyRegistrationError('Choose an enabled guild from the suggestions.')
    if alliance.rank_kind != 'ally':
        raise AllyRegistrationError('This guild needs a valid ally rank in Externals & alliances.')
    role = member.guild.get_role(alliance.role_id)
    if role is None:
        raise AllyRegistrationError(f'Could not find the configured {alliance.name} role.')
    identity = identity or await asyncio.to_thread(getPlayerUUID, ign)
    if not identity:
        raise AllyRegistrationError(f'Could not find a Minecraft account for `{ign}`.')
    canonical_ign, uuid = identity
    await asyncio.to_thread(_check_registration, member.id, str(uuid))
    if role not in member.roles:
        await member.add_roles(role, reason=f'Ally registration for {alliance.name} (ran by {actor_name})', atomic=True)
    await member.edit(nick=f'{alliance.rank} {canonical_ign}')
    await asyncio.to_thread(_save_registration, member.id, canonical_ign, str(uuid), alliance)
    return RegisteredAlly(alliance, canonical_ign, str(uuid))


def _link_identity(discord_id: int, ign: str, uuid: str):
    db = DB()
    db.connect()
    try:
        upsert_identity(db.cursor, discord_id=discord_id, ign=ign, uuid=uuid)
        db.connection.commit()
    finally:
        db.close()


async def register_community_member(member: discord.Member | None, discord_id: int, ign: str,
                                    declared_guild: str, *, actor_name: str) -> str:
    identity = await asyncio.to_thread(getPlayerUUID, ign)
    if not identity:
        return 'Registration needs review: the Minecraft account could not be resolved.'
    canonical_ign, uuid = identity
    try:
        await asyncio.to_thread(_link_identity, discord_id, canonical_ign, str(uuid))
        alliances = await asyncio.to_thread(enabled_alliances)
        profile = await asyncio.to_thread(getPlayerDatav3, str(uuid))
        alliance = community_alliance(alliances, declared_guild, profile, str(uuid))
        if alliance:
            if member is None:
                return 'Ally registration needs review: the applicant is not in the Discord server.'
            registered = await register_ally(member, canonical_ign, str(alliance.id),
                                             actor_name=actor_name, identity=(canonical_ign, str(uuid)))
            status = f'Registered as {registered.alliance.rank} for {registered.alliance.name}.'
            if not isinstance(profile, dict) or 'guild' not in profile:
                status += ' Used the application guild because the Wynncraft lookup was unavailable.'
            return status
        if member:
            await member.edit(nick=canonical_ign)
        return ''
    except LinkConflictError as error:
        return error.user_message()
    except AllyRegistrationError as error:
        return str(error)
    except discord.Forbidden:
        return 'Registration needs review: I could not update the nickname or guild role.'
    except discord.HTTPException:
        return 'Registration needs review: Discord rejected the member update.'
    except psycopg2.Error:
        log(ERROR, 'Community registration database update failed.', context='ally_registration')
        return 'Registration needs review: the database update failed.'
