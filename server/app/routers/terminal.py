"""Terminal session history API — capture and query tmux/bash history."""
from __future__ import annotations

import asyncio
import json
import hashlib
import logging
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

_WINDOWS = sys.platform == "win32"

from fastapi import APIRouter, Depends, Query, UploadFile, File, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.core.config import settings
from app.core.security import require_user, verify_session
from app.services import terminal_live_service as live_svc
from app.services.terminal_live_manager import manager as live_manager

router = APIRouter()
logger = logging.getLogger(__name__)

_DB_PATH = settings.data_dir / "terminal_history.sqlite3"
_UPLOAD_DIR = Path.home() / "saved-images"
_LEGACY_TTYD_SERVICE = "ttyd"


def _legacy_ttyd_url() -> str:
    """User-configurable URL for the external ttyd window. Empty until set."""
    from app.core import hub_settings as _hs
    return (_hs.get("terminal_url") or "").strip()


def _legacy_ttyd_status() -> dict:
    active = subprocess.run(
        ["systemctl", "is-active", f"{_LEGACY_TTYD_SERVICE}.service"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    show = subprocess.run(
        ["systemctl", "show", f"{_LEGACY_TTYD_SERVICE}.service", "--property=ActiveState", "--property=SubState"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    status = active.stdout.strip() or active.stderr.strip() or "unknown"
    active_state = status
    substate = "unknown"
    if show.returncode == 0:
        for line in show.stdout.splitlines():
            if line.startswith("ActiveState="):
                active_state = line.split("=", 1)[1].strip() or active_state
            elif line.startswith("SubState="):
                substate = line.split("=", 1)[1].strip() or substate
    return {
        "service": _LEGACY_TTYD_SERVICE,
        "active": active_state == "active",
        "status": active_state,
        "substate": substate,
        "url": _legacy_ttyd_url(),
    }


def _legacy_ttyd_action(action: str) -> dict:
    subprocess.run(
        ["systemctl", action, f"{_LEGACY_TTYD_SERVICE}.service"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return _legacy_ttyd_status()


# ---------------------------------------------------------------------------
# Sensitive-data redaction
# ---------------------------------------------------------------------------
# Applied to every captured session before persistence. Patterns target
# common credential shapes; tune as needed. Unmatched data passes through
# untouched.
_REDACTIONS: list[tuple[re.Pattern[str], object]] = [
    # Multi-line PEM-style private keys (capture the whole block).
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    # AWS access key id.
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_AK]"),
    # GitHub fine-grained / classic PAT.
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED_GH_TOKEN]"),
    # OpenAI-style secret keys (sk-…).
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"), "[REDACTED_SK]"),
    # Generic Bearer tokens in Authorization-style headers.
    (
        re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}"),
        "Bearer [REDACTED]",
    ),
    # JWT (three base64url segments).
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
        "[REDACTED_JWT]",
    ),
    # key=value / key: value where key looks credential-ish.
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|auth)\b"
            r"(\s*[:=]\s*)"
            r"(['\"]?)([^\s'\"`,;]{6,})\3"
        ),
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}[REDACTED]{m.group(3)}",
    ),
    # Mainland China mobile phone numbers (11-digit, starting 1[3-9]).
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[REDACTED_PHONE]"),
    # Mainland China resident ID (18 digits, last may be X).
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[REDACTED_IDCARD]"),
]


