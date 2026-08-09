"""领星 (LingXing) gateway API (mounted at ``/api/lingxing``, admin-only).

P0 surface:
* ``GET /status``  — config/switch snapshot (no secrets) for the UI.
* ``POST /probe``  — live ``tools/list`` against LingXing, classified read/write.
                     This is the step that resolves whether ad-write tools exist.
* ``GET /audit``   — recent gateway call log.

The write-execution endpoints (operate switch toggle, op tickets, triple
review, human confirm) land in P3.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.core import hub_settings as _hs
from app.services import lingxing_service as lx
from app.services import lingxing_data as lxd
from app.services import lingxing_automation as lxa
from app.services import lingxing_operate as lxo

router = APIRouter()


class ReadRequest(BaseModel):
    params: Dict[str, Any] = {}
    force: bool = False


_AUTO_CONFIG_KEYS = [
    "lingxing_auto_enabled", "lingxing_auto_weekday", "lingxing_auto_hour",
    "lingxing_auto_report_days", "lingxing_auto_stores", "lingxing_auto_max_campaigns",
    "lingxing_max_change_pct",
]


class AutoConfigPatch(BaseModel):
    config: Dict[str, Any] = {}


@router.get("/status")
async def status() -> Dict[str, Any]:
    return lx.status()


@router.post("/probe")
async def probe() -> Dict[str, Any]:
    """Verify both backends end-to-end: OpenAPI (token + signed read) and, if an
    X-Mcp-Key is configured, MCP (tools/list classified read/write)."""
    out: Dict[str, Any] = {"openapi": None, "mcp": None}
    from app.services import lingxing_openapi as lo
    if lo.is_configured():
        try:
            out["openapi"] = await lx.openapi_verify(caller="probe")
        except lx.LingXingError as e:
            out["openapi"] = {"ok": False, "error": str(e)}
    if lx._key():
        try:
            out["mcp"] = await lx.probe(caller="probe")
        except lx.LingXingError as e:
            out["mcp"] = {"ok": False, "error": str(e)}
    if out["openapi"] is None and out["mcp"] is None:
        raise HTTPException(status_code=400, detail="未配置任何领星后端（OpenAPI 凭证或 MCP key）")
    return out


@router.get("/audit")
async def audit(limit: int = 100) -> Dict[str, Any]:
    return {"rows": lx.recent_audit(limit=max(1, min(limit, 500)))}


@router.get("/optimizer/run")
async def optimizer_run_legacy() -> Dict[str, Any]:
    """Legacy synchronous entry — replaced by the background-job flow."""
    raise HTTPException(status_code=410,
                        detail="该接口已改为后台任务：POST /optimizer/run 启动，GET /optimizer/runs/{id} 轮询进度")


@router.post("/optimizer/run")
async def optimizer_run_start(sid: int, days: int = 0) -> Dict[str, Any]:
    """Start one deterministic rule-engine run in the background (advisory).
    Returns the run row immediately; poll ``/optimizer/runs/{id}`` for
    progress + result. Honors target-ACOS-from-margin + conservative thresholds."""
    from app.services import lingxing_optimizer as lxopt
    if not lx.is_master_enabled():
        raise HTTPException(status_code=400, detail="领星集成未启用（总开关关闭）")
    if days:
        _hs.save({"lingxing_opt_window_days": max(7, min(days, 60))})
    return lxopt.start_background_run(int(sid))


@router.get("/optimizer/runs")
async def optimizer_runs(sid: Optional[int] = None, limit: int = 20) -> Dict[str, Any]:
    from app.services import lingxing_optimizer as lxopt
    return {"runs": lxopt.list_opt_runs(sid=sid, limit=max(1, min(limit, 50)))}


@router.get("/optimizer/runs/{run_id}")
async def optimizer_run_detail(run_id: str) -> Dict[str, Any]:
    from app.services import lingxing_optimizer as lxopt
    run = lxopt.get_opt_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="未找到该优化运行")
    return run


@router.get("/optimizer/runs/{run_id}/findings")
async def optimizer_run_findings(run_id: str) -> Dict[str, Any]:
    """把这次运行的候选操作译成统一结论契约（core/findings）。

    比 LLM 那条链路的结论"硬"一档：证据是接口取回来的真实指标，不是模型转述的。
    同一个结论卡片、同一份带证据页的交付物，这边写的是「花费 820.50 USD /
    点击 312 / 订单 0，取自 lingxing，近 30 天」。"""
    from app.services import lingxing_findings as lxf
    out = lxf.findings_for_run(run_id)
    if out is None:
        raise HTTPException(status_code=404, detail="未找到该优化运行")
    return out


@router.get("/optimizer/runs/{run_id}/deliverable")
async def optimizer_run_deliverable(run_id: str, fmt: str = "xlsx"):
    """导出成可以直接发给别人的交付物：结论 / 证据 / 说明 三页。"""
    from fastapi.responses import FileResponse

    from pathlib import Path

    from app.core.config import settings
    from app.services import deliverable, lingxing_findings as lxf
    if fmt not in ("xlsx", "md"):
        raise HTTPException(status_code=400, detail="fmt 只支持 xlsx 或 md")
    findings = lxf.findings_for_run(run_id)
    if findings is None:
        raise HTTPException(status_code=404, detail="未找到该优化运行")

    meta = {"job_id": run_id, "kind": "领星广告规则优化"}
    out_dir = Path(settings.data_dir) / "lingxing" / "deliverables"
    out_dir.mkdir(parents=True, exist_ok=True)
    if fmt == "md":
        path = out_dir / f"{run_id}.md"
        path.write_text(deliverable.build_markdown(findings, meta), encoding="utf-8")
        return FileResponse(path, media_type="text/markdown",
                            filename=f"领星广告优化-{run_id}-结论.md")
    path = deliverable.build_xlsx(out_dir / f"{run_id}.xlsx", findings, meta)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"领星广告优化-{run_id}-交付物.xlsx")


@router.get("/dashboard")
async def dashboard(sids: str = "", days: int = 7) -> Dict[str, Any]:
    """广告数据大盘聚合（按店铺/活动/天）。sids 逗号分隔，空=全部店铺。"""
    from app.services import lingxing_dashboard as lxdash
    sid_list = [int(x) for x in sids.replace("，", ",").split(",") if x.strip().isdigit()] or None
    try:
        return await lxdash.dashboard(sid_list, days)
    except lx.LingXingError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/datasets")
async def datasets() -> Dict[str, Any]:
    """Read-dataset registry that drives the 浏览/分析 panels."""
    return {"datasets": lxd.catalog()}


@router.post("/read/{dataset}")
async def read(dataset: str, body: ReadRequest) -> Dict[str, Any]:
    try:
        return await lxd.fetch_dataset(dataset, body.params, force=body.force, caller="panel")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except lx.LingXingError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# --- weekly advisory automation (P2) ---------------------------------------
@router.get("/auto/config")
async def auto_config() -> Dict[str, Any]:
    cfg = _hs.load()
    return {"config": {k: cfg.get(k) for k in _AUTO_CONFIG_KEYS}}


@router.patch("/auto/config")
async def auto_config_patch(body: AutoConfigPatch) -> Dict[str, Any]:
    updates = {k: v for k, v in body.config.items() if k in _AUTO_CONFIG_KEYS}
    _hs.save(updates)
    cfg = _hs.load()
    return {"config": {k: cfg.get(k) for k in _AUTO_CONFIG_KEYS}}


@router.get("/auto/runs")
async def auto_runs(limit: int = 30) -> Dict[str, Any]:
    return {"runs": lxa.list_runs(limit=max(1, min(limit, 100)))}


@router.get("/auto/runs/{run_id}")
async def auto_run_detail(run_id: str) -> Dict[str, Any]:
    run = lxa.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="未找到该运行记录")
    return run


@router.post("/auto/run")
async def auto_run_now() -> Dict[str, Any]:
    """Trigger one advisory run in the background (analyse + recommend, no writes)."""
    if not lx.is_master_enabled():
        raise HTTPException(status_code=400, detail="领星集成未启用（总开关关闭）")
    run_id = lxa.start_background_run(trigger="manual")
    return {"ok": True, "run_id": run_id}


# --- controlled write operations (P3) --------------------------------------
class ConfirmRequest(BaseModel):
    dry_run: bool = False


class BatchTickets(BaseModel):
    payloads: list[Dict[str, Any]] = []


class BatchAction(BaseModel):
    action: str  # confirm | reject
    ids: list[str] = []
    dry_run: bool = False


class ManualTicket(BaseModel):
    op_type: str
    sid: int
    # modify-type
    target_id: str | None = None
    target_name: str | None = None
    cur_value: float | None = None
    cur_state: str | None = None
    new_value: float | None = None
    new_state: str | None = None
    # add-type (加词 / 否词)
    campaign_id: str | None = None
    ad_group_id: str | None = None
    keyword_text: str | None = None
    match_type: str | None = None
    bid: float | None = None
    rationale: str | None = None
    opt: Dict[str, Any] | None = None  # optimizer rule trail (rule/metrics/significance)


@router.get("/help")
async def help_doc() -> Dict[str, Any]:
    """The Chinese user guide (markdown) for in-app rendering."""
    from app.core.version import runtime_root
    p = runtime_root() / "docs" / "lingxing-erp-guide.md"
    try:
        return {"markdown": p.read_text(encoding="utf-8")}
    except Exception:
        return {"markdown": "# 文档未找到\n请见仓库 docs/lingxing-erp-guide.md"}


@router.get("/review/providers")
async def review_providers() -> Dict[str, Any]:
    cfg = _hs.load()
    return {"available": lxo.available_providers(),
            "review_providers": cfg.get("lingxing_review_providers") or "ivyea-agent,deepseek,assistant",
            "analysis_provider": cfg.get("lingxing_analysis_provider") or "ivyea-agent",
            "personas": [p[0] for p in lxo._REVIEWERS],
            "rules_doc": _hs.get("lingxing_rules_doc") or "",
            "rules_doc_default": _hs._DEFAULTS.get("lingxing_rules_doc", "")}


@router.get("/operate/op-types")
async def operate_op_types() -> Dict[str, Any]:
    return {"op_types": lxo.op_types_catalog()}


@router.post("/operate/manual")
async def operate_manual(body: ManualTicket) -> Dict[str, Any]:
    try:
        return await lxo.create_manual_ticket(body.model_dump())
    except lx.LingXingError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/operate/batch-tickets")
async def operate_batch_tickets(body: BatchTickets) -> Dict[str, Any]:
    """Create tickets for a selected set of optimizer/advisory payloads.
    Returns immediately; each ticket's review pipeline runs in the background."""
    try:
        return await lxo.create_tickets_batch(body.payloads)
    except lx.LingXingError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/operate/tickets/batch")
