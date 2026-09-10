"""自定义 MCP 数据源的增删改 + 探测 + 试跑。

内置的 Sorftime / 卖家精灵 / SIF 不归这里管 —— 它们的 key 仍在「系统配置 →
数据源」里，这个路由只处理用户自己加的源。

三个动作对应配一个数据源的三步：
  探测（probe）  连得上吗？对方有哪些工具、每个工具收什么参数
  试跑（test）   照我配的映射真调一次，看翻译出来的字段对不对
  保存（save）   存进注册表，板块的数据源下拉里立刻多一项
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.security import require_user
from app.services import custom_source_autoconfig as _auto
from app.services import custom_source_mcp as _mcp
from app.services import custom_source_provider as _provider
from app.services import custom_source_registry as _registry

logger = logging.getLogger("ivyea.routers.data_sources")

router = APIRouter()


class SourceBody(BaseModel):
    source: Dict[str, Any] = Field(default_factory=dict)


class AutoBody(BaseModel):
    source: Dict[str, Any] = Field(default_factory=dict)
    # 自动配置要真调一次工具，得有个能查得到东西的样例。默认值挑的是各站都有货、
    # 各家数据源都覆盖得到的品类词和一个长销款 ASIN；用户自己的品类当然更准。
    sample_keyword: str = "wireless earbuds"
    sample_asin: str = "B08N5WRWNW"
    marketplace: str = "US"


class TestBody(BaseModel):
    source: Dict[str, Any] = Field(default_factory=dict)
    capability: str = "home_asin_pulse"
    query: str = ""
    marketplace: str = "US"


def _hydrate(raw: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return _registry.hydrate(raw)
    except _registry.RegistryError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/data-sources")
def list_sources(_u: str = Depends(require_user)) -> Dict[str, Any]:
    return {
        "sources": _registry.list_all(redact=True),
        "capabilities": list(_registry.CAPABILITIES),
        "surfaces": list(_registry.SURFACES),
        "surface_requires": {k: list(v) for k, v in _registry.SURFACE_REQUIRES.items()},
        "prefix": _registry.PREFIX,
    }


@router.post("/data-sources")
def save_source(body: SourceBody, _u: str = Depends(require_user)) -> Dict[str, Any]:
    try:
        return {"source": _registry.save(body.source)}
    except _registry.RegistryError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/data-sources/{slug}")
def delete_source(slug: str, _u: str = Depends(require_user)) -> Dict[str, Any]:
    if not _registry.delete(slug):
        raise HTTPException(404, "该数据源不存在")
    return {"ok": True}


@router.post("/data-sources/probe")
async def probe_source(body: SourceBody, _u: str = Depends(require_user)) -> Dict[str, Any]:
    cfg = _hydrate(body.source)
    try:
        result = await _mcp.probe(cfg)
    except _mcp.CustomSourceError as exc:
        return {"ok": False, "error": str(exc), "tools": [], "count": 0}
    except Exception as exc:      # noqa: BLE001 — 探测失败是常态，别把它变成 500
        logger.debug("自定义数据源探测失败", exc_info=True)
        return {"ok": False, "error": str(exc), "tools": [], "count": 0}
    # ⚠️ 大量 MCP 服务器的 tools/list 根本不鉴权，瞎填的 key 也能把工具列出来。
    # 所以这里明确告诉用户：能列出工具 ≠ 密钥有效，密钥要靠"试跑"验证。
    result["note"] = "工具清单通常不需要鉴权即可读取，能列出工具不代表密钥有效；请用「试跑」验证。"
    return result


@router.post("/data-sources/autoconfig")
async def autoconfig_source(body: AutoBody, _u: str = Depends(require_user)) -> Dict[str, Any]:
    """探测 + 试调 + 自动生成字段映射。

    不落盘 —— 结果回给前端，用户看过报告再点保存。自动配错了还能进「高级」里改。
    """
    cfg = _hydrate(body.source)
    try:
        out = await _auto.autoconfigure(
            cfg, body.sample_keyword.strip(), body.sample_asin.strip(), body.marketplace,
        )
        # 回给前端的配置里把凭据摘掉 —— hydrate 补进去的是明文，原样回传等于让密钥
        # 在响应体里再走一趟网络、还会落进浏览器内存和 devtools 的历史里。
        # 前端保存时用的是用户自己那份 auth，不依赖这里回传。
        if out.get("source"):
            auth = dict(out["source"].get("auth") or {})
            out["source"]["auth"] = {**auth, "value": "",
                                     "value_set": bool(str(auth.get("value") or "").strip())}
        return out
    except _mcp.CustomSourceError as exc:
        return {"source": None, "report": {"ok": False, "error": str(exc),
                                           "tools": 0, "capabilities": [], "surfaces": []}}
    except Exception as exc:      # noqa: BLE001 — 自动配置失败是常态，不该变成 500
        logger.debug("自动配置失败", exc_info=True)
        return {"source": None, "report": {"ok": False, "error": str(exc),
                                           "tools": 0, "capabilities": [], "surfaces": []}}


class RemapBody(BaseModel):
    source: Dict[str, Any] = Field(default_factory=dict)
    capability: str
    tool: str
    sample_keyword: str = "wireless earbuds"
    sample_asin: str = "B08N5WRWNW"
    marketplace: str = "US"


@router.post("/data-sources/remap")
async def remap_capability(body: RemapBody, _u: str = Depends(require_user)) -> Dict[str, Any]:
    """用指定的工具重新推断某一项能力的字段映射。

    自动挑错了或压根没认出来时的补救口。**让人挑工具是合理的，让人逐个填字段
    路径不是** —— 所以这里只接受一个工具名，映射照样由系统按真实返回推断。
    """
    cfg = _hydrate(body.source)
    if body.capability not in _registry.CAPABILITIES:
        raise HTTPException(400, f"未知能力：{body.capability}")
    try:
        return await _auto.remap_capability(
            cfg, body.capability, body.tool.strip(),
            body.sample_keyword.strip(), body.sample_asin.strip(), body.marketplace,
        )
    except _mcp.CustomSourceError as exc:
        return {"ok": False, "spec": None, "error": str(exc)}
    except Exception as exc:      # noqa: BLE001 — 指错工具是常态，不该变成 500
        logger.debug("重新推断映射失败", exc_info=True)
        return {"ok": False, "spec": None, "error": str(exc)}


@router.post("/data-sources/test")
async def test_source(body: TestBody, _u: str = Depends(require_user)) -> Dict[str, Any]:
    cfg = _hydrate(body.source)
    capability = body.capability
    if capability not in _registry.CAPABILITIES:
        raise HTTPException(400, f"未知能力：{capability}")
    provider = _provider.CustomProvider(cfg)
    if not provider.has(capability):
        raise HTTPException(400, f"该数据源没有配置 {capability}")
    query = body.query.strip()
    if not query:
        raise HTTPException(400, "请填一个用于试跑的关键词或 ASIN")

    async def _noop(step: str, done: int, total: int) -> None:
        return None

    try:
        if capability == "keyword_pipeline":
            data, errors = await provider.keyword_pipeline(query, body.marketplace, _noop)
            return {"ok": not errors, "errors": errors, "result": _preview(data)}
        if capability == "asin_pipeline":
            data, errors = await provider.asin_pipeline(query, body.marketplace, _noop)
            return {"ok": not errors, "errors": errors, "result": _preview(data)}
        result = await getattr(provider, capability)(query, body.marketplace)
    except Exception as exc:      # noqa: BLE001 — 试跑的全部意义就是把错误显示出来
        logger.debug("自定义数据源试跑失败", exc_info=True)
        return {"ok": False, "errors": [str(exc)], "result": None}

    # 元组返回的能力（拓展词 / 趋势序列）统一拆成 {items, error}
    if isinstance(result, tuple):
        items, error = result
        return {"ok": bool(items), "errors": [error] if error else [],
                "result": _preview(items)}
    if isinstance(result, dict):
        errors = [e for e in (result.get("error"), result.get("detail_error"),
                              result.get("trend_error")) if e]
        # 映射没配对时最典型的症状：不报错，但每个字段都是 None。这里直接把
        # "映射出来还剩几个字段有值" 告诉用户，比让他自己盯着一屏 null 强。
        filled = sum(1 for k, v in result.items()
                     if k not in ("raw_report",) and v not in (None, "", [], {}))
        return {"ok": not errors and filled > 3, "errors": errors,
                "filled_fields": filled, "result": _preview(result)}
    return {"ok": result is not None, "errors": [], "result": result}


def _preview(value: Any, limit: int = 20) -> Any:
    """截断预览：试跑只是看字段对不对，没必要把 100 行商品全推给浏览器。"""
    if isinstance(value, list):
        return [_preview(v, limit) for v in value[:limit]]
    if isinstance(value, dict):
        return {k: _preview(v, limit) for k, v in value.items()}
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "…"
    return value
