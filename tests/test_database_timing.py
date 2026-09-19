"""
Test suite for DB timing proxies (Helpers/database.py).

Tests:
1. execute() is timed into db.query
2. Non-timed cursor methods delegate unchanged
3. commit() is timed into db.commit
4. Non-timed connection methods delegate unchanged
5. A raising execute() still records and re-raises
"""

import os
import sys
from typing import ClassVar

import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers import telemetry
from Helpers.database import DB, _TimedConnection, _TimedCursor


@pytest.fixture(autouse=True)
def _reset_context():
    token = telemetry._current.set(None)
    yield
    telemetry._current.reset(token)


class FakeCursor:
    def __init__(self):
        self.executed = []
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return ("row",)

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self):
        self.committed = False
        self.closed = False
        self.status = 1

    def commit(self):
        self.committed = True

    def cursor(self):
        return FakeCursor()

    def close(self):
        self.closed = True


class FakePool:
    created: ClassVar[list] = []

    def __init__(self, minconn, maxconn, **kwargs):
        self.minconn = minconn
        self.maxconn = maxconn
        self.kwargs = kwargs
        self.connection = FakeConnection()
        self.returned = []
        FakePool.created.append(self)

    def getconn(self):
        return self.connection

    def putconn(self, connection, close=False):
        self.returned.append((connection, close))


def test_execute_is_timed_into_db_query():
    telemetry.begin("test", None, None, None)
    cursor = _TimedCursor(FakeCursor())
    cursor.execute("SELECT 1", None)
    assert telemetry._current.get().buckets["db.query"]["n"] == 1


def test_cursor_delegates_untimed_methods():
    inner = FakeCursor()
    cursor = _TimedCursor(inner)
    assert cursor.fetchone() == ("row",)
    cursor.close()
    assert inner.closed is True


def test_commit_is_timed_into_db_commit():
    telemetry.begin("test", None, None, None)
    inner = FakeConnection()
    connection = _TimedConnection(inner)
    connection.commit()
    assert inner.committed is True
    assert telemetry._current.get().buckets["db.commit"]["n"] == 1


def test_connection_delegates_untimed_methods():
    inner = FakeConnection()
    connection = _TimedConnection(inner)
    connection.close()
    assert inner.closed is True


def test_failing_execute_records_and_reraises():
    class Boom:
        def execute(self, *args, **kwargs):
            raise RuntimeError("bad sql")

    telemetry.begin("test", None, None, None)
    cursor = _TimedCursor(Boom())
    with pytest.raises(RuntimeError, match="bad sql"):
        cursor.execute("SELECT 1")
    assert telemetry._current.get().buckets["db.query"]["n"] == 1


@pytest.fixture
def _db_env(monkeypatch):
    monkeypatch.setenv("DB_LOGIN", "user")
    monkeypatch.setenv("DB_PASS", "pass")
    monkeypatch.setenv("DB_HOST", "localhost")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_DATABASE", "postgres")
    monkeypatch.setenv("DB_SSLMODE", "disable")
    DB._pools.clear()
    FakePool.created.clear()
    yield
    DB._pools.clear()
    FakePool.created.clear()


def test_db_can_opt_out_of_pooling(monkeypatch, _db_env):
    created = []

    def fake_connect(**kwargs):
        connection = FakeConnection()
        created.append((connection, kwargs))
        return connection

    monkeypatch.setattr("Helpers.database.psycopg2.connect", fake_connect)
    monkeypatch.setattr("Helpers.database.psycopg2.pool.ThreadedConnectionPool", FakePool)

    db = DB(use_pool=False)
    db.connect()
    db.close()

    assert len(created) == 1
    assert FakePool.created == []
    assert created[0][0].closed is True


def test_db_pooling_is_used_by_default(monkeypatch, _db_env):
    monkeypatch.setattr(
        "Helpers.database.psycopg2.connect",
        lambda **kwargs: pytest.fail("fresh connect should not be used"),
    )
    monkeypatch.setattr("Helpers.database.psycopg2.pool.ThreadedConnectionPool", FakePool)

    first = DB()
    first.connect()
    first.close()
    second = DB()
    second.connect()
    second.close()

    assert len(FakePool.created) == 1
    pool = FakePool.created[0]
    assert pool.minconn == 1
    assert pool.maxconn == 10
    assert len(pool.returned) == 2
    assert all(close is False for _, close in pool.returned)