async def operate_tickets_batch(body: BatchAction) -> Dict[str, Any]:
    """Confirm/reject a human-selected set of tickets (each still passes every
    per-ticket gate; a real write failure stops the rest + trips the breaker)."""
    try:
        return await lxo.batch_tickets_action(body.action, body.ids, dry_run=body.dry_run)
    except lx.LingXingError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/operate/enable")
async def operate_enable() -> Dict[str, Any]:
    st = lxo.enable_operate()
    await lxo.send_alert("🔓 操作开关已开启（进入可写态，写操作仍需三重复核+人工确认）")
    return {"status": st}


@router.post("/operate/disable")
async def operate_disable() -> Dict[str, Any]:
    st = lxo.disable_operate()
    await lxo.send_alert("🔒 操作开关已关闭（恢复只读）")
    return {"status": st}


@router.get("/operate/tickets")
async def operate_tickets(limit: int = 50) -> Dict[str, Any]:
    return {"tickets": lxo.list_tickets(limit=max(1, min(limit, 200)))}


@router.get("/operate/tickets/{tid}")
async def operate_ticket_detail(tid: str) -> Dict[str, Any]:
    t = lxo.get_ticket(tid)
    if not t:
        raise HTTPException(status_code=404, detail="未找到工单")
    return t


@router.post("/operate/from-run/{run_id}")
async def operate_from_run(run_id: str) -> Dict[str, Any]:
    """Turn a run's advisory proposals into review-gated tickets."""
    try:
        return await lxo.create_tickets_from_run(run_id)
    except lx.LingXingError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/operate/tickets/{tid}/report", response_class=HTMLResponse)
async def operate_report(tid: str, download: int = 1) -> HTMLResponse:
    """Self-contained HTML operation report (renders + prints to PDF anywhere)."""
    from app.services import lingxing_report as lxr
    try:
        html = await lxr.build_report_html(tid)
    except lx.LingXingError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    headers = {"Content-Disposition": f'attachment; filename="lingxing-op-{tid}.html"'} if download else {}
    return HTMLResponse(content=html, headers=headers)


@router.post("/operate/tickets/{tid}/confirm")
async def operate_confirm(tid: str, body: ConfirmRequest) -> Dict[str, Any]:
    try:
        return await lxo.confirm_ticket(tid, decided_by="human", dry_run=body.dry_run)
    except lx.LingXingError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/operate/tickets/{tid}/reject")
async def operate_reject(tid: str) -> Dict[str, Any]:
    try:
        return await lxo.reject_ticket(tid, decided_by="human")
    except lx.LingXingError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/operate/tickets/{tid}/rollback")
async def operate_rollback(tid: str) -> Dict[str, Any]:
    try:
        return await lxo.rollback_ticket(tid, decided_by="human")
    except lx.LingXingError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
