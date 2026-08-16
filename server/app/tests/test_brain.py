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


# ── GBrain 摘除：/brain 必须在「只有 IvyeaAgent」的机器上完整工作 ──────────

def _kill_gbrain(monkeypatch):
    """把 GBrain 变成"本机根本没装"，且任何触碰二进制的调用都当场炸。"""
    from app.services import gbrain_service as gb
    monkeypatch.setattr(gb, "installed", lambda: False)
    monkeypatch.setattr(gb, "_gbrain_cmd", lambda: None)

    def boom(*a, **k):
        raise AssertionError("触碰了已摘除的 GBrain 二进制")

    for fn in ("search", "overview", "stats", "doctor", "get_page",
               "import_brain", "git_status", "ensure_db_ready"):
        monkeypatch.setattr(gb, fn, boom)
    return gb


def test_citations_use_the_agent_not_gbrain(monkeypatch):
    """对话引用检索此前**完全没有 ivyea 分支** —— agent 明明是前门、数据也早已
    整批导入，每次对话还要起一次外部二进制查一遍，agent 内部又查一遍。"""
    from app.services import brain_chat_service as bc
    _kill_gbrain(monkeypatch)
    monkeypatch.setattr(bc, "ivyea_chat_available", lambda: True)
    monkeypatch.setattr(bc, "ia_search", lambda q, m="search", limit=12: {
        "items": [{"slug": "k1", "title": "否词", "snippet": "s", "category": "amazon_ads"}]})

    cites = bc._search_citations("广告怎么优化否词")
    assert [c["slug"] for c in cites] == ["k1"]


def test_citations_respect_category_scope(monkeypatch):
    from app.services import brain_chat_service as bc
    _kill_gbrain(monkeypatch)
    monkeypatch.setattr(bc, "ivyea_chat_available", lambda: True)
    monkeypatch.setattr(bc, "ia_search", lambda q, m="search", limit=12: {
        "items": [{"slug": "a", "category": "amazon_ads"},
                  {"slug": "b", "category": "policies"}]})

    assert [c["slug"] for c in bc._search_citations("x", "amazon_ads")] == ["a"]
    assert bc._search_citations("x", "不存在") == []


def test_citations_return_empty_when_nothing_available(monkeypatch):
    """agent 不可用 + 没装 GBrain → 干净地返回空，不抛错、不留"检索失败"假引用。"""
    from app.services import brain_chat_service as bc
    _kill_gbrain(monkeypatch)
    monkeypatch.setattr(bc, "ivyea_chat_available", lambda: False)
    assert bc._search_citations("随便问点什么") == []


def test_legacy_gbrain_category_is_recovered():
    """从 GBrain 导入的卡片在 agent 里统一是 legacy_gbrain，原分类藏在
    source_url / path 里。不还原的话按分类过滤会把历史卡片全滤掉。"""
    from app.services import brain_chat_service as bc
    assert bc._legacy_category(
        {"source_url": "gbrain://amazon/ads/2026-06-10-x", "category": "legacy_gbrain"}) == "amazon"
    assert bc._legacy_category(
        {"path": "user/imported/gbrain/ops/notes/a.md", "category": "legacy_gbrain"}) == "ops"
    assert bc._legacy_category({"category": "amazon_ads"}) == "amazon_ads"


def test_reindex_prefers_the_agent(monkeypatch):
    from app.services import brain_chat_service as bc
    _kill_gbrain(monkeypatch)
    monkeypatch.setattr(bc, "ivyea_chat_available", lambda: True)
    import app.services.ivyea_agent_service as ia_mod
    monkeypatch.setattr(ia_mod, "retrieval_sync", lambda: {"ok": True})
    status, raw = bc.reindex_after_save()
    assert status == "ok" and "ivyea-agent" in raw


def test_reindex_without_any_backend_is_not_a_failure(monkeypatch):
    """文件已经落到 BRAIN_ROOT，agent 下次同步会捡到 —— 不该报成失败吓用户。"""
    from app.services import brain_chat_service as bc
    _kill_gbrain(monkeypatch)
    monkeypatch.setattr(bc, "ivyea_chat_available", lambda: False)
    assert bc.reindex_after_save() == ("skipped", "")


def test_missing_page_is_a_404_not_a_gbrain_fallback(monkeypatch):
    """"卡片不存在"是 agent 的**正常答复**，不是故障 —— 不能拿它当理由去回退
    已被摘除的 GBrain。"""
    from fastapi import HTTPException
    from app.routers import brain as B
    from app.services import ivyea_agent_service as ia_mod
    _kill_gbrain(monkeypatch)
    monkeypatch.setattr(B, "_ivyea_front_door", lambda: True)

    def not_found(_slug):
        raise ia_mod.IvyeaAgentNotFound("IvyeaAgent HTTP 404: 知识卡不存在")

    monkeypatch.setattr(B, "_ia_page", not_found)
    with pytest.raises(HTTPException) as ei:
        B.get_page("nope")
    assert ei.value.status_code == 404
    assert "页面不存在" in str(ei.value.detail)   # 不透出 URL 编码的原始报错
