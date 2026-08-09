"""Ad-report audit API routes.

Workflow: upload -> start -> poll -> download.

- POST /ad-audit/upload     multipart xlsx/csv -> returns job_id in state=uploaded
- POST /ad-audit/start      attach task context -> state=queued/running
- GET  /ad-audit/{job_id}   status + structured result
- GET  /ad-audit/{job_id}/download?fmt=md|json|xlsx
- GET  /ad-audit/list       recent jobs
- POST /ad-audit/clear-failed
- GET  /ad-audit/runners    (reuses the same runner matrix as ASIN audit)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.security import require_user
from app.services import ad_audit

router = APIRouter()

MARKETPLACES = {
    "US", "UK", "DE", "FR", "CA", "JP", "ES", "IT", "MX", "AU", "AE", "BR", "SA",
}


# ---------- Response models ----------

class AdSourceInfo(BaseModel):
    source_id: str
    file_name: str
    file_ext: str
    file_size: int
    ad_type: str
    date_range: str
    row_count: int
    columns: List[str] = []
    campaign_name: str
    daily_budget_usd: Optional[float] = None
    uploaded_at: str = ""


class AdUploadResult(BaseModel):
    job_id: str
    file_name: str
    file_size: int
    ad_type: str
    marketplace: str
    date_range: str
    row_count: int
    columns: List[str]
    status: str
    created_at: str
    sources: List[AdSourceInfo] = []


class AdStartBody(BaseModel):
    job_id: str
    goal: str = Field(..., description="profit | new_launch | relaunch | clearance")
    output_mode: str = Field(default="report", description="report | xlsx_plan")
    asin: str = Field(default="")
    product_notes: str = Field(default="")
    protected_keywords: List[str] = Field(default_factory=list)
    runner: str = Field(default="auto")
    # Map of source_id -> daily budget USD. Optional — omitted campaigns get
    # % reallocation instead of $ numbers.
    daily_budgets: Dict[str, float] = Field(default_factory=dict)


class AdSourceUpdateBody(BaseModel):
    campaign_name: Optional[str] = None
    daily_budget_usd: Optional[float] = None
    clear_daily_budget: bool = False


class AdStartResult(BaseModel):
    job_id: str
    status: str
    runner_used: Optional[str] = None


class AdStatusResult(BaseModel):
    job_id: str
    status: str
    progress: Optional[str] = None
    file_name: str = ""
    file_ext: str = ""
    file_size: int = 0
    ad_type: str = ""
    marketplace: str = ""
    date_range: str = ""
    row_count: int = 0
    columns: List[str] = []
    sources: List[AdSourceInfo] = []
    goal: str = ""
    output_mode: str = "report"
    asin: str = ""
    product_notes: str = ""
    protected_keywords: List[str] = []
    daily_budgets: Dict[str, float] = {}
    runner_pref: Optional[str] = None
    runner_used: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    raw_md: Optional[str] = None
    structured: Optional[Dict[str, Any]] = None


# ---------- Endpoints ----------

@router.get("/runners")
def ad_runners(_user: str = Depends(require_user)) -> Dict[str, Any]:
    """Same matrix as ASIN audit — the UI re-uses it."""
    return {"runners": ad_audit.runner_status()}


def _job_to_upload_result(job: ad_audit.AdJob) -> AdUploadResult:
    return AdUploadResult(
        job_id=job.job_id,
        file_name=job.file_name,
        file_size=job.file_size,
        ad_type=job.ad_type,
        marketplace=job.marketplace,
        date_range=job.date_range,
        row_count=job.row_count,
        columns=job.columns,
        status=job.status,
        created_at=job.created_at,
        sources=[AdSourceInfo(**s) for s in (job.sources or [])],
    )


@router.post("/upload", response_model=AdUploadResult)
async def ad_upload(
    file: UploadFile = File(...),
    marketplace: str = Form(default="US"),
    job_id: Optional[str] = Form(default=None),
    _user: str = Depends(require_user),
) -> AdUploadResult:
    """Upload one search-term report.

    - First upload (no ``job_id``): creates a new job with one source.
    - Subsequent uploads (``job_id`` present): append to the existing job,
      enabling multi-campaign analysis for the same ASIN. Deduped by SHA-1;
      capped at ``MAX_SOURCES`` files.
    """
    mk = (marketplace or "US").strip().upper()
    if mk not in MARKETPLACES:
        raise HTTPException(status_code=400, detail=f"unsupported marketplace {mk}")

    filename = file.filename or ""
    if not filename:
        raise HTTPException(status_code=400, detail="filename is required")

    # Read the upload into memory. MAX_UPLOAD_BYTES is enforced inside the
    # service; we cap the read to that + 1 so oversized files trip the check.
    content = await file.read(ad_audit.MAX_UPLOAD_BYTES + 1)
    try:
        job = ad_audit.upload_report(
            filename, content, marketplace=mk, job_id=(job_id or None)
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"upload failed: {e}")

    return _job_to_upload_result(job)


@router.delete("/{job_id}/source/{source_id}", response_model=AdUploadResult)
async def ad_remove_source(
    job_id: str,
    source_id: str,
    _user: str = Depends(require_user),
) -> AdUploadResult:
    """Remove one source file from an uploaded (not yet started) job."""
    try:
        job = ad_audit.remove_source(job_id, source_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _job_to_upload_result(job)


@router.patch("/{job_id}/source/{source_id}", response_model=AdUploadResult)
async def ad_update_source(
    job_id: str,
    source_id: str,
    body: AdSourceUpdateBody,
    _user: str = Depends(require_user),
) -> AdUploadResult:
    """Rename a source's campaign or set/clear its daily budget."""
    try:
        job = ad_audit.update_source(
            job_id,
            source_id,
            campaign_name=body.campaign_name,
            daily_budget_usd=body.daily_budget_usd,
            clear_daily_budget=body.clear_daily_budget,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _job_to_upload_result(job)


@router.post("/start", response_model=AdStartResult)
async def ad_start(
    body: AdStartBody,
    _user: str = Depends(require_user),
) -> AdStartResult:
    if body.goal not in ad_audit.GOALS:
        raise HTTPException(
            status_code=400,
            detail=f"goal must be one of {list(ad_audit.GOALS)}",
        )
    if body.output_mode not in ad_audit.OUTPUT_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"output_mode must be one of {list(ad_audit.OUTPUT_MODES)}",
        )
    runner_pref = (body.runner or "auto").lower()
    if runner_pref not in ("auto", "hermes", "codex", "claude"):
        raise HTTPException(status_code=400, detail=f"unknown runner: {runner_pref}")
    if ad_audit.is_busy():
        raise HTTPException(status_code=409, detail="另一个审计任务正在运行，请稍后")

    try:
        job = await ad_audit.start_job(
            job_id=body.job_id,
            goal=body.goal,
            protected_keywords=body.protected_keywords,
            asin=body.asin,
            product_notes=body.product_notes,
            runner_pref=runner_pref,
            daily_budgets=body.daily_budgets or None,
            output_mode=body.output_mode,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        msg = str(e)
        # 409 for busy/state mismatch, 400 for runner-not-available / bad input.
        code = 400 if ("not available" in msg or "cannot start" in msg) else 409
        raise HTTPException(status_code=code, detail=msg)

    return AdStartResult(
        job_id=job.job_id,
        status=job.status,
        runner_used=job.runner_used,
    )


@router.get("/list")
def ad_list(
    limit: int = 20,
    _user: str = Depends(require_user),
) -> Dict[str, Any]:
    limit = max(1, min(int(limit), 100))
    return {"items": ad_audit.list_jobs(limit=limit), "busy": ad_audit.is_busy()}


@router.post("/clear-failed")
def ad_clear_failed(_user: str = Depends(require_user)) -> Dict[str, Any]:
    return {"removed": ad_audit.clear_failed()}


@router.delete("/{job_id}")
def ad_delete(job_id: str, _user: str = Depends(require_user)) -> Dict[str, Any]:
    ok = ad_audit.delete_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="job not found or still running")
    return {"deleted": True}


