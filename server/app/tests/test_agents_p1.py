"""P1 tests: projects + sessions read/management against a temp DB and a
hand-written Claude JSONL transcript."""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_ORIGIN = "https://test.example.com"
_HDR = {"Origin": _ORIGIN}


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "agents.db"
    monkeypatch.setenv("IVYEA_OPS_SECRET", "test-secret")
    monkeypatch.setenv("IVYEA_OPS_ALLOWED_ORIGINS", _ORIGIN)
    monkeypatch.setenv("AGENTS_DB_PATH", str(db_path))

    # Isolate HOME so the projects-list synchronizer scans an empty ~/.claude
    # (otherwise it would pull in the host's real sessions and pollute the test).
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    from app.core import config as cfg_mod
    importlib.reload(cfg_mod)
    from app.core import security as sec_mod
    importlib.reload(sec_mod)
    from app.agents import db as db_mod
    importlib.reload(db_mod)
    db_mod.init_db()
    from app.agents import synchronizer as sync_mod
    importlib.reload(sync_mod)  # re-evaluate _CLAUDE_HOME against the isolated HOME

    # Seed a project + two claude sessions; one session has a transcript file.
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    sid = "11111111-1111-1111-1111-111111111111"
    transcript.write_text("\n".join(json.dumps(e) for e in [
        {"sessionId": sid, "timestamp": "2026-01-01T00:00:00Z",
         "message": {"role": "user", "content": "hello world"}},
        {"sessionId": sid, "timestamp": "2026-01-01T00:00:01Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "hi there"}]}},
    ]), encoding="utf-8")

    with db_mod.db_conn() as conn:
        conn.execute(
            "INSERT INTO projects(project_id, project_path, custom_project_name, isStarred, isArchived)"
            " VALUES(?,?,?,?,?)", ("p1", str(proj_dir), None, 0, 0))
        conn.execute(
            "INSERT INTO sessions(session_id, provider, custom_name, project_path, jsonl_path, isArchived,"
            " created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (sid, "claude", "First session", str(proj_dir), str(transcript), 0,
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:02Z"))
        conn.execute(
            "INSERT INTO sessions(session_id, provider, custom_name, project_path, jsonl_path, isArchived,"
            " created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            ("sess-archived", "claude", "Old", str(proj_dir), None, 1,
             "2025-12-01T00:00:00Z", "2025-12-01T00:00:00Z"))

    from app import main as main_mod
    importlib.reload(main_mod)
    cookie = sec_mod.issue_session("admin", "admin")
    with TestClient(main_mod.app) as c:
        c.cookies.set(cfg_mod.settings.session_cookie_name, cookie)
        yield c, sid, str(proj_dir)


def test_list_projects(ctx):
    c, sid, _ = ctx
    r = c.get("/api/agents/projects")
    assert r.status_code == 200, r.text
    projects = r.json()
    assert len(projects) == 1
    p = projects[0]
    assert p["projectId"] == "p1"
    assert p["displayName"] == "proj"  # basename fallback
    assert [s["id"] for s in p["sessions"]] == [sid]  # active only, archived excluded
    assert p["sessionMeta"]["total"] == 1


def test_project_sessions_page(ctx):
    c, sid, _ = ctx
    r = c.get("/api/agents/projects/p1/sessions", params={"limit": 10, "offset": 0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["projectId"] == "p1"
    assert body["sessions"][0]["summary"] == "First session"


def test_session_messages(ctx):
    c, sid, _ = ctx
    r = c.get(f"/api/agents/providers/sessions/{sid}/messages")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    kinds = [(m["kind"], m.get("role")) for m in body["messages"]]
    assert ("text", "user") in kinds and ("text", "assistant") in kinds
    assert body["messages"][0]["content"] == "hello world"


def test_archived_sessions(ctx):
    c, *_ = ctx
    r = c.get("/api/agents/providers/sessions/archived")
    assert r.status_code == 200, r.text
    sessions = r.json()["data"]["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["sessionId"] == "sess-archived"
    assert sessions[0]["sessionTitle"] == "Old"


def test_rename_and_star_and_archive_project(ctx):
    c, sid, _ = ctx
    assert c.put("/api/agents/projects/p1/rename", json={"displayName": "My Proj"}, headers=_HDR).json()["success"]
    assert c.get("/api/agents/projects").json()[0]["displayName"] == "My Proj"

    star = c.post("/api/agents/projects/p1/toggle-star", headers=_HDR).json()
    assert star["isStarred"] is True

    # Soft delete (archive) hides it from the active list, surfaces in archived.
    assert c.request("DELETE", "/api/agents/projects/p1", headers=_HDR).json()["success"]
    assert c.get("/api/agents/projects").json() == []
    arch = c.get("/api/agents/projects/archived").json()["data"]["projects"]
    assert len(arch) == 1 and arch[0]["isArchived"] is True
    # Restore brings it back.
    assert c.post("/api/agents/projects/p1/restore", headers=_HDR).json()["data"]["isArchived"] is False
    assert len(c.get("/api/agents/projects").json()) == 1


def test_rename_delete_restore_session(ctx):
    c, sid, _ = ctx
    assert c.put(f"/api/agents/providers/sessions/{sid}", json={"summary": "Renamed"}, headers=_HDR).json()["data"]["summary"] == "Renamed"
    # Soft delete -> archived
    d = c.request("DELETE", f"/api/agents/providers/sessions/{sid}", headers=_HDR).json()
    assert d["data"]["action"] == "archived"
    # Restore
    r = c.post(f"/api/agents/providers/sessions/{sid}/restore", headers=_HDR).json()
    assert r["data"]["isArchived"] is False


def test_invalid_session_id(ctx):
    c, *_ = ctx
    r = c.get("/api/agents/providers/sessions/bad id!/messages")
    assert r.status_code == 400


def test_token_usage(ctx):
    c, sid, _ = ctx
    # transcript fixture has no usage; seed one quickly via a second transcript? The
    # P1 transcript lacks usage, so expect zeros but a well-formed shape.
    r = c.get(f"/api/agents/projects/p1/sessions/{sid}/token-usage", params={"provider": "claude"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"used", "total", "inputTokens", "outputTokens", "breakdown"}.issubset(body.keys())
    # non-claude provider is reported unsupported
    r2 = c.get(f"/api/agents/projects/p1/sessions/{sid}/token-usage", params={"provider": "codex"})
    assert r2.json().get("unsupported") is True
