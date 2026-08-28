"""IvyeaAgent bridge routes inside IvyeaOps."""
from __future__ import annotations

import importlib
import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException


_ORIGIN = "https://test.example.com"
@pytest.fixture
def ctx(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("IVYEA_OPS_SECRET", "test-secret")
    monkeypatch.setenv("IVYEA_OPS_ALLOWED_ORIGINS", _ORIGIN)
    monkeypatch.setenv("AGENTS_DB_PATH", str(tmp_path / "agents.db"))
    monkeypatch.setenv("IVYEA_AGENT_URL", "127.0.0.1:9876")
    monkeypatch.setenv("IVYEA_AGENT_TOKEN", "secret-token")

    from app.core import config as cfg_mod
    importlib.reload(cfg_mod)
    from app.core import security as sec_mod
    importlib.reload(sec_mod)
    from app.services import ivyea_agent_service as svc_mod
    importlib.reload(svc_mod)
    from app.routers import ivyea_agent as router_mod
    importlib.reload(router_mod)

    return svc_mod, router_mod


class FakeRequest:
    base_url = "http://ops.test/"


def test_status_reports_local_agent_without_leaking_token(ctx, monkeypatch):
    svc, router = ctx
    monkeypatch.setattr(svc, "request_json", lambda *a, **k: {"ok": True, "name": "ivyea-agent"})

    body = router.status()
    assert body["available"] is True
    assert body["base_url"] == "http://127.0.0.1:9876"
    assert body["token_configured"] is True
    assert "secret-token" not in str(body)


def test_bootstrap_manifest_and_provider_routes_proxy(ctx, monkeypatch):
    svc, router = ctx
    monkeypatch.setattr(svc, "bootstrap", lambda: {"ok": True, "urls": {"manifest": "http://x/v1/manifest"}})
    monkeypatch.setattr(svc, "manifest", lambda: {"ok": True, "name": "ivyea-agent"})
    monkeypatch.setattr(svc, "model_providers", lambda: {"ok": True, "providers": [{"id": "openai"}]})
    seen = {}
    monkeypatch.setattr(svc, "provider_models", lambda provider_id, refresh=False: {
        "ok": True,
        "catalog": {"provider_id": provider_id, "refresh": refresh, "models": ["m"]},
    })
    def fake_probe(provider_id, payload):
        seen["probe"] = (provider_id, payload)
        return {"ok": True}

    monkeypatch.setattr(svc, "provider_probe", fake_probe)

    assert router.bootstrap()["urls"]["manifest"].endswith("/v1/manifest")
    assert router.manifest()["name"] == "ivyea-agent"
    assert router.model_providers()["providers"][0]["id"] == "openai"
    assert router.provider_models("openai", refresh=True)["catalog"]["refresh"] is True
    assert router.provider_probe("openai", router.ProviderProbeBody(model="m", timeout=3))["ok"] is True
    assert seen["probe"] == ("openai", {"model": "m", "timeout": 3.0})


def test_chat_routes_forward_payload(ctx, monkeypatch):
    svc, router = ctx
    seen = {}
    def fake_chat(payload):
        seen["chat"] = payload
        return {"ok": True, "text": "hi"}

    def fake_create(payload):
        seen["create"] = payload
        return {"ok": True, "session": {"id": "new"}}

    monkeypatch.setattr(svc, "chat", fake_chat)
    monkeypatch.setattr(svc, "chat_sessions", lambda limit=20: {"ok": True, "sessions": [{"id": "s1"}], "limit": limit})
    # 详情按轮分页（agent ≥ v1.10.3）：桩要跟着接住 turns / before，
    # 顺手把它们记下来，下面断言路由确实把分页参数传下去了。
    monkeypatch.setattr(svc, "chat_session", lambda session_id, turns=8, before=None: {
        "ok": True, "session": {"id": session_id}, "turns": turns, "before": before})
    monkeypatch.setattr(svc, "chat_create", fake_create)
    monkeypatch.setattr(svc, "ensure_available", lambda: {"available": True})
    monkeypatch.setattr(svc, "chat_stream", lambda payload: iter([b"event: final\ndata: {\"ok\": true, \"text\": \"hi\"}\n\n"]))

    assert router.chat(router.ChatBody(message="你好", session_id="s1", max_steps=6), FakeRequest())["ok"] is True
    assert seen["chat"]["message"] == "你好"
    assert seen["chat"]["session_id"] == "s1"
    assert seen["chat"]["max_steps"] == 6
    assert seen["chat"]["inject_retrieval"] is True
    assert seen["chat"]["ops_bridge"]["base_url"] == "http://ops.test/api/ivyea-agent-bridge"
    assert seen["chat"]["ops_bridge"]["token"]
    assert router.chat_sessions(limit=3)["sessions"][0]["id"] == "s1"
    assert router.chat_session("s1")["session"]["id"] == "s1"
    paged = router.chat_session("s1", turns=5, before=7)
    assert (paged["turns"], paged["before"]) == (5, 7)
    assert router.chat_create(router.ChatSessionCreateBody(title="新会话"))["session"]["id"] == "new"
    assert seen["create"]["title"] == "新会话"
    streamed = router.chat_stream(router.ChatBody(message="stream"), FakeRequest())
    assert streamed.media_type == "text/event-stream"


def test_retrieval_sync_and_embeddings_proxy(ctx, monkeypatch):
    svc, router = ctx
    monkeypatch.setattr(svc, "retrieval_status", lambda: {"ok": True, "index": {"needs_rebuild": False}})
    monkeypatch.setattr(svc, "retrieval_embeddings", lambda: {"ok": True, "embeddings": {"active_backend": "hash"}})
    monkeypatch.setattr(svc, "retrieval_sync", lambda: {"ok": True, "changed": False})

    assert router.retrieval_status()["index"]["needs_rebuild"] is False
    assert router.retrieval_embeddings()["embeddings"]["active_backend"] == "hash"
    assert router.retrieval_sync()["changed"] is False


def test_knowledge_update_bridge_paths(ctx, monkeypatch):
    svc, _router = ctx
    calls = []

    def fake_request(method, path, payload=None, **_kwargs):
        calls.append((method, path, payload))
        return {"ok": True, "path": path}

    monkeypatch.setattr(svc, "request_json", fake_request)
    monkeypatch.setattr(svc, "_token", lambda: "shared-test-token")

    assert svc.knowledge_watchlist()["path"] == "/v1/knowledge/watchlist"
    assert svc.knowledge_governance()["path"] == "/v1/knowledge/governance"
    assert svc.knowledge_coverage()["path"] == "/v1/knowledge/coverage"
    assert svc.knowledge_freshness()["path"] == "/v1/knowledge/freshness"
    assert svc.knowledge_quality()["path"] == "/v1/knowledge/quality"
    assert svc.knowledge_changes(limit=5, status="pending")["path"] == "/v1/knowledge/changes?limit=5&status=pending"
    assert svc.knowledge_reviews(limit=6, event_id="chg-1")["path"] == "/v1/knowledge/reviews?limit=6&event_id=chg-1"
    assert svc.knowledge_publications(limit=7)["path"] == "/v1/knowledge/publications?limit=7"
    assert svc.knowledge_versions("user.card", limit=8)["path"] == "/v1/knowledge/versions?limit=8&card_id=user.card"
    assert svc.knowledge_version_rollback({"card_id": "user.card", "version_id": "kv-1"})["path"] == "/v1/knowledge/versions/rollback"
    assert svc.knowledge_evidence(limit=9)["path"] == "/v1/knowledge/evidence?limit=9"
    assert svc.knowledge_evidence_schema()["path"] == "/v1/knowledge/evidence/schema"
    assert svc.knowledge_evidence_draft({"kind": "tax_report"})["path"] == "/v1/knowledge/evidence/draft"
    assert svc.knowledge_evidence_apply({"kind": "tax_report", "confirm": True})["path"] == "/v1/knowledge/evidence/apply"
    assert svc.knowledge_review_change({"event_id": "chg-1", "decision": "approved"})["path"] == "/v1/knowledge/changes/review"
    review_payload = next(payload for method, path, payload in calls if path == "/v1/knowledge/changes/review")
    assert "identity_assertion" not in review_payload  # reviewer source is required before signing
    assert svc.knowledge_review_change({
        "event_id": "chg-1", "decision": "approved", "reviewer": "admin",
        "reviewer_source": "ops_authenticated_admin", "identity_verified": True,
    })["path"] == "/v1/knowledge/changes/review"
    signed_review = [payload for method, path, payload in calls if path == "/v1/knowledge/changes/review"][-1]
    assert len(signed_review["identity_assertion"]["signature"]) == 64
    assert "identity_verified" not in signed_review
    assert svc.knowledge_change_packet("chg-1", "card.1")["path"] == "/v1/knowledge/changes/chg-1/packet?card_id=card.1"
    assert svc.knowledge_change_draft({"event_id": "chg-1", "body": "draft"})["path"] == "/v1/knowledge/changes/draft"
    assert svc.knowledge_change_apply({"event_id": "chg-1", "body": "draft", "confirm": True})["path"] == "/v1/knowledge/changes/apply"
    assert svc.knowledge_sync({"force": True})["path"] == "/v1/knowledge/sync"
    assert svc.knowledge_cards(limit=5)["path"] == "/v1/knowledge/cards?limit=5"
    assert svc.knowledge_search("否词", limit=4)["path"].startswith("/v1/knowledge/search?")
    assert svc.knowledge_files(limit=9)["path"] == "/v1/knowledge/files?limit=9"
    assert svc.knowledge_uploads(limit=7)["path"] == "/v1/knowledge/uploads?limit=7"
    assert svc.knowledge_file("uploads/a.md")["path"].startswith("/v1/knowledge/file?")
    assert svc.knowledge_delete_file("uploads/a.md")["path"].startswith("/v1/knowledge/file?")
    assert svc.knowledge_update_draft({"body": "draft"})["path"] == "/v1/knowledge/update/draft"
    assert svc.knowledge_update_apply({"body": "apply", "confirm": True})["path"] == "/v1/knowledge/update/apply"
    assert svc.knowledge_upload({"filename": "a.md", "content_base64": "YQ=="})["path"] == "/v1/knowledge/upload"
    assert svc.knowledge_upload_apply({"upload_id": "up1", "confirm": True})["path"] == "/v1/knowledge/uploads/apply"
    assert svc.knowledge_import_directory({"root": "/tmp/brain", "confirm": False, "max_files": 3})["path"] == "/v1/knowledge/import-directory"
    assert calls[0] == ("GET", "/v1/knowledge/watchlist", None)
    assert calls[-3] == ("POST", "/v1/knowledge/upload", {"filename": "a.md", "content_base64": "YQ=="})
    assert calls[-2] == ("POST", "/v1/knowledge/uploads/apply", {"upload_id": "up1", "confirm": True})
    assert calls[-1][0] == "POST"
    assert calls[-1][1] == "/v1/knowledge/import-directory"
    assert calls[-1][2]["root"] == "/tmp/brain"
    assert calls[-1][2]["namespace"] == "gbrain"


def test_knowledge_update_routes_forward_payload(ctx, monkeypatch):
    svc, router = ctx
    seen = {}
    monkeypatch.setattr(svc, "knowledge_watchlist", lambda: {"ok": True, "sources": [{"id": "amazon_ads.sponsored_products"}]})
    monkeypatch.setattr(svc, "knowledge_cards", lambda limit=200: {"ok": True, "cards": [{"id": "c"}], "limit": limit})
    monkeypatch.setattr(svc, "knowledge_search", lambda q, limit=8: {"ok": True, "results": [{"id": q}], "limit": limit})
    monkeypatch.setattr(svc, "knowledge_files", lambda limit=500: {"ok": True, "uploads": [], "cards": [], "limit": limit})
    monkeypatch.setattr(svc, "knowledge_uploads", lambda limit=50: {"ok": True, "uploads": [{"id": "up"}], "limit": limit})
    monkeypatch.setattr(svc, "knowledge_file", lambda path: {"ok": True, "file": {"path": path}})
    monkeypatch.setattr(svc, "knowledge_delete_file", lambda path: {"ok": True, "path": path})

    def fake_draft(payload):
        seen["draft"] = payload
        return {"ok": True, "draft": {"action": "create", "card_id": payload["id"]}}

    def fake_apply(payload):
        seen["apply"] = payload
        return {"ok": True, "result": {"applied": payload["confirm"]}}

    monkeypatch.setattr(svc, "knowledge_update_draft", fake_draft)
    monkeypatch.setattr(svc, "knowledge_update_apply", fake_apply)
    monkeypatch.setattr(svc, "knowledge_import_directory", lambda payload: {
        "ok": True,
        "import": {"summary": {"candidate_files": payload["max_files"], "imported": 0}, "confirm": payload["confirm"]},
    })

    assert router.knowledge_watchlist()["sources"][0]["id"] == "amazon_ads.sponsored_products"
    assert router.knowledge_cards(limit=2)["limit"] == 2
    assert router.knowledge_search(q="否词", limit=3)["results"][0]["id"] == "否词"
    assert router.knowledge_files(limit=4)["limit"] == 4
    assert router.knowledge_uploads(limit=5)["uploads"][0]["id"] == "up"
    assert router.knowledge_file(path="uploads/a.md")["file"]["path"] == "uploads/a.md"
    assert router.knowledge_delete_file(path="uploads/a.md")["path"] == "uploads/a.md"
    body = router.KnowledgeUpdateBody(
        id="user.ops-note",
        title="Ops note",
        body="知识更新内容",
        source_url="https://advertising.amazon.com/solutions/products/sponsored-products",
        source_type="official",
        tags=["budget"],
        confirm=True,
        rebuild=False,
    )
    assert router.knowledge_update_draft(body)["draft"]["card_id"] == "user.ops-note"
    assert router.knowledge_update_apply(body)["result"]["applied"] is True
    migrated = router.knowledge_import_directory(router.KnowledgeImportDirectoryBody(max_files=6, confirm=False))
    assert migrated["import"]["summary"]["candidate_files"] == 6
    assert migrated["import"]["confirm"] is False
    assert seen["draft"]["tags"] == ["budget"]
    assert seen["apply"]["confirm"] is True
    assert seen["apply"]["rebuild"] is False


def test_knowledge_governance_routes_forward_payload(ctx, monkeypatch):
    svc, router = ctx
    seen = {}
    monkeypatch.setattr(svc, "knowledge_governance", lambda: {"ok": True, "summary": {"pending_reviews": 2}})
    monkeypatch.setattr(svc, "knowledge_coverage", lambda: {"ok": True, "coverage": {"requirements": []}})
    monkeypatch.setattr(svc, "knowledge_freshness", lambda: {"ok": True, "freshness": {"sources": []}})
    monkeypatch.setattr(svc, "knowledge_quality", lambda: {"ok": True, "quality": {"summary": {"cases": 15}}})
    monkeypatch.setattr(svc, "knowledge_changes", lambda limit=50, status="": {
        "ok": True, "summary": {"changes": limit}, "status": status, "changes": [],
    })
    monkeypatch.setattr(svc, "knowledge_reviews", lambda limit=100, event_id="": {
        "ok": True, "summary": {"reviews": limit}, "event_id": event_id, "reviews": [],
    })
    monkeypatch.setattr(svc, "knowledge_publications", lambda limit=100, event_id="": {
        "ok": True, "summary": {"publications": limit}, "event_id": event_id, "publications": [],
    })
    monkeypatch.setattr(svc, "knowledge_versions", lambda card_id="", limit=100: {
        "ok": True, "summary": {"versions": limit}, "card_id": card_id, "versions": [],
    })
    monkeypatch.setattr(svc, "knowledge_evidence", lambda limit=100: {
        "ok": True, "summary": {"evidence": limit}, "evidence": [],
    })
    monkeypatch.setattr(svc, "knowledge_evidence_schema", lambda: {"ok": True, "schema": {"type": "object"}})
    monkeypatch.setattr(svc, "knowledge_change_packet", lambda event_id, card_id="": {
        "ok": True, "packet": {"event": {"event_id": event_id}, "target": {"id": card_id}},
    })

    def review(payload):
        seen["review"] = payload
        return {"ok": True, "reviewed": payload["confirm"]}

    def draft(payload):
        seen["draft"] = payload
        return {"ok": True, "draft_ready": bool(payload["body"])}

    def apply(payload):
        seen["apply"] = payload
        return {"ok": True, "applied": payload["confirm"]}

    def sync(payload):
        seen["sync"] = payload
        return {"ok": True, "summary": {"selected": len(payload["source_ids"])}}

    def rollback(payload):
        seen["rollback"] = payload
        return {"ok": True, "rolled_back": payload["confirm"]}

    def evidence_draft(payload):
        seen["evidence_draft"] = payload
        return {"ok": True, "draft_ready": True}

    def evidence_apply(payload):
        seen["evidence_apply"] = payload
        return {"ok": True, "applied": payload.get("confirm") is True}

    monkeypatch.setattr(svc, "knowledge_review_change", review)
    monkeypatch.setattr(svc, "knowledge_change_draft", draft)
    monkeypatch.setattr(svc, "knowledge_change_apply", apply)
    monkeypatch.setattr(svc, "knowledge_sync", sync)
    monkeypatch.setattr(svc, "knowledge_version_rollback", rollback)
    monkeypatch.setattr(svc, "knowledge_evidence_draft", evidence_draft)
    monkeypatch.setattr(svc, "knowledge_evidence_apply", evidence_apply)

    assert router.knowledge_governance()["summary"]["pending_reviews"] == 2
    assert router.knowledge_coverage()["coverage"]["requirements"] == []
    assert router.knowledge_freshness()["freshness"]["sources"] == []
    assert router.knowledge_quality()["quality"]["summary"]["cases"] == 15
    assert router.knowledge_changes(limit=8, status="pending")["status"] == "pending"
    assert router.knowledge_reviews(limit=9, event_id="chg-1")["event_id"] == "chg-1"
    assert router.knowledge_publications(limit=10, event_id="")["summary"]["publications"] == 10
    assert router.knowledge_versions(card_id="user.card", limit=11)["summary"]["versions"] == 11
    assert router.knowledge_evidence(limit=12)["summary"]["evidence"] == 12
    assert router.knowledge_evidence_schema()["schema"]["type"] == "object"
    assert router.knowledge_change_packet("chg-1", "card-1")["packet"]["target"]["id"] == "card-1"
    review_body = router.KnowledgeReviewBody(
        event_id="chg-1", decision="approved", reviewer="qa", note="verified", confirm=True,
    )
    assert router.knowledge_review_change(review_body, _admin="admin")["reviewed"] is True
    rollback_body = router.KnowledgeVersionRollbackBody(
        card_id="user.card", version_id="kv-1", confirm=True, rebuild=False,
    )
    assert router.knowledge_version_rollback(rollback_body, _admin="admin")["rolled_back"] is True
    assert router.knowledge_evidence_draft({"kind": "tax_report"}, _admin="admin")["draft_ready"] is True
    assert router.knowledge_evidence_apply(
        {"kind": "tax_report", "confirm": True}, _admin="admin",
    )["applied"] is True
    draft_body = router.KnowledgeChangeDraftBody(event_id="chg-1", card_id="card-1", body="draft")
    assert router.knowledge_change_draft(draft_body, _admin="admin")["draft_ready"] is True
    apply_body = router.KnowledgeChangeApplyBody(
        event_id="chg-1", card_id="card-1", body="draft", confirm=True, rebuild=False,
    )
    assert router.knowledge_change_apply(apply_body, _admin="admin")["applied"] is True
    assert router.knowledge_sync(router.KnowledgeSyncBody(source_ids=["source-1"], force=True), _admin="admin")["ok"] is True
    assert seen["review"]["decision"] == "approved"
    assert seen["review"]["reviewer"] == "admin"
    assert seen["review"]["reviewer_source"] == "ops_authenticated_admin"
    assert "identity_verified" not in seen["review"]
    assert seen["rollback"]["actor"] == "admin"
    assert seen["rollback"]["actor_source"] == "ops_authenticated_admin"
    assert seen["evidence_draft"]["actor"] == "admin"
    assert seen["evidence_apply"]["actor_source"] == "ops_authenticated_admin"
    assert seen["apply"]["rebuild"] is False
    assert seen["sync"]["source_ids"] == ["source-1"]


def test_knowledge_upload_route_encodes_file(ctx, monkeypatch):
    svc, router = ctx
    seen = {}

    class FakeUpload:
        filename = "note.md"

        async def read(self, _limit):
            return b"# Note\n\nupload body"

    def fake_upload(payload):
        seen.update(payload)
        return {"ok": True, "upload": {"id": "up1"}}

    monkeypatch.setattr(svc, "knowledge_upload", fake_upload)
    monkeypatch.setattr(svc, "knowledge_upload_apply", lambda payload: {"ok": True, "result": {"applied": payload["confirm"]}})

    try:
        result = asyncio.run(router.knowledge_upload(
            file=FakeUpload(),
            title="Note",
            id="user.note",
            source_url="https://example.com",
            source_type="official",
            confidence="high",
            license="public_summary",
            tags="a,b",
            confirm=False,
            rebuild=False,
        ))
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())
    assert result["upload"]["id"] == "up1"
    assert seen["filename"] == "note.md"
    assert seen["content_base64"]
    assert seen["tags"] == ["a", "b"]
    assert seen["rebuild"] is False
    assert router.knowledge_upload_apply(router.KnowledgeUploadApplyBody(upload_id="up1", confirm=True))["result"]["applied"] is True


