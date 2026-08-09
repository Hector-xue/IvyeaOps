"""Application version helpers."""
from __future__ import annotations

import subprocess
import logging
import sys
from pathlib import Path

logger = logging.getLogger("ivyea.core.version")


def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


_CACHED: str | None = None


def _git_tag(root: Path) -> str:
    """Latest tag reachable from HEAD on a git checkout, or '' if unavailable."""
    if not (root / ".git").exists():
        return ""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "describe", "--tags", "--abbrev=0"],
            capture_output=True, text=True, timeout=3,
        )
        return (out.stdout or "").strip()
    except Exception:
        return ""


def app_version() -> str:
    """Reported version.

    On a git checkout, the latest *tag* is the source of truth — the release
    workflow overwrites the VERSION file at build time but never commits it back,
    so a stale VERSION file used to make a freshly-pulled server think it was
    behind. Frozen bundles have no .git, so they fall back to the baked VERSION
    file (which the release jobs DO set to the tag).

    Git checkout: **不缓存**——每次读 git tag。开发机在本机 commit+tag 发版后，运行中的
    服务下次检测就能反映最新 tag，无需重启（否则 _CACHED 停在启动时的旧版本，明明是最新代码
    却一直误报"有更新"变红）。git describe 很轻(~10ms)。frozen/VERSION 路径仍缓存。"""
    root = runtime_root()
    if not getattr(sys, "frozen", False):
        tag = _git_tag(root)
        if tag:
            return tag
    global _CACHED
    if _CACHED is not None:
        return _CACHED
    try:
        value = (root / "VERSION").read_text(encoding="utf-8").strip()
        if value:
            _CACHED = value
            return value
    except Exception:
        logger.debug("value = 失败（旁路，已忽略）", exc_info=True)
    _CACHED = "dev"
    return _CACHED
