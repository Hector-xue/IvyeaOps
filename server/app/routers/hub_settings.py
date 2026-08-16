"""GET /api/settings  ·  PATCH /api/settings  ·  GET /api/settings/health"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

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
    return {"settings": updated, "secret_keys": _SECRET_KEYS}


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

    # Probe the local ollama server directly — the most reliable "installed &
    # running" signal. `shutil.which` alone gives false negatives because the
    # systemd service PATH often omits /usr/local/bin where ollama lives.
    ollama_host = (cfg.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/")
    imgflow_result, ollama_http = await asyncio.gather(
        _check_http(imgflow_url + "/"),
        _check_http(ollama_host + "/api/tags", 1.5),
    )

    from app.core import integrations as _integ

    def _check_ollama() -> Dict[str, Any]:
        if ollama_http.get("ok"):
            return {"ok": True, "detail": f"运行中 · {ollama_host}"}
        import shutil
        found = shutil.which("ollama")
        if not found:
            cands = [
                *(f"{d}/ollama" for d in _integ.extra_path_dirs()),
                "/usr/local/bin/ollama",
                str(Path.home() / ".local" / "bin" / "ollama"),
                str(Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"),
            ]
            found = next((c for c in cands if Path(c).exists()), None)
        return {"ok": True, "detail": f"已安装 · {found}"} if found else {"ok": False, "detail": "未安装"}

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
        "ollama": _check_ollama(),
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
