"""对外 MCP 的访问令牌。

**这是整件事的关键差异**：贝狸也发 MCP 令牌，但它的令牌指向贝狸的云 —— 用户要用
就得把店铺授权交出去。**我们发的令牌指向用户自己的机器**：Claude Desktop、Cursor
或任何别的 Agent 连过来，数据从头到尾没离开过他那台服务器。

三条设计
--------
* **只存哈希**。令牌明文只在生成的那一刻出现一次，之后库里只有 sha256。
  被拖库也拿不到能用的令牌 —— 这是最基本的，但很多自建服务都省了。
* **read 与 write 分开，write 默认不给**。读数据和改真实广告活动是两件事，
  一个用来做分析的令牌不该顺带具备改人家投放的能力。
* **记最后使用时间**。用户能看出哪个令牌还在用、哪个早就该撤销了 ——
  一个永远列着十个令牌却不知道谁在用的界面，等于没有撤销能力。
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ivyea.services.mcp_tokens")

SCOPES = ("read", "write")
_PREFIX = "ivmcp_"          # 前缀让人一眼认出这是什么，也便于日志里脱敏

_MIGRATIONS: tuple = ()


def _db() -> Path:
    from app.core.config import settings
    return Path(settings.data_dir) / "mcp_tokens.sqlite3"


def _connect() -> sqlite3.Connection:
    path = _db()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mcp_tokens (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                token_hash  TEXT NOT NULL UNIQUE,
                scopes      TEXT NOT NULL DEFAULT 'read',
                created_at  INTEGER NOT NULL,
                expires_at  INTEGER,
                last_used_at INTEGER,
                last_used_ip TEXT NOT NULL DEFAULT '',
                revoked     INTEGER NOT NULL DEFAULT 0
            )
        """)
        from app.core.db_migrations import apply_migrations
        apply_migrations(conn, _MIGRATIONS)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue(name: str, *, scopes: Optional[List[str]] = None,
          ttl_days: int = 0) -> Dict[str, Any]:
    """生成一个令牌。**明文只在这次返回里出现，之后再也拿不到。**"""
    wanted = [s for s in (scopes or ["read"]) if s in SCOPES] or ["read"]
    token = _PREFIX + secrets.token_urlsafe(32)
    now = int(time.time())
    row = {
        "id": secrets.token_hex(8), "name": name.strip()[:80] or "未命名",
        "token_hash": _hash(token), "scopes": ",".join(wanted),
        "created_at": now,
        "expires_at": now + ttl_days * 86400 if ttl_days > 0 else None,
    }
    with _connect() as conn:
        conn.execute(
            "INSERT INTO mcp_tokens(id, name, token_hash, scopes, created_at, expires_at)"
            " VALUES(?,?,?,?,?,?)",
            (row["id"], row["name"], row["token_hash"], row["scopes"],
             row["created_at"], row["expires_at"]))

    from app.core import audit
    audit.record("mcp", "token.issue", target=row["name"],
                 detail={"scopes": wanted, "ttl_days": ttl_days})
    return {**row, "token": token}      # 明文只此一次


def verify(token: str, *, need: str = "read", ip: str = "") -> Optional[dict]:
    """校验令牌并记录使用。不通过返回 None（调用方一律回 401，不解释原因 ——
    "令牌过期了"和"令牌不存在"的区别，对攻击者是有用信息，对正常用户没用）。"""
    if not token or not token.startswith(_PREFIX):
        return None
    now = int(time.time())
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM mcp_tokens WHERE token_hash = ? AND revoked = 0",
            (_hash(token),)).fetchone()
        if not row:
            return None
        if row["expires_at"] and row["expires_at"] < now:
            return None
        if need not in (row["scopes"] or "").split(","):
            return None
        conn.execute("UPDATE mcp_tokens SET last_used_at = ?, last_used_ip = ? WHERE id = ?",
                     (now, (ip or "")[:45], row["id"]))
        return dict(row)


def listing() -> List[dict]:
    """列出令牌 —— **不含哈希**，那是密钥材料，界面上不需要也不该出现。"""
    with _connect() as conn:
        return [
            {k: v for k, v in dict(r).items() if k != "token_hash"}
            for r in conn.execute("SELECT * FROM mcp_tokens ORDER BY created_at DESC")
        ]


def revoke(token_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("UPDATE mcp_tokens SET revoked = 1 WHERE id = ? AND revoked = 0",
                           (token_id,))
        ok = cur.rowcount > 0
    if ok:
        from app.core import audit
        audit.record("mcp", "token.revoke", target=token_id)
    return ok
