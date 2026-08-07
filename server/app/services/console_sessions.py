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
        source     TEXT NOT NULL DEFAULT 'console',
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
    """
    CREATE TABLE IF NOT EXISTS console_presets (
        name      TEXT NOT NULL,
        principal TEXT NOT NULL DEFAULT '',
        skill     TEXT NOT NULL DEFAULT '',
        approval  TEXT NOT NULL DEFAULT 'none',
        workspace TEXT NOT NULL DEFAULT '',
        note      TEXT NOT NULL DEFAULT '',
        created   REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (name, principal)
    );
    """,
)

def _m001_add_source(conn: sqlite3.Connection) -> None:
    """给老库补 source 列。新装的 baseline 已经带了，这里只补已存在的库。

    存量行一律记为 console —— 这个表在加 source 之前只有任务台会往里写。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(console_sessions)")}
    if "source" not in cols:
        conn.execute("ALTER TABLE console_sessions ADD COLUMN source TEXT NOT NULL DEFAULT 'console'")


# 追加即可，永远不要重排或删除已应用过的迁移。
_MIGRATIONS: tuple = (_m001_add_source,)

# 会话来源：任务台 / AI 问答 / 知识库对话。三处收编到同一个会话库之后，
# 左栏靠它区分并筛选。
SOURCES = ("console", "assistant", "brain")
SOURCE_LABELS = {"console": "任务台", "assistant": "AI 问答", "brain": "知识库"}


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

def register_session(session_id: str, principal: str, workspace: str = "",
                     source: str = "console") -> None:
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
                "INSERT INTO console_sessions (session_id, principal, workspace, title, source,"
                " created, updated) VALUES (?, ?, ?, '', ?, ?, ?)",
                (session_id, principal or "", workspace or "",
                 source if source in SOURCES else "console", now, now),
            )


def owned_sessions(principal: str, is_admin: bool, workspace: str = "",
                   source: str = "") -> dict[str, dict[str, Any]]:
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
    if source:
        where.append("source = ?")
        args.append(source)
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


def create_workspace(name: str, principal: str, path: str = "",
                     is_admin: bool = False) -> dict[str, Any]:
    """建一个工作区。可选绑定一个目录 —— 那会成为 Agent 文件类工具的工作目录。

    绑目录**仅限管理员**：绑了之后 Agent 的相对路径读写都落在那里，等于给了一片
    文件系统的访问面。和 MCP 的 stdio command 是同一类授权，规则保持一致。
    """
    name = (name or "").strip()[:120]
    path = (path or "").strip()
    if not name:
        raise ValueError("工作区名不能为空")
    if name == DEFAULT_WORKSPACE:
        raise ValueError("这是内置工作区，不需要创建")
    if path:
        if not is_admin:
            raise ValueError("只有管理员可以给工作区绑定目录")
        p = Path(path).expanduser()
        if not p.is_absolute():
            raise ValueError("目录必须是绝对路径")
        if not p.is_dir():
            raise ValueError(f"目录不存在：{p}")
        path = str(p.resolve())
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO console_workspaces (name, principal, path, created)"
            " VALUES (?, ?, ?, ?)",
            (name, principal or "", path, time.time()),
        )
    return {"name": name, "path": path, "builtin": False}


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
    """工作区绑定的目录（传给 agent 当文件类工具的工作目录）。没绑就空。

    ⚠️ 工作区名和目录是**两件事**：名字是给人看的分组标签（可能是中文），目录才是
    路径。把名字直接当 workspace 发给 agent，会让文件工具的相对路径落到一个不存在
    的目录上 —— 这是实测踩到过的 bug，所以路由层必须经这里换算一次。

    目录后来被删了就当没绑，别把 agent 的工作目录指到一个不存在的地方。
    """
    name = (name or "").strip()
    if not name or name == DEFAULT_WORKSPACE:
        return ""
    with _conn() as conn:
        row = conn.execute(
            "SELECT path FROM console_workspaces WHERE name = ? AND (principal = ? OR principal = '')",
            (name, principal or ""),
        ).fetchone()
    path = str(row["path"]) if row and row["path"] else ""
    if path and not Path(path).is_dir():
        return ""
    return path


# ── 智能体预设 ──────────────────────────────────────────────────────────────
# 一条预设 = 一句"以后这类活按这套跑"：用哪个技能、审批档位、落在哪个工作区。
# 存的是**设置**不是模型 —— 主脑在系统配置里全局切，预设里再放一份只会两处打架。
#
# 按 principal 隔离：预设里带着工作区（可能绑到某个目录），共享等于把别人的
# 目录选项摆进你的下拉框。要共享另说，先别把口子开在这。

def list_presets(principal: str) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM console_presets WHERE principal = ? ORDER BY created DESC",
            (principal or "",),
        ).fetchall()
    return [dict(r) for r in rows]


def save_preset(name: str, principal: str, *, skill: str = "", approval: str = "none",
                workspace: str = "", note: str = "") -> dict[str, Any]:
    clean = (name or "").strip()
    if not clean:
        raise ValueError("预设名不能为空")
    if approval not in ("none", "remote"):
        raise ValueError("审批档位只能是 none 或 remote")
    row = {
        "name": clean[:120], "principal": principal or "", "skill": (skill or "")[:200],
        "approval": approval, "workspace": (workspace or "")[:120], "note": (note or "")[:500],
        "created": time.time(),
    }
    with _conn() as conn:
        # 同名即覆盖：用户改一个预设时按的是"保存"，不该冒出第二条同名的
        conn.execute(
            "INSERT INTO console_presets (name, principal, skill, approval, workspace, note, created)"
            " VALUES (:name, :principal, :skill, :approval, :workspace, :note, :created)"
            " ON CONFLICT(name, principal) DO UPDATE SET"
            " skill=excluded.skill, approval=excluded.approval,"
            " workspace=excluded.workspace, note=excluded.note",
            row,
        )
    return row


def delete_preset(name: str, principal: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM console_presets WHERE name = ? AND principal = ?",
                           ((name or "").strip(), principal or ""))
        return cur.rowcount > 0
