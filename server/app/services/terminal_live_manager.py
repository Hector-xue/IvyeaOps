"""Live PTY manager for IvyeaOps server-terminal sessions.

One terminal session ↔ one shell PTY. Sessions stay alive when the browser
reloads; WebSocket subscribers can detach/reattach. Output is persisted via
terminal_live_service so the UI can view old records.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

_WINDOWS = sys.platform == "win32"
if not _WINDOWS:
    import pty

import pyte

from app.services import terminal_live_service as svc

MAX_LIVE_TERMINALS = int(os.environ.get("IVYEA_OPS_MAX_TERMINALS", "8"))
IDLE_RECYCLE_SECS = int(os.environ.get("IVYEA_OPS_TERMINAL_IDLE_SECS", str(12 * 60 * 60)))
RING_BYTES = int(os.environ.get("IVYEA_OPS_TERMINAL_RING_BYTES", str(512 * 1024)))
PERSIST_FLUSH_MS = int(os.environ.get("IVYEA_OPS_TERMINAL_FLUSH_MS", "250"))
READ_CHUNK = 8192
RUNAWAY_ITERS = int(os.environ.get("IVYEA_OPS_TERMINAL_RUNAWAY_ITERS", "256"))
PYTE_COLS = 220
PYTE_ROWS = 50

# Periodic 3-slot rolling snapshot. Each capture is the ANSI-stripped
# contents of the terminal's ring buffer (~512KB), so a single snapshot
# row covers minutes of activity (hundreds of lines), not just the
# currently-visible 50-line pyte window. The service layer keeps at most
# three rows per session (curr / prev / before), so the count stays
# bounded no matter how long the session runs.
SNAPSHOT_INTERVAL_S = int(os.environ.get("IVYEA_OPS_TERMINAL_SNAPSHOT_INTERVAL", "300"))
# Skip writing if normalized content is shorter than this (prompt-only /
# blank). Manual capture bypasses.
SNAPSHOT_MIN_CHARS = 200

OSC_COLOR_REPLY_RE = re.compile(r"\x1b\](10|11);(?:rgb:[0-9a-fA-F/]+|\?)(?:\x07|\x1b\\)")
BARE_OSC_COLOR_REPLY_RE = re.compile(r"\](10|11);rgb:[0-9a-fA-F/]+")
ANSI_STRIP_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]|\x1B\][^\x07]*\x07|\x1B[=>]")
CR_NL_RE = re.compile(r"\r\n?")
ALT_SCREEN_ENTER_RE = re.compile(rb"\x1b\[\?(?:1049|47)h")
ALT_SCREEN_LEAVE_RE = re.compile(rb"\x1b\[\?(?:1049|47)l")
BOX_HLINE_RE = re.compile(r"─{3,}")
PROMPT_ONLY_RE = re.compile(r"^\[[^\n]+@[^\n]+\][#$] ?$")
PROMPT_PREFIX_RE = re.compile(r"^(\[[^\n]+@[^\n]+\][#$] ?)(.*)$")


def strip_terminal_auto_replies(data: str) -> str:
    if not data:
        return data
    data = OSC_COLOR_REPLY_RE.sub("", data)
    data = BARE_OSC_COLOR_REPLY_RE.sub("", data)
    return data


def strip_ansi(text: str) -> str:
    return CR_NL_RE.sub("\n", ANSI_STRIP_RE.sub("", text))


def strip_control_chars(text: str) -> str:
    if not text:
        return text
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if ch in ("\n", "\t"):
            out.append(ch)
        elif code < 32 or code == 127:
            continue
        else:
            out.append(ch)
    return "".join(out)


def normalize_input_chunk(current: str, data: str) -> tuple[str, list[str]]:
    submitted: list[str] = []
    i = 0
    while i < len(data):
        ch = data[i]
        if ch == "\x1b":
            i += 1
            if i < len(data) and data[i] == "[":
                i += 1
                while i < len(data) and not ("@" <= data[i] <= "~"):
                    i += 1
                if i < len(data):
                    i += 1
            continue
        if ch in ("\b", "\x7f"):
            current = current[:-1]
            i += 1
            continue
        if ch == "\r":
            line = current.strip()
            if line:
                submitted.append(line)
            current = ""
            i += 1
            if i < len(data) and data[i] == "\n":
                i += 1
            continue
        if ch == "\n":
            line = current.strip()
            if line:
                submitted.append(line)
            current = ""
            i += 1
            continue
        code = ord(ch)
        if code < 32:
            i += 1
            continue
        current += ch
        i += 1
    return current, submitted


def clean_output_for_history(term: "LiveTerminal | None", text: str) -> str:
    text = strip_control_chars(text)
    if not text:
        return ""
    raw_lines = text.split("\n")
    kept: list[str] = []
    saw_non_prompt = False
    current_input = (term.input_buffer if term else "").strip()
    last_submitted = (term.last_submitted_input if term else "").strip()

    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            if saw_non_prompt and kept and kept[-1] != "":
                kept.append("")
            continue
        if PROMPT_ONLY_RE.match(stripped):
            continue
        prompt_match = PROMPT_PREFIX_RE.match(stripped)
        if prompt_match:
            payload = prompt_match.group(2).strip()
            if not payload:
                continue
            if current_input and current_input.startswith(payload):
                continue
            if last_submitted and payload == last_submitted:
                continue
            stripped = payload
        kept.append(stripped)
        saw_non_prompt = True

    while kept and kept[-1] == "":
        kept.pop()
    return "\n".join(kept).strip()


@dataclass
class LiveTerminal:
    session_id: str
    proc: asyncio.subprocess.Process
    fd_master: int
    shell: str
    workdir: str
    started_at: float = field(default_factory=time.time)
    last_io_at: float = field(default_factory=time.time)
    ring: bytearray = field(default_factory=bytearray)
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    pending_persist: bytearray = field(default_factory=bytearray)
    last_flush: float = field(default_factory=time.time)
    reader_task: asyncio.Task | None = None
    closed: bool = False
    iters_since_progress: int = 0
    pyte_screen: pyte.Screen = field(default_factory=lambda: pyte.Screen(PYTE_COLS, PYTE_ROWS))
    pyte_stream: pyte.ByteStream = field(init=False)
    last_out_text: str = field(default="")
    in_alt_screen: bool = field(default=False)
    input_buffer: str = field(default="")
    last_submitted_input: str = field(default="")
    pending_output_text: str = field(default="")
    last_snapshot_hash: str = field(default="")
    last_snapshot_at: float = field(default=0.0)
    # Windows ConPTY process (pywinpty PtyProcess). None on POSIX, where fd_master
    # is the real PTY master fd. When set, all fd-based ops branch to winpty.
    winpty: Any = field(default=None)

    def __post_init__(self) -> None:
        self.pyte_stream = pyte.ByteStream(self.pyte_screen)

    def append_ring(self, data: bytes) -> None:
        self.ring.extend(data)
        if len(self.ring) > RING_BYTES:
            del self.ring[: len(self.ring) - RING_BYTES]

    def snapshot(self) -> bytes:
        return bytes(self.ring)


def render_screen_text(term: LiveTerminal) -> str:
    """Render the terminal's recent output for a snapshot.

    Sources from the ring buffer (~512KB of raw PTY bytes), strips ANSI /
    control sequences, and returns plain text. This gives us *scrollback*
    in the snapshot, not just the 50-row pyte viewport — important for AI
    CLIs and other TUI apps where the visible window only shows the last
    few lines of a much longer output stream.

    Returns an empty string if the ring buffer is empty.
    """
    raw = term.snapshot()  # bytes
    if not raw:
        return ""
    text = raw.decode("utf-8", errors="replace")
    text = strip_terminal_auto_replies(text)
    text = strip_ansi(text)
    text = strip_control_chars(text)
    # Drop empty trailing lines so the size reflects real content.
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _normalize_snapshot(text: str) -> str:
    """Reduce a screen render to a comparable form (cursor / trailing ws
    / ANSI / zero-width chars don't count as changes)."""
    if not text:
        return ""
    s = ANSI_STRIP_RE.sub("", text)
    # Drop common zero-width / variation-selector chars
    s = re.sub("[​-‏‪-‮⁠-⁯︀-️]", "", s)
    lines = [ln.rstrip() for ln in s.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


class TerminalLiveManager:
    def __init__(self) -> None:
        self._pool: "OrderedDict[str, LiveTerminal]" = OrderedDict()
        self._lock = asyncio.Lock()
        self._idle_sweep_task: asyncio.Task | None = None
        self._snapshot_task: asyncio.Task | None = None

    def start_background_tasks(self) -> None:
        # Reaper + snapshot loops are pure asyncio/pyte (no PTY syscalls), so they
        # run on Windows too now that ConPTY terminals exist.
        if self._idle_sweep_task is None or self._idle_sweep_task.done():
            self._idle_sweep_task = asyncio.create_task(self._idle_reaper(), name="terminal-live-reaper")
        if self._snapshot_task is None or self._snapshot_task.done():
            self._snapshot_task = asyncio.create_task(self._snapshot_loop(), name="terminal-live-snapshot")

    async def shutdown(self) -> None:
        if _WINDOWS:
            return
        if self._idle_sweep_task:
            self._idle_sweep_task.cancel()
        if self._snapshot_task:
            self._snapshot_task.cancel()
        for sid in list(self._pool.keys()):
            await self._kill(sid, reason="shutdown")

    async def _snapshot_loop(self) -> None:
        """Walk all live terminals every SNAPSHOT_INTERVAL_S seconds and
        rotate-write a fresh snapshot if there's new content since last time."""
        import hashlib
        await asyncio.sleep(min(SNAPSHOT_INTERVAL_S, 60))
        while True:
            try:
                for sid, term in list(self._pool.items()):
                    if term.closed:
                        continue
                    try:
                        text = render_screen_text(term)
                    except Exception:
                        continue
                    norm = _normalize_snapshot(text)
                    if len(norm) < SNAPSHOT_MIN_CHARS:
                        continue
                    h = hashlib.sha1(norm.encode("utf-8", errors="replace")).hexdigest()
                    if h == term.last_snapshot_hash:
                        # No real progress since last capture; skip rotation
                        # to avoid burning the prev/before slots on duplicates.
                        continue
                    try:
                        svc.rotate_snapshot(sid, text)
                        term.last_snapshot_hash = h
                        term.last_snapshot_at = time.time()
                    except Exception:
                        pass
            except Exception:
                pass
            await asyncio.sleep(SNAPSHOT_INTERVAL_S)

    def capture_now(self, session_id: str) -> dict[str, Any]:
        """On-demand snapshot. Bypasses the min-chars check but still
        dedupes against the last stored capture (so spamming the button
        doesn't blow away the prev/before slots with identical content)."""
        import hashlib
        term = self._pool.get(session_id)
        if not term or term.closed:
            return {"ok": False, "error": "终端未运行（先打开终端再保存）"}
        text = render_screen_text(term)
        norm = _normalize_snapshot(text)
        if not norm:
            return {"ok": False, "error": "终端画面为空"}
        h = hashlib.sha1(norm.encode("utf-8", errors="replace")).hexdigest()
        if h == term.last_snapshot_hash:
            return {"ok": True, "skipped": True, "reason": "unchanged"}
        try:
            row = svc.rotate_snapshot(session_id, text)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        term.last_snapshot_hash = h
        term.last_snapshot_at = time.time()
        return {"ok": True, "id": row["id"] if row else None, "ts": row.get("created_at") if row else None}

    async def start(self, session_id: str, *, shell: str, workdir: str | None) -> LiveTerminal:
        async with self._lock:
            existing = self._pool.get(session_id)
            if existing and not existing.closed:
                self._touch(session_id)
                return existing
            await self._evict_if_needed()
            term = await self._spawn(session_id, shell=shell, workdir=workdir)
            self._pool[session_id] = term
            svc.update_session(session_id, status="live", workdir=term.workdir)
            return term

    async def _spawn(self, session_id: str, *, shell: str, workdir: str | None) -> LiveTerminal:
        if _WINDOWS:
            return await self._spawn_windows(session_id, shell=shell, workdir=workdir)
        master, slave = pty.openpty()
        env = os.environ.copy()
        env["TERM"] = env.get("TERM", "xterm-256color")
        env["LANG"] = env.get("LANG", "en_US.UTF-8")
        env["FORCE_COLOR"] = "1"
        cwd = workdir if workdir and os.path.isdir(workdir) else os.path.expanduser("~")
        safe_shell = shell if shell and os.path.exists(shell) else "/bin/bash"
        argv = [safe_shell, "-i"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
        except FileNotFoundError as e:
            os.close(master)
            os.close(slave)
            raise RuntimeError(f"无法启动终端进程: {e}") from e
        os.close(slave)
        term = LiveTerminal(session_id=session_id, proc=proc, fd_master=master, shell=safe_shell, workdir=cwd)
        term.reader_task = asyncio.create_task(self._read_loop(term), name=f"terminal-read-{session_id[:6]}")
        try:
            svc.add_history(
                session_id,
                stream="system",
                content=f"[terminal started] shell={safe_shell} cwd={cwd}\n",
            )
        except Exception:
            pass
        return term

    async def _spawn_windows(self, session_id: str, *, shell: str, workdir: str | None) -> LiveTerminal:
        """Real Windows terminal via ConPTY (pywinpty) — the same mechanism IDEs
        use. Self-contained: never touches the POSIX fd path."""
        try:
            import winpty  # pywinpty; only installed on Windows
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "Windows 终端组件 (pywinpty) 未能加载。请更新 IvyeaOps 到含该组件的版本。"
            ) from e
        env = {str(k): str(v) for k, v in os.environ.items()}
        env.setdefault("TERM", "xterm-256color")
        cwd = workdir if workdir and os.path.isdir(workdir) else os.path.expanduser("~")
        # Prefer an explicitly configured shell, else PowerShell, else cmd.exe.
        safe_shell = (shell if shell and os.path.isabs(shell) and os.path.exists(shell) else None) \
            or shutil.which("powershell.exe") or os.environ.get("COMSPEC") or "cmd.exe"
        try:
            pty_proc = winpty.PtyProcess.spawn(
                [safe_shell], cwd=cwd, env=env, dimensions=(PYTE_ROWS, PYTE_COLS))
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"无法启动 Windows 终端进程: {e}") from e
        term = LiveTerminal(session_id=session_id, proc=None, fd_master=-1,
                            shell=safe_shell, workdir=cwd, winpty=pty_proc)
        # NOTE: do not touch self._pool / self._lock here — start() holds the lock
        # and registers the term after we return (the POSIX _spawn does the same).
        term.reader_task = asyncio.create_task(
            self._read_loop_win(term), name=f"terminal-readwin-{session_id[:6]}")
        try:
            svc.add_history(session_id, stream="system",
                            content=f"[terminal started] shell={safe_shell} cwd={cwd}\n")
        except Exception:
            pass
        return term

    async def _read_loop_win(self, term: LiveTerminal) -> None:
        """Drain a pywinpty process. ConPTY has no pollable fd, so a daemon thread
        does the blocking reads and hands bytes to the same processing as POSIX."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def reader_thread() -> None:
            while True:
                try:
                    data = term.winpty.read(READ_CHUNK)
                except EOFError:
                    data = ""
                except Exception:  # noqa: BLE001
                    data = ""
                if data:
                    chunk = data.encode("utf-8", errors="replace") if isinstance(data, str) else bytes(data)
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
                    continue
                try:
                    alive = bool(term.winpty.isalive())
                except Exception:  # noqa: BLE001
                    alive = False
                if not alive or term.closed:
                    loop.call_soon_threadsafe(queue.put_nowait, b"")
                    return
                time.sleep(0.02)  # transient empty read; avoid a busy spin

        threading.Thread(target=reader_thread, daemon=True,
                         name=f"winpty-{term.session_id[:6]}").start()
        try:
            while not term.closed:
                chunk = await queue.get()
                if not chunk:
                    break
                term.last_io_at = time.time()
                term.append_ring(chunk)
                term.pending_persist.extend(chunk)
                payload = {"type": "output", "data": chunk.decode("utf-8", errors="replace")}
                for q in list(term.subscribers):
                    if q.qsize() > 200:
                        continue
                    try:
                        q.put_nowait(payload)
                    except asyncio.QueueFull:
                        pass
                now = time.time()
                if (now - term.last_flush) * 1000 >= PERSIST_FLUSH_MS and term.pending_persist:
                    flushed = bytes(term.pending_persist)
                    term.pending_persist.clear()
                    term.last_flush = now
                    self._persist_output(term.session_id, flushed)
        finally:
            if term.pending_persist:
                self._persist_output(term.session_id, bytes(term.pending_persist))
                term.pending_persist.clear()
            self._persist_output(term.session_id, b"", final=True)
            await self._on_exit(term)

    async def _read_loop(self, term: LiveTerminal) -> None:
        loop = asyncio.get_running_loop()
        os.set_blocking(term.fd_master, False)
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def _trip_runaway() -> None:
            if term.proc.returncode is not None:
                try:
                    loop.remove_reader(term.fd_master)
                except Exception:
                    pass
                try:
                    queue.put_nowait(b"")
                except Exception:
                    pass
                return
            term.closed = True
            try:
                loop.remove_reader(term.fd_master)
            except Exception:
                pass
            try:
                loop.create_task(self._kill(term.session_id, reason="runaway"))
            except Exception:
                pass
            try:
                queue.put_nowait(b"")
            except Exception:
                pass

        def on_readable() -> None:
            term.iters_since_progress += 1
            if term.iters_since_progress > RUNAWAY_ITERS:
                _trip_runaway()
                return
            if term.closed:
                try:
                    loop.remove_reader(term.fd_master)
                except Exception:
                    pass
                return
            try:
                chunk = os.read(term.fd_master, READ_CHUNK)
            except (OSError, BlockingIOError):
                if term.proc.returncode is not None:
                    try:
                        loop.remove_reader(term.fd_master)
                    except Exception:
                        pass
                    queue.put_nowait(b"")
                return
            if not chunk:
                try:
                    loop.remove_reader(term.fd_master)
                except Exception:
                    pass
                queue.put_nowait(b"")
                return
            term.iters_since_progress = 0
            queue.put_nowait(chunk)

        loop.add_reader(term.fd_master, on_readable)
        try:
            while not term.closed:
                chunk = await queue.get()
                if not chunk:
                    break
                term.last_io_at = time.time()
                term.append_ring(chunk)
                term.pending_persist.extend(chunk)
                payload = {"type": "output", "data": chunk.decode("utf-8", errors="replace")}
                for q in list(term.subscribers):
                    if q.qsize() > 200:
                        continue
                    try:
                        q.put_nowait(payload)
                    except asyncio.QueueFull:
                        pass
                now = time.time()
                if (now - term.last_flush) * 1000 >= PERSIST_FLUSH_MS and term.pending_persist:
                    flushed = bytes(term.pending_persist)
                    term.pending_persist.clear()
                    term.last_flush = now
                    self._persist_output(term.session_id, flushed)
        finally:
            try:
                loop.remove_reader(term.fd_master)
            except Exception:
                pass
            if term.pending_persist:
                self._persist_output(term.session_id, bytes(term.pending_persist))
                term.pending_persist.clear()
            self._persist_output(term.session_id, b"", final=True)
            await self._on_exit(term)

    def _persist_output(self, session_id: str, data: bytes, *, final: bool = False) -> None:
        term = self._pool.get(session_id)
        if not term:
            return
        if data:
            text = self._render_plain(term, data)
            if text:
                term.pending_output_text += text
        if not term.pending_output_text:
            return

        combined = term.pending_output_text
        if final:
            persist_text = combined
            term.pending_output_text = ""
        else:
            if "\n" not in combined:
                return
            cut = combined.rfind("\n")
            persist_text = combined[: cut + 1]
            term.pending_output_text = combined[cut + 1 :]

        persist_text = clean_output_for_history(term, persist_text)
        if not persist_text:
            return
        # Skip if content is identical or just an incremental extension of the same line
        old = term.last_out_text.strip()
        new = persist_text.strip()
        new_lines = new.splitlines()
        old_lines = old.splitlines()
        # Deduplicate: same single line being extended (user typing)
        if (
            old
            and len(new_lines) == 1
            and len(old_lines) == 1
            and (new_lines[0].startswith(old_lines[0]) or old_lines[0].startswith(new_lines[0]))
        ):
            term.last_out_text = persist_text
            try:
                svc.update_last_output(session_id, persist_text)
            except Exception:
                pass
            return
        term.last_out_text = persist_text
        try:
            svc.add_history(session_id, stream="output", content=persist_text)
        except Exception:
            pass

    @staticmethod
    def _render_plain(term: "LiveTerminal | None", data: bytes) -> str:
        decoded = strip_terminal_auto_replies(data.decode("utf-8", errors="replace"))
        cleaned = strip_ansi(decoded)
        if term is None:
            return cleaned
        try:
            # Track alternate screen mode (TUI apps like vim, htop, codex)
            if ALT_SCREEN_ENTER_RE.search(data):
                term.in_alt_screen = True
            if ALT_SCREEN_LEAVE_RE.search(data):
                term.in_alt_screen = False

            term.pyte_stream.feed(data)

            # Skip capture while inside a TUI alternate screen
            if term.in_alt_screen:
                term.pyte_screen.dirty.clear()
                return ""

            if cleaned.strip():
                return cleaned

            dirty = sorted(term.pyte_screen.dirty)
            term.pyte_screen.dirty.clear()
            lines = []
            for row_idx in dirty:
                row = term.pyte_screen.buffer.get(row_idx)
                if not row:
                    continue
                max_col = max(row.keys(), default=-1)
                if max_col < 0:
                    continue
                line = "".join(row[i].data for i in range(max_col + 1)).rstrip()
                stripped = line.strip()
                if not stripped:
                    continue
                # Skip box-drawing decoration lines (TUI borders like ╭────╮)
                if BOX_HLINE_RE.search(stripped):
                    continue
                # Skip lines that are only border characters with spaces
                if all(c in "│╭╮╰╯├┤┬┴┼ " for c in stripped):
                    continue
                lines.append(line)
            return "\n".join(lines) + "\n" if lines else ""
        except Exception:
            return cleaned

    async def _on_exit(self, term: LiveTerminal) -> None:
        if term.closed:
            return
        term.closed = True
        if term.winpty is not None:
            try:
                rc = term.winpty.exitstatus
            except Exception:  # noqa: BLE001
                rc = None
            try:
                if term.winpty.isalive():
                    term.winpty.terminate(force=True)
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                asyncio.get_running_loop().remove_reader(term.fd_master)
            except Exception:
                pass
            try:
                rc = await asyncio.wait_for(term.proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                rc = None
            try:
                os.close(term.fd_master)
            except OSError:
                pass
        for q in list(term.subscribers):
            try:
                q.put_nowait({"type": "exit", "code": rc})
            except asyncio.QueueFull:
                pass
        try:
            svc.update_session(term.session_id, status="closed")
            svc.add_history(term.session_id, stream="system", content=f"\n[terminal exited] code={rc}\n")
        except Exception:
            pass
        async with self._lock:
            self._pool.pop(term.session_id, None)

    async def _evict_if_needed(self) -> None:
        if len(self._pool) < MAX_LIVE_TERMINALS:
            return
        for sid, term in list(self._pool.items()):
            if not term.subscribers:
                await self._kill(sid, reason="lru")
                if len(self._pool) < MAX_LIVE_TERMINALS:
                    return
        oldest = next(iter(self._pool))
        await self._kill(oldest, reason="lru-forced")

    async def _kill(self, session_id: str, *, reason: str) -> None:
        term = self._pool.get(session_id)
        if not term:
            return
        term.closed = True
        if term.winpty is not None:
            try:
                if term.winpty.isalive():
                    term.winpty.terminate(force=True)
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                asyncio.get_running_loop().remove_reader(term.fd_master)
            except Exception:
                pass
            try:
                term.proc.terminate()
                try:
                    await asyncio.wait_for(term.proc.wait(), timeout=4)
                except asyncio.TimeoutError:
                    term.proc.kill()
            except ProcessLookupError:
                pass
            try:
                os.close(term.fd_master)
            except OSError:
                pass
        for q in list(term.subscribers):
            try:
                q.put_nowait({"type": "exit", "code": -1, "reason": reason})
            except asyncio.QueueFull:
                pass
        self._pool.pop(session_id, None)
        try:
            svc.update_session(session_id, status="closed")
            svc.add_history(session_id, stream="system", content=f"\n[terminal closed] reason={reason}\n")
        except Exception:
            pass

    async def _idle_reaper(self) -> None:
        try:
            while True:
                await asyncio.sleep(60)
                now = time.time()
                async with self._lock:
                    for sid, term in list(self._pool.items()):
                        if term.subscribers:
                            continue
                        if now - term.last_io_at > IDLE_RECYCLE_SECS:
                            await self._kill(sid, reason="idle")
        except asyncio.CancelledError:
            return

    def _touch(self, session_id: str) -> None:
        if session_id in self._pool:
            self._pool.move_to_end(session_id)

    async def write(self, session_id: str, data: str) -> None:
        term = self._pool.get(session_id)
        if not term or term.closed:
            raise RuntimeError("终端会话不在线")
        data = strip_terminal_auto_replies(data)
        if not data:
            return
        if term.winpty is not None:
            try:
                term.winpty.write(data)
                term.last_io_at = time.time()
                self._touch(session_id)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError("终端已关闭") from e
        else:
            try:
                os.write(term.fd_master, data.encode("utf-8"))
                term.last_io_at = time.time()
                self._touch(session_id)
            except BrokenPipeError as e:
                raise RuntimeError("终端已关闭") from e
        term.input_buffer, submitted_lines = normalize_input_chunk(term.input_buffer, data)
        for line in submitted_lines:
            term.last_submitted_input = line
            try:
                svc.add_history(session_id, stream="input", content=f"{line}\n")
            except Exception:
                pass

    async def resize(self, session_id: str, cols: int, rows: int) -> None:
        term = self._pool.get(session_id)
        if not term:
            return
        if term.winpty is not None:
            try:
                term.winpty.setwinsize(rows, cols)
            except Exception:  # noqa: BLE001
                pass
            return
        import fcntl
        import struct
        import termios

        try:
            fcntl.ioctl(term.fd_master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass

    def subscribe(self, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        term = self._pool.get(session_id)
        if not term:
            raise RuntimeError("终端会话不在线")
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=400)
        term.subscribers.add(q)
        snapshot = term.snapshot()
        if snapshot:
            try:
                q.put_nowait({"type": "snapshot", "data": snapshot.decode("utf-8", errors="replace")})
            except asyncio.QueueFull:
                pass
        return q

    def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        term = self._pool.get(session_id)
        if term:
            term.subscribers.discard(queue)

    def is_live(self, session_id: str) -> bool:
        term = self._pool.get(session_id)
        return bool(term and not term.closed)

    def stats(self) -> dict[str, Any]:
        return {
            "pool_size": len(self._pool),
            "max_live": MAX_LIVE_TERMINALS,
            "sessions": [
                {
                    "session_id": t.session_id,
                    "shell": t.shell,
                    "workdir": t.workdir,
                    "subscribers": len(t.subscribers),
                    "started_at": t.started_at,
                    "last_io_at": t.last_io_at,
                }
                for t in self._pool.values()
            ],
        }


manager = TerminalLiveManager()
