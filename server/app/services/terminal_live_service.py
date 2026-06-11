"""Persistent server-terminal sessions + history for IvyeaOps.

Separate from the old tmux snapshot table in routers/terminal.py.
This DB stores one row per terminal session plus an append-only history log
of user input / shell output / system events so the UI can reopen old
sessions and inspect prior commands.
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from app.core.config import settings

DB_PATH = Path(
    os.environ.get(
        "IVYEA_OPS_TERMINAL_LIVE_DB",
        str(settings.data_dir / "terminal_live.sqlite3"),
    )
)

_write_lock = Lock()

VALID_STREAMS = {
    "input", "output", "system",
    # Legacy single-stream snapshot; kept for back-compat reads but no
    # longer written. New writes use the 3-slot rolling stream values:
    "snapshot",
    # 3-slot rolling snapshot:
    #   snap_curr     = most recent capture
    #   snap_prev     = the one before (used to diff against curr)
    #   snap_before   = everything older, merged into one growing blob
    "snap_curr", "snap_prev", "snap_before",
}

# Cap for the merged "before" slot — large enough to hold many hours of
# busy AI sessions but bounded so the DB doesn't grow unbounded per session.
SNAP_BEFORE_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
SNAP_ROLES = ("snap_before", "snap_prev", "snap_curr")
VALID_STATUS = {"idle", "live", "closed", "archived"}


class TerminalSessionError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sid() -> str:
    return uuid.uuid4().hex


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


# Versioned migrations for this DB (see app/core/db_migrations). The baseline
# schema below is "version 0"; append future breaking changes here.
_MIGRATIONS: tuple = ()


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS terminal_sessions (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL DEFAULT 'root',
              title TEXT NOT NULL,
              shell TEXT NOT NULL DEFAULT '/bin/bash',
              workdir TEXT,
              status TEXT NOT NULL DEFAULT 'idle',
              last_preview TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              archived INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_terminal_sessions_user_updated
              ON terminal_sessions(user_id, archived, updated_at DESC);

            CREATE TABLE IF NOT EXISTS terminal_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL,
              seq INTEGER NOT NULL,
              stream TEXT NOT NULL,
              content TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(session_id) REFERENCES terminal_sessions(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_terminal_history_session_seq
              ON terminal_history(session_id, seq);
            """
        )
        from app.core.db_migrations import apply_migrations
        apply_migrations(conn, _MIGRATIONS)


def list_sessions(user_id: str = "root", include_archived: bool = False, limit: int = 200) -> list[dict[str, Any]]:
    sql = "SELECT * FROM terminal_sessions WHERE user_id = ?"
    params: list[Any] = [user_id]
    if not include_archived:
        sql += " AND archived = 0"
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["archived"] = bool(d["archived"])
        out.append(d)
    return out


def create_session(
    *,
    user_id: str,
    title: str | None = None,
    shell: str | None = None,
    workdir: str | None = None,
) -> dict[str, Any]:
    init_db()
    sid = _sid()
    ts = _now()
    safe_title = (title or f"终端 {datetime.now().strftime('%m-%d %H:%M')}").strip()[:120]
    safe_shell = (shell or "/bin/bash").strip() or "/bin/bash"
    safe_workdir = (workdir or str(Path.home())).strip() or str(Path.home())
    with _write_lock, _connect() as conn:
        conn.execute(
            """INSERT INTO terminal_sessions(id, user_id, title, shell, workdir, status, created_at, updated_at)
               VALUES(?, ?, ?, ?, ?, 'idle', ?, ?)""",
            (sid, user_id, safe_title, safe_shell, safe_workdir, ts, ts),
        )
    return get_session(sid)


def get_session(session_id: str) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM terminal_sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        raise TerminalSessionError("终端会话不存在")
    d = dict(row)
    d["archived"] = bool(d["archived"])
    return d


