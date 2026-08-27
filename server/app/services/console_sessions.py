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
    "\n\n[Ivyea 记忆召回",
    "\n\n[Ivyea 内置亚马逊知识库",
    "\n\n[任务范围锁定",
    "\n\n[工程上下文]",
    "\n\n[用户附图",
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
    CREATE TABLE IF NOT EXISTS console_approvals (
        request_id   TEXT PRIMARY KEY NOT NULL,
        session_id   TEXT NOT NULL DEFAULT '',
        principal    TEXT NOT NULL DEFAULT '',
        title        TEXT NOT NULL DEFAULT '',
        op_type      TEXT NOT NULL DEFAULT '',
        decision     TEXT NOT NULL DEFAULT '',
        requested_at REAL NOT NULL DEFAULT 0,
        decided_at   REAL NOT NULL DEFAULT 0
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_console_approvals_session ON console_approvals(session_id, requested_at);",
    """
    CREATE TABLE IF NOT EXISTS console_presets (
        name      TEXT NOT NULL,
        principal TEXT NOT NULL DEFAULT '',
        skill     TEXT NOT NULL DEFAULT '',
        approval  TEXT NOT NULL DEFAULT 'none',
        workspace TEXT NOT NULL DEFAULT '',
        system    TEXT NOT NULL DEFAULT '',
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


def _m002_add_preset_system(conn: sqlite3.Connection) -> None:
    """给预设补 system 列（人设/判断标准）。

    存量预设留空 —— 空的话这一轮就不注入任何人设，行为与加这列之前逐字一致。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(console_presets)")}
    if "system" not in cols:
        conn.execute("ALTER TABLE console_presets ADD COLUMN system TEXT NOT NULL DEFAULT ''")


# 追加即可，永远不要重排或删除已应用过的迁移。
_MIGRATIONS: tuple = (_m001_add_source, _m002_add_preset_system)

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


def session_row(session_id: str) -> dict[str, Any] | None:
    """索引里的那一行（没有就是 None）。自动起名要先看标题是不是还空着 ——
    用户手动改过的名字**绝不能被模型覆盖**。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM console_sessions WHERE session_id = ?", (session_id or "",)
        ).fetchone()
    return dict(row) if row else None


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
                workspace: str = "", note: str = "", system: str = "") -> dict[str, Any]:
    clean = (name or "").strip()
    if not clean:
        raise ValueError("预设名不能为空")
    if approval not in ("none", "remote", "auto"):
        raise ValueError("审批档位只能是 none（只读）、remote（逐项审批）或 auto（完全放行）")
    row = {
        "name": clean[:120], "principal": principal or "", "skill": (skill or "")[:200],
        "approval": approval, "workspace": (workspace or "")[:120],
        # 人设会整段进这一轮的系统提示。给个上限，别让一条预设把上下文吃掉一大块。
        "system": (system or "")[:4000],
        "note": (note or "")[:500],
        "created": time.time(),
    }
    with _conn() as conn:
        # 同名即覆盖：用户改一个预设时按的是"保存"，不该冒出第二条同名的
        conn.execute(
            "INSERT INTO console_presets (name, principal, skill, approval, workspace, system,"
            " note, created)"
            " VALUES (:name, :principal, :skill, :approval, :workspace, :system, :note, :created)"
            " ON CONFLICT(name, principal) DO UPDATE SET"
            " skill=excluded.skill, approval=excluded.approval,"
            " workspace=excluded.workspace, system=excluded.system, note=excluded.note",
            row,
        )
    return row


def delete_preset(name: str, principal: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM console_presets WHERE name = ? AND principal = ?",
                           ((name or "").strip(), principal or ""))
        return cur.rowcount > 0


# ── 审批留痕 ────────────────────────────────────────────────────────────────
# 「Agent 想改线上数据，我批了还是拒了」是**这套系统最该留下的一条记录**。
# 之前它只活在前端 state 里，刷新一下就没了 —— 出了事根本查不到是谁点的确认。
#
# 请求和决定分两次落：permission_request 到达时先记下"问过什么"，
# 用户点了之后再补上"答的什么"。中途关掉页面就停在"未决"，这本身也是信息。

def record_approval_request(request_id: str, session_id: str, principal: str,
                            title: str = "", op_type: str = "") -> None:
    if not request_id:
        return
    with _conn() as conn:
        conn.execute(
            "INSERT INTO console_approvals (request_id, session_id, principal, title, op_type,"
            " decision, requested_at, decided_at) VALUES (?, ?, ?, ?, ?, '', ?, 0)"
            # 同一个 request_id 不会重来，真撞上说明是重放，保留最早那条
            " ON CONFLICT(request_id) DO NOTHING",
            (request_id, session_id or "", principal or "", (title or "")[:300],
             (op_type or "")[:80], time.time()),
        )


def record_approval_decision(request_id: str, decision: str) -> None:
    """只补决定，不新建行 —— 没有对应请求的决定是伪造的，不该在这里凭空长出记录。"""
    if not request_id or not decision:
        return
    with _conn() as conn:
        conn.execute(
            "UPDATE console_approvals SET decision = ?, decided_at = ?"
            " WHERE request_id = ? AND decision = ''",
            (decision[:40], time.time(), request_id),
        )


def pending_approvals(principal: str, limit: int = 50) -> list[dict[str, Any]]:
    """这个人名下**所有还没决定**的审批，跨会话。

    此前审批只能按会话查（``session_approvals``），意味着要处理一条审批，得先
    知道它在哪个会话、点进去、再在长长的对话里找到那张卡片。在电脑前还能忍，
    在手机上等于做不到 —— 而"手机上点同意/拒绝"正是这个产品对 WorkBuddy
    「IM 远程下指令」的回答（只审批、不下达，更安全）。
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM console_approvals WHERE principal = ? AND decision = ''"
            " ORDER BY requested_at DESC LIMIT ?",
            (principal or "", max(1, min(int(limit or 50), 200))),
        ).fetchall()
    return [dict(r) for r in rows]


