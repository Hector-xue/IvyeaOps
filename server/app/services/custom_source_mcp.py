"""自定义数据源的通用 MCP 客户端。

和内置的两个客户端（sorftime_service / sellersprite_service）刻意分开：那两个
是按各自服务器的真实行为写死的，改动它们等于动生产链路。这里只做**通用**的一
份，所有服务器差异都从注册表配置里读。

三个必须做对的地方（都是 ops 在内置源上真踩过的）：
  1. 响应可能是 SSE（``data: {...}``）也可能是裸 JSON —— 两种都要认。
  2. **失败会装在成功响应里**：HTTP 200 + ``isError`` 或 ``{"code":"ERR"}``，
     甚至是一句 "Please specify the site to query" 的纯文本。不识别就会把
     "报错" 当数据喂给模型，模型照着编。
  3. 信封五花八门（``{doc,data}`` / ``{code,data}`` / 裸数组），拆不对就是
     一路 None。
"""
from __future__ import annotations

import json as _json
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urlparse, urlunparse

import httpx

logger = logging.getLogger("ivyea.services.custom_source_mcp")

_CONN_TIMEOUT = 10.0

_ACCEPT = "application/json, text/event-stream"

# 工具"成功"返回但内容其实是一句提示语时的特征词。命中就当错误抛，
# 而不是把这句话当数据往下传。
_NON_DATA_MARKERS = (
    "please specify", "please provide", "not found", "no data",
    "invalid", "unauthorized", "forbidden", "quota", "rate limit",
    "请指定", "请提供", "未找到", "无数据", "无效", "未授权", "额度",
)

# 数据节点的常见容器名，按顺序试。后两个是 Sorftime 的类目报告用的名字 ——
# 列在这里是因为"自建网关转发内置源"是最常见的第一个自定义源。
_DATA_KEYS = ("data", "items", "results", "list", "rows", "records",
              "top100_products", "analysis_results")

# 信封的伴生字段。一个 dict 里除了 data 就只剩这些名字时，它就是个信封，
# 真内容在 data 里。列举法比"猜"稳：漏一个只是少拆一层（还能靠显式 envelope
# 配置补救），认错一个则会把真数据当信封扔掉。
_META_KEYS = {
    "doc", "code", "message", "msg", "success", "status", "total", "count",
    "page", "size", "pages", "errno", "error_code", "request_id", "requestid",
    "traceid", "trace_id", "timestamp", "cost", "state",
}


class CustomSourceError(RuntimeError):
    """调用自定义源失败 —— 消息会原样显示给用户，写清楚是哪一步坏的。"""


def _endpoint(cfg: Dict[str, Any]) -> str:
    url = str(cfg.get("url") or "").strip()
    auth = cfg.get("auth") or {}
    # "auto" 是自动配置**还没跑**的状态。真到了调用这一步，按最常见的 ?key= 走 ——
    # 自动配置跑过一次就会把它写成具体档位，正常流程不会停在 auto。
    if str(auth.get("mode")) in ("query", "auto") and auth.get("value"):
        parts = urlparse(url)
        query = parts.query
        extra = urlencode({str(auth.get("name") or "key"): str(auth["value"])})
        parts = parts._replace(query=f"{query}&{extra}" if query else extra)
        return urlunparse(parts)
    return url


def _headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    out = {"Content-Type": "application/json", "Accept": _ACCEPT}
    for key, value in (cfg.get("headers") or {}).items():
        if str(value or "").strip():
            out[str(key)] = str(value)
    auth = cfg.get("auth") or {}
    mode = str(auth.get("mode") or "none")
    value = str(auth.get("value") or "")
    if value and mode == "header":
        out[str(auth.get("name") or "Authorization")] = value
    elif value and mode == "bearer":
        out["Authorization"] = f"Bearer {value}"
    return out


def parse_body(text: str) -> Dict[str, Any]:
    """SSE 或裸 JSON 里取出 JSON-RPC 对象。"""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            raw = line[5:].strip()
            if raw:
                try:
                    return _json.loads(raw)
                except Exception:       # noqa: BLE001 — 多帧 SSE 里可能夹着非 JSON 行
                    logger.debug("SSE data 帧解析失败（继续找下一帧）", exc_info=True)
    try:
        parsed = _json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:                   # noqa: BLE001
        return {}


def _is_non_data_text(text: Any) -> bool:
    low = str(text).strip().lower()
    if not low or len(low) > 400:       # 长文本更可能是真内容，短提示语才是错误
        return False
    return any(marker in low for marker in _NON_DATA_MARKERS)


