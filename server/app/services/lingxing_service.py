"""领星 (LingXing) ERP gateway — the single chokepoint for all LingXing MCP traffic.

Design (see project_lingxing_mcp): panels, AI analysis, and the weekly
automation ALL go through this module. Agents never talk to LingXing directly
and never see the key — the gateway:

* holds ``X-Mcp-Key`` server-side and injects it on every request;
* enforces a **global 1 request/second** token bucket (LingXing hard limit);
* classifies every tool read vs write and **gates writes** behind the master +
  operate switches (read-only until both are on);
* writes a full **audit row** for every call (read/write/probe), including
  denials and errors;
* speaks MCP **Streamable HTTP** (JSON-RPC 2.0) over httpx — no extra deps.

P0 scope: client + limiter + classification + audit + read passthrough +
``probe`` (the live ``tools/list`` that resolves whether ad-write tools exist).
The write-execution path (triple review, guardrails, rollback) lands in P3 and
will reuse :func:`is_operate_active`, :func:`classify_tool`, and the audit table.
"""
from __future__ import annotations

import asyncio
import logging
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from app.core import hub_settings as _hs
from app.core.config import settings
from app.services import lingxing_openapi as _openapi

logger = logging.getLogger("ivyea.services.lingxing_service")

# --- MCP protocol constants -------------------------------------------------
_PROTOCOL_VERSION = "2025-06-18"
_CLIENT_INFO = {"name": "ivyea-ops-lingxing-gateway", "version": "0.1.0"}
_REQUEST_TIMEOUT_S = 60.0
_RATE_MIN_INTERVAL_S = 1.05  # >= 1/s with a small safety margin


class LingXingError(RuntimeError):
    """Any gateway-level failure (config, transport, protocol, or policy)."""


# --- tool read/write classification ----------------------------------------
# Known tools from the official docs (authoritative for these names). The live
# ``probe`` may surface more; unknown tools default to WRITE (gated) so a
# never-before-seen tool can never slip through as an unguarded read.
_KNOWN_READ = {
    "get_fba_stock_list", "erp_listing", "query_product_performance_asin_lists",
    "get_profit_report_msku", "query_order_profit_list_gross_profit",
    "query_erp_keyword_ranking_keyword", "query_erp_keyword_ranking_asin",
    "query_erp_competitive_monitor", "query_erp_follow_sale_monitor",
    "query_erp_new_monitor",
}
_KNOWN_WRITE = {
    "create_erp_keyword", "create_erp_competitive_monitor",
    "create_erp_follow_sale_monitor", "create_erp_new_monitor",
}
_READ_PREFIXES = ("get_", "query_", "list_", "search_", "fetch_", "describe_",
                  "read_", "export_", "show_", "find_")
_WRITE_PREFIXES = ("create_", "update_", "delete_", "set_", "modify_", "adjust_",
                   "add_", "remove_", "edit_", "put_", "post_", "sync_", "batch_",
                   "enable_", "disable_", "pause_", "start_", "stop_", "apply_",
                   "submit_", "cancel_", "archive_", "bid_", "budget_")


def classify_tool(name: str) -> str:
    """Return ``read`` | ``write`` | ``unknown`` for a tool name.

    ``unknown`` is treated as write everywhere a gate is applied — fail closed.
    """
    n = (name or "").strip().lower()
    if not n:
        return "unknown"
    if n in _KNOWN_READ:
        return "read"
    if n in _KNOWN_WRITE:
        return "write"
    if n.startswith(_WRITE_PREFIXES):
        return "write"
    if n.startswith(_READ_PREFIXES):
        return "read"
    return "unknown"


# --- audit store ------------------------------------------------------------
def _db_path() -> Path:
    return settings.data_dir / "lingxing.sqlite3"


def _connect() -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path()), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def connect() -> sqlite3.Connection:
    """Public handle to the shared lingxing SQLite db (audit + cache + tickets)."""
    return _connect()


