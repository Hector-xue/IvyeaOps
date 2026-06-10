"""Drive the Hermes agent CLI for agents chat + read hermes history.

Hermes isn't a claudecodeui provider (no claude-style JSONL/SDK), so this is a
thin adapter: run ``hermes chat -q <prompt> -Q --yolo [-m model]`` and surface
the reply as the agents chat contract (session_created → text/error →
complete). Hermes sessions live in hermes' own SQLite store and per-session
JSON files at ``~/.hermes/sessions/session_<id>.json`` (no project cwd).

Hardening (after observing a real run): hermes can take ~60s and then return a
provider error (e.g. 401 invalid key); we therefore (a) cap the run with a
timeout, (b) classify error-looking output and emit it as a `kind:error` event
instead of a silent wait / a normal assistant bubble, and (c) log throughout.
"""
from __future__ import annotations

from app.core.proc import no_window_kwargs

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.agents.claude_sessions import create_normalized_message

logger = logging.getLogger("agents.hermes")

PROVIDER = "hermes"
_active_sessions: dict[str, dict] = {}
_HERMES_SESSIONS_DIR = Path.home() / ".hermes" / "sessions"
_TIMEOUT_S = float(os.getenv("AGENTS_HERMES_TIMEOUT_S", "300") or "300")
# Output that means "the provider call failed" — surface as an error, not a reply.
_ERROR_MARKERS = ("invalid api key", "error code:", "\"code\": \"401\"", "status\":401",
                  "unauthorized", "rate limit", "quota exceeded", "insufficient")


def _hermes_bin() -> str:
    try:
        from app.core import hub_settings
        override = (hub_settings.get("hermes_bin") or "").strip()
        if override and os.path.exists(override):
            return override
    except Exception:
        pass
    search = ":".join([os.path.expanduser("~/.local/bin"),
                       os.path.expanduser("~/.hermes/node/bin"),
                       os.environ.get("PATH", "")])
    return shutil.which("hermes", path=search) or "hermes"


def _proc_env() -> dict:
    env = os.environ.copy()
    extra = [os.path.expanduser("~/.local/bin"), os.path.expanduser("~/.hermes/node/bin")]
    env["PATH"] = ":".join(extra + [env.get("PATH", "")])
    env.setdefault("HOME", os.path.expanduser("~"))
    return env


def is_active(session_id: str) -> bool:
    s = _active_sessions.get(session_id)
    return bool(s and s.get("status") == "active")


def get_active() -> list[str]:
    return list(_active_sessions.keys())


async def abort_session(session_id: str) -> bool:
    s = _active_sessions.get(session_id)
    if not s:
        return False
    s["status"] = "aborted"
    proc = s.get("proc")
    try:
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                proc.kill()
    except ProcessLookupError:
        pass
    except Exception:
        logger.exception("hermes abort failed for %s", session_id)
    _active_sessions.pop(session_id, None)
    return True


def _looks_like_error(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in _ERROR_MARKERS)


