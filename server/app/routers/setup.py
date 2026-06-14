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
import tempfile
import threading
import urllib.request
import zipfile
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


# ── In-app update with real progress (Windows x64) ───────────────────────────
# Two-phase flow driven by an in-app modal instead of the external WinForms
# window: (1) the backend downloads the zip itself, exposing live progress;
# (2) "install" spawns the detached updater with the pre-downloaded zip — it
# stops this backend, copies files, restarts. The frontend then polls
# /api/health until the new version is up.

_UPDATE_LOCK = threading.Lock()
_UPDATE_STATE: dict = {"phase": "idle", "percent": 0, "downloaded": 0, "total": 0,
                       "error": "", "zip_path": "", "target": ""}
_UPDATE_ZIP_URL = ("https://github.com/Hector-xue/IvyeaOps/releases/latest/download/"
                   "IvyeaOps-Windows-x64.zip")


def _update_download_worker(url: str, dest: Path) -> None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "IvyeaOps-updater"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            _UPDATE_STATE.update(total=total)
            done = 0
            with open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    _UPDATE_STATE.update(
                        downloaded=done,
                        percent=round(100 * done / total, 1) if total else 0)
        # Integrity gate — a truncated/corrupt download must NEVER reach robocopy,
        # or it installs a broken onefile exe whose python3xx.dll fails to load
        # ("找不到指定的模块"). Three checks, cheap → definitive:
        size = dest.stat().st_size
        if size < 1024 * 1024:  # sanity: a real bundle is ~90MB
            raise RuntimeError("下载文件异常偏小，可能不是有效安装包")
        if total and size != total:  # byte count must match Content-Length exactly
            raise RuntimeError(
                f"下载不完整（{size}/{total} 字节），请重试更新")
        # Full CRC scan of the archive — catches silent truncation/corruption that
        # a size match alone can miss, and confirms the exe entry is present.
        try:
            with zipfile.ZipFile(dest) as zf:
                bad = zf.testzip()
                if bad is not None:
                    raise RuntimeError(f"安装包损坏（{bad} 校验失败），请重试更新")
                if not any(n.endswith("IvyeaOpsServer.exe") for n in zf.namelist()):
                    raise RuntimeError("安装包内未找到 IvyeaOpsServer.exe，下载可能损坏")
        except zipfile.BadZipFile:
            raise RuntimeError("下载的文件不是有效的 zip 安装包，请重试更新")
        _UPDATE_STATE.update(phase="downloaded", percent=100, zip_path=str(dest))
    except Exception as exc:  # noqa: BLE001
        _UPDATE_STATE.update(phase="error", error=f"下载失败：{exc}")
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass


