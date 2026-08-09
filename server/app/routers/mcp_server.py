"""对外 MCP 服务：把 IvyeaOps 的亚马逊能力开放给别的 Agent。

对标贝狸的 Amazon Seller MCP，**但方向是反的**：贝狸发的令牌指向贝狸的云，用户
要用就得把店铺授权交出去；我们发的令牌指向**用户自己的机器** —— Claude Desktop、
Cursor 或任何别的 Agent 连过来，数据从头到尾没离开过他那台服务器。

这也是这个产品无法被 SaaS 复制的位置：同样是"一手数据 + AI 分析"，
信任成本完全不同。

协议
----
走 JSON-RPC over HTTP（MCP 的 Streamable HTTP 形态）。**只读工具**开放，写操作
（改真实广告活动）需要 write scope，而 write 令牌默认不发 ——
一个用来做分析的令牌，不该顺带具备改人家投放的能力。
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from threading import Lock
from typing import Any, Deque, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.services import mcp_tokens

logger = logging.getLogger("ivyea.routers.mcp_server")
router = APIRouter()

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "ivyea-ops", "version": "1"}

# 每令牌每分钟的调用上限。挡的不是攻击（令牌泄漏了限速也救不了），是**失控的
# Agent 循环** —— 对面一个写坏的 while 循环能把这台自托管机器的 CPU 和上游
# API 配额一起烧光，而用户往往到收到账单才发现。
_RATE_PER_MIN = 60
_hits: Dict[str, Deque[float]] = {}
_hits_lock = Lock()


def _schema(props: Optional[dict] = None, required: Optional[list] = None) -> dict:
    return {"type": "object", "properties": props or {},
            "required": required or [], "additionalProperties": False}


# 第一期只放**只读**工具。写工具（改预算/改竞价）等规则引擎的护栏落地后再开，
# 而不是先把口子开出来再补护栏。
TOOLS: Dict[str, dict] = {
    "ivyea_health": {
        "description": "IvyeaOps 版本与集成开关状态。用于确认连接是否正常。",
        "inputSchema": _schema(),
        "scope": "read",
    },
    "ivyea_ads_findings": {
        "description": "读取一次广告审计的结论：每条带证据（指标/数值/时间窗/来源）"
                       "与带护栏的建议动作。job_id 来自 IvyeaOps 广告审计页。",
        "inputSchema": _schema({
            "job_id": {"type": "string", "description": "广告审计任务 id"},
        }, ["job_id"]),
        "scope": "read",
    },
    "ivyea_knowledge_search": {
        "description": "检索本机亚马逊运营知识库（政策、打法、站点差异），返回带出处的条目。",
        "inputSchema": _schema({
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 8},
        }, ["query"]),
        "scope": "read",
    },
    "ivyea_keyword_research": {
        "description": "关键词调研：搜索量、竞争度、相关词与在架商品概况（走已配置的数据源）。",
        "inputSchema": _schema({
            "keyword": {"type": "string"},
            "marketplace": {"type": "string", "default": "US"},
        }, ["keyword"]),
        "scope": "read",
    },
}


def _rate_ok(token_id: str) -> bool:
    now = time.time()
    with _hits_lock:
        q = _hits.setdefault(token_id, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= _RATE_PER_MIN:
            return False
        q.append(now)
        return True


def _auth(request: Request, authorization: Optional[str], need: str = "read") -> dict:
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    ip = request.client.host if request.client else ""
    row = mcp_tokens.verify(token, need=need, ip=ip)
    if not row:
        # 不区分"过期""不存在""scope 不够" —— 那个区别对攻击者有用，对正常用户没用。
        raise HTTPException(401, "无效的 MCP 令牌")
    if not _rate_ok(str(row["id"])):
        raise HTTPException(429, f"超过每分钟 {_RATE_PER_MIN} 次调用上限")
    return row


def _text(text: str, *, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _dump(obj: Any) -> dict:
    return _text(json.dumps(obj, ensure_ascii=False, default=str))


async def _call_tool(name: str, args: dict) -> dict:
    """执行一个工具，返回 MCP 的 content 结构。"""
    if name == "ivyea_health":
        from app.core.version import app_version
        from app.services import skill_market
        return _dump({"version": app_version(),
                      "skill_market_enabled": skill_market.market_enabled()})

    if name == "ivyea_ads_findings":
        from app.services.ad_audit import get_job
        job = get_job(str(args.get("job_id") or ""))
        if not job:
            return _text("没有这个广告审计任务", is_error=True)
        # get_job 已经把结论归一成 FindingList（core/findings 的统一契约），
        # 直接给外部 Agent 的正是这一份 —— 带证据、带护栏，不是一段散文。
        return _dump({"status": job.get("status"),
                      "findings": job.get("findings", {})})

    if name == "ivyea_knowledge_search":
        from app.services import ivyea_agent_service as ia
        return _dump(ia.knowledge_search(str(args.get("query") or ""),
                                         limit=int(args.get("limit") or 8)))

    if name == "ivyea_keyword_research":
        from app.services import sorftime_service
        data, errors = await sorftime_service.keyword_pipeline(
            str(args.get("keyword") or ""), str(args.get("marketplace") or "US"))
        # errors 一起回给对面：数据源半瘫时它得知道哪几块是缺的，
        # 而不是拿着一份不完整的数据当完整的用。
        return _dump({"data": data, "errors": errors})

    return _text(f"未知工具：{name}", is_error=True)


class RpcBody(BaseModel):
    jsonrpc: str = "2.0"
    id: Any = None
    method: str
    params: Optional[dict] = None


@router.post("")
@router.post("/")
async def rpc(body: RpcBody, request: Request,
              authorization: Optional[str] = Header(None)) -> dict:
    """MCP JSON-RPC 入口。"""
    method = body.method
    params = body.params or {}

    if method in ("initialize", "ping"):
        _auth(request, authorization)
        if method == "ping":
            return {"jsonrpc": "2.0", "id": body.id, "result": {}}
        return {"jsonrpc": "2.0", "id": body.id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }}

    if method.startswith("notifications/"):
        _auth(request, authorization)
        return {"jsonrpc": "2.0", "id": body.id, "result": {}}

    if method == "tools/list":
        row = _auth(request, authorization)
        scopes = set((row.get("scopes") or "").split(","))
        # **只列这个令牌调得动的工具**。列出一个调用必然 401 的工具，
        # 对面的 Agent 只会反复重试然后给用户一个说不清的失败。
        return {"jsonrpc": "2.0", "id": body.id, "result": {
            "tools": [{"name": n, "description": t["description"],
                       "inputSchema": t["inputSchema"]}
                      for n, t in TOOLS.items() if t["scope"] in scopes],
        }}

    if method == "tools/call":
        name = str(params.get("name") or "")
        spec = TOOLS.get(name)
        if not spec:
            raise HTTPException(404, f"未知工具：{name}")
        # 按工具**各自声明的 scope** 校验，而不是入口处一刀切。
        row = _auth(request, authorization, need=spec["scope"])
        from app.core import audit
        audit.record("mcp", "tools/call", target=name, actor_name=str(row.get("name") or ""))
        try:
            return {"jsonrpc": "2.0", "id": body.id,
                    "result": await _call_tool(name, params.get("arguments") or {})}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — 工具异常要变成 MCP 的错误结果而不是 500
            logger.exception("MCP 工具执行失败 %s", name)
            return {"jsonrpc": "2.0", "id": body.id,
                    "result": _text(f"{type(exc).__name__}: {exc}", is_error=True)}

    raise HTTPException(400, f"不支持的方法：{method}")
