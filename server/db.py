"""SQLite access helpers.

Every statement in Velora uses bound parameters (``?`` placeholders). There is no
string-formatted SQL anywhere in the codebase, which removes SQL injection as a
class of bug.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(ISO_FORMAT)


def now_iso() -> str:
    return to_iso(utcnow())


def future_iso(seconds: int | float) -> str:
    return to_iso(utcnow() + timedelta(seconds=seconds))


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, ISO_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def is_past(value: str | None) -> bool:
    parsed = parse_iso(value)
    if parsed is None:
        return True
    return parsed <= utcnow()


class Database:
    """Creates short-lived connections.

    SQLite connections are cheap; giving each request its own connection keeps
    the WSGI worker threads from sharing mutable statement state.
    """

    def __init__(self, path: str):
        self.path = str(path)
        self._write_lock = threading.RLock()
        self._local = threading.local()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 20000")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @contextmanager
    def connection(self):
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self, conn: sqlite3.Connection | None = None):
        """Explicit write transaction (BEGIN IMMEDIATE ... COMMIT/ROLLBACK).

        Nested calls reuse the outer transaction, so service functions can be
        composed without splitting a payment into two half-applied writes.
        """
        if conn is not None:
            if conn.in_transaction:
                # Already inside a write transaction on this connection: join it.
                yield conn
                return
            if getattr(self._local, "write_depth", 0):
                # Two connections writing in one thread would self-deadlock on
                # SQLite's write lock; surface it as a clear programming error.
                raise RuntimeError(
                    "nested write transaction on a second connection; pass the outer "
                    "connection to the nested call instead"
                )
            with self._write_lock:
                self._local.write_depth = 1
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                else:
                    conn.execute("COMMIT")
                finally:
                    self._local.write_depth = 0
            return

        with self.connection() as own_conn:
            with self.transaction(own_conn):
                yield own_conn


def q1(conn: sqlite3.Connection, sql: str, params=()):
    return conn.execute(sql, params).fetchone()


def qall(conn: sqlite3.Connection, sql: str, params=()):
    return conn.execute(sql, params).fetchall()


def execute(conn: sqlite3.Connection, sql: str, params=()):
    return conn.execute(sql, params)


def insert(conn: sqlite3.Connection, sql: str, params=()) -> int:
    return conn.execute(sql, params).lastrowid


def scalar(conn: sqlite3.Connection, sql: str, params=(), default=0):
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return default
    value = row[0]
    return default if value is None else value


def rows_to_dicts(rows) -> list[dict]:
    return [dict(row) for row in rows]
