"""SQLite layer for the native Agents backend.

We reuse the same database file the old Node service wrote to
(``~/.agents/auth.db`` by default, overridable via ``AGENTS_DB_PATH``) so all
existing projects/sessions metadata carries over at cutover with zero
migration. The schema below mirrors ``claudecodeui/server/modules/database/
schema.ts`` for the tables this backend owns; ``CREATE TABLE IF NOT EXISTS``
keeps it a no-op against the live DB while still bootstrapping a fresh deploy.

The metadata tables are only an *index* over Claude's native transcripts at
``~/.claude/projects/<encoded-path>/<session_id>.jsonl`` — message history is
read from those JSONL files, not stored here.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# --- Schema (mirrors claudecodeui modules/database/schema.ts) ---------------

_PROJECTS_TABLE = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY NOT NULL,
    project_path TEXT NOT NULL UNIQUE,
    custom_project_name TEXT DEFAULT NULL,
    isStarred BOOLEAN DEFAULT 0,
    isArchived BOOLEAN DEFAULT 0
);
"""

_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'claude',
    custom_name TEXT,
    project_path TEXT,
    jsonl_path TEXT,
    isArchived BOOLEAN DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id),
    FOREIGN KEY (project_path) REFERENCES projects(project_path)
    ON DELETE SET NULL
    ON UPDATE CASCADE
);
"""

_SCAN_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS scan_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  last_scanned_at TIMESTAMP NULL
);
"""

_APP_CONFIG_TABLE = """
CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

_SCHEMA = (_PROJECTS_TABLE, _SESSIONS_TABLE, _SCAN_STATE_TABLE, _APP_CONFIG_TABLE)


def get_db_path() -> Path:
    """Resolve the SQLite file path. Defaults to the live Node DB so data
    carries over; override with ``AGENTS_DB_PATH`` for tests/fresh installs."""
    override = os.getenv("AGENTS_DB_PATH")
    if override:
        return Path(override)
    return Path.home() / ".agents" / "auth.db"


def connect() -> sqlite3.Connection:
    """Open a connection with row access by name and FK enforcement on."""
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def db_conn() -> Iterator[sqlite3.Connection]:
    """Connection context manager that commits on success, rolls back on error."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Versioned, append-only migrations for this DB. The baseline schema (_SCHEMA
# above, created with CREATE TABLE IF NOT EXISTS) is "version 0"; ship future
# *breaking* changes here instead of ad-hoc ALTERs, e.g.:
#   _MIGRATIONS = (lambda c: c.execute("ALTER TABLE sessions ADD COLUMN pinned INTEGER DEFAULT 0"),)
# Never reorder or delete an already-shipped migration. Other DBs (brain_chat,
# terminal_live, agent_session, listing, users...) can adopt the same pattern.
_MIGRATIONS: tuple = ()


def init_db() -> None:
    """Idempotently ensure the tables this backend owns exist + run any pending
    versioned migrations. Safe to call on every startup."""
    from app.core.db_migrations import apply_migrations
    with db_conn() as conn:
        for ddl in _SCHEMA:
            conn.execute(ddl)
        apply_migrations(conn, _MIGRATIONS)
