"""Per-session PTY manager with LRU eviction and live broadcasting.

Design notes
------------
* One session ↔ one PTY (when active).  We don't multiplex.  This keeps the
  agent's own sense of "current task" intact.
* LRU pool with a hard cap (default 3).  When full, the oldest idle PTY is
  asked to quit and recycled.  Active sessions (one with a connected
  client) are never evicted.
* Output is captured by a background asyncio task, fanned out to all
  subscribed `asyncio.Queue` instances (multi-device attach), AND a small
  ring buffer is retained for late-joiners to replay the last screen.
* Frames also persist to SQLite as `cli_frame` messages, but we throttle
  to one DB write per ~250ms so a chatty agent doesn't thrash the disk.
* Resume: when `start()` is asked to wake a dormant session, we let the
  caller decide whether to push a "resume prompt" into stdin after the
  banner settles.

This is asyncio-first.  All public methods are coroutines so the WS handler
and the chat SSE endpoint can interleave reads cleanly.
"""
from __future__ import annotations

import asyncio
import os
import re
import shlex
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

# PTY is a POSIX-only feature. On Windows we degrade gracefully: the manager
# still exists but all public methods return early with a clear error so the
# rest of the server can boot and serve non-terminal features normally.
_WINDOWS = sys.platform == "win32"
if not _WINDOWS:
    import pty

from app.services import agent_registry as registry
from app.services import agent_session_service as svc

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
MAX_LIVE_PTYS = int(os.environ.get("IVYEA_OPS_MAX_PTYS", "5"))
IDLE_RECYCLE_SECS = int(os.environ.get("IVYEA_OPS_PTY_IDLE_SECS", str(30 * 60)))
RING_BYTES = int(os.environ.get("IVYEA_OPS_PTY_RING_BYTES", str(256 * 1024)))
PERSIST_FLUSH_MS = int(os.environ.get("IVYEA_OPS_PTY_FLUSH_MS", "250"))
READ_CHUNK = 8192
# Circuit-breaker: if the reader callback fires this many times in a row
# without progress (zero-byte reads or os-level errors), the session is
# torn down. Picked conservatively — even a busy agent rarely produces
# more than a few hundred chunks per second per stream, and progress
# resets the counter, so a healthy 1 MB/s stream is fine.
RUNAWAY_ITERS = int(os.environ.get("IVYEA_OPS_PTY_RUNAWAY_ITERS", "256"))

ANSI_STRIP_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]|\x1B\][^\x07]*\x07|\x1B[=>]")
CR_NL_RE = re.compile(r"\r\n?")
OSC_COLOR_REPLY_RE = re.compile(r"\x1b\](10|11);(?:rgb:[0-9a-fA-F/]+|\?)(?:\x07|\x1b\\)")
BARE_OSC_COLOR_REPLY_RE = re.compile(r"\](10|11);rgb:[0-9a-fA-F/]+")


def strip_terminal_auto_replies(data: str) -> str:
    if not data:
        return data
    data = OSC_COLOR_REPLY_RE.sub("", data)
    data = BARE_OSC_COLOR_REPLY_RE.sub("", data)
    return data


def strip_ansi(text: str) -> str:
    """Normalize ANSI-coloured CLI output for the chat-bubble renderer."""
    return CR_NL_RE.sub("\n", ANSI_STRIP_RE.sub("", text))


def split_ansi_safe(buffer: str) -> tuple[str, str]:
    """Split a streaming text buffer into (safe_to_emit, hold_back).

    When we read PTY/subprocess output in chunks, an ANSI escape sequence
    can be split across reads (e.g. one chunk ends with ``\\x1B[`` and the
    next starts with ``m``).  Stripping each chunk independently leaks the
    partial sequence into the user-visible output.

    This function looks at the tail of ``buffer`` and, if it contains the
    start of an incomplete escape sequence, returns that tail in
    ``hold_back`` so the caller can prepend it to the next chunk.

    The look-back window is bounded (32 chars) so a stray ``\\x1B`` doesn't
    block emission indefinitely.
    """
    if not buffer:
        return "", ""
    # Scan backwards for the last \x1B; only check the tail window.
    window = min(len(buffer), 32)
    for i in range(len(buffer) - 1, len(buffer) - window - 1, -1):
        if buffer[i] != "\x1B":
            continue
        tail = buffer[i:]
        if len(tail) == 1:
            # bare ESC — incomplete
            return buffer[:i], tail
        c1 = tail[1]
        if c1 == "[":
            # CSI: complete when a final byte in @-~ appears.
            for ch in tail[2:]:
                if "\x40" <= ch <= "\x7E":
                    return buffer, ""
            return buffer[:i], tail
        if c1 == "]":
            # OSC: complete on BEL (\x07) or ST (\x1B\\).
            if "\x07" in tail or "\x1B\\" in tail:
                return buffer, ""
            return buffer[:i], tail
        if c1 in "=>":
            return buffer, ""
        # Any other 2-char escape — treat as complete.
        return buffer, ""
    return buffer, ""


