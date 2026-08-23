"""促销活动看板：把领星四类活动 + ASIN 维度促销归一成一份倒计时清单。

驾驶舱要回答的三个问题，决定了这个模块的形状：

1. **我报的活动什么时候结束？** —— 四类活动（优惠券 / 秒杀 / 管理促销 / 会员折扣）
   各有各的接口和字段名，归一成同一个 ``PromoItem`` 形状，界面只认一种。
2. **是哪个 ASIN 的券要结束了？** —— 活动列表接口**不带 ASIN**，只有
   ``/basicOpen/promotion/listingList`` 是按 listing 维度返回的，里面每个 ASIN
   挂着自己的 ``promotion_list``。所以 ASIN 是从那一份按 promotion_id 反挂回来的。
3. **这些数还能信吗？** —— 见下面这条。

两条必须如实暴露的现实
----------------------

* **数据是浏览器插件抓的，不是 API 直连。** 领星官方说明：促销模块的数据由
  「LINGXING助手」同步，需要保持助手登录在线。插件掉线时接口照样返回 200 和
  旧数据 —— 倒计时会安安静静地停在过期的值上。所以每条都带 ``last_sync_time``，
  汇总里给 ``freshness``，界面必须显示，不能只画个好看的倒计时。
* **时间是站点当地时间的裸字符串。** ``"2026-08-24 23:59:00"`` 不带时区。
  按服务器时区去算倒计时，UK 的活动会差 7~8 小时 —— 正好是"以为还有一天、
  其实已经结束"这种最坏的错法。这里一律按店铺的 marketplace_id 查时区后再算。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.core import marketplaces as _mkt
from app.services import lingxing_data as _data
from app.services import lingxing_service as _gw

logger = logging.getLogger("ivyea.services.promotions")

#: 促销数据变化不快（插件同步周期以小时计），但倒计时要跟手 —— 10 分钟。
PROMO_TTL_S = 600

#: 四类活动 → (数据集, 中文名, 图标)。顺序即界面默认顺序。
PROMO_KINDS: Dict[str, Dict[str, str]] = {
    "coupon": {"dataset": "promo_coupon", "label": "优惠券", "icon": "🎟"},
    "seckill": {"dataset": "promo_seckill", "label": "秒杀", "icon": "⚡"},
    "manage": {"dataset": "promo_manage", "label": "管理促销", "icon": "🏷"},
    "vip_discount": {"dataset": "promo_vip_discount", "label": "会员折扣", "icon": "👑"},
}

#: listingList 的 category 编码 → 我们的 kind
_CATEGORY_TO_KIND = {1: "coupon", 2: "seckill", 3: "manage", 4: "vip_discount"}

#: 平台原始状态 → (中文, 是否算"还在生效链路上")。
#: 取消/过期/失败/禁显的活动不该出现在倒计时里 —— 它们没有"还剩多久"。
_STATUS: Dict[str, tuple] = {
    "ACTIVE": ("进行中", True),
    "RUNNING": ("生效中", True),
    "APPROVED": ("已通过·未开始", True),
    "SUBMITTED": ("已提交", True),
    "DRAFT": ("草稿", True),
    "EXPIRING SOON": ("即将过期", True),
    "NEEDS ACTION": ("需要处理", True),
    "SUPPRESSED": ("被抑制", True),
    "CANCELED": ("已取消", False),
    "CANCELLED": ("已取消", False),
    "EXPIRED": ("已过期", False),
    "ENDED": ("已结束", False),
    "FAILED": ("失败", False),
    "DISMISSED": ("禁止显示", False),
}

#: 秒杀类型编码
_SECKILL_TYPE = {1: "Best Deal", 2: "Lightning Deal"}

_MONEY_RE = re.compile(r"-?[\d.]+")


def _money(value: Any) -> Optional[float]:
    """把 ``"JP¥10,084.0"`` / ``"0.00"`` / ``12.3`` 解析成数字。

    领星把货币符号拼在金额字符串里（``budget`` 带 icon，``cost`` 不带），
    所以不能直接 float()。解析不出来返回 None 而不是 0 —— "没有预算这个概念"
    和"预算是 0"在界面上必须能区分开。
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("，", "")
    match = _MONEY_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_site_time(raw: Any, tz: Any) -> Optional[datetime]:
    """站点当地时间的裸字符串 → 带时区的 datetime。"""
    if not raw:
        return None
    text = str(raw).strip()
    if not text or text.startswith("0000"):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def _status_of(raw: Any) -> tuple:
    key = str(raw or "").strip().upper()
    return _STATUS.get(key, (str(raw or "") or "未知", True))


