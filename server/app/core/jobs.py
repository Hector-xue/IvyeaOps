"""任务持久化引擎：重启不丢任务。

**为什么必须有**：现在每个子系统各写一套任务状态（listing 用 listing_jobs 表、
ad_audit / asin_audit 用 meta.json、调度器是进程内 ``while True``），共同的毛病是
**进程一重启，正在跑的任务就没了**：子进程没了，磁盘上的状态永远停在 running，
UI 上要么挂着一个永远转圈的幽灵任务，要么被开机扫描静默改成 failed —— 用户看到
的是"任务凭空消失"，而且不知道为什么。

这个模块提供一份共用的账本，三件事是它和现有各套实现的区别：

1. **租约（lease）而不是"活着的进程"**。执行者定期续租；进程没了租约就到期，
   下次启动/别的 worker 能安全接管。判断"这个任务还有人在跑吗"从此有依据，
   而不是靠"_live_jobs 是空的所以磁盘上的都是死的"这种只在单进程下成立的假设。
2. **孤儿要显形，不要静默改成 failed**。重启冲掉的任务标成 ``orphaned``，
   可重入的自动重排队，不可重入的（写操作类）留在那儿等人处理 ——
   "任务失败了"和"任务被重启打断了"对用户是两件事。
3. **一份统一的进度协议**。所有长任务用同一个 ``progress()``，前端一个订阅点
   就够，不必每个板块各写一套轮询。

**不引入 Celery/Redis**：零外部依赖是自托管产品的核心体验，不能为了一个任务队列
让用户先去装个 Redis。SQLite + 租约足够扛住"一台机器上的一个团队"这个真实负载。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("ivyea.core.jobs")

# 任务状态。沿用各子系统已有的词汇，只多一个 orphaned。
QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
ORPHANED = "orphaned"          # 被重启/崩溃打断，不是"失败"

TERMINAL = frozenset({SUCCEEDED, FAILED, CANCELLED, ORPHANED})

DEFAULT_LEASE_SECONDS = 60     # 执行者要在这之内续租，否则视为掉线

_MIGRATIONS: tuple = ()


def _db_path() -> Path:
    from app.core.config import settings
    return Path(settings.data_dir) / "jobs.sqlite3"


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
            CREATE TABLE IF NOT EXISTS jobs (
                id            TEXT PRIMARY KEY,
                kind          TEXT NOT NULL,
                payload       TEXT NOT NULL DEFAULT '{}',
                status        TEXT NOT NULL,
                progress      REAL NOT NULL DEFAULT 0,
                message       TEXT NOT NULL DEFAULT '',
                result        TEXT,
                error         TEXT,
                owner_id      TEXT,
                attempt       INTEGER NOT NULL DEFAULT 0,
                max_attempts  INTEGER NOT NULL DEFAULT 1,
                retriable     INTEGER NOT NULL DEFAULT 1,
                lease_until   INTEGER,
                created_at    INTEGER NOT NULL,
                started_at    INTEGER,
                finished_at   INTEGER,
                request_id    TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, kind)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC)")
        from app.core.db_migrations import apply_migrations
        apply_migrations(conn, _MIGRATIONS)


def _row(r: sqlite3.Row) -> dict:
    out = dict(r)
    for field in ("payload", "result"):
        if out.get(field):
            try:
                out[field] = json.loads(out[field])
            except (TypeError, ValueError):
                pass
    return out


# ── 生命周期 ────────────────────────────────────────────────────────────

def create(
    kind: str,
    payload: Optional[dict] = None,
    *,
    owner_id: Optional[str] = None,
    max_attempts: int = 1,
    retriable: bool = True,
) -> str:
    """排一个任务，返回 id。

    ``retriable=False`` 用于写操作类任务（改了外部系统的那种）：被重启打断后
    **绝不能**自动重跑，只能标成孤儿等人确认。
    """
    from app.core import obs

    job_id = uuid.uuid4().hex
    with _connect() as conn:
        conn.execute(
            "INSERT INTO jobs(id, kind, payload, status, owner_id, max_attempts,"
            " retriable, created_at, request_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (job_id, kind, json.dumps(payload or {}, ensure_ascii=False), QUEUED,
             owner_id, max(1, int(max_attempts)), 1 if retriable else 0,
             int(time.time()), obs.get_request_id()),
        )
    return job_id


def claim(job_id: str, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> bool:
    """把任务标成 running 并拿一段租约。已经被别人持有时返回 False。"""
    now = int(time.time())
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status=?, started_at=COALESCE(started_at, ?),"
            " attempt=attempt+1, lease_until=? "
            "WHERE id=? AND (status=? OR (status=? AND lease_until < ?))",
            (RUNNING, now, now + lease_seconds, job_id, QUEUED, RUNNING, now),
        )
        return cur.rowcount > 0


def heartbeat(job_id: str, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> None:
    """续租。执行者要定期调，否则会被当成掉线而被接管。"""
    with _connect() as conn:
        conn.execute("UPDATE jobs SET lease_until=? WHERE id=? AND status=?",
                     (int(time.time()) + lease_seconds, job_id, RUNNING))


def progress(job_id: str, pct: float, message: str = "",
             *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> None:
    """上报进度。**顺带续租** —— 有进度就说明还活着，让调用方不必记得两件事。"""
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET progress=?, message=?, lease_until=? WHERE id=? AND status=?",
            (max(0.0, min(100.0, float(pct))), message[:500],
             int(time.time()) + lease_seconds, job_id, RUNNING),
        )


def finish(job_id: str, *, result: Any = None) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status=?, progress=100, result=?, finished_at=?,"
            " lease_until=NULL WHERE id=?",
            (SUCCEEDED, json.dumps(result, ensure_ascii=False, default=str)
             if result is not None else None, int(time.time()), job_id),
        )


def fail(job_id: str, error: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status=?, error=?, finished_at=?, lease_until=NULL WHERE id=?",
            (FAILED, str(error)[:2000], int(time.time()), job_id),
        )


def cancel(job_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status=?, finished_at=?, lease_until=NULL"
            " WHERE id=? AND status NOT IN (?,?,?,?)",
            (CANCELLED, int(time.time()), job_id, SUCCEEDED, FAILED, CANCELLED, ORPHANED),
        )
        return cur.rowcount > 0


# ── 查询 ────────────────────────────────────────────────────────────────

def get(job_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _row(row) if row else None


def list_jobs(*, kind: Optional[str] = None, status: Optional[str] = None,
              limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM jobs WHERE 1=1"
    args: list[Any] = []
    if kind:
        sql += " AND kind=?"; args.append(kind)
    if status:
        sql += " AND status=?"; args.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(max(1, min(int(limit), 1000)))
    with _connect() as conn:
        return [_row(r) for r in conn.execute(sql, args)]


# ── 启动自愈 ────────────────────────────────────────────────────────────

def recover_orphans() -> dict:
    """开机自愈：把租约过期的 running 任务处理掉。

    **可重入的重新排队，不可重入的标成 orphaned 留在那儿等人看。**
    关键在于绝不静默改成 failed —— "任务失败了"和"任务被重启打断了"是两件事，
    混为一谈的话用户永远查不出为什么他的任务凭空消失了。
    """
    now = int(time.time())
    requeued = orphaned = 0
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, retriable, attempt, max_attempts FROM jobs"
            " WHERE status=? AND (lease_until IS NULL OR lease_until < ?)",
            (RUNNING, now),
        ).fetchall()
        for r in rows:
            if r["retriable"] and r["attempt"] < r["max_attempts"]:
                conn.execute("UPDATE jobs SET status=?, lease_until=NULL WHERE id=?",
                             (QUEUED, r["id"]))
                requeued += 1
            else:
                conn.execute(
                    "UPDATE jobs SET status=?, error=?, finished_at=?, lease_until=NULL"
                    " WHERE id=?",
                    (ORPHANED, "服务重启打断了这个任务（不是执行失败）。"
                               "如需继续请重新发起。", now, r["id"]),
                )
                orphaned += 1
    if requeued or orphaned:
        logger.warning("启动自愈：重排队 %d 个、标记孤儿 %d 个", requeued, orphaned)
    return {"requeued": requeued, "orphaned": orphaned}


def purge(older_than_days: int = 30) -> int:
    """清理很久以前的终态任务，别让账本无限长。"""
    cutoff = int(time.time()) - older_than_days * 86400
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM jobs WHERE finished_at IS NOT NULL AND finished_at < ?"
            " AND status IN (?,?,?,?)",
            (cutoff, SUCCEEDED, FAILED, CANCELLED, ORPHANED),
        )
        return cur.rowcount


# ── 执行 ────────────────────────────────────────────────────────────────

async def run(job_id: str, fn: Callable[[str], Any]) -> dict:
    """跑一个已排队的任务，负责认领 / 落结果 / 落异常。

    ``fn`` 收到 job_id，自己按需调 ``progress()``。异常统一变成 failed 并把堆栈
    落日志 —— 不能让一个任务的异常把调度循环带走。
    """
    import asyncio
    import inspect

    if not claim(job_id):
        return {"ok": False, "reason": "任务已被其他执行者持有或已结束"}
    try:
        out = fn(job_id)
        if inspect.isawaitable(out):
            out = await out
        finish(job_id, result=out)
        return {"ok": True, "result": out}
    except asyncio.CancelledError:
        cancel(job_id)
        raise
    except Exception as exc:  # noqa: BLE001 — 任务异常不能带走调度循环
        logger.exception("任务失败 job=%s", job_id)
        fail(job_id, f"{type(exc).__name__}: {exc}")
        return {"ok": False, "error": str(exc)}
