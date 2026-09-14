from dataclasses import dataclass

from Helpers.database import DB


TICKET_TYPES = {"war", "shell"}
TICKET_COUNTER_SEEDS = {
    "war": 180,
    "shell": 733,
}


@dataclass(frozen=True)
class TicketRecord:
    ticket_type: str
    ticket_number: int
    channel_id: int
    opener_discord_id: int
    status: str


def _counter_key(ticket_type: str) -> str:
    _validate_ticket_type(ticket_type)
    return f"ticket_{ticket_type}_counter"


def _validate_ticket_type(ticket_type: str):
    if ticket_type not in TICKET_TYPES:
        raise ValueError(f"unknown ticket type: {ticket_type}")


def ensure_ticket_storage(db: DB):
    db.cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
          key   TEXT PRIMARY KEY,
          value TEXT NOT NULL
        )
    """)
    db.cursor.execute("""
        CREATE TABLE IF NOT EXISTS support_tickets (
          id                SERIAL       PRIMARY KEY,
          ticket_type       VARCHAR(16)  NOT NULL CHECK (ticket_type IN ('war', 'shell')),
          ticket_number     INT          NOT NULL,
          channel_id        BIGINT       NOT NULL UNIQUE,
          opener_discord_id BIGINT       NOT NULL,
          opener_name       VARCHAR(100),
          status            VARCHAR(20)  NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
          created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
          closed_at         TIMESTAMPTZ,
          closed_by         BIGINT,
          close_reason      TEXT,
          UNIQUE (ticket_type, ticket_number)
        )
    """)
    db.cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_support_tickets_opener
          ON support_tickets(ticket_type, opener_discord_id, status)
    """)
    for ticket_type, seed in TICKET_COUNTER_SEEDS.items():
        db.cursor.execute(
            "INSERT INTO bot_settings (key, value) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (_counter_key(ticket_type), str(seed)),
        )


def get_next_ticket_number(ticket_type: str) -> int:
    db = DB()
    db.connect()
    try:
        ensure_ticket_storage(db)
        db.cursor.execute(
            "UPDATE bot_settings SET value = (value::int + 1)::text "
            "WHERE key = %s RETURNING value::int",
            (_counter_key(ticket_type),),
        )
        row = db.cursor.fetchone()
        db.connection.commit()
        if row is None:
            raise RuntimeError(f"ticket counter missing for {ticket_type}")
        return int(row[0])
    finally:
        db.close()


def get_ticket_counters() -> dict[str, int]:
    db = DB()
    db.connect()
    try:
        ensure_ticket_storage(db)
        db.cursor.execute(
            "SELECT key, value FROM bot_settings WHERE key IN (%s, %s)",
            (_counter_key("war"), _counter_key("shell")),
        )
        rows = db.cursor.fetchall()
        db.connection.commit()
        values = {key: int(value) for key, value in rows}
        return {
            "war": values.get(_counter_key("war"), TICKET_COUNTER_SEEDS["war"]),
            "shell": values.get(_counter_key("shell"), TICKET_COUNTER_SEEDS["shell"]),
        }
    finally:
        db.close()


def get_ticket_creator_ign(opener_discord_id: int) -> str | None:
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "SELECT ign FROM discord_links WHERE discord_id = %s LIMIT 1",
            (opener_discord_id,),
        )
        row = db.cursor.fetchone()
        return row[0] if row and row[0] else None
    finally:
        db.close()


def set_ticket_counter(ticket_type: str, current_number: int):
    db = DB()
    db.connect()
    try:
        ensure_ticket_storage(db)
        db.cursor.execute(
            "INSERT INTO bot_settings (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (_counter_key(ticket_type), str(int(current_number))),
        )
        db.connection.commit()
    finally:
        db.close()


def create_ticket_record(
    ticket_type: str,
    ticket_number: int,
    channel_id: int,
    opener_discord_id: int,
    opener_name: str,
):
    _validate_ticket_type(ticket_type)
    db = DB()
    db.connect()
    try:
        ensure_ticket_storage(db)
        db.cursor.execute(
            """
            INSERT INTO support_tickets
                (ticket_type, ticket_number, channel_id, opener_discord_id, opener_name)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (ticket_type, ticket_number, channel_id, opener_discord_id, opener_name[:100]),
        )
        db.connection.commit()
    finally:
        db.close()


def get_open_ticket_for_user(ticket_type: str, opener_discord_id: int) -> TicketRecord | None:
    _validate_ticket_type(ticket_type)
    db = DB()
    db.connect()
    try:
        ensure_ticket_storage(db)
        db.cursor.execute(
            """
            SELECT ticket_type, ticket_number, channel_id, opener_discord_id, status
            FROM support_tickets
            WHERE ticket_type = %s
              AND opener_discord_id = %s
              AND status = 'open'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (ticket_type, opener_discord_id),
        )
        row = db.cursor.fetchone()
        db.connection.commit()
        return TicketRecord(*row) if row else None
    finally:
        db.close()


def get_ticket_by_channel(channel_id: int) -> TicketRecord | None:
    db = DB()
    db.connect()
    try:
        ensure_ticket_storage(db)
        db.cursor.execute(
            """
            SELECT ticket_type, ticket_number, channel_id, opener_discord_id, status
            FROM support_tickets
            WHERE channel_id = %s
            LIMIT 1
            """,
            (channel_id,),
        )
        row = db.cursor.fetchone()
        db.connection.commit()
        return TicketRecord(*row) if row else None
    finally:
        db.close()


def close_ticket(channel_id: int, closed_by: int, reason: str | None = None) -> bool:
    db = DB()
    db.connect()
    try:
        ensure_ticket_storage(db)
        db.cursor.execute(
            """
            UPDATE support_tickets
               SET status = 'closed',
                   closed_at = NOW(),
                   closed_by = %s,
                   close_reason = %s
             WHERE channel_id = %s
               AND status = 'open'
            """,
            (closed_by, reason, channel_id),
        )
        changed = db.cursor.rowcount > 0
        db.connection.commit()
        return changed
    finally:
        db.close()
