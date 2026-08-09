"""Sorftime MCP HTTP client + two-phase data pipelines.

Keyword pipeline  (10 calls, 2 phases):
  Phase 1 (concurrent): keyword_detail, keyword_trend, keyword_extends,
                         keyword_search_results, category_search_from_product_name,
                         similar_product_feature
  Phase 2 (depends on phase 1): product_detail×2, potential_product,
                                  category_report (top-100 products in category)

ASIN pipeline (8 calls, 2 phases):
  Phase 1 (concurrent): product_detail, product_trend, product_traffic_terms,
                         product_reviews, product_variations
  Phase 2 (main traffic keyword + own sub-category): keyword_detail,
                         keyword_search_results, competitor_product_keywords, category_report
"""
from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

import httpx
from app.core import secret_env as _secret_env

logger = logging.getLogger("ivyea.services.sorftime_service")

_log = logging.getLogger(__name__)

_SORFTIME_BASE = "https://mcp.sorftime.com"

_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

_TOOL_TIMEOUT = 30.0
_CONN_TIMEOUT = 10.0


def _url() -> str:
    from app.core import hub_settings
    key = str(hub_settings.get("sorftime_key") or _secret_env.get("SORFTIME_KEY", "")).strip()
    if not key:
        raise RuntimeError("Sorftime Key 未配置，请在系统配置 → 市场数据中保存后重试")
    return f"{_SORFTIME_BASE}?key={key}"


