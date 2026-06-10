"""First-run Setup Wizard endpoints.

GET  /api/setup/status              — check whether the wizard needs to run
GET  /api/setup/install-stream      — SSE stream: install optional local CLIs
POST /api/setup/complete            — mark setup as done (write setup_done flag)

Design notes
------------
- needs_setup is True only when setup_done is explicitly False AND no password
  has been set yet (covers fresh installs).  Users who already configured the
  server manually before this feature existed will have setup_done=False but
  a password set, so they won't be forced through the wizard.
- The install-stream endpoint runs the platform installer in a subprocess and
  streams stdout/stderr as SSE events so the frontend can show a live log.
- All endpoints require authentication so an unauthenticated visitor cannot
  trigger package installations.
"""
from __future__ import annotations

from app.core.proc import no_window_kwargs

import asyncio
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.core import hub_settings as _hs
from app.core.security import require_user
from app.core.version import app_version

router = APIRouter()

# Mapping from the agent name the frontend sends to the npm package to install.
_INSTALLABLE: dict[str, str] = {
    "codex":  "@openai/codex",
    "claude": "@anthropic-ai/claude-code",
}
_COMPONENTS = {"hermes", "gbrain", "ollama", "codex", "claude", "all"}
_LATEST_RELEASE_API = "https://api.github.com/repos/Hector-xue/IvyeaOps/releases/latest"


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    text = (value or "").strip().lstrip("vV")
    parts = text.split(".")
    if len(parts) < 3:
        return None
    nums: list[int] = []
    for p in parts[:3]:
        digits = ""
        for ch in p:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            return None
        nums.append(int(digits))
    return nums[0], nums[1], nums[2]


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@router.get("/setup/status")
def setup_status(_u: str = Depends(require_user)):
    """Return whether the first-run wizard needs to run and what's configured."""
    from app.services.runners import _find_bin, RUNNER_ORDER
    from app.core.config import settings as _cfg

    cfg = _hs.load()
    setup_done: bool = bool(cfg.get("setup_done"))

    # Password is either in hub_settings.json or the startup .env
    password_set: bool = bool(
        cfg.get("password_hash") or _cfg.admin_password_hash
    )

    agents_found = {name: bool(_find_bin(name)) for name in RUNNER_ORDER}
    agents_found["gbrain"] = bool(shutil.which("gbrain") or (Path.home() / ".bun" / "bin" / "gbrain.exe").exists())
    agents_found["ollama"] = bool(
        shutil.which("ollama")
        or (Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe").exists()
    )
    any_agent_found = any(agents_found.get(name) for name in RUNNER_ORDER)
    apimart_set: bool = bool(cfg.get("apimart_key"))

    # Trigger the wizard only for genuine fresh installs.
    needs_setup = not setup_done and not password_set

    return {
        "needs_setup": needs_setup,
        "setup_done": setup_done,
        "checks": {
            "password_set": password_set,
            "any_agent_found": any_agent_found,
            "agents": agents_found,
            "apimart_set": apimart_set,
        },
    }


# ---------------------------------------------------------------------------
# Agent install — SSE stream
# ---------------------------------------------------------------------------

def _npm_bin() -> str | None:
    """Locate npm, searching PATH augmentations that systemd strips."""
    w = shutil.which("npm")
    if w:
        return w
    home = Path.home()
    candidates = [
        home / ".hermes" / "node" / "bin" / "npm",
        Path("/usr/local/bin/npm"),
        Path("/usr/bin/npm"),
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def _powershell_bin() -> str | None:
    return shutil.which("powershell") or shutil.which("powershell.exe") or shutil.which("pwsh")


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    candidates = [
        Path.cwd(),
        Path(__file__).resolve().parents[3],
    ]
    for root in candidates:
        if (root / "scripts" / "install-components.ps1").is_file():
            return root
    return Path(__file__).resolve().parents[3]


def _windows_update_supported(root: Path) -> bool:
    return (
        sys.platform.startswith("win")
        and (root / "IvyeaOpsServer.exe").is_file()
        and (root / "scripts" / "windows-action-gui.ps1").is_file()
    )


@router.get("/setup/update-info")
def update_info(_u: str = Depends(require_user)):
    current = app_version()
    root = _runtime_root()
    supported = _windows_update_supported(root)
    fallback_url = "https://github.com/Hector-xue/IvyeaOps/releases/latest"
    result = {
        "current": current,
        "latest": "",
        "update_available": False,
        "release_url": fallback_url,
        "platform_update_supported": supported,
        "detail": "已是最新版本",
    }

    try:
        req = urllib.request.Request(
            _LATEST_RELEASE_API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "IvyeaOps-update-check",
            },
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        result["detail"] = f"暂时无法检测新版本：{exc}"
        return result

    latest = str(data.get("tag_name") or "")
    release_url = str(data.get("html_url") or fallback_url)
    result["latest"] = latest
    result["release_url"] = release_url

    current_v = _version_tuple(current)
    latest_v = _version_tuple(latest)
    available = bool(current_v and latest_v and latest_v > current_v)
    result["update_available"] = available
    if available:
        if supported:
            result["detail"] = f"发现新版本 {latest}"
        else:
            result["detail"] = f"发现新版本 {latest}，当前平台请查看 Release 手动更新"
    return result


@router.post("/setup/update")
def start_windows_update(_u: str = Depends(require_user)):
    """Launch the Windows x64 updater GUI from inside the running app.

    The updater stops this backend process, so this endpoint only starts the
    detached updater and returns immediately.
    """
    root = _runtime_root()
    if not _windows_update_supported(root):
        raise HTTPException(400, "应用内更新仅支持 Windows x64 免 Python 包。")

    script = root / "scripts" / "windows-action-gui.ps1"
    if not script.is_file():
        raise HTTPException(404, f"更新窗口脚本不存在：{script}")

    ps = _powershell_bin()
    if not ps:
        raise HTTPException(500, "PowerShell 不可用，无法启动更新窗口。")

    # The updater is a *visible* WinForms window (it shows the progress bar) and
    # it STOPS this backend mid-way. So it must be:
    #   - visible: do NOT pass -WindowStyle Hidden / CREATE_NO_WINDOW, or the
    #     progress window never appears.
    #   - detached: DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP so killing this
    #     backend doesn't kill the updater (previously CREATE_NO_WINDOW kept it as
    #     a child, so stopping the service also stopped the updater — no progress,
    #     service never actually stopped/updated).
    cmd = [
        ps,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Mode",
        "update",
    ]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            cmd,
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
    except Exception as exc:
        raise HTTPException(500, f"启动更新失败：{exc}") from exc

    return {"ok": True, "detail": "更新窗口已启动。"}


async def _component_install_stream(component: str) -> AsyncGenerator[str, None]:
    if component not in _COMPONENTS:
        yield f"data: ERROR: unknown component '{component}'. Supported: {', '.join(sorted(_COMPONENTS))}\n\n"
        yield "data: __ERROR__\n\n"
        return

    root = _runtime_root()
    script = root / "scripts" / "install-components.ps1"
    ps = _powershell_bin()
    if sys.platform.startswith("win"):
        if not script.is_file():
            yield f"data: ERROR: Windows installer not found: {script}\n\n"
            yield "data: __ERROR__\n\n"
            return
        if not ps:
            yield "data: ERROR: PowerShell not found. Please start IvyeaOps from a normal Windows environment.\n\n"
            yield "data: __ERROR__\n\n"
            return
        cmd = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Component", component]
    elif component == "hermes":
        cmd = ["bash", "-lc", "curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash"]
    elif component == "gbrain":
        cmd = ["bash", "-lc", "command -v bun >/dev/null || curl -fsSL https://bun.sh/install | bash; export PATH=\"$HOME/.bun/bin:$PATH\"; bun install -g github:garrytan/gbrain; mkdir -p \"$HOME/brain\"; cd \"$HOME/brain\" && (gbrain init --pglite || true)"]
    elif component == "ollama":
        cmd = ["bash", "-lc", "command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh; ollama pull nomic-embed-text"]
    elif component in _INSTALLABLE:
        async for event in _npm_install_stream(component, _INSTALLABLE[component]):
            yield event
        return
    else:
        cmd = ["bash", "-lc", "curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash; command -v bun >/dev/null || curl -fsSL https://bun.sh/install | bash; export PATH=\"$HOME/.bun/bin:$PATH\"; bun install -g github:garrytan/gbrain; mkdir -p \"$HOME/brain\"; cd \"$HOME/brain\" && (gbrain init --pglite || true)"]

    yield f"data: > {' '.join(cmd)}\n\n"
    env = {**os.environ}
    home = Path.home()
    extra = [
        str(home / ".bun" / "bin"),
        str(home / ".hermes" / "bin"),
        str(home / ".hermes" / "node" / "bin"),
        str(home / ".local" / "bin"),
        "/usr/local/bin",
        "/usr/bin",
    ]
    env["PATH"] = os.pathsep.join(dict.fromkeys(p for p in extra + env.get("PATH", "").split(os.pathsep) if p))
    env.setdefault("HOME", str(home))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            **no_window_kwargs(),
        )
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                yield f"data: {line}\n\n"
        await proc.wait()
        if proc.returncode == 0:
            yield "data: \n\n"
            yield f"data: ✓ {component} installed / repaired.\n\n"
            yield "data: __DONE__\n\n"
        else:
            yield f"data: ✗ installer exited with code {proc.returncode}\n\n"
            yield "data: __ERROR__\n\n"
    except Exception as exc:
        yield f"data: ERROR: {exc}\n\n"
        yield "data: __ERROR__\n\n"


async def _npm_install_stream(agent: str, package: str) -> AsyncGenerator[str, None]:
    npm = _npm_bin()
    if not npm:
        yield "data: ERROR: npm not found. Please install Node.js first.\n\n"
        yield "data: Download: https://nodejs.org/\n\n"
        return

    # Build a rich PATH so npm can find node and write to the right global prefix.
    env = {**os.environ}
    home = Path.home()
    extra = [
        str(home / ".hermes" / "node" / "bin"),
        str(home / ".local" / "bin"),
        "/usr/local/bin",
        "/usr/bin",
    ]
    path_parts = extra + env.get("PATH", "").split(os.pathsep)
    env["PATH"] = os.pathsep.join(dict.fromkeys(p for p in path_parts if p))
    env.setdefault("HOME", str(home))

    cmd = [npm, "install", "-g", package]
    yield f"data: > {' '.join(cmd)}\n\n"

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            **no_window_kwargs(),
        )
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                yield f"data: {line}\n\n"
        await proc.wait()
        if proc.returncode == 0:
            yield "data: \n\n"
            yield f"data: ✓ {package} installed successfully.\n\n"
            yield "data: __DONE__\n\n"
        else:
            yield f"data: ✗ npm exited with code {proc.returncode}\n\n"
            yield "data: __ERROR__\n\n"
    except Exception as exc:
        yield f"data: ERROR: {exc}\n\n"
        yield "data: __ERROR__\n\n"


async def _install_stream(agent: str) -> AsyncGenerator[str, None]:
    if agent in _COMPONENTS:
        async for event in _component_install_stream(agent):
            yield event
        return

    supported = sorted(_COMPONENTS)
    yield f"data: ERROR: unknown agent/component '{agent}'. Supported: {', '.join(supported)}\n\n"


@router.get("/setup/install-stream")
async def install_stream(agent: str, _u: str = Depends(require_user)):
    """SSE endpoint: stream npm install output for the given agent."""
    return StreamingResponse(
        _install_stream(agent),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


# ---------------------------------------------------------------------------
# Complete setup
# ---------------------------------------------------------------------------

@router.post("/setup/complete")
def setup_complete(_u: str = Depends(require_user)):
    """Mark the first-run wizard as complete."""
    _hs.save({"setup_done": True})
    return {"ok": True}
