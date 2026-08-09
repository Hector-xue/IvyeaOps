"""Listing 通用后台任务引擎。

所有长任务（采集 / AI 分析 / 文案 / 套图策划 / 生图 / 整套生成 / 整套复核）都
走这一个引擎：sqlite 持久化 + 内存 pub/sub 推 SSE 进度 + 轮询兜底。刷新页面、
切项目、关浏览器都不影响任务；服务重启时 running 任务被标记为失败并可重跑。
"""
from __future__ import annotations

import asyncio
import logging
import json
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.core.security import require_user

from .common import _db

logger = logging.getLogger("ivyea.routers.listing.jobs")

router = APIRouter()

# 每个 job 一组订阅队列；进程内即可（SSE 断线由前端轮询兜底）。
_SUBSCRIBERS: dict[str, set[asyncio.Queue]] = {}
_TASKS: dict[str, asyncio.Task] = {}

_TERMINAL = {"done", "failed"}


def _startup_cleanup() -> None:
    """服务重启后，孤儿 running 任务不能永远转圈。"""
    conn = _db()
    conn.execute(
        "UPDATE listing_jobs SET status='failed', error=?, updated_at=? WHERE status='running'",
        ("服务重启中断，请重新运行", time.time()),
    )
    conn.commit()
    conn.close()


_startup_cleanup()


def _row_to_job(row) -> dict:
    job = dict(row)
    for key in ("params", "result"):
        if job.get(key):
            try:
                job[key] = json.loads(job[key])
            except Exception:
                logger.debug("job 失败（旁路，已忽略）", exc_info=True)
    return job


def get_job(job_id: str) -> Optional[dict]:
    conn = _db()
    row = conn.execute("SELECT * FROM listing_jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    return _row_to_job(row) if row else None


def active_job(project_id: str, kind: str) -> Optional[dict]:
    conn = _db()
    row = conn.execute(
        "SELECT * FROM listing_jobs WHERE project_id=? AND kind=? AND status='running' "
        "ORDER BY created_at DESC LIMIT 1",
        (project_id, kind),
    ).fetchone()
    conn.close()
    return _row_to_job(row) if row else None


def _publish(job_id: str, payload: dict) -> None:
    for queue in list(_SUBSCRIBERS.get(job_id, ())):
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.debug("queue.put_nowait 失败（旁路，已忽略）", exc_info=True)


class JobHandle:
    """传给任务执行体的进度句柄：每次 update 都持久化并推送 SSE。"""

    def __init__(self, job_id: str, project_id: str, kind: str):
        self.job_id = job_id
        self.project_id = project_id
        self.kind = kind

    def update(self, stage: str = None, message: str = None, progress: float = None,
               total: int = None, done_count: int = None) -> None:
        fields: dict[str, Any] = {}
        if stage is not None:
            fields["stage"] = stage
        if message is not None:
            fields["message"] = message
        if progress is not None:
            fields["progress"] = max(0.0, min(1.0, float(progress)))
        if total is not None:
            fields["total"] = int(total)
        if done_count is not None:
            fields["done_count"] = int(done_count)
        if not fields:
            return
        sets = ", ".join(f"{key}=?" for key in fields)
        conn = _db()
        conn.execute(
            f"UPDATE listing_jobs SET {sets}, updated_at=? WHERE id=?",
            (*fields.values(), time.time(), self.job_id),
        )
        conn.commit()
        conn.close()
        _publish(self.job_id, {"event": "progress", **(get_job(self.job_id) or {})})


Runner = Callable[[JobHandle], Awaitable[Any]]


def start_job(kind: str, project_id: str, params: dict, runner: Runner,
              *, singleton: bool = True) -> dict:
    """创建并启动一个后台任务；singleton=True 时同项目同类型只允许一个在跑。"""
    if singleton:
        existing = active_job(project_id, kind)
        if existing:
            return existing
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    conn = _db()
    conn.execute(
        "INSERT INTO listing_jobs (id, project_id, kind, status, stage, message, progress, "
        "total, done_count, params, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (job_id, project_id, kind, "running", "queued", "任务已创建", 0.0, 0, 0,
         json.dumps(params or {}, ensure_ascii=False), now, now),
    )
    conn.commit()
    conn.close()
    handle = JobHandle(job_id, project_id, kind)

    async def _run() -> None:
        try:
            result = await runner(handle)
            conn = _db()
            conn.execute(
                "UPDATE listing_jobs SET status='done', progress=1.0, result=?, updated_at=? WHERE id=?",
                (json.dumps(result if result is not None else {}, ensure_ascii=False),
                 time.time(), job_id),
            )
            conn.commit()
            conn.close()
        except Exception as exc:  # noqa: BLE001
            detail = getattr(exc, "detail", None) or str(exc) or exc.__class__.__name__
            conn = _db()
            conn.execute(
                "UPDATE listing_jobs SET status='failed', error=?, updated_at=? WHERE id=?",
                (str(detail)[:1000], time.time(), job_id),
            )
            conn.commit()
            conn.close()
        finally:
            _publish(job_id, {"event": "end", **(get_job(job_id) or {})})
            _TASKS.pop(job_id, None)

    _TASKS[job_id] = asyncio.create_task(_run())
    return get_job(job_id) or {"id": job_id, "kind": kind, "status": "running"}


# ─── 路由 ─────────────────────────────────────────────────────────────────────

@router.get("/jobs/{job_id}")
def read_job(job_id: str, _user: str = Depends(require_user)):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


@router.get("/projects/{project_id}/jobs")
def project_jobs(project_id: str, _user: str = Depends(require_user)):
    """项目最近任务 + 当前活动任务，前端进页面/切项目时用它恢复任务状态。"""
    conn = _db()
    rows = conn.execute(
        "SELECT * FROM listing_jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 20",
        (project_id,),
    ).fetchall()
    conn.close()
    jobs = [_row_to_job(row) for row in rows]
    return {"jobs": jobs, "active": [job for job in jobs if job["status"] == "running"]}


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, _user: str = Depends(require_user)):
    """SSE 进度流；任务结束后自动关闭。前端断线时退回轮询 /jobs/{id}。"""
    if not get_job(job_id):
        raise HTTPException(404, "job not found")

    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _SUBSCRIBERS.setdefault(job_id, set()).add(queue)

    async def stream():
        try:
            current = get_job(job_id) or {}
            yield f"data: {json.dumps({'event': 'progress', **current}, ensure_ascii=False)}\n\n"
            if current.get("status") in _TERMINAL:
                return
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if payload.get("event") == "end" or payload.get("status") in _TERMINAL:
                    return
        finally:
            subs = _SUBSCRIBERS.get(job_id)
            if subs:
                subs.discard(queue)
                if not subs:
                    _SUBSCRIBERS.pop(job_id, None)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # 反代（nginx/Cloudflare）不缓冲 SSE
    })
