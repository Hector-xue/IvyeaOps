from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


_ORIGIN = "https://test.example.com"
_HDR = {"Origin": _ORIGIN}


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    brain = tmp_path / "brain"
    brain.mkdir()
    (brain / "amazon").mkdir()
    (brain / "amazon" / "note.md").write_text("# Note\n\nAmazon 广告优化\n", encoding="utf-8")

    monkeypatch.setenv("IVYEA_OPS_BRAIN_ROOT", str(brain))
    monkeypatch.setenv("IVYEA_OPS_BRAIN_CHAT_DB", str(tmp_path / "brain_chat.sqlite3"))
    monkeypatch.setenv("IVYEA_OPS_SECRET", "test-secret")
    monkeypatch.setenv("IVYEA_OPS_ALLOWED_ORIGINS", _ORIGIN)

    import importlib
    from app.core import config as cfg_mod
    importlib.reload(cfg_mod)
    from app.services import gbrain_service as gb_mod
    importlib.reload(gb_mod)
    from app.services import brain_chat_service as bc_mod
    importlib.reload(bc_mod)
    from app.routers import brain as brain_router_mod
    importlib.reload(brain_router_mod)
    from app import main as main_mod
    importlib.reload(main_mod)

    # 知识库的"前门"是 IvyeaAgent，只有本地 agent 服务不可达时才回退到 GBrain
    # markdown 存储（见 routers/brain._ivyea_front_door）。这批测试写的是 GBrain
    # 那条路 —— 如果不钉死，结果就取决于**跑测试的机器上 8765 端口有没有 agent
    # 在跑**：开发机上走 agent 分支去读真实知识库，于是 `total` 对不上、文件也不是
    # 测试自己造的那份；CI 上没 agent 又能过。测试不该依赖某个服务恰好起着。
    #
    # （前门那条路自己的覆盖是缺的，见 test_brain_front_door 那条。）
    monkeypatch.setattr(bc_mod, "ivyea_chat_available", lambda: False)

    from app.core import security as sec_mod
    main_mod.app.dependency_overrides[sec_mod.require_user] = lambda: "tester"
    # 聊天消息那几个接口用的是 require_user_info（返回 principal 字典）而不是
    # require_user —— 只 override 后者覆盖不到，请求会 401。
    main_mod.app.dependency_overrides[sec_mod.require_user_info] = lambda: {
        "id": "admin", "role": "admin", "email": "tester", "permissions": [],
    }

    with TestClient(main_mod.app) as c:
        yield c, brain, gb_mod, bc_mod


def test_list_and_read_file(client):
    c, _brain, _gb, _bc = client
    r = c.get("/api/brain/files")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 1
    assert data["files"][0]["path"] == "amazon/note.md"

    r = c.get("/api/brain/file", params={"path": "amazon/note.md"})
    assert r.status_code == 200, r.text
    assert "广告优化" in r.json()["content"]


def test_write_file_rejects_path_escape(client):
    c, _brain, _gb, _bc = client
    r = c.put(
        "/api/brain/file",
        json={"path": "../x.md", "content": "bad"},
        headers=_HDR,
    )
    assert r.status_code == 400


def test_write_file_allows_markdown_under_brain(client):
    c, brain, _gb, _bc = client
    r = c.put(
        "/api/brain/file",
        json={"path": "products/test.md", "content": "# Product\n"},
        headers=_HDR,
    )
    assert r.status_code == 200, r.text
    assert (brain / "products" / "test.md").read_text(encoding="utf-8") == "# Product\n"


def test_search_uses_whitelisted_service(client, monkeypatch):
    c, _brain, gb, _bc = client

    def fake_search(query: str, mode: str = "search"):
        return {"query": query, "mode": mode, "raw": "", "items": [{"slug": "amazon/note", "score": 1, "snippet": "ok"}]}

    monkeypatch.setattr(gb, "search", fake_search)
    r = c.post("/api/brain/search", json={"query": "广告", "mode": "search"}, headers=_HDR)
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["slug"] == "amazon/note"


def test_search_rejects_bad_mode(client):
    c, _brain, _gb, _bc = client
    r = c.post("/api/brain/search", json={"query": "x", "mode": "shell"}, headers=_HDR)
    assert r.status_code == 422


