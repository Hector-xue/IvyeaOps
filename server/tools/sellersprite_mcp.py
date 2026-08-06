#!/usr/bin/env python3
"""SellerSprite MCP server (stdio transport).

Wraps the SellerSprite REST API as MCP tools so Hermes can call
SellerSprite data the same way it calls SIF or Sorftime.

Registration (done automatically when key is saved in IvyeaOps settings):
    hermes mcp add sellersprite \
        --command python3 /path/to/sellersprite_mcp.py \
        --env SELLERSPRITE_KEY=<your-key>

Tools exposed:
    keyword_traffic      — search volume, trend, competition for a keyword
    asin_keywords        — top organic keywords driving traffic to an ASIN
    keyword_research     — expand a seed keyword into related keyword list
    competitor_keywords  — keyword overlap across a list of competitor ASINs
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

_BASE = "https://api.sellersprite.com/v1"

# ── Auth ──────────────────────────────────────────────────────────────────────

def _key() -> str:
    k = os.environ.get("SELLERSPRITE_KEY", "").strip()
    if not k:
        raise RuntimeError(
            "SELLERSPRITE_KEY 未设置。请在 IvyeaOps 系统配置页填写卖家精灵密钥并保存。"
        )
    return k


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _request(method: str, path: str, payload: dict | None = None) -> Any:
    url = _BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "secret-key": _key(),
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"SellerSprite API 错误 {e.code}: {body}")


def _post(path: str, payload: dict) -> Any:
    return _request("POST", path, payload)


# ── Tool definitions ──────────────────────────────────────────────────────────

_TOOLS: list[dict] = [
    {
        "name": "keyword_traffic",
        "description": (
            "获取关键词的搜索量、趋势、购买率、竞争度等核心流量数据（卖家精灵）。"
            "适用场景：评估关键词价值、判断竞争激烈程度。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword":     {"type": "string",  "description": "目标关键词"},
                "marketplace": {"type": "string",  "description": "站点代码，如 US DE JP UK FR CA", "default": "US"},
                "month":       {"type": "string",  "description": "查询月份 YYYY-MM，不填取最新数据"},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "asin_keywords",
        "description": (
            "获取指定 ASIN 的自然流量关键词列表，含各词搜索量与排名（卖家精灵）。"
            "适用场景：分析竞品流量来源、发现高价值长尾词。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asin":        {"type": "string",  "description": "目标 ASIN，例如 B08N5WRWNW"},
                "marketplace": {"type": "string",  "default": "US"},
                "page":        {"type": "integer", "description": "页码，从 1 开始", "default": 1},
                "size":        {"type": "integer", "description": "每页条数，最大 50",  "default": 20},
            },
            "required": ["asin"],
        },
    },
    {
        "name": "keyword_research",
        "description": (
            "关键词挖掘：输入种子词，返回相关关键词列表及其流量数据（卖家精灵）。"
            "适用场景：开品前选词、Listing 关键词布局。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword":     {"type": "string"},
                "marketplace": {"type": "string",  "default": "US"},
                "page":        {"type": "integer", "default": 1},
                "size":        {"type": "integer", "default": 20},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "competitor_keywords",
        "description": (
            "批量获取多个竞品 ASIN 的关键词交集与差异分析（卖家精灵）。"
            "适用场景：竞品对比、找差异化切入词。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asins":       {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "竞品 ASIN 列表，1-10 个",
                },
                "marketplace": {"type": "string", "default": "US"},
            },
            "required": ["asins"],
        },
    },
]


# ── Tool dispatch ─────────────────────────────────────────────────────────────

def _call_tool(name: str, args: dict) -> str:
    mkt = args.get("marketplace", "US")
    try:
        if name == "keyword_traffic":
            payload: dict = {"keyword": args["keyword"], "marketplace": mkt}
            if "month" in args:
                payload["month"] = args["month"]
            result = _post("/traffic/keyword", payload)

        elif name == "asin_keywords":
            result = _post("/product/keyword", {
                "asin":        args["asin"],
                "marketplace": mkt,
                "page":        args.get("page", 1),
                "size":        args.get("size", 20),
            })

        elif name == "keyword_research":
            result = _post("/keyword/research", {
                "keyword":     args["keyword"],
                "marketplace": mkt,
                "page":        args.get("page", 1),
                "size":        args.get("size", 20),
            })

        elif name == "competitor_keywords":
            result = _post("/product/keywords/competitor", {
                "asins":       args["asins"],
                "marketplace": mkt,
            })

        else:
            return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)

        return json.dumps(result, ensure_ascii=False)

    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


# ── MCP stdio transport ───────────────────────────────────────────────────────

# 两种分帧都要支持：
#   * NDJSON —— MCP stdio 规范的分帧（一行一个 JSON 对象），ivyea-agent 用这种。
#   * Content-Length —— LSP 风格，本脚本最初为 hermes 写的那种。
# 只认 Content-Length 时，NDJSON 客户端发来的 `{"jsonrpc":...}` 会被当成一行
# "header"（它含冒号），然后死等那个永远不会来的空行 —— 表现为 agent 一开工具
# 就整轮挂死、连 SSE 响应头都发不出来。回复必须与请求同种分帧。
_ndjson_mode = False


def _read_msg() -> dict | None:
    """Read one JSON-RPC message from stdin, auto-detecting the framing."""
    global _ndjson_mode
    headers: dict[str, str] = {}
    while True:
        raw = sys.stdin.buffer.readline()
        if not raw:
            return None
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            if headers:
                break        # 空行 = Content-Length 头部结束
            continue         # NDJSON 流里的空行，跳过
        if line.startswith("{"):
            # NDJSON：整行就是一条消息。
            _ndjson_mode = True
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue     # 半截/脏行不至于打死整个 server
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length", 0))
    if not length:
        return None
    _ndjson_mode = False
    return json.loads(sys.stdin.buffer.read(length))


def _write_msg(msg: dict) -> None:
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    if _ndjson_mode:
        sys.stdout.buffer.write(body + b"\n")
    else:
        sys.stdout.buffer.write(
            f"Content-Length: {len(body)}\r\n\r\n".encode() + body
        )
    sys.stdout.buffer.flush()


def _respond(msg_id: Any, result: Any) -> None:
    _write_msg({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error(msg_id: Any, code: int, message: str) -> None:
    _write_msg({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    while True:
        msg = _read_msg()
        if msg is None:
            break

        method = msg.get("method", "")
        mid    = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            _respond(mid, {
                "protocolVersion": "2024-11-05",
                "capabilities":    {"tools": {}},
                "serverInfo":      {"name": "sellersprite", "version": "1.0.0"},
            })

        elif method == "notifications/initialized":
            pass  # notification — no response

        elif method == "tools/list":
            _respond(mid, {"tools": _TOOLS})

        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            content   = _call_tool(tool_name, tool_args)
            parsed    = json.loads(content)
            _respond(mid, {
                "content": [{"type": "text", "text": content}],
                "isError": "error" in parsed,
            })

        elif mid is not None:
            _error(mid, -32601, f"Method not found: {method}")


if __name__ == "__main__":
    main()
