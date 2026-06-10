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
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.core.security import verify_session
from app.agents import claude_driver, codex_driver, hermes_driver
from app.agents.claude_sessions import create_normalized_message

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
            pass


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

        if msg_type == "abort-session":
            provider = data.get("provider") or "claude"
            session_id = data.get("sessionId") if isinstance(data.get("sessionId"), str) else ""
            if provider == "hermes":
                success = await hermes_driver.abort_session(session_id)
            elif provider == "codex":
                success = await codex_driver.abort_session(session_id)
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
            if session_id and claude_driver.is_active(session_id):
                await writer.send({"type": "pending-permissions-response", "sessionId": session_id,
                                   "data": claude_driver.get_pending_for_session(session_id)})
            return

        if msg_type == "get-active-sessions":
            await writer.send({"type": "active-sessions", "sessions": {
                "claude": claude_driver.get_active(), "hermes": hermes_driver.get_active(),
                "codex": codex_driver.get_active(),
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
        # The PTY backend depends on Unix-only modules (fcntl/pty/termios). On
        # Windows the import fails; without this guard the socket errored out and
        # the client reconnected in a tight loop — the terminal "屏闪" symptom.
        import sys
        msg = ("内置终端在 Windows 上不可用（不支持 PTY 多终端会话）。其余功能正常。"
               if sys.platform == "win32" else f"终端后端不可用：{e}")
        try:
            await websocket.send_json({"type": "error", "message": msg})
        except Exception:
            pass
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
