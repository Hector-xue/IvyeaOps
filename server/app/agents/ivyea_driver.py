"""Drive the ivyea CLI (`ivyea chat -p --output-format stream-json`) for agents chat.

ivyea-agent ≥1.2.0 emits Claude Code-aligned NDJSON on stdout:
  {"type":"system","subtype":"init","session_id",...}        -> session_created
  {"type":"assistant","message":{content:[text|tool_use]}}   -> text / tool_use
  {"type":"user","message":{content:[tool_result]}}          -> tool_result
  {"type":"result","result","usage","total_cost_cny",...}    -> token budget, then complete

Unlike claude there is no `--input-format stream-json` resident process: each turn
spawns one process (stdin closed) and `--resume <session_id>` continues the native
ivyea session (~/.ivyea/sessions/). So this driver mirrors codex_driver.py, not the
interactive-permission claude_driver.py.
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

from app.agents.claude_sessions import create_normalized_message, generate_message_id

logger = logging.getLogger("ivyea.agents.ivyea_driver")

PROVIDER = "ivyea"
_active_sessions: dict[str, dict] = {}
_CONTEXT_WINDOW = 128000   # deepseek-chat 主脑的上下文规模（token budget 进度条用）


def _ivyea_bin() -> str:
    search = os.pathsep.join([
        os.path.expanduser("~/.ivyea/bin"),
        os.path.expanduser("~/.local/bin"),
        os.environ.get("PATH", ""),
    ])
    return shutil.which("ivyea", path=search) or "ivyea"


def _proc_env() -> dict:
    env = os.environ.copy()
    env.setdefault("HOME", os.path.expanduser("~"))
    env.setdefault("NO_COLOR", "1")
    return env


def is_active(session_id: str) -> bool:
    s = _active_sessions.get(session_id)
    return bool(s and s.get("status") == "active")


def get_active() -> list[str]:
    return list(_active_sessions.keys())


def read_history(session_id: str) -> dict:
    """Read an ivyea session transcript (~/.ivyea/sessions/<id>.json) into the
    agents message shape, so clicking a history session loads its conversation.
    Empty result if the file is missing/unreadable (mirrors hermes_driver)."""
    from datetime import datetime, timezone
    empty = {"messages": [], "total": 0, "hasMore": False, "offset": 0, "limit": None}
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "_-")
    if not safe:
        return empty
    path = os.path.join(os.path.expanduser("~/.ivyea/sessions"), f"{safe}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            raw = (json.load(fh).get("messages") or [])
    except (OSError, ValueError):
        return empty
    out: list[dict] = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant"):     # 跳过 system 人设 / tool 事件
            continue
        content = m.get("content")
        if isinstance(content, list):
            content = "\n".join(
                p.get("text", "") for p in content
                if isinstance(p, dict) and isinstance(p.get("text"), str))
        if not isinstance(content, str) or not content.strip():
            continue
        out.append(create_normalized_message(
            kind="text", role=role, content=content, sessionId=session_id, provider=PROVIDER,
            timestamp=datetime.now(timezone.utc).isoformat()))
    return {"messages": out, "total": len(out), "hasMore": False, "offset": 0, "limit": None}


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


def _permission_args(options: dict) -> list[str]:
    """UI 权限档 → ivyea 无人值守审批参数。

    bypass/skip/acceptEdits → --approve-all（全放行）；
    其余（default/plan）→ --permission-mode policy：按 ~/.ivyea/policy.json 判定，
    单工具拒绝不终止整轮（default 档在非 tty 下首个写工具会终止全轮，不适合对话）。"""
    tools = options.get("toolsSettings") or {}
    mode = options.get("permissionMode") or ""
    if tools.get("skipPermissions") or mode in ("bypassPermissions", "acceptEdits"):
        return ["--approve-all"]
    return ["--permission-mode", "policy"]


def _build_argv(command: str, options: dict) -> list[str]:
    argv = [_ivyea_bin(), "chat", "-p", command or "", "--output-format", "stream-json"]
    session_id = options.get("sessionId")
    if session_id:
        argv += ["--resume", str(session_id)]
    argv += _permission_args(options)
    return argv


def _translate(ev: dict, sid: Optional[str]) -> list[dict]:
    """一条 ivyea NDJSON 事件 → 归一消息列表（同 claude 的 kind schema，前端零改动渲染）。"""
    out: list[dict] = []
    base_id = generate_message_id(PROVIDER)
    message = ev.get("message") or {}
    content = message.get("content") or []
    if ev.get("type") == "assistant":
        for i, part in enumerate(content):
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and part.get("text"):
                out.append(create_normalized_message(
                    id=f"{base_id}_{i}", sessionId=sid, provider=PROVIDER,
                    kind="text", role="assistant", content=part["text"]))
            elif part.get("type") == "tool_use":
                out.append(create_normalized_message(
                    id=f"{base_id}_{i}", sessionId=sid, provider=PROVIDER,
                    kind="tool_use", toolName=part.get("name"),
                    toolInput=part.get("input"), toolId=part.get("id")))
    elif ev.get("type") == "user":
        for part in content:
            if isinstance(part, dict) and part.get("type") == "tool_result":
                c = part.get("content")
                out.append(create_normalized_message(
                    id=f"{base_id}_tr_{part.get('tool_use_id')}", sessionId=sid,
                    provider=PROVIDER, kind="tool_result", toolId=part.get("tool_use_id"),
                    content=c if isinstance(c, str) else json.dumps(c, ensure_ascii=False),
                    isError=bool(part.get("is_error"))))
    return out


async def query_ivyea(command: str, options: dict, writer) -> None:
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
            kind="error", content="IvyeaAgent CLI (ivyea) is not installed.",
            sessionId=captured, provider=PROVIDER))
        return
    except Exception as e:
        await writer.send(create_normalized_message(
            kind="error", content=str(e), sessionId=captured, provider=PROVIDER))
        return

    if captured:
        _active_sessions[captured] = {"proc": proc, "status": "active", "writer": writer, "start": time.time()}

    session_created_sent = False
    saw_result = False
    try:
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = ev.get("type")

            if etype == "system" and ev.get("subtype") == "init":
                sid_new = ev.get("session_id")
                if sid_new and not captured:
                    captured = sid_new
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

            if etype in ("assistant", "user"):
                for m in _translate(ev, sid):
                    await writer.send(m)
                continue

            if etype == "result":
                saw_result = True
                usage = ev.get("usage") or {}
                inp = int(usage.get("input_tokens") or 0)
                outp = int(usage.get("output_tokens") or 0)
                await writer.send(create_normalized_message(
                    kind="status", text="token_budget", sessionId=sid, provider=PROVIDER,
                    tokenBudget={"used": inp + outp, "total": _CONTEXT_WINDOW, "inputTokens": inp,
                                 "outputTokens": outp,
                                 "breakdown": {"input": inp, "output": outp}},
                    costCny=ev.get("total_cost_cny")))
                if ev.get("is_error") and ev.get("result"):
                    await writer.send(create_normalized_message(
                        kind="error", content=str(ev.get("result")), sessionId=sid, provider=PROVIDER))
                continue

        rc = await proc.wait()
        aborted = bool(captured and _active_sessions.get(captured, {}).get("status") == "aborted")
        if captured:
            _active_sessions.pop(captured, None)
        if aborted:
            return
        if rc != 0 and not saw_result:
            await writer.send(create_normalized_message(
                kind="error", content=f"ivyea exited with code {rc}（可能是主脑 key 未配置，"
                                      "在服务器上运行 `ivyea config` 检查）",
                sessionId=captured or requested_session_id, provider=PROVIDER))
        await writer.send(create_normalized_message(
            kind="complete", exitCode=rc, isNewSession=bool(not requested_session_id and command),
            sessionId=captured, provider=PROVIDER))
    except Exception as e:
        if captured:
            _active_sessions.pop(captured, None)
        await writer.send(create_normalized_message(
            kind="error", content=str(e), sessionId=captured or requested_session_id, provider=PROVIDER))
