"""Lightweight, dependency-free SQLite schema migrations keyed on
``PRAGMA user_version``.

Why
---
The codebase historically evolved schema with ``CREATE TABLE IF NOT EXISTS`` plus
ad-hoc "add the column if it's missing" blocks. That works for additive changes
but has no version tracking and no safe story for *breaking* changes (renaming a
column, changing a type, splitting a table) — a botched one can corrupt or lose
a user's data on upgrade.

How
---
A DB adopts this by calling :func:`apply_migrations` right after ensuring its
baseline tables exist::

    from app.core.db_migrations import apply_migrations

    _MIGRATIONS = (
        lambda c: c.execute("ALTER TABLE sessions ADD COLUMN pinned INTEGER DEFAULT 0"),
        # ...append future migrations; never reorder or delete an applied one.
    )

    def init_db():
        with db_conn() as conn:
            for ddl in _BASELINE_SCHEMA:
                conn.execute(ddl)              # version 0 = current shipped schema
            apply_migrations(conn, _MIGRATIONS)

Each migration is a ``callable(conn) -> None`` run exactly once, in order. The
DB file's ``user_version`` records how many have applied, so every install
converges to the same schema regardless of which version it started from.

``user_version`` is per-database-file, so each SQLite file tracks its own
sequence independently.
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Sequence

Migration = Callable[[sqlite3.Connection], None]


def apply_migrations(conn: sqlite3.Connection, migrations: Sequence[Migration]) -> int:
    """Run any not-yet-applied migrations in order, bumping ``user_version`` after
    each. Returns the resulting schema version. Idempotent: calling it again
    after all migrations have applied is a no-op.

    Runs inside the caller's transaction so a migration + its version bump commit
    (or roll back) atomically — a half-applied migration won't be recorded.
    """
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    target = len(migrations)
    for version in range(current, target):
        migrations[version](conn)          # upgrades user_version `version` -> `version+1`
        # PRAGMA can't be parameterized; version+1 is an int from range() — no injection.
        conn.execute(f"PRAGMA user_version = {version + 1}")
    return max(current, target)
