"""SellerSprite MCP client + data pipelines for 市场调研.

SellerSprite's open platform is an MCP server (streamable HTTP) — same shape as
Sorftime: POST JSON-RPC to https://mcp.sellersprite.com/mcp?secret-key=<key>,
responses come back as SSE (`data: {...}`). Each tool returns
`{"code":"OK","message":"成功","data":...}` inside the MCP text content. The
collected `data` flows straight into ai_synthesis_service (data-driven prompt),
so no field-by-field mapping is needed.

Tools used (verified against the live MCP):
  keyword: keyword_research_trends, aba_research_trend
  asin:    asin_detail, asin_sales_trend, traffic_keyword_stat
"""
from __future__ import annotations

import asyncio
import logging
import datetime
import json as _json
import re
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger("ivyea.services.sellersprite_service")

_BASE = "https://mcp.sellersprite.com/mcp"
_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
_TOOL_TIMEOUT = 40.0
_CONN_TIMEOUT = 10.0

ProgressFn = Callable[[str, int, int], Awaitable[None]]


def _key() -> str:
    from app.core import hub_settings
    return str(hub_settings.get("sellersprite_key") or "").strip()


def _url() -> str:
    return f"{_BASE}?secret-key={_key()}"


def recent_month() -> str:
    """Most recently completed month as yyyyMM (SellerSprite data lags ~1 month)."""
    return (datetime.date.today().replace(day=1) - datetime.timedelta(days=1)).strftime("%Y%m")