def test_code_bundle_validates_and_forwards_payload(ctx, monkeypatch, tmp_path):
    svc, router = ctx
    seen = {}

    def fake_bundle(payload):
        seen.update(payload)
        return {"ok": True, "bundle": {"mode": "read-only-task-bundle"}}

    monkeypatch.setattr(svc, "code_bundle", fake_bundle)
    monkeypatch.setattr(svc, "code_apply_loop", lambda payload: {
        "ok": True,
        "run": {"mode": "execute" if payload["execute"] else "dry-run"},
    })
    body = router.CodeBundleBody(
        root=str(tmp_path),
        goal="fix tests",
        test_output="failed",
        limit=4,
    )
    result = router.code_bundle(body)
    assert result["bundle"]["mode"] == "read-only-task-bundle"
    assert seen == {"root": str(tmp_path), "goal": "fix tests", "test_output": "failed", "limit": 4}

    loop = router.code_apply_loop(router.CodeApplyLoopBody(
        root=str(tmp_path),
        spec={"ops": []},
        execute=False,
        persist=False,
    ))
    assert loop["run"]["mode"] == "dry-run"


def test_service_management_routes_forward_payload(ctx, monkeypatch):
    svc, router = ctx
    seen = {}
    monkeypatch.setattr(svc, "service_status", lambda host="", port=None: {"ok": True, "service": {"host": host, "port": port}})
    monkeypatch.setattr(svc, "service_logs", lambda lines=80: {"ok": True, "logs": {"lines": ["x"], "limit": lines}})
    def fake_start(payload):
        seen["start"] = payload
        return {"ok": True}

    def fake_stop(payload):
        seen["stop"] = payload
        return {"ok": True}

    def fake_autostart(payload):
        seen["autostart"] = payload
        return {"ok": True}

    monkeypatch.setattr(svc, "service_start", fake_start)
    monkeypatch.setattr(svc, "service_stop", fake_stop)
    monkeypatch.setattr(svc, "service_autostart", fake_autostart)

    assert router.service_status(host="127.0.0.1", port=8765)["service"]["port"] == 8765
    assert router.service_logs(lines=3)["logs"]["limit"] == 3
    assert router.service_start(router.ServiceStartBody(host="127.0.0.1", port=9876, api_token="secret"))["ok"] is True
    assert router.service_stop(router.ServiceStopBody(force=True))["ok"] is True
    assert router.service_autostart(router.ServiceAutostartBody(port=9876))["ok"] is True
    assert seen["start"]["port"] == 9876
    assert seen["start"]["api_token"] == "secret"
    assert seen["stop"]["force"] is True
    assert seen["autostart"]["port"] == 9876


