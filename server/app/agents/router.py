"""Aggregate routers for the Agents native backend.

Exposes two routers that main.py mounts under ``/api/agents``:
  • ``api_router`` — all REST endpoints; mounted WITH the ops module dependency
    so access aligns with the existing "agents" board permission.
  • ``ws_router``  — WebSocket endpoints; mounted WITHOUT that dependency (they
    do their own cookie auth at handshake time, see ws.py).

The frontend's ``api.js`` rewrites ``/api/*`` → ``/api/agents/*``, so the sub-route
prefixes below reproduce the original claudecodeui paths (``/projects``,
``/providers/sessions``, ``/user``, ``/auth`` ...).
"""
from __future__ import annotations

from fastapi import APIRouter

from app.agents import ws as ws_module
from app.agents.routers import (auth, commands, core, files, git, mcp_utils, plugins, projects,
                              providers, sessions, settings, taskmaster, user)

api_router = APIRouter()
api_router.include_router(core.router, tags=["agents"])
api_router.include_router(auth.router, prefix="/auth", tags=["agents-auth"])
api_router.include_router(user.router, prefix="/user", tags=["agents-user"])
api_router.include_router(projects.router, prefix="/projects", tags=["agents-projects"])
api_router.include_router(sessions.router, prefix="/providers", tags=["agents-sessions"])
api_router.include_router(providers.router, prefix="/providers", tags=["agents-providers"])
api_router.include_router(git.router, prefix="/git", tags=["agents-git"])
api_router.include_router(taskmaster.router, prefix="/taskmaster", tags=["agents-taskmaster"])
api_router.include_router(settings.router, prefix="/settings", tags=["agents-settings"])
api_router.include_router(commands.router, prefix="/commands", tags=["agents-commands"])
api_router.include_router(mcp_utils.router, prefix="/mcp-utils", tags=["agents-mcp-utils"])
api_router.include_router(plugins.router, prefix="/plugins", tags=["agents-plugins"])
# File ops use full paths (/projects/{id}/file..., /browse-filesystem, /create-folder).
api_router.include_router(files.router, tags=["agents-files"])

ws_router = ws_module.router