_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def normalize_args(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Lower-snake-case every argument key.

    Sorftime's MCP schema is snake_case throughout (``amz_site``,
    ``keyword_support_site``, ``product_name``, ``node_id``, ``start_date`` …).
    A camelCase key is not rejected — it is silently ignored, so a call missing
    ``amz_site`` comes back as a *successful* result whose text reads
    "Please specify the site to query", and required-but-misnamed params make
    the tool blow up server-side.  Normalising here means no call site can
    reintroduce the bug.
    """
    return {_CAMEL_RE.sub("_", k).lower(): v for k, v in (arguments or {}).items()}


async def _call_tool(
    client: httpx.AsyncClient,
    tool_name: str,
    arguments: Dict[str, Any],
    call_id: int = 1,
) -> Any:
    """Call a single Sorftime MCP tool. Returns parsed result content or raises."""
    import json as _json

    payload = {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": normalize_args(arguments)},
    }
    resp = await client.post(_url(), json=payload, headers=_HEADERS)
    resp.raise_for_status()

    # Sorftime returns SSE format: "event: message\ndata: {...}\n\n"
    body = None
    for line in resp.text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            raw = line[5:].strip()
            if raw:
                try:
                    body = _json.loads(raw)
                    break
                except Exception:
                    logger.debug("_json.loads 失败（旁路，已忽略）", exc_info=True)

    if body is None:
        raise RuntimeError(f"sorftime/{tool_name}: could not parse SSE response")

    if "error" in body:
        raise RuntimeError(f"sorftime/{tool_name} error: {body['error']}")

    result = body.get("result", {})

    # isError flag indicates auth failure or tool-level error
    if result.get("isError"):
        content_list = result.get("content", [])
        msg = next(
            (c.get("text", "") for c in content_list if isinstance(c, dict) and c.get("type") == "text"),
            "unknown error",
        )
        raise RuntimeError(f"sorftime/{tool_name}: {msg}")

    content = result.get("content", [])
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and first.get("type") == "text":
            text = first.get("text", "")
            try:
                return _json.loads(text)
            except Exception:
                # Not JSON. Most tools answer with JSON, a few (product_trend)
                # answer with a plain data line — but Sorftime also reports some
                # failures as *successful* prose (no isError, HTTP 200). Letting
                # that prose through is what made the market panel "analyse" an
                # error message instead of data, so reject it explicitly.
                if _is_non_data_text(text):
                    raise RuntimeError(f"sorftime/{tool_name}: {str(text).strip()[:300]}")
                return text
    return result


# Prose Sorftime returns *in a success envelope* when the call cannot produce
# data: a missing/mistyped site param, an auth problem, or a tool that is really
# a how-to (product_report just explains which other tools to combine).
_NON_DATA_MARKERS = (
    "please specify the site",
    "parameter description in the method signature",
    "authentication required",
    "call the following tools to combine their data",
)


def _is_non_data_text(text: Any) -> bool:
    low = str(text).strip().lower()
    return any(marker in low for marker in _NON_DATA_MARKERS)


def unwrap(payload: Any) -> Any:
    """Return the payload's data node.

    Sorftime wraps every JSON answer as ``{"doc": {field: description…},
    "data": <dict|list>}`` — ``doc`` is a field dictionary for the model, the
    real content is ``data``. Consumers should read through this helper so they
    keep working if a tool answers unwrapped.
    """
    if isinstance(payload, dict) and "data" in payload and isinstance(payload.get("doc"), dict):
        return payload["data"]
    return payload


# Row containers seen across the tool set: the data node is either the list
# itself, or a dict holding one (``top100_products`` for category reports).
_ROW_KEYS = ("data", "items", "results", "list", "top100_products", "analysis_results")


def rows(payload: Any) -> List[Dict[str, Any]]:
    """List-of-dict rows out of any tool answer, envelope-agnostic."""
    node = unwrap(payload)
    if isinstance(node, list):
        return [r for r in node if isinstance(r, dict)]
    if isinstance(node, dict):
        for key in _ROW_KEYS:
            arr = node.get(key)
            if isinstance(arr, list):
                return [r for r in arr if isinstance(r, dict)]
    return []


def record(payload: Any) -> Dict[str, Any]:
    """Single-record answers (product_detail / keyword_detail) as a flat dict."""
    node = unwrap(payload)
    if isinstance(node, dict):
        return node
    if isinstance(node, list) and node and isinstance(node[0], dict):
        return node[0]
    return {}


def compact(payload: Any, limit: int = 30) -> Any:
    """Cap every row list in a tool answer at ``limit`` rows.

    A full keyword pipeline is ~54KB of JSON (category_report alone carries 100
    products), so dumping it whole into a prompt hits the size cut and the last
    sources get chopped mid-JSON. Trimming each source instead keeps all of them
    represented — the aggregate stats blocks (category_stats_report …) are
    dicts and survive untouched.
    """
    node = unwrap(payload)
    trimmed: Any = node
    if isinstance(node, list) and len(node) > limit:
        trimmed = node[:limit]
    elif isinstance(node, dict):
        for key in _ROW_KEYS:
            arr = node.get(key)
            if isinstance(arr, list) and len(arr) > limit:
                if trimmed is node:
                    trimmed = dict(node)
                trimmed[key] = arr[:limit]
    if trimmed is node:
        return payload
    if isinstance(payload, dict) and "data" in payload and isinstance(payload.get("doc"), dict):
        return {**payload, "data": trimmed}
    return trimmed


def compact_all(data: Dict[str, Any], limit: int = 30) -> Dict[str, Any]:
    """``compact`` across a whole pipeline result (values may be lists of answers)."""
    out: Dict[str, Any] = {}
    for name, value in (data or {}).items():
        if name.endswith("_list") and isinstance(value, list):
            out[name] = [compact(item, limit) for item in value]
        else:
            out[name] = compact(value, limit)
    return out


def summarize_for_prompt(data: Dict[str, Any], budget: int = 45000) -> str:
    """JSON dump of a pipeline result that fits ``budget`` chars *and* keeps
    every source in it.

    A full keyword pipeline is ~148KB — dumping it and cutting the tail drops
    whole sources (the category report and potential products sit last), so the
    model writes those chapters with nothing to go on. Here each source gets an
    equal share of the budget and is thinned by rows until it fits, so every
    tool that answered is represented.
    """
    import json as _json

    sources = list((data or {}).items())
    if not sources:
        return "{}"
    share = max(budget // len(sources), 800)
    parts: List[str] = []
    for name, value in sources:
        limit = 30
        text = _json.dumps(value, ensure_ascii=False, indent=2)
        while len(text) > share and limit > 1:
            limit = max(1, limit // 2)
            trimmed = ([compact(v, limit) for v in value]
                       if name.endswith("_list") and isinstance(value, list)
                       else compact(value, limit))
            text = _json.dumps(trimmed, ensure_ascii=False, indent=2)
        if len(text) > share:
            text = text[:share] + "\n…(本项已截断)"
        parts.append(f'  "{name}": {text}')
    return "{\n" + ",\n".join(parts) + "\n}"


def first_field(payload: Any, *names: str) -> str:
    """First non-empty value of ``names`` from a record or the first row."""
    for src in (record(payload), *rows(payload)[:1]):
        for name in names:
            val = src.get(name)
            if val not in (None, "", []):
                return str(val)
    return ""


async def _safe_call(
    client: httpx.AsyncClient,
    tool_name: str,
    arguments: Dict[str, Any],
    call_id: int = 1,
) -> Tuple[str, Any, Optional[str]]:
    """Wrapper that returns (tool_name, result_or_None, error_or_None)."""
    try:
        data = await asyncio.wait_for(
            _call_tool(client, tool_name, arguments, call_id),
            timeout=_TOOL_TIMEOUT,
        )
        return tool_name, data, None
    except asyncio.TimeoutError:
        _log.warning("sorftime/%s timed out", tool_name)
        return tool_name, None, f"{tool_name} 超时"
    except Exception as exc:
        _log.warning("sorftime/%s failed: %s", tool_name, exc)
        return tool_name, None, f"{tool_name}: {exc}"


@asynccontextmanager
async def _make_client():
    """Async context manager that creates an httpx client and performs the
    MCP initialize handshake required by Sorftime before any tool/call."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(_TOOL_TIMEOUT, connect=_CONN_TIMEOUT),
        limits=httpx.Limits(max_connections=20),
    ) as client:
        try:
            init_payload = {
                "jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "IvyeaOps", "version": "1.0"},
                },
            }
            resp = await asyncio.wait_for(
                client.post(_url(), json=init_payload, headers=_HEADERS),
                timeout=_CONN_TIMEOUT,
            )
            resp.raise_for_status()
            _log.debug("sorftime initialize ok")
        except Exception as exc:
            _log.warning("sorftime initialize failed: %s", exc)
        yield client