def _sanitize(text: str) -> str:
    """Redact common credential / PII patterns from captured terminal text."""
    if not text:
        return text
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'tmux'
        )
    """)
    conn.commit()
    return conn


def _capture_tmux() -> Optional[str]:
    """Capture current tmux pane scrollback."""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", "main", "-p", "-S", "-3000"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except Exception:
        logger.debug("subprocess.run 失败（旁路，已忽略）", exc_info=True)
    return None


def _content_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()


# Strip ANSI CSI / OSC / SGR sequences. tmux capture-pane -p shouldn't emit them
# (it gives plain text), but we strip anyway as a safety net for cases where the
# pane has raw escape bytes in its scrollback.
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
# Zero-width / bidi / variation selectors that flicker in rendered TUI output
# (progress spinners, frame chars) without changing meaning. Covers:
#   U+200B-200F  zero-width / bidi marks
#   U+202A-202E  bidi embedding overrides
#   U+2060-206F  word joiners / invisible separators
#   U+FE00-FE0F  variation selectors
_INVISIBLE_RE = re.compile(
    "[\u200B-\u200F\u202A-\u202E\u2060-\u206F\uFE00-\uFE0F]"
)


def _normalize_for_dedup(text: str) -> str:
    """Reduce a pane capture to its "meaningful" content for change detection.

    Two snapshots that differ only by:
      - cursor position / blink
      - trailing whitespace on lines
      - empty trailing lines
      - inline ANSI color codes
      - zero-width characters
    will normalize to the same string. The normalized form is used ONLY for
    SHA1 dedup; the raw content is still what gets stored, so viewing isn't
    affected.
    """
    if not text:
        return ""
    s = _ANSI_RE.sub("", text)
    s = _INVISIBLE_RE.sub("", s)
    # Per-line right-strip, then drop empty trailing lines.
    lines = [ln.rstrip() for ln in s.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


# Skip writing a snapshot when the normalized payload is shorter than this.
# Bumped from 40 → 200 since each new snapshot is supposed to capture a
# meaningful chunk of activity, not just a prompt line.
_MIN_MEANINGFUL_CHARS = 200

# Cap for the merged "before" slot — 5 MB is enough for many hours of busy
# captures while keeping the SQLite row size bounded.
_LEGACY_BEFORE_MAX_BYTES = 5 * 1024 * 1024


def _do_capture(title: str = "", source: str = "manual") -> dict:
    """Capture the tmux 'main' pane into the 3-slot rolling window.

    Slots (encoded in the ``source`` column):
        snap_curr   – the latest capture
        snap_prev   – the one before (so users can diff current vs last)
        snap_before – everything older, merged into one growing blob with
                      timestamp dividers

    On each call:
        1. Old ``snap_prev`` gets folded into ``snap_before`` with a divider.
        2. Old ``snap_curr`` is re-tagged as ``snap_prev``.
        3. New content becomes the new ``snap_curr``.

    The ``source`` arg (kept for API compat) is ignored except for the
    "skip min-size on manual capture" rule.
    """
    content = _capture_tmux()
    if not content:
        return {"ok": False, "error": "无法捕获终端内容，tmux会话可能未运行"}

    content = _sanitize(content)
    normalized = _normalize_for_dedup(content)

    # Auto-captures of essentially-empty panes are dropped entirely.
    if source == "auto" and len(normalized) < _MIN_MEANINGFUL_CHARS:
        return {"ok": True, "skipped": True, "reason": "idle"}

    new_hash = _content_hash(normalized)
    ts = datetime.now().isoformat(timespec="seconds")

    db = _get_db()
    try:
        curr = db.execute(
            "SELECT id, ts, content FROM sessions WHERE source = 'snap_curr' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        # Dedup against the current slot — duplicate captures shouldn't
        # rotate prev/before out of the window.
        if curr is not None and _content_hash(_normalize_for_dedup(curr["content"])) == new_hash:
            return {"ok": True, "skipped": True, "reason": "unchanged", "last_id": curr["id"]}

        prev = db.execute(
            "SELECT id, ts, content FROM sessions WHERE source = 'snap_prev' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        before = db.execute(
            "SELECT id, content FROM sessions WHERE source = 'snap_before' ORDER BY id DESC LIMIT 1"
        ).fetchone()

        # 1. Fold old prev → before.
        if prev is not None:
            divider = f"\n\n──────── 快照 @ {prev['ts']} ────────\n\n"
            new_before_content = (before["content"] if before else "") + divider + prev["content"]
            if len(new_before_content) > _LEGACY_BEFORE_MAX_BYTES:
                new_before_content = (
                    "…（更早内容已超出 5MB 限制，已自动裁剪）…\n"
                    + new_before_content[-_LEGACY_BEFORE_MAX_BYTES:]
                )
            if before is not None:
                db.execute(
                    "UPDATE sessions SET content = ?, ts = ? WHERE id = ?",
                    (new_before_content, ts, before["id"]),
                )
            else:
                db.execute(
                    "INSERT INTO sessions (ts, title, content, source) VALUES (?, ?, ?, 'snap_before')",
                    (ts, "之前（合并）", new_before_content),
                )
            db.execute("DELETE FROM sessions WHERE id = ?", (prev["id"],))

        # 2. Old curr → prev (just retag).
        if curr is not None:
            db.execute("UPDATE sessions SET source = 'snap_prev' WHERE id = ?", (curr["id"],))

        # 3. New content becomes curr.
        new_title = title or f"快照 {ts[:16].replace('T', ' ')}"
        cur = db.execute(
            "INSERT INTO sessions (ts, title, content, source) VALUES (?, ?, ?, 'snap_curr')",
            (ts, new_title, content),
        )
        db.commit()
        return {"ok": True, "id": cur.lastrowid, "ts": ts, "title": new_title, "role": "snap_curr"}
    finally:
        db.close()


def _legacy_clear_snapshots() -> int:
    db = _get_db()
    try:
        cur = db.execute(
            "DELETE FROM sessions WHERE source IN ('snap_curr','snap_prev','snap_before')"
        )
        db.commit()
        return cur.rowcount
    finally:
        db.close()


@router.post("/capture")
async def capture_session(title: str = ""):
    """Capture current terminal content and save as a session."""
    return _do_capture(title=title, source="manual")


_ROLE_LABEL_CN = {"snap_curr": "当前", "snap_prev": "上一个", "snap_before": "之前"}


@router.get("/sessions")
async def list_sessions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    legacy: bool = Query(False, description="True to return pre-rolling auto/manual/tmux history instead"),
):
    """List snapshot rows. Default = the 3-slot rolling view (当前/上一个/之前)."""
    db = _get_db()
    try:
        if legacy:
            rows = db.execute(
                """SELECT id, ts, title, source, LENGTH(content) AS size
                     FROM sessions
                    WHERE source NOT IN ('snap_curr','snap_prev','snap_before')
                    ORDER BY id DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
            total = db.execute(
                "SELECT COUNT(*) FROM sessions WHERE source NOT IN ('snap_curr','snap_prev','snap_before')"
            ).fetchone()[0]
            return {"sessions": [dict(r) for r in rows], "total": total}
        # Rolling-3 view: explicit ordering 当前 → 上一个 → 之前
        rows: list[dict] = []
        for role in ("snap_curr", "snap_prev", "snap_before"):
            row = db.execute(
                """SELECT id, ts, title, source, LENGTH(content) AS size
                     FROM sessions WHERE source = ?
                     ORDER BY id DESC LIMIT 1""",
                (role,),
            ).fetchone()
            if row:
                d = dict(row)
                d["role"] = role
                d["label"] = _ROLE_LABEL_CN[role]
                rows.append(d)
        return {"sessions": rows, "total": len(rows)}
    finally:
        db.close()


