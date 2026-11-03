"""claudecodeui 插件接口的原生后端占位。

原版 claudecodeui 的插件是 Node 进程里 git clone 下来跑的前端+RPC 包；原生 FastAPI
后端不跑第三方 Node 代码，所以不支持插件。但前端的 PluginsContext 会在任务台每次
加载时请求 ``/plugins``，没有这条路由就是满屏 404，设置页的安装按钮还会把 404 的
错误信封（对象）当字符串渲染，整页崩溃。这里给出明确的"空列表 + 不支持"答复。
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

_UNSUPPORTED = "当前版本的任务台不支持 claudecodeui 插件（原生后端不运行第三方 Node 代码）。"


@router.get("")
def list_plugins() -> Dict[str, Any]:
    return {"plugins": []}


@router.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def plugin_unsupported(rest: str) -> JSONResponse:
    return JSONResponse(status_code=501, content={"error": _UNSUPPORTED})
