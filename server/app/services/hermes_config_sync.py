"""Sync IvyeaOps settings into the runner config files.

Called whenever hub_settings are saved, and once per boot with the full
settings dict. Idempotent — safe to call repeatedly.

Responsibilities:
  1. LLM model config  → config.yaml model + fallback_providers
                          + ~/.hermes/.env API key variables
  2. Sorftime key      → mcp_servers.sorftime URL query param
  3. SIF key           → mcp_servers.sif_mcp Bearer token
  4. SellerSprite key  → mcp_servers.sellersprite (stdio MCP)
  5. All three keys    → ~/.ivyea/mcp.json, so IvyeaAgent (the default brain)
                          can reach the same data sources — without this a
                          fresh install has the keys in System Settings and an
                          agent with zero MCP servers.
"""
from __future__ import annotations

import json
import logging
import os
import re

from pathlib import Path
from typing import Any, Dict

import yaml  # PyYAML — available in the IvyeaOps venv

logger = logging.getLogger("ivyea.services.hermes_config_sync")


_HERMES_CFG  = Path.home() / ".hermes" / "config.yaml"
_HERMES_ENV  = Path.home() / ".hermes" / ".env"
_MCP_SCRIPT  = Path(__file__).resolve().parents[2] / "tools" / "sellersprite_mcp.py"


def _agent_mcp_file() -> Path:
    """IvyeaAgent's MCP registry — honours IVYEA_HOME like the agent does."""
    home = os.environ.get("IVYEA_HOME") or str(Path.home() / ".ivyea")
    return Path(home) / "mcp.json"

# Map provider id → (env_var_name, default_base_url)
_PROVIDER_ENV: Dict[str, tuple[str, str]] = {
    "deepseek":   ("DEEPSEEK_API_KEY",               "https://api.deepseek.com/v1"),
    "anthropic":  ("ANTHROPIC_API_KEY",              ""),
    "openai":     ("OPENAI_API_KEY",                 "https://api.openai.com/v1"),
    "openrouter": ("OPENROUTER_API_KEY",             "https://openrouter.ai/api/v1"),
    "google":     ("GOOGLE_GENERATIVE_AI_API_KEY",   ""),
    "groq":       ("GROQ_API_KEY",                   "https://api.groq.com/openai/v1"),
    "together":   ("TOGETHER_API_KEY",               "https://api.together.xyz/v1"),
    "minimax":    ("MINIMAX_API_KEY",                ""),
    "zhipu":      ("ZHIPUAI_API_KEY",                ""),
    "kimi":       ("KIMI_API_KEY",                   "https://api.kimi.com/coding/v1"),
    "xiaomi":     ("XIAOMI_API_KEY",                 "https://token-plan-sgp.xiaomimimo.com/v1"),
    "custom":     ("",                               ""),
}


# ── YAML helpers (round-trip preserving comments as best as PyYAML can) ──────

def _load() -> Dict[str, Any]:
    if not _HERMES_CFG.exists():
        return {}
    try:
        return yaml.safe_load(_HERMES_CFG.read_text("utf-8")) or {}
    except Exception:
        return {}


def _save(cfg: Dict[str, Any]) -> None:
    _HERMES_CFG.parent.mkdir(parents=True, exist_ok=True)
    tmp = _HERMES_CFG.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.dump(cfg, allow_unicode=True, default_flow_style=False), "utf-8")
    tmp.replace(_HERMES_CFG)


# ── Sync functions ────────────────────────────────────────────────────────────

def sync_sorftime(key: str) -> None:
    """Update Sorftime URL query param in hermes config."""
    cfg = _load()
    mcp = cfg.setdefault("mcp_servers", {})
    sorftime = mcp.setdefault("sorftime", {})
    if key:
        base = re.sub(r"\?.*$", "", sorftime.get("url", "")) or "https://mcp.sorftime.com"
        sorftime["url"] = f"{base}?key={key}"
    sorftime.setdefault("timeout", 180)
    sorftime.setdefault("connect_timeout", 60)
    _save(cfg)


