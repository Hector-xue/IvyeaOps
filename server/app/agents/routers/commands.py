"""Slash commands API (mounted at ``/commands``) — port of routes/commands.js.

``/list`` scans built-in commands + ``.claude/commands/**.md`` (project + user)
for the chat slash-command menu; ``/execute`` loads a custom command's markdown
and substitutes ``$ARGUMENTS`` / ``$1..`` (the load-bearing path), with minimal
built-in handlers.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

_BUILTIN = [
    {"name": "/help", "description": "Show help documentation for Claude Code", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/models", "description": "View available models for the current provider", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/cost", "description": "Display token usage information", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/memory", "description": "Open CLAUDE.md memory file for editing", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/config", "description": "Open settings and configuration", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/status", "description": "Show system status and version information", "namespace": "builtin", "metadata": {"type": "builtin"}},
]


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            block = text[3:end].strip()
            body = text[end + 4:].lstrip("\n")
            data: dict = {}
            for line in block.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    data[k.strip()] = v.strip().strip("\"'")
            return data, body
    return {}, text


def _scan_commands(dir_path: str, base_dir: str, namespace: str) -> list[dict]:
    out: list[dict] = []
    if not os.path.isdir(dir_path):
        return out
    try:
        for entry in os.scandir(dir_path):
            full = os.path.join(dir_path, entry.name)
            if entry.is_dir(follow_symlinks=False):
                out.extend(_scan_commands(full, base_dir, namespace))
            elif entry.is_file() and entry.name.endswith(".md"):
                try:
                    with open(full, "r", encoding="utf-8") as fh:
                        content = fh.read()
                except OSError:
                    continue
                fm, body = _parse_frontmatter(content)
                rel = os.path.relpath(full, base_dir)
                name = "/" + re.sub(r"\.md$", "", rel).replace("\\", "/")
                desc = fm.get("description") or ""
                if not desc:
                    first = body.strip().split("\n")[0] if body.strip() else ""
                    desc = re.sub(r"^#+\s*", "", first).strip()
                out.append({"name": name, "path": full, "relativePath": rel,
                            "description": desc, "namespace": namespace, "metadata": fm})
    except OSError:
        pass
    return out


class ListBody(BaseModel):
    projectPath: Optional[str] = None


@router.post("/list")
async def list_commands(body: ListBody) -> dict:
    all_cmds = list(_BUILTIN)
    if body.projectPath:
        d = os.path.join(body.projectPath, ".claude", "commands")
        all_cmds += _scan_commands(d, d, "project")
    user_dir = os.path.join(os.path.expanduser("~"), ".claude", "commands")
    all_cmds += _scan_commands(user_dir, user_dir, "user")
    custom = sorted([c for c in all_cmds if c["namespace"] != "builtin"], key=lambda c: c["name"])
    return {"builtIn": _BUILTIN, "custom": custom, "count": len(all_cmds)}


class ExecuteBody(BaseModel):
    commandName: str
    commandPath: Optional[str] = None
    args: list = []
    context: dict = {}


@router.post("/execute")
async def execute_command(body: ExecuteBody) -> dict:
    if not body.commandName:
        raise HTTPException(400, "Command name is required")

    builtin_names = {c["name"] for c in _BUILTIN}
    if body.commandName in builtin_names:
        action = body.commandName.lstrip("/")
        return {"type": "builtin", "action": action, "command": body.commandName,
                "data": {"message": f"{body.commandName} handled by the client UI."}}

    if not body.commandPath:
        raise HTTPException(400, "Command path is required for custom commands")

    # Security: commandPath must be under ~/.claude/commands or <project>/.claude/commands
    resolved = os.path.abspath(body.commandPath)
    user_base = os.path.abspath(os.path.join(os.path.expanduser("~"), ".claude", "commands"))
    project_path = (body.context or {}).get("projectPath")
    project_base = os.path.abspath(os.path.join(project_path, ".claude", "commands")) if project_path else None

    def _under(base):
        rel = os.path.relpath(resolved, base)
        return rel != "" and not rel.startswith("..") and not os.path.isabs(rel)

    if not (_under(user_base) or (project_base and _under(project_base))):
        raise HTTPException(403, "Command must be in .claude/commands directory")

    try:
        with open(resolved, "r", encoding="utf-8") as fh:
            content = fh.read()
    except FileNotFoundError:
        raise HTTPException(404, f"Command file not found: {body.commandPath}")

    metadata, command_content = _parse_frontmatter(content)
    processed = command_content.replace("$ARGUMENTS", " ".join(str(a) for a in body.args))
    for i, arg in enumerate(body.args):
        processed = re.sub(rf"\${i + 1}\b", str(arg), processed)

    return {"type": "custom", "command": body.commandName, "content": processed,
            "metadata": metadata, "hasFileIncludes": "@" in processed,
            "hasBashCommands": "!" in processed}
