"""领星 read-data layer: a config-driven dataset registry + local cache.

The 浏览/分析 panels are driven by :data:`READ_DATASETS` — each entry maps a
friendly dataset key to a real OpenAPI read route, its parameter schema (for the
UI form), and the columns worth showing. Fetches go through the gateway
(:func:`lingxing_service.call_openapi_read`, so master-switch + rate-limit +
audit all apply) and are cached in ``lingxing_cache`` to respect the API rate
limit and make repeat views instant.

Adding a dataset = one registry entry; no panel code changes.
"""
from __future__ import annotations

import hashlib
import logging
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.services import lingxing_service as _gw

logger = logging.getLogger("ivyea.services.lingxing_data")

# Default cache freshness (seconds). Panels can force-refresh.
_DEFAULT_TTL_S = 1800


def _col(key: str, label: str) -> Dict[str, str]:
    return {"key": key, "label": label}


def _promo_dataset(key: str, label: str, route: str, *,
                   extra_columns: List[Dict[str, str]],
                   hint: str = "") -> Dict[str, Any]:
    """四个促销活动列表接口共用的规格（形状完全一致，只有返回字段不同）。

    窗口默认「过去 30 天 ~ 未来 60 天」：倒计时要看的是**还没结束**的活动，
    但已经开始的活动其 start_date 在过去，所以窗口必须两头都留。
    领星限制单次查询跨度 ≤90 天，30+60=90 正好贴着上限。
    """
    return {
        "label": f"促销·{label}", "group": "促销", "route": route, "method": "POST",
        "params": [
            {"name": "start_date", "type": "date", "default": "-30d", "label": "活动起(≤90天跨度)"},
            {"name": "end_date", "type": "date", "default": "+60d", "label": "活动止"},
            {"name": "sids", "type": "sids", "label": "店铺SID(逗号分隔,可空=全部)"},
            {"name": "length", "type": "int", "default": 200}, {"name": "offset", "type": "int", "default": 0},
        ],
        "columns": [_col("promotion_id", "活动ID"), _col("name", "名称"),
                    _col("origin_status", "平台状态"),
                    _col("promotion_start_time", "开始"), _col("promotion_end_time", "结束"),
                    *extra_columns,
                    _col("sales_amount", "销售额"), _col("sales_volume", "销量"),
                    _col("last_sync_time", "最后同步")],
        "hint": hint,
        "promo_kind": key,
    }


