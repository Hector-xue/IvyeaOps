"""运营驾驶舱的日常运营数据面：促销倒计时 + 广告看板 + 就地调整。

**为什么挂 require_admin**：这里返回的是店铺经营数据，并且 ``/ads/adjust``
能创建真会改线上广告的工单 —— 与 ``/api/lingxing`` 同源同敏感度，权限就该一样。
驾驶舱的其余标签页（大盘/关键词/竞品）对所有登录用户开放，不受影响；前端按角色
决定显不显示这两个标签，后端这道是真闸。

**为什么读的是缓存**：广告看板冷启动实测 9 个店 × 1 天要 24.7 秒（领星限流
340ms/次）。页面直连没法用，所以后台预热（``cockpit_sync``）把数据灌进
``lingxing_cache``，这里永远读缓存；``force=1`` 才现拉，留给"我刚改完想立刻看"。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.security import require_admin
from app.services import ads_board_service as _ads
from app.services import lingxing_operate as _op
from app.services import lingxing_service as _gw
from app.services import promotions_service as _promo

logger = logging.getLogger("ivyea.routers.cockpit")
router = APIRouter(dependencies=[Depends(require_admin)])


def _sids(raw: Optional[str]) -> Optional[List[int]]:
    if not raw:
        return None
    out = [s.strip() for s in raw.replace("，", ",").split(",") if s.strip()]
    return [int(s) for s in out if s.lstrip("-").isdigit()] or None


def _guard(exc: Exception) -> HTTPException:
    return HTTPException(400, str(exc))


@router.get("/promotions")
async def promotions(sids: str = "", horizon_days: int = 30,
                     include_ended: bool = False, force: bool = False) -> Dict[str, Any]:
    """已报活动 / 优惠券倒计时。"""
    try:
        return await _promo.board(_sids(sids), horizon_days=horizon_days,
                                  include_ended=include_ended, force=force)
    except _gw.LingXingError as exc:
        raise _guard(exc) from exc


@router.get("/ads")
async def ads(sids: str = "", days: int = 7, top: int = 25,
              force: bool = False) -> Dict[str, Any]:
    """广告看板：总览 / 分店 / 分活动 / 趋势 / 异常。"""
    try:
        return await _ads.board(_sids(sids), days=days, top=top, force=force)
    except _gw.LingXingError as exc:
        raise _guard(exc) from exc


@router.get("/ads/hourly")
async def ads_hourly(sid: int, campaign_ids: str, date: str = "",
                     force: bool = False) -> Dict[str, Any]:
    """选中活动当天的小时曲线（亚马逊后台最难看的那块）。"""
    ids = [c.strip() for c in campaign_ids.replace("，", ",").split(",") if c.strip()]
    if not ids:
        raise HTTPException(400, "至少选一个广告活动")
    try:
        return await _ads.hourly(sid, ids, date=date or None, force=force)
    except _gw.LingXingError as exc:
        raise _guard(exc) from exc


class AdjustBody(BaseModel):
    op_type: str = "campaign_budget"
    sid: int
    target_id: str = ""
    target_name: str = ""
    new_value: Optional[float] = None
    cur_value: Optional[float] = None
    new_state: str = ""
    cur_state: str = ""
    rationale: str = ""
    # 加词/否词
    campaign_id: str = ""
    ad_group_id: str = ""
    keyword_text: str = ""
    match_type: str = ""
    bid: Optional[float] = None


@router.post("/ads/adjust")
async def ads_adjust(body: AdjustBody) -> Dict[str, Any]:
    """从看板发起一次调整。

    **这里不执行任何写操作** —— 它创建工单，后台跑护栏 + （小幅止血则免）复核，
    结果回到 ``awaiting_human``，必须再调 ``/confirm`` 才真的写。前端拿到
    ``fast_lane.eligible`` 就知道该显示"待确认"还是"AI 复核中"。
    """
    payload = body.model_dump()
    payload["rationale"] = payload.get("rationale") or "(驾驶舱直调)"
    try:
        ticket = await _op.create_manual_ticket(payload)
    except _gw.LingXingError as exc:
        raise _guard(exc) from exc
    return ticket


@router.get("/ads/adjust/{tid}")
def ads_adjust_status(tid: str) -> Dict[str, Any]:
    ticket = _op.get_ticket(tid)
    if not ticket:
        raise HTTPException(404, "未找到工单")
    return ticket


@router.post("/ads/adjust/{tid}/confirm")
async def ads_adjust_confirm(tid: str, dry_run: bool = Query(False)) -> Dict[str, Any]:
    """人工确认后执行。执行前每道闸重新过一遍，并抓回滚快照。"""
    try:
        return await _op.confirm_ticket(tid, decided_by="human", dry_run=dry_run)
    except _gw.LingXingError as exc:
        raise _guard(exc) from exc


@router.post("/ads/adjust/{tid}/reject")
async def ads_adjust_reject(tid: str) -> Dict[str, Any]:
    try:
        return await _op.reject_ticket(tid)
    except _gw.LingXingError as exc:
        raise _guard(exc) from exc


@router.get("/status")
def status() -> Dict[str, Any]:
    """驾驶舱这两块能不能用、数据从哪来、上次预热什么时候 —— 一次问清。"""
    from app.core import hub_settings as _hs
    from app.services import cockpit_sync as _sync

    return {
        "lingxing_enabled": _gw.is_master_enabled(),
        "operate_active": _gw.is_operate_active(),
        "fast_lane": {
            "enabled": bool(_hs.get("lingxing_fast_lane_enabled")),
            "max_pct": _hs.get("lingxing_fast_lane_max_pct"),
            "require_human": bool(_hs.get("lingxing_operate_require_human", True)),
        },
        "sync": _sync.status(),
        "op_types": _op.op_types_catalog(),
    }


@router.post("/sync")
async def sync_now() -> Dict[str, Any]:
    """手动跑一次预热（配置页/驾驶舱的「立即刷新」）。"""
    from app.services import cockpit_sync as _sync
    return await _sync.sync_once(trigger="manual")
