"""WebSocket endpoints for the Agents backend: chat (``/ws``) and shell
(``/shell``).

Auth: registered WITHOUT the router-level ops module dependency (HTTP-cookie
dependencies don't translate to the WS handshake), so we verify the
``ivyea_ops_session`` cookie manually at accept time via ``verify_session``.

Chat (P2): drives the claude CLI via stream-json (see claude_driver). The
``claude-command`` handler runs the turn as a background task so the receive
loop stays free to handle ``abort-session`` / ``claude-permission-response`` /
status probes concurrently — mirroring how Node's per-message handlers run
concurrently. Shell (``/shell``) remains a P5 stub.
"""
from __future__ import annotations

import asyncio
import logging
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.core.security import verify_session
from app.agents import claude_driver, codex_driver, hermes_driver, ivyea_driver
from app.agents.claude_sessions import create_normalized_message

logger = logging.getLogger("ivyea.agents.ws")

router = APIRouter()


class ChatWriter:
    """Serializes JSON events to a (swappable) WebSocket under a lock so the
    background query task and the receive loop never interleave a frame."""

    def __init__(self, ws: WebSocket):
        self._ws = ws
        self._lock = asyncio.Lock()
        self.session_id = None

    def update_ws(self, ws: WebSocket) -> None:
        self._ws = ws

    def set_session_id(self, session_id) -> None:
        self.session_id = session_id

    async def send(self, message: dict) -> None:
        try:
            async with self._lock:
                await self._ws.send_text(json.dumps(message, ensure_ascii=False, default=str))
        except Exception:
            # Client may have disconnected; the session keeps running so a
            # reconnect can swap the socket back in via reconnect_writer.
            logger.debug("self._ws.send_text 失败（旁路，已忽略）", exc_info=True)


def _authed(websocket: WebSocket) -> bool:
    token = websocket.cookies.get(settings.session_cookie_name)
    return bool(token and verify_session(token))


