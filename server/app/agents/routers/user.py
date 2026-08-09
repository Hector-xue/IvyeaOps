"""User-scoped settings: onboarding state and git identity.

agents's Node backend stored these per user row; in platform mode we have a
single shared workspace, so we persist them in the ``app_config`` key/value
table. Git identity falls back to the system ``git config --global`` values,
matching the Node behavior of auto-populating from the system config.
"""
from __future__ import annotations

from app.core.proc import no_window_kwargs

import asyncio
import logging
import re

from fastapi import APIRouter
from pydantic import BaseModel

from app.agents.db import db_conn

logger = logging.getLogger("ivyea.agents.routers.user")

router = APIRouter()

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _config_get(key: str, default: str | None = None) -> str | None:
    with db_conn() as conn:
        row = conn.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def _config_set(key: str, value: str) -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO app_config(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


async def _git_global(field: str) -> str | None:
    """Read a `git config --global user.<field>` value, or None if unset."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "config", "--global", f"user.{field}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            **no_window_kwargs(),
        )
        out, _ = await proc.communicate()
        val = out.decode().strip()
        return val or None
    except Exception:
        return None


@router.get("/git-config")
async def get_git_config() -> dict:
    git_name = _config_get("git_name")
    git_email = _config_get("git_email")
    if not git_name and not git_email:
        git_name = await _git_global("name")
        git_email = await _git_global("email")
        if git_name:
            _config_set("git_name", git_name)
        if git_email:
            _config_set("git_email", git_email)
    return {"success": True, "gitName": git_name or None, "gitEmail": git_email or None}


class GitConfigBody(BaseModel):
    gitName: str
    gitEmail: str


@router.post("/git-config")
async def update_git_config(body: GitConfigBody) -> dict:
    if not body.gitName or not body.gitEmail:
        return {"error": "Git name and email are required"}
    if not _EMAIL_RE.match(body.gitEmail):
        return {"error": "Invalid email format"}
    _config_set("git_name", body.gitName)
    _config_set("git_email", body.gitEmail)
    for field, value in (("name", body.gitName), ("email", body.gitEmail)):
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "config", "--global", f"user.{field}", value,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                **no_window_kwargs(),
            )
            await proc.communicate()
        except Exception:
            logger.debug("proc = await asyncio.create_subprocess_exec 失败（旁路，已忽略）", exc_info=True)
    return {"success": True, "gitName": body.gitName, "gitEmail": body.gitEmail}


@router.get("/onboarding-status")
async def onboarding_status() -> dict:
    # Default to completed so we never trap the user in an onboarding flow.
    val = _config_get("onboarding_completed", "1")
    return {"success": True, "hasCompletedOnboarding": val == "1"}


@router.post("/complete-onboarding")
async def complete_onboarding() -> dict:
    _config_set("onboarding_completed", "1")
    return {"success": True, "message": "Onboarding completed successfully"}
