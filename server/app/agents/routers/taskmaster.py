"""TaskMaster API (mounted at ``/taskmaster``) — port of routes/taskmaster.js.

File-based endpoints (tasks/PRD read+write, templates, detection) are real.
CLI-driving endpoints (init/add-task/parse-prd/update-task) run the
``task-master`` / ``task-master-ai`` CLI when present, and return a clear "not
installed" error otherwise (the CLI isn't installed in every environment) —
avoiding an ``npx`` auto-download hang while keeping endpoints 404-free for the
P9 cutover.
"""
from __future__ import annotations

from app.core.proc import no_window_kwargs

import asyncio
import json
import os
import re
import shutil
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agents import repos
from app.agents.db import db_conn

router = APIRouter()

_FILENAME_RE = re.compile(r"^[\w\-. ]+\.(txt|md)$")


def _project_path(project_id: str) -> str:
    with db_conn() as conn:
        row = repos.get_project_by_id(conn, project_id)
    if not row:
        raise HTTPException(404, f'Project "{project_id}" does not exist')
    return row["project_path"]


def _which(bin_name: str) -> Optional[str]:
    search = os.pathsep.join([os.path.expanduser("~/.hermes/node/bin"),
                       os.path.expanduser("~/.local/bin"), os.environ.get("PATH", "")])
    return shutil.which(bin_name, path=search)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _run_cli(bin_name: str, args: list[str], cwd: str, timeout: float = 180.0) -> dict:
    """Run a task-master CLI command, or raise 400 if the CLI isn't installed."""
    binary = _which(bin_name)
    if not binary:
        raise HTTPException(400, f"TaskMaster CLI ({bin_name}) is not installed. "
                                 f"Install with: npm i -g task-master-ai")
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, *args, cwd=cwd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, env=os.environ.copy(),
            **no_window_kwargs())
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = out.decode("utf-8", "replace")
        if proc.returncode != 0:
            raise HTTPException(500, output.strip() or f"{bin_name} failed")
        return {"output": output}
    except asyncio.TimeoutError:
        raise HTTPException(504, f"{bin_name} timed out")


# --- detection --------------------------------------------------------------

def _check_installation() -> dict:
    binary = _which("task-master")
    return {"isInstalled": bool(binary), "command": binary or None, "version": None}


@router.get("/installation-status")
async def installation_status() -> dict:
    inst = _check_installation()
    mcp = {"hasMCPServer": False, "servers": []}
    return {**inst, "mcpServer": mcp, "isReady": inst["isInstalled"] and mcp["hasMCPServer"]}


@router.get("/taskmaster-server")
async def taskmaster_server() -> dict:
    return {"hasMCPServer": False, "servers": []}


# --- tasks ------------------------------------------------------------------