@router.websocket("/ws")
async def chat_ws(websocket: WebSocket) -> None:
    if not _authed(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    writer = ChatWriter(websocket)
    tasks: set[asyncio.Task] = set()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue
            await _handle_chat_message(data, writer, websocket, tasks)
    except WebSocketDisconnect:
        # Leave any in-flight claude tasks running so the user can reconnect and
        # resume streaming (check-session-status -> reconnect_writer).
        return


async def _handle_chat_message(data: dict, writer: ChatWriter, ws: WebSocket,
                               tasks: set[asyncio.Task]) -> None:
    msg_type = data.get("type")
    try:
        if msg_type == "claude-command":
            task = asyncio.create_task(
                claude_driver.query_claude(data.get("command") or "", data.get("options") or {}, writer))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            return

        if msg_type == "hermes-command":
            task = asyncio.create_task(
                hermes_driver.query_hermes(data.get("command") or "", data.get("options") or {}, writer))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            return

        if msg_type == "codex-command":
            task = asyncio.create_task(
                codex_driver.query_codex(data.get("command") or "", data.get("options") or {}, writer))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            return

        if msg_type == "ivyea-command":
            task = asyncio.create_task(
                ivyea_driver.query_ivyea(data.get("command") or "", data.get("options") or {}, writer))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            return

        if msg_type == "agent-inject":
            # 轮次跑着的时候用户又说了一句。**能真插就真插**（claude 的 stdin 本来就开着；
            # ivyea 走 --input-format stream-json 那条控制通道），插不进去就明说 —— 前端据此
            # 把这句话排到下一轮，而不是让它无声消失。
            provider = data.get("provider") or "claude"
            session_id = data.get("sessionId") if isinstance(data.get("sessionId"), str) else ""
            text = str(data.get("text") or "").strip()
            if not session_id or not text:
                await writer.send({"type": "inject-result", "accepted": False,
                                   "reason": "bad_request", "sessionId": session_id})
                return
            if provider == "ivyea":
                out = await ivyea_driver.inject(session_id, text)
            elif provider == "claude":
                out = await claude_driver.inject(session_id, text)
            else:
                # codex/hermes 是一次性进程，stdin 从开头就关着 —— 没有中途插话这回事。
                out = {"ok": True, "accepted": False, "reason": "provider_unsupported"}
            await writer.send({"type": "inject-result", "sessionId": session_id,
                               "provider": provider, "text": text, **out})
            return

        if msg_type == "abort-session":
            provider = data.get("provider") or "claude"
            session_id = data.get("sessionId") if isinstance(data.get("sessionId"), str) else ""
            if provider == "hermes":
                success = await hermes_driver.abort_session(session_id)
            elif provider == "codex":
                success = await codex_driver.abort_session(session_id)
            elif provider == "ivyea":
                success = await ivyea_driver.abort_session(session_id)
            elif provider == "claude":
                success = await claude_driver.abort_session(session_id)
            else:
                success = False
            await writer.send(create_normalized_message(
                kind="complete", exitCode=0 if success else 1, aborted=True,
                success=success, sessionId=session_id, provider=provider))
            return

        if msg_type == "claude-permission-response":
            request_id = data.get("requestId")
            if isinstance(request_id, str) and request_id:
                # 选项卡（ask_user_question）和写操作审批走同一条回传消息 —— 前端那张
                # 面板是同一个。谁认领这个 request_id 谁处理：ivyea 的问答表先认一遍，
                # 不是它的再交给 claude 的审批表。
                updated = data.get("updatedInput")
                answers = (updated or {}).get("answers") if isinstance(updated, dict) else None
                if ivyea_driver.resolve_question(request_id, answers if isinstance(answers, dict) else {}):
                    return
                claude_driver.resolve_tool_approval(request_id, {
                    "allow": bool(data.get("allow")),
                    "updatedInput": data.get("updatedInput"),
                    "message": data.get("message") if isinstance(data.get("message"), str) else None,
                    "rememberEntry": data.get("rememberEntry"),
                })
            return

        if msg_type == "check-session-status":
            provider = data.get("provider") or "claude"
            session_id = data.get("sessionId") if isinstance(data.get("sessionId"), str) else ""
            if provider == "hermes":
                is_active = hermes_driver.is_active(session_id)
            elif provider == "codex":
                is_active = codex_driver.is_active(session_id)
            elif provider == "ivyea":
                is_active = ivyea_driver.is_active(session_id)
            elif provider == "claude":
                is_active = claude_driver.is_active(session_id)
                if is_active:
                    claude_driver.reconnect_writer(session_id, ws)
            else:
                is_active = False
            await writer.send({"type": "session-status", "sessionId": session_id,
                               "provider": provider, "isProcessing": is_active})
            return

        if msg_type == "get-pending-permissions":
            session_id = data.get("sessionId") if isinstance(data.get("sessionId"), str) else ""
            if not session_id:
                return
            pending: list = []
            if claude_driver.is_active(session_id):
                pending += claude_driver.get_pending_for_session(session_id)
            if ivyea_driver.is_active(session_id):
                # 切走再切回来时，那张还没点的选项卡要能回到界面上 —— 否则轮次在那边
                # 干等五分钟，用户这边什么都看不到。
                pending += ivyea_driver.get_pending_questions(session_id)
            if pending:
                await writer.send({"type": "pending-permissions-response",
                                   "sessionId": session_id, "data": pending})
            return

        if msg_type == "get-active-sessions":
            await writer.send({"type": "active-sessions", "sessions": {
                "claude": claude_driver.get_active(), "hermes": hermes_driver.get_active(),
                "codex": codex_driver.get_active(), "ivyea": ivyea_driver.get_active(),
                "cursor": [], "gemini": [], "opencode": []}})
            return
    except Exception as e:
        await writer.send({"type": "error", "error": str(e)})


@router.websocket("/shell")
async def shell_ws(websocket: WebSocket) -> None:
    if not _authed(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        from app.agents.shell_pty import ShellConnection
    except Exception as e:  # noqa: BLE001
        # shell_pty now has a ConPTY (pywinpty) backend on Windows, so this import
        # only fails when something is genuinely broken. Without this guard the
        # socket errored out and the client reconnected in a tight loop ("屏闪").
        msg = f"终端后端不可用：{e}"
        try:
            await websocket.send_json({"type": "error", "message": msg})
        except Exception:
            logger.debug("websocket.send_json 失败（旁路，已忽略）", exc_info=True)
        # Stay open and drain input so the client doesn't see a disconnect and
        # reconnect repeatedly. Just idle until the client closes.
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            return
        return
    conn = ShellConnection(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if data.get("type"):
                await conn.handle(data)
    except WebSocketDisconnect:
        # Keep the PTY alive for reconnects; idle-kill after the timeout.
        conn.on_close()
        return
