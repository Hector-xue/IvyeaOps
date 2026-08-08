"""P6 tests: provider info routes (auth status, models, skills, mcp)."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_ORIGIN = "https://test.example.com"
_HDR = {"Origin": _ORIGIN}


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("IVYEA_OPS_SECRET", "test-secret")
    monkeypatch.setenv("IVYEA_OPS_ALLOWED_ORIGINS", _ORIGIN)
    monkeypatch.setenv("AGENTS_DB_PATH", str(tmp_path / "agents.db"))
    from app.core import config as cfg_mod
    importlib.reload(cfg_mod)
    from app.core import security as sec_mod
    importlib.reload(sec_mod)
    from app.agents import db as db_mod
    importlib.reload(db_mod); db_mod.init_db()
    from app.agents.routers import providers as prov_mod
    importlib.reload(prov_mod)
    from app.agents import router as router_mod
    importlib.reload(router_mod)
    from app import main as main_mod
    importlib.reload(main_mod)
    cookie = sec_mod.issue_session("admin", "admin")
    c = TestClient(main_mod.app)
    c.cookies.set(cfg_mod.settings.session_cookie_name, cookie)
    return c


def test_claude_auth_status_shape(client):
    r = client.get("/api/agents/providers/claude/auth/status")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["provider"] == "claude"
    assert {"installed", "authenticated", "email", "method"}.issubset(data.keys())


def test_other_provider_auth_status(client):
    for p in ("codex", "hermes", "gemini", "agy"):
        data = client.get(f"/api/agents/providers/{p}/auth/status").json()["data"]
        assert data["provider"] == p
        assert isinstance(data["installed"], bool)


def test_models_claude_vs_other(client):
    cm = client.get("/api/agents/providers/claude/models").json()["data"]
    assert cm["provider"] == "claude"
    # DEFAULT is registry-driven (the live configured model) when available,
    # else the static fallback — just assert a sane catalog + a known option.
    assert cm["models"]["DEFAULT"]
    assert any(o["value"] == "sonnet" for o in cm["models"]["OPTIONS"])
    # cache must be a truthy ProviderModelsCacheInfo or the frontend discards the
    # whole catalog (provider becomes unselectable with no models).
    assert cm["cache"] and cm["cache"]["source"] and cm["cache"]["updatedAt"]
    om = client.get("/api/agents/providers/codex/models").json()["data"]
    assert om["provider"] == "codex" and "OPTIONS" in om["models"]


def test_skills_and_mcp_stub(client):
    sk = client.get("/api/agents/providers/claude/skills").json()["data"]
    assert sk["skills"] == []
    mcp = client.get("/api/agents/providers/claude/mcp/servers").json()["data"]
    assert "scopes" in mcp
    scoped = client.get("/api/agents/providers/claude/mcp/servers", params={"scope": "user"}).json()["data"]
    assert scoped["scope"] == "user" and scoped["servers"] == []


def test_active_model_ack(client):
    r = client.post("/api/agents/providers/claude/sessions/abc/active-model",
                    json={"model": "sonnet"}, headers=_HDR)
    assert r.status_code == 200
    assert r.json()["data"]["model"] == "sonnet"


def test_sessions_route_not_shadowed_by_provider(client):
    # /providers/sessions/archived must still hit the sessions router, not {provider}.
    r = client.get("/api/agents/providers/sessions/archived")
    assert r.status_code == 200
    assert r.json() == {"success": True, "data": {"sessions": []}}
