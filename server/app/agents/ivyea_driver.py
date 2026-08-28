"""Drive the ivyea CLI (`ivyea chat -p --output-format stream-json`) for agents chat.

ivyea-agent ≥1.2.0 emits Claude Code-aligned NDJSON on stdout:
  {"type":"system","subtype":"init","session_id",...}        -> session_created
  {"type":"assistant","message":{content:[text|tool_use]}}   -> text / tool_use
  {"type":"user","message":{content:[tool_result]}}          -> tool_result
  {"type":"result","result","usage","total_cost_cny",...}    -> token budget, then complete

每轮一个进程 + `--resume <session_id>` 续接原生会话（~/.ivyea/sessions/）。

**stdin 是开着的**（ivyea-agent ≥ v1.16.1 的 `--input-format stream-json`）：那条
控制通道让轮次跑着的时候还能从这边走回去 —— 追加指令、回答选项卡、优雅中止。
没有它的话（老 agent）三件事都做不成，而且"停止"只能 SIGTERM 掉整个进程，
**这一轮跑出来的东西一个字都不会落盘**。所以这里先探一次能力，探不到就退回老行为。

  ops → 进程   {"type":"user_input"|"control_response"|"interrupt", …}
  进程 → ops   {"type":"control_request","request":{"subtype":"ask_user_question",…}}
               {"type":"injected", …}                追加指令真的插进去了
               {"type":"result","subtype":"cancelled"} 被叫停，且已落盘
"""
from __future__ import annotations

from app.core.proc import no_window_kwargs

import asyncio
import logging
import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.agents.claude_sessions import create_normalized_message, generate_message_id

logger = logging.getLogger("ivyea.agents.ivyea_driver")

PROVIDER = "ivyea"
_active_sessions: dict[str, dict] = {}
#: 正等着用户点的选项卡（request_id → {future, sessionId, input}）。
#: 和 claude_driver 的 _pending_approvals 同一套路数：轮次那边阻塞在 future 上，
#: 决策由 ws 层从另一条协程回来 set_result。
_pending_questions: dict[str, dict] = {}
_CONTEXT_WINDOW = 128000   # deepseek-chat 主脑的上下文规模（token budget 进度条用）


def _ivyea_bin() -> str:
    search = os.pathsep.join([
        os.path.expanduser("~/.ivyea/bin"),
        os.path.expanduser("~/.local/bin"),
        os.environ.get("PATH", ""),
    ])
    return shutil.which("ivyea", path=search) or "ivyea"


#: `ivyea chat` 支不支持 `--input-format stream-json`。探一次记住 —— 每轮都探一次
#: 等于每轮多起一个进程；而这个能力在服务活着的期间不会变（换版本要重启 ops）。
_SUPPORTS_STDIN: Optional[bool] = None


async def _supports_stdin_channel() -> bool:
    """能不能往这个 ivyea 里说话（`--input-format stream-json`）。

    用 `chat --help` 探而不是比版本号：版本字符串的形状变过好几次，而"帮助里有没有
    这个开关"就是要问的那件事本身。探不出来一律当**不支持**，退回老行为（stdin 关着），
    绝不因为探测失败把整轮搞挂。
    """
    global _SUPPORTS_STDIN
    if _SUPPORTS_STDIN is not None:
        return _SUPPORTS_STDIN
    # 环境变量优先：测试和"我就想关掉它"都靠它，不必去猜一个进程的输出。
    pinned = os.environ.get("IVYEA_AGENT_STDIN_CHANNEL", "").strip().lower()
    if pinned in ("0", "false", "off", "no"):
        _SUPPORTS_STDIN = False
        return False
    if pinned in ("1", "true", "on", "yes"):
        _SUPPORTS_STDIN = True
        return True
    if not shutil.which("ivyea", path=os.pathsep.join([
            os.path.expanduser("~/.ivyea/bin"), os.path.expanduser("~/.local/bin"),
            os.environ.get("PATH", "")])):
        _SUPPORTS_STDIN = False        # 装都没装，别去 spawn 一个不存在的东西
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            _ivyea_bin(), "chat", "--help", stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env=_proc_env(), **no_window_kwargs())
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
        _SUPPORTS_STDIN = b"--input-format" in (out or b"")
    except Exception:  # noqa: BLE001
        logger.debug("探测 --input-format 失败（按不支持处理）", exc_info=True)
        _SUPPORTS_STDIN = False
    return _SUPPORTS_STDIN


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