# ---------------------------------------------------------------------------
# PTY session state
# ---------------------------------------------------------------------------
@dataclass
class PtySession:
    """Encapsulates a live (or just-died) PTY for one session_id."""

    session_id: str
    agent_id: str
    model: str | None
    workdir: str | None
    proc: asyncio.subprocess.Process
    fd_master: int
    started_at: float = field(default_factory=time.time)
    last_io_at: float = field(default_factory=time.time)
    ring: bytearray = field(default_factory=bytearray)
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    pending_persist: bytearray = field(default_factory=bytearray)
    last_flush: float = field(default_factory=time.time)
    reader_task: asyncio.Task | None = None
    closed: bool = False
    # Circuit breaker — count on_readable iterations since the last
    # successful (non-empty) read. If this gets implausibly large the
    # reader is in a tight error loop (typically because the kernel-level
    # fd was closed but our asyncio reader couldn't be unregistered, or
    # the fd was reused by another transport). When the threshold is
    # crossed we mark the session closed and schedule _kill so the loop
    # can never burn CPU indefinitely.
    iters_since_progress: int = 0

    def append_ring(self, data: bytes) -> None:
        self.ring.extend(data)
        if len(self.ring) > RING_BYTES:
            del self.ring[: len(self.ring) - RING_BYTES]

    def snapshot(self) -> bytes:
        """Bytes a fresh client should receive to redraw current state."""
        return bytes(self.ring)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
