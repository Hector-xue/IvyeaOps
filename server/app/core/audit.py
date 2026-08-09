"""统一审计流水：谁、在哪个板块、做了什么、成没成。

**为什么需要它**：领星那边已经有 ``lingxing_audit`` / ``lingxing_op_ticket``
把写操作留了痕，但**终端、git、autofix 这三个能执行代码的模块一条记录都没有**。
于是团队自托管时，"谁往生产库里写了那批否定关键词""谁把配置改了""谁在服务器上
跑了什么命令"全都答不上来 —— 领星那边只看到你这一个 AppID。

这同时是**面对企业用户时的合规资产**：SaaS 反而不好证明"你的数据我没动过"，
自托管能把本机审计流水直接拿出来。

三条设计约束
------------
1. **审计不能反过来把系统弄挂**。写流水失败只记一条 warning，绝不影响业务动作
   —— 这是审计系统的通病：为了"不丢记录"而让主流程失败。
2. **流水里不能有凭据**。detail 走和诊断包同一套脱敏，命令行里夹着的 token 也
   要脱掉（``--token=xxx`` 这种）。
3. **独立一个库**（``data/audit.sqlite3``）。它的保留周期、导出、备份策略都和
   业务数据不同，混在一起以后不好单独处理。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger("ivyea.core.audit")

_MIGRATIONS: tuple = ()

# 命令行里常见的"值就跟在后面"的凭据写法。审计要留下"跑了什么"，
# 但不能把 token 一起留下来。
_INLINE_SECRET = re.compile(
    r"((?:--?(?:token|key|secret|password|passwd|auth)[= ])|(?:Bearer\s+))(\S+)",
    re.IGNORECASE,
)


def _db_path() -> Path:
    from app.core.config import settings
    return Path(settings.data_dir) / "audit.sqlite3"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          INTEGER NOT NULL,
                actor_id    TEXT,
                actor_name  TEXT,
                module      TEXT NOT NULL,
                action      TEXT NOT NULL,
                target      TEXT,
                outcome     TEXT NOT NULL,
                detail      TEXT,
                request_id  TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_module ON audit_log(module, ts DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_name, ts DESC)")
        from app.core.db_migrations import apply_migrations
        apply_migrations(conn, _MIGRATIONS)


def scrub(text: str) -> str:
    """把命令行/参数里夹带的凭据换掉，保留结构以便看懂当时跑了什么。"""
    if not text:
        return text
    return _INLINE_SECRET.sub(lambda m: m.group(1) + "***", text)


def _current_actor() -> tuple[Optional[str], str]:
    """尽力拿到当前调用者。拿不到就记 system（启动任务、定时调度等）。"""
    try:
        from app.core.security import current_user
        cu = current_user.get()
        if cu:
            return str(cu.get("id") or ""), str(cu.get("email") or "unknown")
    except Exception:  # noqa: BLE001 — 审计取不到身份也不能把业务动作弄挂
        logger.debug("current_user.get 失败（旁路，已忽略）", exc_info=True)
    return None, "system"


def record(
    module: str,
    action: str,
    *,
    target: str = "",
    outcome: str = "ok",
    detail: Any = None,
    actor_id: Optional[str] = None,
    actor_name: Optional[str] = None,
) -> None:
    """记一条流水。**任何失败都只打日志，绝不上抛。**"""
    try:
        if actor_name is None:
            actor_id, actor_name = _current_actor()

        from app.core import obs
        from app.services.diagnostics import redact

        payload = None
        if detail is not None:
            payload = json.dumps(redact(detail), ensure_ascii=False, default=str)
            payload = scrub(payload)[:4000]

        with _connect() as conn:
            conn.execute(
                "INSERT INTO audit_log(ts, actor_id, actor_name, module, action, target,"
                " outcome, detail, request_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (int(time.time()), actor_id, actor_name, module, action,
                 scrub(str(target))[:1000], outcome, payload, obs.get_request_id()),
            )
    except Exception as exc:  # noqa: BLE001
        # 审计系统的通病就是"为了不丢记录而让主流程失败"。这里明确反过来。
        logger.warning("审计写入失败（不影响业务动作）：%s: %s", type(exc).__name__, exc)


def query(
    *,
    module: Optional[str] = None,
    actor: Optional[str] = None,
    since: Optional[int] = None,
    limit: int = 200,
) -> list[dict]:
    sql = "SELECT * FROM audit_log WHERE 1=1"
    args: list[Any] = []
    if module:
        sql += " AND module = ?"; args.append(module)
    if actor:
        sql += " AND actor_name = ?"; args.append(actor)
    if since:
        sql += " AND ts >= ?"; args.append(int(since))
    sql += " ORDER BY ts DESC, id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 2000)))
    try:
        with _connect() as conn:
            return [dict(r) for r in conn.execute(sql, args)]
    except sqlite3.Error as exc:
        logger.warning("审计查询失败：%s", exc)
        return []


def modules() -> list[str]:
    try:
        with _connect() as conn:
            return [r[0] for r in conn.execute(
                "SELECT DISTINCT module FROM audit_log ORDER BY module")]
    except sqlite3.Error:
        return []


def to_csv(rows: Iterable[dict]) -> str:
    import csv
    import io

    buf = io.StringIO()
    cols = ["ts", "actor_name", "module", "action", "target", "outcome", "request_id", "detail"]
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()