def _fmt_duration(ms: int) -> str:
    sec = ms / 1000
    if sec < 60:
        return f"{sec:.1f} 秒"
    minutes = int(sec // 60)
    rest = int(round(sec - minutes * 60))
    if minutes < 60:
        return f"{minutes} 分 {rest} 秒"
    return f"{minutes // 60} 小时 {minutes % 60} 分"


def read_history(session_id: str) -> dict:
    """把 ivyea 的会话存档（~/.ivyea/sessions/<id>.json）读成 agents 的消息形状。

    **时间戳取存档里的逐轮时刻表**（agent ≥ v1.16.0 的 `turn_times`），不是读盘那一刻
    的 now()：以前每条消息都盖 now()，于是打开一条三天前的会话，每句话都显示"刚刚"——
    一个看着有效、其实全错的时间。没有时刻表（老存档）就不给时间戳，让界面自己空着。

    另外每轮末尾补一条"结束于 … · 用时 …"的提示行 —— 和直播时收尾那条是同一句话，
    这样刷新前后看到的是同一个东西，而不是刷新之后凭空少了一行。
    """
    from datetime import datetime, timezone
    empty = {"messages": [], "total": 0, "hasMore": False, "offset": 0, "limit": None}
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "_-")
    if not safe:
        return empty
    path = os.path.join(os.path.expanduser("~/.ivyea/sessions"), f"{safe}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return empty
    raw = data.get("messages") or []
    times = {int(t.get("turn", -1)): t for t in (data.get("turn_times") or [])
             if isinstance(t, dict)}

    def _stamp(epoch: float) -> Optional[str]:
        if not epoch:
            return None
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()

    def _clock_line(turn_no: int) -> Optional[dict]:
        """这一轮的收尾行。没有时刻表就不画 —— 不编时间。"""
        row = times.get(turn_no)
        if not row or not row.get("ended_at"):
            return None
        ended = datetime.fromtimestamp(float(row["ended_at"])).astimezone()
        ms = int(row.get("ms") or 0)
        tail = f" · 用时 {_fmt_duration(ms)}" if ms > 0 else ""
        return create_normalized_message(
            kind="task_notification", sessionId=session_id, provider=PROVIDER,
            summary=f"结束于 {ended.strftime('%H:%M')}{tail}", status="completed",
            timestamp=_stamp(row["ended_at"]))

    out: list[dict] = []
    turn_no = -1
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
        if role == "user":
            if turn_no >= 0:
                line = _clock_line(turn_no)       # 上一轮的收尾行
                if line:
                    out.append(line)
            turn_no += 1
        row = times.get(turn_no) or {}
        stamp = _stamp(row.get("started_at") if role == "user" else row.get("ended_at"))
        out.append(create_normalized_message(
            kind="text", role=role, content=content, sessionId=session_id, provider=PROVIDER,
            **({"timestamp": stamp} if stamp else {})))
    if turn_no >= 0:
        line = _clock_line(turn_no)               # 最后一轮
        if line:
            out.append(line)
    return {"messages": out, "total": len(out), "hasMore": False, "offset": 0, "limit": None}


async def abort_session(session_id: str) -> bool:
    """停这一轮。**先好好说，说不通再动手。**

    老做法是直接 SIGTERM：进程当场死，`_persist()` 根本来不及跑 —— 这一轮已经读过的
    文件、已经吐出来的正文、时间账，全部凭空消失，而用户以为自己只是"点了停止"。
    现在先递一条 interrupt，让它在下一个安全点自己收摊、落盘、回一条 cancelled；
    只有它 8 秒内没走（卡在一个长工具里）才 terminate 兜底。
    """
    s = _active_sessions.get(session_id)
    if not s:
        return False
    s["status"] = "aborted"
    # 还挂着的选项卡先放掉，免得轮次卡在一个没人会点的问题上出不来
    for rid, entry in list(_pending_questions.items()):
        if entry.get("sessionId") == session_id and not entry["future"].done():
            entry["future"].set_result({})
    proc = s.get("proc")
    if proc is None:
        _active_sessions.pop(session_id, None)
        return True
    try:
        if s.get("stdin_channel") and proc.returncode is None:
            if await _write_stdin(proc, {"type": "interrupt"}):
                try:
                    await asyncio.wait_for(proc.wait(), timeout=8)
                except asyncio.TimeoutError:
                    logger.info("ivyea 没在 8 秒内自行收摊，改用 terminate")
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                proc.kill()
    except (ProcessLookupError, Exception):
        logger.debug("中止 ivyea 进程失败（旁路，已忽略）", exc_info=True)
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


def _build_argv(command: str, options: dict, stdin_channel: bool = False) -> list[str]:
    argv = [_ivyea_bin(), "chat", "-p", command or "", "--output-format", "stream-json"]
    if stdin_channel:
        argv += ["--input-format", "stream-json"]
    session_id = options.get("sessionId")
    if session_id:
        argv += ["--resume", str(session_id)]
    argv += _permission_args(options)
    return argv


async def _write_stdin(proc, obj: dict) -> bool:
    """往轮次进程里说一句话。写不进去（老 agent 关着 stdin / 进程已退）就回 False。"""
    stdin = getattr(proc, "stdin", None)
    if stdin is None or stdin.is_closing():
        return False
    try:
        stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        await stdin.drain()
        return True
    except Exception:  # noqa: BLE001 —— 进程刚好退了：当没送到，调用方据此安排
        logger.debug("写 ivyea stdin 失败（旁路，已忽略）", exc_info=True)
        return False


async def inject(session_id: str, text: str) -> dict:
    """把一条追加指令送进**正在跑的那一轮**。

    没有活轮、或这个 agent 版本没有输入通道时明确回 accepted=False —— 调用方据此
    把这句话当成下一轮发出去。含糊的成功比失败更糟：用户会以为说过的话进去了。
    """
    s = _active_sessions.get(session_id)
    if not s or s.get("status") != "active":
        return {"ok": True, "accepted": False, "reason": "no_live_turn"}
    if not s.get("stdin_channel"):
        return {"ok": True, "accepted": False, "reason": "no_input_channel"}
    item_id = uuid.uuid4().hex[:12]
    sent = await _write_stdin(s.get("proc"), {"type": "user_input", "id": item_id, "text": text})
    if not sent:
        return {"ok": True, "accepted": False, "reason": "write_failed"}
    return {"ok": True, "accepted": True, "item": {"id": item_id, "text": text}}


def resolve_question(request_id: str, answers: dict) -> bool:
    """回送一张选项卡的答案（由 ws 层在收到前端决策时调用）。"""
    entry = _pending_questions.get(request_id)
    if not entry or entry["future"].done():
        return False
    entry["future"].set_result(answers or {})
    return True


def get_pending_questions(session_id: str) -> list:
    return [{"requestId": rid, "toolName": "AskUserQuestion", "input": e.get("input"),
             "sessionId": session_id, "receivedAt": e.get("receivedAt")}
            for rid, e in _pending_questions.items() if e.get("sessionId") == session_id]


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


async def _handle_question(ev: dict, proc, writer, sid: Optional[str]) -> None:
    """把一张选项卡转给前端，等用户点，再把答案写回进程的 stdin。

    等不到答案（超时/断开）时**什么都不写**：进程那边自己有五分钟的表，到点会按
    推荐项继续 —— 两边各守各的那一半，谁也不假设对方一定会回。
    """
    req = ev.get("request") or {}
    request_id = str(ev.get("request_id") or "")
    if req.get("subtype") == "ask_user_question_timeout":
        await writer.send(create_normalized_message(
            kind="permission_cancelled", requestId=request_id, reason="timeout",
            sessionId=sid, provider=PROVIDER))
        _pending_questions.pop(request_id, None)
        return
    if req.get("subtype") != "ask_user_question" or not request_id:
        return

    questions = req.get("questions") or []
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _pending_questions[request_id] = {
        "future": fut, "sessionId": sid, "input": {"questions": questions},
        "receivedAt": datetime.now(timezone.utc).isoformat()}
    await writer.send(create_normalized_message(
        kind="permission_request", requestId=request_id, toolName="AskUserQuestion",
        input={"questions": questions}, sessionId=sid, provider=PROVIDER))
    try:
        timeout = float(req.get("timeout_s") or 300)
        answers = await asyncio.wait_for(fut, timeout=timeout + 5)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        answers = None
    finally:
        _pending_questions.pop(request_id, None)
    if answers:
        await _write_stdin(proc, {"type": "control_response", "request_id": request_id,
                                  "response": {"answers": answers}})


async def query_ivyea(command: str, options: dict, writer) -> None:
    options = options or {}
    requested_session_id = options.get("sessionId")
    captured = requested_session_id
    cwd = options.get("cwd") or os.path.expanduser("~")
    if not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")

    # 这个 agent 认不认输入通道。认 → stdin 开着（能插话、能弹选项卡、能优雅停）；
    # 不认 → 逐字退回老行为，一个字节都不多传。
    stdin_channel = await _supports_stdin_channel()
    try:
        proc = await asyncio.create_subprocess_exec(
            *_build_argv(command, options, stdin_channel),
            stdin=(asyncio.subprocess.PIPE if stdin_channel else asyncio.subprocess.DEVNULL),
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
        _active_sessions[captured] = {"proc": proc, "status": "active", "writer": writer,
                                      "start": time.time(), "stdin_channel": stdin_channel}

    session_created_sent = False
    saw_result = False
    turn_ms = 0
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
                                                  "writer": writer, "start": time.time(),
                                                  "stdin_channel": stdin_channel}
                    writer.set_session_id(captured)
                    if not requested_session_id and not session_created_sent:
                        session_created_sent = True
                        await writer.send(create_normalized_message(
                            kind="session_created", newSessionId=captured,
                            sessionId=captured, provider=PROVIDER))
                continue

            sid = captured or requested_session_id

            if etype == "control_request":
                # 模型拿不准，把选项弹给用户。形状复用 claude 那套 permission_request
                # （toolName=AskUserQuestion）—— 前端那张选项卡面板一行都不用改。
                await _handle_question(ev, proc, writer, sid)
                continue

            if etype == "injected":
                # 追加指令真的插进这一轮了。前端据此销账：没收到回执的，本轮结束后补发。
                await writer.send(create_normalized_message(
                    kind="injected", sessionId=sid, provider=PROVIDER,
                    injectId=str(ev.get("id") or ""), content=str(ev.get("text") or "")))
                continue

            if etype in ("assistant", "user"):
                for m in _translate(ev, sid):
                    await writer.send(m)
                continue

            if etype == "result":
                saw_result = True
                turn_ms = int(ev.get("duration_ms") or 0)
                if ev.get("cancelled") or ev.get("subtype") == "cancelled":
                    # 被叫停 —— **正常结局**，而且已经落盘了。不能画成红色的失败。
                    await writer.send(create_normalized_message(
                        kind="cancelled", sessionId=sid, provider=PROVIDER,
                        content=str(ev.get("result") or "")))
                    continue
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
        # 这一轮的时刻表跟着收尾一起给。**时间必须是服务端的事实**：客户端自己掐的表
        # 在断链/换标签页之后就没了，而收尾时前端还会重新拉一次存档把内存里的数冲掉。
        await writer.send(create_normalized_message(
            kind="complete", exitCode=rc, isNewSession=bool(not requested_session_id and command),
            sessionId=captured, provider=PROVIDER,
            durationMs=turn_ms or None, endedAt=int(time.time() * 1000)))
    except Exception as e:
        if captured:
            _active_sessions.pop(captured, None)
        await writer.send(create_normalized_message(
            kind="error", content=str(e), sessionId=captured or requested_session_id, provider=PROVIDER))
