"""FBA 头程报价比价 API."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.config import settings
from app.core.security import require_user

router = APIRouter()

FREIGHT_DIR = settings.data_dir / "freight"
QUOTES_DIR = FREIGHT_DIR / "quotes"
INDEX_FILE = FREIGHT_DIR / "index.json"
META_FILE = FREIGHT_DIR / "meta.json"

FREIGHT_DIR.mkdir(parents=True, exist_ok=True)
QUOTES_DIR.mkdir(parents=True, exist_ok=True)


def _load_meta() -> Dict[str, Any]:
    if META_FILE.exists():
        return json.loads(META_FILE.read_text(encoding="utf-8"))
    return {"files": {}}


def _save_meta(meta: Dict[str, Any]) -> None:
    META_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _rebuild() -> Dict[str, Any]:
    from app.services.freight_parser import build_index
    meta = _load_meta()
    file_metadata = meta.get("files", {})
    disabled_files = {k for k, v in file_metadata.items() if v.get("disabled")}
    return build_index(
        folder=QUOTES_DIR,
        out_file=INDEX_FILE,
        disabled_files=disabled_files,
        file_metadata=file_metadata,
    )


def _load_index() -> Dict[str, Any]:
    if not INDEX_FILE.exists():
        return {
            "built_at": "",
            "record_count": 0,
            "warehouse_count": 0,
            "companies": [],
            "files": [],
            "records": [],
        }
    return json.loads(INDEX_FILE.read_text(encoding="utf-8"))


# ─── Models ──────────────────────────────────────────────────────────────────

class SearchReq(BaseModel):
    warehouse_code: str
    weight_kg: Optional[float] = None
    company: Optional[str] = None


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("/status")
def get_status(_user: str = Depends(require_user)) -> Dict[str, Any]:
    idx = _load_index()
    return {
        "built_at": idx.get("built_at", ""),
        "record_count": idx.get("record_count", 0),
        "warehouse_count": idx.get("warehouse_count", 0),
        "companies": idx.get("companies", []),
    }


@router.post("/search")
def search_quotes(body: SearchReq, _user: str = Depends(require_user)) -> Dict[str, Any]:
    code = (body.warehouse_code or "").strip().upper()
    if not code:
        raise HTTPException(400, "warehouse_code 不能为空")

    idx = _load_index()
    records = [r for r in idx.get("records", []) if r.get("warehouse_code", "").upper() == code]

    if body.company:
        c = body.company.strip()
        records = [r for r in records if c.lower() in r.get("company", "").lower()]

    # Weight matching: find records where weight falls within the tier range
    if body.weight_kg and body.weight_kg > 0:
        w = body.weight_kg
        matched = []
        for r in records:
            tier = r.get("tier", "")
            unit = r.get("unit", "")
            if unit and "KG" in unit.upper():
                # Parse tier like "0+", "30+", "100+" or range "0-30"
                import re
                m = re.search(r"(\d+(?:\.\d+)?)\+", tier)
                if m:
                    low = float(m.group(1))
                    if w >= low:
                        matched.append(r)
                    continue
                m2 = re.search(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)", tier)
                if m2:
                    low, high = float(m2.group(1)), float(m2.group(2))
                    if low <= w <= high:
                        matched.append(r)
                    continue
                matched.append(r)  # no tier info, include
            else:
                matched.append(r)
        records = matched

    # Sort: by price_value asc, then company
    records = sorted(records, key=lambda r: (r.get("price_value") or 9999, r.get("company", "")))

    return {
        "warehouse_code": code,
        "weight_kg": body.weight_kg,
        "count": len(records),
        "records": records[:200],
    }


@router.get("/files")
def list_files(_user: str = Depends(require_user)) -> Dict[str, Any]:
    idx = _load_index()
    meta = _load_meta()
    file_meta = meta.get("files", {})
    files = []
    for f in idx.get("files", []):
        name = f.get("name", "")
        fm = file_meta.get(name, {})
        files.append({**f, "disabled": fm.get("disabled", f.get("disabled", False))})
    # Files on disk not yet indexed
    disk_names = {f["name"] for f in files}
    for p in sorted(QUOTES_DIR.glob("*.xls*")):
        if p.name not in disk_names and not p.name.startswith("~$"):
            files.append({
                "name": p.name,
                "size": p.stat().st_size,
                "company": "",
                "records": 0,
                "disabled": False,
                "error": "",
            })
    return {"files": files}


@router.post("/upload")
async def upload_files(
    files: List[UploadFile] = File(...),
    company: str = Form(default=""),
    market: str = Form(default=""),
    _user: str = Depends(require_user),
) -> Dict[str, Any]:
    if not files:
        raise HTTPException(400, "未选择文件")

    saved: List[str] = []
    errors: List[str] = []
    meta = _load_meta()
    file_meta = meta.setdefault("files", {})

    for f in files:
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in {".xls", ".xlsx"}:
            errors.append(f"{f.filename}: 格式不支持（仅 xls/xlsx）")
            continue
        content = await f.read()
        if len(content) > 160 * 1024 * 1024:
            errors.append(f"{f.filename}: 文件过大（>160MB）")
            continue
        dest = QUOTES_DIR / f.filename
        dest.write_bytes(content)
        saved.append(f.filename)
        file_meta[f.filename] = {
            "company": company.strip(),
            "market": market.strip(),
            "profile": "auto",
            "source": "upload-default" if company.strip() else "filename",
            "disabled": False,
        }

    _save_meta(meta)

    if not saved:
        raise HTTPException(400, "没有有效文件被保存: " + "; ".join(errors))

    # Rebuild index after upload
    try:
        result = _rebuild()
        return {
            "saved": saved,
            "errors": errors,
            "record_count": result["record_count"],
            "warehouse_count": result["warehouse_count"],
        }
    except Exception as exc:
        return {"saved": saved, "errors": errors + [f"索引构建失败: {exc}"], "record_count": 0}


@router.post("/rebuild")
def rebuild_index(_user: str = Depends(require_user)) -> Dict[str, Any]:
    try:
        result = _rebuild()
        return {
            "ok": True,
            "built_at": result.get("built_at", ""),
            "record_count": result.get("record_count", 0),
            "warehouse_count": result.get("warehouse_count", 0),
            "companies": result.get("companies", []),
        }
    except Exception as exc:
        raise HTTPException(500, f"索引构建失败: {exc}")


@router.post("/files/{filename}/toggle")
def toggle_file(filename: str, _user: str = Depends(require_user)) -> Dict[str, Any]:
    meta = _load_meta()
    file_meta = meta.setdefault("files", {})
    fm = file_meta.setdefault(filename, {})
    fm["disabled"] = not fm.get("disabled", False)
    _save_meta(meta)
    # Rebuild
    try:
        _rebuild()
    except Exception:
        pass
    return {"filename": filename, "disabled": fm["disabled"]}


@router.delete("/files/{filename}")
def delete_file(filename: str, _user: str = Depends(require_user)) -> Dict[str, Any]:
    path = QUOTES_DIR / filename
    if path.exists():
        path.unlink()
    meta = _load_meta()
    meta.get("files", {}).pop(filename, None)
    _save_meta(meta)
    try:
        _rebuild()
    except Exception:
        pass
    return {"ok": True}
