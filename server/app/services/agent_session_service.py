"""Multi-agent chat/CLI session persistence.

Independent SQLite database (agent_sessions.sqlite3) decoupled from brain_chat,
because the multi-agent hub has different semantics (CLI bridge, branching,
summary checkpoints) and we want clean backup/migration boundaries.

Tables:
  agents               static metadata for agents we know about (also runtime
                       discovered by agent_registry; this table is the
                       persisted projection so the UI stays usable when an
                       agent binary disappears).
  agent_sessions       one row per chat/CLI session. Branches reference parent.
  agent_messages       per-session message log. role + kind for clean dispatch.
  agent_summaries      compressed checkpoints (token-saving, also used to
                       reconstruct context when waking a dormant session).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

from app.core.config import settings

DB_PATH = Path(
    os.environ.get(
        "IVYEA_OPS_AGENT_SESSIONS_DB",
        str(settings.data_dir / "agent_sessions.sqlite3"),
    )
)

# Roles are intentionally narrow; renderer dispatches on `kind`.
VALID_ROLES = {"user", "assistant", "system"}
VALID_KINDS = {"text", "tool_call", "tool_result", "summary", "cli_frame", "error"}
VALID_SOURCES = {"chat", "cli", "system"}

# Coarse lock — sqlite handles its own concurrency; this exists only to
# serialize the read-modify-write of session.updated_at + last_preview when a
# message lands. SQLite WAL covers the rest.
_write_lock = Lock()


class AgentSessionError(RuntimeError):
    """User-facing error."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ulid() -> str:
    """A short sortable id. We don't pull in a ULID dep — uuid4 hex is fine."""
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
# schema below (+ the legacy resume_target ALTER) is "version 0"; append future
# breaking changes here instead of ad-hoc ALTERs.
_MIGRATIONS: tuple = ()


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agents (
              id TEXT PRIMARY KEY,
              display_name TEXT NOT NULL,
              binary_path TEXT NOT NULL,
              default_model TEXT,
              models_json TEXT NOT NULL DEFAULT '[]',
              caps_json TEXT NOT NULL DEFAULT '{}',
              enabled INTEGER NOT NULL DEFAULT 1,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_sessions (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL DEFAULT 'root',
              parent_id TEXT,
              branch_anchor_seq INTEGER,
              agent_id TEXT NOT NULL,
              model TEXT,
              title TEXT NOT NULL,
              workdir TEXT,
              status TEXT NOT NULL DEFAULT 'idle',  -- idle | live | dormant | archived
              last_summary_id TEXT,
              last_preview TEXT NOT NULL DEFAULT '',
              token_estimate INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              archived INTEGER NOT NULL DEFAULT 0,
              -- For sessions started by clicking "继续会话" on an external
              -- (Claude/Codex) jsonl history. Format: "<source>:<external_sid>".
              -- Consumed once by pty_manager when first spawning the CLI;
              -- the actual conversation thereafter is the agent's own state.
              resume_target TEXT,
              FOREIGN KEY(parent_id) REFERENCES agent_sessions(id)
            );
            CREATE INDEX IF NOT EXISTS idx_agent_sessions_user_updated
              ON agent_sessions(user_id, archived, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_agent_sessions_parent
              ON agent_sessions(parent_id);

            CREATE TABLE IF NOT EXISTS agent_messages (
              id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL,
              seq INTEGER NOT NULL,
              role TEXT NOT NULL,
              kind TEXT NOT NULL DEFAULT 'text',
              source TEXT NOT NULL DEFAULT 'chat',
              content TEXT NOT NULL,
              meta_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              FOREIGN KEY(session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_agent_messages_session_seq
              ON agent_messages(session_id, seq);

            CREATE TABLE IF NOT EXISTS agent_summaries (
              id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL,
              upto_seq INTEGER NOT NULL,
              content TEXT NOT NULL,
              token_estimate INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              FOREIGN KEY(session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_agent_summaries_session
              ON agent_summaries(session_id, upto_seq DESC);
            """
        )
        # Idempotent migration for users on older DB files: add resume_target
        # column if it isn't already there. Sqlite ALTER TABLE only supports
        # ADD COLUMN (not IF NOT EXISTS), so we check pragma first.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(agent_sessions)").fetchall()}
        if "resume_target" not in cols:
            conn.execute("ALTER TABLE agent_sessions ADD COLUMN resume_target TEXT")
        from app.core.db_migrations import apply_migrations
        apply_migrations(conn, _MIGRATIONS)
        if "meta_json" not in cols:
            conn.execute("ALTER TABLE agent_sessions ADD COLUMN meta_json TEXT NOT NULL DEFAULT '{}'")


# ---------------------------------------------------------------------------
# Agent registry projection
# ---------------------------------------------------------------------------
def upsert_agent(
    agent_id: str,
    display_name: str,
    binary_path: str,
    default_model: str | None,
    models: list[str],
    caps: dict[str, Any],
    enabled: bool = True,
) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO agents(id, display_name, binary_path, default_model,
                                  models_json, caps_json, enabled, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 display_name=excluded.display_name,
                 binary_path=excluded.binary_path,
                 default_model=excluded.default_model,
                 models_json=excluded.models_json,
                 caps_json=excluded.caps_json,
                 enabled=excluded.enabled,
                 updated_at=excluded.updated_at""",
            (
                agent_id,
                display_name,
                binary_path,
                default_model,
                json.dumps(models, ensure_ascii=False),
                json.dumps(caps, ensure_ascii=False),
                1 if enabled else 0,
                _now(),
            ),
        )


def list_agents_db() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM agents WHERE enabled=1 ORDER BY display_name"
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["models"] = json.loads(d.pop("models_json") or "[]")
        d["caps"] = json.loads(d.pop("caps_json") or "{}")
        d["enabled"] = bool(d["enabled"])
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def list_sessions(user_id: str = "root", include_archived: bool = False, limit: int = 200) -> list[dict[str, Any]]:
    sql = "SELECT * FROM agent_sessions WHERE user_id = ?"
    params: list[Any] = [user_id]
    if not include_archived:
        sql += " AND archived = 0"
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["archived"] = bool(d["archived"])
        out.append(d)
    return out


def create_session(
    *,
    user_id: str,
    agent_id: str,
    model: str | None,
    title: str | None,
    workdir: str | None,
    parent_id: str | None = None,
    branch_anchor_seq: int | None = None,
    resume_target: str | None = None,
) -> dict[str, Any]:
    init_db()
    sid = _ulid()
    ts = _now()
    safe_title = (title or f"新会话 {datetime.now().strftime('%m-%d %H:%M')}").strip()[:120]
    with _write_lock, _connect() as conn:
        conn.execute(
            """INSERT INTO agent_sessions(id, user_id, parent_id, branch_anchor_seq,
                                          agent_id, model, title, workdir, status,
                                          created_at, updated_at, resume_target)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'idle', ?, ?, ?)""",
            (sid, user_id, parent_id, branch_anchor_seq, agent_id, model, safe_title, workdir, ts, ts, resume_target),
        )
    return get_session(sid)


def consume_resume_target(session_id: str) -> str | None:
    """One-shot read of ``resume_target`` — clears it after returning so the
    agent CLI is only resumed once (subsequent PTY restarts are normal)."""
    with _write_lock, _connect() as conn:
        row = conn.execute(
            "SELECT resume_target FROM agent_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row or not row["resume_target"]:
            return None
        target = row["resume_target"]
        conn.execute(
            "UPDATE agent_sessions SET resume_target = NULL WHERE id = ?", (session_id,)
        )
    return target


def get_session(session_id: str) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM agent_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    if not row:
        raise AgentSessionError("会话不存在")
    d = dict(row)
    d["archived"] = bool(d["archived"])
    try:
        d["meta"] = json.loads(d.get("meta_json") or "{}")
    except Exception:
        d["meta"] = {}
    return d


def update_session(
    session_id: str,
    *,
    title: str | None = None,
    archived: bool | None = None,
    model: str | None = None,
    status: str | None = None,
    last_summary_id: str | None = None,
    workdir: str | None = None,
    meta_patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sets: list[str] = []
    params: list[Any] = []
    if title is not None:
        sets.append("title = ?")
        params.append(title.strip()[:120] or "未命名会话")
    if archived is not None:
        sets.append("archived = ?")
        params.append(1 if archived else 0)
    if model is not None:
        sets.append("model = ?")
        params.append(model)
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if last_summary_id is not None:
        sets.append("last_summary_id = ?")
        params.append(last_summary_id)
    if workdir is not None:
        sets.append("workdir = ?")
        params.append(workdir)
    if meta_patch:
        # Merge into existing meta_json rather than overwrite, so unrelated
        # keys (e.g. claude_session_id) survive independent updates.
        current = get_session(session_id).get("meta", {})
        current.update(meta_patch)
        sets.append("meta_json = ?")
        params.append(json.dumps(current, ensure_ascii=False))
    if not sets:
        return get_session(session_id)
    sets.append("updated_at = ?")
    params.append(_now())
    params.append(session_id)
    with _write_lock, _connect() as conn:
        cur = conn.execute(f"UPDATE agent_sessions SET {', '.join(sets)} WHERE id = ?", params)
        if cur.rowcount == 0:
            raise AgentSessionError("会话不存在")
    return get_session(session_id)


def delete_session(session_id: str) -> None:
    """Hard-delete. Cascades to messages/summaries via FK ON DELETE CASCADE.

    Refuses if any branch points at this session — the caller (router) should
    surface that to the user. We don't auto-recurse because that's a
    surprising blast radius for a personal hub.
    """
    with _connect() as conn:
        children = conn.execute(
            "SELECT 1 FROM agent_sessions WHERE parent_id = ? LIMIT 1", (session_id,)
        ).fetchone()
        if children:
            raise AgentSessionError("该会话有分支，无法删除")
        conn.execute("DELETE FROM agent_sessions WHERE id = ?", (session_id,))


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
def _next_seq(conn: sqlite3.Connection, session_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) AS s FROM agent_messages WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return int(row["s"]) + 1


def add_message(
    session_id: str,
    *,
    role: str,
    content: str,
    kind: str = "text",
    source: str = "chat",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if role not in VALID_ROLES:
        raise AgentSessionError(f"非法 role: {role}")
    if kind not in VALID_KINDS:
        raise AgentSessionError(f"非法 kind: {kind}")
    if source not in VALID_SOURCES:
        raise AgentSessionError(f"非法 source: {source}")
    mid = _ulid()
    ts = _now()
    preview = content.replace("\n", " ").strip()[:160]
    with _write_lock, _connect() as conn:
        seq = _next_seq(conn, session_id)
        conn.execute(
            """INSERT INTO agent_messages(id, session_id, seq, role, kind, source,
                                          content, meta_json, created_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (mid, session_id, seq, role, kind, source, content,
             json.dumps(meta or {}, ensure_ascii=False), ts),
        )
        # Approximate token bump for compaction trigger; ~4 chars/token.
        bump = max(1, len(content) // 4)
        conn.execute(
            """UPDATE agent_sessions
               SET updated_at = ?, last_preview = ?, token_estimate = token_estimate + ?
               WHERE id = ?""",
            (ts, preview, bump, session_id),
        )
    return {
        "id": mid,
        "session_id": session_id,
        "seq": seq,
        "role": role,
        "kind": kind,
        "source": source,
        "content": content,
        "meta": meta or {},
        "created_at": ts,
    }


def append_to_message(message_id: str, additional: str) -> None:
    """Used during streaming to grow the assistant message in place.

    We append to the content column in a single UPDATE so partial reads see a
    consistent prefix. Token estimate is lazily updated on stream completion.
    """
    if not additional:
        return
    with _connect() as conn:
        conn.execute(
            "UPDATE agent_messages SET content = content || ? WHERE id = ?",
            (additional, message_id),
        )


def finalize_message(message_id: str, *, meta_patch: dict[str, Any] | None = None) -> None:
    if not meta_patch:
        return
    with _connect() as conn:
        row = conn.execute(
            "SELECT meta_json FROM agent_messages WHERE id = ?", (message_id,)
        ).fetchone()
        if not row:
            return
        try:
            meta = json.loads(row["meta_json"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        meta.update(meta_patch)
        conn.execute(
            "UPDATE agent_messages SET meta_json = ? WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False), message_id),
        )


def list_messages(
    session_id: str,
    *,
    after_seq: int = 0,
    limit: int = 1000,
    include_branch_inheritance: bool = True,
) -> list[dict[str, Any]]:
    """Return messages for a session, optionally inheriting from parent.

    When a session has a parent, the conceptual history is:
        parent.messages where seq <= branch_anchor_seq, then child.messages.
    We materialize this view at read time so branching costs O(1) at write.
    """
    sess = get_session(session_id)
    rows: list[sqlite3.Row] = []
    with _connect() as conn:
        if include_branch_inheritance and sess.get("parent_id") and sess.get("branch_anchor_seq"):
            anchor = int(sess["branch_anchor_seq"])
            rows.extend(
                conn.execute(
                    """SELECT *, ? AS effective_session FROM agent_messages
                       WHERE session_id = ? AND seq <= ? AND seq > ?
                       ORDER BY seq ASC LIMIT ?""",
                    (sess["parent_id"], sess["parent_id"], anchor, after_seq, limit),
                ).fetchall()
            )
        rows.extend(
            conn.execute(
                """SELECT *, ? AS effective_session FROM agent_messages
                   WHERE session_id = ? AND seq > ?
                   ORDER BY seq ASC LIMIT ?""",
                (session_id, session_id, after_seq, limit),
            ).fetchall()
        )
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["meta"] = json.loads(d.pop("meta_json") or "{}")
        except json.JSONDecodeError:
            d["meta"] = {}
        # inherited_from is only set when the row's session_id ≠ current session.
        if d.pop("effective_session") != d["session_id"]:
            d["inherited"] = True
        else:
            d["inherited"] = False
        out.append(d)
    return out


def get_max_seq(session_id: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS s FROM agent_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return int(row["s"]) if row else 0


# ---------------------------------------------------------------------------
# Branching (logical, no copy)
# ---------------------------------------------------------------------------
def branch_from(session_id: str, anchor_seq: int, *, title: str | None = None) -> dict[str, Any]:
    parent = get_session(session_id)
    max_seq = get_max_seq(session_id)
    if anchor_seq < 1 or anchor_seq > max_seq:
        raise AgentSessionError(f"分支锚点无效（范围 1..{max_seq}）")
    new_title = (title or f"{parent['title']} · 分支 @{anchor_seq}").strip()[:120]
    return create_session(
        user_id=parent["user_id"],
        agent_id=parent["agent_id"],
        model=parent.get("model"),
        title=new_title,
        workdir=parent.get("workdir"),
        parent_id=session_id,
        branch_anchor_seq=anchor_seq,
    )


def list_children(session_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_sessions WHERE parent_id = ? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["archived"] = bool(d["archived"])
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------
def add_summary(session_id: str, *, upto_seq: int, content: str, token_estimate: int) -> dict[str, Any]:
    sid = _ulid()
    ts = _now()
    with _write_lock, _connect() as conn:
        conn.execute(
            """INSERT INTO agent_summaries(id, session_id, upto_seq, content, token_estimate, created_at)
               VALUES(?, ?, ?, ?, ?, ?)""",
            (sid, session_id, upto_seq, content, token_estimate, ts),
        )
        # Reset the running estimate; the post-summary token cost is roughly
        # the summary itself.
        conn.execute(
            "UPDATE agent_sessions SET last_summary_id = ?, token_estimate = ?, updated_at = ? WHERE id = ?",
            (sid, token_estimate, ts, session_id),
        )
    # Also drop a system message so the chat view reflects the compaction event.
    add_message(
        session_id,
        role="system",
        kind="summary",
        source="system",
        content=content,
        meta={"summary_id": sid, "upto_seq": upto_seq},
    )
    return {"id": sid, "session_id": session_id, "upto_seq": upto_seq, "content": content, "created_at": ts}


def latest_summary(session_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM agent_summaries WHERE session_id = ? ORDER BY upto_seq DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    return dict(row) if row else None


def list_summaries(session_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_summaries WHERE session_id = ? ORDER BY upto_seq ASC",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]
