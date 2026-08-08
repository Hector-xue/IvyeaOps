"""Health check + 一键诊断包。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response

from app.core.security import require_admin
from app.core.version import app_version
from app.services import diagnostics

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "IvyeaOps", "version": app_version()}


@router.get("/audit")
def audit_list(
    module: str = Query("", description="按板块筛：settings / git / autofix / lingxing …"),
    actor: str = Query("", description="按操作人筛（邮箱）"),
    limit: int = Query(200, ge=1, le=2000),
    fmt: str = Query("json", pattern="^(json|csv)$"),
    _admin: str = Depends(require_admin),
) -> Response:
    """统一审计流水（admin only）。

    在此之前只有领星那边有留痕，终端/git/autofix/配置这些能改东西的地方一条记录
    都没有 —— 团队自托管时"谁把配置改了""谁触发了自动修复"根本答不上来。
    CSV 导出是给需要把记录交出去的场合用的（合规、事故复盘）。
    """
    from app.core import audit

    rows = audit.query(module=module or None, actor=actor or None, limit=limit)
    if fmt == "csv":
        return Response(
            content=audit.to_csv(rows),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="ivyea-ops-audit.csv"'},
        )
    return JSONResponse({"total": len(rows), "modules": audit.modules(), "rows": rows})


@router.get("/health/diagnostic-bundle")
def diagnostic_bundle(
    lines: int = Query(2000, ge=100, le=20000, description="日志取最后多少行"),
    _admin: str = Depends(require_admin),
) -> Response:
    """导出诊断包（zip）。

    **必须是 admin**：包里有配置结构、库表清单和日志，虽然密钥已脱敏，但这仍然
    是整台机器的横截面，不该让普通成员导走。

    这个接口不发起任何外部请求 —— 见 services/diagnostics 的说明。
    """
    payload = diagnostics.build_bundle(log_lines=lines)
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{diagnostics.bundle_filename()}"'
        },
    )