# dataset key -> spec. ``params`` types: string|int|date|sids/ints (csv→list[int]).
# ``date`` defaults accept relative tokens like "-1d"/"-7d" resolved at call time.
READ_DATASETS: Dict[str, Dict[str, Any]] = {
    "sellers": {
        "label": "店铺列表", "group": "基础", "route": "/erp/sc/data/seller/lists",
        "method": "GET", "params": [], "row_key": "sid",
        "columns": [_col("sid", "SID"), _col("name", "店铺"), _col("marketplace", "站点"),
                    _col("country", "国家"), _col("seller_id", "SellerID"), _col("region", "区域")],
        "hint": "所有可访问店铺；其它数据集的 sid 来自这里。",
    },
    "fba_stock": {
        "label": "FBA 库存", "group": "库存", "route": "/erp/sc/routing/fba/fbaStock/fbaList",
        "method": "POST",
        "params": [
            {"name": "sid", "required": True, "type": "string", "label": "店铺SID(逗号分隔)"},
            {"name": "length", "type": "int", "default": 50, "label": "条数"},
            {"name": "offset", "type": "int", "default": 0, "label": "偏移"},
        ],
        "columns": [_col("msku", "MSKU"), _col("asin", "ASIN"), _col("product_name", "品名"),
                    _col("afn_fulfillable_quantity", "可售"), _col("total_fulfillable_quantity", "总可用"),
                    _col("afn_inbound_shipped_quantity", "在途"), _col("afn_unsellable_quantity", "不可售")],
    },
    "sp_campaigns": {
        "label": "SP 广告活动", "group": "广告", "route": "/pb/openapi/newad/spCampaigns",
        "method": "POST",
        "params": [
            {"name": "sid", "required": True, "type": "int", "label": "店铺SID"},
            {"name": "state", "type": "string", "label": "状态(enabled/paused/archived)"},
            {"name": "length", "type": "int", "default": 50}, {"name": "offset", "type": "int", "default": 0},
        ],
        "columns": [_col("campaign_id", "活动ID"), _col("name", "活动名"), _col("state", "状态"),
                    _col("daily_budget", "日预算"), _col("targeting_type", "投放")],
        "hint": "广告活动的预算/状态 —— 也是后续受控写操作的前置数据。",
    },
    "sp_adgroups": {
        "label": "SP 广告组", "group": "广告", "route": "/pb/openapi/newad/spAdGroups",
        "method": "POST",
        "params": [
            {"name": "sid", "required": True, "type": "int", "label": "店铺SID"},
            {"name": "state", "type": "string", "label": "状态(enabled/paused/archived)"},
            {"name": "length", "type": "int", "default": 100}, {"name": "offset", "type": "int", "default": 0},
        ],
        "columns": [_col("ad_group_id", "广告组ID"), _col("name", "名称"), _col("default_bid", "默认竞价"),
                    _col("state", "状态"), _col("campaign_id", "活动ID")],
        "hint": "广告组默认竞价 —— 调默认竞价(adgroup_bid)的目标ID与当前值看这里。",
    },
    "sp_keywords": {
        "label": "SP 关键词", "group": "广告", "route": "/pb/openapi/newad/spKeywords",
        "method": "POST",
        "params": [
            {"name": "sid", "required": True, "type": "int", "label": "店铺SID"},
            {"name": "state", "type": "string", "label": "状态(enabled/paused/archived)"},
            {"name": "length", "type": "int", "default": 100}, {"name": "offset", "type": "int", "default": 0},
        ],
        "columns": [_col("keyword_id", "关键词ID"), _col("keyword_text", "词"), _col("match_type", "匹配"),
                    _col("bid", "竞价"), _col("state", "状态"), _col("campaign_id", "活动ID")],
        "hint": "关键词竞价 —— 调 bid(keyword_bid)的目标ID与当前竞价看这里。",
    },
    "sp_targets": {
        "label": "SP 定向", "group": "广告", "route": "/pb/openapi/newad/spTargets",
        "method": "POST",
        "params": [
            {"name": "sid", "required": True, "type": "int", "label": "店铺SID"},
            {"name": "state", "type": "string", "label": "状态(enabled/paused/archived)"},
            {"name": "length", "type": "int", "default": 100}, {"name": "offset", "type": "int", "default": 0},
        ],
        "columns": [_col("target_id", "定向ID"), _col("bid", "竞价"), _col("state", "状态"),
                    _col("expression", "表达式"), _col("campaign_id", "活动ID")],
        "hint": "定向竞价 —— 调 bid(target_bid)的目标ID与当前竞价看这里。",
    },
    "sp_campaign_report": {
        "label": "SP 活动报表", "group": "广告", "route": "/pb/openapi/newad/spCampaignReports",
        "method": "POST",
        "params": [
            {"name": "sid", "required": True, "type": "int", "label": "店铺SID"},
            {"name": "report_date", "required": True, "type": "date", "default": "-1d", "label": "报表日期"},
            {"name": "length", "type": "int", "default": 50}, {"name": "offset", "type": "int", "default": 0},
        ],
        "columns": [_col("campaign_id", "活动ID"), _col("impressions", "曝光"), _col("clicks", "点击"),
                    _col("cost", "花费"), _col("orders", "订单"), _col("sales", "销售额")],
    },
    "sp_product_ads": {
        "label": "SP 投放商品", "group": "广告", "route": "/pb/openapi/newad/spProductAds",
        "method": "POST",
        "params": [
            {"name": "sid", "required": True, "type": "int", "label": "店铺SID"},
            {"name": "state", "type": "string", "label": "状态"},
            {"name": "length", "type": "int", "default": 200}, {"name": "offset", "type": "int", "default": 0},
        ],
        "columns": [_col("ad_id", "广告ID"), _col("asin", "ASIN"), _col("sku", "SKU"),
                    _col("campaign_id", "活动ID"), _col("ad_group_id", "广告组ID"), _col("state", "状态")],
        "hint": "活动投放的 ASIN —— 用于把活动映射到产品毛利(per-campaign 目标ACOS)。",
    },
    "sp_keyword_report": {
        "label": "SP 关键词报表", "group": "广告", "route": "/pb/openapi/newad/spKeywordReports",
        "method": "POST",
        "params": [
            {"name": "sid", "required": True, "type": "int", "label": "店铺SID"},
            {"name": "report_date", "required": True, "type": "date", "default": "-1d", "label": "报表日期"},
            {"name": "length", "type": "int", "default": 300}, {"name": "offset", "type": "int", "default": 0},
        ],
        "columns": [_col("keyword_id", "关键词ID"), _col("keyword_text", "词"), _col("match_type", "匹配"),
                    _col("impressions", "曝光"), _col("clicks", "点击"), _col("cost", "花费"),
                    _col("orders", "订单"), _col("sales", "销售额")],
        "hint": "关键词级表现 —— 降/加 bid 的依据。",
    },
    "sp_target_report": {
        "label": "SP 定向报表", "group": "广告", "route": "/pb/openapi/newad/spTargetReports",
        "method": "POST",
        "params": [
            {"name": "sid", "required": True, "type": "int", "label": "店铺SID"},
            {"name": "report_date", "required": True, "type": "date", "default": "-1d", "label": "报表日期"},
            {"name": "length", "type": "int", "default": 300}, {"name": "offset", "type": "int", "default": 0},
        ],
        "columns": [_col("target_id", "定向ID"), _col("targeting_expression", "表达式"),
                    _col("impressions", "曝光"), _col("clicks", "点击"), _col("cost", "花费"),
                    _col("orders", "订单"), _col("sales", "销售额")],
        "hint": "定向级表现 —— 定向 bid 的依据。",
    },
    "sp_search_term_report": {
        "label": "SP 搜索词报表", "group": "广告", "route": "/pb/openapi/newad/queryWordReports",
        "method": "POST",
        "params": [
            {"name": "sid", "required": True, "type": "int", "label": "店铺SID"},
            {"name": "report_date", "required": True, "type": "date", "default": "-1d", "label": "报表日期"},
            {"name": "length", "type": "int", "default": 300}, {"name": "offset", "type": "int", "default": 0},
        ],
        "columns": [_col("query", "搜索词"), _col("target_text", "投放词"), _col("match_type", "匹配"),
                    _col("impressions", "曝光"), _col("clicks", "点击"), _col("cost", "花费"),
                    _col("orders", "订单"), _col("sales", "销售额"), _col("campaign_id", "活动ID")],
        "hint": "真实搜索词 —— 否词(高点击0单)与收割(≥3单)的依据。",
    },
    "asin_profit": {
        "label": "ASIN 利润", "group": "财务", "route": "/bd/profit/statistics/open/asin/list",
        "method": "POST",
        "params": [
            {"name": "sids", "type": "sids", "label": "店铺SID(逗号分隔,可空=全部)"},
            {"name": "startDate", "required": True, "type": "date", "default": "-7d", "label": "起始(≤7天跨度)"},
            {"name": "endDate", "required": True, "type": "date", "default": "-1d", "label": "结束"},
            {"name": "length", "type": "int", "default": 50}, {"name": "offset", "type": "int", "default": 0},
        ],
        "columns": [_col("asin", "ASIN"), _col("storeName", "店铺"), _col("totalSalesAmount", "销售额"),
                    _col("totalAdsCost", "广告花费"), _col("grossProfit", "毛利"), _col("grossRate", "毛利率")],
    },
    # --- 促销活动 ------------------------------------------------------------
    # 领星这块数据**不是 API 直连亚马逊拿的**，是「LINGXING助手」浏览器插件抓回
    # 去的（官方原话：需要登录助手保持后台在线）。所以每条记录都带 last_sync_time，
    # 上层必须把新鲜度如实显示出来 —— 插件掉线时倒计时会静静地停在旧值上，
    # 那比没有倒计时更危险。
    #
    # 四个接口形状完全一致（start_date/end_date/sids/offset/length，窗口 ≤90 天），
    # 差别只在返回字段，所以用一个工厂生成。
    "promo_coupon": _promo_dataset(
        "coupon", "优惠券", "/basicOpen/promotionalActivities/coupon/list",
        extra_columns=[_col("discount", "折扣"), _col("budget", "预算"), _col("cost", "已花"),
                       _col("draw_quantity", "领取"), _col("exchange_quantity", "兑换")],
        hint="优惠券活动：起止时间用于倒计时，budget/cost 用于预算耗尽预警。",
    ),
    "promo_seckill": _promo_dataset(
        "seckill", "秒杀(BD/LD)", "/basicOpen/promotionalActivities/secKill/list",
        extra_columns=[_col("promotion_type", "类型"), _col("product_quantity", "商品数"),
                       _col("seckill_fee", "秒杀费"), _col("sold_rate", "售出率"),
                       _col("page_view", "浏览量")],
        hint="秒杀：promotion_type 1=Best Deal 2=Lightning Deal。",
    ),
    "promo_manage": _promo_dataset(
        "manage", "管理促销", "/basicOpen/promotionalActivities/manage/list",
        extra_columns=[_col("promotion_type", "类型"), _col("product_quantity", "商品数")],
        hint="管理促销。注意路由里的 manage 是业务名词不是动词 —— 读写判定见 "
             "lingxing_openapi.classify_route 的注释。",
    ),
    "promo_vip_discount": _promo_dataset(
        "vip_discount", "会员/Prime 折扣", "/basicOpen/promotionalActivities/vipDiscount/list",
        extra_columns=[_col("customer_target", "面向人群"), _col("product_quantity", "商品数")],
        hint="Prime 专享折扣 / 会员折扣。",
    ),
    "promo_listing": {
        "label": "ASIN 维度促销", "group": "促销", "route": "/basicOpen/promotion/listingList",
        "method": "POST",
        "params": [
            {"name": "site_date", "required": True, "type": "date", "default": "0d", "label": "站点日期"},
            {"name": "start_time", "type": "date", "default": "-30d", "label": "活动起(≤90天跨度)"},
            {"name": "end_time", "type": "date", "default": "+60d", "label": "活动止"},
            {"name": "sids", "type": "sids", "label": "店铺SID(逗号分隔,可空=全部)"},
            {"name": "status", "type": "ints", "default": "0,1,2,3", "label": "促销状态(0其他 1进行中 2已过期 3未开始)"},
            {"name": "product_status", "type": "ints", "default": "1", "label": "商品状态(-1删除 0停售 1在售)"},
            {"name": "promotion_category", "type": "ints", "default": "1,2,3,4",
             "label": "促销类型(1券 2秒杀 3管理促销 4会员折扣)"},
            {"name": "length", "type": "int", "default": 200}, {"name": "offset", "type": "int", "default": 0},
        ],
        "columns": [_col("asin", "ASIN"), _col("item_name", "标题"), _col("seller_sku", "MSKU"),
                    _col("store_name", "店铺"), _col("sales_price", "优惠价"),
                    _col("afn_fulfillable_quantity", "FBA可售")],
        "hint": "**按 ASIN** 列促销窗口（promotion_list 里带每条促销的起止时间与类型）—— "
                "「哪个 ASIN 的券要结束了」只能从这里拿，活动列表接口不带 ASIN。",
    },
    # --- 广告·小时级 ----------------------------------------------------------
    # 亚马逊后台看小时数据很难受，这是驾驶舱「比后台好用」的底牌之一。
    # 代价：一次调用只能取**一个活动**一天的 24 个点，撞限流很快 ——
    # 只能后台同步进缓存，绝不能让页面直连。
    "sp_campaign_hour": {
        "label": "SP 活动小时数据", "group": "广告", "route": "/pb/openapi/newad/spCampaignHourData",
        "method": "POST",
        "params": [
            {"name": "report_date", "required": True, "type": "date", "default": "0d", "label": "报表日期(近60天)"},
            {"name": "campaign_id", "required": True, "type": "int", "label": "广告活动ID"},
        ],
        "columns": [_col("hour", "小时"), _col("impressions", "曝光"), _col("clicks", "点击"),
                    _col("cost", "花费"), _col("orders", "订单"), _col("sales", "销售额"),
                    _col("acos", "ACOS"), _col("cpc", "CPC")],
        "hint": "单个活动当天 24 小时的曲线。按活动逐个取，注意限流。",
    },
}