def init_db() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lingxing_audit (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT NOT NULL,
                caller      TEXT,            -- panel|agent|automation|probe|service
                tool        TEXT,
                kind        TEXT,            -- read|write|unknown|meta
                args_json   TEXT,           -- redacted/truncated args
                ok          INTEGER,         -- 1/0
                status      TEXT,            -- ok|denied|blocked|error
                detail      TEXT,
                latency_ms  INTEGER
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_lingxing_audit_ts ON lingxing_audit(ts)"
        )
        # Local read cache / warehouse (regulates the rate limit, instant panels).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lingxing_cache (
                dataset     TEXT NOT NULL,
                params_hash TEXT NOT NULL,
                params_json TEXT,
                payload_json TEXT,
                synced_at   TEXT,
                PRIMARY KEY (dataset, params_hash)
            )
            """
        )
        # Weekly advisory automation runs (P2 — analyse + recommend only).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lingxing_auto_runs (
                id          TEXT PRIMARY KEY,
                started_at  TEXT,
                finished_at TEXT,
                status      TEXT,            -- collecting|analyzing|done|failed
                trigger     TEXT,            -- scheduled|manual
                scope_json  TEXT,
                metrics_json TEXT,
                proposals_json TEXT,
                summary     TEXT,
                error       TEXT
            )
            """
        )
        # P3 operation tickets (write ops: review → guardrails → human → execute).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lingxing_op_ticket (
                id          TEXT PRIMARY KEY,
                created_at  TEXT,
                updated_at  TEXT,
                source      TEXT,            -- run:<id> | manual
                status      TEXT,
                intent_json TEXT,
                reviews_json TEXT,
                guardrail_json TEXT,
                snapshot_json TEXT,          -- pre-execution state for rollback
                result_json TEXT,
                decided_by  TEXT,
                error       TEXT
            )
            """
        )
        # Rule-engine optimizer runs (background jobs with progress + result).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lingxing_optimizer_runs (
                id          TEXT PRIMARY KEY,
                sid         INTEGER,
                started_at  TEXT,
                finished_at TEXT,
                status      TEXT,            -- running|done|failed
                phase       TEXT,            -- human-readable current step
                done        INTEGER,         -- progress numerator
                total       INTEGER,         -- progress denominator
                summary     TEXT,
                result_json TEXT,
                error       TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _audit(caller: str, tool: str, kind: str, args: Any, ok: bool,
           status: str, detail: str = "", latency_ms: int = 0) -> None:
    try:
        raw = json.dumps(args, ensure_ascii=False, default=str) if args is not None else ""
        if len(raw) > 2000:
            raw = raw[:2000] + "…"
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO lingxing_audit "
                "(ts,caller,tool,kind,args_json,ok,status,detail,latency_ms) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), caller, tool, kind, raw,
                 1 if ok else 0, status, (detail or "")[:500], int(latency_ms)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # Audit must never break the call path; swallow.
        logger.debug("json.dumps 失败（旁路，已忽略）", exc_info=True)


def recent_audit(limit: int = 100) -> List[Dict[str, Any]]:
    try:
        conn = _connect()
        try:
            cur = conn.execute(
                "SELECT ts,caller,tool,kind,ok,status,detail,latency_ms "
                "FROM lingxing_audit ORDER BY id DESC LIMIT ?", (int(limit),))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


# --- global rate limiter (1 req/s) ------------------------------------------
class _RateLimiter:
    """Process-wide async limiter: serialises queries and spaces them >= 1s."""

    def __init__(self, min_interval: float) -> None:
        self._min = min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._min - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


_limiter = _RateLimiter(_RATE_MIN_INTERVAL_S)          # MCP backend (1/s hard limit)
_openapi_limiter = _RateLimiter(0.34)                 # OpenAPI backend (configurable)


# --- config helpers ---------------------------------------------------------
def _cfg() -> Dict[str, Any]:
    return _hs.load()


def _key() -> str:
    return (_hs.get("lingxing_mcp_key") or "").strip()


def _url() -> str:
    return (_hs.get("lingxing_mcp_url") or "").strip()


def is_master_enabled() -> bool:
    return bool(_hs.get("lingxing_enabled"))


def is_operate_active() -> bool:
    """Write switch is active only if master+operate are on and not expired."""
    if not is_master_enabled():
        return False
    if not bool(_hs.get("lingxing_operate_enabled")):
        return False
    exp = (_hs.get("lingxing_operate_expires_at") or "").strip()
    if exp:
        try:
            if datetime.now(timezone.utc) >= datetime.fromisoformat(exp):
                return False
        except ValueError:
            return False  # unparseable expiry => treat as expired (fail closed)
    return True


def _ticket_counts() -> Dict[str, int]:
    """Ticket status counts for the UI badge (awaiting_human / reviewing)."""
    out = {"awaiting_human": 0, "reviewing": 0, "executing": 0}
    try:
        conn = _connect()
        try:
            cur = conn.execute(
                "SELECT status, COUNT(*) FROM lingxing_op_ticket "
                "WHERE status IN ('awaiting_human','reviewing','executing') GROUP BY status")
            for status_, n in cur.fetchall():
                out[str(status_)] = int(n)
        finally:
            conn.close()
    except Exception:
        logger.debug("_connect 失败（旁路，已忽略）", exc_info=True)
    return out


def status() -> Dict[str, Any]:
    """Config/health snapshot for the UI (no secrets)."""
    cfg = _cfg()
    exp = (cfg.get("lingxing_operate_expires_at") or "").strip()
    remaining = 0
    if exp:
        try:
            remaining = max(0, int(
                (datetime.fromisoformat(exp) - datetime.now(timezone.utc)).total_seconds()))
        except ValueError:
            remaining = 0
    return {
        "key_present": bool(_key()),
        "url": _url(),
        "openapi_configured": _openapi.is_configured(),
        "openapi_host": (cfg.get("lingxing_openapi_host") or ""),
        "master_enabled": is_master_enabled(),
        "operate_enabled": bool(cfg.get("lingxing_operate_enabled")),
        "operate_active": is_operate_active(),
        "operate_expires_at": exp,
        "operate_remaining_seconds": remaining,
        "require_human": bool(cfg.get("lingxing_operate_require_human")),
        "circuit_reason": cfg.get("lingxing_circuit_reason") or "",
        "scope_stores": cfg.get("lingxing_scope_stores") or "",
        "scope_asins": cfg.get("lingxing_scope_asins") or "",
        "max_ops_per_run": cfg.get("lingxing_max_ops_per_run"),
        "max_change_pct": cfg.get("lingxing_max_change_pct"),
        "ticket_counts": _ticket_counts(),
    }


# --- MCP Streamable HTTP client ---------------------------------------------
def _parse_jsonrpc(resp: httpx.Response) -> Dict[str, Any]:
    """Extract the JSON-RPC message from a response that is either
    ``application/json`` (single object) or ``text/event-stream`` (SSE)."""
    ctype = (resp.headers.get("content-type") or "").lower()
    if "text/event-stream" in ctype:
        last: Optional[Dict[str, Any]] = None
        for line in resp.text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            # Prefer a message carrying result/error (the response to our call).
            if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                last = obj
        if last is None:
            raise LingXingError("SSE 响应未包含 JSON-RPC 结果")
        return last
    try:
        return resp.json()
    except json.JSONDecodeError as e:
        raise LingXingError(f"非法 JSON 响应: {resp.text[:200]}") from e


class _McpSession:
    """One MCP session: initialize once, then call tools. Async context manager.

    Handshake (``initialize`` + ``notifications/initialized``) is NOT rate
    limited; only actual ``tools/*`` queries pass through the limiter.
    """

    def __init__(self, *, rate_limited: bool = True) -> None:
        url, key = _url(), _key()
        if not url:
            raise LingXingError("未配置领星 MCP 地址 (lingxing_mcp_url)")
        if not key:
            raise LingXingError("未配置领星 MCP 密钥 (lingxing_mcp_key)")
        self._url = url
        self._headers = {
            "X-Mcp-Key": key,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self._rate_limited = rate_limited
        self._client: Optional[httpx.AsyncClient] = None
        self._session_id: Optional[str] = None
        self._server_proto: Optional[str] = None

    async def __aenter__(self) -> "_McpSession":
        self._client = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S)
        await self._initialize()
        return self

    async def __aexit__(self, *exc) -> None:
        # Best-effort session termination + client close.
        try:
            if self._client and self._session_id:
                await self._client.delete(self._url, headers=self._req_headers())
        except Exception:
            logger.debug("self._client.delete 失败（旁路，已忽略）", exc_info=True)
        if self._client:
            await self._client.aclose()
        self._client = None

    def _req_headers(self) -> Dict[str, str]:
        h = dict(self._headers)
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        if self._server_proto:
            h["MCP-Protocol-Version"] = self._server_proto
        return h

    async def _post(self, body: Dict[str, Any]) -> httpx.Response:
        assert self._client is not None
        try:
            return await self._client.post(self._url, headers=self._req_headers(),
                                           content=json.dumps(body, ensure_ascii=False))
        except httpx.HTTPError as e:
            raise LingXingError(f"领星 MCP 连接失败: {e}") from e

    async def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method}
        if params is not None:
            body["params"] = params
        resp = await self._post(body)
        if resp.status_code >= 400:
            raise LingXingError(f"领星 MCP HTTP {resp.status_code}: {resp.text[:200]}")
        msg = _parse_jsonrpc(resp)
        if isinstance(msg, dict) and msg.get("error"):
            err = msg["error"]
            raise LingXingError(f"领星 MCP 错误 {err.get('code')}: {err.get('message')}")
        return msg.get("result") if isinstance(msg, dict) else None

    async def _initialize(self) -> None:
        body = {
            "jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": _CLIENT_INFO,
            },
        }
        resp = await self._post(body)
        if resp.status_code >= 400:
            raise LingXingError(f"领星 MCP 初始化 HTTP {resp.status_code}: {resp.text[:200]}")
        self._session_id = resp.headers.get("mcp-session-id") or resp.headers.get("Mcp-Session-Id")
        result = _parse_jsonrpc(resp)
        if isinstance(result, dict):
            inner = result.get("result") or {}
            self._server_proto = inner.get("protocolVersion") or _PROTOCOL_VERSION
        # Notify initialized (notification: no id, 202 expected).
        try:
            await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except LingXingError:
            logger.debug("self._post 失败（旁路，已忽略）", exc_info=True)

    async def list_tools(self) -> List[Dict[str, Any]]:
        if self._rate_limited:
            await _limiter.acquire()
        result = await self._rpc("tools/list")
        tools = (result or {}).get("tools") if isinstance(result, dict) else None
        return list(tools or [])

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        if self._rate_limited:
            await _limiter.acquire()
        return await self._rpc("tools/call", {"name": name, "arguments": arguments or {}})


# --- public gateway API -----------------------------------------------------
async def probe(caller: str = "probe") -> Dict[str, Any]:
    """Connect with the configured key, run ``tools/list``, classify each tool.

    This is the P0 headline: it tells us the **real** tool inventory (and thus
    whether ad-write tools exist). Allowed whenever a key is present, even if
    the master switch is off — it is a read-only setup diagnostic.
    """
    t0 = time.monotonic()
    if not _key():
        _audit(caller, "tools/list", "meta", None, False, "denied", "no key")
        raise LingXingError("未配置领星 MCP 密钥，无法实测")
    try:
        async with _McpSession() as s:
            tools = await s.list_tools()
    except LingXingError as e:
        _audit(caller, "tools/list", "meta", None, False, "error", str(e),
               int((time.monotonic() - t0) * 1000))
        raise
    classified = {"read": [], "write": [], "unknown": []}
    for t in tools:
        name = t.get("name", "")
        kind = classify_tool(name)
        classified[kind].append({
            "name": name,
            "description": (t.get("description") or "")[:300],
        })
    out = {
        "tool_count": len(tools),
        "read": classified["read"],
        "write": classified["write"],
        "unknown": classified["unknown"],
        "has_write_tools": bool(classified["write"] or classified["unknown"]),
    }
    _audit(caller, "tools/list", "meta", {"count": len(tools)}, True, "ok",
           f"read={len(classified['read'])} write={len(classified['write'])} "
           f"unknown={len(classified['unknown'])}", int((time.monotonic() - t0) * 1000))
    return out


async def call_tool(tool: str, arguments: Optional[Dict[str, Any]] = None, *,
                    caller: str = "service", allow_write: bool = False) -> Any:
    """Policy-enforced tool call. P0 supports reads; writes require both the
    operate switch active AND ``allow_write`` (the P3 execution path passes it
    only after triple-review + human confirmation)."""
    arguments = arguments or {}
    if not is_master_enabled():
        _audit(caller, tool, "meta", arguments, False, "denied", "master disabled")
        raise LingXingError("领星集成未启用（总开关关闭）")
    kind = classify_tool(tool)
    if kind != "read":
        if not (allow_write and is_operate_active()):
            _audit(caller, tool, kind, arguments, False, "blocked",
                   "write blocked (operate switch off / not authorised)")
            raise LingXingError(f"写操作被拦截：{tool}（操作开关未开启或未授权）")
    t0 = time.monotonic()
    try:
        async with _McpSession() as s:
            result = await s.call_tool(tool, arguments)
    except LingXingError as e:
        _audit(caller, tool, kind, arguments, False, "error", str(e),
               int((time.monotonic() - t0) * 1000))
        raise
    _audit(caller, tool, kind, arguments, True, "ok", "",
           int((time.monotonic() - t0) * 1000))
    return result


async def call_read(tool: str, arguments: Optional[Dict[str, Any]] = None, *,
                    caller: str = "panel") -> Any:
    """Convenience for the read-only data plane (panels / AI analysis) — MCP."""
    if classify_tool(tool) != "read":
        raise LingXingError(f"{tool} 非只读工具，请走受控写通道")
    return await call_tool(tool, arguments, caller=caller, allow_write=False)


# --- OpenAPI backend (data + write operations) ------------------------------
async def call_openapi(route: str, params: Optional[Dict[str, Any]] = None, *,
                       method: str = "POST", caller: str = "service",
                       allow_write: bool = False) -> Any:
    """Policy-enforced LingXing OpenAPI call (same gating model as the MCP path).

    Reads pass when the master switch is on; writes additionally require the
    operate switch active AND ``allow_write`` (set only by the P3 execution path
    after triple-review + human confirmation)."""
    params = params or {}
    if not is_master_enabled():
        _audit(caller, route, "meta", params, False, "denied", "master disabled")
        raise LingXingError("领星集成未启用（总开关关闭）")
    kind = _openapi.classify_route(route)
    if kind != "read":
        if not (allow_write and is_operate_active()):
            _audit(caller, route, kind, params, False, "blocked",
                   "write blocked (operate switch off / not authorised)")
            raise LingXingError(f"写操作被拦截：{route}（操作开关未开启或未授权）")
    # pace per configured OpenAPI interval
    try:
        _openapi_limiter._min = max(0.05, float(_hs.get("lingxing_openapi_min_interval_ms")) / 1000.0)
    except (TypeError, ValueError):
        logger.debug("_openapi_limiter._min = max 失败（旁路，已忽略）", exc_info=True)
    await _openapi_limiter.acquire()
    t0 = time.monotonic()
    try:
        result = await _openapi.call(route, params, method=method)
    except _openapi.LingXingOpenAPIError as e:
        _audit(caller, route, kind, params, False, "error", str(e),
               int((time.monotonic() - t0) * 1000))
        raise LingXingError(str(e)) from e
    _audit(caller, route, kind, params, True, "ok", "",
           int((time.monotonic() - t0) * 1000))
    return result


async def call_openapi_read(route: str, params: Optional[Dict[str, Any]] = None, *,
                            method: str = "POST", caller: str = "panel") -> Any:
    if _openapi.classify_route(route) != "read":
        raise LingXingError(f"{route} 命中写标记，请走受控写通道")
    return await call_openapi(route, params, method=method, caller=caller, allow_write=False)


async def openapi_verify(caller: str = "probe") -> Dict[str, Any]:
    """End-to-end credential + signature check (token + one signed read)."""
    t0 = time.monotonic()
    try:
        res = await _openapi.verify()
    except _openapi.LingXingOpenAPIError as e:
        _audit(caller, "openapi/verify", "meta", None, False, "error", str(e),
               int((time.monotonic() - t0) * 1000))
        raise LingXingError(str(e)) from e
    _audit(caller, "openapi/verify", "meta", None, True, "ok",
           f"sellers={res.get('probe_seller_count')}", int((time.monotonic() - t0) * 1000))
    return res
