"""Append-only audit log for Skill Studio mutations.

JSON Lines format, one event per line, flushed and fsynced on every write.
Reads are capped (tail-style) so a malicious or bloated log can't blow up
the process — we keep it simple and don't rotate here; operators can
truncate or rename the file between runs if it gets too large.

Audit events we care about (callers pass these as ``event_type``):
    skill.create / skill.update / skill.delete / skill.restore_trash
    snapshot.create / snapshot.delete / snapshot.restore
    import.github
    trash.purge

Every event carries:
    ts           ISO-8601 timestamp with microseconds
    event_type   string
    actor        username or session id (free-form, caller supplies)
    skill_name   the skill this concerns (or None for global events)
    details      dict with event-specific fields
    ok           bool — did the action succeed?
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Any

from app.core.skill_paths import AUDIT_LOG_FILE as AUDIT_LOG

logger = logging.getLogger("ivyea.services.studio_audit")


_WRITE_LOCK = threading.Lock()
_MAX_TAIL_LINES = 500    # hard cap on how many events a caller can tail
_MAX_SCAN_BYTES = 2 * 1024 * 1024  # never slurp more than 2 MB to tail


def record(
    event_type: str,
    *,
    actor: str | None = None,
    skill_name: str | None = None,
    details: dict[str, Any] | None = None,
    ok: bool = True,
) -> None:
    """Append one audit record. Never raises on I/O — auditing failure is
    logged to stderr but must not break the action we're auditing.
    """
    entry = {
        "ts": datetime.now().isoformat(),
        "event_type": event_type,
        "actor": actor,
        "skill_name": skill_name,
        "ok": ok,
        "details": details or {},
    }
    line = json.dumps(entry, ensure_ascii=False) + "\n"

    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK:
            with open(AUDIT_LOG, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                # fsync is overkill for audit but cheap enough and protects
                # against a sudden power loss eating the most recent events.
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
    except OSError as e:
        # Swallow — auditing is a side effect, not a gate.
        logger.warning("failed to write audit event %s: %s", event_type, e)


def tail(limit: int = 100) -> list[dict]:
    """Return the most recent ``limit`` events, newest first. Bad lines
    (non-JSON) are skipped silently — we'd rather lose a line than crash."""
    if limit <= 0:
        return []
    limit = min(limit, _MAX_TAIL_LINES)

    if not AUDIT_LOG.is_file():
        return []

    # Read only the tail of the file: seek to max scan window from the end.
    try:
        size = AUDIT_LOG.stat().st_size
    except OSError:
        return []
    start = max(0, size - _MAX_SCAN_BYTES)

    try:
        with open(AUDIT_LOG, "rb") as f:
            f.seek(start)
            data = f.read()
    except OSError:
        return []

    # If we didn't start at 0 we probably clipped a partial line — drop it.
    lines = data.splitlines()
    if start > 0 and lines:
        lines = lines[1:]

    events: list[dict] = []
    for raw in lines[-limit:]:
        try:
            events.append(json.loads(raw.decode("utf-8", errors="replace")))
        except json.JSONDecodeError:
            continue
    events.reverse()
    return events