@router.get("/{job_id}", response_model=AdStatusResult)
def ad_get(
    job_id: str,
    _user: str = Depends(require_user),
) -> AdStatusResult:
    data = ad_audit.get_job(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="job not found")
    return AdStatusResult(**data)


@router.get("/{job_id}/download")
def ad_download(
    job_id: str,
    fmt: str = "md",
    _user: str = Depends(require_user),
) -> FileResponse:
    if fmt not in ("md", "json", "xlsx", "html", "deliverable", "brief"):
        raise HTTPException(status_code=400,
                            detail="fmt must be md, json, xlsx, html, deliverable or brief")

    # deliverable / brief 走统一的结论契约（core/findings），跟报表里那份原始
    # xlsx 是两个东西：**这份是给别人看的** —— 结论一页、证据一页（指标/数值/
    # 时间窗/来源）、说明一页。别人质疑"你凭什么这么说"时翻第二页。
    if fmt in ("deliverable", "brief"):
        job = ad_audit.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="没有这个任务")
        findings = job.get("findings") or {}
        meta = {"job_id": job_id, "kind": "广告审计"}
        from app.services import deliverable
        if fmt == "brief":
            text = deliverable.build_markdown(findings, meta)
            out = ad_audit._job_dir(job_id) / "brief.md"
            out.write_text(text, encoding="utf-8")
            return FileResponse(out, media_type="text/markdown",
                                filename=f"ad-audit-{job_id}-结论.md")
        out = deliverable.build_xlsx(
            ad_audit._job_dir(job_id) / "deliverable.xlsx", findings, meta)
        return FileResponse(
            out,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=f"ad-audit-{job_id}-交付物.xlsx")

    path = ad_audit.download_path(job_id, fmt)
    if path is None:
        if fmt == "xlsx":
            detail = "artifact not available (xlsx 需要 report.json；请等任务跑完再下载)"
        elif fmt == "html":
            detail = "artifact not available (html 需要 report.json；请等任务跑完再下载)"
        else:
            detail = "artifact not available"
        raise HTTPException(status_code=404, detail=detail)
    media_map = {
        "md": "text/markdown",
        "json": "application/json",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "html": "text/html; charset=utf-8",
    }
    filename = f"ad-audit-{job_id}.{fmt}"
    return FileResponse(path, media_type=media_map[fmt], filename=filename)
