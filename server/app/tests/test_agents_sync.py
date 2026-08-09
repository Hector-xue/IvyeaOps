"""Tests for the claude session synchronizer. Fully isolated: HOME points at a
temp dir with a fake ~/.claude/projects tree, and the DB is a temp file."""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def sync_env(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    claude_proj = home / ".claude" / "projects" / "-some-proj"
    claude_proj.mkdir(parents=True)
    # transcript: first line carries sessionId + cwd; an ai-title near the end.
    (claude_proj / "sess1.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"sessionId": "sess1", "cwd": "/some/proj", "type": "user",
         "message": {"role": "user", "content": "hi"}},
        {"type": "ai-title", "sessionId": "sess1", "aiTitle": "My First Chat"},
    ]), encoding="utf-8")
    # history.jsonl name map (won't override the ai-title for sess1; add sess2-style)
    (home / ".claude" / "history.jsonl").write_text(
        json.dumps({"sessionId": "sess1", "display": "history-name"}) + "\n", encoding="utf-8")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AGENTS_DB_PATH", str(tmp_path / "agents.db"))
    from app.agents import db as db_mod
    importlib.reload(db_mod); db_mod.init_db()
    from app.agents import repos as repos_mod
    importlib.reload(repos_mod)
    from app.agents import synchronizer as sync_mod
    importlib.reload(sync_mod)
    return sync_mod, db_mod, repos_mod


def test_synchronize_creates_project_and_session(sync_env):
    sync_mod, db_mod, repos_mod = sync_env
    n = sync_mod.synchronize()
    assert n == 1
    with db_mod.db_conn() as conn:
        proj = repos_mod.get_project_by_path(conn, "/some/proj")
        assert proj is not None
        sess = repos_mod.get_session_by_id(conn, "sess1")
        assert sess is not None
        assert sess["project_path"] == "/some/proj"
        assert sess["jsonl_path"].endswith("sess1.jsonl")
        # existing custom_name wins; first sync uses history map ("history-name")
        # because it's checked before the ai-title fallback.
        assert sess["custom_name"] == "history-name"


def test_maybe_synchronize_throttles(sync_env):
    sync_mod, _db, _repos = sync_env
    assert sync_mod.maybe_synchronize() >= 1   # first runs
    assert sync_mod.maybe_synchronize() == 0    # throttled within 3s


def test_idempotent_resync(sync_env):
    sync_mod, db_mod, repos_mod = sync_env
    sync_mod.synchronize()
    sync_mod.synchronize()  # again — must not duplicate
    with db_mod.db_conn() as conn:
        rows = conn.execute("SELECT COUNT(*) AS c FROM sessions WHERE session_id='sess1'").fetchone()
        assert rows["c"] == 1
