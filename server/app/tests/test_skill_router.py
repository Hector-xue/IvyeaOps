"""Integration tests for the /api/skill router (read-only endpoints for now).

We override the ``require_user`` dependency to bypass cookie auth in tests.
The CSRF origin guard only fires on unsafe methods, so GETs pass through
without needing an Origin header.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    skills = tmp_path / "skills"
    studio = tmp_path / "skill-studio"
    skills.mkdir()
    studio.mkdir()
    monkeypatch.setenv("IVYEA_OPS_SKILLS_ROOT", str(skills))
    monkeypatch.setenv("IVYEA_OPS_STUDIO_ROOT", str(studio))
    # Need a secret for the session serializer to initialize (harmless value).
    monkeypatch.setenv("IVYEA_OPS_SECRET", "test-secret-not-used")

    import importlib
    # Reload in dependency order.
    from app.core import skill_paths as sp_mod
    importlib.reload(sp_mod)
    sp_mod.ensure_studio_dirs()
    from app.services import skill_repo as sr_mod
    importlib.reload(sr_mod)
    from app.services import snapshot as snap_mod
    importlib.reload(snap_mod)
    from app.services import trash as trash_mod
    importlib.reload(trash_mod)
    from app.routers import skill as skill_router_mod
    importlib.reload(skill_router_mod)
    from app import main as main_mod
    importlib.reload(main_mod)

    # Populate some skills before spinning up the client.
    def _mk(name: str, body: str = "body"):
        d = skills / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name.split('/')[-1]}\ndescription: \"{name} desc\"\n---\n\n{body}\n",
            encoding="utf-8",
        )
        return d

    d1 = _mk("dogfood", "dogfood body")
    (d1 / "references").mkdir()
    (d1 / "references" / "notes.md").write_text("ref notes\n", encoding="utf-8")
    _mk("research/arxiv", "arxiv body")
    _mk("mlops/inference/llama-cpp", "llama body")
    # Hidden must be ignored:
    (skills / ".archive" / "old").mkdir(parents=True)
    (skills / ".archive" / "old" / "SKILL.md").write_text(
        "---\nname: old\ndescription: d\n---\n", encoding="utf-8"
    )

    # Override auth.
    from app.core import security as sec_mod
    main_mod.app.dependency_overrides[sec_mod.require_user] = lambda: "tester"

    with TestClient(main_mod.app) as c:
        yield c


def test_stats_endpoint(client):
    r = client.get("/api/skill/stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_skills"] == 3
    # .archive was excluded.
    assert body["total_size_bytes"] > 0
    assert "research" in body["categories"]
    assert body["categories"]["research"] == 1
    assert body["categories"]["mlops/inference"] == 1
    assert len(body["recently_edited"]) <= 5


def test_list_endpoint(client):
    r = client.get("/api/skill/list")
    assert r.status_code == 200
    body = r.json()
    names = {s["name"] for s in body["skills"]}
    assert names == {"dogfood", "research/arxiv", "mlops/inference/llama-cpp"}
    assert body["total"] == 3


def test_list_filters(client):
    # name substring
    r = client.get("/api/skill/list", params={"q": "arxiv"})
    assert [s["name"] for s in r.json()["skills"]] == ["research/arxiv"]

    # category
    r = client.get("/api/skill/list", params={"category": "research"})
    assert [s["name"] for s in r.json()["skills"]] == ["research/arxiv"]

    # description substring (case-insensitive)
    r = client.get("/api/skill/list", params={"q": "DOGFOOD"})
    assert [s["name"] for s in r.json()["skills"]] == ["dogfood"]


def test_item_endpoint_nested_name(client):
    r = client.get("/api/skill/item/mlops/inference/llama-cpp")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "mlops/inference/llama-cpp"
    assert body["category"] == "mlops/inference"
    assert "llama body" in body["content_body"]


def test_item_not_found(client):
    r = client.get("/api/skill/item/does-not-exist")
    assert r.status_code == 404


def test_file_endpoint_reads_linked_file(client):
    r = client.get(
        "/api/skill/file/dogfood",
        params={"path": "references/notes.md"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content"].strip() == "ref notes"
    assert body["is_binary"] is False


def test_file_endpoint_rejects_traversal(client):
    r = client.get(
        "/api/skill/file/dogfood",
        params={"path": "../../../etc/passwd"},
    )
    assert r.status_code in (400, 403)


def test_file_endpoint_404_on_missing(client):
    r = client.get(
        "/api/skill/file/dogfood",
        params={"path": "does/not/exist.md"},
    )
    assert r.status_code == 404


def test_hidden_paths_are_not_listed(client):
    r = client.get("/api/skill/list")
    names = {s["name"] for s in r.json()["skills"]}
    assert not any(n.startswith(".") or "/." in n for n in names)
