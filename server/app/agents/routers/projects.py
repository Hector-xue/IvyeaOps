"""Projects API — port of claudecodeui modules/projects/projects.routes.ts.

Reads the projects/sessions metadata tables (kept in sync with Claude's native
JSONL transcripts) and returns the provider-bucketed shape the sidebar expects.

NOTE: the Node version runs ``synchronizeSessions()`` before listing to refresh
the DB from ``~/.claude/projects``. During dogfood the still-running Node
service keeps that DB fresh, so we skip sync here; porting the synchronizer for
self-sufficiency is tracked for before P9 cutover.
"""
from __future__ import annotations

import os
import posixpath
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.agents import repos, synchronizer
from app.agents.db import db_conn

router = APIRouter()

_DEFAULT_PAGE = 20
_MAX_PAGE = 200

# Workspace-path safety (shared/utils.ts) — used only by create-project.
WORKSPACES_ROOT = os.getenv("WORKSPACES_ROOT") or str(Path.home())
_FORBIDDEN = {
    "/", "/etc", "/bin", "/sbin", "/usr", "/dev", "/proc", "/sys", "/var",
    "/boot", "/root", "/lib", "/lib64", "/opt", "/tmp", "/run",
}


def _bucket_sessions(rows) -> dict:
    buckets = {"claude": [], "cursor": [], "codex": [], "gemini": [], "opencode": [], "hermes": [], "agy": []}
    for row in rows:
        provider = row["provider"]
        if provider not in buckets:
            continue
        buckets[provider].append({
            "id": row["session_id"],
            "summary": row["custom_name"] or "",
            "messageCount": 0,
            "lastActivity": row["updated_at"] or row["created_at"] or "",
        })
    return buckets


def _clamp_page(limit: Optional[int], offset: Optional[int]) -> tuple[int, int]:
    lim = _DEFAULT_PAGE if limit is None else int(limit)
    off = 0 if offset is None else int(offset)
    return min(max(1, lim), _MAX_PAGE), max(0, off)


def _display_name(row) -> str:
    custom = (row["custom_project_name"] or "").strip()
    if custom:
        return custom
    project_path = row["project_path"]
    return repos.generate_display_name(posixpath.basename(project_path) or project_path, project_path)


def _project_item(conn, row, *, include_archived: bool) -> dict:
    project_path = row["project_path"]
    if include_archived:
        rows = repos.get_sessions_by_project_path_including_archived(conn, project_path)
        total, has_more = len(rows), False
    else:
        lim, off = _clamp_page(None, None)
        rows = repos.get_sessions_by_project_path_page(conn, project_path, lim, off)
        total = repos.count_sessions_by_project_path(conn, project_path)
        has_more = off + len(rows) < total
    buckets = _bucket_sessions(rows)
    return {
        "projectId": row["project_id"],
        "path": project_path,
        "displayName": _display_name(row),
        "fullPath": project_path,
        "isStarred": bool(row["isStarred"]),
        "sessions": buckets["claude"],
        "cursorSessions": buckets["cursor"],
        "codexSessions": buckets["codex"],
        "geminiSessions": buckets["gemini"],
        "opencodeSessions": buckets["opencode"],
        "hermesSessions": buckets["hermes"],
        "agySessions": buckets["agy"],
        "sessionMeta": {"hasMore": has_more, "total": total},
    }


def _sync() -> None:
    # Self-sufficient: refresh projects/sessions from ~/.claude before listing
    # (throttled). Best-effort — never block listing on a sync hiccup.
    try:
        synchronizer.maybe_synchronize()
    except Exception:
        pass


@router.get("")
async def list_projects() -> list:
    _sync()
    with db_conn() as conn:
        return [_project_item(conn, row, include_archived=False)
                for row in repos.get_project_paths(conn)]


@router.get("/archived")
async def list_archived_projects() -> dict:
    _sync()
    with db_conn() as conn:
        projects = [
            {**_project_item(conn, row, include_archived=True), "isArchived": True}
            for row in repos.get_archived_project_paths(conn)
        ]
    return {"success": True, "data": {"projects": projects}}


@router.get("/{project_id}/sessions")
async def project_sessions(
    project_id: str,
    limit: int = Query(_DEFAULT_PAGE, ge=0),
    offset: int = Query(0, ge=0),
) -> dict:
    with db_conn() as conn:
        row = repos.get_project_by_id(conn, project_id)
        if not row:
            raise HTTPException(404, f'Project "{project_id}" was not found.')
        lim, off = _clamp_page(limit, offset)
        srows = repos.get_sessions_by_project_path_page(conn, row["project_path"], lim, off)
        total = repos.count_sessions_by_project_path(conn, row["project_path"])
        buckets = _bucket_sessions(srows)
    return {
        "projectId": row["project_id"],
        "sessions": buckets["claude"],
        "cursorSessions": buckets["cursor"],
        "codexSessions": buckets["codex"],
        "geminiSessions": buckets["gemini"],
        "opencodeSessions": buckets["opencode"],
        "hermesSessions": buckets["hermes"],
        "agySessions": buckets["agy"],
        "sessionMeta": {"hasMore": off + len(srows) < total, "total": total},
    }


