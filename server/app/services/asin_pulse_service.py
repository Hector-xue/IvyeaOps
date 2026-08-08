"""Lightweight single-ASIN pulse for the home monitoring dashboard.

Uses Sorftime's ``product_detail`` (NOT ``product_report`` — that one returns
LLM orchestration instructions for every ASIN). It answers with a
``{"doc": …, "data": {…}}`` envelope whose record carries English snake_case
fields (``monthly_sales_volume``, ``star_rating``, ``top_category``:
"Kitchen & Dining (Rank: 3662)" …). The older plain-text ``字段：值`` form is
still parsed as a fallback. ``product_variations`` is fetched concurrently just
for the variant count.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional

from app.services.sorftime_service import _make_client, _safe_call, record, rows

_NOT_FOUND = ("未查询到", "请检查")


def _parse_kv(text: str) -> Dict[str, str]:
    """Parse 'label：value' lines (full-width colon) into a dict."""
    kv: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if "：" in line:
            k, v = line.split("：", 1)
            k = k.strip()
            if k and k not in kv:
                kv[k] = v.strip()
    return kv


def _num(s: Any) -> Optional[float]:
    """First numeric token in a value (handles '月销量：29746', '22.48', etc.)."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = re.search(r"-?\d+\.?\d*", str(s).replace(",", ""))
    return float(m.group()) if m else None


def _rank(s: Any) -> Optional[float]:
    """Pull a rank number out of 'Kitchen & Dining (Rank: 3662)' or the older
    '所属大类：Sports & Outdoors（排名:23）'."""
    if not s:
        return None
    m = re.search(r"排名[:：]?\s*(\d+)", str(s)) or re.search(r"rank[:：]?\s*(\d+)", str(s), re.I)
    return float(m.group(1)) if m else None


def _cat_name(s: Any) -> Optional[str]:
    """Category name before the rank: 'Sports & Outdoors（排名:23）' -> 'Sports & Outdoors'."""
    if not s:
        return None
    name = re.split(r"[（(]", str(s))[0].strip()
    return name or None


def _pick(kv: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = kv.get(k)
        if v not in (None, "", []):
            return v
    return None


def _normalize(detail: Any, variations: Any) -> Dict[str, Any]:
    kv: Dict[str, Any] = dict(record(detail))
    if not kv:
        if not isinstance(detail, str):
            return {"_not_found": False, "_unparsed": True}
        if any(tok in detail for tok in _NOT_FOUND) and "标题" not in detail:
            return {"_not_found": True}
        kv = dict(_parse_kv(detail))   # legacy plain-text form
        if not kv:
            return {"_not_found": False, "_unparsed": True}

    var_count: Optional[int] = None
    sub = _num(_pick(kv, "variation_count", "子体数"))
    if sub is not None:
        var_count = int(sub)
    else:
        var_rows = rows(variations)
        if var_rows:
            var_count = len(var_rows)

    top_cat = _pick(kv, "top_category", "所属大类")
    sub_cat = _pick(kv, "subcategory", "所属细分类目")
    return {
        "title": _pick(kv, "title", "标题"),
        "brand": _pick(kv, "brand", "品牌"),
        "image": _pick(kv, "main_image", "主图"),
        "price": _num(_pick(kv, "price", "价格")),
        # BSR = the main-category (大类) Best Sellers Rank shown on Amazon's page,
        # NOT the much-smaller subcategory rank. Subcategory kept separately.
        "bsr": _rank(top_cat) or _rank(sub_cat),
        "bsr_category": _cat_name(top_cat),
        "sub_rank": _rank(sub_cat),
        "sub_category": _cat_name(sub_cat),
        "est_sales": _num(_pick(kv, "monthly_sales_volume", "月销量")),
        "rating": _num(_pick(kv, "star_rating", "星级")),
        "review_count": _num(_pick(kv, "review_count", "评论数")),
        "variations": var_count,
        "coupon": _num(_pick(kv, "coupon")),
        # Sorftime product_detail does not expose these — left N/A.
        "deal": None,
        "inventory": None,
    }


async def fetch_asin_pulse(asin: str, marketplace: str) -> Dict[str, Any]:
    """Fetch + normalize one ASIN. ``error`` is set (and metric fields None)
    when product_detail failed or the ASIN isn't in Sorftime's library."""
    async with _make_client() as client:
        detail_task = _safe_call(client, "product_detail", {"asin": asin, "amz_site": marketplace}, 1)
        var_task = _safe_call(client, "product_variations", {"asin": asin, "amz_site": marketplace}, 2)
        (_, detail, detail_err), (_, variations, _var_err) = await asyncio.gather(detail_task, var_task)

    empty = dict.fromkeys(("title", "brand", "image", "price", "bsr", "bsr_category", "sub_rank", "sub_category", "est_sales", "rating", "review_count", "variations", "coupon", "deal", "inventory"))

    if detail_err:
        return {"asin": asin, "marketplace": marketplace, "error": detail_err, **empty, "raw_report": detail}

    norm = _normalize(detail, variations)
    if norm.get("_not_found"):
        return {"asin": asin, "marketplace": marketplace,
                "error": "未查询到该 ASIN（可能不在 Sorftime 库中）", **empty, "raw_report": detail}
    if norm.get("_unparsed"):
        return {"asin": asin, "marketplace": marketplace,
                "error": "product_detail 返回格式异常", **empty, "raw_report": detail}

    return {
        "asin": asin, "marketplace": marketplace, "error": None,
        **{k: norm.get(k) for k in empty},
        "raw_report": detail,
    }


SNAPSHOT_METRICS: List[str] = [
    "price", "bsr", "est_sales", "rating", "review_count", "inventory",
]

# Full set persisted per snapshot so a cached card can render fully (title /
# image / category) without a fresh Sorftime call. Numeric deltas still use
# SNAPSHOT_METRICS only.
SNAPSHOT_FIELDS: List[str] = SNAPSHOT_METRICS + [
    "title", "brand", "image", "bsr_category", "sub_rank", "sub_category",
    "variations", "coupon", "deal",
]


def snapshot_payload(pulse: Dict[str, Any]) -> Dict[str, Any]:
    """Subset of a pulse result stored as a snapshot (renderable + metrics)."""
    return {k: pulse.get(k) for k in SNAPSHOT_FIELDS}
