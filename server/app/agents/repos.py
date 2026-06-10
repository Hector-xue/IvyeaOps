"""Database repositories for projects and sessions.

Direct port of claudecodeui's ``modules/database/repositories/projects.db.ts``
and ``sessions.db.ts`` plus the path/display-name helpers from
``shared/utils.ts``. Every function takes an open sqlite3 connection so a single
request can batch its queries on one connection (the router opens it via
``db.db_conn()``).
"""
from __future__ import annotations

import json
import posixpath
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Optional


# --- path / display-name helpers (shared/utils.ts) --------------------------

def normalize_project_path(input_path: Any) -> str:
    """Canonicalize a project path for stable DB keys. Backslashes are converted
    to forward slashes first so Windows paths (``C:\\Users\\x\\proj``) normalize
    consistently — otherwise posixpath keeps the backslashes and containment
    checks like ``path.startswith(root + "/")`` fail, which broke agent project
    creation on Windows ('Failed to create project'). On Linux this is a no-op."""
    if not isinstance(input_path, str):
        return ""
    trimmed = input_path.strip().replace("\\", "/")
    if not trimmed:
        return ""
    norm = posixpath.normpath(trimmed)
    if norm == "/":
        return "/"
    return norm.rstrip("/") or "/"


def generate_display_name(project_name: str, actual_project_dir: Optional[str] = None) -> str:
    """package.json `name` if present, else the last path segment."""
    project_path = actual_project_dir or project_name.replace("-", "/")
    try:
        pkg = json.loads((Path(project_path) / "package.json").read_text(encoding="utf-8"))
        name = pkg.get("name")
        if isinstance(name, str) and name:
            return name
    except Exception:
        pass
    if project_path.startswith("/"):
        parts = [p for p in project_path.split("/") if p]
        return parts[-1] if parts else project_path
    return project_path


# --- projects repository (projects.db.ts) -----------------------------------

_PROJECT_COLS = "project_id, project_path, custom_project_name, isStarred, isArchived"


def get_project_by_id(conn: sqlite3.Connection, project_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        f"SELECT {_PROJECT_COLS} FROM projects WHERE project_id = ?", (project_id,)
    ).fetchone()


def get_project_by_path(conn: sqlite3.Connection, project_path: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        f"SELECT {_PROJECT_COLS} FROM projects WHERE project_path = ?",
        (normalize_project_path(project_path),),
    ).fetchone()


def get_project_paths(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        f"SELECT {_PROJECT_COLS} FROM projects WHERE isArchived = 0"
    ).fetchall()


def get_archived_project_paths(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        f"SELECT {_PROJECT_COLS} FROM projects WHERE isArchived = 1"
    ).fetchall()


def create_project_path(
    conn: sqlite3.Connection, project_path: str, custom_project_name: Optional[str] = None
) -> dict:
    """INSERT ... ON CONFLICT(project_path) reactivate-if-archived, returning the
    row. Mirrors the TS createProjectPath outcome semantics."""
    # Note: no RETURNING (bundled SQLite is 3.34, RETURNING needs 3.35), so we
    # branch on the existing row instead — same outcome semantics as the TS upsert.
    normalized = normalize_project_path(project_path)
    name = (custom_project_name or "").strip() or (posixpath.basename(normalized) or normalized)
    existing = get_project_by_path(conn, normalized)
    if existing is None:
        conn.execute(
            "INSERT INTO projects (project_id, project_path, custom_project_name, isArchived)"
            " VALUES (?, ?, ?, 0)", (str(uuid.uuid4()), normalized, name))
        return {"outcome": "created", "project": get_project_by_path(conn, normalized)}
    if existing["isArchived"]:
        conn.execute("UPDATE projects SET isArchived = 0 WHERE project_path = ?", (normalized,))
        return {"outcome": "reactivated_archived", "project": get_project_by_path(conn, normalized)}
    return {"outcome": "active_conflict", "project": existing}


def update_custom_project_name_by_id(
    conn: sqlite3.Connection, project_id: str, custom_project_name: Optional[str]
) -> None:
    conn.execute(
        "UPDATE projects SET custom_project_name = ? WHERE project_id = ?",
        (custom_project_name, project_id),
    )


def update_project_is_starred_by_id(conn: sqlite3.Connection, project_id: str, is_starred: bool) -> None:
    conn.execute(
        "UPDATE projects SET isStarred = ? WHERE project_id = ?",
        (1 if is_starred else 0, project_id),
    )


