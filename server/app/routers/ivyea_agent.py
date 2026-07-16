"""IvyeaAgent integration endpoints for IvyeaOps."""
from __future__ import annotations

import base64
import threading as _threading
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.security import require_user, require_admin
from app.services import ivyea_agent_service as svc
from app.services import ivyea_ops_tools


router = APIRouter(dependencies=[Depends(require_user)])
bridge_router = APIRouter()


class CodeBundleBody(BaseModel):
    root: str = Field(..., min_length=1, max_length=1000)
    goal: str = Field(..., min_length=1, max_length=4000)
    test_output: str = Field(default="", max_length=20000)
    limit: int = Field(default=8, ge=1, le=30)


class CodeApplyLoopBody(BaseModel):
    root: str = Field(..., min_length=1, max_length=1000)
    spec: dict[str, Any] = Field(default_factory=dict)
    test_command: str = Field(default="", max_length=1000)
    execute: bool = False
    timeout: int = Field(default=120, ge=1, le=1800)
    persist: bool = True


class ServiceStartBody(BaseModel):
    host: str = Field(default="127.0.0.1", min_length=1, max_length=120)
    port: int = Field(default=8765, ge=1, le=65535)
    allow_remote: bool = False
    api_token: str = Field(default="", max_length=4000)
    wait: bool = True
    timeout: float = Field(default=10.0, ge=1.0, le=60.0)


class ServiceStopBody(BaseModel):
    timeout: float = Field(default=10.0, ge=1.0, le=60.0)
    force: bool = False


class ServiceAutostartBody(BaseModel):
    host: str = Field(default="127.0.0.1", min_length=1, max_length=120)
    port: int = Field(default=8765, ge=1, le=65535)


class ProviderProbeBody(BaseModel):
    model: str = Field(default="", max_length=200)
    timeout: float = Field(default=30.0, ge=1.0, le=120.0)


class ChatBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=20000)
    session_id: str = Field(default="", max_length=200)
    workspace: str = Field(default="", max_length=1000)
    asin: str = Field(default="", max_length=80)
    ops_context: dict[str, Any] = Field(default_factory=dict)
    # 0 = 不限定，交给 agent serve 的 config 默认（chat_max_tool_steps，默认 200）。
    # 旧默认 12/上限 80 把 serve 端预算压死，复杂运营任务动辄撞"工具步数上限"。
    max_steps: int = Field(default=0, ge=0, le=400)
    persist: bool = True
    plan_mode: bool = True
    inject_retrieval: bool = True


class ChatSessionCreateBody(BaseModel):
    title: str = Field(default="", max_length=200)
    message: str = Field(default="", max_length=2000)


class KnowledgeUpdateBody(BaseModel):
    id: str = Field(default="", max_length=200)
    card_id: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=50000)
    source_url: str = Field(default="", max_length=2000)
    source_type: str = Field(default="user", max_length=80)
    confidence: str = Field(default="", max_length=80)
    license: str = Field(default="user_supplied", max_length=200)
    tags: list[str] = Field(default_factory=list)
    confirm: bool = False
    rebuild: bool = True


class KnowledgeUploadApplyBody(BaseModel):
    upload_id: str = Field(..., min_length=1, max_length=200)
    confirm: bool = False
    rebuild: bool = True


class KnowledgeImportDirectoryBody(BaseModel):
    root: str = Field(default="", max_length=1000)
    namespace: str = Field(default="gbrain", min_length=1, max_length=80)
    confirm: bool = False
    rebuild: bool = True
    max_files: int = Field(default=1000, ge=1, le=5000)
    max_file_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=25 * 1024 * 1024)


class KnowledgeReviewBody(BaseModel):
    event_id: str = Field(..., min_length=1, max_length=120)
    decision: str = Field(..., pattern="^(approved|rejected|superseded)$")
    reviewer: str = Field(default="local-operator", max_length=80)
    note: str = Field(default="", max_length=1000)
    confirm: bool = False


class KnowledgeVersionRollbackBody(BaseModel):
    card_id: str = Field(..., min_length=1, max_length=240)
    version_id: str = Field(..., min_length=1, max_length=120)
    confirm: bool = False
    rebuild: bool = True


class KnowledgeChangeDraftBody(BaseModel):
    event_id: str = Field(..., min_length=1, max_length=120)
    card_id: str = Field(default="", max_length=240)
    new_card_id: str = Field(default="", max_length=240)
    title: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=500000)