def _validate_workspace_path(requested: str) -> str:
    """Compact port of validateWorkspacePath: resolve, block system dirs, enforce
    containment under WORKSPACES_ROOT. Returns the resolved path or raises 400."""
    normalized = repos.normalize_project_path(requested)
    if not normalized:
        raise HTTPException(400, "Workspace path is required")
    absolute = repos.normalize_project_path(os.path.abspath(normalized))
    root = repos.normalize_project_path(os.path.realpath(WORKSPACES_ROOT))
    # Containment: the workspace must live inside the configured root. This is the
    # primary security boundary.
    if absolute != root and not absolute.startswith(root + "/"):
        raise HTTPException(400, f"Workspace path must be within the allowed workspace root: {WORKSPACES_ROOT}")
    if absolute == "/":
        raise HTTPException(400, "Cannot use system-critical directories as workspace locations")
    # Block system-critical dirs — but never the workspace root itself or its
    # subtree. When the server runs as root, home (== WORKSPACES_ROOT) is /root,
    # which is also listed in _FORBIDDEN; without this exemption *every* path under
    # the user's own home is rejected and project creation is impossible.
    for forbidden in _FORBIDDEN:
        if root == forbidden or root.startswith(forbidden + "/"):
            continue  # the workspace root legitimately lives at/under this dir
        if absolute == forbidden or absolute.startswith(forbidden + "/"):
            if forbidden == "/var" and (absolute.startswith("/var/tmp") or absolute.startswith("/var/folders")):
                continue
            raise HTTPException(400, f"Cannot create workspace in system directory: {forbidden}")
    return absolute


class CreateProjectBody(BaseModel):
    path: str = ""
    customName: Optional[str] = None
    workspaceType: Optional[str] = None
    githubUrl: Optional[str] = None


@router.post("/create-project")
async def create_project(body: CreateProjectBody) -> dict:
    if body.workspaceType is not None:
        raise HTTPException(400, "workspaceType is no longer supported. Use the single create-project flow.")
    if body.githubUrl:
        raise HTTPException(400, "Repository cloning is not supported on create-project")
    resolved = _validate_workspace_path(body.path or "")
    os.makedirs(resolved, exist_ok=True)
    if not os.path.isdir(resolved):
        raise HTTPException(400, "Path exists but is not a directory")
    with db_conn() as conn:
        result = repos.create_project_path(conn, resolved, body.customName)
        if result["outcome"] == "active_conflict":
            raise HTTPException(409, f"Project path already exists: {resolved}")
        row = result["project"]
        item = {
            "projectId": row["project_id"], "path": row["project_path"], "fullPath": row["project_path"],
            "displayName": _display_name(row), "customName": row["custom_project_name"],
            "isArchived": bool(row["isArchived"]), "isStarred": bool(row["isStarred"]),
            "sessions": [], "cursorSessions": [], "codexSessions": [], "geminiSessions": [],
            "opencodeSessions": [], "hermesSessions": [], "agySessions": [],
            "sessionMeta": {"hasMore": False, "total": 0},
        }
    return {
        "success": True, "project": item,
        "message": "Archived project path reused successfully"
        if result["outcome"] == "reactivated_archived" else "Project created successfully",
    }


@router.post("/deep-analysis-workspace")
async def deep_analysis_workspace() -> dict:
    """Ensure the dedicated, reusable workspace used by the market-research
    "深入分析" handoff exists, and return it.

    Idempotent: reuses the existing project (reactivating it if archived)
    instead of erroring on conflict, so every handoff lands in one tidy place
    rather than polluting the user's real repos.

    NOTE: this is a fixed, server-controlled path (a dedicated subdir of the
    service home), not user input, so it deliberately skips the
    `_validate_workspace_path` guard — that guard rejects anything under home
    on deployments where home itself is a "system" dir (e.g. /root), which
    would otherwise make this workspace impossible to create.
    """
    path = repos.normalize_project_path(os.path.join(str(Path.home()), "ivyea-deep-analysis"))
    os.makedirs(path, exist_ok=True)
    with db_conn() as conn:
        result = repos.create_project_path(conn, path, "深入分析")
        item = _project_item(conn, result["project"], include_archived=False)
    return {"success": True, "project": item}


class MigrateStarsBody(BaseModel):
    projectIds: list = []