def catalog() -> List[Dict[str, Any]]:
    """Registry view for the UI (key/label/group/params/columns/hint)."""
    out = []
    for key, d in READ_DATASETS.items():
        out.append({
            "key": key, "label": d["label"], "group": d.get("group", ""),
            "params": d.get("params", []), "columns": d.get("columns", []),
            "hint": d.get("hint", ""), "method": d["method"],
        })
    return out


def _resolve_date(token: Any) -> Any:
    """Resolve relative date tokens like '-1d'/'-7d'/'0d' to YYYY-MM-DD."""
    if not isinstance(token, str) or not token:
        return token
    t = token.strip()
    if t.endswith("d") and (t[:-1].lstrip("+-").isdigit()):
        days = int(t[:-1].lstrip("+"))
        if t.startswith("-"):
            days = -abs(days)
        return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")
    return token


def _coerce(spec_params: List[Dict[str, Any]], given: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + coerce user params against the dataset schema; fill defaults."""
    out: Dict[str, Any] = {}
    for p in spec_params:
        name, typ = p["name"], p.get("type", "string")
        val = given.get(name, p.get("default"))
        if typ == "date":
            val = _resolve_date(val)
        if val in (None, ""):
            if p.get("required"):
                raise ValueError(f"缺少必填参数: {name}（{p.get('label', name)}）")
            continue
        if typ == "int":
            try:
                val = int(val)
            except (TypeError, ValueError):
                raise ValueError(f"参数 {name} 需为整数")
        elif typ in ("sids", "ints"):
            if isinstance(val, str):
                val = [int(x) for x in val.replace("，", ",").split(",") if x.strip().isdigit()]
            elif isinstance(val, list):
                val = [int(x) for x in val]
        out[name] = val
    return out


def _params_hash(dataset: str, params: Dict[str, Any]) -> str:
    raw = dataset + "|" + json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_get(dataset: str, ph: str, ttl: int) -> Optional[Dict[str, Any]]:
    try:
        conn = _gw.connect()
        try:
            cur = conn.execute(
                "SELECT payload_json, synced_at FROM lingxing_cache WHERE dataset=? AND params_hash=?",
                (dataset, ph))
            row = cur.fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    if not row:
        return None
    payload_json, synced_at = row
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(synced_at)).total_seconds()
    except Exception:
        age = ttl + 1
    if age > ttl:
        return None
    try:
        return {"payload": json.loads(payload_json), "synced_at": synced_at}
    except Exception:
        return None


def _cache_put(dataset: str, ph: str, params: Dict[str, Any], payload: Any) -> str:
    ts = datetime.now(timezone.utc).isoformat()
    try:
        conn = _gw.connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO lingxing_cache "
                "(dataset, params_hash, params_json, payload_json, synced_at) VALUES (?,?,?,?,?)",
                (dataset, ph, json.dumps(params, ensure_ascii=False, default=str),
                 json.dumps(payload, ensure_ascii=False, default=str), ts))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.debug("_gw.connect 失败（旁路，已忽略）", exc_info=True)
    return ts


def _extract_rows(payload: Any) -> Any:
    """Best-effort pull of the row list from a LingXing response envelope."""
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for k in ("list", "records", "rows", "items"):
                if isinstance(data.get(k), list):
                    return data[k]
            return data
        return data
    return payload


async def fetch_dataset(dataset: str, params: Optional[Dict[str, Any]] = None, *,
                        force: bool = False, ttl: Optional[int] = None,
                        caller: str = "panel") -> Dict[str, Any]:
    """Resolve params → serve fresh cache or call the gateway → cache → return.

    Returns ``{dataset, rows, count, synced_at, cached, params}``.
    """
    spec = READ_DATASETS.get(dataset)
    if not spec:
        raise ValueError(f"未知数据集: {dataset}")
    resolved = _coerce(spec.get("params", []), params or {})
    ph = _params_hash(dataset, resolved)
    ttl = _DEFAULT_TTL_S if ttl is None else ttl

    if not force:
        hit = _cache_get(dataset, ph, ttl)
        if hit is not None:
            rows = _extract_rows(hit["payload"])
            return {"dataset": dataset, "rows": rows,
                    "count": len(rows) if isinstance(rows, list) else None,
                    "synced_at": hit["synced_at"], "cached": True, "params": resolved}

    payload = await _gw.call_openapi_read(spec["route"], resolved,
                                          method=spec["method"], caller=caller)
    synced_at = _cache_put(dataset, ph, resolved, payload)
    rows = _extract_rows(payload)
    return {"dataset": dataset, "rows": rows,
            "count": len(rows) if isinstance(rows, list) else None,
            "synced_at": synced_at, "cached": False, "params": resolved}
