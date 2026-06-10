"""GET /api/settings  ·  PATCH /api/settings  ·  GET /api/settings/health"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core import hub_settings as _hs
from app.core.security import require_user
from app.core.version import app_version

router = APIRouter()

_SECRET_KEYS: List[str] = [
    "apimart_key", "sorftime_key", "sif_key", "sellersprite_key",
    "hermes_api_key", "hermes_fallback_api_key", "gbrain_embed_api_key",
    "alert_app_secret", "alert_webhook", "openai_api_key",
    "lingxing_mcp_key", "lingxing_openapi_secret",
]

# Keys that, when changed, require syncing into Hermes config.
_HERMES_SYNC_KEYS = {
    "sorftime_key", "sif_key", "sellersprite_key",
    "hermes_provider", "hermes_model", "hermes_api_key", "hermes_base_url",
    "hermes_fallback_provider", "hermes_fallback_model",
    "hermes_fallback_api_key", "hermes_fallback_base_url",
    "gbrain_embed_provider", "gbrain_embed_model", "gbrain_embed_api_key",
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
            pass  # non-fatal — settings are saved regardless
    return {"settings": updated, "secret_keys": _SECRET_KEYS}


@router.post("/settings/test")
async def test_setting(body: TestRequest, _u: str = Depends(require_user)):
    """Probe one config key with the provided (or stored) value."""
    from app.services import settings_test
    return await settings_test.test_value(body.key, body.value)


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

    imgflow_url = (cfg.get("imgflow_url") or "http://127.0.0.1:3001").rstrip("/")
    gbrain_bin = cfg.get("gbrain_bin") or ""
    if not gbrain_bin:
        gbrain_bin = __import__("os").environ.get("IVYEA_OPS_GBRAIN_BIN", "/usr/local/bin/gbrain")

    brain_root = cfg.get("brain_root") or ""
    if not brain_root:
        brain_root = __import__("os").environ.get("IVYEA_OPS_BRAIN_ROOT") or str(Path.home() / "brain")

    # Probe the local ollama server directly — the most reliable "installed &
    # running" signal. `shutil.which` alone gives false negatives because the
    # systemd service PATH often omits /usr/local/bin where ollama lives.
    ollama_host = (cfg.get("gbrain_embed_base_url") or "http://127.0.0.1:11434").rstrip("/")
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

    # AI readiness: can the standard text chain / vision actually run? Gives a
    # fresh install an at-a-glance answer for "why is AI not working".
    from app.services import ai_synthesis_service as _ai
    from app.services.runners import _find_bin as _fb
    _global_fb = bool(_ai.assistant_text_cfg().get("api_key"))
    _any_runner = any(_fb(n) for n in ("hermes", "codex", "claude"))
    _http_text = bool(_ai._deepseek_key() or _ai._apimart_key())
    _text_ok = _global_fb or _any_runner or _http_text
    _vision_ok = _ai.has_vision_capability()
    ai_chain = {
        "text": {
            "ok": _text_ok,
            "detail": "至少一个文本 AI 可用" if _text_ok
            else "无可用文本 AI：请配置「全局兜底大模型」或安装 hermes/codex/claude 任一",
        },
        "global_fallback": {
            "ok": _global_fb,
            "detail": "已配置" if _global_fb else "未配置（建议配置以保证开箱即用）",
        },
        "vision": {
            "ok": _vision_ok,
            "detail": "可用" if _vision_ok else "未配置（影响 Listing 图片识别 / 视觉 Skill）",
        },
        "chain_order": ", ".join(_ai._text_provider_chain()),
    }

    return {
        "version": {"ok": True, "detail": app_version()},
        "ai_chain":  ai_chain,
        "apimart":   _check_key("apimart_key", "API Key 已设置"),
        "sorftime":  _check_key("sorftime_key", "API Key 已设置"),
        "imgflow":   imgflow_result,
        "gbrain_bin": _check_bin(gbrain_bin),
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
