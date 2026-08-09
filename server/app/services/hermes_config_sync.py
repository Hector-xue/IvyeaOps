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
import subprocess

from app.core.proc import no_window_kwargs
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

# GBrain embedding providers → env var name (key is read from the environment
# by the `gbrain serve` subprocess, which inherits Hermes' env). ollama needs
# no key (local).
_GBRAIN_EMBED_ENV: Dict[str, str] = {
    "openai":    "OPENAI_API_KEY",
    "zhipu":     "ZHIPUAI_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "minimax":   "MINIMAX_API_KEY",
    "voyage":    "VOYAGE_API_KEY",
    "google":    "GOOGLE_GENERATIVE_AI_API_KEY",
    "ollama":    "",
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


# Known embedding model → native dimension. Used to keep GBrain's pglite
# vector column in sync (it's created vector(1536) by default for OpenAI).
_EMBED_MODEL_DIMS = {
    "nomic-embed-text": 768, "mxbai-embed-large": 1024, "all-minilm": 384,
    "text-embedding-3-large": 1536, "text-embedding-3-small": 1536,
    "embedding-3": 1024,                # zhipu
    "text-embedding-v3": 1024,          # dashscope
    "embo-01": 1536,                    # minimax
    "voyage-3": 1024, "voyage-3-large": 1024,
    "text-embedding-004": 768,          # google
}
_GBRAIN_CONFIG = Path.home() / ".gbrain" / "config.json"


def _gbrain_bin() -> str | None:
    import shutil
    found = shutil.which("gbrain")
    if found:
        return found
    name = "gbrain.exe" if os.name == "nt" else "gbrain"
    for cand in (Path.home() / ".bun" / "bin" / name, Path("/usr/local/bin/gbrain")):
        if cand.exists():
            return str(cand)
    return None


def _migrate_gbrain_dims(new_dims: int) -> bool:
    """ALTER the pglite embedding column to ``new_dims`` if it differs.

    GBrain refuses to embed when the model's dim != the column's dim. We run
    the official migration recipe (drop index → alter type → null vectors →
    recreate index) via GBrain's own pglite + vector extension. Idempotent.
    """
    import json
    cfg_path = _GBRAIN_CONFIG
    if not cfg_path.exists():
        return False
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    db_path = cfg.get("database_path")
    if not db_path or not Path(db_path).exists():
        return False

    gbrain_pkg = Path.home() / ".bun" / "install" / "global" / "node_modules" / "gbrain"
    if not gbrain_pkg.exists():
        return False

    script = f"""
import {{ PGlite }} from "@electric-sql/pglite";
import {{ vector }} from "@electric-sql/pglite/vector";
import {{ pg_trgm }} from "@electric-sql/pglite/contrib/pg_trgm";
const db = new PGlite({json.dumps(db_path)}, {{ extensions: {{ vector, pg_trgm }} }});
await db.waitReady;
const r = await db.query(`SELECT atttypmod FROM pg_attribute
  WHERE attrelid='content_chunks'::regclass AND attname='embedding'`);
const cur = r.rows[0]?.atttypmod;
if (cur === {new_dims}) {{ console.log("noop"); await db.close(); process.exit(0); }}
await db.exec(`DROP INDEX IF EXISTS idx_chunks_embedding;
  ALTER TABLE content_chunks ALTER COLUMN embedding TYPE vector({new_dims});
  UPDATE content_chunks SET embedding=NULL, embedded_at=NULL;
  CREATE INDEX idx_chunks_embedding ON content_chunks USING hnsw (embedding vector_cosine_ops);`);
console.log("migrated");
await db.close();
"""
    bun = str(Path.home() / ".bun" / "bin" / ("bun.exe" if os.name == "nt" else "bun"))
    if not Path(bun).exists():
        return False
    try:
        proc = subprocess.run([bun, "-e", script], cwd=str(gbrain_pkg),
                              capture_output=True, text=True, timeout=120,
                              **no_window_kwargs())
        return "migrated" in proc.stdout or "noop" in proc.stdout
    except Exception:
        return False


def sync_gbrain_embedding(provider: str, model: str, api_key: str) -> None:
    """Configure GBrain semantic-search embedding — the version that actually works.

    Three things, learned the hard way:
      1. API key → ~/.hermes/.env (the `gbrain serve` subprocess inherits it).
      2. embedding_model MUST be written to ~/.gbrain/config.json in
         ``provider:model`` form. `gbrain config set` writes the pglite DB,
         but loadConfig() reads config.json — they don't agree, so set is a
         no-op for this key. Bare model names fall back to OpenAI.
      3. The pglite vector column dim must match the model; ALTER it if not.
    """
    import json
    provider = (provider or "").strip()
    model = (model or "").strip()
    api_key = (api_key or "").strip()
    if not provider:
        return

    # 1. API key into Hermes env.
    env_var = _GBRAIN_EMBED_ENV.get(provider, "")
    if env_var and api_key:
        _write_env_file({env_var: api_key})

    if not model:
        return

    # 2. Write embedding_model (provider:model) + dimensions into config.json.
    full_model = model if ":" in model else f"{provider}:{model}"
    bare_model = full_model.split(":", 1)[1]
    dims = _EMBED_MODEL_DIMS.get(bare_model)

    # config.json only exists after the local DB has been initialised. If it's
    # missing (e.g. GBrain freshly installed, DB not yet inited), initialise it
    # first — otherwise picking an embedding provider in 系统配置 would silently do
    # nothing and the 知识库 board would keep showing "未配置 Embedding".
    if not _GBRAIN_CONFIG.exists():
        try:
            from app.services import gbrain_service as _gb
            _gb.ensure_db_ready()
        except Exception:
            logger.debug("_gb.ensure_db_ready 失败（旁路，已忽略）", exc_info=True)

    if _GBRAIN_CONFIG.exists():
        try:
            cfg = json.loads(_GBRAIN_CONFIG.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        cfg["embedding_model"] = full_model
        if dims:
            cfg["embedding_dimensions"] = dims
        tmp = _GBRAIN_CONFIG.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        tmp.replace(_GBRAIN_CONFIG)
    else:
        import logging
        logging.getLogger(__name__).warning(
            "sync_gbrain_embedding: ~/.gbrain/config.json 不存在且无法初始化，"
            "embedding_model 未写入（GBrain 可能未正确安装）。")

    # 3. Migrate the vector column dimension if the model needs it.
    if dims:
        _migrate_gbrain_dims(dims)

    # Also push via CLI (harmless; some GBrain read paths use it).
    gbrain = _gbrain_bin()
    if gbrain:
        _bun_bin = str(Path.home() / ".bun" / "bin")
        env = {**os.environ, "PATH": os.pathsep.join([_bun_bin, os.environ.get("PATH", "")])}
        try:
            subprocess.run([gbrain, "config", "set", "embedding_model", full_model],
                           env=env, capture_output=True, timeout=20,
                           **no_window_kwargs())
        except Exception:
            logger.debug("subprocess.run 失败（旁路，已忽略）", exc_info=True)


# ── Public entry point ────────────────────────────────────────────────────────

_LLM_KEYS = {
    "hermes_provider", "hermes_model", "hermes_api_key", "hermes_base_url",
    "hermes_fallback_provider", "hermes_fallback_model",
    "hermes_fallback_api_key", "hermes_fallback_base_url",
}
_GBRAIN_EMBED_KEYS = {"gbrain_embed_provider", "gbrain_embed_model", "gbrain_embed_api_key"}


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

    if _GBRAIN_EMBED_KEYS & updates.keys():
        try:
            sync_gbrain_embedding(
                provider=updates.get("gbrain_embed_provider", ""),
                model=updates.get("gbrain_embed_model", ""),
                api_key=updates.get("gbrain_embed_api_key", ""),
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("gbrain embedding sync failed: %s", exc)
