"""Listing 项目 CRUD、产品信息、素材图上传与图片文件服务。"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.security import require_user

from .common import (
    IMAGES_DIR, _cached_white_product_source, _db, _parse_copy_result,
    _white_background_score, project_row, update_project,
)

logger = logging.getLogger("ivyea.routers.listing.projects")

router = APIRouter()


class CreateProjectReq(BaseModel):
    asin: str
    marketplace: str = "US"
    supplier_url: Optional[str] = None


class ProductInfoReq(BaseModel):
    product_name: Optional[str] = None
    description: Optional[str] = None
    selling_points: Optional[str] = None
    target_audience: Optional[str] = None


@router.get("/projects")
def list_projects(_user: str = Depends(require_user)):
    conn = _db()
    rows = conn.execute(
        "SELECT id, asin, marketplace, status, title, created_at, updated_at "
        "FROM listing_projects ORDER BY updated_at DESC"
    ).fetchall()
    active = conn.execute(
        "SELECT project_id, kind FROM listing_jobs WHERE status='running'"
    ).fetchall()
    conn.close()
    running: dict[str, list[str]] = {}
    for row in active:
        running.setdefault(row["project_id"], []).append(row["kind"])
    return [{**dict(r), "active_jobs": running.get(r["id"], [])} for r in rows]


@router.post("/projects")
async def create_project(body: CreateProjectReq, _user: str = Depends(require_user)):
    pid = str(uuid.uuid4())[:8]
    now = time.time()
    # 建项目就是纯本地一行记录：采集走本机 curl / 本机浏览器，不再向任何外部
    # 采集服务注册（旧实现要 POST 一个 Docker 服务，没装 Docker 的用户每次建项目
    # 都得干等连接超时）。imgflow_project_id 列保留但恒空，仅为老库兼容。
    conn = _db()
    conn.execute(
        "INSERT INTO listing_projects (id,asin,marketplace,imgflow_project_id,status,created_at,updated_at) VALUES (?,?,?,'','created',?,?)",
        (pid, body.asin, body.marketplace, now, now)
    )
    conn.commit()
    conn.close()
    return {"id": pid, "asin": body.asin, "scrape_available": True}


@router.get("/projects/{project_id}")
def get_project(project_id: str, _user: str = Depends(require_user)):
    row = project_row(project_id)
    if not row:
        raise HTTPException(404, "project not found")
    result = dict(row)
    # Read-time migration keeps saved projects safe after quality rules evolve.
    # Existing bad renders remain visible as history, but internal copy is
    # stripped and their missing render QA blocks commercial approval.
    if result.get("creative_sets"):
        from .visuals import _bind_reference_templates, _normalize_shot_plan
        try:
            sets = json.loads(result["creative_sets"])
            scrape_data = json.loads(result["scrape_data"]) if result.get("scrape_data") else {}
            migrated = {}
            for key, value in sets.items():
                if isinstance(value, dict):
                    normalized = _normalize_shot_plan(
                        value, len(value.get("images") or []),
                        "aplus" if key == "aplus" else "gallery",
                    )
                    migrated[key] = _bind_reference_templates(
                        normalized, project_id, scrape_data,
                        "aplus" if key == "aplus" else "gallery",
                    )
            result["creative_sets"] = json.dumps(migrated, ensure_ascii=False)
        except Exception:
            logger.debug("json.loads 失败（旁路，已忽略）", exc_info=True)
    if result.get("copy_result"):
        try:
            copy_payload = json.loads(result["copy_result"])
        except Exception:
            copy_payload = result["copy_result"]
        repaired_copy = _parse_copy_result(copy_payload)
        if repaired_copy:
            result["copy_result"] = json.dumps(repaired_copy, ensure_ascii=False)
    return result


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, _user: str = Depends(require_user)):
    conn = _db()
    conn.execute("DELETE FROM listing_projects WHERE id = ?", (project_id,))
    conn.execute("DELETE FROM listing_jobs WHERE project_id = ?", (project_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/projects/{project_id}/product-info")
def save_product_info(project_id: str, body: ProductInfoReq, _user: str = Depends(require_user)):
    row = project_row(project_id, "scrape_data")
    if not row:
        raise HTTPException(404)
    existing = json.loads(row["scrape_data"]) if row["scrape_data"] else {}
    existing["manual"] = {
        "product_name": body.product_name or "",
        "description": body.description or "",
        "selling_points": body.selling_points or "",
        "target_audience": body.target_audience or "",
    }
    update_project(project_id, scrape_data=json.dumps(existing, ensure_ascii=False))
    return {"ok": True, "saved_at": time.time()}


# ─── 素材图上传 / 引用 ────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/upload-image")
async def upload_product_image(project_id: str, file: UploadFile = File(...), _user: str = Depends(require_user)):
    """Upload a product reference image."""
    if not project_row(project_id, "id"):
        raise HTTPException(404)

    proj_dir = IMAGES_DIR / project_id
    proj_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "img.jpg").suffix or ".jpg"
    fname = f"{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
    dest = proj_dir / fname
    content = await file.read()
    dest.write_bytes(content)

    row = project_row(project_id, "scrape_data")
    existing = json.loads(row["scrape_data"]) if row and row["scrape_data"] else {}
    uploaded = existing.get("uploaded_images", [])
    uploaded.append(str(dest))
    existing["uploaded_images"] = uploaded
    white_check = _white_background_score(content)
    if white_check.get("ready"):
        existing["white_product_source"] = f"/api/listing/images/{project_id}/{fname}"
        existing["white_product_source_check"] = {"url": existing["white_product_source"], **white_check}
    update_project(project_id, scrape_data=json.dumps(existing, ensure_ascii=False))
    return {"path": str(dest), "filename": fname,
            "url": f"/api/listing/images/{project_id}/{fname}",
            "white_ready": bool(white_check.get("ready"))}


@router.get("/projects/{project_id}/reference-images")
def get_reference_images(project_id: str, _user: str = Depends(require_user)):
    """Get all reference images (scraped URLs + uploaded files)."""
    row = project_row(project_id, "scrape_data")
    if not row:
        raise HTTPException(404)
    data = json.loads(row["scrape_data"]) if row["scrape_data"] else {}
    recommended = _cached_white_product_source(data)
    uploaded_urls = []
    for p in data.get("uploaded_images", []):
        path_obj = Path(p)
        if path_obj.exists():
            uploaded_urls.append({
                "filename": path_obj.name,
                "url": f"/api/listing/images/{project_id}/{path_obj.name}",
                "white_ready": f"/api/listing/images/{project_id}/{path_obj.name}" == recommended,
            })
    return {
        "scraped": data.get("reference_images", []),
        "uploaded": uploaded_urls,
        "white_product_source": recommended,
        "white_product_source_check": data.get("white_product_source_check") or {},
    }


@router.delete("/projects/{project_id}/uploaded-image/{filename}")
def delete_uploaded_image(project_id: str, filename: str, _user: str = Depends(require_user)):
    """Delete an uploaded reference image."""
    path = IMAGES_DIR / project_id / filename
    if path.exists():
        path.unlink()
    row = project_row(project_id, "scrape_data")
    if row:
        data = json.loads(row["scrape_data"]) if row["scrape_data"] else {}
        uploaded = [p for p in data.get("uploaded_images", []) if not p.endswith(f"/{filename}")]
        data["uploaded_images"] = uploaded
        if str(data.get("white_product_source") or "").endswith(f"/{filename}"):
            data["white_product_source"] = ""
            data["white_product_source_check"] = {"ready": False, "reason": "deleted"}
        update_project(project_id, scrape_data=json.dumps(data, ensure_ascii=False))
    return {"ok": True}


@router.get("/images/{project_id}/{filename}")
def serve_image(project_id: str, filename: str):
    """Serve uploaded project images. 路径契约不可变：老项目的 creative_sets 里
    存的都是 /api/listing/images/... URL。"""
    safe_project = "".join(ch for ch in project_id if ch.isalnum() or ch in "-_")
    safe_name = Path(filename).name
    path = IMAGES_DIR / safe_project / safe_name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(str(path))