@router.post("/sessions/clear")
async def clear_legacy_snapshots():
    """Wipe all rolling snapshots (curr/prev/before) of the main terminal.
    Old auto/manual/tmux history rows are NOT touched."""
    return {"ok": True, "removed": _legacy_clear_snapshots()}


@router.get("/sessions/{session_id}")
async def get_session(session_id: int):
    """Get full content of a saved session."""
    db = _get_db()
    row = db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    db.close()
    if not row:
        return {"ok": False, "error": "会话不存在"}
    return dict(row)


@router.get("/search")
async def search_sessions(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(50, ge=1, le=200),
):
    """Full-text search over saved session content & titles.

    Returns matched rows with a short snippet centered on the first hit
    (case-insensitive). Content itself is NOT included in the response —
    callers should hit /sessions/{id} for the full body.
    """
    db = _get_db()
    pattern = f"%{q}%"
    rows = db.execute(
        """
        SELECT id, ts, title, source, LENGTH(content) AS size, content
          FROM sessions
         WHERE content LIKE ? COLLATE NOCASE
            OR title   LIKE ? COLLATE NOCASE
         ORDER BY id DESC
         LIMIT ?
        """,
        (pattern, pattern, limit),
    ).fetchall()
    db.close()

    q_lower = q.lower()
    results = []
    for r in rows:
        content = r["content"] or ""
        idx = content.lower().find(q_lower)
        if idx < 0:
            # Match was on title only.
            snippet = (r["title"] or "")[:200]
        else:
            start = max(0, idx - 80)
            end = min(len(content), idx + len(q) + 120)
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(content) else ""
            snippet = prefix + content[start:end] + suffix
        results.append({
            "id": r["id"],
            "ts": r["ts"],
            "title": r["title"],
            "source": r["source"],
            "size": r["size"],
            "snippet": snippet,
        })
    return {"sessions": results, "query": q, "total": len(results)}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: int):
    """Delete a saved session."""
    db = _get_db()
    db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    db.commit()
    db.close()
    return {"ok": True}