def parse_sse(text: str) -> dict:
    """Pull the JSON-RPC object out of an SSE (`data: {...}`) or plain JSON body."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            raw = line[5:].strip()
            if raw:
                try:
                    return _json.loads(raw)
                except Exception:
                    logger.debug("_json.loads 失败（旁路，已忽略）", exc_info=True)
    try:
        return _json.loads(text)
    except Exception:
        return {}


async def _initialize(client: httpx.AsyncClient) -> None:
    try:
        await client.post(_url(), headers=_HEADERS, json={
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "IvyeaOps", "version": "1.0"}},
        })
    except Exception:
        logger.debug("client.post 失败（旁路，已忽略）", exc_info=True)


async def _call_tool(client: httpx.AsyncClient, name: str, args: dict, call_id: int = 1) -> Any:
    if not _key():
        raise RuntimeError("卖家精灵 key 未配置（系统配置 → 数据源 → 卖家精灵 Key）")
    r = await client.post(_url(), headers=_HEADERS, json={
        "jsonrpc": "2.0", "id": call_id, "method": "tools/call",
        "params": {"name": name, "arguments": args},
    })
    body = parse_sse(r.text)
    if body.get("error"):
        raise RuntimeError(f"{name}: {body['error']}")
    result = body.get("result") or {}
    content = result.get("content") or []
    text = content[0].get("text", "") if content and isinstance(content[0], dict) else ""
    if result.get("isError"):
        raise RuntimeError(f"{name}: {(text or '工具返回错误')[:200]}")
    # text content is the tool payload: {"code":"OK","data":...}
    try:
        parsed = _json.loads(text) if text else {}
    except Exception:
        return {"_raw": text[:4000]}
    code = str(parsed.get("code") or "")
    if code and code != "OK":
        raise RuntimeError(f"{name} {code}: {parsed.get('message', '')}")
    return parsed.get("data", parsed)


async def _run_steps(steps: list[tuple[str, str, dict]], progress: ProgressFn) -> tuple[dict, list[str]]:
    data: dict[str, Any] = {}
    errors: list[str] = []
    total = len(steps)
    async with httpx.AsyncClient(timeout=httpx.Timeout(_TOOL_TIMEOUT, connect=_CONN_TIMEOUT)) as client:
        await _initialize(client)
        for i, (label, name, args) in enumerate(steps):
            await progress(label, i, total)
            try:
                data[label] = await _call_tool(client, name, args, i + 1)
            except Exception as exc:  # noqa: BLE001 — collect, don't abort the whole run
                errors.append(f"{label}: {exc}")
    await progress("完成", total, total)
    return data, errors


async def keyword_pipeline(query: str, marketplace: str, progress: ProgressFn) -> tuple[dict, list[str]]:
    month = recent_month()
    return await _run_steps([
        ("关键词搜索/购买趋势", "keyword_research_trends",
         {"marketplace": marketplace, "keyword": query, "month": month}),
        ("ABA 排名趋势", "aba_research_trend",
         {"marketplace": marketplace, "keyword": query, "timeGranularity": "month"}),
    ], progress)


async def asin_pipeline(asin: str, marketplace: str, progress: ProgressFn) -> tuple[dict, list[str]]:
    month = recent_month()
    return await _run_steps([
        ("商品详情", "asin_detail", {"marketplace": marketplace, "asin": asin}),
        ("销量趋势", "asin_sales_trend", {"marketplace": marketplace, "asin": asin}),
        ("流量关键词概览", "traffic_keyword_stat", {"marketplace": marketplace, "asin": asin, "month": month}),
    ], progress)


# ── Home dashboard adapters ──────────────────────────────────────────────────
#
# The home dashboard has a stable, provider-neutral response contract.  Keep
# SellerSprite's field names out of the router/UI so a source switch cannot
# accidentally leak a second schema into the same cards and charts.

_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        cleaned = str(value).replace(",", "").replace("$", "").replace("%", "").strip()
        return float(cleaned) if cleaned else None
    except (TypeError, ValueError):
        return None


def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("items", "list", "results", "data", "rows"):
            found = value.get(key)
            if isinstance(found, list):
                return [item for item in found if isinstance(item, dict)]
    return []


def _keyword_exact(payload: Any, keyword: str) -> dict[str, Any]:
    rows = _items(payload)
    target = keyword.strip().lower()
    return next(
        (row for row in rows if str(row.get("keywords") or row.get("keyword") or "").strip().lower() == target),
        rows[0] if rows else {},
    )


def _research_args(keyword: str, marketplace: str, *, size: int = 30) -> dict[str, Any]:
    return {
        "request": {
            "marketplace": marketplace,
            "keywords": keyword,
            "month": recent_month(),
            "page": 1,
            "size": size,
        }
    }


def _competition_index(row: dict[str, Any]) -> float | None:
    """Provider-neutral 0–100 pressure index derived from supply vs demand.

    SellerSprite exposes product count and monthly searches rather than a
    proprietary 0–100 competition score.  The bounded product share keeps the
    dashboard's opportunity matrix meaningful without pretending it is a raw
    SellerSprite field.
    """
    products = _num(row.get("products"))
    searches = _num(row.get("searches"))
    if products is None or searches is None:
        return None
    return round(max(0.0, min(100.0, 100.0 * products / max(products + searches, 1.0))), 2)


def _normalize_keyword_detail(row: dict[str, Any]) -> dict[str, Any] | None:
    if not row:
        return None
    searches = _num(row.get("searches"))
    bid = _num(row.get("bid"))
    purchase_rate = _num(row.get("purchaseRate"))
    return {
        **row,
        "月搜索量": searches,
        "推荐cpc竞价": bid,
        "searchVolume": searches,
        "averageCpc": bid,
        "purchaseRate": purchase_rate,
        "competitionIndex": _competition_index(row),
        "competitionMethod": "products_share_of_products_plus_searches",
        "provider": "sellersprite",
    }


def _normalize_keyword_trend(payload: Any) -> dict[str, Any]:
    rows = _items(payload)
    normalized = []
    for row in rows:
        searches = _num(row.get("search") or row.get("searches") or row.get("searchVolume"))
        if searches is None:
            continue
        normalized.append({**row, "searchVolume": searches, "value": searches})
    return {"data": normalized, "provider": "sellersprite"}


def _price_bands(products: list[dict[str, Any]], buckets: int = 5) -> list[dict[str, Any]]:
    prices = [p["price"] for p in products if isinstance(p.get("price"), (int, float))]
    if not prices:
        return []
    low, high = min(prices), max(prices)
    if high <= low:
        sales = sum(p.get("est_sales") or 0 for p in products) or None
        return [{"label": f"${low:.0f}", "min": low, "max": high, "count": len(prices), "sales": sales}]
    width = (high - low) / buckets
    out: list[dict[str, Any]] = []
    for index in range(buckets):
        start = low + index * width
        end = low + (index + 1) * width if index < buckets - 1 else high
        members = [
            p for p in products
            if isinstance(p.get("price"), (int, float)) and start <= p["price"] <= end
        ]
        sales = sum(p.get("est_sales") or 0 for p in members) or None
        out.append({
            "label": f"${start:.0f}–${end:.0f}",
            "min": round(start, 2),
            "max": round(end, 2),
            "count": len(members),
            "sales": round(sales, 1) if isinstance(sales, (int, float)) else None,
        })
    return out


def _normalize_market_product(row: dict[str, Any], rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "asin": str(row.get("asin") or row.get("ASIN") or ""),
        "title": row.get("title") or row.get("productName"),
        "brand": row.get("brand"),
        "image": row.get("imageUrl") or row.get("image"),
        "price": _num(row.get("price")),
        "bsr": _num(row.get("bsrRank") or row.get("bsr")),
        "est_sales": _num(row.get("totalUnits") or row.get("monthlySales") or row.get("sales")),
        "rating": _num(row.get("rating")),
        "review_count": _num(row.get("ratings") or row.get("reviewCount") or row.get("reviews")),
    }


def _empty_pulse(asin: str, marketplace: str, error: str) -> dict[str, Any]:
    fields = (
        "title", "brand", "image", "price", "bsr", "bsr_category", "sub_rank",
        "sub_category", "est_sales", "rating", "review_count", "variations",
        "coupon", "deal", "inventory",
    )
    return {
        "asin": asin,
        "marketplace": marketplace,
        "data_source": "sellersprite",
        "error": error,
        **dict.fromkeys(fields),
    }


async def home_asin_pulse(asin: str, marketplace: str) -> dict[str, Any]:
    """Fetch one SellerSprite ASIN and normalize it for the home monitor."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_TOOL_TIMEOUT, connect=_CONN_TIMEOUT)) as client:
            await _initialize(client)
            payload = await _call_tool(
                client, "asin_sales_trend", {"marketplace": marketplace, "asin": asin}, 1,
            )
    except Exception as exc:  # noqa: BLE001 - provider error becomes card error
        return _empty_pulse(asin, marketplace, str(exc))

    detail = payload.get("asin") if isinstance(payload, dict) and isinstance(payload.get("asin"), dict) else payload
    if not isinstance(detail, dict) or not detail.get("asin"):
        return _empty_pulse(asin, marketplace, "卖家精灵未返回该 ASIN 的商品详情")
    trend = payload.get("salesTrendPoints") if isinstance(payload, dict) else []
    trend = [point for point in (trend or []) if isinstance(point, dict)]
    latest = trend[-1] if trend else {}
    subcategories = detail.get("subcategories") or []
    sub = subcategories[0] if subcategories and isinstance(subcategories[0], dict) else {}
    coupon = detail.get("coupon")
    return {
        "asin": asin,
        "marketplace": marketplace,
        "data_source": "sellersprite",
        "error": None,
        "title": detail.get("title"),
        "brand": detail.get("brand"),
        "image": detail.get("zoomImageUrl") or detail.get("imageUrl"),
        "price": _num(detail.get("price")),
        "bsr": _num(detail.get("bsrRank")),
        "bsr_category": detail.get("bsrLabel"),
        "sub_rank": _num(sub.get("rank")),
        "sub_category": sub.get("label"),
        "est_sales": _num(latest.get("parentUnitSales") or latest.get("childUnitSales")),
        "rating": _num(detail.get("rating")),
        "review_count": _num(detail.get("ratings") or detail.get("reviews")),
        "variations": _num(detail.get("variations")),
        "coupon": coupon or None,
        "deal": None,
        "inventory": None,
        "raw_report": payload,
    }