def unwrap(payload: Any, envelope: str = "") -> Any:
    """把工具返回拆到真正的数据节点。

    ``envelope`` 显式配置时按点号路径取（如 ``data`` / ``result.items``）；
    留空时按内置源见过的几种信封自动识别。
    """
    node = payload
    if envelope:
        for part in envelope.split("."):
            part = part.strip()
            if not part:
                continue
            if isinstance(node, dict):
                node = node.get(part)
            elif isinstance(node, list) and part.isdigit():
                index = int(part)
                node = node[index] if index < len(node) else None
            else:
                return None
        return node
    if isinstance(node, dict) and "data" in node:
        # Sorftime 的 {"doc": …, "data": …}、卖家精灵的 {"code","message","data"}、
        # 以及最常见的裸 {"data": …} 是同一件事：除 data 外全是信封字段就往里钻。
        others = [k for k in node if k != "data"]
        if all(str(k).lower() in _META_KEYS for k in others):
            return node["data"]
    return node


def rows(payload: Any, envelope: str = "") -> List[Dict[str, Any]]:
    """任意信封里取出「一行行的字典」。"""
    node = unwrap(payload, envelope)
    if isinstance(node, list):
        return [r for r in node if isinstance(r, dict)]
    if isinstance(node, dict):
        for key in _DATA_KEYS:
            arr = node.get(key)
            if isinstance(arr, list):
                return [r for r in arr if isinstance(r, dict)]
        # 容器名不在清单里就退而找"第一个列表值"。找到了空列表也算数 ——
        # 那代表"这次真没数据"，不能接着往下把整个信封当成一行返回。
        for value in node.values():
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
        # 整个 dict 里一个列表都没有 = 单条记录型返回，当作一行。
        return [node] if node else []
    return []


def record(payload: Any, envelope: str = "") -> Dict[str, Any]:
    """单条记录型返回拍平成一个字典。"""
    node = unwrap(payload, envelope)
    if isinstance(node, dict):
        for key in _DATA_KEYS:
            inner = node.get(key)
            if isinstance(inner, dict):
                return inner
            if isinstance(inner, list) and inner and isinstance(inner[0], dict):
                return inner[0]
        return node
    if isinstance(node, list) and node and isinstance(node[0], dict):
        return node[0]
    return {}


# 见过的鉴权写法，按出现频率排。自动配置会挨个试，第一个真能调通的就是它。
# 让用户自己选"鉴权方式"再填"参数名"是没必要的 —— 这件事机器试几次就知道了。
AUTH_CANDIDATES: List[Dict[str, str]] = [
    {"mode": "query", "name": "key"},
    {"mode": "query", "name": "secret-key"},
    {"mode": "query", "name": "api_key"},
    {"mode": "query", "name": "apikey"},
    {"mode": "query", "name": "token"},
    {"mode": "bearer", "name": ""},
    {"mode": "header", "name": "X-API-Key"},
    {"mode": "header", "name": "Authorization"},
]

AUTH_LABELS = {
    "none": "不需要鉴权",
    "query": "URL 参数",
    "bearer": "Authorization: Bearer",
    "header": "自定义 Header",
}


def auth_label(auth: Dict[str, Any]) -> str:
    mode = str(auth.get("mode") or "")
    name = str(auth.get("name") or "")
    if mode == "query":
        return f"URL 参数 {name or 'key'}"
    if mode == "header":
        return f"Header {name or 'X-API-Key'}"
    return AUTH_LABELS.get(mode, mode)


async def detect_auth(cfg: Dict[str, Any], probe_tool: str,
                      probe_args: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """挨个试鉴权写法，返回第一个真能调通工具的那种。

    为什么必须用 ``tools/call`` 来验而不是 ``tools/list``：大量服务器的工具清单
    **不鉴权**，瞎填的 key 也能列出来。拿它当判据，八种写法会全部"成功"，
    等于什么都没测。
    """
    value = str((cfg.get("auth") or {}).get("value") or "").strip()
    if not value:
        return {"mode": "none", "name": ""}
    for candidate in AUTH_CANDIDATES:
        trial = {**cfg, "auth": {**candidate, "value": value}}
        try:
            async with session(trial) as state:
                await call_tool(state, probe_tool, probe_args)
            return candidate
        except Exception:               # noqa: BLE001 — 换下一种写法接着试
            logger.debug("鉴权写法 %s 不通，试下一种", candidate, exc_info=True)
    return None


@asynccontextmanager
async def session(cfg: Dict[str, Any]):
    """建连 + 可选握手。``handshake`` 关掉时直接发 tools/call（Sorftime 就是这样）。"""
    timeout = float(cfg.get("timeout") or 40.0)
    state: Dict[str, Any] = {"cfg": cfg, "seq": 0, "session_id": ""}
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=_CONN_TIMEOUT)) as client:
        state["client"] = client
        if cfg.get("handshake", True):
            await _initialize(state)
        yield state