@router.get("/bash-history")
async def get_bash_history(lines: int = Query(100, ge=1, le=2000)):
    """Read recent bash history."""
    hist_file = Path.home() / ".bash_history"
    if not hist_file.exists():
        return {"lines": []}
    try:
        text = hist_file.read_text(errors="replace")
        all_lines = [l for l in text.splitlines() if l.strip()]
        return {"lines": all_lines[-lines:]}
    except Exception as e:
        return {"lines": [], "error": str(e)}


@router.post("/upload-image")
async def upload_image(file: UploadFile = File(...)):
    """Upload an image and return its server path."""
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = Path(file.filename or "img.png").suffix or ".png"
    name = f"upload_{ts}{suffix}"
    dest = _UPLOAD_DIR / name
    content = await file.read()
    dest.write_bytes(content)
    return {"ok": True, "path": str(dest), "size": len(content)}


# ---------------------------------------------------------------------------
# Background auto-capture task
# ---------------------------------------------------------------------------
# Owned by the FastAPI lifespan context (see app.main). Runs forever in the
# event loop, calling _do_capture() on a fixed interval. Dedup logic inside
# _do_capture means an idle terminal won't generate any new rows.
_autocapture_task: Optional[asyncio.Task] = None


async def _autocapture_loop(interval: int) -> None:
    logger.info("[terminal] auto-capture loop started (interval=%ds)", interval)
    # Initial small delay so the first run doesn't race with startup work.
    await asyncio.sleep(min(interval, 30))
    while True:
        try:
            # _do_capture is blocking (subprocess + sqlite). Push it to a
            # thread so we don't stall the event loop on slow tmux output.
            result = await asyncio.to_thread(_do_capture, "", "auto")
            if result.get("ok") and not result.get("skipped"):
                logger.info("[terminal] auto-capture saved id=%s", result.get("id"))
        except Exception:  # noqa: BLE001
            logger.exception("[terminal] auto-capture iteration failed")
        await asyncio.sleep(interval)


def start_autocapture() -> None:
    """Spawn the background auto-capture task. Idempotent."""
    global _autocapture_task
    if _WINDOWS:
        logger.info("[terminal] auto-capture not supported on Windows (tmux unavailable)")
        return
    if not settings.terminal_autocapture_enabled:
        logger.info("[terminal] auto-capture disabled by config")
        return
    if _autocapture_task and not _autocapture_task.done():
        return
    interval = max(30, settings.terminal_autocapture_interval)
    _autocapture_task = asyncio.create_task(
        _autocapture_loop(interval), name="terminal-autocapture"
    )