def _phase(start: Optional[datetime], end: Optional[datetime], alive: bool,
           now: datetime) -> str:
    if not alive:
        return "closed"
    if end and now >= end:
        return "ended"
    if start and now < start:
        return "upcoming"
    if start or end:
        return "running"
    return "unknown"


def _seconds(target: Optional[datetime], now: datetime) -> Optional[int]:
    if target is None:
        return None
    return int((target - now).total_seconds())


def _normalize(kind: str, row: Dict[str, Any], store: Dict[str, Any],
               now: datetime) -> Dict[str, Any]:
    tz = _mkt.tzinfo(store.get("marketplace_id"), country=store.get("country", ""))
    start = _parse_site_time(row.get("promotion_start_time"), tz)
    end = _parse_site_time(row.get("promotion_end_time"), tz)
    synced = _parse_site_time(row.get("last_sync_time"), tz)
    status_label, alive = _status_of(row.get("origin_status"))
    budget = _money(row.get("budget"))
    cost = _money(row.get("cost"))
    used_pct = None
    if budget and budget > 0 and cost is not None:
        used_pct = round(cost / budget * 100, 1)

    promotion_id = str(row.get("promotion_id") or "")
    item: Dict[str, Any] = {
        "id": f"{kind}:{store.get('sid')}:{promotion_id}",
        "promotion_id": promotion_id,
        "kind": kind,
        "kind_label": PROMO_KINDS.get(kind, {}).get("label", kind),
        "name": row.get("name") or row.get("description") or "(未命名)",
        "sid": store.get("sid"),
        "store": store.get("name"),
        "marketplace": store.get("code"),
        "country": store.get("country"),
        "tz": store.get("tz"),
        "currency_icon": row.get("currency_icon") or "",
        "status_raw": row.get("origin_status") or "",
        "status_label": status_label,
        "start_at": start.isoformat() if start else None,
        "end_at": end.isoformat() if end else None,
        "start_local": str(row.get("promotion_start_time") or ""),
        "end_local": str(row.get("promotion_end_time") or ""),
        "seconds_to_start": _seconds(start, now),
        "seconds_to_end": _seconds(end, now),
        "phase": _phase(start, end, alive, now),
        "budget": budget,
        "cost": cost,
        "budget_used_pct": used_pct,
        "sales_amount": _money(row.get("sales_amount")),
        "sales_volume": _money(row.get("sales_volume")),
        "discount": row.get("discount") or "",
        "draw_quantity": _money(row.get("draw_quantity")),
        "exchange_quantity": _money(row.get("exchange_quantity")),
        "product_quantity": row.get("product_quantity"),
        "last_sync_time": synced.isoformat() if synced else None,
        "sync_age_hours": (round((now - synced).total_seconds() / 3600, 1)
                           if synced else None),
        "asins": [],
    }
    if kind == "seckill":
        item["type_label"] = _SECKILL_TYPE.get(_safe_int(row.get("promotion_type")), "")
        item["sold_rate"] = _money(row.get("sold_rate"))
        item["page_view"] = _money(row.get("page_view"))
        item["seckill_fee"] = _money(row.get("seckill_fee"))
    return item


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _stores(sids: Optional[List[int]] = None) -> Dict[int, Dict[str, Any]]:
    sellers = await _data.fetch_dataset("sellers", {}, caller="promotions")
    out: Dict[int, Dict[str, Any]] = {}
    for row in (sellers.get("rows") or []):
        meta = _mkt.store_meta(row)
        sid = meta["sid"]
        if not isinstance(sid, int):
            continue
        if sids and sid not in sids:
            continue
        out[sid] = meta
    return out


