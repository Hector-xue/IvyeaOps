"""Market-level (大盘) traffic metrics for daily recording.

Combines a demand proxy (category main-keyword search volume) with category
throughput (TOP-N total est. sales + average price) into a single point that
the home scheduler records once per day, building a daily time-series.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.services import category_service
from app.services.sorftime_service import _make_client, _safe_call, record

# Sorftime writes months either as '2024年05月' (product_trend) or '2024-05'
# (keyword_trend) depending on the tool — accept both.
_MONTH_RE = re.compile(r"(\d{4})年(\d{1,2})月|(\d{4})-(\d{1,2})(?!\d)")


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = str(v).replace(",", "").replace("$", "").replace("%", "").strip()
        return float(s) if s else None
    except Exception:
        return None


def _parse_search_volume(detail: Any) -> Optional[float]:
    # Live keyword_detail: {"doc": …, "data": {"monthly_search_volume": "362", …}}.
    root = record(detail)
    return _num(
        root.get("monthly_search_volume") or root.get("月搜索量")
        or root.get("searchVolume") or root.get("search_volume")
        or root.get("searches") or root.get("monthlySearches")
    )


async def fetch_market_metrics(query: str, marketplace: str) -> Dict[str, Any]:
    """Return {search_volume, total_sales, avg_price, node_id, error}.

    ``error`` is only set when *no* metric could be obtained; partial data
    (e.g. search volume present but category failed) is returned with error=None.
    """
    query = query.strip()

    # Demand proxy: keyword_detail search volume.
    async with _make_client() as client:
        _, detail, kw_err = await _safe_call(
            client, "keyword_detail",
            {"keyword": query, "keyword_support_site": marketplace}, 1,
        )
    search_volume = _parse_search_volume(detail)

    # Throughput + price: reuse the category dashboard aggregates.
    cat = await category_service.fetch_category(query, marketplace)
    summary = cat.get("summary") or {}
    total_sales = summary.get("total_sales")
    avg_price = summary.get("avg_price")

    has_any = any(v is not None for v in (search_volume, total_sales, avg_price))
    error = None if has_any else (kw_err or cat.get("error") or "无可用大盘数据")

    return {
        "query": query,
        "marketplace": marketplace,
        "search_volume": search_volume,
        "total_sales": total_sales,
        "avg_price": avg_price,
        "node_id": cat.get("node_id", ""),
        "error": error,
    }


# ── Historical backfill (monthly) via trend tools ─────────────────────────────

def _month_day(s: str) -> Optional[str]:
    """'2024年05月...' / '2024-05 search volume 42' -> '2024-05-01'."""
    m = _MONTH_RE.search(str(s))
    if not m:
        return None
    year, month = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    return f"{int(year):04d}-{int(month):02d}-01"


def parse_keyword_trend(detail: Any) -> List[Tuple[str, float]]:
    """keyword_trend → [(YYYY-MM-01, search_volume)]. Entries look like
    '2024-05 search volume 144680' (older responses: '2024年05月搜索量144680')."""
    out: List[Tuple[str, float]] = []
    root = record(detail)
    arr = root.get("search_volume_trend") or root.get("搜索量趋势")
    if isinstance(arr, list):
        for s in arr:
            day = _month_day(s)
            v = (re.search(r"搜索量\s*(\d+)", str(s))
                 or re.search(r"search volume\s*(\d+)", str(s), re.I)
                 or re.search(r"(\d+)\s*$", str(s)))
            if day and v:
                out.append((day, float(v.group(1))))
    return out


def parse_product_trend(text: Any) -> List[Tuple[str, float]]:
    """product_trend → [(YYYY-MM-01, monthly_sales)]. Format:
    '2024年05月=1802,2024年06月=1641,...'."""
    out: List[Tuple[str, float]] = []
    if not isinstance(text, str):
        return out
    for part in text.split(","):
        day = _month_day(part)
        v = re.search(r"=\s*(\d+)", part)
        if day and v:
            out.append((day, float(v.group(1))))
    return out


async def fetch_keyword_trend_series(query: str, marketplace: str) -> Tuple[List[Tuple[str, float]], Optional[str]]:
    async with _make_client() as client:
        _, detail, err = await _safe_call(
            client, "keyword_trend", {"keyword": query, "keyword_support_site": marketplace}, 1)
    return parse_keyword_trend(detail), err


async def fetch_product_trend_series(asin: str, marketplace: str) -> Tuple[List[Tuple[str, float]], Optional[str]]:
    async with _make_client() as client:
        _, text, err = await _safe_call(
            client, "product_trend", {"asin": asin, "amz_site": marketplace}, 1)
    return parse_product_trend(text), err