def test_model_config_sync_forwards_settings(ctx, monkeypatch):
    svc, _router = ctx
    calls = []

    def fake_request(method, path, payload=None, **_kwargs):
        calls.append((method, path, payload))
        return {"ok": True, "model": {"model": payload["model"]}}

    monkeypatch.setattr(svc, "request_json", fake_request)
    result = svc.sync_model_settings({
        "ivyea_agent_provider": "openrouter",
        "ivyea_agent_model": "anthropic/claude-sonnet-4.6",
        "ivyea_agent_api_key": "sk-secret",
        "ivyea_agent_base_url": "",
    }, force=True)

    assert result["ok"] is True
    assert calls == [("POST", "/v1/model/configure", {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4.6",
        "api_key": "sk-secret",
    })]


def test_ops_tools_permission_filter_and_bridge_token(ctx):
    _svc, router = ctx
    from app.core.security import current_user
    from app.services import ivyea_ops_tools

    current_user.set({"id": 7, "role": "user", "email": "u@example.com", "permissions": ["listing"]})
    tools = ivyea_ops_tools.list_tools()
    names = {row["name"] for row in tools["tools"]}
    assert "listing_projects" in names
    assert "market_history" in names  # base module
    assert "monitor_snapshot" not in names

    token = ivyea_ops_tools.issue_bridge_token()
    current_user.set(None)
    listed = router.bridge_tools(router.OpsToolsListBody(module="listing"), authorization=f"Bearer {token}")
    assert listed["principal"]["email"] == "u@example.com"
    assert {row["name"] for row in listed["tools"]} >= {"listing_projects"}