@router.post("/migrate-legacy-stars")
async def migrate_legacy_stars(body: MigrateStarsBody) -> dict:
    seen, updated = set(), 0
    with db_conn() as conn:
        for pid in body.projectIds:
            pid = str(pid).strip()
            if not pid or pid in seen:
                continue
            seen.add(pid)
            row = repos.get_project_by_id(conn, pid)
            if row and not bool(row["isStarred"]):
                repos.update_project_is_starred_by_id(conn, pid, True)
                updated += 1
    return {"success": True, "updated": updated}


class RenameBody(BaseModel):
    displayName: Optional[str] = None


@router.put("/{project_id}/rename")
async def rename_project(project_id: str, body: RenameBody) -> dict:
    trimmed = (body.displayName or "").strip() if isinstance(body.displayName, str) else ""
    with db_conn() as conn:
        repos.update_custom_project_name_by_id(conn, project_id, trimmed or None)
    return {"success": True}


@router.get("/clone-progress")
async def clone_progress():
    """GitHub-import (clone a repo into a workspace) was retired in the native
    rewrite. Return a graceful SSE 'disabled' message instead of 404."""
    from fastapi.responses import StreamingResponse
    import json as _json

    def gen():
        yield ("data: " + _json.dumps({
            "type": "error",
            "message": "GitHub 导入已停用。请在本地 git clone 后,用「新建项目」打开该目录。",
        }) + "\n\n")

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@router.get("/{project_id}/taskmaster")
async def project_taskmaster(project_id: str) -> dict:
    from app.agents.routers import taskmaster
    return taskmaster.get_project_taskmaster(project_id)


@router.get("/{project_id}/sessions/{session_id}/token-usage")
async def token_usage(project_id: str, session_id: str, provider: str = Query("claude")) -> dict:
    """Context-window token usage from the latest assistant message of a
    session transcript. Port of the claude branch in index.js token-usage."""
    import json as _json
    if provider != "claude":
        return {"used": 0, "total": 0, "inputTokens": 0, "outputTokens": 0,
                "breakdown": {"input": 0, "output": 0}, "unsupported": True,
                "message": f"Token usage tracking not available for {provider} sessions"}
    with db_conn() as conn:
        session = repos.get_session_by_id(conn, session_id)
    jsonl_path = session["jsonl_path"] if session else None
    if not jsonl_path or not os.path.exists(jsonl_path):
        raise HTTPException(404, "Session file not found")
    context_window = int(os.getenv("CONTEXT_WINDOW", "160000") or "160000")
    input_tokens = output_tokens = 0
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = _json.loads(line)
            except (ValueError, TypeError):
                continue
            if entry.get("type") == "assistant" and isinstance(entry.get("message"), dict):
                usage = entry["message"].get("usage")
                if isinstance(usage, dict):
                    input_tokens = int(usage.get("input_tokens") or 0)
                    output_tokens = int(usage.get("output_tokens") or 0)
                    break
    except OSError:
        raise HTTPException(404, "Session file not found")
    return {"used": input_tokens + output_tokens, "total": context_window,
            "inputTokens": input_tokens, "outputTokens": output_tokens,
            "breakdown": {"input": input_tokens, "output": output_tokens}}


@router.post("/{project_id}/toggle-star")
async def toggle_star(project_id: str) -> dict:
    with db_conn() as conn:
        row = repos.get_project_by_id(conn, project_id)
        if not row:
            raise HTTPException(404, "Project not found")
        next_state = not bool(row["isStarred"])
        repos.update_project_is_starred_by_id(conn, project_id, next_state)
    return {"success": True, "isStarred": next_state}


@router.post("/{project_id}/restore")
async def restore_project(project_id: str) -> dict:
    with db_conn() as conn:
        row = repos.get_project_by_id(conn, project_id)
        if not row:
            raise HTTPException(404, f"Unknown projectId: {project_id}")
        repos.update_project_is_archived_by_id(conn, project_id, False)
    return {"success": True, "data": {"projectId": project_id, "isArchived": False}}


@router.delete("/{project_id}")
async def delete_project(project_id: str, force: bool = Query(False)) -> dict:
    with db_conn() as conn:
        row = repos.get_project_by_id(conn, project_id)
        if not row:
            raise HTTPException(404, f"Unknown projectId: {project_id}")
        if not force:
            repos.update_project_is_archived_by_id(conn, project_id, True)
            return {"success": True}
        # Force: remove transcript files, then session rows + project row.
        for s in repos.get_sessions_by_project_path_including_archived(conn, row["project_path"]):
            jp = (s["jsonl_path"] or "").strip()
            if jp:
                try:
                    os.unlink(jp if os.path.isabs(jp) else os.path.abspath(jp))
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
        repos.delete_sessions_by_project_path(conn, row["project_path"])
        repos.delete_project_by_id(conn, project_id)
    return {"success": True}