async def _initialize(state: Dict[str, Any]) -> None:
    cfg, client = state["cfg"], state["client"]
    try:
        resp = await client.post(_endpoint(cfg), headers=_headers(cfg), json={
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "IvyeaOps", "version": "1.0"},
            },
        })
        # streamable HTTP 的服务器会在响应头回 session id，后续请求必须带上；
        # 不带的服务器（Sorftime）这里就是 None，不影响。
        sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
        if sid:
            state["session_id"] = sid
            await client.post(_endpoint(cfg), headers={**_headers(cfg), "Mcp-Session-Id": sid}, json={
                "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
            })
    except Exception:                   # noqa: BLE001 — 不握手也能用的服务器不该因此整体失败
        logger.debug("MCP initialize 失败（旁路，继续直接调用工具）", exc_info=True)


async def _rpc(state: Dict[str, Any], method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    cfg, client = state["cfg"], state["client"]
    state["seq"] += 1
    headers = _headers(cfg)
    if state.get("session_id"):
        headers["Mcp-Session-Id"] = state["session_id"]
    try:
        resp = await client.post(_endpoint(cfg), headers=headers, json={
            "jsonrpc": "2.0", "id": state["seq"], "method": method, "params": params,
        })
    except httpx.HTTPError as exc:
        raise CustomSourceError(f"连接失败：{exc}") from exc
    if resp.status_code >= 400:
        raise CustomSourceError(f"HTTP {resp.status_code}：{resp.text[:200]}")
    body = parse_body(resp.text)
    if not body:
        raise CustomSourceError(f"无法解析响应（前 200 字：{resp.text[:200]}）")
    if body.get("error"):
        error = body["error"]
        message = error.get("message") if isinstance(error, dict) else error
        raise CustomSourceError(f"服务器返回错误：{message}")
    return body.get("result") or {}


async def list_tools(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """拉工具清单。

    ⚠️ 很多 MCP 服务器的 ``tools/list`` **不鉴权** —— 能列出工具不等于 key 有效。
    所以探测成功不能当成"配好了"，真伪要靠 tools/call 试跑。
    """
    result = await _rpc(state, "tools/list", {})
    tools = result.get("tools")
    return [t for t in tools if isinstance(t, dict)] if isinstance(tools, list) else []


async def call_tool(state: Dict[str, Any], tool: str, arguments: Dict[str, Any]) -> Any:
    result = await _rpc(state, "tools/call", {"name": tool, "arguments": arguments or {}})
    content = result.get("content") or []
    text = ""
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text") or ""
                break
    if result.get("isError"):
        raise CustomSourceError(f"{tool}：{(text or '工具返回错误')[:200]}")
    if not text:
        # 有的服务器把结构化结果放 structuredContent，没有 text 内容
        structured = result.get("structuredContent")
        if structured is not None:
            return structured
        raise CustomSourceError(f"{tool}：返回内容为空")
    try:
        parsed = _json.loads(text)
    except Exception:                   # noqa: BLE001 — 纯文本返回
        if _is_non_data_text(text):
            raise CustomSourceError(f"{tool}：{text.strip()[:200]}") from None
        return {"_text": text[:8000]}
    if isinstance(parsed, dict):
        code = str(parsed.get("code") or "")
        if code and code.upper() not in ("OK", "0", "200", "SUCCESS"):
            raise CustomSourceError(f"{tool} {code}：{str(parsed.get('message') or '')[:200]}")
    return parsed


async def probe(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """连通性探测：返回工具清单和每个工具的入参 schema，供配置界面填映射。"""
    async with session(cfg) as state:
        tools = await list_tools(state)
    return {
        "ok": True,
        "count": len(tools),
        "tools": [
            {
                "name": t.get("name"),
                "description": (t.get("description") or "")[:400],
                "params": sorted((t.get("inputSchema") or {}).get("properties", {}).keys())
                if isinstance(t.get("inputSchema"), dict) else [],
                "required": (t.get("inputSchema") or {}).get("required", [])
                if isinstance(t.get("inputSchema"), dict) else [],
            }
            for t in tools
        ],
    }
