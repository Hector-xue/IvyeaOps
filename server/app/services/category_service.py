"""Category-level dashboard data for the home cockpit.

Two modes:
  • mode="category": resolve a category node, then pull its category_report
    (true category bestseller ranking). Node resolution accepts a nodeId, an
    ASIN (reverse-looked-up to its real category — the reliable path), or a
    product name (Sorftime's name→category matcher, which is unreliable; the
    resolved category name is surfaced so the user can sanity-check it).
  • mode="keyword": keyword_search_results — the keyword's actual Amazon search
    ranking (relevant but naturally noisy: ads / accessories).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.services.sorftime_service import _make_client, _safe_call, record, rows, unwrap

_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")


def _root(d: Any) -> Dict[str, Any]:
    inner = unwrap(d)
    return inner if isinstance(inner, dict) else (d if isinstance(d, dict) else {})


def _pick(d: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] not in (None, "", []):
            return d[k]
    return None


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = str(v).replace(",", "").replace("$", "").replace("%", "").strip().lstrip("#").strip()
        return float(s) if s else None
    except Exception:
        return None


def _rank_in(s: Any) -> Optional[float]:
    """Rank out of a category_rank string: 'Category: Food Bags, Rank: 1' /
    '类目：X，排名:1'."""
    if not s:
        return None
    m = re.search(r"排名[:：]?\s*(\d+)", str(s)) or re.search(r"rank[:：]?\s*(\d+)", str(s), re.I)
    return float(m.group(1)) if m else None


def _extract_nodeid(cat_res: Any) -> str:
    def from_dict(d: Dict[str, Any]) -> str:
        return str(d.get("nodeid") or d.get("nodeId") or d.get("node_id") or "")
    nid = from_dict(record(cat_res))
    if nid:
        return nid
    for row in rows(cat_res)[:1]:
        return from_dict(row)
    return ""


def _cat_name_of(cat_res: Any) -> Optional[str]:
    for src in (record(cat_res), *rows(cat_res)[:1]):
        name = _pick(src, "category_name", "类目名称")
        if name:
            return str(name)
    return None


def _extract_list(report: Any) -> List[Dict[str, Any]]:
    return rows(report)


def _cat_name_from_products(raw: List[Dict[str, Any]]) -> Optional[str]:
    """Pull category name from a product's category_rank
    ('Category: Disposable Food Storage Bags, Rank: 1' / '类目：X，排名:1')."""
    for p in raw:
        s = _pick(p, "category_rank", "所处类目排名")
        if s:
            m = (re.search(r"类目[:：]\s*([^，,]+)", str(s))
                 or re.search(r"category[:：]\s*([^，,]+)", str(s), re.I))
            if m:
                return m.group(1).strip()
    return None


def _normalize_product(p: Dict[str, Any], rank: int) -> Dict[str, Any]:
    return {
        "rank": rank,
        "asin": _pick(p, "ASIN", "asin") or "",
        "title": _pick(p, "标题", "title", "productName", "name"),
        "brand": _pick(p, "品牌", "brand", "brandName"),
        "price": _num(_pick(p, "价格", "price", "currentPrice")),
        "bsr": (_rank_in(_pick(p, "category_rank", "所处类目排名"))
                or _num(_pick(p, "bsr", "rank", "salesRank"))),
        "est_sales": _num(_pick(p, "月销量", "monthly_sales_volume", "monthlySales",
                                "sales", "estimatedSales")),
        "rating": _num(_pick(p, "星级", "star_rating", "rating", "star", "score")),
        "review_count": _num(_pick(p, "评论数", "review_count", "reviewCount",
                                   "reviews", "ratingsTotal")),
    }


def _sum_sales(products: List[Dict[str, Any]]) -> Optional[float]:
    vals = [p["est_sales"] for p in products if isinstance(p.get("est_sales"), (int, float))]
    return round(sum(vals), 1) if vals else None


def _price_bands(products: List[Dict[str, Any]], buckets: int = 5) -> List[Dict[str, Any]]:
    prices = [p["price"] for p in products if isinstance(p.get("price"), (int, float))]
    if len(prices) < 2:
        return []
    lo, hi = min(prices), max(prices)
    if hi <= lo:
        return [{"label": f"${lo:.0f}", "min": lo, "max": hi, "count": len(prices), "sales": _sum_sales(products)}]
    width = (hi - lo) / buckets
    bands: List[Dict[str, Any]] = []
    for i in range(buckets):
        bmin = lo + i * width
        bmax = lo + (i + 1) * width if i < buckets - 1 else hi
        members = [p for p in products if isinstance(p.get("price"), (int, float)) and bmin <= p["price"] <= bmax]
        bands.append({"label": f"${bmin:.0f}–${bmax:.0f}", "min": round(bmin, 2), "max": round(bmax, 2),
                      "count": len(members), "sales": _sum_sales(members)})
    return bands


async def _resolve_node(client, query: str, marketplace: str) -> Tuple[str, Optional[str], str, Optional[str]]:
    """Return (node_id, category_name, source, error). source ∈ nodeId|asin|name."""
    q = query.strip()
    if re.fullmatch(r"\d{4,}", q):
        return q, None, "nodeId", None

    if _ASIN_RE.match(q.upper()) and any(ch.isalpha() for ch in q):
        _, detail, err = await _safe_call(client, "product_detail", {"asin": q.upper(), "amz_site": marketplace}, 1)
        kv = record(detail)
        if not kv and isinstance(detail, str) and "未查询到" not in detail:
            # Legacy plain-text product_detail ("标题：…\n所属nodeid：…").
            for line in detail.splitlines():
                if "：" in line:
                    k, v = line.split("：", 1)
                    kv.setdefault(k.strip(), v.strip())
        nid = str(_pick(kv, "node_id", "所属nodeid") or "")
        sub = str(_pick(kv, "subcategory", "top_category", "category",
                        "所属细分类目", "所属大类") or "")
        name = (re.split(r"[（(]", sub)[0].strip() or None) if sub else None
        if nid:
            return nid, name, "asin", None
        return "", None, "asin", (err or "未查询到该 ASIN（无法反查类目）")

    _, cat_res, err = await _safe_call(
        client, "category_search_from_product_name", {"product_name": q, "amz_site": marketplace}, 1)
    nid = _extract_nodeid(cat_res)
    name = _cat_name_of(cat_res)
    if not nid:
        return "", name, "name", (err or "无法从该名称解析出类目节点（可粘贴 nodeId 或用 ASIN 反查）")
    return nid, name, "name", None


def _empty(query: str, marketplace: str, mode: str, error: Optional[str], **extra) -> Dict[str, Any]:
    return {"query": query, "marketplace": marketplace, "mode": mode, "error": error,
            "node_id": "", "category_name": None, "source": mode,
            "summary": None, "bands": [], "top": [], **extra}


async def fetch_category(query: str, marketplace: str, mode: str = "category", top_n: int = 30) -> Dict[str, Any]:
    query = query.strip()
    async with _make_client() as client:
        if mode == "keyword":
            _, res, err = await _safe_call(
                client, "keyword_search_results", {"keyword": query, "keyword_support_site": marketplace}, 1)
            raw = res if isinstance(res, list) else _extract_list(res)
            products = [_normalize_product(p, i + 1) for i, p in enumerate(raw)]
            if not products:
                return _empty(query, marketplace, "keyword", err or "无搜索结果", source="keyword")
            prices = [p["price"] for p in products if isinstance(p.get("price"), (int, float))]
            return {
                "query": query, "marketplace": marketplace, "mode": "keyword", "error": None,
                "node_id": "", "category_name": None, "source": "keyword",
                "summary": {"count": len(products),
                            "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
                            "total_sales": _sum_sales(products)},
                "bands": _price_bands(products),
                "top": products[:top_n],
            }

        # mode == "category"
        node_id, cat_name, source, rerr = await _resolve_node(client, query, marketplace)
        if not node_id:
            return _empty(query, marketplace, "category", rerr, category_name=cat_name, source=source)
        _, report, report_err = await _safe_call(client, "category_report", {"node_id": node_id, "amz_site": marketplace}, 2)

    if report_err:
        return _empty(query, marketplace, "category", report_err, node_id=node_id, category_name=cat_name, source=source)

    raw_list = _extract_list(report)
    products = [_normalize_product(p, i + 1) for i, p in enumerate(raw_list)]
    if not cat_name:
        cat_name = _cat_name_from_products(raw_list)
    prices = [p["price"] for p in products if isinstance(p.get("price"), (int, float))]
    return {
        "query": query, "marketplace": marketplace, "mode": "category", "error": None,
        "node_id": node_id, "category_name": cat_name, "source": source,
        "summary": {"count": len(products),
                    "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
                    "total_sales": _sum_sales(products)},
        "bands": _price_bands(products),
        "top": products[:top_n],
    }


def rank_map(top: List[Dict[str, Any]]) -> Dict[str, int]:
    return {p["asin"]: p["rank"] for p in top if p.get("asin")}
