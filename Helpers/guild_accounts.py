"""Guild-owned in-game accounts (TAQ-88).

Woealer holds the ingredient stock, GordLonner holds the guild's LE. They
are on guild_roster because they are in the guild, and unlinked because
there is no person behind them. Everything that judges *members* --
/rankcheck's linkage report, the leave-message buttons, inactivity views --
must skip them. The record is management_exceptions with
exception_type = 'guild_account' and minecraft_uuid set (the website's
Externals page owns the table).
"""

GUILD_ACCOUNT_TYPE = 'guild_account'


def uuid_key(value):
    if not value:
        return None
    return str(value).replace('-', '').lower()


def guild_account_uuids(cursor):
    """Dashless uuids of every guild-owned account. Blocking."""
    cursor.execute(
        "SELECT minecraft_uuid::text FROM management_exceptions"
        " WHERE exception_type = %s AND minecraft_uuid IS NOT NULL",
        (GUILD_ACCOUNT_TYPE,),
    )
    return {uuid_key(row[0]) for row in cursor.fetchall()}


def is_guild_account(cursor, uuid):
    return uuid_key(uuid) in guild_account_uuids(cursor)