def update_project_is_archived_by_id(conn: sqlite3.Connection, project_id: str, is_archived: bool) -> None:
    conn.execute(
        "UPDATE projects SET isArchived = ? WHERE project_id = ?",
        (1 if is_archived else 0, project_id),
    )


def delete_project_by_id(conn: sqlite3.Connection, project_id: str) -> None:
    conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))


# --- sessions repository (sessions.db.ts) -----------------------------------

_SESSION_COLS = (
    "session_id, provider, project_path, jsonl_path, custom_name, "
    "isArchived, created_at, updated_at"
)


def get_session_by_id(conn: sqlite3.Connection, session_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        f"""SELECT {_SESSION_COLS} FROM sessions
            WHERE session_id = ? ORDER BY updated_at DESC LIMIT 1""",
        (session_id,),
    ).fetchone()


def get_all_sessions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        f"SELECT {_SESSION_COLS} FROM sessions WHERE isArchived = 0"
    ).fetchall()


def get_archived_sessions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        f"""SELECT {_SESSION_COLS} FROM sessions WHERE isArchived = 1
            ORDER BY datetime(COALESCE(updated_at, created_at)) DESC, session_id DESC"""
    ).fetchall()


def get_sessions_by_project_path_including_archived(
    conn: sqlite3.Connection, project_path: str
) -> list[sqlite3.Row]:
    return conn.execute(
        f"SELECT {_SESSION_COLS} FROM sessions WHERE project_path = ?",
        (normalize_project_path(project_path),),
    ).fetchall()


def get_sessions_by_project_path_page(
    conn: sqlite3.Connection, project_path: str, limit: int, offset: int
) -> list[sqlite3.Row]:
    return conn.execute(
        f"""SELECT {_SESSION_COLS} FROM sessions
            WHERE project_path = ? AND isArchived = 0
            ORDER BY datetime(COALESCE(updated_at, created_at)) DESC, session_id DESC
            LIMIT ? OFFSET ?""",
        (normalize_project_path(project_path), limit, offset),
    ).fetchall()


def count_sessions_by_project_path(conn: sqlite3.Connection, project_path: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM sessions WHERE project_path = ? AND isArchived = 0",
        (normalize_project_path(project_path),),
    ).fetchone()
    return int(row["c"] if row else 0)


def delete_sessions_by_project_path(conn: sqlite3.Connection, project_path: str) -> None:
    conn.execute(
        "DELETE FROM sessions WHERE project_path = ?",
        (normalize_project_path(project_path),),
    )


def create_session(conn: sqlite3.Connection, session_id: str, provider: str, project_path: str,
                   custom_name: Optional[str] = None, created_at: Optional[str] = None,
                   updated_at: Optional[str] = None, jsonl_path: Optional[str] = None) -> str:
    """Upsert a session row, ensuring its project row exists first (FK).
    Port of sessions.db.ts createSession."""
    normalized = normalize_project_path(project_path)
    create_project_path(conn, normalized)
    conn.execute(
        """INSERT INTO sessions (session_id, provider, custom_name, project_path, jsonl_path,
                                 isArchived, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 0, COALESCE(?, CURRENT_TIMESTAMP), COALESCE(?, CURRENT_TIMESTAMP))
           ON CONFLICT(session_id) DO UPDATE SET
             provider = excluded.provider,
             updated_at = excluded.updated_at,
             project_path = excluded.project_path,
             jsonl_path = excluded.jsonl_path,
             isArchived = 0,
             custom_name = COALESCE(excluded.custom_name, sessions.custom_name)""",
        (session_id, provider, custom_name, normalized, jsonl_path, created_at, updated_at))
    return session_id


def update_session_custom_name(conn: sqlite3.Connection, session_id: str, custom_name: str) -> None:
    conn.execute(
        "UPDATE sessions SET custom_name = ? WHERE session_id = ?", (custom_name, session_id)
    )


def update_session_is_archived(conn: sqlite3.Connection, session_id: str, is_archived: bool) -> None:
    conn.execute(
        "UPDATE sessions SET isArchived = ? WHERE session_id = ?",
        (1 if is_archived else 0, session_id),
    )


def delete_session_by_id(conn: sqlite3.Connection, session_id: str) -> bool:
    return conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,)).rowcount > 0
