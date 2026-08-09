"""通知与预算的管理接口（管理员）。

发通知是这台机器主动往外发请求的少数几个动作之一，所以配置只有管理员能改。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.security import require_admin
from app.services import budget as budget_svc
from app.services import notify as notify_svc

logger = logging.getLogger("ivyea.routers.notify")
router = APIRouter(dependencies=[Depends(require_admin)])


class TestBody(BaseModel):
    url: str = ""


@router.get("/config")
def config() -> Dict[str, Any]:
    """设置页需要的全部信息：可选事件、当前勾选、当前地址与识别出的渠道。"""
    url = notify_svc.webhook_url()
    return {
        "events": notify_svc.EVENTS,
        "default_events": notify_svc.DEFAULT_EVENTS,
        "enabled_events": notify_svc.enabled_events(),
        "webhook_set": bool(url),
        "channel": notify_svc._channel(url) if url else "",
    }


@router.post("/test")
def test(body: TestBody) -> Dict[str, Any]:
    """往配置的地址发一条测试消息，直接把对方的回应给用户看。"""
    return notify_svc.test(body.url)


@router.get("/budget")
def budget() -> Dict[str, Any]:
    """本月估算花费与预算状态。顺带做一次"超了就提醒"的检查 —— 用户打开
    这个页面本来就是在关心花费，此刻检查最自然，不必再养一个定时任务。"""
    return budget_svc.check_and_notify()