def test_unavailable_agent_maps_to_503(ctx, monkeypatch):
    svc, router = ctx
    monkeypatch.setattr(svc, "bootstrap", lambda: (_ for _ in ()).throw(svc.IvyeaAgentUnavailable("down")))

    with pytest.raises(HTTPException) as exc:
        router.bootstrap()
    assert exc.value.status_code == 503
    assert "IvyeaAgent 不可用" in exc.value.detail


def test_service_availability_handles_unreachable(ctx, monkeypatch):
    svc, _router = ctx
    monkeypatch.setattr(svc, "request_json", lambda *a, **k: (_ for _ in ()).throw(svc.IvyeaAgentUnavailable("offline")))

    body = svc.availability()
    assert body["ok"] is False
    assert body["available"] is False
    assert "offline" in body["error"]


# ── 轮次跑着的时候还能说话：追加指令 / 选项卡 / 活轮清单 ──────────────────────

def _own_session(router, monkeypatch, allowed: set[str]):
    """把归属校验按 allowed 名单短路 —— 这几条路由的第一道闸就是它。"""
    monkeypatch.setattr(router.console_sessions, "can_access",
                        lambda sid, principal, is_admin: sid in allowed)


def test_inject_forwards_to_the_running_turn(ctx, monkeypatch):
    svc, router = ctx
    seen = {}
    def fake_inject(payload):
        seen["p"] = payload
        return {"ok": True, "accepted": True}

    monkeypatch.setattr(svc, "chat_inject", fake_inject)
    _own_session(router, monkeypatch, {"s1"})

    out = router.chat_inject(router.ChatInjectBody(session_id="s1", text="顺便把标题也改了"),
                             info={"user": "u1", "is_admin": False})
    assert out["accepted"] is True
    assert seen["p"] == {"session_id": "s1", "text": "顺便把标题也改了"}


