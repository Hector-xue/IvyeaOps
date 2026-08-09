"""Drive the `claude` CLI via stream-json and translate its event stream into
the agents WebSocket message contract — the Python equivalent of
claudecodeui's ``claude-sdk.js`` (the official Python SDK needs 3.10+; this host
is 3.9, so we speak the CLI's stream-json protocol directly, exactly as the Node
SDK does under the hood).

Two permission paths, mirroring the SDK:

* bypass (toolsSettings.skipPermissions or permissionMode=bypassPermissions):
  run unattended with ``--permission-mode bypassPermissions``; stdin is closed
  right after the turn's user message.

* interactive (default / acceptEdits / plan): add ``--permission-prompt-tool
  stdio`` and open the session with an ``initialize`` control_request. claude
  then emits ``control_request {subtype:can_use_tool}`` for tools needing
  approval; we forward a ``permission_request`` to the UI, await the user's
  ``claude-permission-response`` (resolved via resolve_tool_approval), and write
  back a ``control_response``. AskUserQuestion / ExitPlanMode wait indefinitely;
  other tools time out (deny) after CLAUDE_TOOL_APPROVAL_TIMEOUT_MS.

Stream events (system/init, assistant, user, result) reuse the already-ported
``claude_sessions.normalize_message`` to emit the frontend's message events.
"""
from __future__ import annotations

from app.core.proc import no_window_kwargs

import asyncio
import logging
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.agents import claude_sessions

logger = logging.getLogger("ivyea.agents.claude_driver")

# sessionId -> {"proc", "status", "writer", "start"}
_active_sessions: dict[str, dict] = {}
# requestId(=claude control request_id) -> {"future", "_sessionId", "_toolName", "_input", "_receivedAt"}
_pending_approvals: dict[str, dict] = {}

_INTERACTIVE_MODES = {"default", "plan", "acceptEdits", "dontAsk", "auto"}
_TOOLS_REQUIRING_INTERACTION = {"AskUserQuestion", "ExitPlanMode"}
_APPROVAL_TIMEOUT_S = (int(os.getenv("CLAUDE_TOOL_APPROVAL_TIMEOUT_MS", "55000") or "55000")) / 1000.0
_DATA_URL_RE = re.compile(r"^data:([^;]+);base64,(.+)$", re.DOTALL)
_CONTEXT_WINDOW = int(os.getenv("CONTEXT_WINDOW", "160000") or "160000")


def _rid() -> str:
    return uuid.uuid4().hex[:13]


# --- binary / env resolution ------------------------------------------------

def _claude_bin() -> str:
    try:
        from app.core import hub_settings
        override = (hub_settings.get("claude_bin") or "").strip()
        if override and os.path.exists(override):
            return override
    except Exception:
        logger.debug("override = 失败（旁路，已忽略）", exc_info=True)
    try:
        from app.core import integrations
        search = os.pathsep.join([p for p in integrations.extra_path_dirs() if p]
                          + [os.environ.get("PATH", "")])
    except Exception:
        search = os.environ.get("PATH", "")
    return shutil.which("claude", path=search) or "claude"


def _proc_env() -> dict:
    env = os.environ.copy()
    try:
        from app.core import integrations
        extra = integrations.extra_path_dirs()
    except Exception:
        extra = []
    cur = env.get("PATH", "")
    env["PATH"] = os.pathsep.join([p for p in extra if p] + ([cur] if cur else []))
    env.setdefault("HOME", os.path.expanduser("~"))
    # claude refuses bypassPermissions/--dangerously-skip-permissions as root
    # unless it believes it's sandboxed; ops runs as root, so opt in (matches
    # the workaround in app/services/runners.py).
    env.setdefault("IS_SANDBOX", "1")
    return env


# --- active session bookkeeping ---------------------------------------------

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
    # Cancel any pending tool approvals for this session so the driver unblocks.
    for rid, entry in list(_pending_approvals.items()):
        if entry.get("_sessionId") == session_id and not entry["future"].done():
            entry["future"].set_result({"cancelled": True})
    proc = s.get("proc")
    try:
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                proc.kill()
    except (ProcessLookupError, Exception):
        logger.debug("proc.terminate 失败（旁路，已忽略）", exc_info=True)
    _active_sessions.pop(session_id, None)
    return True


def reconnect_writer(session_id: str, ws) -> bool:
    s = _active_sessions.get(session_id)
    if not s or not s.get("writer"):
        return False
    s["writer"].update_ws(ws)
    return True


