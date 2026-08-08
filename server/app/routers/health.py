"""Health check + 一键诊断包。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.core.security import require_admin
from app.core.version import app_version
from app.services import diagnostics

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "IvyeaOps", "version": app_version()}


class BackupBody(BaseModel):
    passphrase: str = ""          # 给了才把主密钥带进包（加密后）
    include_media: bool = False   # 图片等可再生内容，默认不收
    keep: int = 7                 # 本地保留份数


@router.post("/admin/backup")
def backup_create(body: BackupBody, _admin: str = Depends(require_admin)) -> dict:
    """生成备份包（admin only）。

    不设口令也能备份，只是包里不含主密钥 —— 恢复后各处 API 密钥要重填。
    设了口令才是"换台机器能完整还原"的那种备份。
    """
    from app.core import backup

    path = backup.create(passphrase=body.passphrase, include_media=body.include_media)
    pruned = backup.prune(keep=body.keep)
    report = backup.inspect(path)
    audit_note = "with-key" if report.get("master_key_included") else "no-key"
    from app.core import audit
    audit.record("backup", "create", target=path.name, detail={"mode": audit_note})
    return {"ok": True, "path": str(path), "name": path.name,
            "bytes": path.stat().st_size, "pruned": pruned,
            "master_key_included": report.get("master_key_included", False),
            "warnings": report.get("problems", [])}


@router.get("/admin/backups")
def backup_list(_admin: str = Depends(require_admin)) -> dict:
    from app.core import backup
    from app.core.config import settings

    folder = Path(settings.data_dir) / "backups"
    items = []
    if folder.is_dir():
        for p in sorted(folder.glob("ivyea-ops-backup-*.zip"),
                        key=lambda x: x.stat().st_mtime, reverse=True):
            report = backup.inspect(p)
            items.append({"name": p.name, "path": str(p), "bytes": p.stat().st_size,
                          "ok": report.get("ok", False),
                          "master_key_included": report.get("master_key_included", False),
                          "created_at": (report.get("manifest") or {}).get("created_at", "")})
    return {"total": len(items), "items": items}


class RestoreBody(BaseModel):
    path: str
    passphrase: str = ""
    dry_run: bool = True
    confirm: bool = False       # 真正落地必须显式确认


@router.post("/admin/restore")
def backup_restore(body: RestoreBody, _admin: str = Depends(require_admin)) -> dict:
    """恢复备份。**默认只干跑**。

    真正覆盖数据要 ``dry_run=false`` **且** ``confirm=true`` —— 两个开关而不是
    一个，是因为这个动作不可逆：干跑报告和执行之间必须隔着一次有意识的确认。
    """
    from app.core import audit, backup

    if not body.dry_run and not body.confirm:
        raise HTTPException(400, "恢复会覆盖现有数据：请先看干跑报告，再带 confirm=true 执行")

    report = backup.restore(Path(body.path), passphrase=body.passphrase,
                            dry_run=body.dry_run)
    if not body.dry_run:
        audit.record("backup", "restore", target=Path(body.path).name,
                     outcome="ok" if report.get("ok") else "failed",
                     detail={"restored": report.get("restored", 0)})
    return report


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
