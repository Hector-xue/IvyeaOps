"""一键图片翻译 (One-click Image Translation).

For multi-marketplace sellers: one set of images → translated into many languages
→ reused across sites. Text on an image (title/callouts/labels) is translated and
re-rendered onto the image via gpt-image-2 image-to-image (reference image +
input_fidelity:high + a translate-only instruction), reusing the already-configured
Apimart channel — no new API keys required.

The "图片工作区" (image workspace) is NOT a separate board: it is this board's
sub-area. Images land here from three sources:
  - upload          : user-uploaded images
  - listing         : auto-ingested from the Listing 工作台 when it generates images
  - translation     : the localized outputs this board produces

Storage: files under data/image_workspace/, metadata in image_workspace.sqlite3.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
import uuid
from io import BytesIO
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.config import settings
from app.core.security import require_user
from app.services.ai_synthesis_service import _apimart_base, _apimart_key

router = APIRouter()

WORKSPACE_DIR = settings.data_dir / "image_workspace"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = settings.data_dir / "image_workspace.sqlite3"

# Target marketplaces → language for the translate instruction. English variants
# are included for completeness (mostly a no-op when the source is English).
TARGET_LANGS: dict[str, dict[str, str]] = {
    "DE": {"lang": "German", "locale": "de-DE", "label": "德国 🇩🇪"},
    "FR": {"lang": "French", "locale": "fr-FR", "label": "法国 🇫🇷"},
    "ES": {"lang": "Spanish", "locale": "es-ES", "label": "西班牙 🇪🇸"},
    "IT": {"lang": "Italian", "locale": "it-IT", "label": "意大利 🇮🇹"},
    "JP": {"lang": "Japanese", "locale": "ja-JP", "label": "日本 🇯🇵"},
    "MX": {"lang": "Spanish (Latin America)", "locale": "es-MX", "label": "墨西哥 🇲🇽"},
    "NL": {"lang": "Dutch", "locale": "nl-NL", "label": "荷兰 🇳🇱"},
    "SE": {"lang": "Swedish", "locale": "sv-SE", "label": "瑞典 🇸🇪"},
    "US": {"lang": "English (US)", "locale": "en-US", "label": "美国 🇺🇸"},
    "UK": {"lang": "English (UK)", "locale": "en-GB", "label": "英国 🇬🇧"},
}


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS workspace_images (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            original_name TEXT,
            source TEXT,
            lang TEXT,
            parent_id TEXT,
            project_id TEXT,
            folder_id TEXT,
            created_at REAL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS folders (
            id TEXT PRIMARY KEY,
            name TEXT,
            created_at REAL
        )"""
    )
    conn.commit()
    return conn


_db().close()

# Migration: add folder_id to pre-existing workspace_images tables.
try:
    _mig = _db()
    _mig.execute("ALTER TABLE workspace_images ADD COLUMN folder_id TEXT")
    _mig.commit()
    _mig.close()
except Exception:
    pass


def _row_to_item(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "url": f"/api/image-translate/images/{r['filename']}",
        "original_name": r["original_name"],
        "source": r["source"],
        "lang": r["lang"] or "",
        "parent_id": r["parent_id"] or "",
        "project_id": r["project_id"] or "",
        "folder_id": (r["folder_id"] if "folder_id" in r.keys() else "") or "",
        "created_at": r["created_at"],
    }


