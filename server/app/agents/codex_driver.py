"""Drive the codex CLI (`codex exec --json`) for agents chat — the Python
equivalent of claudecodeui's openai-codex.js (which used the Node @openai/codex-sdk).

`codex exec --json` emits newline-delimited events:
  thread.started {thread_id}                         -> session id / session_created
  turn.started                                        -> (ignored)
  item.completed {item:{type:agent_message,text}}     -> assistant text
  item.completed {item:{type:reasoning,text}}         -> thinking
  item.completed {item:{type:command_execution,...}}  -> tool_use + tool_result
  turn.completed {usage}                              -> token budget, then complete
  turn.failed {error} / error {message}               -> error

Resume continues a thread via `codex exec resume <thread_id>`. ChatGPT-account
auth rejects some models (e.g. gpt-5.4); default to gpt-5.5.
"""
from __future__ import annotations

from app.core.proc import no_window_kwargs

import asyncio
import logging
import json
import os
import shutil
import time
from typing import Optional

from app.agents.claude_sessions import create_normalized_message

logger = logging.getLogger("ivyea.agents.codex_driver")

PROVIDER = "codex"
_active_sessions: dict[str, dict] = {}
_CONTEXT_WINDOW = 272000
_DEFAULT_MODEL = "gpt-5.5"


