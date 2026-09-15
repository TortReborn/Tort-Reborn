"""
Test suite for the DB connection pool hardening (Helpers/database.py).

The pool structure (keyed pools, use_pool opt-out, status-aware release) is
covered by tests/test_database_timing.py. This file covers the hardening
grafted onto it:

1. close() rolls back only when a transaction is open, and returns to the pool
2. A broken (closed) connection is discarded with putconn(close=True)
3. Sequential DB() uses check out and return through the same pool
4. connect() times the checkout in the db.connect bucket
5. A post-checkout setup failure discards the connection instead of leaking it
6. PoolError checkout retries (bounded), warns only from the second retry, then succeeds
7. Sustained exhaustion raises PoolError after the retries are spent
"""

import os
import sys
from unittest.mock import MagicMock, patch

import psycopg2.pool
import pytest
from psycopg2 import extensions

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import database, telemetry
from Helpers.database import DB


class FakeConn:
    def __init__(self, status=extensions.STATUS_READY):
        self.closed = 0
        self.status = status
        self.rolled_back = False

    def cursor(self):
        return MagicMock()

    def rollback(self):
        self.rolled_back = True


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    token = telemetry._current.set(None)
    DB._pools.clear()
    # connect() resolves env config before touching the pool; stub it so these
    # tests need no environment and open no sockets.
    monkeypatch.setattr(DB, "_connection_kwargs", staticmethod(lambda: ("true", {})))
    yield
    DB._pools.clear()
    telemetry._current.reset(token)


def _with_fake_pool(conn):
    fake = MagicMock()
    fake.getconn.return_value = conn
    return patch.object(DB, "_pool_for", MagicMock(return_value=fake)), fake


def test_close_rolls_back_open_transaction_and_returns():
    conn = FakeConn(status=extensions.STATUS_BEGIN)
    patcher, fake = _with_fake_pool(conn)
    with patcher:
        db = DB()
        db.connect()
        db.close()
    assert conn.rolled_back is True
    fake.putconn.assert_called_once_with(conn, close=False)


def test_close_skips_rollback_when_idle():
    conn = FakeConn(status=extensions.STATUS_READY)
    patcher, fake = _with_fake_pool(conn)
    with patcher:
        db = DB()
        db.connect()
        db.close()
    assert conn.rolled_back is False
    fake.putconn.assert_called_once_with(conn, close=False)


def test_broken_connection_is_discarded():
    conn = FakeConn()
    conn.closed = 1
    patcher, fake = _with_fake_pool(conn)
    with patcher:
        db = DB()
        db.connect()
        db.close()
    fake.putconn.assert_called_once_with(conn, close=True)


def test_sequential_uses_check_out_and_return():
    conn = FakeConn()
    patcher, fake = _with_fake_pool(conn)
    with patcher:
        for _ in range(2):
            db = DB()
            db.connect()
            db.close()
    assert fake.getconn.call_count == 2
    assert fake.putconn.call_count == 2


def test_checkout_is_timed_into_db_connect():
    conn = FakeConn()
    patcher, fake = _with_fake_pool(conn)
    telemetry.begin("test", None, None, None)
    with patcher:
        db = DB()
        db.connect()
    assert telemetry._current.get().buckets["db.connect"]["n"] == 1


def test_checkout_discarded_when_post_checkout_setup_fails():
    class BrokenCursorConn(FakeConn):
        def cursor(self):
            raise RuntimeError("cursor blew up")

    conn = BrokenCursorConn()
    patcher, fake = _with_fake_pool(conn)
    with patcher:
        db = DB()
        with pytest.raises(RuntimeError, match="cursor blew up"):
            db.connect()
    fake.putconn.assert_called_once_with(conn, close=True)
    assert db.connection is None and db.cursor is None


def test_pool_exhaustion_retries_then_succeeds():
    conn = FakeConn()
    fake = MagicMock()
    fake.getconn.side_effect = [
        psycopg2.pool.PoolError("exhausted"),
        psycopg2.pool.PoolError("exhausted"),
        conn,
    ]
    with patch.object(DB, "_pool_for", MagicMock(return_value=fake)), \
         patch.object(database, "log") as mock_log, \
         patch.object(database.time, "sleep") as mock_sleep:
        db = DB()
        db.connect()
    assert fake.getconn.call_count == 3
    mock_sleep.assert_any_call(0.2)
    mock_sleep.assert_any_call(0.4)
    warns = [c for c in mock_log.call_args_list if "pool exhausted" in str(c)]
    assert len(warns) == 1 and "attempt 2" in str(warns[0])


def test_pool_exhaustion_raises_after_retries_spent():
    fake = MagicMock()
    fake.getconn.side_effect = psycopg2.pool.PoolError("exhausted")
    with patch.object(DB, "_pool_for", MagicMock(return_value=fake)), \
         patch.object(database, "log"), \
         patch.object(database.time, "sleep"):
        db = DB()
        with pytest.raises(psycopg2.pool.PoolError):
            db.connect()
    assert fake.getconn.call_count == 3