def update_session(
    session_id: str,
    *,
    title: str | None = None,
    archived: bool | None = None,
    status: str | None = None,
    last_preview: str | None = None,
    workdir: str | None = None,
) -> dict[str, Any]:
    sets: list[str] = []
    params: list[Any] = []
    if title is not None:
        sets.append("title = ?")
        params.append(title.strip()[:120] or "未命名终端")
    if archived is not None:
        sets.append("archived = ?")
        params.append(1 if archived else 0)
    if status is not None:
        if status not in VALID_STATUS:
            raise TerminalSessionError(f"非法状态: {status}")
        sets.append("status = ?")
        params.append(status)
    if last_preview is not None:
        sets.append("last_preview = ?")
        params.append(last_preview[:4000])
    if workdir is not None:
        sets.append("workdir = ?")
        params.append(workdir.strip() or str(Path.home()))
    if not sets:
        return get_session(session_id)
    sets.append("updated_at = ?")
    params.append(_now())
    params.append(session_id)
    with _write_lock, _connect() as conn:
        cur = conn.execute(f"UPDATE terminal_sessions SET {', '.join(sets)} WHERE id = ?", params)
        if cur.rowcount == 0:
            raise TerminalSessionError("终端会话不存在")
    return get_session(session_id)


def delete_session(session_id: str) -> None:
    """Delete a terminal session and ALL its history / snapshot rows.

    The history table doesn't have an enforced FK ON DELETE CASCADE (it
    pre-dates that decision), so we explicitly clear both in a single
    transaction.
    """
    with _write_lock, _connect() as conn:
        # Run as a single implicit transaction; the connection uses
        # isolation_level=None (autocommit) but executescript / multi-stmt
        # via execute are atomic within sqlite's WAL.
        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM terminal_history WHERE session_id = ?", (session_id,))
            cur = conn.execute("DELETE FROM terminal_sessions WHERE id = ?", (session_id,))
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                raise TerminalSessionError("终端会话不存在")
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise


