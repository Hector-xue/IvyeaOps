"""领星 广告数据大盘 — cross-store/campaign aggregation.

Reuses the gateway read layer (cache-friendly: past-day reports are immutable)
to aggregate SP campaign reports over a window into: headline totals, per-store
rollup, top campaigns, and a per-day trend. Pure read; no writes.

**界面上已经没有调用方了**（2026-08-29）。领星板块并进运营驾驶舱时，它的「大盘」
标签撤掉了，由驾驶舱的广告看板承接 —— ``ads_board_service`` 是这个模块的严格超集
（同一批 SP 日报表、同样的 ``1..days`` 窗口，另外多出目标 ACOS、今日预算进度、
异常标注和小时曲线）。

**那为什么不删**：``ivyea_ops_tools`` 的 ``lingxing_dashboard`` 工具直接 import
路由函数 ``routers.lingxing.dashboard``，agent 拿到的是这里的返回形状。改成
``ads_board`` 的瘦封装等于悄悄换掉一个工具的输出 schema —— 那是 agent 侧的契约，
不该被一次前端重构顺手改掉。所以原样留着，只在这里写清楚它现在服务谁。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.services import lingxing_data as _data
from app.services import lingxing_service as _gw

_REPORT_TTL_S = 7 * 86400  # past-day reports never change


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _derive(m: Dict[str, float]) -> Dict[str, Any]:
    spend, sales, clicks, impr, orders = (m["spend"], m["sales"], m["clicks"], m["impressions"], m["orders"])
    return {
        "spend": round(spend, 2), "sales": round(sales, 2), "orders": int(orders),
        "clicks": int(clicks), "impressions": int(impr),
        "acos": round(spend / sales, 4) if sales else None,
        "roas": round(sales / spend, 2) if spend else None,
        "ctr": round(clicks / impr, 4) if impr else None,
        "cvr": round(orders / clicks, 4) if clicks else None,
    }


def _bucket() -> Dict[str, float]:
    return {"spend": 0.0, "sales": 0.0, "orders": 0.0, "clicks": 0.0, "impressions": 0.0}


def _add(b: Dict[str, float], r: Dict[str, Any]) -> None:
    b["spend"] += _f(r.get("cost"))
    b["sales"] += _f(r.get("sales"))
    b["orders"] += _f(r.get("orders"))
    b["clicks"] += _f(r.get("clicks"))
    b["impressions"] += _f(r.get("impressions"))


async def _resolve_sids(sids: Optional[List[int]]) -> Dict[int, str]:
    """Return {sid: store_name} for the requested sids (or all if None/empty)."""
    sellers = await _data.fetch_dataset("sellers")
    name_by_sid = {int(s["sid"]): s.get("name") for s in (sellers.get("rows") or [])
                   if str(s.get("sid", "")).isdigit()}
    if sids:
        return {sid: name_by_sid.get(sid, str(sid)) for sid in sids}
    return name_by_sid


async def dashboard(sids: Optional[List[int]] = None, days: int = 7) -> Dict[str, Any]:
    if not _gw.is_master_enabled():
        raise _gw.LingXingError("领星集成未启用（总开关关闭）")
    days = max(1, min(int(days), 60))
    store_names = await _resolve_sids(sids)

    totals = _bucket()
    prev_totals = _bucket()
    prev_seen = False
    by_store: Dict[int, Dict[str, float]] = {}
    by_campaign: Dict[str, Dict[str, Any]] = {}
    by_day: Dict[str, Dict[str, float]] = {}

    for sid, sname in store_names.items():
        # campaign names for nicer labels
        try:
            camps = await _data.fetch_dataset("sp_campaigns", {"sid": sid, "length": 300})
            cname = {str(c.get("campaign_id")): c.get("name") for c in (camps.get("rows") or [])}
        except _gw.LingXingError:
            cname = {}
        sb = by_store.setdefault(sid, _bucket())
        # current window (1..days) + the window right before it (days+1..2*days)
        # for period-over-period KPIs; past-day reports are cached so the extra
        # window is cheap after the first load.
        for d in range(1, 2 * days + 1):
            day = (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%d")
            try:
                rep = await _data.fetch_dataset(
                    "sp_campaign_report", {"sid": sid, "report_date": day, "length": 300},
                    ttl=_REPORT_TTL_S)
            except _gw.LingXingError:
                continue
            if d > days:
                for r in (rep.get("rows") or []):
                    _add(prev_totals, r)
                    prev_seen = True
                continue
            db = by_day.setdefault(day, _bucket())
            for r in (rep.get("rows") or []):
                cid = str(r.get("campaign_id"))
                _add(totals, r); _add(sb, r); _add(db, r)
                key = f"{sid}:{cid}"
                cb = by_campaign.setdefault(key, {"sid": sid, "store": sname, "campaign_id": cid,
                                                  "name": cname.get(cid), **_bucket()})
                _add(cb, r)

    stores = [{"sid": sid, "store": store_names.get(sid, str(sid)), **_derive(b)}
              for sid, b in by_store.items()]
    stores.sort(key=lambda x: x["spend"], reverse=True)

    campaigns = []
    for v in by_campaign.values():
        d = _derive(v)
        campaigns.append({"sid": v["sid"], "store": v["store"], "campaign_id": v["campaign_id"],
                          "name": v["name"], **d})
    campaigns.sort(key=lambda x: x["spend"], reverse=True)

    trend = [{"date": day, **_derive(b)} for day, b in sorted(by_day.items())]

    return {
        "scope": {"sids": list(store_names.keys()), "days": days, "store_count": len(store_names)},
        "totals": _derive(totals),
        "prev_totals": _derive(prev_totals) if prev_seen else None,
        "by_store": stores,
        "by_campaign": campaigns[:25],
        "trend": trend,
    }
