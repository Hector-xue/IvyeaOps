"""MCP 令牌管理（管理员）。

发令牌是把这台机器的一部分能力交出去，所以这一整组接口只对管理员开放 ——
即使某个用户被授予了广告或知识库板块，他也不能自己签一个绕过界面的通道。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.security import require_admin
from app.services import mcp_tokens as svc

logger = logging.getLogger("ivyea.routers.mcp_tokens")
router = APIRouter(dependencies=[Depends(require_admin)])


class IssueBody(BaseModel):
    name: str = ""
    scopes: Optional[List[str]] = None
    ttl_days: int = 0


@router.get("/tokens")
def list_tokens() -> Dict[str, Any]:
    return {"tokens": svc.listing(), "scopes": list(svc.SCOPES)}


@router.post("/tokens")
def issue_token(body: IssueBody) -> Dict[str, Any]:
    """生成令牌。**明文只在这个响应里出现一次**，之后库里只有哈希。"""
    return svc.issue(body.name, scopes=body.scopes, ttl_days=body.ttl_days)


@router.delete("/tokens/{token_id}")
def revoke_token(token_id: str) -> Dict[str, Any]:
    if not svc.revoke(token_id):
        raise HTTPException(404, "令牌不存在或已撤销")
    return {"ok": True}


@router.get("/config")
def client_config(request: Request, token: str = "") -> Dict[str, Any]:
    """生成 Claude Desktop / Cursor 的配置片段。

    地址取**用户当前访问用的地址**而不是写死 localhost —— 大多数人是从别的机器
    连过来的，给一个 localhost 的片段等于让他自己去猜该填什么。
    """
    base = str(request.base_url).rstrip("/")
    endpoint = f"{base}/api/mcp"
    shown = token or "在上面生成令牌后粘贴到这里"
    # 两家客户端目前的远端 MCP 配置格式一致，但**分开给** —— 用户不该去猜
    # 自己那份能不能照抄。哪天格式分叉了，改这里就行。
    block = {
        "mcpServers": {
            "ivyea-ops": {
                "type": "http",
                "url": endpoint,
                "headers": {"Authorization": f"Bearer {shown}"},
            }
        }
    }
    return {
        "endpoint": endpoint,
        "claude_desktop": block,
        "cursor": block,
        "note": "对方机器要能访问到这个地址。只在公网暴露时务必套 HTTPS —— "
                "令牌是明文放在请求头里的。",
    }
