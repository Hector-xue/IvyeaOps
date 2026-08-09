"""Provider/session API (mounted under ``/providers``) — port of the session
routes in claudecodeui modules/providers/provider.routes.ts + sessions.service.ts.

Implemented in P1: archived list, message history (Claude JSONL), rename,
archive/delete, restore. Provider auth/models/skills/mcp and conversation
search stay on the Node service until later phases.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents import claude_sessions, hermes_driver, repos, search
from app.agents.db import db_conn

logger = logging.getLogger("ivyea.agents.routers.sessions")

router = APIRouter()

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9._-]{1,120}$")


def _parse_session_id(value: str) -> str:
    sid = (value or "").strip()
    if not _SESSION_ID_RE.match(sid):
        raise HTTPException(400, "Invalid sessionId.")
    return sid


@router.get("/sessions/archived")
async def archived_sessions() -> dict:
    with db_conn() as conn:
        rows = repos.get_archived_sessions(conn)
        cache: dict[str, Optional[object]] = {}
        sessions = []
        for s in rows:
            project_path = s["project_path"] if (s["project_path"] or "").strip() else None
            project = None
            if project_path:
                if project_path not in cache:
                    cache[project_path] = repos.get_project_by_path(conn, project_path)
                project = cache[project_path]
            custom = (project["custom_project_name"] if project else None) or ""
            display = custom.strip() or (os.path.basename(project_path) or project_path
                                         if project_path else "Unknown Project")
            sessions.append({
                "sessionId": s["session_id"],
                "provider": s["provider"],
                "projectId": project["project_id"] if project else None,
                "projectPath": project_path,
                "projectDisplayName": display,
                "sessionTitle": (s["custom_name"] or "").strip() or s["session_id"],
                "createdAt": s["created_at"],
                "updatedAt": s["updated_at"],
                "lastActivity": s["updated_at"] or s["created_at"],
                "isProjectArchived": bool(project["isArchived"]) if project else False,
            })
    return {"success": True, "data": {"sessions": sessions}}


@router.get("/sessions/{session_id}/messages")
async def session_messages(
    session_id: str,
    limit: Optional[int] = Query(None, ge=0),
    offset: int = Query(0, ge=0),
) -> dict:
    sid = _parse_session_id(session_id)
    with db_conn() as conn:
        session = repos.get_session_by_id(conn, sid)
        if not session:
            raise HTTPException(404, f'Session "{sid}" was not found.')
        provider = session["provider"]
        if provider == "claude":
            return claude_sessions.fetch_history(conn, sid, limit=limit, offset=offset)
        codex_jsonl = session["jsonl_path"] if provider == "codex" else None
    # hermes transcripts come from its own session store (read outside the DB conn).
    if provider == "hermes":
        return hermes_driver.read_history(sid)
    if provider == "ivyea":
        from app.agents import ivyea_driver
        return ivyea_driver.read_history(sid)
    if provider == "codex":
        from app.agents import codex_driver
        return codex_driver.read_history(codex_jsonl, sid)
    return {"messages": [], "total": 0, "hasMore": False, "offset": offset, "limit": limit}


class RenameSessionBody(BaseModel):
    summary: str


@router.put("/sessions/{session_id}")
async def rename_session(session_id: str, body: RenameSessionBody) -> dict:
    sid = _parse_session_id(session_id)
    summary = (body.summary or "").strip()
    if not summary:
        raise HTTPException(400, "Summary is required.")
    if len(summary) > 500:
        raise HTTPException(400, "Summary must not exceed 500 characters.")
    with db_conn() as conn:
        if not repos.get_session_by_id(conn, sid):
            raise HTTPException(404, f'Session "{sid}" was not found.')
        repos.update_session_custom_name(conn, sid, summary)
    return {"success": True, "data": {"sessionId": sid, "summary": summary}}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    force: bool = Query(False),
    deletedFromDisk: Optional[bool] = Query(None),
) -> dict:
    sid = _parse_session_id(session_id)
    delete_disk = force if deletedFromDisk is None else deletedFromDisk
    with db_conn() as conn:
        session = repos.get_session_by_id(conn, sid)
        if not session:
            raise HTTPException(404, f'Session "{sid}" was not found.')
        if not force:
            repos.update_session_is_archived(conn, sid, True)
            return {"success": True, "data": {"sessionId": sid, "action": "archived",
                                              "deletedFromDisk": False}}
        removed = False
        if delete_disk and session["jsonl_path"]:
            try:
                os.unlink(session["jsonl_path"])
                removed = True
            except FileNotFoundError:
                removed = False
            except OSError:
                removed = False
        # Hermes sessions live in hermes' own SQLite store; without purging it
        # there too, the next sync re-indexes (resurrects) the deleted session.
        if session["provider"] == "hermes":
            try:
                from app.agents import hermes_driver
                await hermes_driver.purge_session(sid)
            except Exception:
                logger.debug("hermes_driver.purge_session 失败（旁路，已忽略）", exc_info=True)
        if not repos.delete_session_by_id(conn, sid):
            raise HTTPException(404, f'Session "{sid}" was not found.')
    return {"success": True, "data": {"sessionId": sid, "action": "deleted",
                                      "deletedFromDisk": removed}}


@router.get("/search/sessions")
async def search_sessions(q: str = Query(...), limit: int = Query(50)):
    """SSE conversation search across claude transcripts. Emits `result` /
    `progress` / `done` / `error` events the sidebar's EventSource consumes."""
    query = (q or "").strip()
    if len(query) < 2:
        raise HTTPException(400, "Query must be at least 2 characters")
    safe_limit = max(1, min(int(limit), 100))

    def gen():
        try:
            for event, data in search.search_conversations(query, safe_limit):
                yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            yield "event: done\ndata: {}\n\n"
        except Exception:
            yield f"event: error\ndata: {json.dumps({'error': 'Search failed'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/sessions/{session_id}/restore")
async def restore_session(session_id: str) -> dict:
    sid = _parse_session_id(session_id)
    with db_conn() as conn:
        if not repos.get_session_by_id(conn, sid):
            raise HTTPException(404, f'Session "{sid}" was not found.')
        repos.update_session_is_archived(conn, sid, False)
    return {"success": True, "data": {"sessionId": sid, "isArchived": False}}