async def stop_autocapture() -> None:
    """Cancel the background auto-capture task on shutdown."""
    global _autocapture_task
    if _autocapture_task and not _autocapture_task.done():
        _autocapture_task.cancel()
        try:
            await _autocapture_task
        except (asyncio.CancelledError, Exception):
            logger.debug("_autocapture_task 失败（旁路，已忽略）", exc_info=True)
    _autocapture_task = None


# ---------------------------------------------------------------------------
# Live multi-terminal sessions (new workbench implementation)
# ---------------------------------------------------------------------------
class TerminalCreateBody(BaseModel):
    title: str | None = None
    shell: str | None = None
    workdir: str | None = None


class TerminalPatchBody(BaseModel):
    title: str | None = None
    archived: bool | None = None
    workdir: str | None = None


def init_live_sessions() -> None:
    live_svc.init_db()
    live_manager.start_background_tasks()


async def shutdown_live_sessions() -> None:
    await live_manager.shutdown()


def _ws_authenticate(websocket: WebSocket) -> str | None:
    cookie = websocket.cookies.get(settings.session_cookie_name)
    if not cookie:
        return None
    return verify_session(cookie)


@router.get("/live/sessions")
def list_live_sessions(
    user: str = Depends(require_user),
    archived: bool = Query(False),
):
    live_svc.init_db()
    return {"sessions": live_svc.list_sessions(user_id=user, include_archived=archived)}


@router.post("/live/sessions")
async def create_live_session(body: TerminalCreateBody, user: str = Depends(require_user)):
    sess = live_svc.create_session(
        user_id=user,
        title=body.title,
        shell=body.shell,
        workdir=body.workdir,
    )
    await live_manager.start(sess["id"], shell=sess["shell"], workdir=sess.get("workdir"))
    return live_svc.get_session(sess["id"])


@router.get("/live/sessions/{session_id}")
def get_live_session(session_id: str, _user: str = Depends(require_user)):
    return live_svc.get_session(session_id)


@router.patch("/live/sessions/{session_id}")
def patch_live_session(session_id: str, body: TerminalPatchBody, _user: str = Depends(require_user)):
    return live_svc.update_session(
        session_id,
        title=body.title,
        archived=body.archived,
        workdir=body.workdir,
    )


@router.delete("/live/sessions/{session_id}")
async def delete_live_session(session_id: str, _user: str = Depends(require_user)):
    if live_manager.is_live(session_id):
        await live_manager._kill(session_id, reason="delete")
    live_svc.delete_session(session_id)
    return {"ok": True}


@router.post("/live/sessions/{session_id}/close")
async def close_live_session(session_id: str, _user: str = Depends(require_user)):
    await live_manager._kill(session_id, reason="manual")
    return {"ok": True}


@router.get("/live/sessions/{session_id}/history")
def get_live_history(
    session_id: str,
    _user: str = Depends(require_user),
    after_seq: int = Query(0, ge=0),
    before_seq: int | None = Query(None, ge=1),
    limit: int = Query(500, ge=1, le=5000),
):
    return {
        "items": live_svc.list_history(session_id, after_seq=after_seq, before_seq=before_seq, limit=limit),
        "total": live_svc.count_history(session_id),
    }


# ─── Live session snapshots ─────────────────────────────────────────────────
# Periodic full-pane captures of each live terminal — covers TUI / AI CLI
# output the event log skips. Cascades on session delete.