def _read_tasks(project_path: str) -> dict:
    tasks_file = os.path.join(project_path, ".taskmaster", "tasks", "tasks.json")
    if not os.path.exists(tasks_file):
        return {"tasks": [], "message": "No tasks.json file found"}
    with open(tasks_file, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    current_tag = "master"
    tasks = []
    if isinstance(data, list):
        tasks = data
    elif isinstance(data, dict) and isinstance(data.get("tasks"), list):
        tasks = data["tasks"]
    elif isinstance(data, dict):
        if isinstance(data.get(current_tag), dict) and isinstance(data[current_tag].get("tasks"), list):
            tasks = data[current_tag]["tasks"]
        elif isinstance(data.get("master"), dict) and isinstance(data["master"].get("tasks"), list):
            tasks = data["master"]["tasks"]
        else:
            for k, v in data.items():
                if isinstance(v, dict) and isinstance(v.get("tasks"), list):
                    tasks, current_tag = v["tasks"], k
                    break
    transformed = [{
        "id": t.get("id"), "title": t.get("title") or "Untitled Task",
        "description": t.get("description") or "", "status": t.get("status") or "pending",
        "priority": t.get("priority") or "medium", "dependencies": t.get("dependencies") or [],
        "createdAt": t.get("createdAt") or t.get("created") or _now(),
        "updatedAt": t.get("updatedAt") or t.get("updated") or _now(),
        "details": t.get("details") or "", "testStrategy": t.get("testStrategy") or t.get("test_strategy") or "",
        "subtasks": t.get("subtasks") or [],
    } for t in tasks if isinstance(t, dict)]
    by_status = {s: len([t for t in transformed if t["status"] == s])
                 for s in ("pending", "in-progress", "done", "review", "deferred", "cancelled")}
    return {"tasks": transformed, "currentTag": current_tag,
            "totalTasks": len(transformed), "tasksByStatus": by_status}


@router.get("/tasks/{project_id}")
async def get_tasks(project_id: str) -> dict:
    project_path = _project_path(project_id)
    try:
        result = _read_tasks(project_path)
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(500, f"Failed to parse tasks file: {e}")
    return {"projectId": project_id, "projectPath": project_path, "timestamp": _now(), **result}


# --- PRD files --------------------------------------------------------------

@router.get("/prd/{project_id}")
async def list_prd(project_id: str) -> dict:
    project_path = _project_path(project_id)
    docs = os.path.join(project_path, ".taskmaster", "docs")
    if not os.path.isdir(docs):
        return {"projectId": project_id, "prdFiles": [], "message": "No .taskmaster/docs directory found"}
    files = []
    for name in os.listdir(docs):
        fp = os.path.join(docs, name)
        if os.path.isfile(fp) and (name.endswith(".txt") or name.endswith(".md")):
            st = os.stat(fp)
            files.append({"name": name, "path": os.path.relpath(fp, project_path), "size": st.st_size,
                          "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                          "created": datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_ctime), tz=timezone.utc).isoformat()})
    files.sort(key=lambda f: f["modified"], reverse=True)
    return {"projectId": project_id, "projectPath": project_path, "prdFiles": files, "timestamp": _now()}


class PrdBody(BaseModel):
    fileName: str
    content: str


@router.post("/prd/{project_id}")
async def save_prd(project_id: str, body: PrdBody) -> dict:
    if not body.fileName or not body.content:
        raise HTTPException(400, "fileName and content are required")
    if not _FILENAME_RE.match(body.fileName):
        raise HTTPException(400, "Filename must end with .txt or .md")
    project_path = _project_path(project_id)
    docs = os.path.join(project_path, ".taskmaster", "docs")
    os.makedirs(docs, exist_ok=True)
    fp = os.path.join(docs, body.fileName)
    with open(fp, "w", encoding="utf-8") as fh:
        fh.write(body.content)
    st = os.stat(fp)
    return {"projectId": project_id, "projectPath": project_path, "fileName": body.fileName,
            "filePath": os.path.relpath(fp, project_path), "size": st.st_size,
            "message": "PRD file saved successfully", "timestamp": _now()}


@router.get("/prd/{project_id}/{file_name}")
async def read_prd(project_id: str, file_name: str) -> dict:
    project_path = _project_path(project_id)
    fp = os.path.join(project_path, ".taskmaster", "docs", file_name)
    # keep within docs dir
    docs = os.path.abspath(os.path.join(project_path, ".taskmaster", "docs"))
    if not os.path.abspath(fp).startswith(docs + os.sep):
        raise HTTPException(403, "Invalid path")
    if not os.path.exists(fp):
        raise HTTPException(404, f'File "{file_name}" does not exist')
    with open(fp, "r", encoding="utf-8") as fh:
        content = fh.read()
    return {"projectId": project_id, "fileName": file_name, "content": content,
            "filePath": os.path.relpath(fp, project_path), "timestamp": _now()}


# --- templates --------------------------------------------------------------

def _templates() -> list[dict]:
    today = datetime.now(timezone.utc).date().isoformat()
    web_app = f"""# Product Requirements Document - Web Application

## Overview
**Product Name:** [Your App Name]
**Version:** 1.0
**Date:** {today}

## Executive Summary
Brief description of what this web application will do and why it's needed.

## User Stories
1. As a user, I want [feature] so I can [benefit]
2. As a user, I want [feature] so I can [benefit]

## Technical Requirements
- Frontend framework
- Backend services
- Database requirements
- Security considerations

## Success Metrics
- User engagement metrics
- Performance benchmarks
- Business objectives
"""
    cli_tool = f"""# Product Requirements Document - CLI Tool

## Overview
**Tool Name:** [Your Tool]
**Date:** {today}

## Commands
- `tool init` — ...
- `tool run` — ...

## Requirements
- Language/runtime
- Distribution (npm/pip/binary)
- Config & flags

## Success Metrics
- Adoption, reliability, performance
"""
    return [
        {"id": "web-app", "name": "Web Application", "category": "web",
         "description": "Template for web application projects", "content": web_app},
        {"id": "cli-tool", "name": "CLI Tool", "category": "cli",
         "description": "Template for command-line tools", "content": cli_tool},
    ]


@router.get("/prd-templates")
async def prd_templates() -> dict:
    return {"templates": _templates(), "timestamp": _now()}


class ApplyTemplateBody(BaseModel):
    templateId: str
    fileName: Optional[str] = None
    customizations: Optional[dict] = None


@router.post("/apply-template/{project_id}")
async def apply_template(project_id: str, body: ApplyTemplateBody) -> dict:
    template = next((t for t in _templates() if t["id"] == body.templateId), None)
    if not template:
        raise HTTPException(404, f'Template "{body.templateId}" not found')
    project_path = _project_path(project_id)
    docs = os.path.join(project_path, ".taskmaster", "docs")
    os.makedirs(docs, exist_ok=True)
    file_name = body.fileName or "prd.txt"
    if not _FILENAME_RE.match(file_name):
        raise HTTPException(400, "Filename must end with .txt or .md")
    fp = os.path.join(docs, file_name)
    with open(fp, "w", encoding="utf-8") as fh:
        fh.write(template["content"])
    return {"projectId": project_id, "fileName": file_name,
            "filePath": os.path.relpath(fp, project_path),
            "message": "Template applied successfully", "timestamp": _now()}


# --- CLI-driven endpoints (require task-master CLI) -------------------------

@router.post("/init/{project_id}")
async def init_taskmaster(project_id: str) -> dict:
    project_path = _project_path(project_id)
    result = await _run_cli("task-master", ["init"], project_path)
    return {"success": True, "output": result["output"], "message": "TaskMaster initialized"}


class AddTaskBody(BaseModel):
    prompt: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    dependencies: Optional[str] = None


@router.post("/add-task/{project_id}")
async def add_task(project_id: str, body: AddTaskBody) -> dict:
    project_path = _project_path(project_id)
    args = ["add-task"]
    if body.prompt:
        args += ["--prompt", body.prompt, "--research"]
    elif body.title:
        args += ["--prompt", f'Create a task titled "{body.title}" with description: {body.description or ""}']
    if body.priority:
        args += ["--priority", body.priority]
    if body.dependencies:
        args += ["--dependencies", body.dependencies]
    result = await _run_cli("task-master-ai", args, project_path)
    return {"success": True, "output": result["output"]}


class ParsePrdBody(BaseModel):
    fileName: str
    numTasks: Optional[int] = None
    append: Optional[bool] = False


@router.post("/parse-prd/{project_id}")
async def parse_prd(project_id: str, body: ParsePrdBody) -> dict:
    project_path = _project_path(project_id)
    prd_path = os.path.join(project_path, ".taskmaster", "docs", body.fileName)
    if not os.path.exists(prd_path):
        raise HTTPException(404, f'File "{body.fileName}" does not exist in .taskmaster/docs/')
    args = ["parse-prd", prd_path]
    if body.numTasks:
        args += ["--num-tasks", str(body.numTasks)]
    if body.append:
        args += ["--append"]
    args += ["--research"]
    result = await _run_cli("task-master-ai", args, project_path)
    return {"success": True, "prdFile": body.fileName, "output": result["output"]}


class UpdateTaskBody(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    details: Optional[str] = None


@router.put("/update-task/{project_id}/{task_id}")
async def update_task(project_id: str, task_id: str, body: UpdateTaskBody) -> dict:
    project_path = _project_path(project_id)
    fields = body.dict(exclude_none=True)
    if body.status and len(fields) == 1:
        result = await _run_cli("task-master-ai",
                                ["set-status", f"--id={task_id}", f"--status={body.status}"], project_path)
        return {"success": True, "taskId": task_id, "output": result["output"],
                "message": "Task status updated successfully"}
    updates = []
    for key in ("title", "description", "priority", "details"):
        val = getattr(body, key)
        if val:
            updates.append(f'{key}: "{val}"')
    prompt = f"Update task with the following changes: {', '.join(updates)}"
    result = await _run_cli("task-master-ai",
                            ["update-task", f"--id={task_id}", f"--prompt={prompt}"], project_path)
    return {"success": True, "taskId": task_id, "output": result["output"]}


# --- used by the projects route: GET /projects/{id}/taskmaster --------------

def get_project_taskmaster(project_id: str) -> dict:
    """Detection result for the sidebar's per-project taskmaster badge."""
    with db_conn() as conn:
        row = repos.get_project_by_id(conn, project_id)
    if not row:
        return {"hasTaskmaster": False, "metadata": None}
    project_path = row["project_path"]
    if not os.path.isdir(os.path.join(project_path, ".taskmaster")):
        return {"hasTaskmaster": False, "metadata": None}
    try:
        info = _read_tasks(project_path)
        by = info.get("tasksByStatus", {})
        return {"hasTaskmaster": True, "metadata": {
            "totalTasks": info.get("totalTasks", 0),
            "pending": by.get("pending", 0),
            "inProgress": by.get("in-progress", 0),
            "done": by.get("done", 0)}}
    except Exception:
        return {"hasTaskmaster": True, "metadata": None}
