"""任务台的会话索引与工作区。

**只做索引，不搬真相。** 会话正文仍然只存在 agent 那边的 ``~/.ivyea/sessions/*.json``；
这里存的是 ops 才知道、agent 不关心的东西：这条会话属于谁、归到哪个工作区、
用户给它起了什么名字。复制一份消息进来只会制造两个会分叉的真相源。

为什么必须记"属于谁"：agent 的会话库是**整机共享**的一个目录，
``GET /api/ivyea-agent/chat/sessions`` 会把机器上所有会话原样返回。以前它只在
右下角悬浮球的「历史会话」里露一下，没人注意；现在会话要常驻左栏，等于把同事的
对话摆在每个人眼前。所以列表按归属过滤：普通用户只看自己的，管理员看全部
（那是他自己的机器）。

工作区 = ops 侧的分组概念，可选绑一个目录。注意它和 agent 的 ``/v1/workspace/*``
不是一回事 —— 那组端点是代码索引（搜符号、算影响面）。这里的工作区只负责
"把会话归堆" + 可选地告诉 agent 文件类工具该在哪个目录下干活。
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.core.config import settings
from app.core.db_migrations import apply_migrations

DEFAULT_WORKSPACE = "默认工作区"

# 每轮会往 user 消息尾巴上追加"喂给模型"的上下文（命中的技能说明书、检索到的知识
# 证据）。它们和用户真正说的话存在同一条消息里，所以 agent 那边算出来的 preview
# 会带上一大段 `[Ivyea Skill：...]`。展示前一律从第一个标记处截断。
_INJECTION_MARKERS = (
    "\n\n[Ivyea Skill：",
    "\n\n[Ivyea 本地知识检索",
    "\n\n[Ivyea 内置亚马逊知识库",
    "\n\n[任务范围锁定",
    "\n\n[工程上下文]",
)


def clean_preview(text: str) -> str:
    """把注入给模型的上下文从展示文本里剥掉，只留用户真正打的那句话。

    要处理**被截断的半截标记**：agent 侧的 `sessions.listing` 先把首条 user 消息
    砍到 50 字才交给我们，所以经常拿到 `…一句话总结。\\n\\n[Iv` 这种收尾 ——
    完整标记匹配不上，半个标记就留在标题里了。
    """
    out = str(text or "")
    for marker in _INJECTION_MARKERS:
        idx = out.find(marker)
        if idx >= 0:
            out = out[:idx]
    # 收尾是某个标记的前缀（被截断了）→ 一并切掉
    for marker in _INJECTION_MARKERS:
        for size in range(len(marker), 2, -1):
            if out.endswith(marker[:size]):
                out = out[:-size]
                break
    return out.strip()

_BASELINE_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS console_sessions (
        session_id TEXT PRIMARY KEY NOT NULL,
        principal  TEXT NOT NULL DEFAULT '',
        workspace  TEXT NOT NULL DEFAULT '',
        title      TEXT NOT NULL DEFAULT '',
        created    REAL NOT NULL DEFAULT 0,
        updated    REAL NOT NULL DEFAULT 0
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_console_sessions_principal ON console_sessions(principal, updated DESC);",
    """
    CREATE TABLE IF NOT EXISTS console_workspaces (
        name      TEXT NOT NULL,
        principal TEXT NOT NULL DEFAULT '',
        path      TEXT NOT NULL DEFAULT '',
        created   REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (name, principal)
    );
    """,
)

# 追加即可，永远不要重排或删除已应用过的迁移。
_MIGRATIONS: tuple = ()


def _db_path() -> Path:
    return settings.data_dir / "console_sessions.sqlite3"


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        for ddl in _BASELINE_SCHEMA:
            conn.execute(ddl)
        apply_migrations(conn, _MIGRATIONS)


# ── 会话 ────────────────────────────────────────────────────────────────────

def register_session(session_id: str, principal: str, workspace: str = "") -> None:
    """记下一条会话的归属。首次见到就落库，之后只更新时间戳。

    从转发 SSE 时捕获的 ``start`` 事件调用 —— session_id 是 agent 现场生成的，
    ops 只有在流里才第一次看到它。
    """
    session_id = (session_id or "").strip()
    if not session_id:
        return
    now = time.time()
    with _conn() as conn:
        row = conn.execute(
            "SELECT session_id FROM console_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row:
            # 归属不覆盖：一条会话的主人就是开它的那个人。
            conn.execute(
                "UPDATE console_sessions SET updated = ? WHERE session_id = ?", (now, session_id))
        else:
            conn.execute(
                "INSERT INTO console_sessions (session_id, principal, workspace, title, created, updated)"
                " VALUES (?, ?, ?, '', ?, ?)",
                (session_id, principal or "", workspace or "", now, now),
            )


def owned_sessions(principal: str, is_admin: bool, workspace: str = "") -> dict[str, dict[str, Any]]:
    """当前用户可见的会话索引，键是 session_id。"""
    sql = "SELECT * FROM console_sessions"
    args: list[Any] = []
    where: list[str] = []
    if not is_admin:
        where.append("principal = ?")
        args.append(principal or "")
    if workspace:
        where.append("workspace = ?")
        args.append(workspace)
    if where:
        sql += " WHERE " + " AND ".join(where)
    with _conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return {r["session_id"]: dict(r) for r in rows}


def can_access(session_id: str, principal: str, is_admin: bool) -> bool:
    """管理员看全部；普通用户只碰自己的。

    索引里没有的会话（悬浮球开的、CLI 开的、装这套之前就有的）对普通用户一律
    不可见 —— 宁可少给，不能把别人的对话端出去。
    """
    if is_admin:
        return True
    with _conn() as conn:
        row = conn.execute(
            "SELECT principal FROM console_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return bool(row and row["principal"] == (principal or ""))


def update_session(session_id: str, *, title: str | None = None,
                   workspace: str | None = None) -> None:
    sets, args = [], []
    if title is not None:
        sets.append("title = ?")
        args.append(title.strip()[:120])
    if workspace is not None:
        sets.append("workspace = ?")
        args.append(workspace.strip()[:120])
    if not sets:
        return
    sets.append("updated = ?")
    args.append(time.time())
    args.append(session_id)
    with _conn() as conn:
        conn.execute(f"UPDATE console_sessions SET {', '.join(sets)} WHERE session_id = ?", args)


def forget_session(session_id: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM console_sessions WHERE session_id = ?", (session_id,))


# ── 工作区 ──────────────────────────────────────────────────────────────────

def list_workspaces(principal: str, is_admin: bool) -> list[dict[str, Any]]:
    """工作区清单。默认工作区总在第一位，且不需要建。"""
    sql = "SELECT * FROM console_workspaces"
    args: list[Any] = []
    if not is_admin:
        sql += " WHERE principal = ?"
        args.append(principal or "")
    with _conn() as conn:
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY created", args).fetchall()]
    out = [{"name": DEFAULT_WORKSPACE, "path": "", "builtin": True}]
    for r in rows:
        if r["name"] == DEFAULT_WORKSPACE:
            continue
        out.append({"name": r["name"], "path": r["path"], "builtin": False})
    return out


def create_workspace(name: str, principal: str, path: str = "") -> dict[str, Any]:
    name = (name or "").strip()[:120]
    if not name:
        raise ValueError("工作区名不能为空")
    if name == DEFAULT_WORKSPACE:
        raise ValueError("这是内置工作区，不需要创建")
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO console_workspaces (name, principal, path, created)"
            " VALUES (?, ?, ?, ?)",
            (name, principal or "", (path or "").strip(), time.time()),
        )
    return {"name": name, "path": (path or "").strip(), "builtin": False}


def delete_workspace(name: str, principal: str, is_admin: bool) -> int:
    """删掉工作区，里面的会话回到默认工作区（**不删会话**）。返回移动的会话数。"""
    name = (name or "").strip()
    if not name or name == DEFAULT_WORKSPACE:
        raise ValueError("内置工作区不能删除")
    with _conn() as conn:
        if is_admin:
            conn.execute("DELETE FROM console_workspaces WHERE name = ?", (name,))
        else:
            conn.execute("DELETE FROM console_workspaces WHERE name = ? AND principal = ?",
                         (name, principal or ""))
        cur = conn.execute(
            "UPDATE console_sessions SET workspace = '' WHERE workspace = ?", (name,))
        return int(cur.rowcount or 0)


def workspace_path(name: str, principal: str) -> str:
    """工作区绑定的目录（传给 agent 当文件类工具的工作目录）。没绑就空。"""
    name = (name or "").strip()
    if not name or name == DEFAULT_WORKSPACE:
        return ""
    with _conn() as conn:
        row = conn.execute(
            "SELECT path FROM console_workspaces WHERE name = ? AND (principal = ? OR principal = '')",
            (name, principal or ""),
        ).fetchone()
    return str(row["path"]) if row and row["path"] else ""