@router.get("/live/sessions/{session_id}/snapshots")
def list_live_snapshots(
    session_id: str,
    _user: str = Depends(require_user),
    limit: int = Query(80, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return live_svc.list_snapshots(session_id, limit=limit, offset=offset)


@router.get("/live/sessions/{session_id}/snapshots/{snap_id}")
def get_live_snapshot(
    session_id: str,
    snap_id: int,
    _user: str = Depends(require_user),
):
    snap = live_svc.get_snapshot(session_id, snap_id)
    if not snap:
        raise __import__("fastapi").HTTPException(status_code=404, detail="snapshot not found")
    return snap


@router.post("/live/sessions/{session_id}/snapshots")
def capture_live_snapshot(
    session_id: str,
    _user: str = Depends(require_user),
):
    return live_manager.capture_now(session_id)


@router.delete("/live/sessions/{session_id}/snapshots/{snap_id}")
def delete_live_snapshot(
    session_id: str,
    snap_id: int,
    _user: str = Depends(require_user),
):
    return {"ok": live_svc.delete_snapshot(session_id, snap_id)}


@router.post("/live/sessions/{session_id}/snapshots/clear")
def clear_live_snapshots(
    session_id: str,
    _user: str = Depends(require_user),
):
    """Wipe all rolling snapshots (curr/prev/before) for this live session."""
    return {"ok": True, "removed": live_svc.clear_snapshots(session_id)}


@router.get("/live/legacy-ttyd")
def legacy_ttyd_status(_user: str = Depends(require_user)):
    return _legacy_ttyd_status()


@router.post("/live/legacy-ttyd/start")
def legacy_ttyd_start(_user: str = Depends(require_user)):
    return _legacy_ttyd_action("start")


@router.post("/live/legacy-ttyd/stop")
def legacy_ttyd_stop(_user: str = Depends(require_user)):
    return _legacy_ttyd_action("stop")


@router.get("/live/stats")
def live_stats(_user: str = Depends(require_user)):
    return live_manager.stats()


@router.websocket("/live/{session_id}/ws")
async def terminal_live_ws(websocket: WebSocket, session_id: str) -> None:
    user = _ws_authenticate(websocket)
    if not user:
        await websocket.close(code=4401)
        return
    try:
        sess = live_svc.get_session(session_id)
    except live_svc.TerminalSessionError:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    if not live_manager.is_live(session_id):
        try:
            await live_manager.start(session_id, shell=sess["shell"], workdir=sess.get("workdir"))
        except Exception as e:
            await websocket.send_json({"type": "error", "detail": str(e)})
            await websocket.close(code=4500)
            return
    queue = live_manager.subscribe(session_id)
    send_task: asyncio.Task | None = None

    async def pump_to_client() -> None:
        try:
            while True:
                item = await queue.get()
                await websocket.send_json(item)
                if item.get("type") == "exit":
                    break
        except (WebSocketDisconnect, RuntimeError):
            return

    send_task = asyncio.create_task(pump_to_client(), name=f"terminal-live-ws-{session_id[:6]}")
    try:
        while True:
            msg = await websocket.receive_text()
            try:
                payload = json.loads(msg)
            except json.JSONDecodeError:
                payload = {"type": "input", "data": msg}
            t = payload.get("type")
            if t == "input":
                data = payload.get("data") or ""
                if data:
                    try:
                        await live_manager.write(session_id, data)
                    except RuntimeError as e:
                        await websocket.send_json({"type": "error", "detail": str(e)})
            elif t == "resize":
                cols = int(payload.get("cols") or 80)
                rows = int(payload.get("rows") or 24)
                await live_manager.resize(session_id, cols, rows)
            elif t == "ping":
                await websocket.send_json({"type": "pong", "t": datetime.now().timestamp()})
            else:
                await websocket.send_json({"type": "error", "detail": f"未知消息类型: {t}"})
    except WebSocketDisconnect:
        logger.debug("msg = await websocket.receive_text 失败（旁路，已忽略）", exc_info=True)
    finally:
        live_manager.unsubscribe(session_id, queue)
        if send_task and not send_task.done():
            send_task.cancel()