def sync_sif(key: str) -> None:
    """Update SIF MCP Bearer token in hermes config."""
    cfg = _load()
    mcp = cfg.setdefault("mcp_servers", {})
    sif = mcp.setdefault("sif_mcp", {})
    sif["url"] = "https://mcp.sif.com/mcp"
    sif.setdefault("timeout", 120)
    sif.setdefault("connect_timeout", 60)
    if key:
        sif.setdefault("headers", {})["Authorization"] = f"Bearer {key}"
    _save(cfg)


def sync_sellersprite(key: str) -> None:
    """Register sellersprite stdio MCP in hermes config."""
    cfg  = _load()
    mcp  = cfg.setdefault("mcp_servers", {})
    entry = mcp.setdefault("sellersprite", {})

    script = str(_MCP_SCRIPT)
    python  = _python_bin()
    entry["command"] = python
    entry["args"]    = [script]
    entry["env"]     = {"SELLERSPRITE_KEY": key} if key else {}
    entry.setdefault("timeout", 30)

    _save(cfg)


def sync_agent_mcp(updates: Dict[str, Any]) -> None:
    """Mirror the data-source keys into ~/.ivyea/mcp.json for IvyeaAgent.

    Only writes: a server is registered/refreshed when IvyeaOps holds a key for
    it, and otherwise left exactly as it is. Nothing is ever removed — boot
    replays this with the *full* settings dict, so an unset key there is
    indistinguishable from a cleared one, and a user who ran ``ivyea mcp add``
    themselves must not lose their entry. Servers are marked ``trusted``: they
    are read-only data APIs whose keys the operator entered in System Settings,
    and an approval prompt no one can answer would just stall unattended runs.
    """
    path = _agent_mcp_file()
    try:
        cfg = json.loads(path.read_text("utf-8")) if path.exists() else {}
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    servers = cfg.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = cfg["mcpServers"] = {}

    def entry(name: str) -> Dict[str, Any]:
        item = servers.get(name)
        return item if isinstance(item, dict) else {}

    changed = False

    key = str(updates.get("sorftime_key") or "").strip()
    if key:
        item = entry("sorftime")
        base = re.sub(r"\?.*$", "", str(item.get("url") or "")) or "https://mcp.sorftime.com"
        item.update({"transport": "http", "url": f"{base}?key={key}", "trusted": True})
        servers["sorftime"] = item
        changed = True

    key = str(updates.get("sif_key") or "").strip()
    if key:
        item = entry("sif_mcp")
        item.update({"transport": "http", "url": "https://mcp.sif.com/mcp", "trusted": True})
        headers = item.get("headers")
        item["headers"] = {**(headers if isinstance(headers, dict) else {}),
                           "Authorization": f"Bearer {key}"}
        servers["sif_mcp"] = item
        changed = True

    key = str(updates.get("sellersprite_key") or "").strip()
    if key:
        item = entry("sellersprite")
        item.update({"transport": "stdio", "command": _python_bin(),
                     "args": [str(_MCP_SCRIPT)], "env": {"SELLERSPRITE_KEY": key},
                     "trusted": True})
        servers["sellersprite"] = item
        changed = True

    if not changed:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)
    try:
        path.chmod(0o600)   # it holds API keys
    except OSError:
        logger.debug("path.chmod 失败（旁路，已忽略）", exc_info=True)


def _python_bin() -> str:
    return os.environ.get("IVYEA_OPS_PYTHON", "python3")


def _read_env_file() -> Dict[str, str]:
    """Parse ~/.hermes/.env into a dict (skip comments and blanks)."""
    result: Dict[str, str] = {}
    if not _HERMES_ENV.exists():
        return result
    for line in _HERMES_ENV.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def _write_env_file(env: Dict[str, str]) -> None:
    """Write back ~/.hermes/.env preserving comments; update/add key=value lines."""
    if not _HERMES_ENV.exists():
        _HERMES_ENV.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
    else:
        lines = _HERMES_ENV.read_text("utf-8").splitlines()

    written = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            new_lines.append(line)
            continue
        if "=" in stripped:
            k = stripped.partition("=")[0].strip()
            if k in env:
                new_lines.append(f"{k}={env[k]}")
                written.add(k)
                continue
        new_lines.append(line)

    # Append keys not already in the file
    for k, v in env.items():
        if k not in written:
            new_lines.append(f"{k}={v}")

    tmp = _HERMES_ENV.with_suffix(".env.tmp")
    tmp.write_text("\n".join(new_lines) + "\n", "utf-8")
    tmp.replace(_HERMES_ENV)