def test_inject_refuses_someone_elses_session(ctx, monkeypatch):
    svc, router = ctx
    monkeypatch.setattr(svc, "chat_inject", lambda payload: {"ok": True, "accepted": True})
    _own_session(router, monkeypatch, set())
    with pytest.raises(HTTPException) as exc:
        router.chat_inject(router.ChatInjectBody(session_id="s1", text="x"),
                           info={"user": "u1", "is_admin": False})
    assert exc.value.status_code == 403


def test_question_requires_a_registered_owner(ctx, monkeypatch):
    """没登记过的选项卡一律当失效：多半是已超时按推荐项继续了。"""
    svc, router = ctx
    monkeypatch.setattr(svc, "chat_question", lambda payload: {"ok": True})
    with pytest.raises(HTTPException) as exc:
        router.chat_question(router.ChatQuestionBody(request_id="r1", answers={"q": "a"}),
                             user="u1")
    assert exc.value.status_code == 404


def test_question_forwards_for_the_owner_and_blocks_others(ctx, monkeypatch):
    svc, router = ctx
    seen = {}
    def fake_question(payload):
        seen["p"] = payload
        return {"ok": True}

    monkeypatch.setattr(svc, "chat_question", fake_question)
    router._remember_approval_owner("r1", "u1")

    assert router.chat_question(
        router.ChatQuestionBody(request_id="r1", answers={"投递语义": "真注入"}), user="u1")["ok"]
    assert seen["p"] == {"request_id": "r1", "answers": {"投递语义": "真注入"}}

    with pytest.raises(HTTPException) as exc:
        router.chat_question(router.ChatQuestionBody(request_id="r1", answers={"q": "a"}),
                             user="u2")
    assert exc.value.status_code == 403