def expire_stale_approvals(live_request_ids: list[str] | None,
                           max_age: float = 900.0) -> int:
    """把**已经不可能再被点**的待审批行销账，返回销掉几条。

    这张表是流水账：只有决策帧或 permission_timeout 帧回到 ops 才会写 decision。
    可这三种情况下这两种帧都不会来 ——
      · 用户直接关掉页面（agent 侧看到 client_gone，就地按拒绝收摊）
      · 传输断链
      · agent 重启（阻塞的队列随进程一起没了）
    于是会话都结束几天了，「待审批」里还挂着一张点了只会 409 的僵尸卡片。

    ``live_request_ids`` 是 agent 报上来的"此刻真的还在等"的集合，它是唯一的真相；
    传 None 表示**问不到**（老 agent / agent 没起），那就只按年龄兜底 —— 绝不能把
    问不到当成空集，那会一把清掉真正等着的审批。

    年龄阈值默认 900s：agent 侧的审批超时是 600s，多留 5 分钟余量，免得把一条刚
    发出、还在正常等待的审批判死。
    """
    now = time.time()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT request_id, requested_at FROM console_approvals WHERE decision = ''"
        ).fetchall()
        stale = []
        for r in rows:
            rid = str(r["request_id"])
            aged = (now - float(r["requested_at"] or 0)) > max_age
            gone = live_request_ids is not None and rid not in live_request_ids
            if aged or gone:
                stale.append(rid)
        for rid in stale:
            conn.execute(
                "UPDATE console_approvals SET decision = 'expired', decided_at = ?"
                " WHERE request_id = ? AND decision = ''", (now, rid))
    return len(stale)


def session_approvals(session_id: str, limit: int = 100) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM console_approvals WHERE session_id = ?"
            " ORDER BY requested_at ASC LIMIT ?",
            (session_id or "", max(1, min(int(limit or 100), 500))),
        ).fetchall()
    return [dict(r) for r in rows]