def read_history(jsonl_path: Optional[str], session_id: str) -> dict:
    """Read a codex rollout (~/.codex/sessions/**/rollout-*.jsonl) into the agents
    message shape, so clicking a codex session shows its transcript (previously this
    returned empty — "看不到对话内容"). User text comes from event_msg/user_message
    (the actually-typed text, not the auto-injected <environment_context>), assistant
    text from response_item/message(role=assistant). Empty result if unreadable."""
    empty = {"messages": [], "total": 0, "hasMore": False, "offset": 0, "limit": None}
    if not jsonl_path or not os.path.isfile(jsonl_path):
        return empty
    out: list[dict] = []
    try:
        with open(jsonl_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except (ValueError, TypeError):
                    continue
                payload = ev.get("payload") or {}
                if not isinstance(payload, dict):
                    continue
                etype = ev.get("type")
                if etype == "event_msg" and payload.get("type") == "user_message":
                    text = (payload.get("message") or "").strip()
                    if text:
                        out.append(create_normalized_message(
                            kind="text", role="user", content=text,
                            sessionId=session_id, provider=PROVIDER))
                elif (etype == "response_item" and payload.get("type") == "message"
                      and payload.get("role") == "assistant"):
                    parts = [b["text"] for b in (payload.get("content") or [])
                             if isinstance(b, dict) and isinstance(b.get("text"), str)]
                    text = "".join(parts).strip()
                    if text:
                        out.append(create_normalized_message(
                            kind="text", role="assistant", content=text,
                            sessionId=session_id, provider=PROVIDER))
    except OSError:
        return empty
    return {"messages": out, "total": len(out), "hasMore": False, "offset": 0, "limit": None}


def _codex_bin() -> str:
    search = os.pathsep.join([os.path.expanduser("~/.hermes/node/bin"), os.environ.get("PATH", "")])
    return shutil.which("codex", path=search) or "codex"


def _proc_env() -> dict:
    env = os.environ.copy()
    extra = os.path.expanduser("~/.hermes/node/bin")
    if extra not in env.get("PATH", ""):
        env["PATH"] = extra + ":" + env.get("PATH", "")
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
    except (ProcessLookupError, Exception):
        logger.debug("proc.terminate 失败（旁路，已忽略）", exc_info=True)
    _active_sessions.pop(session_id, None)
    return True


def _build_argv(command: str, options: dict) -> list[str]:
    argv = [_codex_bin(), "exec"]
    session_id = options.get("sessionId")
    if session_id:
        argv += ["resume", str(session_id)]
    argv += ["--json", "--skip-git-repo-check"]
    cwd = options.get("cwd")
    if cwd and os.path.isdir(cwd):
        argv += ["-C", cwd]
    model = options.get("model")
    if not model or str(model) in ("default", "auto"):
        model = _DEFAULT_MODEL
    argv += ["-m", str(model)]
    tools = options.get("toolsSettings") or {}
    if tools.get("skipPermissions") or options.get("permissionMode") == "bypassPermissions":
        argv += ["--dangerously-bypass-approvals-and-sandbox"]
    else:
        argv += ["--full-auto"]
    argv += [command or ""]
    return argv


async def query_codex(command: str, options: dict, writer) -> None:
    options = options or {}
    requested_session_id = options.get("sessionId")
    captured = requested_session_id
    cwd = options.get("cwd") or os.path.expanduser("~")
    if not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")

    try:
        proc = await asyncio.create_subprocess_exec(
            *_build_argv(command, options), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            cwd=cwd, env=_proc_env(), **no_window_kwargs())
    except FileNotFoundError:
        await writer.send(create_normalized_message(
            kind="error", content="Codex CLI is not installed.", sessionId=captured, provider=PROVIDER))
        return
    except Exception as e:
        await writer.send(create_normalized_message(
            kind="error", content=str(e), sessionId=captured, provider=PROVIDER))
        return

    if captured:
        _active_sessions[captured] = {"proc": proc, "status": "active", "writer": writer, "start": time.time()}

    session_created_sent = False
    try:
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = ev.get("type")

            if etype == "thread.started":
                tid = ev.get("thread_id")
                if tid and not captured:
                    captured = tid
                    _active_sessions[captured] = {"proc": proc, "status": "active",
                                                  "writer": writer, "start": time.time()}
                    writer.set_session_id(captured)
                    if not requested_session_id and not session_created_sent:
                        session_created_sent = True
                        await writer.send(create_normalized_message(
                            kind="session_created", newSessionId=captured,
                            sessionId=captured, provider=PROVIDER))
                continue

            sid = captured or requested_session_id

            if etype == "item.completed":
                item = ev.get("item") or {}
                itype = item.get("type")
                if itype == "agent_message" and item.get("text"):
                    await writer.send(create_normalized_message(
                        kind="text", role="assistant", content=item["text"],
                        sessionId=sid, provider=PROVIDER))
                elif itype == "reasoning" and item.get("text"):
                    await writer.send(create_normalized_message(
                        kind="thinking", content=item["text"], sessionId=sid, provider=PROVIDER))
                elif itype == "command_execution":
                    tool_id = item.get("id") or "codex_cmd"
                    await writer.send(create_normalized_message(
                        kind="tool_use", toolName="shell", toolId=tool_id,
                        toolInput={"command": item.get("command")}, sessionId=sid, provider=PROVIDER))
                    await writer.send(create_normalized_message(
                        kind="tool_result", toolId=tool_id,
                        content=item.get("aggregated_output") or "",
                        isError=bool(item.get("exit_code")), sessionId=sid, provider=PROVIDER))
                continue

            if etype == "turn.completed":
                usage = ev.get("usage") or {}
                inp = int(usage.get("input_tokens") or 0)
                out = int(usage.get("output_tokens") or 0)
                await writer.send(create_normalized_message(
                    kind="status", text="token_budget", sessionId=sid, provider=PROVIDER,
                    tokenBudget={"used": inp + out, "total": _CONTEXT_WINDOW, "inputTokens": inp,
                                 "outputTokens": out, "breakdown": {"input": inp, "output": out}}))
                continue

            if etype in ("turn.failed", "error"):
                err = ev.get("error") or {}
                msg = err.get("message") if isinstance(err, dict) else (ev.get("message") or "Codex error")
                await writer.send(create_normalized_message(
                    kind="error", content=msg or ev.get("message") or "Codex error",
                    sessionId=sid, provider=PROVIDER))
                continue

        await proc.wait()
        aborted = bool(captured and _active_sessions.get(captured, {}).get("status") == "aborted")
        if captured:
            _active_sessions.pop(captured, None)
        if not aborted:
            await writer.send(create_normalized_message(
                kind="complete", exitCode=0, isNewSession=bool(not requested_session_id and command),
                sessionId=captured, provider=PROVIDER))
    except Exception as e:
        if captured:
            _active_sessions.pop(captured, None)
        await writer.send(create_normalized_message(
            kind="error", content=str(e), sessionId=captured or requested_session_id, provider=PROVIDER))