def next_seq(session_id: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM terminal_history WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return int(row["m"] or 0) + 1


def add_history(
    session_id: str,
    *,
    stream: str,
    content: str,
    created_at: str | None = None,
) -> dict[str, Any] | None:
    if stream not in VALID_STREAMS:
        raise TerminalSessionError(f"非法 history stream: {stream}")
    if not content:
        return None
    ts = created_at or _now()
    with _write_lock, _connect() as conn:
        row = conn.execute("SELECT id FROM terminal_sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            raise TerminalSessionError("终端会话不存在")
        seq_row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM terminal_history WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        seq = int(seq_row["m"] or 0) + 1
        conn.execute(
            """INSERT INTO terminal_history(session_id, seq, stream, content, created_at)
               VALUES(?, ?, ?, ?, ?)""",
            (session_id, seq, stream, content, ts),
        )
        preview = content.strip().splitlines()[-1][:300] if content.strip() else ""
        conn.execute(
            "UPDATE terminal_sessions SET updated_at = ?, last_preview = COALESCE(?, last_preview) WHERE id = ?",
            (ts, preview or None, session_id),
        )
    return {"session_id": session_id, "seq": seq, "stream": stream, "content": content, "created_at": ts}


# ─── Snapshots: 3-slot rolling window ───────────────────────────────────────
# We keep at most three snapshots per session, regardless of how often
# capture fires:
#   snap_curr   – the latest capture
#   snap_prev   – the one before (so users can compare "what changed since")
#   snap_before – everything older, merged into a single growing blob with
#                 timestamp dividers. Capped at SNAP_BEFORE_MAX_BYTES.
# When a new capture lands, the old snap_prev gets folded into snap_before,
# the old snap_curr becomes the new snap_prev, and the new content becomes
# snap_curr. This gives users a stable, scannable 3-row list instead of a
# tower of similar mini-captures.


def _read_slot(conn: sqlite3.Connection, session_id: str, role: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT id, seq, created_at, content
             FROM terminal_history
            WHERE session_id = ? AND stream = ?
            ORDER BY id DESC LIMIT 1""",
        (session_id, role),
    ).fetchone()
    return dict(row) if row else None


def _slot_label_cn(role: str) -> str:
    return {"snap_curr": "当前", "snap_prev": "上一个", "snap_before": "之前"}.get(role, role)


def list_snapshots(session_id: str, limit: int = 80, offset: int = 0) -> dict[str, Any]:
    """Return the 3 rolling-window snapshot rows for this session, plus
    `total` (count of rows actually present, 0-3). The `limit`/`offset`
    args are kept for API compatibility but ignored — there are at most 3.
    """
    out: list[dict[str, Any]] = []
    with _connect() as conn:
        # Display order: 当前 → 上一个 → 之前 (newest first)
        for role in ("snap_curr", "snap_prev", "snap_before"):
            row = _read_slot(conn, session_id, role)
            if row:
                out.append({
                    "id": row["id"],
                    "seq": row["seq"],
                    "ts": row["created_at"],
                    "size": len(row["content"]) if row["content"] else 0,
                    "role": role,
                    "label": _slot_label_cn(role),
                })
    return {"snapshots": out, "total": len(out)}


def get_snapshot(session_id: str, snap_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """SELECT id, seq, created_at AS ts, content, LENGTH(content) AS size, stream AS role
                 FROM terminal_history
                WHERE session_id = ? AND id = ? AND stream IN ('snap_curr','snap_prev','snap_before','snapshot')""",
            (session_id, snap_id),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def clear_snapshots(session_id: str) -> int:
    """Wipe all rolling snapshots for a session (button: 清空全部)."""
    with _write_lock, _connect() as conn:
        cur = conn.execute(
            """DELETE FROM terminal_history
                WHERE session_id = ? AND stream IN ('snap_curr','snap_prev','snap_before','snapshot')""",
            (session_id,),
        )
    return cur.rowcount


def rotate_snapshot(session_id: str, content: str) -> dict[str, Any] | None:
    """Add a new capture to the 3-slot rolling window.

    Returns the new ``snap_curr`` row, or None if input is empty / session
    does not exist. Caller does dedup against the previous content (via
    last_snapshot_hash) — we don't re-dedup here.
    """
    if not content:
        return None
    ts = _now()
    with _write_lock, _connect() as conn:
        if not conn.execute("SELECT 1 FROM terminal_sessions WHERE id = ?", (session_id,)).fetchone():
            return None

        conn.execute("BEGIN")
        try:
            curr = _read_slot(conn, session_id, "snap_curr")
            prev = _read_slot(conn, session_id, "snap_prev")
            before = _read_slot(conn, session_id, "snap_before")

            # 1. Fold old prev → before (if prev exists)
            if prev is not None:
                divider = f"\n\n──────── 快照 @ {prev['created_at']} ────────\n\n"
                new_before_content = (before["content"] if before else "") + divider + prev["content"]
                # Cap from the head: keep the most recent bytes.
                if len(new_before_content) > SNAP_BEFORE_MAX_BYTES:
                    new_before_content = "…（更早内容已超出 5MB 限制，已自动裁剪）…\n" + new_before_content[-SNAP_BEFORE_MAX_BYTES:]
                if before is not None:
                    conn.execute(
                        "UPDATE terminal_history SET content = ?, created_at = ? WHERE id = ?",
                        (new_before_content, ts, before["id"]),
                    )
                else:
                    # Reuse the next-seq logic so seq is monotonic per session
                    seq_row = conn.execute(
                        "SELECT COALESCE(MAX(seq),0) AS m FROM terminal_history WHERE session_id = ?",
                        (session_id,),
                    ).fetchone()
                    conn.execute(
                        """INSERT INTO terminal_history(session_id, seq, stream, content, created_at)
                                 VALUES(?, ?, 'snap_before', ?, ?)""",
                        (session_id, int(seq_row["m"] or 0) + 1, new_before_content, ts),
                    )
                # Remove the old prev row.
                conn.execute("DELETE FROM terminal_history WHERE id = ?", (prev["id"],))

            # 2. Old curr → prev (just rename the stream)
            if curr is not None:
                conn.execute(
                    "UPDATE terminal_history SET stream = 'snap_prev' WHERE id = ?",
                    (curr["id"],),
                )

            # 3. New content → curr
            seq_row = conn.execute(
                "SELECT COALESCE(MAX(seq),0) AS m FROM terminal_history WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            new_seq = int(seq_row["m"] or 0) + 1
            cur = conn.execute(
                """INSERT INTO terminal_history(session_id, seq, stream, content, created_at)
                         VALUES(?, ?, 'snap_curr', ?, ?)""",
                (session_id, new_seq, content, ts),
            )
            new_id = cur.lastrowid

            # 4. Bump session's updated_at for sidebar ordering.
            preview = content.strip().splitlines()[-1][:300] if content.strip() else ""
            conn.execute(
                "UPDATE terminal_sessions SET updated_at = ?, last_preview = COALESCE(?, last_preview) WHERE id = ?",
                (ts, preview or None, session_id),
            )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

        return {"id": new_id, "seq": new_seq, "stream": "snap_curr", "content": content, "created_at": ts}


# ─── Back-compat shim ──────────────────────────────────────────────────────
# Old call sites used capture_snapshot; route through rotate_snapshot so
# nothing breaks during the transition.
def capture_snapshot(session_id: str, content: str) -> dict[str, Any] | None:
    return rotate_snapshot(session_id, content)


def delete_snapshot(session_id: str, snap_id: int) -> bool:
    """Legacy single-row delete. Prefer clear_snapshots() in new code."""
    with _write_lock, _connect() as conn:
        cur = conn.execute(
            """DELETE FROM terminal_history
                WHERE session_id = ? AND id = ?
                  AND stream IN ('snap_curr','snap_prev','snap_before','snapshot')""",
            (session_id, snap_id),
        )
    return cur.rowcount > 0


def update_last_output(session_id: str, content: str) -> bool:
    """Update the content of the most recent output record for a session (for incremental typing dedup)."""
    if not content:
        return False
    with _write_lock, _connect() as conn:
        row = conn.execute(
            """SELECT id FROM terminal_history
               WHERE session_id = ? AND stream = 'output'
               ORDER BY seq DESC LIMIT 1""",
            (session_id,),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE terminal_history SET content = ? WHERE id = ?",
            (content, row["id"]),
        )
    return True


def list_history(
    session_id: str,
    after_seq: int = 0,
    limit: int = 500,
    before_seq: int | None = None,
) -> list[dict[str, Any]]:
    with _connect() as conn:
        if before_seq and before_seq > 0:
            rows = conn.execute(
                """SELECT id, session_id, seq, stream, content, created_at
                     FROM terminal_history
                    WHERE session_id = ? AND seq < ?
                    ORDER BY seq DESC
                    LIMIT ?""",
                (session_id, before_seq, limit),
            ).fetchall()
            rows = list(reversed(rows))
        elif after_seq > 0:
            rows = conn.execute(
                """SELECT id, session_id, seq, stream, content, created_at
                     FROM terminal_history
                    WHERE session_id = ? AND seq > ?
                    ORDER BY seq ASC
                    LIMIT ?""",
                (session_id, after_seq, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, session_id, seq, stream, content, created_at
                     FROM terminal_history
                    WHERE session_id = ?
                    ORDER BY seq DESC
                    LIMIT ?""",
                (session_id, limit),
            ).fetchall()
            rows = list(reversed(rows))
    return [dict(r) for r in rows]


def session_text(session_id: str, limit: int = 5000) -> str:
    rows = list_history(session_id, limit=limit)
    return "".join(r["content"] for r in rows)


def count_history(session_id: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM terminal_history WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return int(row["c"] or 0)
