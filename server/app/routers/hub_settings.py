"""GET /api/settings  ·  PATCH /api/settings  ·  GET /api/settings/health"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.core import hub_settings as _hs
from app.core.security import require_user
from app.core.version import app_version

logger = logging.getLogger("ivyea.routers.hub_settings")

router = APIRouter()

_SECRET_KEYS: List[str] = [
    "apimart_key", "sorftime_key", "sif_key", "sellersprite_key",
    "hermes_api_key", "hermes_fallback_api_key",
    "alert_app_secret", "alert_webhook", "openai_api_key",
    "ivyea_agent_token", "ivyea_agent_api_key", "lingxing_mcp_key", "lingxing_openapi_secret",
    "vision_api_key",
]

# Keys that, when changed, require syncing into Hermes config.
_HERMES_SYNC_KEYS = {
    "sorftime_key", "sif_key", "sellersprite_key",
    "hermes_provider", "hermes_model", "hermes_api_key", "hermes_base_url",
    "hermes_fallback_provider", "hermes_fallback_model",
    "hermes_fallback_api_key", "hermes_fallback_base_url",
}

_IVYEA_AGENT_SYNC_KEYS = {
    "ivyea_agent_provider", "ivyea_agent_model", "ivyea_agent_api_key", "ivyea_agent_base_url",
}

# 飞书凭据改了就要同步给 IvyeaAgent —— 这一份同时供巡检卡片、审批回调、
# 飞书对话使用。不同步的话，界面上换了应用，服务器告警走新应用、
# 巡检卡片还在用旧的，而且没有任何地方看得出来。
_FEISHU_SYNC_KEYS = {
    "alert_app_id", "alert_app_secret", "alert_chat_id",
    "alert_feishu_domain", "alert_webhook",
}


class SettingsPatch(BaseModel):
    settings: Dict[str, Any]


class TestRequest(BaseModel):
    key: str
    value: str | None = None


@router.get("/settings")
async def get_settings(_u: str = Depends(require_user)):
    return {"settings": _hs.load(), "secret_keys": _SECRET_KEYS}


@router.patch("/settings")
async def patch_settings(body: SettingsPatch, _u: str = Depends(require_user)):
    updated = _hs.save(body.settings)
    # Sync data-source keys into Hermes config if any relevant key was touched.
    if _HERMES_SYNC_KEYS & body.settings.keys():
        try:
            from app.services.hermes_config_sync import on_settings_saved
            on_settings_saved(updated)
        except Exception:
            logger.debug("on_settings_saved 失败（旁路，已忽略）", exc_info=True)
    if _IVYEA_AGENT_SYNC_KEYS & body.settings.keys():
        try:
            from app.services import ivyea_agent_service
            ivyea_agent_service.ensure_available()
            ivyea_agent_service.sync_model_settings(updated, force=True)
        except Exception:
            logger.debug("ivyea_agent_service.ensure_available 失败（旁路，已忽略）", exc_info=True)
    if _FEISHU_SYNC_KEYS & body.settings.keys():
        try:
            from app.services import ivyea_agent_service
            ivyea_agent_service.sync_feishu_settings(updated)
        except Exception:
            logger.debug("sync_feishu_settings 失败（旁路，已忽略）", exc_info=True)
    return {"settings": updated, "secret_keys": _SECRET_KEYS}


# ── 飞书配置向导 ────────────────────────────────────────────────────────────
# 状态与辅助动作都在 IvyeaAgent 那边（凭据、白名单、巡检任务都归它管），
# 这里只做一层带鉴权的转发，不在 ops 侧另存一份状态。

class FeishuAction(BaseModel):
    # 巡检档位（l1 / l2 / daily / weekly / monthly …）由 **IvyeaAgent** 定义，
    # 界面也是按它回的 patrol.defaults 渲染的。这里不再逐个列字段：
    # 列了就得每加一档改一次 ops，忘了改的那一档会被 pydantic 静默丢掉 ——
    # 表现是"界面上勾了周报、保存成功、什么都没发生"。
    model_config = ConfigDict(extra="allow")

    action: str
    chat_id: str = ""
    text: str = ""
    # 白名单（谁能点审批按钮）
    allowed_senders: List[str] | None = None
    allowed_chats: List[str] | None = None
    # 巡检任务
    scope: str = "all"
    sids: List[str] | None = None
    exclude_sids: List[str] | None = None
    channel: str = "feishu_app"


@router.get("/settings/feishu")
async def feishu_setup_status(probe: bool = False, _u: str = Depends(require_user)):
    """向导要显示的全部状态。``probe=1`` 会真的去飞书换一次 token。"""
    from app.services import ivyea_agent_service
    try:
        return ivyea_agent_service.feishu_status(probe=probe)
    except Exception as exc:  # noqa: BLE001 —— agent 没起来时给人话，别 500
        return {"ok": False, "error": str(exc),
                "hint": "IvyeaAgent 本地服务（默认 127.0.0.1:8765）没连上。"
                        "服务器告警那条链路不受影响，但巡检卡片和审批需要它。"}


@router.post("/settings/feishu")
async def feishu_setup_action(body: FeishuAction, _u: str = Depends(require_user)):
    """向导的动作：列群 / 列成员 / 发测试 / 存白名单 / 配巡检任务。"""
    from app.services import ivyea_agent_service

    payload = body.model_dump(exclude_none=True)
    action = str(payload.pop("action", "")).strip()
    try:
        if action == "whitelist":
            return ivyea_agent_service.configure_feishu({
                k: v for k, v in payload.items()
                if k in ("allowed_senders", "allowed_chats")
            })
        return ivyea_agent_service.feishu_action({"action": action, **payload})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ── 亚马逊官方 API ──────────────────────────────────────────────────────────
# 与飞书那组不同，凭据只存 IvyeaAgent 一侧：这里没有"agent 挂了也要能用"的场景。

class AmazonConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    # 空串一律当"不改"（界面上没填的框会老实传空串），要清除得在 clear 里点名
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""
    ads_client_id: str = ""
    ads_client_secret: str = ""
    ads_refresh_token: str = ""
    seller_id: str | None = None
    region: str | None = None
    marketplaces: List[Dict[str, Any]] | None = None
    clear: List[str] | None = None


class AmazonAction(BaseModel):
    action: str


@router.get("/settings/amazon")
async def amazon_status(_u: str = Depends(require_user)):
    from app.services import ivyea_agent_service
    try:
        return ivyea_agent_service.amazon_status()
    except Exception as exc:  # noqa: BLE001 —— agent 没起来时给人话，别 500
        return {"ok": False, "error": str(exc),
                "hint": "IvyeaAgent 本地服务（默认 127.0.0.1:8765）没连上。"
                        "亚马逊凭据存在它那边，它不在就没法读写。"}


@router.post("/settings/amazon")
async def amazon_configure(body: AmazonConfig, _u: str = Depends(require_user)):
    from app.services import ivyea_agent_service
    try:
        return ivyea_agent_service.configure_amazon(body.model_dump(exclude_none=True))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@router.post("/settings/amazon/action")
async def amazon_do(body: AmazonAction, _u: str = Depends(require_user)):
    """verify（真打一次接口）/ profiles（列广告档案，用来填 profileId）。"""
    from app.services import ivyea_agent_service
    try:
        return ivyea_agent_service.amazon_action(body.model_dump())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ── 模型清单：把"填模型名"从背默写变成挑一个 ────────────────────────────────
# 四个槽位（主脑 / 全局兜底 / 视觉 / 生图）此前都是自由文本框：用户得自己记住
# "Qwen/Qwen3-VL-30B-A3B-Instruct" 这种字符串，记错了也要等到真调用时才报错。
# 这里按槽位解析出 provider + 地址 + 密钥，去问那个端点到底支持哪些模型。

_SLOT_KEYS: Dict[str, Dict[str, str]] = {
    "agent":     {"provider": "ivyea_agent_provider", "api_key": "ivyea_agent_api_key",
                  "base_url": "ivyea_agent_base_url"},
    "assistant": {"provider": "assistant_provider", "api_key": "assistant_api_key",
                  "base_url": "assistant_base_url"},
    "vision":    {"provider": "vision_provider", "api_key": "vision_api_key",
                  "base_url": "vision_base_url"},
    # 生图槽留空时**沿用 apimart 那套账号** —— 和 routers/assistant.py 的 _image_cfg
    # 完全同一套优先级。两边各写一份必然走偏：面板列出来的模型来自 A 端点，
    # 真生成时打的却是 B 端点。
    "image":     {"provider": "", "api_key": "image_api_key", "base_url": "image_base_url",
                  "fallback_api_key": "apimart_key", "fallback_base_url": "apimart_base"},
}


class ModelCatalogBody(BaseModel):
    """slot 决定去问哪个端点；provider/base_url/api_key 允许现给。

    现给是必须的：系统配置页在**保存之前**就要能看清单，那时新填的 key 还没落库。
    三者都留空才回落到库里存的那份。
    """

    slot: str = "agent"
    provider: str = ""
    base_url: str = ""
    api_key: str = ""
    refresh: bool = False


def _slot_endpoint(body: ModelCatalogBody, cfg: Dict[str, Any]) -> Dict[str, Any]:
    keys = _SLOT_KEYS.get(body.slot.strip().lower())
    if keys is None:
        return {}
    provider = (body.provider or (cfg.get(keys.get("provider", "")) or "")).strip()
    api_key = (body.api_key or (cfg.get(keys.get("api_key", "")) or "")).strip()
    base_url = (body.base_url or (cfg.get(keys.get("base_url", "")) or "")).strip()
    if not api_key and keys.get("fallback_api_key"):
        api_key = str(cfg.get(keys["fallback_api_key"]) or "").strip()
    if not base_url and keys.get("fallback_base_url"):
        base_url = str(cfg.get(keys["fallback_base_url"]) or "").strip()
    if not base_url and provider:
        from app.services.ai_synthesis_service import ASSISTANT_PROVIDER_BASE
        base_url = str(ASSISTANT_PROVIDER_BASE.get(provider, "") or "").strip()
    return {"provider": provider, "base_url": base_url, "api_key": api_key,
            "refresh": bool(body.refresh)}


@router.post("/settings/model-catalog")
async def model_catalog(body: ModelCatalogBody, _u: str = Depends(require_user)):
    """列出某个槽位当前那套账号能用哪些模型。**永远不回显密钥。**"""
    cfg = _hs.load()
    payload = _slot_endpoint(body, cfg)
    if not payload:
        return {"ok": False, "error": "unknown_slot",
                "catalog": {"ok": False, "models": [], "source": "none",
                            "error": f"不认识的槽位：{body.slot}"}}
    if not payload.get("provider") and not payload.get("base_url"):
        return {"ok": False, "error": "not_configured",
                "catalog": {"ok": False, "models": [], "source": "none",
                            "error": "这个槽位还没选 Provider，也没填 Base URL。"}}
    try:
        from app.services import ivyea_agent_service
        result = ivyea_agent_service.model_catalog(payload)
    except Exception as exc:  # noqa: BLE001
        # agent 没起来 / 是个不认识这个端点的老版本：面板不能因此打不开，
        # 退回"手输模型名"那条路，并把原因说清楚。
        logger.debug("model_catalog 走 agent 失败：%s", exc)
        return {"ok": False, "error": "agent_unavailable",
                "catalog": {"ok": False, "models": [], "source": "none",
                            "error": f"取模型清单失败：{exc}"}}
    catalog = result.get("catalog") if isinstance(result, dict) else None
    return {"ok": bool(result.get("ok")) if isinstance(result, dict) else False,
            "error": (result or {}).get("error", ""),
            "catalog": catalog or {"ok": False, "models": [], "source": "none", "error": ""}}


@router.post("/settings/test")
async def test_setting(body: TestRequest, _u: str = Depends(require_user)):
    """Probe one config key with the provided (or stored) value."""
    from app.services import settings_test
    return await settings_test.test_value(body.key, body.value)


@router.post("/settings/self-check")
async def self_check_settings(_u: str = Depends(require_user)):
    """一键自检：对每个已配置项跑真实在线测试，返回 ok/err/skip 矩阵。"""
    from app.services import settings_test
    return await settings_test.self_check()


@router.post("/settings/autodetect")
async def autodetect_settings(_u: str = Depends(require_user)):
    """Scan the host for known integration paths and return suggestions."""
    from app.services import settings_test
    return settings_test.autodetect()


@router.get("/settings/ai-log")
def ai_call_log(_u: str = Depends(require_user)):
    """Recent text-AI chain calls — which provider answered, for observability."""
    from app.services import ai_synthesis_service
    return {"calls": ai_synthesis_service.recent_ai_calls()}


@router.get("/settings/health")
async def settings_health(_u: str = Depends(require_user)):
    """Quick connectivity / existence check for every configured service."""
    cfg = _hs.load()

    async def _check_http(url: str, timeout: float = 3.0) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as c:
                r = await c.get(url)
            return {"ok": r.status_code < 500, "detail": f"HTTP {r.status_code}"}
        except httpx.ConnectError:
            return {"ok": False, "detail": "连接被拒绝（服务未启动）"}
        except Exception as e:
            return {"ok": False, "detail": str(e)[:120]}

    def _check_key(key: str, label: str) -> Dict[str, Any]:
        val = cfg.get(key, "")
        if val:
            return {"ok": True, "detail": f"已配置（{label}）"}
        return {"ok": False, "detail": "未配置"}

    def _check_bin(path: str) -> Dict[str, Any]:
        if not path:
            return {"ok": False, "detail": "未配置路径"}
        p = Path(path)
        if p.exists():
            return {"ok": True, "detail": str(p)}
        # Try auto-detect common locations
        import shutil
        name = p.name
        found = shutil.which(name)
        if found:
            return {"ok": True, "detail": f"自动发现 {found}"}
        return {"ok": False, "detail": f"未找到：{path}"}

    def _check_runner(name: str) -> Dict[str, Any]:
        from app.services.runners import _find_bin
        p = _find_bin(name)
        if p:
            return {"ok": True, "detail": p}
        return {"ok": False, "detail": "未安装"}

    def _check_command(name: str, *extra: str) -> Dict[str, Any]:
        import shutil
        found = shutil.which(name)
        if found:
            detail = found
            for p in extra:
                if Path(p).exists():
                    detail = p
                    break
            return {"ok": True, "detail": detail}
        for p in extra:
            if Path(p).exists():
                return {"ok": True, "detail": p}
        return {"ok": False, "detail": "未安装"}

    def _check_ivyea_agent() -> Dict[str, Any]:
        from app.core.config import settings as _cfg
        from app.services import ivyea_agent_service as _ivyea
        import shutil

        status = _ivyea.ensure_available()
        if status.get("available"):
            return {"ok": True, "detail": f"服务已连接 · {status.get('base_url')}"}

        candidates = [
            shutil.which("ivyea") or "",
            str(_cfg.root_dir / "server" / ".venv" / "bin" / "ivyea"),
            str(_cfg.root_dir / "server" / ".venv" / "Scripts" / "ivyea.exe"),
            str(Path.home() / ".local" / "bin" / "ivyea"),
        ]
        found = next((p for p in candidates if p and Path(p).exists()), "")
        if found:
            detail = status.get("error") or "服务未启动"
            return {"ok": True, "detail": f"CLI 已安装 · {found}；{detail}"}
        return {"ok": False, "detail": "未安装 IvyeaAgent"}

    imgflow_url = (cfg.get("imgflow_url") or "http://127.0.0.1:3001").rstrip("/")
    brain_root = cfg.get("brain_root") or ""
    if not brain_root:
        brain_root = __import__("os").environ.get("IVYEA_OPS_BRAIN_ROOT") or str(Path.home() / "brain")

    # 曾经这里还探一个本地 ollama —— 那是给 GBrain 的 embedding 用的
    # （安装按钮拉的就是 `ollama pull nomic-embed-text`）。GBrain 已整体摘除，
    # IvyeaAgent 的语义检索是随包自带的 ONNX 模型，装完即用、不需要任何服务，
    # 所以这一项连同它的状态行、安装按钮和 `ollama_base_url` 配置一起去掉了。
    # （Ollama 作为**本地大模型 provider** 仍然可用，那是另一回事，见首启向导。）
    imgflow_result = await _check_http(imgflow_url + "/")

    from app.core import integrations as _integ

    def _vision_detail(tier: int, label: str) -> str:
        """把档位说成人话——重点是让 T3 的用户知道"能用，只是少了什么"。"""
        if tier == 1:
            return f"{label}：主脑模型自带视觉，全部图片分析可用"
        if tier == 2:
            return f"{label}：主脑不支持图片，已由独立视觉模型代读，全部图片分析可用"
        if tier == 3:
            return (f"{label}：未配置视觉模型，图片走本地量化——"
                    "合规/比例/主体占比/配色/图上文字照常分析；"
                    "版式逆向与审美判断需配置一个支持视觉的模型")
        return "不可用：IvyeaAgent 未连接且未配置视觉模型（影响 Listing 图片识别 / 视觉 Skill）"

    # AI readiness: can the standard text chain / vision actually run? Gives a
    # fresh install an at-a-glance answer for "why is AI not working".
    from app.services import ai_synthesis_service as _ai
    from app.services.runners import _find_bin as _fb
    ivyea_agent_result = _check_ivyea_agent()
    _global_fb = bool(_ai.assistant_text_cfg().get("api_key"))
    _any_runner = any(_fb(n) for n in ("hermes", "codex", "claude"))
    _http_text = bool(_ai._deepseek_key() or _ai._apimart_key())
    _text_ok = bool(ivyea_agent_result.get("ok")) or _global_fb or _any_runner or _http_text
    # 「重新检测」必须真的重测：先强制刷一次 agent 视觉链，把 5 秒缓存顶掉，
    # 后面 has_vision_capability / vision_tier / vision_tier_label 复用这一次结果。
    _ai._agent_vision_chain(fresh=True)
    _vision_ok = _ai.has_vision_capability()
    ai_chain = {
        "text": {
            "ok": _text_ok,
            "detail": "至少一个文本 AI 可用" if _text_ok
            else "无可用文本 AI：请配置「全局兜底大模型」或 DeepSeek Key",
        },
        "global_fallback": {
            "ok": _global_fb,
            "detail": "已配置" if _global_fb else "未配置（建议配置以保证开箱即用）",
        },
        # 视觉不再是"有/无"两态，而是三档降级链——只报 ok 会让用户以为 T3
        # 等于没有，而 T3 其实能做全部可测量的分析（合规/比例/占比/配色/图上文字）。
        "vision": {
            "ok": _vision_ok,
            "tier": _ai.vision_tier(),
            "tier_label": _ai.vision_tier_label(),
            "detail": _vision_detail(_ai.vision_tier(), _ai.vision_tier_label()),
        },
        "chain_order": ", ".join(_ai._text_provider_chain()),
    }

    return {
        "version": {"ok": True, "detail": app_version()},
        "ai_chain":  ai_chain,
        "ivyea_agent": ivyea_agent_result,
        "apimart":   _check_key("apimart_key", "API Key 已设置"),
        "sorftime":  _check_key("sorftime_key", "API Key 已设置"),
        "imgflow":   imgflow_result,
        "brain_root": {
            "ok": Path(brain_root).exists(),
            "detail": brain_root if Path(brain_root).exists() else f"目录不存在：{brain_root}",
        },
        "openai":    _check_key("openai_api_key", "API Key 已设置"),
        "runners": {
            "hermes": _check_runner("hermes"),
            "codex":  _check_runner("codex"),
            "claude": _check_runner("claude"),
            "kiro":   _check_runner("kiro-cli"),
        },
        "integrations": _integ.all_status(),
    }