async def home_keyword_pulse(keyword: str, marketplace: str) -> dict[str, Any]:
    """Return normalized detail + trend for the keyword monitor."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(_TOOL_TIMEOUT, connect=_CONN_TIMEOUT)) as client:
        await _initialize(client)
        detail_result, trend_result = await asyncio.gather(
            _call_tool(client, "keyword_research", _research_args(keyword, marketplace), 1),
            _call_tool(
                client,
                "keyword_research_trends",
                {"marketplace": marketplace, "keyword": keyword, "month": recent_month()},
                2,
            ),
            return_exceptions=True,
        )
    detail_error = str(detail_result) if isinstance(detail_result, Exception) else None
    trend_error = str(trend_result) if isinstance(trend_result, Exception) else None
    detail = None if detail_error else _normalize_keyword_detail(_keyword_exact(detail_result, keyword))
    trend = None if trend_error else _normalize_keyword_trend(trend_result)
    if detail is None and detail_error is None:
        detail_error = "卖家精灵未返回该关键词数据"
    return {
        "keyword": keyword,
        "marketplace": marketplace,
        "data_source": "sellersprite",
        "detail": detail,
        "detail_error": detail_error,
        "trend": trend,
        "trend_error": trend_error,
    }


async def home_keyword_extends(keyword: str, marketplace: str) -> tuple[list[dict[str, Any]], str | None]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_TOOL_TIMEOUT, connect=_CONN_TIMEOUT)) as client:
            await _initialize(client)
            payload = await _call_tool(client, "keyword_research", _research_args(keyword, marketplace, size=50), 1)
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)
    target = keyword.strip().lower()
    out: list[dict[str, Any]] = []
    for row in _items(payload):
        word = str(row.get("keywords") or row.get("keyword") or "").strip()
        if not word or word.lower() == target:
            continue
        out.append({
            "keyword": word,
            "monthly_search": _num(row.get("searches")),
            "cpc": _num(row.get("bid")),
            "seasonality": row.get("marketPeriod"),
            # SellerSprite exposes monthly purchases directly; this is stronger
            # order evidence than inferring a median from search-result products.
            "evidence_sales": _num(row.get("purchases")),
        })
    return out, None if out else "卖家精灵未返回拓展词"


async def home_keyword_purchase_evidence(keyword: str, marketplace: str) -> float | None:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_TOOL_TIMEOUT, connect=_CONN_TIMEOUT)) as client:
            await _initialize(client)
            payload = await _call_tool(client, "keyword_research", _research_args(keyword, marketplace, size=5), 1)
        return _num(_keyword_exact(payload, keyword).get("purchases"))
    except Exception:
        return None


async def _resolve_home_node(
    client: httpx.AsyncClient, query: str, marketplace: str,
) -> tuple[str, str | None, str, str | None]:
    value = query.strip()
    if _ASIN_RE.fullmatch(value.upper()) and any(char.isalpha() for char in value):
        try:
            detail = await _call_tool(client, "asin_detail", {"marketplace": marketplace, "asin": value.upper()}, 1)
        except Exception as exc:  # noqa: BLE001
            return "", None, "asin", str(exc)
        if isinstance(detail, dict) and detail.get("nodeIdPath"):
            return str(detail["nodeIdPath"]), detail.get("nodeLabelPath"), "asin", None
        return "", None, "asin", "卖家精灵无法从该 ASIN 解析类目"
    if re.fullmatch(r"\d+(?::\d+)*", value):
        return value, None, "nodeId", None

    # Category-name matching is intentionally transparent: use the first
    # related ASIN returned for the keyword, then resolve its real node path.
    try:
        research = await _call_tool(client, "keyword_research", _research_args(value, marketplace, size=5), 1)
        exact = _keyword_exact(research, value)
        related = exact.get("relationAsinList") or []
        first_asin = related[0].get("asin") if related and isinstance(related[0], dict) else None
        if not first_asin:
            return "", None, "name", "卖家精灵无法从该类目词定位代表商品；请使用 ASIN 或 nodeIdPath"
        detail = await _call_tool(client, "asin_detail", {"marketplace": marketplace, "asin": first_asin}, 2)
        if isinstance(detail, dict) and detail.get("nodeIdPath"):
            return str(detail["nodeIdPath"]), detail.get("nodeLabelPath"), "name", None
    except Exception as exc:  # noqa: BLE001
        return "", None, "name", str(exc)
    return "", None, "name", "卖家精灵无法解析类目节点"


async def home_category(
    query: str, marketplace: str, mode: str = "category", top_n: int = 30,
) -> dict[str, Any]:
    query = query.strip()
    async with httpx.AsyncClient(timeout=httpx.Timeout(_TOOL_TIMEOUT, connect=_CONN_TIMEOUT)) as client:
        await _initialize(client)
        if mode == "keyword":
            try:
                research = await _call_tool(client, "keyword_research", _research_args(query, marketplace), 1)
            except Exception as exc:  # noqa: BLE001
                return {
                    "query": query, "marketplace": marketplace, "mode": mode, "error": str(exc),
                    "node_id": "", "category_name": None, "source": "keyword",
                    "summary": None, "bands": [], "top": [], "data_source": "sellersprite",
                }
            exact = _keyword_exact(research, query)
            raw = exact.get("relationAsinList") or []
            products = [_normalize_market_product(row, i + 1) for i, row in enumerate(raw) if isinstance(row, dict)]
            prices = [p["price"] for p in products if isinstance(p.get("price"), (int, float))]
            return {
                "query": query, "marketplace": marketplace, "mode": mode, "error": None if products else "无搜索结果",
                "node_id": "", "category_name": None, "source": "keyword",
                "summary": {
                    "count": len(products),
                    "avg_price": round(sum(prices) / len(prices), 2) if prices else _num(exact.get("avgPrice")),
                    "total_sales": _num(exact.get("purchases")),
                },
                "bands": _price_bands(products), "top": products[:top_n], "data_source": "sellersprite",
                "summary_kind": "keyword_purchases",
            }

        node_path, category_name, source, resolve_error = await _resolve_home_node(client, query, marketplace)
        if not node_path:
            return {
                "query": query, "marketplace": marketplace, "mode": mode, "error": resolve_error,
                "node_id": "", "category_name": category_name, "source": source,
                "summary": None, "bands": [], "top": [], "data_source": "sellersprite",
            }
        try:
            raw = await _call_tool(client, "market_product_concentration", {
                "request": {
                    "marketplace": marketplace,
                    "nodeIdPath": node_path,
                    "month": recent_month(),
                    "topN": top_n,
                    "returnFields": "asin,title,brand,price,bsrRank,totalUnits,rating,ratings,imageUrl",
                }
            }, 3)
        except Exception as exc:  # noqa: BLE001
            return {
                "query": query, "marketplace": marketplace, "mode": mode, "error": str(exc),
                "node_id": node_path.split(":")[-1], "category_name": category_name, "source": source,
                "summary": None, "bands": [], "top": [], "data_source": "sellersprite",
            }
    products = [_normalize_market_product(row, i + 1) for i, row in enumerate(_items(raw))]
    prices = [p["price"] for p in products if isinstance(p.get("price"), (int, float))]
    sales = [p["est_sales"] for p in products if isinstance(p.get("est_sales"), (int, float))]
    return {
        "query": query, "marketplace": marketplace, "mode": mode, "error": None if products else "无类目商品数据",
        "node_id": node_path.split(":")[-1], "node_id_path": node_path,
        "category_name": category_name, "source": source,
        "summary": {
            "count": len(products),
            "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
            "total_sales": round(sum(sales), 1) if sales else None,
        },
        "bands": _price_bands(products), "top": products[:top_n], "data_source": "sellersprite",
    }


async def home_market_metrics(query: str, marketplace: str) -> dict[str, Any]:
    """Keyword demand + representative category throughput for one baseline."""
    errors: list[str] = []
    exact: dict[str, Any] = {}
    node_path = ""
    category_name = None
    total_sales = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(_TOOL_TIMEOUT, connect=_CONN_TIMEOUT)) as client:
        await _initialize(client)
        try:
            research = await _call_tool(client, "keyword_research", _research_args(query, marketplace, size=5), 1)
            exact = _keyword_exact(research, query)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
        related = exact.get("relationAsinList") or []
        representative = related[0].get("asin") if related and isinstance(related[0], dict) else None
        if representative:
            try:
                detail = await _call_tool(client, "asin_detail", {"marketplace": marketplace, "asin": representative}, 2)
                if isinstance(detail, dict):
                    node_path = str(detail.get("nodeIdPath") or "")
                    category_name = detail.get("nodeLabelPath")
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
        if node_path:
            try:
                market = await _call_tool(client, "market_research", {
                    "request": {
                        "marketplace": marketplace,
                        "nodeIdPath": node_path,
                        "month": recent_month(),
                        "returnFields": "nodeId,nodeIdPath,totalUnits",
                    }
                }, 3)
                rows = _items(market)
                total_sales = _num(rows[0].get("totalUnits")) if rows else None
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
    search_volume = _num(exact.get("searches"))
    avg_price = _num(exact.get("avgPrice"))
    has_any = any(value is not None for value in (search_volume, total_sales, avg_price))
    return {
        "query": query,
        "marketplace": marketplace,
        "data_source": "sellersprite",
        "search_volume": search_volume,
        "total_sales": total_sales,
        "avg_price": avg_price,
        "node_id": node_path.split(":")[-1] if node_path else "",
        "node_id_path": node_path,
        "category_name": category_name,
        "error": None if has_any else ("；".join(errors) or "卖家精灵无可用大盘数据"),
    }


def _trend_day(value: Any) -> str | None:
    match = re.search(r"(\d{4})[^\d]?(\d{1,2})", str(value or ""))
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-01" if match else None


async def home_keyword_trend_series(
    keyword: str, marketplace: str,
) -> tuple[list[tuple[str, float]], str | None]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_TOOL_TIMEOUT, connect=_CONN_TIMEOUT)) as client:
            await _initialize(client)
            payload = await _call_tool(
                client, "keyword_research_trends",
                {"marketplace": marketplace, "keyword": keyword, "month": recent_month()}, 1,
            )
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)
    out: list[tuple[str, float]] = []
    for row in _items(payload):
        day = _trend_day(row.get("time") or row.get("month"))
        searches = _num(row.get("search") or row.get("searches") or row.get("searchVolume"))
        if day and searches is not None:
            out.append((day, searches))
    return out, None if out else "卖家精灵未返回关键词趋势"


async def home_product_trend_series(
    asin: str, marketplace: str,
) -> tuple[list[tuple[str, float]], str | None]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_TOOL_TIMEOUT, connect=_CONN_TIMEOUT)) as client:
            await _initialize(client)
            payload = await _call_tool(client, "asin_sales_trend", {"marketplace": marketplace, "asin": asin}, 1)
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)
    points = payload.get("salesTrendPoints") if isinstance(payload, dict) else []
    out: list[tuple[str, float]] = []
    for row in points or []:
        if not isinstance(row, dict):
            continue
        day = _trend_day(row.get("month"))
        sales = _num(row.get("parentUnitSales") or row.get("childUnitSales"))
        if day and sales is not None:
            out.append((day, sales))
    return out, None if out else "卖家精灵未返回 ASIN 销量趋势"