def sync_llm_model(
    provider: str, model: str, api_key: str, base_url: str,
    fallback_provider: str = "", fallback_model: str = "",
    fallback_api_key: str = "", fallback_base_url: str = "",
) -> None:
    """Write primary + fallback model config into config.yaml and .env."""
    provider = provider.strip()
    model    = model.strip()
    api_key  = api_key.strip()
    base_url = base_url.strip()

    if not provider:
        return  # nothing to write

    # ── 1. Write API key into ~/.hermes/.env ──────────────────────────────────
    env_updates: Dict[str, str] = {}
    env_info = _PROVIDER_ENV.get(provider, ("", ""))
    env_var = env_info[0]
    default_base = env_info[1]

    if env_var and api_key:
        env_updates[env_var] = api_key

    # Fallback key
    if fallback_provider:
        fb_env_info = _PROVIDER_ENV.get(fallback_provider.strip(), ("", ""))
        fb_env_var  = fb_env_info[0]
        if fb_env_var and fallback_api_key.strip():
            env_updates[fb_env_var] = fallback_api_key.strip()

    if env_updates:
        _write_env_file(env_updates)

    # ── 2. Write model + base_url into config.yaml ────────────────────────────
    cfg = _load()

    # Primary model
    effective_base = base_url or default_base
    cfg["model"] = {
        "default": model,
        "provider": provider,
        **({"base_url": effective_base} if effective_base else {}),
    }

    # Fallback providers list
    if fallback_provider:
        fb_info  = _PROVIDER_ENV.get(fallback_provider.strip(), ("", ""))
        fb_base  = (fallback_base_url.strip() or fb_info[1])
        fb_entry: Dict[str, str] = {
            "provider": fallback_provider.strip(),
            "model":    fallback_model.strip(),
        }
        if fb_base:
            fb_entry["base_url"] = fb_base
        cfg["fallback_providers"] = [fb_entry]
    else:
        cfg.setdefault("fallback_providers", [])

    _save(cfg)


# ── Public entry point ────────────────────────────────────────────────────────

_LLM_KEYS = {
    "hermes_provider", "hermes_model", "hermes_api_key", "hermes_base_url",
    "hermes_fallback_provider", "hermes_fallback_model",
    "hermes_fallback_api_key", "hermes_fallback_base_url",
}


def on_settings_saved(updates: Dict[str, Any]) -> None:
    """Called after hub_settings.save() with the full updated settings dict."""
    import logging
    _log = logging.getLogger(__name__)

    if _LLM_KEYS & updates.keys():
        try:
            sync_llm_model(
                provider         = updates.get("hermes_provider", ""),
                model            = updates.get("hermes_model", ""),
                api_key          = updates.get("hermes_api_key", ""),
                base_url         = updates.get("hermes_base_url", ""),
                fallback_provider = updates.get("hermes_fallback_provider", ""),
                fallback_model   = updates.get("hermes_fallback_model", ""),
                fallback_api_key = updates.get("hermes_fallback_api_key", ""),
                fallback_base_url = updates.get("hermes_fallback_base_url", ""),
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("hermes llm sync failed: %s", exc)

    if "sorftime_key" in updates:
        try:
            sync_sorftime((updates.get("sorftime_key") or "").strip())
        except Exception as exc:  # noqa: BLE001
            _log.warning("hermes sorftime sync failed: %s", exc)

    if "sif_key" in updates:
        try:
            sync_sif((updates.get("sif_key") or "").strip())
        except Exception as exc:  # noqa: BLE001
            _log.warning("hermes sif sync failed: %s", exc)

    if "sellersprite_key" in updates:
        try:
            sync_sellersprite((updates.get("sellersprite_key") or "").strip())
        except Exception as exc:  # noqa: BLE001
            _log.warning("hermes sellersprite sync failed: %s", exc)

    # Same keys, IvyeaAgent's own registry (~/.ivyea/mcp.json).
    try:
        sync_agent_mcp(updates)
    except Exception as exc:  # noqa: BLE001
        _log.warning("ivyea-agent mcp sync failed: %s", exc)