async def query_hermes(command: str, options: dict, writer) -> None:
    options = options or {}
    requested = options.get("sessionId")
    session_id = requested or str(uuid.uuid4())
    model = options.get("model")
    cwd = options.get("cwd") or os.path.expanduser("~")
    if not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")

    argv = [_hermes_bin(), "chat", "-q", command or "", "-Q", "--yolo"]
    if model and str(model) not in ("default", "auto"):
        argv += ["-m", str(model)]
    # Resume the real hermes session so multi-turn chats stay in one session
    # (the agents UI passes back the real id captured on the previous turn).
    if requested:
        argv += ["--resume", str(requested)]

    logger.info("hermes chat start session=%s model=%s cwd=%s", session_id, model, cwd)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, cwd=cwd, env=_proc_env(),
            **no_window_kwargs())
    except FileNotFoundError:
        await writer.send(create_normalized_message(
            kind="error", content="Hermes CLI 未安装。", sessionId=session_id, provider=PROVIDER))
        return
    except Exception as e:
        logger.exception("hermes spawn failed")
        await writer.send(create_normalized_message(
            kind="error", content=f"启动 Hermes 失败: {e}", sessionId=session_id, provider=PROVIDER))
        return

    _active_sessions[session_id] = {"proc": proc, "status": "active", "writer": writer,
                                    "start": time.time()}
    if not requested:
        await writer.send(create_normalized_message(
            kind="session_created", newSessionId=session_id, sessionId=session_id, provider=PROVIDER))

    chunks: list[str] = []

    async def _drain() -> None:
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            chunks.append(raw.decode("utf-8", "replace"))
        await proc.wait()

    timed_out = False
    try:
        await asyncio.wait_for(_drain(), timeout=_TIMEOUT_S)
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.kill()
        except Exception:
            pass
        logger.warning("hermes chat timed out after %ss session=%s", _TIMEOUT_S, session_id)
    except Exception as e:
        _active_sessions.pop(session_id, None)
        logger.exception("hermes read loop error")
        await writer.send(create_normalized_message(
            kind="error", content=f"Hermes 读取失败: {e}", sessionId=session_id, provider=PROVIDER))
        return

    aborted = _active_sessions.get(session_id, {}).get("status") == "aborted"
    _active_sessions.pop(session_id, None)
    if aborted:
        return

    # `-Q` prints a trailing "session_id: <real_id>" line: capture it (hermes
    # mints its own id; ours was a provisional handle) and drop it from the reply.
    real_id = None
    body_lines = []
    for ln in "".join(chunks).splitlines():
        if ln.strip().startswith("session_id:"):
            real_id = ln.split(":", 1)[1].strip() or real_id
            continue
        body_lines.append(ln)
    text = "\n".join(body_lines).strip()
    final_id = real_id or session_id

    if timed_out:
        await writer.send(create_normalized_message(
            kind="error", content=f"Hermes 响应超时（>{int(_TIMEOUT_S)}s）。" + (f"\n{text}" if text else ""),
            sessionId=session_id, provider=PROVIDER))
        return

    if text and _looks_like_error(text):
        # e.g. "Error code: 401 - Invalid API Key" — hermes provider auth failed.
        logger.info("hermes returned provider error session=%s: %s", session_id, text[:200])
        await writer.send(create_normalized_message(
            kind="error", content=text, sessionId=session_id, provider=PROVIDER))
    elif text:
        await writer.send(create_normalized_message(
            kind="text", role="assistant", content=text, sessionId=session_id, provider=PROVIDER))
    else:
        # Hermes exited without producing any reply — surface it instead of leaving
        # the user with a silent "no response". Almost always a model/provider/key
        # config gap (hermes ran, but its underlying LLM returned nothing).
        logger.info("hermes returned empty output session=%s rc=%s", session_id, proc.returncode)
        await writer.send(create_normalized_message(
            kind="error",
            content="Hermes 没有返回任何内容。通常是 Hermes 的模型 / Provider / API Key 未正确配置（hermes 跑通了，但底层大模型没产出）。请到「系统配置 → 大模型」检查 Hermes 主模型与 Key，保存后重试。",
            sessionId=session_id, provider=PROVIDER))

    # Index this session into the projects DB *now*, under hermes' REAL id (which
    # is also what the full sync uses) so it appears in the sidebar immediately
    # without waiting for the throttled (60s) full sync — and without creating a
    # duplicate phantom row under our provisional id.
    if text and not _looks_like_error(text):
        try:
            from app.agents import synchronizer
            synchronizer.index_hermes_session(final_id, command if not requested else None)
        except Exception:
            logger.exception("index_hermes_session after turn failed")

    # If hermes minted a different id than our provisional handle, tell the client
    # to swap (replaceSessionId) — same contract claude/codex use — so the sidebar
    # entry and the open chat both point at the real, resumable session id.
    await writer.send(create_normalized_message(
        kind="complete", exitCode=proc.returncode or 0,
        isNewSession=bool(not requested and command), sessionId=session_id,
        actualSessionId=(final_id if final_id != session_id else None),
        provider=PROVIDER))


async def purge_session(session_id: str) -> bool:
    """Delete a session from hermes' SQLite store so the next sync can't
    resurrect it after the user deletes it in the UI. Best-effort."""
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "_-")
    if not safe:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            _hermes_bin(), "sessions", "delete", safe, "--yes",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, env=_proc_env(), **no_window_kwargs())
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            logger.warning("hermes sessions delete rc=%s: %s", proc.returncode, (out or b"")[:200])
            return False
        return True
    except Exception:
        logger.exception("hermes purge_session failed for %s", session_id)
        return False


def _export_messages(safe_id: str) -> list:
    """Fallback for sessions that live only in hermes' SQLite store (no json
    file): export the single session and read its messages."""
    binary = _hermes_bin()
    env = os.environ.copy()
    env["PATH"] = ":".join([os.path.expanduser("~/.local/bin"),
                            os.path.expanduser("~/.hermes/node/bin"), env.get("PATH", "")])
    try:
        proc = subprocess.run([binary, "sessions", "export", "-", "--session-id", safe_id],
                              capture_output=True, text=True, timeout=30, env=env,
                              **no_window_kwargs())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning("hermes export --session-id %s failed: %s", safe_id, e)
        return []
    if proc.returncode != 0:
        return []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except (ValueError, TypeError):
            continue
        if d.get("id") == safe_id or d.get("messages") is not None:
            return d.get("messages") or []
    return []


def read_history(session_id: str) -> dict:
    """Read a hermes session transcript into the agents message shape. Tries
    the per-session JSON file first, then falls back to the SQLite store via
    `hermes sessions export`. Empty result if neither yields messages."""
    empty = {"messages": [], "total": 0, "hasMore": False, "offset": 0, "limit": None}
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "_-")
    if not safe:
        return empty

    raw_messages = None
    path = _HERMES_SESSIONS_DIR / f"session_{safe}.json"
    if path.exists():
        try:
            raw_messages = json.loads(path.read_text(encoding="utf-8")).get("messages")
        except (OSError, ValueError):
            logger.exception("failed reading hermes session file %s", session_id)
            raw_messages = None
    if raw_messages is None:
        raw_messages = _export_messages(safe)

    raw_messages = raw_messages or []
    out: list[dict] = []
    for m in raw_messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if isinstance(content, list):
            content = "\n".join(
                p.get("text", "") for p in content
                if isinstance(p, dict) and isinstance(p.get("text"), str)
            )
        if not isinstance(content, str) or not content.strip():
            continue
        out.append(create_normalized_message(
            kind="text", role=role, content=content, sessionId=session_id, provider=PROVIDER,
            timestamp=datetime.now(timezone.utc).isoformat()))
    return {"messages": out, "total": len(out), "hasMore": False, "offset": 0, "limit": None}
