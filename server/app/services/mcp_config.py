"""Manage Claude Code MCP servers (user scope).

Reads are done straight from ~/.claude.json's ``mcpServers`` map (structured,
reliable). Writes go through the ``claude mcp`` CLI, which validates the config
shape, handles scopes, and keeps approval state consistent — safer than hand
-editing the JSON.
"""
from __future__ import annotations


import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_TIMEOUT_S = 30


class MCPError(RuntimeError):
    pass


def _claude_bin() -> str:
    from app.services.runners import _find_bin
    p = _find_bin("claude")
    if not p:
        raise MCPError("未找到 claude CLI")
    return p


def _config_path() -> Path:
    return Path(os.path.expanduser("~")) / ".claude.json"


def _validate_name(name: str) -> str:
    name = (name or "").strip()
    if not name or not _NAME_RE.match(name):
        raise MCPError("非法服务器名（只允许字母、数字、_ . -）")
    return name


def list_servers() -> list[dict[str, Any]]:
    """Return the user-scope MCP servers as structured rows."""
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8"))
    except Exception:
        return []
    servers = data.get("mcpServers") or {}
    out: list[dict[str, Any]] = []
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        transport = cfg.get("type") or ("http" if cfg.get("url") else "stdio")
        out.append({
            "name": name,
            "type": transport,
            "command": cfg.get("command", "") or "",
            "args": cfg.get("args", []) or [],
            "url": cfg.get("url", "") or "",
            "env_keys": list((cfg.get("env") or {}).keys()),
        })
    return sorted(out, key=lambda s: s["name"])


def _run_mcp(args: list[str], action: str) -> None:
    try:
        from app.core import proc as _proc
        cp = _proc.run(
            [_claude_bin(), "mcp", *args],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
            audit_module="mcp", audit_action=action,
        )
    except subprocess.TimeoutExpired as e:
        raise MCPError(f"{action}超时") from e
    if cp.returncode != 0:
        raise MCPError((cp.stderr or cp.stdout or f"{action}失败").strip()[:300])


def add_server(name: str, config: dict[str, Any]) -> dict[str, Any]:
    """Add (or replace) a user-scope MCP server from a config dict, e.g.
    {"command": "npx", "args": ["-y", "pkg"], "env": {"K": "v"}} or
    {"type": "http", "url": "https://...", "headers": {...}}."""
    name = _validate_name(name)
    if not isinstance(config, dict) or not config:
        raise MCPError("配置为空")
    payload = json.dumps(config, ensure_ascii=False)
    # add-json replaces an existing same-name server, so no need to pre-remove.
    _run_mcp(["add-json", name, payload, "--scope", "user"], "添加")
    return {"ok": True, "name": name}


def remove_server(name: str) -> dict[str, Any]:
    name = _validate_name(name)
    _run_mcp(["remove", name, "--scope", "user"], "删除")
    return {"ok": True, "name": name}