def resolve_tool_approval(request_id: str, decision: dict) -> None:
    entry = _pending_approvals.get(request_id)
    if entry and not entry["future"].done():
        entry["future"].set_result(decision)


def get_pending_for_session(session_id: str) -> list:
    out = []
    for rid, e in _pending_approvals.items():
        if e.get("_sessionId") == session_id:
            out.append({"requestId": rid, "toolName": e.get("_toolName") or "UnknownTool",
                        "input": e.get("_input"), "sessionId": session_id,
                        "receivedAt": e.get("_receivedAt")})
    return out


# --- input building ---------------------------------------------------------

def _build_user_message(command: str, images: Optional[list]) -> dict:
    content: list[dict] = []
    if command:
        content.append({"type": "text", "text": command})
    for img in images or []:
        data_url = img.get("data") if isinstance(img, dict) else None
        if not isinstance(data_url, str):
            continue
        m = _DATA_URL_RE.match(data_url)
        if not m:
            continue
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": m.group(1), "data": m.group(2)}})
    if not content:
        content.append({"type": "text", "text": command or ""})
    return {"type": "user", "message": {"role": "user", "content": content}}


def _extract_token_budget(ev: dict) -> Optional[dict]:
    usage = (ev.get("message") or {}).get("usage") or ev.get("usage")
    if isinstance(usage, dict):
        inp = int(usage.get("input_tokens") or usage.get("inputTokens") or 0)
        out = int(usage.get("output_tokens") or usage.get("outputTokens") or 0)
        return {"used": inp + out, "total": _CONTEXT_WINDOW, "inputTokens": inp,
                "outputTokens": out, "breakdown": {"input": inp, "output": out}}
    return None


def _build_argv(options: dict) -> tuple[list[str], bool]:
    """Returns (argv, interactive). ``interactive`` means per-tool approval over
    the stdio control protocol is enabled."""
    argv = [_claude_bin(), "-p", "--input-format", "stream-json",
            "--output-format", "stream-json", "--verbose"]
    model = options.get("model")
    if model:
        argv += ["--model", str(model)]

    tools = options.get("toolsSettings") or {}
    requested = options.get("permissionMode")
    if tools.get("skipPermissions") or requested == "bypassPermissions":
        argv += ["--permission-mode", "bypassPermissions"]
        interactive = False
    else:
        mode = requested if requested in _INTERACTIVE_MODES else "default"
        argv += ["--permission-mode", mode, "--permission-prompt-tool", "stdio"]
        interactive = True

    disallowed = tools.get("disallowedTools") or []
    if isinstance(disallowed, list) and disallowed:
        argv += ["--disallowedTools", *[str(t) for t in disallowed]]

    session_id = options.get("sessionId")
    if session_id:
        argv += ["--resume", str(session_id)]
    return argv, interactive


# --- control protocol -------------------------------------------------------

async def _write_stdin(proc, obj: dict) -> None:
    if proc.stdin is None or proc.stdin.is_closing():
        return
    proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    await proc.stdin.drain()


async def _handle_can_use_tool(ev: dict, proc, writer, sid: Optional[str]) -> None:
    """Forward a can_use_tool control_request to the UI, await the user's
    decision, and write the control_response back to claude."""
    req = ev.get("request") or {}
    request_id = ev.get("request_id")
    tool_name = req.get("tool_name")
    tool_input = req.get("input")
    tool_use_id = req.get("tool_use_id")

    # Register the pending future BEFORE announcing the request, so a response
    # can never arrive (and be dropped) before we're ready to receive it.
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _pending_approvals[request_id] = {"future": fut, "_sessionId": sid,
                                      "_toolName": tool_name, "_input": tool_input,
                                      "_receivedAt": datetime.now(timezone.utc).isoformat()}

    await writer.send(claude_sessions.create_normalized_message(
        kind="permission_request", requestId=request_id, toolName=tool_name,
        input=tool_input, sessionId=sid, provider="claude"))
    decision: Optional[dict]
    try:
        if tool_name in _TOOLS_REQUIRING_INTERACTION:
            decision = await fut  # interactive tools wait indefinitely
        else:
            decision = await asyncio.wait_for(fut, timeout=_APPROVAL_TIMEOUT_S)
    except asyncio.TimeoutError:
        decision = None
    finally:
        _pending_approvals.pop(request_id, None)

    if not decision:
        resp = {"behavior": "deny", "message": "Permission request timed out", "toolUseID": tool_use_id}
    elif decision.get("cancelled"):
        await writer.send(claude_sessions.create_normalized_message(
            kind="permission_cancelled", requestId=request_id, reason="cancelled",
            sessionId=sid, provider="claude"))
        resp = {"behavior": "deny", "message": "Permission request cancelled", "toolUseID": tool_use_id}
    elif decision.get("allow"):
        resp = {"behavior": "allow",
                "updatedInput": decision.get("updatedInput") if decision.get("updatedInput") is not None else tool_input,
                "toolUseID": tool_use_id}
    else:
        resp = {"behavior": "deny", "message": decision.get("message") or "User denied tool use",
                "toolUseID": tool_use_id}

    await _write_stdin(proc, {"type": "control_response", "response": {
        "subtype": "success", "request_id": request_id, "response": resp}})


