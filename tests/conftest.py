"""Shared fixtures.

``temp_db`` gives a test the real dev database (DB_* in .env, which is
always the local mirror — prod credentials never live in a checkout) with
session-scoped TEMP tables shadowing the tables the linking overhaul touches
(TAQ-76). Unqualified names resolve to pg_temp first, so the code under test
runs its real SQL against real schema shapes -- constraints, partial unique
indexes, ON CONFLICT clauses -- and nothing in the dev database is written.
Tests that need it are skipped when the database is unreachable, so the pure
tests still run anywhere.

The fixture also returns a ``DB``-shaped stand-in (``connect``/``close``/
``connection.commit`` are no-ops on the shared connection) to monkeypatch
into modules that do ``db = DB(); db.connect()`` themselves.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SHADOWED_TABLES = [
    "rank_definitions",
    "discord_links",
    "guild_roster",
    "membership_stints",
    "member_honorifics",
    "member_leave_prompts",
    "applications",
    "promotion_queue",
    "audit_log",
    "shells",
    "management_exceptions",
]


def _dev_connection():
    try:
        import psycopg2
        from dotenv import load_dotenv
    except ImportError:
        return None
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    host = os.getenv("DB_HOST")
    if not host or host not in ("127.0.0.1", "localhost", "::1"):
        return None  # DB-backed tests only ever run against a local database
    try:
        return psycopg2.connect(
            user=os.getenv("DB_LOGIN"),
            password=os.getenv("DB_PASS"),
            host=host,
            port=int(os.getenv("DB_PORT") or 5432),
            database=os.getenv("DB_DATABASE"),
            sslmode=os.getenv("DB_SSLMODE") or None,
            connect_timeout=5,
        )
    except Exception:
        return None


class SharedDB:
    """Quacks like Helpers.database.DB but reuses one live connection."""

    def __init__(self, connection):
        self._connection = connection
        self.cursor = connection.cursor()
        self.connection = self  # code calls db.connection.commit()

    def connect(self):
        pass

    def close(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass


@pytest.fixture(scope="session")
def _dev_db():
    conn = _dev_connection()
    if conn is None:
        pytest.skip("local dev database unreachable (DB_* in .env must point at loopback)")
    conn.autocommit = True
    cur = conn.cursor()
    # rank_definitions is referenced by discord_links' FK on the real table;
    # the temp copy carries no FK, so the seed here is only for readability.
    for table in SHADOWED_TABLES:
        cur.execute(f"CREATE TEMP TABLE {table} (LIKE public.{table} INCLUDING ALL)")
    cur.execute(
        "INSERT INTO rank_definitions SELECT * FROM public.rank_definitions"
    )
    yield SharedDB(conn)
    conn.close()


@pytest.fixture
def temp_db(_dev_db):
    cur = _dev_db.cursor
    cur.execute("TRUNCATE " + ", ".join(t for t in SHADOWED_TABLES if t != "rank_definitions"))
    return _dev_db
