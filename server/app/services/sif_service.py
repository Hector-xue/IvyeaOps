"""Deep Analysis data service — uses Sorftime MCP for keyword/competitor/traffic data.

Replaces SIF MCP with Sorftime tools:
- keyword_competition → keyword_detail + keyword_trend + keyword_extends
- competitor_keyword_signals → product_traffic_terms + competitor_product_keywords
- traffic_anomaly → product_traffic_terms + product_trend
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from app.services import sorftime_service

_log = logging.getLogger(__name__)


async def keyword_competition(
    keyword: str,
    country: str = "US",
    asin: str = "",
) -> Dict[str, Any]:
    """Keyword competition analysis using Sorftime tools."""
    async with sorftime_service._make_client() as client:
        tasks = [
            sorftime_service._safe_call(client, "keyword_detail",
                {"keyword": keyword, "keyword_support_site": country}, 1),
            sorftime_service._safe_call(client, "keyword_trend",
                {"keyword": keyword, "keyword_support_site": country}, 2),
            sorftime_service._safe_call(client, "keyword_extends",
                {"keyword": keyword, "keyword_support_site": country}, 3),
            sorftime_service._safe_call(client, "keyword_search_results",
                {"keyword": keyword, "keyword_support_site": country}, 4),
        ]
        results = await asyncio.gather(*tasks)

    data = {}
    errors = []
    for name, val, err in results:
        if err:
            errors.append(err)
        else:
            data[name] = val

    if not data:
        raise RuntimeError(f"所有数据源均失败: {'; '.join(errors)}")

    return {
        "keyword": keyword,
        "country": country,
        "detail": data.get("keyword_detail"),
        "trend": data.get("keyword_trend"),
        "extends": data.get("keyword_extends"),
        "search_results": data.get("keyword_search_results"),
        "errors": errors,
    }


async def competitor_keyword_signals(
    asin: str,
    country: str = "US",
    time_type: str = "lately",
    time_value: str = "7",
) -> Dict[str, Any]:
    """Competitor keyword signals using Sorftime tools."""
    async with sorftime_service._make_client() as client:
        tasks = [
            sorftime_service._safe_call(client, "product_traffic_terms",
                {"asin": asin, "amz_site": country}, 1),
            sorftime_service._safe_call(client, "competitor_product_keywords",
                {"asin": asin, "keyword_support_site": country}, 2),
            sorftime_service._safe_call(client, "product_detail",
                {"asin": asin, "amz_site": country}, 3),
        ]
        results = await asyncio.gather(*tasks)

    data = {}
    errors = []
    for name, val, err in results:
        if err:
            errors.append(err)
        else:
            data[name] = val

    if not data:
        raise RuntimeError(f"所有数据源均失败: {'; '.join(errors)}")

    return {
        "asin": asin,
        "country": country,
        "traffic_terms": data.get("product_traffic_terms"),
        "competitor_keywords": data.get("competitor_product_keywords"),
        "product_detail": data.get("product_detail"),
        "errors": errors,
    }


async def traffic_anomaly(
    asin: str,
    country: str = "US",
) -> Dict[str, Any]:
    """Traffic anomaly diagnosis using Sorftime tools."""
    async with sorftime_service._make_client() as client:
        tasks = [
            sorftime_service._safe_call(client, "product_traffic_terms",
                {"asin": asin, "amz_site": country}, 1),
            sorftime_service._safe_call(client, "product_trend",
                {"asin": asin, "amz_site": country}, 2),
            # product_detail: product_report is a how-to tool, never data.
            sorftime_service._safe_call(client, "product_detail",
                {"asin": asin, "amz_site": country}, 3),
        ]
        results = await asyncio.gather(*tasks)

    data = {}
    errors = []
    for name, val, err in results:
        if err:
            errors.append(err)
        else:
            data[name] = val

    if not data:
        raise RuntimeError(f"所有数据源均失败: {'; '.join(errors)}")

    return {
        "asin": asin,
        "country": country,
        "traffic_terms": data.get("product_traffic_terms"),
        "trend": data.get("product_trend"),
        "report": data.get("product_detail"),
        "errors": errors,
    }