def _ext_for(raw: bytes, fallback: str = ".png") -> str:
    if raw[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    return fallback


def save_bytes_to_workspace(
    raw: bytes,
    *,
    source: str,
    original_name: str = "",
    lang: str = "",
    parent_id: str = "",
    project_id: str = "",
    folder_id: str = "",
) -> dict:
    """Persist image bytes into the workspace and return the item dict."""
    img_id = uuid.uuid4().hex
    ext = _ext_for(raw)
    filename = f"{img_id}{ext}"
    (WORKSPACE_DIR / filename).write_bytes(raw)
    created = time.time()
    conn = _db()
    conn.execute(
        "INSERT INTO workspace_images (id, filename, original_name, source, lang, parent_id, project_id, folder_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (img_id, filename, original_name, source, lang, parent_id, project_id, folder_id, created),
    )
    conn.commit()
    conn.close()
    return {
        "id": img_id,
        "url": f"/api/image-translate/images/{filename}",
        "original_name": original_name,
        "source": source,
        "lang": lang,
        "parent_id": parent_id,
        "project_id": project_id,
        "folder_id": folder_id,
        "created_at": created,
    }


async def ingest_url(url: str, *, source: str, project_id: str = "") -> dict | None:
    """Best-effort download a remote image into the workspace (used by Listing).
    Returns the item or None on failure — never raises."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url)
            if resp.status_code != 200 or not resp.content:
                return None
            return save_bytes_to_workspace(resp.content, source=source, project_id=project_id)
    except Exception as exc:  # noqa: BLE001
        logging.warning("[image-translate] ingest_url failed: %s", exc)
        return None


def _nearest_size(raw: bytes) -> str:
    """Map the source aspect ratio to a gpt-image-2 supported size, preserving
    orientation so the translated image keeps the original proportions."""
    try:
        from PIL import Image  # Pillow is a project dependency
        with Image.open(BytesIO(raw)) as im:
            w, h = im.size
        ratio = w / h if h else 1.0
        if ratio > 1.2:
            return "1536x1024"
        if ratio < 0.83:
            return "1024x1536"
        return "1024x1024"
    except Exception:
        return "1024x1024"


def _translate_prompt(lang: str, locale: str) -> str:
    return (
        "Reproduce the attached image EXACTLY — identical product, layout, composition, "
        "colors, graphics, icons, fonts and the position of every element. The ONLY change: "
        f"translate every piece of visible text (titles, callouts, labels, badges, specs) into "
        f"{lang} ({locale}). Keep each text block in the same position, style, color and "
        "approximate size as the original. Do NOT add, remove, restyle, recolor or move "
        "anything other than the language of the text. Do NOT add watermarks or new text. "
        f"Output the same image with only the text translated into {lang}."
    )


async def _poll_task(client: httpx.AsyncClient, task_id: str, max_polls: int = 54, interval: float = 5.0) -> str:
    """Poll the Apimart task until completed; return the result image URL.
    Budget 270s, under the nginx 300s proxy timeout."""
    last_status = "unknown"
    for _ in range(max_polls):
        await asyncio.sleep(interval)
        poll = await client.get(
            f"{_apimart_base()}/tasks/{task_id}",
            headers={"Authorization": f"Bearer {_apimart_key()}"},
        )
        if poll.status_code != 200:
            continue
        data = poll.json().get("data", {})
        last_status = data.get("status") or last_status
        if last_status == "completed":
            images = data.get("result", {}).get("images", [])
            if images and images[0].get("url"):
                url = images[0]["url"]
                return url[0] if isinstance(url, list) else url
            raise RuntimeError("翻译完成但未返回图片")
        if last_status in ("failed", "error"):
            raise RuntimeError(f"翻译任务失败: {data.get('error') or last_status}")
    raise RuntimeError(f"翻译超时（最后状态: {last_status}）")


async def _translate_one(data_uri: str, size: str, code: str, parent_id: str, folder_id: str = "") -> dict:
    """Translate the source image into one language and store the result."""
    meta = TARGET_LANGS.get(code)
    if not meta:
        return {"code": code, "error": f"不支持的语言: {code}"}
    body = {
        "model": "gpt-image-2",
        "prompt": _translate_prompt(meta["lang"], meta["locale"]),
        "n": 1,
        "size": size,
        "image_urls": [data_uri],
    }
    # input_fidelity:high preserves the source layout; fall back to plain if rejected.
    attempts = [{**body, "input_fidelity": "high"}, body]
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = None
            for attempt in attempts:
                resp = await client.post(
                    f"{_apimart_base()}/images/generations",
                    headers={"Authorization": f"Bearer {_apimart_key()}", "Content-Type": "application/json"},
                    json=attempt,
                )
                if resp.status_code == 200:
                    break
            if resp is None or resp.status_code != 200:
                return {"code": code, "error": f"提交失败: {resp.text[:200] if resp is not None else 'no response'}"}
            task_id = resp.json().get("data", [{}])[0].get("task_id")
            if not task_id:
                return {"code": code, "error": "未返回 task_id"}
            url = await _poll_task(client, task_id)
            dl = await client.get(url)
            if dl.status_code != 200 or not dl.content:
                return {"code": code, "error": "结果图下载失败"}
        item = save_bytes_to_workspace(
            dl.content, source="translation", lang=code, parent_id=parent_id, folder_id=folder_id,
        )
        return {"code": code, **item}
    except Exception as exc:  # noqa: BLE001
        return {"code": code, "error": str(exc)[:200]}


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/langs")
def list_langs(_u: str = Depends(require_user)):
    """Available target languages/marketplaces for the picker."""
    return {"langs": [{"code": k, **v} for k, v in TARGET_LANGS.items()]}


@router.get("/workspace")
def list_workspace(_u: str = Depends(require_user)):
    conn = _db()
    rows = conn.execute("SELECT * FROM workspace_images ORDER BY created_at DESC").fetchall()
    conn.close()
    return {"images": [_row_to_item(r) for r in rows]}


@router.post("/workspace/upload")
async def upload_to_workspace(file: UploadFile = File(...), _u: str = Depends(require_user)):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "空文件")
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(400, "图片过大（上限 20MB）")
    item = save_bytes_to_workspace(raw, source="upload", original_name=file.filename or "")
    return item


@router.delete("/workspace/{image_id}")
def delete_workspace_image(image_id: str, _u: str = Depends(require_user)):
    conn = _db()
    row = conn.execute("SELECT * FROM workspace_images WHERE id=?", (image_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "图片不存在")
    try:
        (WORKSPACE_DIR / row["filename"]).unlink(missing_ok=True)
    except OSError:
        pass
    conn.execute("DELETE FROM workspace_images WHERE id=?", (image_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ─── Folders ──────────────────────────────────────────────────────────────────

@router.get("/folders")
def list_folders(_u: str = Depends(require_user)):
    conn = _db()
    folders = conn.execute("SELECT * FROM folders ORDER BY created_at ASC").fetchall()
    # image count per folder (folder_id may be NULL/'' for unfiled)
    counts = {fid: n for fid, n in conn.execute(
        "SELECT COALESCE(folder_id,''), COUNT(*) FROM workspace_images GROUP BY COALESCE(folder_id,'')"
    ).fetchall()}
    conn.close()
    return {
        "folders": [{"id": f["id"], "name": f["name"], "count": counts.get(f["id"], 0)} for f in folders],
        "unfiled_count": counts.get("", 0),
    }


class FolderReq(BaseModel):
    name: str


@router.post("/folders")
def create_folder(body: FolderReq, _u: str = Depends(require_user)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "文件夹名不能为空")
    if len(name) > 40:
        raise HTTPException(400, "文件夹名过长（上限 40 字）")
    fid = uuid.uuid4().hex
    conn = _db()
    conn.execute("INSERT INTO folders (id, name, created_at) VALUES (?, ?, ?)", (fid, name, time.time()))
    conn.commit()
    conn.close()
    return {"id": fid, "name": name, "count": 0}


@router.patch("/folders/{folder_id}")
def rename_folder(folder_id: str, body: FolderReq, _u: str = Depends(require_user)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "文件夹名不能为空")
    conn = _db()
    if not conn.execute("SELECT id FROM folders WHERE id=?", (folder_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "文件夹不存在")
    conn.execute("UPDATE folders SET name=? WHERE id=?", (name[:40], folder_id))
    conn.commit()
    conn.close()
    return {"id": folder_id, "name": name[:40]}


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: str, _u: str = Depends(require_user)):
    """Delete a folder; its images are moved back to 未分类 (not deleted)."""
    conn = _db()
    conn.execute("UPDATE workspace_images SET folder_id='' WHERE folder_id=?", (folder_id,))
    conn.execute("DELETE FROM folders WHERE id=?", (folder_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


class MoveReq(BaseModel):
    folder_id: str = ""  # "" = 未分类


@router.post("/workspace/{image_id}/move")
def move_image(image_id: str, body: MoveReq, _u: str = Depends(require_user)):
    conn = _db()
    if not conn.execute("SELECT id FROM workspace_images WHERE id=?", (image_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "图片不存在")
    if body.folder_id and not conn.execute("SELECT id FROM folders WHERE id=?", (body.folder_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "目标文件夹不存在")
    conn.execute("UPDATE workspace_images SET folder_id=? WHERE id=?", (body.folder_id or "", image_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.get("/images/{filename}")
def serve_image(filename: str, _u: str = Depends(require_user)):
    # Guard against path traversal: only a bare filename is allowed.
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "非法文件名")
    path = WORKSPACE_DIR / filename
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(str(path))


class TranslateReq(BaseModel):
    image_id: str
    target_langs: list[str]


@router.post("/translate")
async def translate_image(body: TranslateReq, _u: str = Depends(require_user)):
    if not _apimart_key():
        raise HTTPException(400, "Apimart 密钥未配置 — 请在「系统配置 → AI 服务」填入有 gpt-image-2 权限的密钥。")
    if not body.target_langs:
        raise HTTPException(400, "请至少选择一个目标语言/站点")

    conn = _db()
    row = conn.execute("SELECT * FROM workspace_images WHERE id=?", (body.image_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "源图不存在")

    raw = (WORKSPACE_DIR / row["filename"]).read_bytes()
    import base64 as _b64
    ext = _ext_for(raw)
    mime = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(ext, "image/png")
    data_uri = f"data:{mime};base64,{_b64.b64encode(raw).decode()}"
    size = _nearest_size(raw)
    folder_id = (row["folder_id"] if "folder_id" in row.keys() else "") or ""

    # Translate every requested language concurrently. (Batch over multiple source
    # images is orchestrated by the frontend calling this once per image, so each
    # request stays well under the nginx 300s proxy timeout.)
    results = await asyncio.gather(
        *[_translate_one(data_uri, size, code, body.image_id, folder_id) for code in body.target_langs]
    )
    return {"results": results}
