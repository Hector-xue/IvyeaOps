"""Persistent token-usage archive.

The /api/monitor/token-usage endpoint reads token data live from each tool's
own local store (Hermes state.db, Claude jsonl, etc). That data vanishes when
the tool is uninstalled. This module snapshots it into IvyeaOps's own DB so the
history survives.

Granularity: one row per (day, source, agent, model) — compact (a few rows per
day) yet enough to reconstruct every chart (daily/weekly/monthly/model/agent).

Idempotent: archive_run() re-aggregates recent days and UPSERTs, so running it
repeatedly (or re-archiving a day) overwrites rather than double-counts.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

from app.core.config import settings

_LOCAL_TZ = timezone(timedelta(hours=8))
DB_PATH = Path(settings.data_dir / "token_archive.sqlite3")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(DB_PATH))


def init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS token_daily (
              day          TEXT NOT NULL,   -- YYYY-MM-DD (Asia/Shanghai)
              source       TEXT NOT NULL,
              agent        TEXT NOT NULL,
              model        TEXT NOT NULL,
              sessions     INTEGER NOT NULL DEFAULT 0,
              input_tokens INTEGER NOT NULL DEFAULT 0,
              output_tokens INTEGER NOT NULL DEFAULT 0,
              cache_read   INTEGER NOT NULL DEFAULT 0,
              cache_write  INTEGER NOT NULL DEFAULT 0,
              credits      REAL NOT NULL DEFAULT 0,
              updated_at   REAL NOT NULL,
              PRIMARY KEY (day, source, agent, model)
            );
            CREATE INDEX IF NOT EXISTS idx_token_daily_day ON token_daily(day);
            """
        )
        conn.commit()
        _migrate_synthetic_model(conn)
    finally:
        conn.close()


def _migrate_synthetic_model(conn: sqlite3.Connection) -> None:
    """把历史行里的假模型名 `<synthetic>` 并进 `claude-code`。

    旧的 Claude 扫描器取会话文件里**第一个** message.model，会取到 Claude Code 为配额
    提示/报错本地构造的 `<synthetic>`，于是整个会话被记到一个不存在的模型名下。扫描器
    已修，但已经落库的归档行改不回真实型号（归档只留天粒度，对不回原文件），所以退一步
    并到 `claude-code` —— 至少是个真实存在的归属，且和未知模型同一价档。

    合并而不是改名：主键是 (day, source, agent, model)，同一天可能已有 claude-code 行，
    直接 UPDATE 会撞主键，UPDATE OR REPLACE 则会把另一行悄悄吃掉。
    """
    try:
        rows = conn.execute(
            """SELECT day, source, agent, sessions, input_tokens, output_tokens,
                      cache_read, cache_write, credits, updated_at
               FROM token_daily WHERE model = '<synthetic>'""").fetchall()
        if not rows:
            return
        for r in rows:
            conn.execute(
                """INSERT INTO token_daily
                   (day, source, agent, model, sessions, input_tokens, output_tokens,
                    cache_read, cache_write, credits, updated_at)
                   VALUES (?,?,?,'claude-code',?,?,?,?,?,?,?)
                   ON CONFLICT(day, source, agent, model) DO UPDATE SET
                     sessions=token_daily.sessions+excluded.sessions,
                     input_tokens=token_daily.input_tokens+excluded.input_tokens,
                     output_tokens=token_daily.output_tokens+excluded.output_tokens,
                     cache_read=token_daily.cache_read+excluded.cache_read,
                     cache_write=token_daily.cache_write+excluded.cache_write,
                     credits=token_daily.credits+excluded.credits,
                     updated_at=excluded.updated_at""",
                (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9]))
        conn.execute("DELETE FROM token_daily WHERE model = '<synthetic>'")
        conn.commit()
    except Exception:
        pass


