"""Resolved paths for optional external integrations.

Each helper follows the same precedence:
    1. hub_settings.json (user-configurable via web UI)
    2. IVYEA_OPS_<KEY> env var (handled inside hub_settings.get)
    3. shutil.which / PATH lookup (for binaries)
    4. None (caller decides whether the feature is available)

Nothing here is required for IvyeaOps itself to function. These hooks
exist so the monitor / agent-hub / chat surfaces can light up against
the user's Hermes / Codex / Kiro / Claude / Bun installs without
hard-coding paths.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from app.core import hub_settings as _hs


def _path_or_none(value: str) -> Optional[Path]:
    if not value:
        return None
    p = Path(value)
    return p if p.exists() else None


def _bin_lookup(setting_key: str, name: str) -> Optional[str]:
    """Resolve a CLI: hub_settings entry → PATH → None."""
    configured = (_hs.get(setting_key) or "").strip()
    if configured:
        p = Path(configured)
        if p.is_file():
            return str(p)
    found = shutil.which(name)
    return found or None


# --- Binaries --------------------------------------------------------------

def hermes_bin() -> Optional[str]:
    return _bin_lookup("hermes_bin", "hermes")


def codex_bin() -> Optional[str]:
    return _bin_lookup("codex_bin", "codex")


def claude_bin() -> Optional[str]:
    return _bin_lookup("claude_bin", "claude")


def kiro_cli_bin() -> Optional[str]:
    return _bin_lookup("kiro_cli_bin", "kiro-cli")


# --- Databases / directories ----------------------------------------------

def hermes_db() -> Optional[Path]:
    return _path_or_none(_hs.get("hermes_db", ""))


def codex_db() -> Optional[Path]:
    return _path_or_none(_hs.get("codex_db", ""))


def feishu_codex_db() -> Optional[Path]:
    return _path_or_none(_hs.get("feishu_codex_db", ""))


def kiro_gateway_db() -> Optional[Path]:
    return _path_or_none(_hs.get("kiro_gateway_db", ""))


def kiro_cli_db() -> Optional[Path]:
    return _path_or_none(_hs.get("kiro_cli_db", ""))


def kiro_cli_sessions_dir() -> Optional[Path]:
    return _path_or_none(_hs.get("kiro_cli_sessions_dir", ""))


def claude_projects_dir() -> Optional[Path]:
    return _path_or_none(_hs.get("claude_projects_dir", ""))


def ivyea_sessions_dir() -> Optional[Path]:
    return _path_or_none(_hs.get("ivyea_sessions_dir", ""))


def dsh_sessions_dir() -> Optional[Path]:
    return _path_or_none(_hs.get("dsh_sessions_dir", ""))


# --- PATH augmentations ----------------------------------------------------

def extra_path_dirs() -> list[str]:
    """Directories that should be prepended to PATH when spawning
    subprocess children (because systemd's default PATH is minimal).
    Returns absolute, existing directories only.
    """
    dirs: list[str] = []
    for key in ("hermes_node_bin", "bun_bin"):
        val = (_hs.get(key) or "").strip()
        if val and Path(val).is_dir():
            dirs.append(val)
    return dirs


# --- Diagnostics -----------------------------------------------------------

def all_status() -> dict:
    """Returned by /api/settings/health for the UI status grid."""
    def _bin_row(label: str, path: Optional[str]) -> dict:
        return {"ok": bool(path), "detail": path or "未配置或未找到"}

    def _path_row(label: str, p: Optional[Path]) -> dict:
        return {"ok": p is not None, "detail": str(p) if p else "未配置或不存在"}

    return {
        "hermes_bin":           _bin_row("hermes", hermes_bin()),
        "codex_bin":            _bin_row("codex", codex_bin()),
        "claude_bin":           _bin_row("claude", claude_bin()),
        "kiro_cli_bin":         _bin_row("kiro-cli", kiro_cli_bin()),
        "hermes_db":            _path_row("hermes_db", hermes_db()),
        "codex_db":             _path_row("codex_db", codex_db()),
        "feishu_codex_db":      _path_row("feishu_codex_db", feishu_codex_db()),
        "kiro_gateway_db":      _path_row("kiro_gateway_db", kiro_gateway_db()),
        "kiro_cli_db":          _path_row("kiro_cli_db", kiro_cli_db()),
        "kiro_cli_sessions_dir": _path_row("kiro_cli_sessions_dir", kiro_cli_sessions_dir()),
        "claude_projects_dir":  _path_row("claude_projects_dir", claude_projects_dir()),
    }