@router.post("/setup/update/download")
def update_download(_u: str = Depends(require_user)):
    """Start downloading the latest Windows x64 bundle in the background."""
    root = _runtime_root()
    if not _windows_update_supported(root):
        raise HTTPException(400, "应用内更新仅支持 Windows x64 免 Python 包。")
    with _UPDATE_LOCK:
        if _UPDATE_STATE["phase"] == "downloading":
            return {"ok": True, "detail": "已在下载中"}
        target = ""
        try:
            req = urllib.request.Request(_LATEST_RELEASE_API,
                                         headers={"Accept": "application/vnd.github+json",
                                                  "User-Agent": "IvyeaOps-updater"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                target = str(json.loads(resp.read().decode("utf-8", "replace")).get("tag_name") or "")
        except Exception:  # noqa: BLE001 — tag is cosmetic; download still proceeds
            pass
        dest = Path(tempfile.gettempdir()) / "IvyeaOps-update.zip"
        _UPDATE_STATE.update(phase="downloading", percent=0, downloaded=0, total=0,
                             error="", zip_path="", target=target)
        threading.Thread(target=_update_download_worker, args=(_UPDATE_ZIP_URL, dest),
                         daemon=True, name="ivyea-update-download").start()
    return {"ok": True, "target": target}


@router.get("/setup/update/progress")
def update_progress(_u: str = Depends(require_user)):
    return dict(_UPDATE_STATE)


@router.post("/setup/update/install")
def update_install(_u: str = Depends(require_user)):
    """Spawn the detached updater using the pre-downloaded zip. It stops this
    backend, copies program files (keeping data/config), and restarts — the
    frontend keeps polling /api/health until the new version answers."""
    root = _runtime_root()
    if not _windows_update_supported(root):
        raise HTTPException(400, "应用内更新仅支持 Windows x64 免 Python 包。")
    # Guard against a double-install: two concurrent updaters race on
    # IvyeaOpsServer.exe — the 2nd hits a sharing violation (robocopy exit 11) and
    # reports a scary failure even though the 1st succeeded. Atomically flip to
    # "installing" so a second call is a no-op.
    with _UPDATE_LOCK:
        if _UPDATE_STATE["phase"] == "installing":
            return {"ok": True, "detail": "已在安装中"}
        if _UPDATE_STATE["phase"] != "downloaded" or not _UPDATE_STATE["zip_path"]:
            raise HTTPException(400, "安装包尚未下载完成。")
        _UPDATE_STATE["phase"] = "installing"
    script = root / "scripts" / "update-exe.ps1"
    if not script.is_file():
        raise HTTPException(404, f"更新脚本不存在：{script}")
    ps = _powershell_bin()
    if not ps:
        raise HTTPException(500, "PowerShell 不可用。")

    # Breadcrumb: record that install was triggered BEFORE spawning, so even if the
    # updater process never starts there's evidence (the earlier "no update.log at
    # all" meant the updater never ran — invisible/swallowed).
    log_path = root / "logs" / "update.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"\n[update_install] triggered; ps={ps}; script={script}; "
                     f"zip={_UPDATE_STATE['zip_path']}\n")
    except Exception:
        pass

    # Launch via `cmd /c start` rather than a hidden detached Popen: `start` makes
    # the updater a brand-new independent process (survives this backend being
    # stopped mid-update — a hidden DETACHED child could be cascade-killed by a job
    # object), and it runs in a VISIBLE window WITHOUT -NonInteractive, so on error
    # it pauses on screen instead of vanishing silently. The in-app modal still
    # polls /api/health for completion.
    inner = (f'"{ps}" -NoProfile -ExecutionPolicy Bypass -File "{script}" '
             f'-ZipPath "{_UPDATE_STATE["zip_path"]}"')
    try:
        if sys.platform == "win32":
            # `start "<title>" /min <cmd>` — cmd's start launches powershell as an
            # independent process in its own (minimized) window, then cmd exits.
            subprocess.Popen(f'start "IvyeaOps 更新" /min {inner}', cwd=str(root), shell=True)
        else:
            subprocess.Popen([ps, "-File", str(script), "-ZipPath", _UPDATE_STATE["zip_path"]],
                             cwd=str(root))
    except Exception as exc:  # noqa: BLE001
        _UPDATE_STATE["phase"] = "downloaded"  # let the user retry
        raise HTTPException(500, f"启动安装失败：{exc}") from exc
    return {"ok": True, "detail": "正在安装，服务即将重启。"}


# Pin GBrain to a known-good commit. Upstream HEAD (v0.35+) changed the config
# schema to require database_url and broke `init --pglite`, so an *unpinned*
# install (what this used to do) left the 知识库 board erroring "No database URL".
# Clean-reinstall (remove + cache rm) so an already-installed v0.35 is replaced.
_GBRAIN_REF = "github:garrytan/gbrain#1a6b543cc536cb8c379ce30518390a38e6d2ee57"
_GBRAIN_INSTALL_SH = (
    'command -v bun >/dev/null || curl -fsSL https://bun.sh/install | bash; '
    'export PATH="$HOME/.bun/bin:$PATH"; '
    'bun remove -g gbrain >/dev/null 2>&1 || true; '
    'bun pm cache rm >/dev/null 2>&1 || true; '
    f'bun install -g {_GBRAIN_REF}; '
    # On POSIX the gbrain bin is a symlink to src/cli.ts run via bun's shebang, so
    # `gbrain` works directly (the "Blocked postinstall" is just pglite's migration).
    'mkdir -p "$HOME/brain"; cd "$HOME/brain" && (gbrain init --pglite || true)'
)


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
        cmd = ["bash", "-lc", _GBRAIN_INSTALL_SH]
    elif component == "ollama":
        cmd = ["bash", "-lc", "command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh; ollama pull nomic-embed-text"]
    elif component in _INSTALLABLE:
        async for event in _npm_install_stream(component, _INSTALLABLE[component]):
            yield event
        return
    else:
        cmd = ["bash", "-lc", "curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash; " + _GBRAIN_INSTALL_SH]

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
