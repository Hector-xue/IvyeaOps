"""配置驱动的自定义数据源 provider。

对外长得和 ``sellersprite_service`` 一模一样 —— 同名的十个 async 能力函数、
同样的返回结构。路由层拿到它就能当内置源用，卡片和图表不需要知道数据是从哪
台 MCP 服务器来的。

内部就三件事：
  1. 按配置模板渲染工具入参（``{asin}`` / ``{marketplace}`` / ``{month}`` …）；
  2. 调用工具，拆信封；
  3. 按字段映射把对方的字段名翻译成 ops 内部的 provider-neutral 字段名。

**没配的能力不报 500**：返回该能力契约里的"空 + error"结构，卡片照常渲染出一条
说明。半配的数据源能用起来的部分要能用，这是设计目标不是将就。
"""
from __future__ import annotations

import datetime
import logging
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.services import custom_source_mcp as _mcp
from app.services import custom_source_registry as _registry

logger = logging.getLogger("ivyea.services.custom_source_provider")

ProgressFn = Callable[[str, int, int], Awaitable[None]]

_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")


# ── 值处理（与 sellersprite_service 的同名函数行为一致，图表口径才不会分叉）──

def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        cleaned = str(value).replace(",", "").replace("$", "").replace("%", "").strip()
        return float(cleaned) if cleaned else None
    except (TypeError, ValueError):
        return None


def _price_bands(products: List[Dict[str, Any]], buckets: int = 5) -> List[Dict[str, Any]]:
    prices = [p["price"] for p in products if isinstance(p.get("price"), (int, float))]
    if not prices:
        return []
    low, high = min(prices), max(prices)
    if high <= low:
        sales = sum(p.get("est_sales") or 0 for p in products) or None
        return [{"label": f"${low:.0f}", "min": low, "max": high, "count": len(prices), "sales": sales}]
    width = (high - low) / buckets
    out: List[Dict[str, Any]] = []
    for index in range(buckets):
        start = low + index * width
        end = low + (index + 1) * width if index < buckets - 1 else high
        members = [p for p in products
                   if isinstance(p.get("price"), (int, float)) and start <= p["price"] <= end]
        sales = sum(p.get("est_sales") or 0 for p in members) or None
        out.append({
            "label": f"${start:.0f}–${end:.0f}",
            "min": round(start, 2), "max": round(end, 2),
            "count": len(members),
            "sales": round(sales, 1) if isinstance(sales, (int, float)) else None,
        })
    return out


def _day(value: Any) -> Optional[str]:
    """把各种日期写法归一成 YYYY-MM-DD。

    见过的写法：``2024-05-01`` / ``20240501`` / ``202405`` / ``2024年05月``。
    只到月份的补成当月 1 号 —— 折线图按日排布，月度点必须落在固定位置，否则
    同一个月会画出两个点。
    """
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"(\d{4})\D?(\d{1,2})\D?(\d{1,2})?", text)
    if not match:
        return None
    year, month, day = match.group(1), match.group(2), match.group(3)
    try:
        year_i, month_i = int(year), int(month)
        day_i = int(day) if day else 1
        if not 1 <= month_i <= 12 or not 1 <= day_i <= 31:
            return None
        return f"{year_i:04d}-{month_i:02d}-{day_i:02d}"
    except (TypeError, ValueError):
        return None


# ── 路径取值 ────────────────────────────────────────────────────────────────

_INDEX_RE = re.compile(r"^(.*?)\[(\d+)\]$")


def resolve_path(node: Any, path: str) -> Any:
    """点号路径取值，支持 ``a.b[0].c``，支持 ``a||b`` 备选（前一个取不到才试后一个）。

    备选是刚需：同一家服务器不同工具对同一个概念的字段名经常不一致
    （``reviews`` / ``ratings`` / ``reviewCount``），逼用户为每个工具单独配一遍
    映射是没必要的折磨。
    """
    for candidate in str(path or "").split("||"):
        value = _resolve_single(node, candidate.strip())
        if value not in (None, ""):
            return value
    return None


def _resolve_single(node: Any, path: str) -> Any:
    if not path:
        return None
    current = node
    for part in path.split("."):
        part = part.strip()
        if not part:
            continue
        match = _INDEX_RE.match(part)
        index = None
        if match:
            part, index = match.group(1), int(match.group(2))
        if part:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        if index is not None:
            if isinstance(current, list) and index < len(current):
                current = current[index]
            else:
                return None
        if current is None:
            return None
    return current