def test_question_double_answer_reads_as_conflict_not_server_error(ctx, monkeypatch):
    """另一个页签先选了 —— 那是 409，不是"服务器坏了"。"""
    svc, router = ctx

    def boom(_payload):
        raise HTTPException(status_code=502, detail="IvyeaAgent HTTP 404")

    monkeypatch.setattr(svc, "chat_question", boom)
    router._remember_approval_owner("r2", "u1")
    with pytest.raises(HTTPException) as exc:
        router.chat_question(router.ChatQuestionBody(request_id="r2", answers={"q": "a"}),
                             user="u1")
    assert exc.value.status_code == 409


def test_live_sessions_filters_by_ownership(ctx, monkeypatch):
    svc, router = ctx
    monkeypatch.setattr(svc, "chat_live_sessions",
                        lambda: {"ok": True, "sessions": [{"id": "mine"}, {"id": "theirs"}]})
    _own_session(router, monkeypatch, {"mine"})
    out = router.chat_live_sessions(info={"user": "u1", "is_admin": False})
    assert out["available"] is True
    assert [r["id"] for r in out["sessions"]] == ["mine"]


def test_live_sessions_says_unavailable_instead_of_empty(ctx, monkeypatch):
    """agent 没起时必须说"问不到"——回空列表会让正在跑的会话看着像已经停了。"""
    svc, router = ctx

    def down():
        raise HTTPException(status_code=503, detail="down")

    monkeypatch.setattr(svc, "chat_live_sessions", down)
    out = router.chat_live_sessions(info={"user": "u1", "is_admin": False})
    assert out == {"ok": True, "available": False, "sessions": []}


