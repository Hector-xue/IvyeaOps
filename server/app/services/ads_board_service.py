"""驾驶舱·广告看板 —— 比亚马逊后台好用的地方，全在"把数字翻译成该不该动手"。

后台给的是 ACOS 是多少；运营真正要判断的是**这个 ACOS 好不好、要不要动、动多少**。
所以这个看板做四件后台不做的事：

1. **目标 ACOS 由毛利率自动推**（盈亏平衡 = 毛利率，目标 = factor × 毛利率），
   口径直接复用 :mod:`lingxing_optimizer` 的 ``_store_margin`` / ``_campaign_margins``
   —— 同一个数在两个板块里不能有两套算法。
2. **今日预算消耗进度**：今天已花 / 日预算。后台要点进每个活动才看得到，
   而"下午三点就烧完预算"恰恰是最该当天处理的事。
3. **异常自动标注**，每条异常直接带一个**可执行的调整意图**（op_type + 目标 +
   建议值），界面上就是一个按钮。
4. **跨店铺一屏**：后台要一个个 profile 切。

三条实测得来的现实，写在代码里免得再踩
------------------------------------
* ``has_ads_setting = 0`` 的店铺（本机 TR / PL）调广告报表会回
  ``code=102 参数不合法``。按标志位跳过，并在 ``skipped`` 里说明原因 ——
  给用户看一排红色报错是最差的做法。
* 小时数据 ``spCampaignHourData`` **一次只能取一个活动一天**。11 个店 × 几十个
  活动 = 几百次调用，撞限流。所以它不在 board() 里，是单独按需取的接口，
  且调用数有上限。
* 日报表的历史日期是不变量，缓存可以放很长（7 天）；**今天**的数据一直在回填，
  只能短缓存。两者 TTL 必须分开，否则要么看不到今天的变化、要么天天重拉历史。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core import hub_settings as _hs
from app.core import marketplaces as _mkt
from app.services import lingxing_data as _data
from app.services import lingxing_service as _gw

logger = logging.getLogger("ivyea.services.ads_board")

#: 历史日期的报表是不变量 —— 缓存一周。
_PAST_TTL_S = 7 * 86400
#: 今天的数据一直在回填 —— 5 分钟。
_TODAY_TTL_S = 300
#: 小时数据单次最多拉多少个活动（保护限流）。
MAX_HOURLY_CAMPAIGNS = 12

SEVERITY_ORDER = {"crit": 0, "warn": 1, "info": 2}


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _cfg() -> Dict[str, Any]:
    keys = ("lingxing_target_acos_factor", "lingxing_target_acos_override",
            "lingxing_margin_override", "lingxing_bid_min_clicks",
            "lingxing_neg_min_clicks", "lingxing_bid_step_pct",
            "lingxing_max_change_pct")
    return {k: _hs.get(k) for k in keys}


def _bucket() -> Dict[str, float]:
    return {"spend": 0.0, "sales": 0.0, "orders": 0.0, "clicks": 0.0, "impressions": 0.0}


def _add(b: Dict[str, float], row: Dict[str, Any]) -> None:
    b["spend"] += _f(row.get("cost"))
    b["sales"] += _f(row.get("sales"))
    b["orders"] += _f(row.get("orders"))
    b["clicks"] += _f(row.get("clicks"))
    b["impressions"] += _f(row.get("impressions"))


def _derive(m: Dict[str, float]) -> Dict[str, Any]:
    spend, sales = m["spend"], m["sales"]
    clicks, impr, orders = m["clicks"], m["impressions"], m["orders"]
    return {
        "spend": round(spend, 2), "sales": round(sales, 2), "orders": int(orders),
        "clicks": int(clicks), "impressions": int(impr),
        "acos": round(spend / sales, 4) if sales else None,
        "roas": round(sales / spend, 2) if spend else None,
        "ctr": round(clicks / impr, 4) if impr else None,
        "cvr": round(orders / clicks, 4) if clicks else None,
        "cpc": round(spend / clicks, 2) if clicks else None,
    }


def _pct_change(now: float, before: float) -> Optional[float]:
    if not before:
        return None
    return round((now - before) / before * 100, 1)


async def _stores(sids: Optional[List[int]]) -> tuple:
    """返回 (可投广告的店铺, 被跳过的店铺及原因)。"""
    sellers = await _data.fetch_dataset("sellers", {}, caller="ads_board")
    usable: Dict[int, Dict[str, Any]] = {}
    skipped: List[Dict[str, Any]] = []
    for row in (sellers.get("rows") or []):
        meta = _mkt.store_meta(row)
        sid = meta["sid"]
        if not isinstance(sid, int):
            continue
        if sids and sid not in sids:
            continue
        if not meta["has_ads"]:
            # 实测：这类店查广告报表回 code=102，不是我们的 bug，是没开广告。
            skipped.append({"sid": sid, "name": meta["name"], "reason": "该店未开通广告"})
            continue
        usable[sid] = meta
    return usable, skipped


async def _campaign_config(sid: int, force: bool) -> Dict[str, Dict[str, Any]]:
    """campaign_id → 配置快照（预算 / 状态 / 投放状态）。无日期参数即实时。"""
    out: Dict[str, Dict[str, Any]] = {}
    for offset in range(0, 900, 300):
        try:
            res = await _data.fetch_dataset(
                "sp_campaigns", {"sid": sid, "length": 300, "offset": offset},
                ttl=_TODAY_TTL_S, force=force, caller="ads_board")
        except (_gw.LingXingError, ValueError):
            break
        rows = res.get("rows") or []
        for c in rows:
            out[str(c.get("campaign_id"))] = {
                "name": c.get("name") or "",
                "state": c.get("state") or "",
                "serving_status": c.get("serving_status") or "",
                "daily_budget": _f(c.get("daily_budget")),
                "targeting_type": c.get("targeting_type") or "",
            }
        if len(rows) < 300:
            break
    return out


async def _daily_rows(sid: int, day: str, *, today: bool, force: bool) -> List[Dict[str, Any]]:
    try:
        res = await _data.fetch_dataset(
            "sp_campaign_report", {"sid": sid, "report_date": day, "length": 300},
            ttl=_TODAY_TTL_S if today else _PAST_TTL_S, force=force, caller="ads_board")
    except (_gw.LingXingError, ValueError):
        return []
    return res.get("rows") or []


async def _targets_for(sid: int) -> Dict[str, Any]:
    """目标 ACOS —— 口径与优化器共用一份实现，绝不另算。"""
    from app.services import lingxing_optimizer as _opt

    cfg = _cfg()
    override_target = _f(cfg.get("lingxing_target_acos_override"))
    override_margin = _f(cfg.get("lingxing_margin_override"))
    factor = _f(cfg.get("lingxing_target_acos_factor")) or 0.7

    if override_target > 0:
        return {"margin": None, "breakeven_acos": None, "target_acos": override_target,
                "per_campaign": {}, "note": "目标ACOS=手动设定"}
    if override_margin > 0:
        return {"margin": override_margin, "breakeven_acos": override_margin,
                "target_acos": round(factor * override_margin, 4), "per_campaign": {},
                "note": f"毛利率=手动 {override_margin:.0%}，目标ACOS={factor * override_margin:.0%}"}

    margin = await _opt._store_margin(sid)
    per_campaign: Dict[str, float] = {}
    if margin:
        try:
            per_campaign = {cid: round(factor * m, 4)
                            for cid, m in (await _opt._campaign_margins(sid)).items()}
        except Exception:  # noqa: BLE001 — per-campaign 是加分项，拿不到不能拖垮看板
            logger.debug("_campaign_margins 失败（旁路）", exc_info=True)
    if not margin:
        return {"margin": None, "breakeven_acos": None, "target_acos": None,
                "per_campaign": {}, "note": "没有毛利数据，无法推目标ACOS（需要领星利润报表）"}
    return {
        "margin": round(margin, 4),
        "breakeven_acos": round(margin, 4),
        "target_acos": round(factor * margin, 4),
        "per_campaign": per_campaign,
        "note": (f"毛利率≈{margin:.0%}(店铺均值)，目标ACOS={factor * margin:.0%}"
                 f"(={factor:g}×毛利)"
                 + (f"；{len(per_campaign)} 个活动用各自产品毛利单独定目标" if per_campaign else "")),
    }


def _health(acos: Optional[float], target: Optional[float],
            breakeven: Optional[float], spend: float, sales: float) -> str:
    if spend > 0 and sales <= 0:
        return "bad"
    if acos is None or target is None:
        return "unknown"
    if acos <= target:
        return "good"
    if breakeven and acos <= breakeven:
        return "watch"
    return "bad"


def _anomalies(camp: Dict[str, Any], cfg: Dict[str, Any],
               yesterday: Optional[Dict[str, float]]) -> List[Dict[str, Any]]:
    """确定性规则（不是 LLM）。每条带一个可以直接点的调整意图。

    只写**不需要历史基线**的规则 —— 需要跨天基线做差分的（活动被外部改预算、
    非预期暂停）由 agent 的巡检负责，它有基线快照，这里没有，硬做会误报。
    """
    out: List[Dict[str, Any]] = []
    step = _f(cfg.get("lingxing_bid_step_pct")) or 15.0
    min_clicks = int(_f(cfg.get("lingxing_bid_min_clicks")) or 15)
    budget = camp.get("daily_budget") or 0.0
    spend, sales = camp["spend"], camp["sales"]
    acos, target = camp.get("acos"), camp.get("target_acos")
    cid, sid = camp["campaign_id"], camp["sid"]

    def budget_intent(direction: int, reason: str) -> Dict[str, Any]:
        new = round(budget * (1 + direction * step / 100.0), 2)
        return {"op_type": "campaign_budget", "sid": sid, "target_id": cid,
                "target_name": camp.get("name") or cid,
                "cur_value": budget, "new_value": new, "rationale": reason}

    if "OUT_OF_BUDGET" in (camp.get("serving_status") or "").upper():
        good = acos is not None and target is not None and acos <= target
        out.append({
            "code": "ads.out_of_budget",
            "severity": "warn" if good else "info",
            "label": "预算已耗尽停投",
            "detail": ("这个活动 ACOS 优于目标却因为预算花完停投了 —— 正在漏单。"
                       if good else "预算已花完，今天不再出广告。ACOS 未达目标，先别急着加预算。"),
            "intent": budget_intent(+1, "预算耗尽且 ACOS 优于目标，小幅提预算继续拿量") if good else None,
        })

    if budget > 0 and camp.get("today_spend") is not None:
        used = camp["today_spend"] / budget
        if used >= 0.95 and "OUT_OF_BUDGET" not in (camp.get("serving_status") or "").upper():
            out.append({
                "code": "ads.budget_capped",
                "severity": "info",
                "label": "今日预算见底",
                "detail": f"今天已花掉日预算的 {used:.0%}，接近停投。",
                "intent": budget_intent(+1, "今日预算见底，小幅提预算") if (
                    acos is not None and target is not None and acos <= target) else None,
            })

    if spend > 0 and sales <= 0 and camp["clicks"] >= min_clicks:
        out.append({
            "code": "ads.spend_no_sales",
            "severity": "crit",
            "label": "有花费零销售额",
            "detail": f"{int(camp['clicks'])} 次点击、花了 {spend:.2f}，一单没出。",
            "intent": budget_intent(-1, f"{int(camp['clicks'])} 次点击零转化，先降预算止血"),
        })

    if acos is not None and target is not None and target > 0 and acos > target * 1.5 and spend > 0:
        out.append({
            "code": "ads.acos_breach",
            "severity": "crit" if (camp.get("breakeven_acos") and acos > camp["breakeven_acos"]) else "warn",
            "label": "ACOS 破位",
            "detail": (f"ACOS {acos:.0%} 已是目标 {target:.0%} 的 {acos / target:.1f} 倍"
                       + ("，且高于盈亏平衡线 —— 每卖一单都在亏。"
                          if camp.get("breakeven_acos") and acos > camp["breakeven_acos"] else "。")),
            "intent": budget_intent(-1, f"ACOS {acos:.0%} 超目标 {target:.0%}，降预算止血"),
        })

    if yesterday and camp["impressions"] == 0 and yesterday.get("impressions", 0) > 1000:
        out.append({
            "code": "ads.impression_zero",
            "severity": "crit",
            "label": "曝光归零",
            "detail": f"昨天还有 {int(yesterday['impressions'])} 次曝光，今天为 0。多半是账号/合规/竞价问题，先查再改。",
            "intent": None,   # 这类不给一键改 —— 原因不在预算上，改预算是南辕北辙
        })

    if yesterday and camp.get("cpc") and yesterday.get("cpc"):
        jump = _pct_change(camp["cpc"], yesterday["cpc"])
        if jump is not None and jump >= 30:
            out.append({
                "code": "ads.cpc_jump",
                "severity": "warn",
                "label": "CPC 跳涨",
                "detail": f"CPC 比上一周期涨了 {jump:.0f}%（{yesterday['cpc']:.2f} → {camp['cpc']:.2f}）。",
                "intent": None,
            })
    return out


async def board(sids: Optional[List[int]] = None, *, days: int = 7,
                force: bool = False, top: int = 25,
                caller: str = "cockpit") -> Dict[str, Any]:
    """广告看板的全部数据（不含小时曲线，那个按需单独取）。"""
    if not _gw.is_master_enabled():
        raise _gw.LingXingError("领星集成未启用（总开关关闭）")
    days = max(1, min(int(days), 60))
    stores, skipped = await _stores(sids)
    cfg = _cfg()
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    totals, prev_totals = _bucket(), _bucket()
    by_store: Dict[int, Dict[str, float]] = {}
    by_campaign: Dict[str, Dict[str, Any]] = {}
    prev_by_campaign: Dict[str, Dict[str, float]] = {}
    by_day: Dict[str, Dict[str, float]] = {}
    today_spend: Dict[str, float] = {}
    targets_by_store: Dict[int, Dict[str, Any]] = {}
    errors: List[Dict[str, str]] = []

    for sid, store in stores.items():
        config = await _campaign_config(sid, force)
        try:
            targets_by_store[sid] = await _targets_for(sid)
        except Exception as exc:  # noqa: BLE001
            targets_by_store[sid] = {"margin": None, "breakeven_acos": None,
                                     "target_acos": None, "per_campaign": {},
                                     "note": f"目标ACOS 计算失败：{exc}"}
        sb = by_store.setdefault(sid, _bucket())

        # 今日（用于预算进度）
        for row in await _daily_rows(sid, today, today=True, force=force):
            cid = str(row.get("campaign_id"))
            today_spend[f"{sid}:{cid}"] = today_spend.get(f"{sid}:{cid}", 0.0) + _f(row.get("cost"))

        # 本期 1..days（不含今天，今天数据不完整会把趋势拉歪）+ 上一等长周期
        for offset in range(1, 2 * days + 1):
            day = (now - timedelta(days=offset)).strftime("%Y-%m-%d")
            rows = await _daily_rows(sid, day, today=False, force=force)
            if not rows:
                continue
            if offset > days:
                for row in rows:
                    _add(prev_totals, row)
                    key = f"{sid}:{row.get('campaign_id')}"
                    _add(prev_by_campaign.setdefault(key, _bucket()), row)
                continue
            db = by_day.setdefault(day, _bucket())
            for row in rows:
                cid = str(row.get("campaign_id"))
                key = f"{sid}:{cid}"
                _add(totals, row)
                _add(sb, row)
                _add(db, row)
                conf = config.get(cid, {})
                entry = by_campaign.setdefault(key, {
                    "sid": sid, "store": store["name"], "marketplace": store["code"],
                    "currency": store["currency"], "campaign_id": cid,
                    "name": conf.get("name") or cid,
                    "state": conf.get("state", ""), "serving_status": conf.get("serving_status", ""),
                    "daily_budget": conf.get("daily_budget", 0.0),
                    "targeting_type": conf.get("targeting_type", ""),
                    **_bucket(),
                })
                _add(entry, row)

    campaigns: List[Dict[str, Any]] = []
    all_anomalies: List[Dict[str, Any]] = []
    for key, raw in by_campaign.items():
        sid = raw["sid"]
        tgt = targets_by_store.get(sid, {})
        derived = _derive(raw)
        target_acos = (tgt.get("per_campaign", {}) or {}).get(raw["campaign_id"]) \
            or tgt.get("target_acos")
        camp = {
            **{k: raw[k] for k in ("sid", "store", "marketplace", "currency",
                                   "campaign_id", "name", "state", "serving_status",
                                   "daily_budget", "targeting_type")},
            **derived,
            "target_acos": target_acos,
            "breakeven_acos": tgt.get("breakeven_acos"),
            "today_spend": round(today_spend.get(key, 0.0), 2),
            "budget_used_pct": (round(today_spend.get(key, 0.0) / raw["daily_budget"] * 100, 1)
                                if raw.get("daily_budget") else None),
        }
        camp["acos_vs_target"] = (round(camp["acos"] - target_acos, 4)
                                  if camp["acos"] is not None and target_acos else None)
        camp["health"] = _health(camp["acos"], target_acos, tgt.get("breakeven_acos"),
                                 raw["spend"], raw["sales"])
        prev = prev_by_campaign.get(key)
        prev_derived = _derive(prev) if prev else None
        camp["prev"] = prev_derived
        camp["spend_change_pct"] = _pct_change(raw["spend"], prev["spend"]) if prev else None
        camp["anomalies"] = _anomalies(camp, cfg, prev_derived)
        for a in camp["anomalies"]:
            all_anomalies.append({**a, "sid": sid, "store": raw["store"],
                                  "campaign_id": raw["campaign_id"], "name": raw["name"]})
        campaigns.append(camp)

    campaigns.sort(key=lambda c: c["spend"], reverse=True)
    all_anomalies.sort(key=lambda a: SEVERITY_ORDER.get(a["severity"], 9))

    stores_out = [{"sid": sid, "store": stores[sid]["name"], "marketplace": stores[sid]["code"],
                   "currency": stores[sid]["currency"],
                   "target": targets_by_store.get(sid, {}), **_derive(b)}
                  for sid, b in by_store.items()]
    stores_out.sort(key=lambda s: s["spend"], reverse=True)

    totals_d, prev_d = _derive(totals), _derive(prev_totals)
    return {
        "generated_at": now.isoformat(),
        "source": "lingxing",
        "scope": {"sids": sorted(stores.keys()), "days": days,
                  "store_count": len(stores), "skipped": skipped},
        "totals": totals_d,
        "prev_totals": prev_d if prev_totals["spend"] or prev_totals["sales"] else None,
        "delta": {
            "spend_pct": _pct_change(totals["spend"], prev_totals["spend"]),
            "sales_pct": _pct_change(totals["sales"], prev_totals["sales"]),
            "orders_pct": _pct_change(totals["orders"], prev_totals["orders"]),
            "acos_delta": (round(totals_d["acos"] - prev_d["acos"], 4)
                           if totals_d["acos"] is not None and prev_d["acos"] is not None else None),
        },
        "by_store": stores_out,
        "by_campaign": campaigns[:max(1, int(top))],
        "campaign_count": len(campaigns),
        "trend": [{"date": d, **_derive(b)} for d, b in sorted(by_day.items())],
        "anomalies": all_anomalies,
        "errors": errors,
    }


async def hourly(sid: int, campaign_ids: List[str], *, date: Optional[str] = None,
                 force: bool = False) -> Dict[str, Any]:
    """选中活动当天的 24 小时曲线。

    **一次调用只能取一个活动一天**，所以这里限量 ``MAX_HOURLY_CAMPAIGNS``，
    并且串行走限流器。这是"后台看不到、这里能看到"的那块，但不能因此把
    接口配额烧光。
    """
    if not _gw.is_master_enabled():
        raise _gw.LingXingError("领星集成未启用（总开关关闭）")
    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    is_today = day == datetime.now(timezone.utc).strftime("%Y-%m-%d")
    picked = [str(c) for c in (campaign_ids or [])][:MAX_HOURLY_CAMPAIGNS]
    series: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for cid in picked:
        try:
            res = await _data.fetch_dataset(
                "sp_campaign_hour", {"report_date": day, "campaign_id": cid},
                ttl=_TODAY_TTL_S if is_today else _PAST_TTL_S, force=force,
                caller="ads_board")
        except (_gw.LingXingError, ValueError) as exc:
            errors.append({"campaign_id": cid, "error": str(exc)})
            continue
        points = []
        for row in (res.get("rows") or []):
            points.append({
                "hour": int(_f(row.get("hour"))),
                "spend": round(_f(row.get("cost")), 2),
                "sales": round(_f(row.get("sales")), 2),
                "clicks": int(_f(row.get("clicks"))),
                "impressions": int(_f(row.get("impressions"))),
                "orders": int(_f(row.get("orders"))),
                "acos": _f(row.get("acos")) or None,
                "cpc": _f(row.get("cpc")) or None,
            })
        points.sort(key=lambda p: p["hour"])
        cumulative = 0.0
        for p in points:
            cumulative += p["spend"]
            p["spend_cumulative"] = round(cumulative, 2)
        series.append({"campaign_id": cid, "points": points,
                       "spend_total": round(cumulative, 2)})
    return {"sid": sid, "date": day, "series": series,
            "truncated": len(campaign_ids or []) > MAX_HOURLY_CAMPAIGNS,
            "max_campaigns": MAX_HOURLY_CAMPAIGNS, "errors": errors}