def map_fields(node: Any, spec: Any) -> Dict[str, Any]:
    """按 ``{目标字段: 路径}`` 把一条记录翻译成 ops 的字段名。

    路径可以写成 ``"price"``，也可以写成 ``{"path": "price", "type": "number"}``。
    ``type`` 省略时按目标字段名推断：数值型字段名（price/bsr/rating…）自动走 _num，
    免得用户为每个字段都写一遍类型。
    """
    out: Dict[str, Any] = {}
    if not isinstance(spec, dict):
        return out
    for target, rule in spec.items():
        if isinstance(rule, dict):
            path = str(rule.get("path") or "")
            kind = str(rule.get("type") or "").lower()
            default = rule.get("default")
        else:
            path, kind, default = str(rule or ""), "", None
        value = resolve_path(node, path)
        if value is None:
            value = default
        if kind == "number" or (not kind and _looks_numeric(target)):
            value = _num(value)
        elif kind == "string" and value is not None:
            value = str(value)
        out[target] = value
    return out


_NUMERIC_HINT = re.compile(
    r"(price|bsr|rank|sales|rating|review|count|volume|search|cpc|units|"
    r"variations|evidence|value|monthly|purchase)", re.IGNORECASE)


def _looks_numeric(name: str) -> bool:
    if name in ("bsr_category", "sub_category", "category_name"):
        return False
    return bool(_NUMERIC_HINT.search(name or ""))


# ── 入参模板 ────────────────────────────────────────────────────────────────

def _recent_month() -> str:
    """最近一个**已完结**的月份（yyyyMM）—— 大多数数据源当月数据是不全的。"""
    return (datetime.date.today().replace(day=1) - datetime.timedelta(days=1)).strftime("%Y%m")


def template_vars(**kwargs: Any) -> Dict[str, Any]:
    today = datetime.date.today()
    month = _recent_month()
    base: Dict[str, Any] = {
        "month": month,
        "month_dash": f"{month[:4]}-{month[4:]}",
        "today": today.isoformat(),
        "days_ago_30": (today - datetime.timedelta(days=30)).isoformat(),
        "days_ago_7": (today - datetime.timedelta(days=7)).isoformat(),
    }
    base.update({k: v for k, v in kwargs.items() if v is not None})
    return base


def render_args(template: Any, variables: Dict[str, Any]) -> Any:
    """递归渲染入参模板。

    整个值就是一个占位符时（``"{top_n}"``）保留原始类型 —— 否则 top_n 会变成
    字符串 "30"，而不少服务器对 size/page 这类参数是**按类型**校验的，传字符串
    要么报错要么静默忽略。
    """
    if isinstance(template, dict):
        return {k: render_args(v, variables) for k, v in template.items()}
    if isinstance(template, list):
        return [render_args(v, variables) for v in template]
    if not isinstance(template, str):
        return template
    exact = re.fullmatch(r"\{(\w+)\}", template.strip())
    if exact:
        return variables.get(exact.group(1))
    return re.sub(r"\{(\w+)\}", lambda m: str(variables.get(m.group(1), "")), template)


# ── Provider ────────────────────────────────────────────────────────────────