def test_interactive_flag_only_travels_when_the_caller_asks_for_it(ctx, monkeypatch):
    """选项卡是 opt-in：不带 interactive 的调用方（服务端自己读流的那几处）
    收到的 payload 与改动前逐字一致，agent 那边也就不会弹出没人能点的卡片。"""
    svc, router = ctx
    seen = {}

    def fake_chat(payload):
        seen.setdefault("payloads", []).append(payload)
        return {"ok": True, "text": "hi"}

    monkeypatch.setattr(svc, "chat", fake_chat)
    router.chat(router.ChatBody(message="你好"), FakeRequest())
    router.chat(router.ChatBody(message="你好", interactive=True), FakeRequest())

    plain, interactive = seen["payloads"]
    assert "interactive" not in plain
    assert interactive["interactive"] is True


def test_cancel_forwards_and_checks_ownership(ctx, monkeypatch):
    """「停止」现在是真停止：把中止请求转给 agent，且只能停自己的会话。"""
    svc, router = ctx
    seen = {}

    def fake_cancel(payload):
        seen["p"] = payload
        return {"ok": True, "cancelled": True}

    monkeypatch.setattr(svc, "chat_cancel", fake_cancel)
    _own_session(router, monkeypatch, {"s1"})
    out = router.chat_cancel(router.ChatCancelBody(session_id="s1"),
                             info={"user": "u1", "is_admin": False})
    assert out["cancelled"] is True
    assert seen["p"] == {"session_id": "s1"}

    _own_session(router, monkeypatch, set())
    with pytest.raises(HTTPException) as exc:
        router.chat_cancel(router.ChatCancelBody(session_id="s1"),
                           info={"user": "u1", "is_admin": False})
    assert exc.value.status_code == 403