def archive_run(lookback_days: int = 7) -> Dict[str, Any]:
    """Re-aggregate the last ``lookback_days`` days from live sources and UPSERT.

    We re-archive a rolling window (not just yesterday) because a tool's data
    for a given day can still be growing when we first snapshot it; re-running
    overwrites the day's rows with the latest complete figure.
    """
    init_db()
    # Import here to avoid a circular import at module load (monitor imports
    # this module's load_records).
    from app.routers.monitor import iter_all_records

    now = datetime.now(tz=_LOCAL_TZ)
    since_dt = now - timedelta(days=lookback_days)
    since = since_dt.timestamp()

    records, _coverage = iter_all_records(since)

    # Aggregate into (day, source, agent, model) buckets.
    buckets: Dict[tuple, Dict[str, Any]] = {}
    for r in records:
        day = datetime.fromtimestamp(r["ts"], tz=_LOCAL_TZ).strftime("%Y-%m-%d")
        key = (day, r["source"], r["agent"], r["model"] or "")
        b = buckets.get(key)
        if b is None:
            b = buckets[key] = {"sessions": 0, "input": 0, "output": 0,
                                "cache_read": 0, "cache_write": 0, "credits": 0.0}
        b["sessions"] += 1
        b["input"] += r["input"]
        b["output"] += r["output"]
        b["cache_read"] += r["cache_read"]
        b["cache_write"] += r["cache_write"]
        b["credits"] += r.get("credits", 0.0)

    ts = now.timestamp()
    conn = _connect()
    try:
        for (day, source, agent, model), b in buckets.items():
            conn.execute(
                """INSERT INTO token_daily
                   (day, source, agent, model, sessions, input_tokens, output_tokens,
                    cache_read, cache_write, credits, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(day, source, agent, model) DO UPDATE SET
                     sessions=excluded.sessions,
                     input_tokens=excluded.input_tokens,
                     output_tokens=excluded.output_tokens,
                     cache_read=excluded.cache_read,
                     cache_write=excluded.cache_write,
                     credits=excluded.credits,
                     updated_at=excluded.updated_at""",
                (day, source, agent, model, b["sessions"], b["input"], b["output"],
                 b["cache_read"], b["cache_write"], round(b["credits"], 6), ts),
            )
        conn.commit()
    finally:
        conn.close()
    return {"days": lookback_days, "buckets": len(buckets), "records": len(records)}


def load_records(since: float) -> List[Dict[str, Any]]:
    """Return archived buckets newer than ``since`` as flat replayable records.

    ``ts`` is set to local noon of the archived day — only the day bucket
    matters downstream, and noon avoids any timezone-boundary drift.
    """
    if not DB_PATH.exists():
        return []
    # 读之前也过一遍 init_db：建表是 IF NOT EXISTS，但历史数据迁移挂在这儿，
    # 只靠归档任务触发的话，没跑过归档的部署会一直读到旧的脏行。
    try:
        init_db()
    except Exception:
        pass
    since_day = datetime.fromtimestamp(since, tz=_LOCAL_TZ).strftime("%Y-%m-%d")
    out: List[Dict[str, Any]] = []
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT day, source, agent, model, sessions, input_tokens, output_tokens,
                      cache_read, cache_write, credits
               FROM token_daily WHERE day >= ?""", (since_day,)).fetchall()
    except Exception:
        return []
    finally:
        conn.close()

    for row in rows:
        day = row[0]
        try:
            noon = datetime.strptime(day, "%Y-%m-%d").replace(hour=12, tzinfo=_LOCAL_TZ)
            ts = noon.timestamp()
        except Exception:
            continue
        sessions = row[4] or 1
        # Expand to ``sessions`` records would inflate session counts; instead
        # emit ONE record carrying the bucket's summed tokens. _add counts it as
        # a single "session" — acceptable for archived history (we lost the
        # per-session granularity by design). Session totals for archived days
        # are therefore approximate; live days remain exact.
        out.append({
            "day": day, "ts": ts, "source": row[1], "agent": row[2], "model": row[3],
            "input": row[5] or 0, "output": row[6] or 0,
            "cache_read": row[7] or 0, "cache_write": row[8] or 0, "credits": row[9] or 0.0,
        })
    return out