# ─── Progress callback type ───────────────────────────────────────────────────

ProgressCb = Callable[[str, int, int], Coroutine[Any, Any, None]]


async def keyword_pipeline(
    keyword: str,
    marketplace: str,
    on_progress: Optional[ProgressCb] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Run full keyword research pipeline. Returns (data_dict, error_list)."""
    total = 10
    done = 0
    errors: List[str] = []
    data: Dict[str, Any] = {}

    async def progress(step: str) -> None:
        nonlocal done
        done += 1
        if on_progress:
            await on_progress(step, done, total)

    async with _make_client() as client:
        # ── Phase 1: 6 concurrent calls ──────────────────────────────────────
        phase1_tasks = [
            _safe_call(client, "keyword_detail",
                       {"keyword": keyword, "keyword_support_site": marketplace}, 1),
            _safe_call(client, "keyword_trend",
                       {"keyword": keyword, "keyword_support_site": marketplace}, 2),
            _safe_call(client, "keyword_extends",
                       {"keyword": keyword, "keyword_support_site": marketplace}, 3),
            _safe_call(client, "keyword_search_results",
                       {"keyword": keyword, "keyword_support_site": marketplace}, 4),
            _safe_call(client, "category_search_from_product_name",
                       {"product_name": keyword, "amz_site": marketplace}, 5),
            _safe_call(client, "similar_product_feature",
                       {"product_name": keyword, "amz_site": marketplace}, 6),
        ]
        results = await asyncio.gather(*phase1_tasks)
        for name, val, err in results:
            if err:
                errors.append(err)
            else:
                data[name] = val
            await progress(name)

        # ── Phase 2: depends on phase 1 results ──────────────────────────────
        top_asins = [
            str(row["asin"]) for row in rows(data.get("keyword_search_results"))[:2]
            if row.get("asin")
        ]
        node_id = first_field(data.get("category_search_from_product_name"), "node_id")

        phase2_tasks = []
        if top_asins:
            for i, asin in enumerate(top_asins[:2]):
                phase2_tasks.append(
                    _safe_call(client, "product_detail",
                               {"asin": asin, "amz_site": marketplace}, 10 + i)
                )
        phase2_tasks.append(
            _safe_call(client, "potential_product",
                       {"search_name": keyword, "amz_site": marketplace}, 12)
        )
        if node_id:
            phase2_tasks.append(
                _safe_call(client, "category_report",
                           {"node_id": node_id, "amz_site": marketplace}, 13)
            )
        else:
            # No nodeId found — skip category_report and pad progress counter
            await progress("(category_report_skipped)")

        results2 = await asyncio.gather(*phase2_tasks)
        for i, (name, val, err) in enumerate(results2):
            if err:
                errors.append(err)
            else:
                if name == "product_detail":
                    existing = data.get("product_detail_list", [])
                    existing.append(val)
                    data["product_detail_list"] = existing
                else:
                    data[name] = val
            await progress(name)

    return data, errors


async def asin_pipeline(
    asin: str,
    marketplace: str,
    on_progress: Optional[ProgressCb] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Run full ASIN research pipeline. Returns (data_dict, error_list)."""
    total = 9
    done = 0
    errors: List[str] = []
    data: Dict[str, Any] = {}

    async def progress(step: str) -> None:
        nonlocal done
        done += 1
        if on_progress:
            await on_progress(step, done, total)

    async with _make_client() as client:
        # ── Phase 1: 5 concurrent calls ──────────────────────────────────────
        # product_detail (not product_report): product_report is a how-to tool —
        # for every ASIN it answers with "call the following tools to combine
        # their data", never with data. product_detail carries the core metrics
        # (price / sales / rating / node_id / gross margin) it points at.
        phase1_tasks = [
            _safe_call(client, "product_detail",
                       {"asin": asin, "amz_site": marketplace}, 1),
            _safe_call(client, "product_trend",
                       {"asin": asin, "amz_site": marketplace}, 2),
            _safe_call(client, "product_traffic_terms",
                       {"asin": asin, "amz_site": marketplace}, 3),
            _safe_call(client, "product_reviews",
                       {"asin": asin, "amz_site": marketplace}, 4),
            _safe_call(client, "product_variations",
                       {"asin": asin, "amz_site": marketplace}, 5),
        ]
        results = await asyncio.gather(*phase1_tasks)
        for name, val, err in results:
            if err:
                errors.append(err)
            else:
                data[name] = val
            await progress(name)

        # ── Phase 2: use main traffic keyword for market context ──────────────
        main_kw = first_field(data.get("product_traffic_terms"), "keyword", "term", "search_term")
        # The product's own sub-category top-100 — the market it competes in.
        node_id = first_field(data.get("product_detail"), "node_id")

        phase2_tasks = []
        if main_kw:
            phase2_tasks = [
                _safe_call(client, "keyword_detail",
                           {"keyword": main_kw, "keyword_support_site": marketplace}, 10),
                _safe_call(client, "keyword_search_results",
                           {"keyword": main_kw, "keyword_support_site": marketplace}, 11),
                _safe_call(client, "competitor_product_keywords",
                           {"asin": asin, "keyword_support_site": marketplace}, 12),
            ]
        else:
            phase2_tasks = [
                _safe_call(client, "competitor_product_keywords",
                           {"asin": asin, "keyword_support_site": marketplace}, 12),
            ]
            # Pad progress for skipped calls
            for _ in range(2):
                await progress("(skipped)")

        if node_id:
            phase2_tasks.append(
                _safe_call(client, "category_report",
                           {"node_id": node_id, "amz_site": marketplace}, 13)
            )
        else:
            await progress("(category_report_skipped)")

        results2 = await asyncio.gather(*phase2_tasks)
        for name, val, err in results2:
            if err:
                errors.append(err)
            else:
                data[name] = val
            await progress(name)

    return data, errors
