"""Shell PTY backend — port of claudecodeui's shell-websocket.service.ts.

Spawns ``bash -c <command>`` in a real PTY (``pty.openpty`` + asyncio subprocess,
the same technique as app/services/pty_manager.py), streams output to the
client, accepts keystrokes and resize, and keeps sessions alive across reconnects
(buffer replay + 30-min idle kill). Also detects auth URLs in login flows.

Protocol (matches the frontend shell client):
  in : {type:init, projectPath, sessionId, hasSession, provider, initialCommand,
        isPlainShell, cols, rows} / {type:input, data} / {type:resize, cols, rows}
  out: {type:output, data} / {type:error, message} / {type:auth_url, url, autoOpen}
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("ivyea.agents.shell_pty")

_WINDOWS = sys.platform == "win32"
if not _WINDOWS:
    import fcntl
    import pty
    import termios

_SESSIONS: dict[str, "ShellSession"] = {}
_PTY_TIMEOUT_S = 30 * 60
_URL_BUFFER_LIMIT = 32768
_BUFFER_CAP = 5000

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\))")
_TRAILING_PUNCT_RE = re.compile(r"[)\]}>.,;:!?]+$")
_URL_RE = re.compile(r"https?://[^\s<>\"'`\\\x1b\x07]+", re.IGNORECASE)
_CONTINUATION_RE = re.compile(r"^[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+$")
_SAFE_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_.\-:]+$")


# --- url detection (url-detection.js) ---------------------------------------

def _strip_ansi(value: str) -> str:
    return _ANSI_RE.sub("", value or "")


def _normalize_url(url: str) -> Optional[str]:
    if not url or not isinstance(url, str):
        return None
    cleaned = _TRAILING_PUNCT_RE.sub("", url.strip())
    if not cleaned:
        return None
    from urllib.parse import urlparse
    try:
        parsed = urlparse(cleaned)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return cleaned


def _extract_urls(value: str) -> list[str]:
    direct = _URL_RE.findall(value or "")
    wrapped = []
    lines = re.split(r"\r?\n", value or "")
    for i, line in enumerate(lines):
        m = _URL_RE.search(line.strip())
        if not m:
            continue
        combined = m.group(0)
        j = i + 1
        while j < len(lines):
            cont = lines[j].strip()
            if not cont or not _CONTINUATION_RE.match(cont):
                break
            combined += cont
            j += 1
        wrapped.append(combined)
    seen, out = set(), []
    for u in direct + wrapped:
        if u not in seen:
            seen.add(u); out.append(u)
    return out


def _should_auto_open(value: str) -> bool:
    n = (value or "").lower()
    return any(s in n for s in ("browser didn't open", "open this url",
                                "continue in your browser", "press enter to open", "open_url:"))


# --- shell command building (buildShellCommand) -----------------------------

def _build_command(data: dict) -> str:
    has_session = bool(data.get("hasSession"))
    session_id = data.get("sessionId") or ""
    initial = data.get("initialCommand") or ""
    provider = data.get("provider") or "claude"
    is_plain = bool(data.get("isPlainShell")) or (bool(initial) and not has_session) or provider == "plain-shell"
    if is_plain:
        return initial
    if provider == "cursor":
        return f'cursor-agent --resume="{session_id}"' if (has_session and session_id) else "cursor-agent"
    if provider == "codex":
        return f'codex resume "{session_id}" || codex' if (has_session and session_id) else "codex"
    if provider == "gemini":
        command = initial or "gemini"
        return f'{command} --resume "{session_id}"' if (has_session and session_id and _SAFE_SESSION_ID_RE.match(session_id)) else command
    if provider == "opencode":
        return f'opencode --session "{session_id}"' if (has_session and session_id) else (initial or "opencode")
    if provider == "hermes":
        if has_session and session_id and _SAFE_SESSION_ID_RE.match(session_id):
            return f'hermes chat --resume "{session_id}"'
        return "hermes chat"
    if provider == "agy":
        # Antigravity CLI is unknown/uninstalled here; open a plain shell rather
        # than wrongly launching claude.
        return initial or "bash"
    if provider == "ivyea":
        if has_session and session_id and _SAFE_SESSION_ID_RE.match(session_id):
            return f'ivyea chat --resume "{session_id}"'
        return "ivyea chat"
    command = initial or "claude"
    return f'claude --resume "{session_id}" || claude' if (has_session and session_id) else command


@dataclass
class ShellSession:
    key: str
    proc: Optional[asyncio.subprocess.Process]
    fd_master: int
    # Windows ConPTY process (pywinpty PtyProcess). None on POSIX, where
    # fd_master is the real PTY master fd. When set, all fd ops branch to winpty.
    winpty: object = None
    ws: object = None
    buffer: list = field(default_factory=list)
    out_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    url_buffer: str = ""
    announced_urls: set = field(default_factory=set)
    timeout_handle: Optional[asyncio.TimerHandle] = None
    drain_task: Optional[asyncio.Task] = None
    exit_task: Optional[asyncio.Task] = None


async def _safe_send(ws, message: dict) -> None:
    if ws is None:
        return
    import json
    try:
        await ws.send_text(json.dumps(message, ensure_ascii=False))
    except Exception:
        logger.debug("ws.send_text 失败（旁路，已忽略）", exc_info=True)


def _proc_env() -> dict:
    env = os.environ.copy()
    env.update({"TERM": "xterm-256color", "COLORTERM": "truecolor", "FORCE_COLOR": "3"})
    # systemd hands us a thin PATH, so make every agent CLI discoverable:
    #   ~/.hermes/node/bin                 -> claude / codex (npm-installed)
    #   ~/.local/bin                       -> hermes symlink
    #   ~/.hermes/hermes-agent/venv/bin    -> hermes (real venv entrypoint)
    #   ~/.bun/bin                         -> gbrain / bun-based CLIs
    home = os.path.expanduser("~")
    extra_dirs = [
        os.path.join(home, ".hermes", "node", "bin"),
        os.path.join(home, ".local", "bin"),
        os.path.join(home, ".hermes", "hermes-agent", "venv", "bin"),
        os.path.join(home, ".bun", "bin"),
        "/usr/local/bin",
    ]
    cur = env.get("PATH", "/usr/bin:/bin")
    cur_parts = cur.split(os.pathsep)
    missing = [d for d in extra_dirs if d not in cur_parts]
    if missing:
        env["PATH"] = os.pathsep.join(missing + [cur])
    env.setdefault("IS_SANDBOX", "1")  # so `claude` runs unattended as root
    return env


def _win_proc_env() -> dict:
    """Environment for the Windows ConPTY shell. The agents board resumes codex /
    hermes / claude, so their CLIs must be on PATH — npm global installs to
    %APPDATA%\\npm (codex.cmd / claude.cmd), bun to ~/.bun/bin, hermes to ~/.hermes.
    Without this the terminal showed "'codex' 不是内部或外部命令". Also injects the
    ~/.hermes/.env API keys so hermes authenticates."""
    env = {str(k): str(v) for k, v in os.environ.items()}
    env.setdefault("TERM", "xterm-256color")
    from pathlib import Path as _P
    home = _P(os.path.expanduser("~"))
    extra = [
        str(home / ".bun" / "bin"),
        str(home / ".hermes" / "bin"),
        str(home / ".hermes" / "node"),
        str(home / ".hermes" / "node" / "bin"),
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        extra.append(str(_P(appdata) / "npm"))            # npm global bin (codex/claude)
    localapp = os.environ.get("LOCALAPPDATA")
    if localapp:
        extra.append(str(_P(localapp) / "Programs" / "Ollama"))
    cur = env.get("PATH", "")
    parts = cur.split(os.pathsep)
    missing = [d for d in extra if d and d not in parts]
    if missing:
        env["PATH"] = os.pathsep.join(missing + ([cur] if cur else []))
    try:
        from app.services.hermes_config_sync import _read_env_file
        for k, v in _read_env_file().items():
            if v:
                env.setdefault(k, v)
    except Exception:
        logger.debug("env.setdefault 失败（旁路，已忽略）", exc_info=True)
    return env


def _reader_thread_win(session: ShellSession, loop: asyncio.AbstractEventLoop) -> None:
    """Blocking-read pump for ConPTY (no pollable fd on Windows). Feeds the same
    out_queue/_drain pipeline as the POSIX add_reader path."""
    while True:
        try:
            data = session.winpty.read(65536)
        except EOFError:
            data = ""
        except Exception:  # noqa: BLE001
            data = ""
        if data:
            chunk = data.encode("utf-8", "replace") if isinstance(data, str) else bytes(data)
            try:
                loop.call_soon_threadsafe(session.out_queue.put_nowait, chunk)
            except RuntimeError:
                return  # loop closed
            continue
        try:
            alive = bool(session.winpty.isalive())
        except Exception:  # noqa: BLE001
            alive = False
        if not alive:
            try:
                loop.call_soon_threadsafe(
                    lambda s=session: asyncio.ensure_future(_on_exit_win(s)))
            except RuntimeError:
                logger.debug("loop.call_soon_threadsafe 失败（旁路，已忽略）", exc_info=True)
            return
        time.sleep(0.02)  # transient empty read; avoid a busy spin


async def _on_exit_win(session: ShellSession) -> None:
    """Windows counterpart of _on_exit: flush the drain, announce the exit code."""
    try:
        code = session.winpty.exitstatus
    except Exception:  # noqa: BLE001
        code = None
    session.out_queue.put_nowait(None)
    if session.drain_task:
        try:
            await session.drain_task
        except Exception:
            logger.debug("session.drain_task 失败（旁路，已忽略）", exc_info=True)
    await _safe_send(session.ws, {"type": "output",
                                  "data": f"\r\n\x1b[33mProcess exited with code {code}\x1b[0m\r\n"})
    _cleanup(session.key)


async def _spawn_windows(key: str, command: str, cwd: str, cols: int, rows: int) -> ShellSession:
    """Spawn via ConPTY (pywinpty) — the same mechanism IDE terminals use.
    Commands run through cmd.exe /c (it understands the `a || b` fallbacks that
    _build_command emits); an empty command opens an interactive PowerShell."""
    try:
        import winpty  # pywinpty; only installed/bundled on Windows
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "Windows 终端组件 (pywinpty) 未能加载，请更新 IvyeaOps 到含该组件的版本。") from e
    env = _win_proc_env()
    cmd = (command or "").strip()
    comspec = os.environ.get("COMSPEC") or "cmd.exe"
    if cmd and cmd != "bash":  # "bash" is the POSIX plain-shell default — meaningless here
        # `/k` (not /c): keep the shell interactive AFTER the command so the user
        # can type — with /c, cmd ran the agent command then exited, leaving a dead
        # terminal ("光标闪烁但无法输入"). The command (e.g. `codex resume … || codex`)
        # runs first; cmd's `||` fallback still works.
        argv = [comspec, "/k", cmd]
    else:
        argv = [shutil.which("powershell.exe") or comspec]
    try:
        pty_proc = winpty.PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(rows, cols))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"无法启动 Windows 终端进程: {e}") from e
    session = ShellSession(key=key, proc=None, fd_master=-1, winpty=pty_proc)
    _SESSIONS[key] = session
    loop = asyncio.get_running_loop()
    session.drain_task = asyncio.create_task(_drain(session))
    threading.Thread(target=_reader_thread_win, args=(session, loop),
                     daemon=True, name=f"agents-winpty-{key[-8:]}").start()
    return session


async def _spawn(key: str, command: str, cwd: str, cols: int, rows: int) -> ShellSession:
    if _WINDOWS:
        return await _spawn_windows(key, command, cwd, cols, rows)
    master, slave = pty.openpty()
    # apply initial window size
    try:
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        logger.debug("fcntl.ioctl 失败（旁路，已忽略）", exc_info=True)
    proc = await asyncio.create_subprocess_exec(
        "bash", "-c", command, stdin=slave, stdout=slave, stderr=slave,
        cwd=cwd, env=_proc_env(), start_new_session=True)
    os.close(slave)
    session = ShellSession(key=key, proc=proc, fd_master=master)
    _SESSIONS[key] = session

    loop = asyncio.get_running_loop()
    os.set_blocking(master, False)

    def _on_readable() -> None:
        try:
            data = os.read(master, 65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        if data:
            session.out_queue.put_nowait(data)
        else:
            try:
                loop.remove_reader(master)
            except Exception:
                logger.debug("loop.remove_reader 失败（旁路，已忽略）", exc_info=True)

    loop.add_reader(master, _on_readable)
    session.drain_task = asyncio.create_task(_drain(session))
    session.exit_task = asyncio.create_task(_on_exit(session))
    return session


async def _drain(session: ShellSession) -> None:
    while True:
        chunk = await session.out_queue.get()
        if chunk is None:
            return
        text = chunk.decode("utf-8", "replace")
        if len(session.buffer) >= _BUFFER_CAP:
            session.buffer.pop(0)
        session.buffer.append(text)

        clean = _strip_ansi(text)
        session.url_buffer = (session.url_buffer + clean)[-_URL_BUFFER_LIMIT:]
        out_data = re.sub(r"OPEN_URL:\s*(https?://[^\s\x1b\x07]+)", r"[INFO] Opening in browser: \1", text)

        normalized = [u for u in (_normalize_url(x) for x in _extract_urls(session.url_buffer)) if u]
        deduped = [u for u in dict.fromkeys(normalized)
                   if not any(o != u and o.startswith(u) for o in normalized)]
        for u in deduped:
            if u not in session.announced_urls:
                session.announced_urls.add(u)
                await _safe_send(session.ws, {"type": "auth_url", "url": u, "autoOpen": False})
        if _should_auto_open(clean) and deduped:
            best = max(deduped, key=len)
            if best not in session.announced_urls or True:
                await _safe_send(session.ws, {"type": "auth_url", "url": best, "autoOpen": True})

        await _safe_send(session.ws, {"type": "output", "data": out_data})


async def _on_exit(session: ShellSession) -> None:
    try:
        code = await session.proc.wait()
    except Exception:
        code = -1
    loop = asyncio.get_event_loop()
    try:
        loop.remove_reader(session.fd_master)
    except Exception:
        logger.debug("loop.remove_reader 失败（旁路，已忽略）", exc_info=True)
    # Final drain: a fast process can write its output and exit before the
    # readable callback fires, so pull any remaining bytes off the master here.
    while True:
        try:
            data = os.read(session.fd_master, 65536)
        except (BlockingIOError, InterruptedError):
            break
        except OSError:
            data = b""
        if not data:
            break
        session.out_queue.put_nowait(data)
    # Flush everything queued through the drain task before the exit notice.
    session.out_queue.put_nowait(None)
    if session.drain_task:
        try:
            await session.drain_task
        except Exception:
            logger.debug("session.drain_task 失败（旁路，已忽略）", exc_info=True)
    await _safe_send(session.ws, {"type": "output",
                                  "data": f"\r\n\x1b[33mProcess exited with code {code}\x1b[0m\r\n"})
    _cleanup(session.key)


def _cleanup(key: str) -> None:
    session = _SESSIONS.pop(key, None)
    if not session:
        return
    if session.timeout_handle:
        session.timeout_handle.cancel()
    if session.winpty is not None:
        try:
            if session.winpty.isalive():
                session.winpty.terminate(force=True)
        except Exception:  # noqa: BLE001
            logger.debug("session.winpty.terminate 失败（旁路，已忽略）", exc_info=True)
        return
    loop = asyncio.get_event_loop()
    try:
        loop.remove_reader(session.fd_master)
    except Exception:
        logger.debug("loop.remove_reader 失败（旁路，已忽略）", exc_info=True)
    try:
        os.close(session.fd_master)
    except OSError:
        logger.debug("os.close 失败（旁路，已忽略）", exc_info=True)


def _kill(key: str) -> None:
    session = _SESSIONS.get(key)
    if not session:
        return
    if session.winpty is not None:
        try:
            if session.winpty.isalive():
                session.winpty.terminate(force=True)
        except Exception:  # noqa: BLE001
            logger.debug("session.winpty.terminate 失败（旁路，已忽略）", exc_info=True)
        return
    try:
        session.proc.terminate()
    except ProcessLookupError:
        logger.debug("session.proc.terminate 失败（旁路，已忽略）", exc_info=True)
    except Exception:
        logger.debug("session.proc.terminate 失败（旁路，已忽略）", exc_info=True)


# --- per-connection handler -------------------------------------------------

class ShellConnection:
    """Owns one websocket; tracks its active session key."""

    def __init__(self, websocket):
        self.ws = websocket
        self.key: Optional[str] = None

    async def handle(self, data: dict) -> None:
        mtype = data.get("type")
        if mtype == "init":
            await self._init(data)
        elif mtype == "input":
            session = _SESSIONS.get(self.key) if self.key else None
            if session:
                if session.winpty is not None:
                    try:
                        session.winpty.write(str(data.get("data") or ""))
                    except Exception:  # noqa: BLE001
                        logger.debug("session.winpty.write 失败（旁路，已忽略）", exc_info=True)
                else:
                    try:
                        os.write(session.fd_master, str(data.get("data") or "").encode("utf-8"))
                    except OSError:
                        logger.debug("os.write 失败（旁路，已忽略）", exc_info=True)
        elif mtype == "resize":
            session = _SESSIONS.get(self.key) if self.key else None
            if session:
                cols = int(data.get("cols") or 80)
                rows = int(data.get("rows") or 24)
                if session.winpty is not None:
                    try:
                        session.winpty.setwinsize(rows, cols)
                    except Exception:  # noqa: BLE001
                        logger.debug("session.winpty.setwinsize 失败（旁路，已忽略）", exc_info=True)
                else:
                    try:
                        fcntl.ioctl(session.fd_master, termios.TIOCSWINSZ,
                                    struct.pack("HHHH", rows, cols, 0, 0))
                    except OSError:
                        logger.debug("fcntl.ioctl 失败（旁路，已忽略）", exc_info=True)

    async def _init(self, data: dict) -> None:
        project_path = data.get("projectPath") or os.getcwd()
        session_id = (data.get("sessionId") or "") or None
        has_session = bool(data.get("hasSession"))
        provider = data.get("provider") or "claude"
        initial = data.get("initialCommand") or ""
        is_plain = bool(data.get("isPlainShell")) or (bool(initial) and not has_session) or provider == "plain-shell"

        import base64
        suffix = ""
        if is_plain and initial:
            suffix = "_cmd_" + base64.b64encode(initial.encode()).decode()[:16]
        self.key = f"{project_path}_{session_id or 'default'}{suffix}"

        is_login = bool(initial) and ("setup-token" in initial or "cursor-agent login" in initial or "auth login" in initial)
        if is_login and self.key in _SESSIONS:
            _kill(self.key)
            _cleanup(self.key)

        existing = None if is_login else _SESSIONS.get(self.key)
        if existing:
            if existing.timeout_handle:
                existing.timeout_handle.cancel()
                existing.timeout_handle = None
            existing.ws = self.ws
            await _safe_send(self.ws, {"type": "output",
                                       "data": "\x1b[36m[Reconnected to existing session]\x1b[0m\r\n"})
            for buffered in list(existing.buffer):
                await _safe_send(self.ws, {"type": "output", "data": buffered})
            return

        resolved = os.path.abspath(project_path)
        if not os.path.isdir(resolved):
            await _safe_send(self.ws, {"type": "error", "message": "Invalid project path"})
            return
        if session_id and not _SAFE_SESSION_ID_RE.match(session_id):
            await _safe_send(self.ws, {"type": "error", "message": "Invalid session ID"})
            return

        command = _build_command(data)
        cols = int(data.get("cols") or 80)
        rows = int(data.get("rows") or 24)
        try:
            session = await _spawn(self.key, command, resolved, cols, rows)
        except RuntimeError as e:
            # e.g. pywinpty missing on Windows — tell the user instead of dying silently
            await _safe_send(self.ws, {"type": "error", "message": str(e)})
            return
        session.ws = self.ws

        if is_plain:
            welcome = f"\x1b[36mStarting terminal in: {project_path}\x1b[0m\r\n"
        else:
            names = {"cursor": "Cursor", "codex": "Codex", "gemini": "Gemini",
                     "opencode": "OpenCode", "hermes": "Hermes", "agy": "Antigravity",
                     "ivyea": "Ivyea Agent"}
            pname = names.get(provider, "Claude")
            welcome = (f"\x1b[36mResuming {pname} session {session_id} in: {project_path}\x1b[0m\r\n"
                       if has_session else f"\x1b[36mStarting new {pname} session in: {project_path}\x1b[0m\r\n")
        await _safe_send(self.ws, {"type": "output", "data": welcome})

    def on_close(self) -> None:
        """Detach the socket; schedule an idle kill of the PTY (reconnect-friendly)."""
        if not self.key:
            return
        session = _SESSIONS.get(self.key)
        if not session:
            return
        session.ws = None
        loop = asyncio.get_event_loop()
        key = self.key
        session.timeout_handle = loop.call_later(_PTY_TIMEOUT_S, lambda: (_kill(key), _cleanup(key)))
