"""Health check + 一键诊断包。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.core.security import require_admin
from app.core.version import app_version
from app.services import diagnostics

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "IvyeaOps", "version": app_version()}


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