def test_upload_text_creates_markdown_under_brain(client, monkeypatch):
    c, brain, _gb, _bc = client
    monkeypatch.setattr(_gb, "import_brain", lambda: {"ok": True, "raw": "import ok"})
    r = c.post(
        "/api/brain/upload",
        files={"file": ("note.txt", b"hello knowledge", "text/plain")},
        data={"category": "ads", "title": "Ad Note", "import_after_save": "true"},
        headers=_HDR,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["saved_path"].startswith("ads/uploads/")
    assert data["import_status"] == "ok"
    assert (brain / data["saved_path"]).read_text(encoding="utf-8").find("hello knowledge") >= 0


def test_ingest_text_uses_hermes_analysis_and_creates_new_directory(client, monkeypatch):
    c, brain, _gb, bc = client
    monkeypatch.setattr(_gb, "import_brain", lambda: {"ok": True, "raw": "import ok"})
    monkeypatch.setattr(
        bc,
        "_call_runner_json",
        lambda prompt: {
            "title": "Trail Camera 广告复盘",
            "directory": "amazon/ads/reviews",
            "tags": ["广告", "trail-camera", "ACOS"],
            "summary": "这是一份广告复盘摘要。",
            "content_type": "amazon_ads",
            "confidence": 0.92,
        },
    )

    r = c.post(
        "/api/brain/ingest/text",
        json={"text": "ACOS 上升，trail camera campaign 需要先优化 CTR。", "import_after_save": True},
        headers=_HDR,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["category"] == "amazon/ads/reviews"
    assert data["analysis"]["source"] == "hermes_json"
    assert data["import_status"] == "ok"
    saved = brain / data["saved_path"]
    assert saved.exists()
    content = saved.read_text(encoding="utf-8")
    assert "# Trail Camera 广告复盘" in content
    assert "## 自动摘要" in content
    assert "ACOS 上升" in content


def test_ingest_text_falls_back_and_sanitizes_bad_directory(client, monkeypatch):
    c, brain, _gb, bc = client
    monkeypatch.setattr(_gb, "import_brain", lambda: {"ok": True, "raw": "import ok"})
    monkeypatch.setattr(
        bc,
        "_call_runner_json",
        lambda prompt: {
            "title": "../危险标题",
            "directory": "../../.ssh/secret",
            "tags": ["../bad", "合规"],
            "summary": "危险路径测试。",
            "content_type": "note",
            "confidence": 0.8,
        },
    )

    r = c.post(
        "/api/brain/ingest/text",
        json={"text": "售后模板：不能用好评截图换延保。", "import_after_save": True},
        headers=_HDR,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["category"] == "inbox"
    assert data["saved_path"].startswith("inbox/")
    assert not data["saved_path"].startswith("..")
    assert ".ssh" not in data["saved_path"]
    assert (brain / data["saved_path"]).resolve().relative_to(brain.resolve())


def test_ingest_text_rules_fallback_when_hermes_unavailable(client, monkeypatch):
    c, brain, _gb, bc = client
    monkeypatch.setattr(_gb, "import_brain", lambda: {"ok": True, "raw": "import ok"})
    monkeypatch.setattr(bc, "_call_runner_json", lambda prompt: (_ for _ in ()).throw(RuntimeError("offline")))

    r = c.post(
        "/api/brain/ingest/text",
        json={"text": "供应商 1688 报价，工厂交期和包装风险需要记录。", "import_after_save": True},
        headers=_HDR,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["category"] == "amazon/suppliers"
    assert data["analysis"]["source"] == "rules_fallback"
    assert any("Hermes 自动分析失败" in w for w in data["warnings"])
    assert (brain / data["saved_path"]).exists()


def test_chat_sessions_persist_messages(client, monkeypatch):
    c, _brain, gb, bc = client
    monkeypatch.setattr(gb, "search", lambda q, mode="search": {"items": [{"slug": "amazon/note", "score": 1, "snippet": "广告优化"}]})
    monkeypatch.setattr(bc, "_call_llm", lambda messages: "基于知识库的回答")

    r = c.post("/api/brain/chat/sessions", json={"title": "测试会话", "mode": "amazon_operator"}, headers=_HDR)
    assert r.status_code == 200, r.text
    sid = r.json()["session"]["id"]

    r = c.post(f"/api/brain/chat/sessions/{sid}/messages", json={"content": "广告怎么优化？"}, headers=_HDR)
    assert r.status_code == 200, r.text
    assert r.json()["assistant_message"]["content"] == "基于知识库的回答"

    r = c.get(f"/api/brain/chat/sessions/{sid}")
    assert r.status_code == 200, r.text
    messages = r.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["citations"][0]["slug"] == "amazon/note"


def test_chat_model_status_does_not_leak_keys(client):
    c, _brain, _gb, _bc = client
    r = c.get("/api/brain/chat/status")
    assert r.status_code == 200, r.text
    assert "api_key" not in r.text.lower()