class PtyManager:
    def __init__(self) -> None:
        # OrderedDict by recency: oldest entries are evicted first.
        self._pool: "OrderedDict[str, PtySession]" = OrderedDict()
        self._lock = asyncio.Lock()
        self._idle_sweep_task: asyncio.Task | None = None

    def start_background_tasks(self) -> None:
        """Launch the idle reaper. Called from app lifespan."""
        if _WINDOWS:
            return  # PTY not supported on Windows
        if self._idle_sweep_task is None or self._idle_sweep_task.done():
            self._idle_sweep_task = asyncio.create_task(self._idle_reaper(), name="pty-reaper")

    async def shutdown(self) -> None:
        if _WINDOWS:
            return
        if self._idle_sweep_task:
            self._idle_sweep_task.cancel()
        for sid in list(self._pool.keys()):
            await self._kill(sid, reason="shutdown")

    # --- lifecycle ---------------------------------------------------------
    async def start(
        self,
        session_id: str,
        *,
        agent_id: str,
        model: str | None,
        workdir: str | None,
        resume_prompt: str | None = None,
    ) -> "PtySession":
        """Spawn a PTY for `session_id`. Idempotent — returns existing if alive.

        If `resume_prompt` is given, it's written to stdin once the banner
        is past, so the agent has the prior context. Use ``compactor`` to
        build that string.
        """
        if _WINDOWS:
            raise RuntimeError(
                "Interactive PTY sessions are not supported on Windows. "
                "Use the non-interactive (one-shot) agent API instead."
            )
        async with self._lock:
            existing = self._pool.get(session_id)
            if existing and not existing.closed:
                self._touch(session_id)
                return existing
            await self._evict_if_needed()
            # One-shot consume: if the session was created via "继续会话"
            # (resume from external Claude/Codex jsonl), wake the agent up
            # by passing --resume <orig_id>. consume_resume_target clears
            # the row so future restarts go through clean.
            resume_id = None
            try:
                raw = svc.consume_resume_target(session_id)
                if raw and ":" in raw:
                    _src, _, sid_part = raw.partition(":")
                    resume_id = sid_part or None
            except Exception:
                pass
            # registry.build_argv may raise if the binary is missing — let
            # that propagate so the caller can return a clean 400.
            argv, env_extra = registry.build_argv(
                agent_id, mode="cli", model=model, resume_id=resume_id,
            )
            sess_obj = await self._spawn(session_id, agent_id, model, workdir, argv, env_extra)
            self._pool[session_id] = sess_obj
            svc.update_session(session_id, status="live")

        # Resume injection happens *after* the lock so we don't hold the
        # whole pool while waiting on the agent banner. Race-safety is
        # provided by the per-session subscriber set.
        if resume_prompt:
            # Give the binary a moment to print its banner so we don't end up
            # racing the splash screen.
            await asyncio.sleep(0.6)
            # Write directly to PTY fd — bypass self.write() which would
            # persist the resume prompt as a user message in the chat view.
            sess_obj = self._pool.get(session_id)
            if sess_obj and not sess_obj.closed:
                try:
                    os.write(sess_obj.fd_master, (resume_prompt + "\n").encode("utf-8"))
                    sess_obj.last_io_at = time.time()
                except OSError:
                    pass
        return sess_obj

    async def _spawn(
        self,
        session_id: str,
        agent_id: str,
        model: str | None,
        workdir: str | None,
        argv: list[str],
        env_extra: dict[str, str],
    ) -> "PtySession":
        if _WINDOWS:
            raise RuntimeError("PTY not supported on Windows.")
        master, slave = pty.openpty()
        env = os.environ.copy()
        env.update(env_extra)
        env["TERM"] = env.get("TERM", "xterm-256color")
        env["LANG"] = env.get("LANG", "en_US.UTF-8")
        env["FORCE_COLOR"] = "1"
        # Ensure node/hermes binaries are on PATH (systemd doesn't load .bashrc)
        hermes_bin = os.path.expanduser("~/.hermes/node/bin")
        if hermes_bin not in env.get("PATH", ""):
            env["PATH"] = hermes_bin + ":" + env.get("PATH", "/usr/bin:/bin")
        cwd = workdir if workdir and os.path.isdir(workdir) else os.path.expanduser("~")
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
            raise RuntimeError(f"无法启动 agent 进程: {e}") from e
        # We keep the master end; the child has its own copy of slave.
        os.close(slave)
        sess_obj = PtySession(
            session_id=session_id,
            agent_id=agent_id,
            model=model,
            workdir=cwd,
            proc=proc,
            fd_master=master,
        )
        sess_obj.reader_task = asyncio.create_task(
            self._read_loop(sess_obj),
            name=f"pty-read-{session_id[:6]}",
        )
        # Drop a system message so the chat view shows when the agent
        # spawned and which argv was used.  Helps debugging "why no reply".
        try:
            svc.add_message(
                session_id,
                role="system",
                kind="cli_frame",
                source="system",
                content=f"[CLI 启动] {agent_id} model={model or '-'} cwd={cwd}\n$ {' '.join(shlex.quote(a) for a in argv)}\n",
            )
        except Exception:
            # Don't let a logging error block the spawn.
            pass
        return sess_obj

    async def _read_loop(self, sess_obj: PtySession) -> None:
        """Drains the PTY master into ring + subscribers + DB.

        We use loop.add_reader because asyncio's default file descriptor
        bridge does the right thing for non-blocking ttys.
        """
        loop = asyncio.get_running_loop()
        os.set_blocking(sess_obj.fd_master, False)
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def _trip_runaway() -> None:
            """Tear down a session whose reader is stuck in a tight loop."""
            sess_obj.closed = True
            try:
                loop.remove_reader(sess_obj.fd_master)
            except Exception:
                pass
            # Schedule a full _kill so the subprocess and pool entry are
            # cleaned up. Use call_soon_threadsafe-equivalent via the loop.
            try:
                loop.create_task(self._kill(sess_obj.session_id, reason="runaway"))
            except Exception:
                pass
            # Wake up the awaiting _read_loop so it falls through finally.
            try:
                queue.put_nowait(b"")
            except Exception:
                pass

        def on_readable() -> None:
            # Circuit breaker — bail out before burning more CPU when the
            # reader has been firing without making progress. This protects
            # against any future fd-lifecycle race; in steady state the
            # counter resets to 0 on every real chunk so this never trips.
            sess_obj.iters_since_progress += 1
            if sess_obj.iters_since_progress > RUNAWAY_ITERS:
                _trip_runaway()
                return

            # Bail out early if the session was closed externally
            # (e.g., _kill / _on_exit). The asyncio loop may still fire this
            # callback once before reaching the unregister step; if the
            # underlying fd was already closed and recycled by the kernel
            # for another transport, attempting any FD-level call here
            # would corrupt unrelated state.
            if sess_obj.closed:
                try:
                    loop.remove_reader(sess_obj.fd_master)
                except Exception:
                    # The fd may have been recycled by another transport
                    # (e.g., httpx TCP). uvloop refuses remove_reader in
                    # that case; ignore — the new owner manages it now.
                    pass
                return
            try:
                chunk = os.read(sess_obj.fd_master, READ_CHUNK)
            except (OSError, BlockingIOError):
                return
            if not chunk:
                # EOF — schedule cleanup.
                try:
                    loop.remove_reader(sess_obj.fd_master)
                except Exception:
                    # Same reasoning as above — fd may be owned elsewhere
                    # already; the read returned 0 because of that.
                    pass
                queue.put_nowait(b"")
                return
            # Successful read — reset the breaker.
            sess_obj.iters_since_progress = 0
            queue.put_nowait(chunk)

        loop.add_reader(sess_obj.fd_master, on_readable)
        try:
            while not sess_obj.closed:
                chunk = await queue.get()
                if not chunk:
                    break
                sess_obj.last_io_at = time.time()
                sess_obj.append_ring(chunk)
                sess_obj.pending_persist.extend(chunk)
                # Fan out to subscribers — best effort, drop on full queue.
                payload = {"type": "output", "data": chunk.decode("utf-8", errors="replace")}
                for q in list(sess_obj.subscribers):
                    if q.qsize() > 200:
                        # Slow consumer; drop. They can re-attach later via
                        # snapshot replay.
                        continue
                    try:
                        q.put_nowait(payload)
                    except asyncio.QueueFull:
                        pass
                # Periodic persistence flush.
                now = time.time()
                if (now - sess_obj.last_flush) * 1000 >= PERSIST_FLUSH_MS and sess_obj.pending_persist:
                    flushed = bytes(sess_obj.pending_persist)
                    sess_obj.pending_persist.clear()
                    sess_obj.last_flush = now
                    self._persist_frame(sess_obj.session_id, flushed)
        finally:
            try:
                loop.remove_reader(sess_obj.fd_master)
            except Exception:
                pass
            # Final flush
            if sess_obj.pending_persist:
                self._persist_frame(sess_obj.session_id, bytes(sess_obj.pending_persist))
                sess_obj.pending_persist.clear()
            await self._on_exit(sess_obj)

    def _persist_frame(self, session_id: str, data: bytes) -> None:
        """Persist a CLI output frame.

        We store the ANSI-cleaned version (because that's what the chat view
        consumes) but tag with `kind=cli_frame` so the CLI view can keep
        rendering it as a terminal scrollback.  Empty frames are skipped.
        Resume prompt echoes are also skipped — they're internal context
        injection and shouldn't pollute the chat history.
        """
        text = data.decode("utf-8", errors="replace")
        cleaned = strip_ansi(text)
        if not cleaned.strip():
            return
        # Skip resume prompt echoes
        if "[最近消息]" in cleaned or "[继续指令]" in cleaned or "[会话恢复" in cleaned:
            return
        try:
            svc.add_message(
                session_id,
                role="assistant",
                kind="cli_frame",
                source="cli",
                content=cleaned,
                meta={"raw_len": len(data)},
            )
        except Exception:
            # Persistence failure is non-fatal.
            pass

    async def _on_exit(self, sess_obj: PtySession) -> None:
        if sess_obj.closed:
            return
        sess_obj.closed = True
        # Detach reader immediately — before any await — so on_readable
        # cannot fire on a potentially-recycled fd.
        try:
            asyncio.get_running_loop().remove_reader(sess_obj.fd_master)
        except Exception:
            pass
        try:
            rc = await asyncio.wait_for(sess_obj.proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            rc = None
        try:
            os.close(sess_obj.fd_master)
        except OSError:
            pass
        # Notify subscribers and detach.
        for q in list(sess_obj.subscribers):
            try:
                q.put_nowait({"type": "exit", "code": rc})
            except asyncio.QueueFull:
                pass
        # Update session status.
        try:
            svc.update_session(sess_obj.session_id, status="dormant")
            svc.add_message(
                sess_obj.session_id,
                role="system",
                kind="cli_frame",
                source="system",
                content=f"[CLI 退出] code={rc}",
            )
        except Exception:
            pass
        # Drop from pool.
        async with self._lock:
            self._pool.pop(sess_obj.session_id, None)

    async def _evict_if_needed(self) -> None:
        if len(self._pool) < MAX_LIVE_PTYS:
            return
        # Find the oldest idle (no subscribers).
        for sid, sess_obj in list(self._pool.items()):
            if not sess_obj.subscribers:
                await self._kill(sid, reason="lru")
                if len(self._pool) < MAX_LIVE_PTYS:
                    return
        # Everything is busy. Evict the absolute oldest anyway — the worst
        # outcome is the user sees a "session re-spawning" notice.
        oldest = next(iter(self._pool))
        await self._kill(oldest, reason="lru-forced")

    async def _kill(self, session_id: str, *, reason: str) -> None:
        sess_obj = self._pool.get(session_id)
        if not sess_obj:
            return
        # Mark closed FIRST so on_readable bails out immediately during
        # the await below. Without this, the callback fires on a stale fd
        # that the kernel may have recycled to a TCP transport, causing
        # RuntimeError storms and eventual OOM/restart.
        sess_obj.closed = True
        # Detach the asyncio reader BEFORE terminate/close — prevents the
        # callback from firing while we await the process exit.
        try:
            asyncio.get_running_loop().remove_reader(sess_obj.fd_master)
        except Exception:
            pass
        try:
            sess_obj.proc.terminate()
            try:
                await asyncio.wait_for(sess_obj.proc.wait(), timeout=4)
            except asyncio.TimeoutError:
                sess_obj.proc.kill()
        except ProcessLookupError:
            pass
        try:
            os.close(sess_obj.fd_master)
        except OSError:
            pass
        for q in list(sess_obj.subscribers):
            try:
                q.put_nowait({"type": "exit", "code": -1, "reason": reason})
            except asyncio.QueueFull:
                pass
        self._pool.pop(session_id, None)
        try:
            svc.update_session(session_id, status="dormant")
        except Exception:
            pass

    async def _idle_reaper(self) -> None:
        try:
            while True:
                await asyncio.sleep(60)
                now = time.time()
                async with self._lock:
                    for sid, sess_obj in list(self._pool.items()):
                        if sess_obj.subscribers:
                            continue
                        if now - sess_obj.last_io_at > IDLE_RECYCLE_SECS:
                            await self._kill(sid, reason="idle")
        except asyncio.CancelledError:
            return

    # --- public IO ---------------------------------------------------------
    def _touch(self, session_id: str) -> None:
        if session_id in self._pool:
            self._pool.move_to_end(session_id)

    async def write(self, session_id: str, data: str) -> None:
        sess_obj = self._pool.get(session_id)
        if not sess_obj or sess_obj.closed:
            raise RuntimeError("会话 PTY 不在线")
        data = strip_terminal_auto_replies(data)
        if not data:
            return
        try:
            os.write(sess_obj.fd_master, data.encode("utf-8"))
            sess_obj.last_io_at = time.time()
            self._touch(session_id)
        except BrokenPipeError as e:
            raise RuntimeError("PTY 已关闭") from e
        if "\n" in data or "\r" in data:
            try:
                svc.add_message(
                    session_id,
                    role="user",
                    kind="text",
                    source="cli",
                    content=data.rstrip("\r\n"),
                )
            except Exception:
                pass

    async def resize(self, session_id: str, cols: int, rows: int) -> None:
        sess_obj = self._pool.get(session_id)
        if not sess_obj:
            return
        # Lazy import — fcntl/termios aren't always needed.
        import fcntl
        import struct
        import termios

        try:
            fcntl.ioctl(sess_obj.fd_master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass

    def subscribe(self, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        sess_obj = self._pool.get(session_id)
        if not sess_obj:
            raise RuntimeError("会话 PTY 不在线")
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=400)
        sess_obj.subscribers.add(q)
        # Replay current ring so the new client redraws state.
        snapshot = sess_obj.snapshot()
        if snapshot:
            try:
                q.put_nowait({
                    "type": "snapshot",
                    "data": snapshot.decode("utf-8", errors="replace"),
                })
            except asyncio.QueueFull:
                pass
        return q

    def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        sess_obj = self._pool.get(session_id)
        if sess_obj:
            sess_obj.subscribers.discard(queue)

    def is_live(self, session_id: str) -> bool:
        sess_obj = self._pool.get(session_id)
        return bool(sess_obj and not sess_obj.closed)

    def stop(self, session_id: str) -> None:
        # Sync wrapper for callers outside async context.
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(self._kill(session_id, reason="manual"))
        else:
            loop.run_until_complete(self._kill(session_id, reason="manual"))

    def stats(self) -> dict[str, Any]:
        return {
            "pool_size": len(self._pool),
            "max_live": MAX_LIVE_PTYS,
            "sessions": [
                {
                    "session_id": s.session_id,
                    "agent_id": s.agent_id,
                    "subscribers": len(s.subscribers),
                    "started_at": s.started_at,
                    "last_io_at": s.last_io_at,
                }
                for s in self._pool.values()
            ],
        }


# Module-level singleton — the routers import this directly.
manager = PtyManager()