def _asin_index(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """listingList 的行 → {promotion_id: [ASIN 条目]}。

    活动列表接口不返回 ASIN，这是唯一能把"哪个 ASIN"补上的来源。
    """
    index: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows or []:
        entry = {
            "asin": row.get("asin") or "",
            "title": row.get("item_name") or "",
            "msku": row.get("seller_sku") or "",
            "image": row.get("small_image_url") or "",
            "url": row.get("asin_url") or "",
            "sales_price": _money(row.get("sales_price")),
            "stock": _safe_int(row.get("afn_fulfillable_quantity")),
        }
        for promo in (row.get("promotion_list") or []):
            pid = str(promo.get("promotion_id") or "")
            if not pid:
                continue
            bucket = index.setdefault(pid, [])
            if not any(x["asin"] == entry["asin"] for x in bucket):
                bucket.append(entry)
    return index


def _orphan_items(rows: Iterable[Dict[str, Any]], stores: Dict[int, Dict[str, Any]],
                  known_ids: set, now: datetime) -> List[Dict[str, Any]]:
    """listingList 里出现、但四个活动列表都没有的促销，也要显示。

    两份数据的同步节奏不一定一致，漏掉的那条恰恰可能是"明天就结束的那个券"。
    宁可多一条来源标成 listing 的记录，也不要静默丢。
    """
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        sid = _safe_int(row.get("sid"))
        store = stores.get(sid) or {"sid": sid, "name": row.get("store_name") or "",
                                    "code": "", "country": "", "tz": "UTC",
                                    "marketplace_id": ""}
        tz = _mkt.tzinfo(store.get("marketplace_id"), country=store.get("country", ""))
        for promo in (row.get("promotion_list") or []):
            pid = str(promo.get("promotion_id") or "")
            kind = _CATEGORY_TO_KIND.get(_safe_int(promo.get("category")) or 0, "coupon")
            key = f"{kind}:{sid}:{pid}"
            if not pid or key in known_ids or key in out:
                continue
            start = _parse_site_time(promo.get("promotion_start_time"), tz)
            end = _parse_site_time(promo.get("promotion_end_time"), tz)
            status_label, alive = _status_of(promo.get("origin_status"))
            out[key] = {
                "id": key, "promotion_id": pid, "kind": kind,
                "kind_label": promo.get("category_text")
                or PROMO_KINDS.get(kind, {}).get("label", kind),
                "name": promo.get("name") or "(未命名)",
                "sid": sid, "store": store.get("name"), "marketplace": store.get("code"),
                "country": store.get("country"), "tz": store.get("tz"),
                "currency_icon": row.get("currency_icon") or "",
                "status_raw": promo.get("origin_status") or "",
                "status_label": status_label,
                "start_at": start.isoformat() if start else None,
                "end_at": end.isoformat() if end else None,
                "start_local": str(promo.get("promotion_start_time") or ""),
                "end_local": str(promo.get("promotion_end_time") or ""),
                "seconds_to_start": _seconds(start, now),
                "seconds_to_end": _seconds(end, now),
                "phase": _phase(start, end, alive, now),
                "budget": None, "cost": None, "budget_used_pct": None,
                "sales_amount": None, "sales_volume": None,
                "discount": promo.get("promotion_type_text") or "",
                "last_sync_time": None, "sync_age_hours": None,
                "asins": [], "from_listing_only": True,
            }
    return list(out.values())


def _freshness(items: List[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    """插件同步新鲜度。**这块必须显示** —— 见模块开头。"""
    ages = [i["sync_age_hours"] for i in items if i.get("sync_age_hours") is not None]
    if not ages:
        return {"known": False, "stale": False, "age_hours": None,
                "hint": "本次没有取到同步时间；促销数据由「LINGXING助手」插件同步，"
                        "插件离线时数据会停更。"}
    freshest = min(ages)
    return {
        "known": True,
        "age_hours": freshest,
        "stale": freshest > 24,
        "hint": ("促销数据由「LINGXING助手」浏览器插件同步。最近一次同步在 "
                 f"{freshest} 小时前。"
                 + ("插件可能已离线，倒计时可能停在旧值上。" if freshest > 24 else "")),
    }


async def board(sids: Optional[List[int]] = None, *, horizon_days: int = 30,
                include_ended: bool = False, force: bool = False,
                caller: str = "cockpit") -> Dict[str, Any]:
    """驾驶舱「促销日历」的全部数据。

    ``horizon_days`` 只影响**列表里留哪些**（未来这么多天内开始或结束的），
    不影响向领星要的窗口 —— 窗口固定 90 天（接口上限），少调几次接口。
    """
    if not _gw.is_master_enabled():
        raise _gw.LingXingError("领星集成未启用（总开关关闭）")

    now = datetime.now(timezone.utc)
    stores = await _stores(sids)
    if not stores:
        return {"generated_at": now.isoformat(), "source": "lingxing", "items": [],
                "stores": [], "summary": _summary([], now),
                "freshness": _freshness([], now)}

    sid_list = sorted(stores.keys())
    ttl = 0 if force else PROMO_TTL_S
    items: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    for kind, spec in PROMO_KINDS.items():
        try:
            res = await _data.fetch_dataset(spec["dataset"], {"sids": sid_list},
                                            ttl=ttl, force=force, caller=caller)
        except (_gw.LingXingError, ValueError) as exc:
            errors.append({"source": spec["dataset"], "error": str(exc)})
            continue
        for row in (res.get("rows") or []):
            sid = _safe_int(row.get("sid"))
            store = stores.get(sid)
            if store is None:
                continue
            items.append(_normalize(kind, row, store, now))

    listing_rows: List[Dict[str, Any]] = []
    try:
        res = await _data.fetch_dataset("promo_listing", {"sids": sid_list},
                                        ttl=ttl, force=force, caller=caller)
        listing_rows = res.get("rows") or []
    except (_gw.LingXingError, ValueError) as exc:
        errors.append({"source": "promo_listing", "error": str(exc)})

    asins = _asin_index(listing_rows)
    for item in items:
        item["asins"] = asins.get(item["promotion_id"], [])
        item["asin_count"] = len(item["asins"])

    known = {i["id"] for i in items}
    for extra in _orphan_items(listing_rows, stores, known, now):
        extra["asins"] = asins.get(extra["promotion_id"], [])
        extra["asin_count"] = len(extra["asins"])
        items.append(extra)

    horizon = now + timedelta(days=max(1, int(horizon_days)))
    kept: List[Dict[str, Any]] = []
    for item in items:
        if item["phase"] in ("ended", "closed") and not include_ended:
            continue
        starts = item.get("seconds_to_start")
        if starts is not None and now + timedelta(seconds=starts) > horizon:
            continue
        kept.append(item)

    # 快结束的排最前：这块面板存在的理由就是"别错过截止时间"。
    kept.sort(key=lambda x: (x.get("seconds_to_end") is None,
                             x.get("seconds_to_end") if x.get("seconds_to_end") is not None else 0))

    return {
        "generated_at": now.isoformat(),
        "source": "lingxing",
        "scope": {"sids": sid_list, "store_count": len(stores),
                  "horizon_days": horizon_days, "include_ended": include_ended},
        "stores": [stores[s] for s in sid_list],
        "items": kept,
        "summary": _summary(kept, now),
        "freshness": _freshness(items, now),
        "errors": errors,
    }


def _summary(items: List[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    def ending_within(hours: int) -> int:
        limit = hours * 3600
        return sum(1 for i in items
                   if i.get("phase") == "running"
                   and i.get("seconds_to_end") is not None
                   and 0 <= i["seconds_to_end"] <= limit)

    budget_risk = [i for i in items
                   if i.get("budget_used_pct") is not None and i["budget_used_pct"] >= 80
                   and i.get("phase") == "running"]
    return {
        "total": len(items),
        "running": sum(1 for i in items if i.get("phase") == "running"),
        "upcoming": sum(1 for i in items if i.get("phase") == "upcoming"),
        "ending_24h": ending_within(24),
        "ending_72h": ending_within(72),
        "budget_risk": len(budget_risk),
        "with_asin": sum(1 for i in items if i.get("asin_count")),
    }
