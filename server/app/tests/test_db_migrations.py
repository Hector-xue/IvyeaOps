"""Tests for the lightweight user_version-based migration runner."""
from __future__ import annotations

import sqlite3

from app.core.db_migrations import apply_migrations


def test_migrations_run_once_in_order_and_track_version():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    ran: list[str] = []
    migs = (
        lambda c: (c.execute("ALTER TABLE t ADD COLUMN a TEXT"), ran.append("m1")),
        lambda c: (c.execute("ALTER TABLE t ADD COLUMN b TEXT"), ran.append("m2")),
    )
    assert apply_migrations(conn, migs) == 2
    assert ran == ["m1", "m2"]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    cols = {r[1] for r in conn.execute("PRAGMA table_info(t)")}
    assert {"a", "b"} <= cols


def test_migrations_idempotent_and_append_only():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    ran: list[str] = []
    migs = (lambda c: ran.append("m1"), lambda c: ran.append("m2"))
    apply_migrations(conn, migs)
    apply_migrations(conn, migs)          # re-run: no-op
    assert ran == ["m1", "m2"]
    # Appending a migration runs only the new one.
    migs3 = migs + (lambda c: ran.append("m3"),)
    apply_migrations(conn, migs3)
    assert ran == ["m1", "m2", "m3"]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_empty_migrations_is_noop():
    conn = sqlite3.connect(":memory:")
    assert apply_migrations(conn, ()) == 0
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
