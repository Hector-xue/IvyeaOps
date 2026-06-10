"""Shared agent-runner helpers used by audit services.

The IvyeaOps workbench spawns agent CLIs (hermes / codex / claude) as
subprocesses to execute skills. Both the ASIN audit and the ad-report audit
share the same runner selection and invocation logic, so we lift it into a
standalone module.

Design notes
------------
- systemd runs the service with a minimal PATH that misses ~/.hermes/node/bin
  and ~/.local/bin, so we search a richer list ourselves via ``_find_bin``.
- ``_resolve_runner`` picks the first available CLI from ``RUNNER_ORDER``.
- ``runner_status`` exposes availability info for the UI selector.
- ``build_child_env`` prepares the env for the child process so the runner
  can spawn its own helpers (claude spawns node, hermes reads ~/.hermes, etc).
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

def _extra_paths() -> list[str]:
    """PATH dirs that hold agent CLIs beyond systemd's minimal default.

    Sources, in order:
      1. hub_settings (hermes_node_bin, bun_bin)
      2. ~/.hermes/node/bin and ~/.local/bin (works for non-root installs)
      3. The standard /usr/local/bin and /usr/bin.
    """
    from app.core import integrations
    return [
        *integrations.extra_path_dirs(),
        str(Path.home() / ".hermes" / "node" / "bin"),
        str(Path.home() / ".local" / "bin"),
        "/usr/local/bin",
        "/usr/bin",
    ]

# Ordered runner preference (auto-pick walks this list).
RUNNER_ORDER = ("hermes", "codex", "claude")

# Human-friendly labels shown in the UI selector.
RUNNER_LABELS = {
    "hermes": "Hermes（推荐 · 自带 MCP）",
    "codex":  "Codex（OpenAI）",
    "claude": "Claude Code",
}


def _find_bin(name: str) -> Optional[str]:
    """Locate an executable named ``name`` even when systemd's PATH is thin."""
    # First honor hub_settings explicit binary overrides.
    from app.core import integrations
    direct_lookup = {
        "hermes": integrations.hermes_bin,
        "codex":  integrations.codex_bin,
        "claude": integrations.claude_bin,
        "kiro-cli": integrations.kiro_cli_bin,
    }.get(name)
    if direct_lookup:
        configured = direct_lookup()
        if configured:
            return configured
    p = shutil.which(name)
    if p:
        return p
    # Fallback over known dirs. On Windows the executable is `name.exe` (or .cmd/
    # .bat) and os.access(..., X_OK) is unreliable — so try those suffixes and
    # don't gate on the exec bit. This is why a freshly-installed hermes.exe was
    # not detected (status stayed "需安装/修复") when it wasn't on PATH.
    win = sys.platform == "win32"
    candidates = [name, name + ".exe", name + ".cmd", name + ".bat"] if win else [name]
    for root in _extra_paths():
        for n in candidates:
            cand = Path(root) / n
            if cand.is_file() and (win or os.access(cand, os.X_OK)):
                return str(cand)
    return None


def _resolve_runner() -> tuple[Optional[str], Optional[str]]:
    """Return ``(runner_name, absolute_path)`` for the first available CLI."""
    for name in RUNNER_ORDER:
        p = _find_bin(name)
        if p:
            return name, p
    return None, None


def _build_runner_cmd(runner: str, binary: str, prompt: str) -> List[str]:
    """Build the subprocess argv for the given runner + prompt."""
    if runner == "hermes":
        # -z: one-shot prompt, reply to stdout, no TUI.
        return [binary, "-z", prompt]
    if runner == "codex":
        # exec: non-interactive mode.
        return [binary, "exec", prompt]
    # claude
    return [binary, "--print", "--permission-mode", "bypassPermissions", prompt]


def runner_status() -> List[Dict[str, Any]]:
    """Report availability of each runner for the UI selector.

    Returns a list starting with an ``auto`` row (indicating which runner
    auto-pick would resolve to), followed by one row per canonical runner.
    """
    rows: List[Dict[str, Any]] = []
    for name in RUNNER_ORDER:
        p = _find_bin(name)
        rows.append({
            "name": name,
            "label": RUNNER_LABELS.get(name, name),
            "available": bool(p),
            "path": p,
            "reason": None if p else "未安装",
        })
    auto_name, _ = _resolve_runner()
    rows.insert(0, {
        "name": "auto",
        "label": f"自动（当前：{auto_name or '无可用'}）",
        "available": auto_name is not None,
        "path": None,
        "reason": None if auto_name else "未找到任何可用的 CLI",
        "auto_resolved_to": auto_name,
    })
    return rows


def resolve_with_pref(pref: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve a runner preference to ``(runner_name, binary_path, error)``.

    ``pref`` may be ``"auto"`` or one of :data:`RUNNER_ORDER`.
    Returns ``(name, path, None)`` on success; ``(None, None, error_message)``
    on failure.
    """
    pref = (pref or "auto").lower()
    if pref == "auto":
        name, path = _resolve_runner()
        if not name:
            return None, None, "no agent CLI is available on this host"
        return name, path, None
    if pref in RUNNER_ORDER:
        path = _find_bin(pref)
        if not path:
            return None, None, f"runner '{pref}' is not available"
        return pref, path, None
    return None, None, f"unknown runner: {pref}"


def build_child_env(runner_bin: str) -> Dict[str, str]:
    """Prepare the env for a runner subprocess.

    - Prepends the runner's own directory to ``PATH`` so it can spawn helpers
      (claude spawns node, hermes spawns MCP servers, etc).
    - Ensures ``HOME`` is set (systemd minimal env can drop it).
    - Sets ``IS_SANDBOX=1`` so claude's --dangerously-skip-permissions check
      doesn't refuse when running as root.
    """
    child_env = {**os.environ}
    bin_dir = str(Path(runner_bin).parent)
    if bin_dir not in child_env.get("PATH", "").split(os.pathsep):
        child_env["PATH"] = bin_dir + os.pathsep + child_env.get("PATH", "")
    child_env.setdefault("HOME", str(Path.home()))
    child_env.setdefault("IS_SANDBOX", "1")
    return child_env
