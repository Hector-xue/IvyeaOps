"""定时任务 API。

归属规则与任务台会话一致：管理员看全部（那是他自己的机器），普通用户只碰自己建的。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.security import require_user_info
from app.services import schedules as sc

router = APIRouter(dependencies=[Depends(require_user_info)])


class TaskBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    cron: str = Field(..., min_length=1, max_length=120)
    prompt: str = Field(..., min_length=1, max_length=8000)
    skill: str = Field(default="", max_length=200)
    workspace: str = Field(default="", max_length=120)
    enabled: bool = True


class TaskPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    cron: str | None = Field(default=None, max_length=120)
    prompt: str | None = Field(default=None, max_length=8000)
    skill: str | None = Field(default=None, max_length=200)
    workspace: str | None = Field(default=None, max_length=120)
    enabled: bool | None = None


def _who(info: dict[str, Any]) -> tuple[str, bool]:
    return str(info.get("email") or info.get("id") or ""), (info.get("role") == "admin")


def _mine_or_404(task_id: str, info: dict[str, Any]) -> dict[str, Any]:
    principal, is_admin = _who(info)
    task = sc.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    if not is_admin and task.get("principal") != principal:
        raise HTTPException(status_code=403, detail="无权操作他人的定时任务")
    return task


@router.get("/schedules")
def list_schedules(info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    principal, is_admin = _who(info)
    tasks = sc.list_tasks(principal, is_admin)
    for t in tasks:
        t["next_text"] = sc.describe_cron(t["cron"]) if t["enabled"] else "已停用"
    return {"ok": True, "tasks": tasks}


@router.post("/schedules")
def create_schedule(body: TaskBody,
                    info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    principal, is_admin = _who(info)
    try:
        task = sc.create_task(
            name=body.name, cron=body.cron, prompt=body.prompt, skill=body.skill,
            workspace=body.workspace, enabled=body.enabled,
            principal=principal, role=("admin" if is_admin else "user"),
        )
    except (ValueError, sc.CronError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "task": task}


@router.patch("/schedules/{task_id}")
def patch_schedule(task_id: str, body: TaskPatch,
                   info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    _mine_or_404(task_id, info)
    try:
        task = sc.update_task(task_id, **body.model_dump(exclude_none=True))
    except (ValueError, sc.CronError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "task": task}


@router.delete("/schedules/{task_id}")
def delete_schedule(task_id: str,
                    info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    _mine_or_404(task_id, info)
    sc.delete_task(task_id)
    return {"ok": True, "deleted": task_id}


@router.get("/schedules/{task_id}/runs")
def schedule_runs(task_id: str, limit: int = Query(20, ge=1, le=100),
                  info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    _mine_or_404(task_id, info)
    return {"ok": True, "runs": sc.list_runs(task_id, limit)}


@router.post("/schedules/{task_id}/run")
async def run_schedule(task_id: str,
                       info: dict[str, Any] = Depends(require_user_info)) -> dict[str, Any]:
    """手动触发一次。和到点自动跑走完全同一条路 —— 包括只读那条底线。"""
    import asyncio
    task = _mine_or_404(task_id, info)
    run = await asyncio.to_thread(sc.run_task_now, task, "manual")
    return {"ok": True, "run": run}


@router.post("/schedules/preview-cron")
def preview_cron(expr: str = Query(..., min_length=1, max_length=120)) -> dict[str, Any]:
    """校验 cron 并给出接下来几次触发时刻 —— 让人在保存前就看清它到底什么时候跑。"""
    import time as _t
    from datetime import datetime
    try:
        out, cursor = [], _t.time()
        for _ in range(5):
            cursor = sc.next_fire(expr, cursor)
            out.append(datetime.fromtimestamp(cursor).strftime("%Y-%m-%d %H:%M"))
        return {"ok": True, "next": out}
    except sc.CronError as exc:
        return {"ok": False, "error": str(exc)}