# --- main driver ------------------------------------------------------------

async def query_claude(command: str, options: dict, writer) -> None:
    options = options or {}
    requested_session_id = options.get("sessionId")
    captured = requested_session_id
    cwd = options.get("cwd") or os.path.expanduser("~")
    if not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")

    try:
        argv, interactive = _build_argv(options)
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, cwd=cwd, env=_proc_env(),
            **no_window_kwargs())
    except FileNotFoundError:
        await writer.send(claude_sessions.create_normalized_message(
            kind="error", content="Claude Code is not installed.", sessionId=captured, provider="claude"))
        return
    except Exception as e:
        await writer.send(claude_sessions.create_normalized_message(
            kind="error", content=str(e), sessionId=captured, provider="claude"))
        return

    if captured:
        _active_sessions[captured] = {"proc": proc, "status": "active",
                                      "writer": writer, "start": time.time()}

    # Interactive sessions open with the SDK initialize handshake (enables the
    # can_use_tool control protocol) and keep stdin open for control_responses.
    try:
        if interactive:
            await _write_stdin(proc, {"type": "control_request", "request_id": _rid(),
                                      "request": {"subtype": "initialize", "hooks": {}}})
        await _write_stdin(proc, _build_user_message(command, options.get("images")))
        if not interactive and proc.stdin is not None:
            proc.stdin.close()
    except Exception:
        logger.debug("_write_stdin 失败（旁路，已忽略）", exc_info=True)

    session_created_sent = False
    try:
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = ev.get("type")
            if etype == "control_request":
                if (ev.get("request") or {}).get("subtype") == "can_use_tool":
                    await _handle_can_use_tool(ev, proc, writer, captured or requested_session_id)
                else:
                    # We registered no hooks/MCP, so reply error to avoid a hang.
                    await _write_stdin(proc, {"type": "control_response", "response": {
                        "subtype": "error", "request_id": ev.get("request_id"),
                        "error": "unsupported"}})
                continue
            if etype == "control_response":
                continue  # ack for our initialize/interrupt — nothing to do

            sid_from_ev = ev.get("session_id")
            if sid_from_ev and not captured:
                captured = sid_from_ev
                _active_sessions[captured] = {"proc": proc, "status": "active",
                                              "writer": writer, "start": time.time()}
                writer.set_session_id(captured)
                if not requested_session_id and not session_created_sent:
                    session_created_sent = True
                    await writer.send(claude_sessions.create_normalized_message(
                        kind="session_created", newSessionId=captured,
                        sessionId=captured, provider="claude"))

            sid = captured or requested_session_id
            for m in claude_sessions.normalize_message(ev, sid):
                if ev.get("parent_tool_use_id") and not m.get("parentToolUseId"):
                    m["parentToolUseId"] = ev["parent_tool_use_id"]
                await writer.send(m)

            budget = _extract_token_budget(ev)
            if budget:
                await writer.send(claude_sessions.create_normalized_message(
                    kind="status", text="token_budget", tokenBudget=budget,
                    sessionId=sid, provider="claude"))

            # One turn per command: close stdin after the result so claude exits.
            if etype == "result" and interactive and proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.close()

        await proc.wait()
        aborted = bool(captured and _active_sessions.get(captured, {}).get("status") == "aborted")
        if captured:
            _active_sessions.pop(captured, None)
        if not aborted:
            await writer.send(claude_sessions.create_normalized_message(
                kind="complete", exitCode=0,
                isNewSession=bool(not requested_session_id and command),
                sessionId=captured, provider="claude"))
    except Exception as e:
        if captured:
            _active_sessions.pop(captured, None)
        await writer.send(claude_sessions.create_normalized_message(
            kind="error", content=str(e), sessionId=captured or requested_session_id, provider="claude"))