class CustomProvider:
    """一个自定义 MCP 数据源。方法签名与 sellersprite_service 逐一对齐。"""

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.id = _registry.full_id(cfg.get("id", ""))
        self.name = str(cfg.get("name") or cfg.get("id") or "自定义数据源")
        self.envelope = str(cfg.get("envelope") or "")

    # -- 基础设施 ------------------------------------------------------------

    def spec(self, capability: str) -> Optional[Dict[str, Any]]:
        spec = (self.cfg.get("capabilities") or {}).get(capability)
        return spec if isinstance(spec, dict) else None

    def has(self, capability: str) -> bool:
        return self.spec(capability) is not None

    def _missing(self, capability: str) -> str:
        return f"「{self.name}」未配置 {capability} 能力（系统配置 → 数据源 → 自定义数据源）"

    async def _call(self, spec: Dict[str, Any], variables: Dict[str, Any]) -> Any:
        async with _mcp.session(self.cfg) as state:
            return await _mcp.call_tool(
                state, str(spec.get("tool")), render_args(spec.get("args") or {}, variables),
            )

    def _at(self, payload: Any, path: str) -> Any:
        """显式路径对「拆完信封的节点」和「原始返回」各试一次。

        用户看着试跑面板里打印的 JSON 写路径，写出来的可能是相对信封内的
        （``top``），也可能是从最外层开始的（``data.top``）。两种都认，否则
        一个路径写法差异就要来回猜好几轮。
        """
        node = resolve_path(_mcp.unwrap(payload, self.envelope), path)
        return node if node is not None else resolve_path(payload, path)

    def _rows(self, payload: Any, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        path = str(spec.get("rows") or "")
        if path:
            node = self._at(payload, path)
            if isinstance(node, list):
                return [r for r in node if isinstance(r, dict)]
            return []
        return _mcp.rows(payload, self.envelope)

    def _record(self, payload: Any, spec: Dict[str, Any]) -> Dict[str, Any]:
        path = str(spec.get("record") or "")
        if path:
            node = self._at(payload, path)
            if isinstance(node, dict):
                return node
            if isinstance(node, list) and node and isinstance(node[0], dict):
                return node[0]
            return {}
        return _mcp.record(payload, self.envelope)

    # -- 市场调研 / 打法推荐：整包采集喂给模型 --------------------------------

    async def _run_steps(self, capability: str, variables: Dict[str, Any],
                         progress: ProgressFn) -> Tuple[Dict[str, Any], List[str]]:
        spec = self.spec(capability)
        if not spec:
            await progress("完成", 1, 1)
            return {}, [self._missing(capability)]
        steps = spec.get("steps") or []
        data: Dict[str, Any] = {}
        errors: List[str] = []
        total = len(steps)
        async with _mcp.session(self.cfg) as state:
            for index, step in enumerate(steps):
                label = str(step.get("label") or step.get("tool"))
                await progress(label, index, total)
                try:
                    data[label] = await _mcp.call_tool(
                        state, str(step.get("tool")),
                        render_args(step.get("args") or {}, variables),
                    )
                except Exception as exc:      # noqa: BLE001 — 单步失败不该中断整轮采集
                    errors.append(f"{label}: {exc}")
        await progress("完成", total, total)
        return data, errors

    async def keyword_pipeline(self, query: str, marketplace: str,
                               progress: ProgressFn) -> Tuple[Dict[str, Any], List[str]]:
        variables = template_vars(query=query, keyword=query, marketplace=marketplace)
        return await self._run_steps("keyword_pipeline", variables, progress)

    async def asin_pipeline(self, asin: str, marketplace: str,
                            progress: ProgressFn) -> Tuple[Dict[str, Any], List[str]]:
        variables = template_vars(asin=asin, query=asin, marketplace=marketplace)
        return await self._run_steps("asin_pipeline", variables, progress)

    # -- 首页驾驶舱 ----------------------------------------------------------

    def _empty_pulse(self, asin: str, marketplace: str, error: str) -> Dict[str, Any]:
        fields = ("title", "brand", "image", "price", "bsr", "bsr_category", "sub_rank",
                  "sub_category", "est_sales", "rating", "review_count", "variations",
                  "coupon", "deal", "inventory")
        return {"asin": asin, "marketplace": marketplace, "data_source": self.id,
                "error": error, **dict.fromkeys(fields)}

    async def home_asin_pulse(self, asin: str, marketplace: str) -> Dict[str, Any]:
        spec = self.spec("home_asin_pulse")
        if not spec:
            return self._empty_pulse(asin, marketplace, self._missing("home_asin_pulse"))
        try:
            payload = await self._call(spec, template_vars(asin=asin, marketplace=marketplace))
        except Exception as exc:              # noqa: BLE001 — 源报错要变成卡片上的一行提示
            return self._empty_pulse(asin, marketplace, str(exc))
        detail = self._record(payload, spec)
        if not detail:
            return self._empty_pulse(asin, marketplace, f"「{self.name}」未返回该 ASIN 的商品详情")
        mapped = map_fields(detail, spec.get("fields") or {})
        return {
            "asin": asin, "marketplace": marketplace, "data_source": self.id, "error": None,
            "title": mapped.get("title"), "brand": mapped.get("brand"), "image": mapped.get("image"),
            "price": mapped.get("price"), "bsr": mapped.get("bsr"),
            "bsr_category": mapped.get("bsr_category"), "sub_rank": mapped.get("sub_rank"),
            "sub_category": mapped.get("sub_category"), "est_sales": mapped.get("est_sales"),
            "rating": mapped.get("rating"), "review_count": mapped.get("review_count"),
            "variations": mapped.get("variations"), "coupon": mapped.get("coupon") or None,
            "deal": mapped.get("deal") or None, "inventory": mapped.get("inventory"),
            "raw_report": payload,
        }

    async def home_keyword_pulse(self, keyword: str, marketplace: str) -> Dict[str, Any]:
        spec = self.spec("home_keyword_pulse")
        detail: Optional[Dict[str, Any]] = None
        detail_error: Optional[str] = None
        if not spec:
            detail_error = self._missing("home_keyword_pulse")
        else:
            try:
                payload = await self._call(spec, template_vars(keyword=keyword, query=keyword,
                                                               marketplace=marketplace))
                row = self._record(payload, spec)
                if row:
                    mapped = map_fields(row, spec.get("fields") or {})
                    searches = mapped.get("monthly_search_volume")
                    cpc = mapped.get("recommended_cpc_bid")
                    detail = {
                        **row, **mapped,
                        # 前端按 monthly_search_volume ?? 月搜索量 ?? searchVolume 依次读，
                        # 三个别名一起给，换源不会让卡片突然读不到数。
                        "月搜索量": searches, "searchVolume": searches,
                        "推荐cpc竞价": cpc, "averageCpc": cpc,
                        "provider": self.id,
                    }
                else:
                    detail_error = f"「{self.name}」未返回该关键词数据"
            except Exception as exc:          # noqa: BLE001
                detail_error = str(exc)
        series, trend_error = await self.home_keyword_trend_series(keyword, marketplace)
        trend = None
        if series:
            trend = {"data": [{"day": day, "searchVolume": value, "value": value}
                              for day, value in series], "provider": self.id}
        return {"keyword": keyword, "marketplace": marketplace, "data_source": self.id,
                "detail": detail, "detail_error": detail_error,
                "trend": trend, "trend_error": trend_error}

    async def home_keyword_extends(self, keyword: str,
                                   marketplace: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        spec = self.spec("home_keyword_extends")
        if not spec:
            return [], self._missing("home_keyword_extends")
        try:
            payload = await self._call(spec, template_vars(keyword=keyword, query=keyword,
                                                            marketplace=marketplace))
        except Exception as exc:              # noqa: BLE001
            return [], str(exc)
        target = keyword.strip().lower()
        out: List[Dict[str, Any]] = []
        for row in self._rows(payload, spec):
            mapped = map_fields(row, spec.get("row_fields") or {})
            word = str(mapped.get("keyword") or "").strip()
            if not word or word.lower() == target:
                continue
            out.append({
                "keyword": word,
                "monthly_search": mapped.get("monthly_search"),
                "cpc": mapped.get("cpc"),
                "seasonality": mapped.get("seasonality"),
                "evidence_sales": mapped.get("evidence_sales"),
            })
        return out, None if out else f"「{self.name}」未返回拓展词"

    async def home_keyword_purchase_evidence(self, keyword: str,
                                             marketplace: str) -> Optional[float]:
        spec = self.spec("home_keyword_purchase_evidence")
        if not spec:
            return None
        try:
            payload = await self._call(spec, template_vars(keyword=keyword, query=keyword,
                                                            marketplace=marketplace))
            mapped = map_fields(self._record(payload, spec), spec.get("fields") or {})
            return _num(mapped.get("value"))
        except Exception:                     # noqa: BLE001 — 佐证缺失只是少一条注脚，不该冒泡
            logger.debug("自定义源购买佐证获取失败（旁路，已忽略）", exc_info=True)
            return None

    async def home_category(self, query: str, marketplace: str,
                            mode: str = "category", top_n: int = 30) -> Dict[str, Any]:
        query = query.strip()
        spec = self.spec("home_category")
        base = {"query": query, "marketplace": marketplace, "mode": mode,
                "node_id": "", "category_name": None,
                "source": "keyword" if mode == "keyword" else "name",
                "summary": None, "bands": [], "top": [], "data_source": self.id}
        if not spec:
            return {**base, "error": self._missing("home_category")}
        variables = template_vars(query=query, keyword=query, marketplace=marketplace,
                                  top_n=top_n, size=top_n, mode=mode)
        try:
            payload = await self._call(spec, variables)
        except Exception as exc:              # noqa: BLE001
            return {**base, "error": str(exc)}
        products: List[Dict[str, Any]] = []
        for index, row in enumerate(self._rows(payload, spec)):
            mapped = map_fields(row, spec.get("row_fields") or {})
            products.append({
                "rank": index + 1,
                "asin": str(mapped.get("asin") or ""),
                "title": mapped.get("title"), "brand": mapped.get("brand"),
                "image": mapped.get("image"), "price": mapped.get("price"),
                "bsr": mapped.get("bsr"), "est_sales": mapped.get("est_sales"),
                "rating": mapped.get("rating"), "review_count": mapped.get("review_count"),
            })
        summary_src = map_fields(self._record(payload, spec), spec.get("summary_fields") or {})
        prices = [p["price"] for p in products if isinstance(p.get("price"), (int, float))]
        sales = [p["est_sales"] for p in products if isinstance(p.get("est_sales"), (int, float))]
        node_id = summary_src.get("node_id")
        return {
            **base,
            "error": None if products else f"「{self.name}」未返回类目商品数据",
            "node_id": str(node_id or ""),
            "category_name": summary_src.get("category_name"),
            "summary": {
                "count": len(products),
                "avg_price": round(sum(prices) / len(prices), 2) if prices
                else summary_src.get("avg_price"),
                "total_sales": round(sum(sales), 1) if sales else summary_src.get("total_sales"),
            },
            "bands": _price_bands(products),
            "top": products[:top_n],
        }

    async def home_market_metrics(self, query: str, marketplace: str) -> Dict[str, Any]:
        spec = self.spec("home_market_metrics")
        base = {"query": query, "marketplace": marketplace, "data_source": self.id,
                "search_volume": None, "total_sales": None, "avg_price": None,
                "node_id": "", "node_id_path": "", "category_name": None}
        if not spec:
            return {**base, "error": self._missing("home_market_metrics")}
        try:
            payload = await self._call(spec, template_vars(query=query, keyword=query,
                                                            marketplace=marketplace))
        except Exception as exc:              # noqa: BLE001
            return {**base, "error": str(exc)}
        mapped = map_fields(self._record(payload, spec), spec.get("fields") or {})
        node_path = str(mapped.get("node_id_path") or "")
        values = (mapped.get("search_volume"), mapped.get("total_sales"), mapped.get("avg_price"))
        return {
            **base,
            "search_volume": values[0], "total_sales": values[1], "avg_price": values[2],
            "node_id": str(mapped.get("node_id") or (node_path.split(":")[-1] if node_path else "")),
            "node_id_path": node_path,
            "category_name": mapped.get("category_name"),
            # 三个指标全空说明这轮采集没意义，写进历史会污染趋势图 —— 调用方靠
            # error 判断要不要落库。
            "error": None if any(v is not None for v in values)
            else f"「{self.name}」无可用大盘数据",
        }

    async def _series(self, capability: str,
                      variables: Dict[str, Any]) -> Tuple[List[Tuple[str, float]], Optional[str]]:
        spec = self.spec(capability)
        if not spec:
            return [], self._missing(capability)
        try:
            payload = await self._call(spec, variables)
        except Exception as exc:              # noqa: BLE001
            return [], str(exc)
        out: List[Tuple[str, float]] = []
        for row in self._rows(payload, spec):
            mapped = map_fields(row, spec.get("row_fields") or {})
            day = _day(mapped.get("day"))
            value = _num(mapped.get("value"))
            if day and value is not None:
                out.append((day, value))
        return out, None if out else f"「{self.name}」未返回趋势数据"

    async def home_keyword_trend_series(
        self, keyword: str, marketplace: str,
    ) -> Tuple[List[Tuple[str, float]], Optional[str]]:
        return await self._series("home_keyword_trend_series",
                                  template_vars(keyword=keyword, query=keyword,
                                                marketplace=marketplace))

    async def home_product_trend_series(
        self, asin: str, marketplace: str,
    ) -> Tuple[List[Tuple[str, float]], Optional[str]]:
        return await self._series("home_product_trend_series",
                                  template_vars(asin=asin, marketplace=marketplace))


def provider_for(source_id: str) -> Optional[CustomProvider]:
    """``custom:xxx`` → provider 实例；不是自定义源或未启用时返回 None。

    每次调用都重读配置：设置页改完立刻生效，不用重启服务。
    """
    cfg = _registry.get(source_id)
    return CustomProvider(cfg) if cfg else None