class KnowledgeChangeApplyBody(KnowledgeChangeDraftBody):
    confirm: bool = False
    rebuild: bool = True


class KnowledgeSyncBody(BaseModel):
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    force: bool = False


class OpsToolsListBody(BaseModel):
    module: str = Field(default="", max_length=80)
    query: str = Field(default="", max_length=500)
    context: dict[str, Any] = Field(default_factory=dict)


class OpsToolCallBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


def _call(fn, *args, **kwargs) -> dict[str, Any]:
    try:
        return fn(*args, **kwargs)
    except svc.IvyeaAgentUnavailable as exc:
        status = svc.ensure_available()
        if status.get("available"):
            try:
                return fn(*args, **kwargs)
            except svc.IvyeaAgentUnavailable as retry_exc:
                raise HTTPException(status_code=503, detail=f"IvyeaAgent 不可用：{retry_exc}") from retry_exc
            except svc.IvyeaAgentError as retry_exc:
                raise HTTPException(status_code=502, detail=str(retry_exc)) from retry_exc
        raise HTTPException(status_code=503, detail=f"IvyeaAgent 不可用：{exc}") from exc
    except svc.IvyeaAgentError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _payload(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _bridge_base_url(request: Request) -> str:
    import os
    configured = (os.getenv("IVYEA_OPS_BRIDGE_URL") or "").strip()
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/") + "/api/ivyea-agent-bridge"


def _with_ops_bridge(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    payload = dict(payload)
    payload["ops_bridge"] = {
        "base_url": _bridge_base_url(request),
        "token": ivyea_ops_tools.issue_bridge_token(),
    }
    ctx = payload.get("ops_context")
    if not isinstance(ctx, dict):
        payload["ops_context"] = {}
    return payload


@router.get("/status")
def status() -> dict[str, Any]:
    return svc.ensure_available()


@router.get("/version")
def agent_version() -> dict[str, Any]:
    """IvyeaAgent 版本卡片：当前版本 + GitHub 最新版 + 是否有更新（供系统配置显示/更新按钮）。
    frozen=True 时 IvyeaAgent 是打包进 IvyeaOpsServer.exe 的、随 IvyeaOps 一起更新（无法独立升级）。
    latest_known=False 表示这次没能连上 GitHub 查到最新版（别当成"已是最新"，要提示无法检查）。"""
    import sys as _sys
    installed = svc._installed_agent_version("") or svc.agent_version()
    latest = svc.latest_agent_version()
    return {
        "version": svc.agent_version(),
        "installed": installed,
        "latest": latest,
        "latest_known": bool(latest),
        "frozen": bool(getattr(_sys, "frozen", False)),
        "update_available": svc.agent_update_available(installed, latest),
        "available": svc.availability().get("available", False),
    }


_UPGRADE_LOCK = _threading.Lock()
_UPGRADE_STATE: dict[str, Any] = {"phase": "idle", "percent": 0, "before": "", "after": "",
                                  "ok": None, "note": "", "error": ""}


def _upgrade_worker() -> None:
    def _progress(phase: str, pct: int) -> None:
        _UPGRADE_STATE.update(phase=phase, percent=pct)
    try:
        res = svc.upgrade_agent(progress=_progress)
        _UPGRADE_STATE.update(phase="done" if res.get("ok") else "error", percent=100,
                              before=res.get("before", ""), after=res.get("after", ""),
                              ok=res.get("ok"), note=res.get("note", ""),
                              error=res.get("error", ""))
    except Exception as exc:  # noqa: BLE001
        _UPGRADE_STATE.update(phase="error", percent=100, ok=False, error=str(exc))


@router.post("/upgrade")
def upgrade(_admin: str = Depends(require_admin)) -> dict[str, Any]:
    """Start a background IvyeaAgent upgrade (pip -U from git + serve restart) and
    return immediately. The UI polls /ivyea-agent/upgrade/progress for a progress
    bar — no more blocking the request until a slow pip times out."""
    with _UPGRADE_LOCK:
        if _UPGRADE_STATE["phase"] in ("preparing", "downloading", "restarting"):
            return {"started": True, "already_running": True}
        _UPGRADE_STATE.update(phase="preparing", percent=0, before="", after="",
                              ok=None, note="", error="")
        _threading.Thread(target=_upgrade_worker, daemon=True, name="ivyea-agent-upgrade").start()
    return {"started": True}


@router.get("/upgrade/progress")
def upgrade_progress(_admin: str = Depends(require_admin)) -> dict[str, Any]:
    return dict(_UPGRADE_STATE)


@router.get("/bootstrap")
def bootstrap() -> dict[str, Any]:
    return _call(svc.bootstrap)


@router.get("/manifest")
def manifest() -> dict[str, Any]:
    return _call(svc.manifest)


def _chat_payload(body: ChatBody) -> dict[str, Any]:
    """max_steps<=0 时从 payload 里剔除，serve 端回落到 config 默认（200）。"""
    payload = _payload(body)
    if int(payload.get("max_steps") or 0) <= 0:
        payload.pop("max_steps", None)
    return payload


@router.post("/chat")
def chat(body: ChatBody, request: Request) -> dict[str, Any]:
    return _call(svc.chat, _with_ops_bridge(_chat_payload(body), request))


@router.post("/chat/stream")
def chat_stream(body: ChatBody, request: Request) -> StreamingResponse:
    status = svc.ensure_available()
    if not status.get("available"):
        raise HTTPException(status_code=503, detail=f"IvyeaAgent 不可用：{status.get('error') or '服务未连接'}")
    return StreamingResponse(
        svc.chat_stream(_with_ops_bridge(_chat_payload(body), request)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/chat/sessions")
def chat_sessions(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    return _call(svc.chat_sessions, limit)


@router.get("/chat/sessions/{session_id}")
def chat_session(session_id: str) -> dict[str, Any]:
    return _call(svc.chat_session, session_id)


@router.delete("/chat/sessions/{session_id}")
def chat_session_delete(session_id: str) -> dict[str, Any]:
    return _call(svc.chat_session_delete, session_id)


@router.post("/chat/sessions")
def chat_create(body: ChatSessionCreateBody) -> dict[str, Any]:
    return _call(svc.chat_create, _payload(body))


@router.get("/model/providers")
def model_providers() -> dict[str, Any]:
    return _call(svc.model_providers)


@router.get("/model/providers/{provider_id}/models")
def provider_models(provider_id: str, refresh: bool = False) -> dict[str, Any]:
    return _call(svc.provider_models, provider_id, refresh)


@router.post("/model/providers/{provider_id}/probe")
def provider_probe(provider_id: str, body: ProviderProbeBody) -> dict[str, Any]:
    return _call(svc.provider_probe, provider_id, {"model": body.model, "timeout": body.timeout})


@router.get("/ops-tools")
def ops_tools(module: str = "", query: str = "") -> dict[str, Any]:
    return ivyea_ops_tools.list_tools(module=module, query=query)


@router.post("/ops-tools/call")
async def ops_tool_call(body: OpsToolCallBody) -> dict[str, Any]:
    return await ivyea_ops_tools.call_tool(body.name, body.arguments)


@router.get("/retrieval/status")
def retrieval_status() -> dict[str, Any]:
    return _call(svc.retrieval_status)


@router.get("/retrieval/embeddings")
def retrieval_embeddings() -> dict[str, Any]:
    return _call(svc.retrieval_embeddings)


@router.post("/retrieval/sync")
def retrieval_sync() -> dict[str, Any]:
    return _call(svc.retrieval_sync)


@router.get("/knowledge/watchlist")
def knowledge_watchlist() -> dict[str, Any]:
    return _call(svc.knowledge_watchlist)


@router.get("/knowledge/governance")
def knowledge_governance() -> dict[str, Any]:
    return _call(svc.knowledge_governance)


@router.get("/knowledge/coverage")
def knowledge_coverage() -> dict[str, Any]:
    return _call(svc.knowledge_coverage)


@router.get("/knowledge/freshness")
def knowledge_freshness() -> dict[str, Any]:
    return _call(svc.knowledge_freshness)


@router.get("/knowledge/quality")
def knowledge_quality() -> dict[str, Any]:
    return _call(svc.knowledge_quality)


@router.get("/knowledge/changes")
def knowledge_changes(
    limit: int = Query(50, ge=1, le=500),
    status: str = Query(default="", pattern="^(|pending|approved|rejected|superseded)$"),
) -> dict[str, Any]:
    return _call(svc.knowledge_changes, limit, status)


@router.get("/knowledge/reviews")
def knowledge_reviews(
    limit: int = Query(100, ge=1, le=1000), event_id: str = Query(default="", max_length=120),
) -> dict[str, Any]:
    return _call(svc.knowledge_reviews, limit, event_id)


@router.get("/knowledge/publications")
def knowledge_publications(
    limit: int = Query(100, ge=1, le=1000), event_id: str = Query(default="", max_length=120),
) -> dict[str, Any]:
    return _call(svc.knowledge_publications, limit, event_id)


@router.get("/knowledge/versions")
def knowledge_versions(
    card_id: str = Query(default="", max_length=240), limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    return _call(svc.knowledge_versions, card_id, limit)


@router.get("/knowledge/evidence")
def knowledge_evidence(limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    return _call(svc.knowledge_evidence, limit)


@router.get("/knowledge/evidence/schema")
def knowledge_evidence_schema() -> dict[str, Any]:
    return _call(svc.knowledge_evidence_schema)


@router.get("/knowledge/changes/{event_id}/packet")
def knowledge_change_packet(
    event_id: str, card_id: str = Query(default="", max_length=240),
) -> dict[str, Any]:
    return _call(svc.knowledge_change_packet, event_id, card_id)


@router.post("/knowledge/changes/review")
def knowledge_review_change(
    body: KnowledgeReviewBody, _admin: str = Depends(require_admin),
) -> dict[str, Any]:
    payload = _payload(body)
    payload["reviewer"] = _admin
    payload["reviewer_source"] = "ops_authenticated_admin"
    return _call(svc.knowledge_review_change, payload)


@router.post("/knowledge/versions/rollback")
def knowledge_version_rollback(
    body: KnowledgeVersionRollbackBody, _admin: str = Depends(require_admin),
) -> dict[str, Any]:
    payload = _payload(body)
    payload["actor"] = _admin
    payload["actor_source"] = "ops_authenticated_admin"
    return _call(svc.knowledge_version_rollback, payload)


@router.post("/knowledge/evidence/draft")
def knowledge_evidence_draft(
    body: dict[str, Any], _admin: str = Depends(require_admin),
) -> dict[str, Any]:
    payload = dict(body)
    payload["actor"] = _admin
    payload["actor_source"] = "ops_authenticated_admin"
    return _call(svc.knowledge_evidence_draft, payload)


@router.post("/knowledge/evidence/apply")
def knowledge_evidence_apply(
    body: dict[str, Any], _admin: str = Depends(require_admin),
) -> dict[str, Any]:
    payload = dict(body)
    payload["actor"] = _admin
    payload["actor_source"] = "ops_authenticated_admin"
    return _call(svc.knowledge_evidence_apply, payload)


@router.post("/knowledge/changes/draft")
def knowledge_change_draft(
    body: KnowledgeChangeDraftBody, _admin: str = Depends(require_admin),
) -> dict[str, Any]:
    return _call(svc.knowledge_change_draft, _payload(body))


@router.post("/knowledge/changes/apply")
def knowledge_change_apply(
    body: KnowledgeChangeApplyBody, _admin: str = Depends(require_admin),
) -> dict[str, Any]:
    return _call(svc.knowledge_change_apply, _payload(body))


@router.post("/knowledge/sync")
def knowledge_sync(
    body: KnowledgeSyncBody, _admin: str = Depends(require_admin),
) -> dict[str, Any]:
    return _call(svc.knowledge_sync, _payload(body))


@router.get("/knowledge/cards")
def knowledge_cards(limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    return _call(svc.knowledge_cards, limit)


@router.get("/knowledge/search")
def knowledge_search(q: str = Query("", max_length=1000), limit: int = Query(8, ge=1, le=50)) -> dict[str, Any]:
    return _call(svc.knowledge_search, q, limit)


@router.get("/knowledge/files")
def knowledge_files(limit: int = Query(500, ge=1, le=1000)) -> dict[str, Any]:
    return _call(svc.knowledge_files, limit)


@router.get("/knowledge/uploads")
def knowledge_uploads(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return _call(svc.knowledge_uploads, limit)


@router.get("/knowledge/file")
def knowledge_file(path: str = Query(..., min_length=1, max_length=1000)) -> dict[str, Any]:
    return _call(svc.knowledge_file, path)


@router.delete("/knowledge/file")
def knowledge_delete_file(path: str = Query(..., min_length=1, max_length=1000)) -> dict[str, Any]:
    return _call(svc.knowledge_delete_file, path)


@router.post("/knowledge/update/draft")
def knowledge_update_draft(body: KnowledgeUpdateBody) -> dict[str, Any]:
    return _call(svc.knowledge_update_draft, _payload(body))


@router.post("/knowledge/update/apply")
def knowledge_update_apply(body: KnowledgeUpdateBody) -> dict[str, Any]:
    return _call(svc.knowledge_update_apply, _payload(body))


@router.post("/knowledge/upload")
async def knowledge_upload(
    file: UploadFile = File(...),
    title: str = Form(""),
    id: str = Form(""),
    source_url: str = Form(""),
    source_type: str = Form("user"),
    confidence: str = Form(""),
    license: str = Form("user_supplied"),
    tags: str = Form(""),
    confirm: bool = Form(False),
    rebuild: bool = Form(True),
) -> dict[str, Any]:
    data = await file.read(25 * 1024 * 1024 + 1)
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大，最大 25MB")
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    return _call(
        svc.knowledge_upload,
        {
            "filename": file.filename or "upload",
            "content_base64": base64.b64encode(data).decode("ascii"),
            "title": title,
            "id": id,
            "source_url": source_url,
            "source_type": source_type or "user",
            "confidence": confidence,
            "license": license or "user_supplied",
            "tags": tag_list,
            "confirm": confirm,
            "rebuild": rebuild,
        },
    )


@router.post("/knowledge/uploads/apply")
def knowledge_upload_apply(body: KnowledgeUploadApplyBody) -> dict[str, Any]:
    return _call(svc.knowledge_upload_apply, _payload(body))


@router.post("/knowledge/import-directory")
def knowledge_import_directory(body: KnowledgeImportDirectoryBody) -> dict[str, Any]:
    return _call(svc.knowledge_import_directory, _payload(body))


@router.post("/code/bundle")
def code_bundle(body: CodeBundleBody) -> dict[str, Any]:
    return _call(
        svc.code_bundle,
        {
            "root": body.root,
            "goal": body.goal,
            "test_output": body.test_output,
            "limit": body.limit,
        },
    )


@router.post("/code/apply-loop")
def code_apply_loop(body: CodeApplyLoopBody) -> dict[str, Any]:
    return _call(
        svc.code_apply_loop,
        {
            "root": body.root,
            "spec": body.spec,
            "test_command": body.test_command,
            "execute": body.execute,
            "timeout": body.timeout,
            "persist": body.persist,
        },
    )


@router.get("/service/status")
def service_status(host: str = "", port: int | None = None) -> dict[str, Any]:
    return _call(svc.service_status, host, port)


@router.get("/service/logs")
def service_logs(lines: int = 80) -> dict[str, Any]:
    return _call(svc.service_logs, lines)


@router.post("/service/start")
def service_start(body: ServiceStartBody) -> dict[str, Any]:
    return _call(
        svc.service_start,
        {
            "host": body.host,
            "port": body.port,
            "allow_remote": body.allow_remote,
            "api_token": body.api_token,
            "wait": body.wait,
            "timeout": body.timeout,
        },
    )


@router.post("/service/stop")
def service_stop(body: ServiceStopBody) -> dict[str, Any]:
    return _call(svc.service_stop, {"timeout": body.timeout, "force": body.force})


@router.post("/service/autostart")
def service_autostart(body: ServiceAutostartBody) -> dict[str, Any]:
    return _call(svc.service_autostart, {"host": body.host, "port": body.port})


def _bridge_principal(authorization: str) -> dict[str, Any]:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="missing bridge bearer token")
    return ivyea_ops_tools.activate_bridge_principal(token.strip())


@bridge_router.post("/tools")
def bridge_tools(body: OpsToolsListBody, authorization: str = Header(default="")) -> dict[str, Any]:
    principal = _bridge_principal(authorization)
    return ivyea_ops_tools.list_tools(module=body.module, query=body.query, principal=principal)


@bridge_router.post("/call")
async def bridge_call(body: OpsToolCallBody, authorization: str = Header(default="")) -> dict[str, Any]:
    principal = _bridge_principal(authorization)
    return await ivyea_ops_tools.call_tool(body.name, body.arguments, principal=principal)
