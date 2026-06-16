"""Listing Generator — proxy to imgflow backend + AI copywriting + skill-enhanced analysis."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import mimetypes
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.config import settings
from app.core.security import require_user
from app.services.skill_repo import get_skill

router = APIRouter()

DB_PATH = settings.data_dir / "listing.sqlite3"
IMAGES_DIR = settings.data_dir / "listing_images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

def _imgflow_base() -> str:
    from app.core import hub_settings
    url = hub_settings.get("imgflow_url") or "http://127.0.0.1:3001"
    return str(url).rstrip("/") + "/api"


_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


def _imgflow_dir() -> Optional[Path]:
    """Locate the amazon-image-workflow project dir (the Docker 采集服务). Honours
    the optional `imgflow_dir` setting, else looks for it next to / under the
    IvyeaOps install root. Returns None when not found."""
    from app.core import hub_settings
    candidates: list[Path] = []
    configured = hub_settings.get("imgflow_dir")
    if configured:
        candidates.append(Path(str(configured)))
    # runtime_root() resolves to the exe's dir when frozen (Windows x64) and the
    # repo root from source — using __file__.parents[3] would point inside the
    # PyInstaller _MEIPASS temp dir for the exe and never find the shipped folder.
    from app.core.version import runtime_root
    root = runtime_root()
    candidates += [root / "amazon-image-workflow", root.parent / "amazon-image-workflow",
                   Path(__file__).resolve().parents[3] / "amazon-image-workflow"]
    for d in candidates:
        try:
            if d.is_dir() and any((d / f).exists() for f in _COMPOSE_FILES):
                return d
        except Exception:
            continue
    return None


_DOCKER_DL = "https://www.docker.com/products/docker-desktop/"


def _docker_bin() -> Optional[str]:
    """Find the docker CLI. shutil.which covers the normal case; we also probe
    Docker Desktop's standard install path so a Docker that was installed *after*
    IvyeaOps started (PATH not refreshed in our process) is still found without
    forcing a restart."""
    import shutil
    found = shutil.which("docker")
    if found:
        return found
    for p in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
        Path(os.environ.get("ProgramW6432", r"C:\Program Files")) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
    ):
        try:
            if p.is_file():
                return str(p)
        except Exception:
            continue
    return None


def _docker_running(docker: str) -> bool:
    """True when the Docker daemon is up (`docker info` succeeds). Docker Desktop
    can be installed but not started — compose would then fail with a daemon
    error, so we check first to give a clear 'start Docker Desktop' message."""
    import subprocess
    from app.core.proc import no_window_kwargs
    try:
        r = subprocess.run([docker, "info"], capture_output=True, text=True,
                           timeout=12, **no_window_kwargs())
        return r.returncode == 0
    except Exception:
        return False


@router.get("/imgflow/status")
async def imgflow_status(_u: str = Depends(require_user)):
    """Report whether the 采集服务 is reachable, its dir is found, and Docker is
    installed + running — drives the 'start collection service' button in the UI."""
    d = _imgflow_dir()
    reachable = False
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            r = await client.get(_imgflow_base())
            reachable = r.status_code < 500
    except Exception:
        reachable = False
    docker = _docker_bin()
    return {
        "reachable": reachable,
        "dir": str(d) if d else "",
        "docker_installed": bool(docker),
        "docker_running": bool(docker) and _docker_running(docker),
    }


@router.post("/imgflow/start")
def imgflow_start(_u: str = Depends(require_user)):
    """One-click start of the local Docker 采集服务. Only brings up the `backend`
    service (+ its postgres dependency) — the listing board talks to :3001 and
    doesn't need the workflow's own Next.js frontend, so we skip that build.
    Runs detached, logging to data/imgflow-start.log (the --build can take
    minutes, so we never block)."""
    import subprocess
    from app.core.proc import no_window_kwargs

    d = _imgflow_dir()
    if not d:
        raise HTTPException(400, "未找到 amazon-image-workflow 目录。请把该项目放在 IvyeaOps "
                                 "同级目录，或在「系统配置」设置 imgflow_dir 指向它，再试。")
    docker = _docker_bin()
    if not docker:
        raise HTTPException(400, f"未检测到 Docker。这个「完整主图组」采集服务是一套 Docker 应用，"
                                 f"请先安装 Docker Desktop（{_DOCKER_DL}）。装完启动它（等托盘鲸鱼图标变绿），"
                                 f"若仍提示未检测到，重启一次 IvyeaOps 让其识别 Docker，再点此按钮。")
    if not _docker_running(docker):
        raise HTTPException(400, "检测到 Docker 已安装，但 Docker 引擎未运行。请先启动 Docker Desktop，"
                                 "等托盘鲸鱼图标变绿（不再转圈）后再点此按钮。")

    log_path = settings.data_dir / "imgflow-start.log"
    try:
        logf = open(log_path, "ab")
        kw = dict(no_window_kwargs())
        if os.name == "nt":  # detach on Windows so stopping the app won't kill the build
            kw["creationflags"] = kw.get("creationflags", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        else:
            kw["start_new_session"] = True
        # `backend` pulls in its depends_on (postgres) automatically; skipping the
        # frontend service makes the first build much faster.
        subprocess.Popen([docker, "compose", "up", "-d", "--build", "backend"],
                         cwd=str(d), stdout=logf, stderr=subprocess.STDOUT, **kw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"启动采集服务失败：{exc}") from exc
    return {
        "ok": True,
        "dir": str(d),
        "log": str(log_path),
        "detail": "采集服务正在后台启动（docker compose up -d --build backend），首次构建可能需要几分钟"
                  "（拉取 postgres 镜像 + 构建后端）。完成后重新「采集ASIN数据」即可拿到完整主图组。",
    }


# ─── Native Amazon scrape (no Docker) ──────────────────────────────────────────
# The full main-image set lives in the product page's inline JSON as "hiRes"
# entries. Fetch the page with curl — its TLS fingerprint passes Amazon's anti-bot
# where httpx/undici is blocked, and curl ships with Windows 10/11 + every Linux.
# This makes the full image set work WITHOUT the amazon-image-workflow Docker
# stack; that service is now just an optional fallback for when curl is blocked.

_REAL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_MKT_DOMAIN = {
    "US": "amazon.com", "UK": "amazon.co.uk", "DE": "amazon.de", "JP": "amazon.co.jp",
    "FR": "amazon.fr", "IT": "amazon.it", "ES": "amazon.es", "CA": "amazon.ca",
    "AU": "amazon.com.au", "MX": "amazon.com.mx", "IN": "amazon.in", "NL": "amazon.nl",
    "SE": "amazon.se", "PL": "amazon.pl", "AE": "amazon.ae", "SG": "amazon.sg",
}


def _amazon_domain(marketplace: str) -> str:
    return _MKT_DOMAIN.get((marketplace or "US").upper(), "amazon.com")


def _parse_amazon_html(html_text: str) -> dict:
    """Extract title / bullets / full main-image set from raw Amazon product HTML.
    Images come from the inline "hiRes" (then "large") JSON, not the DOM thumbnails
    (which are injected by JS post-load and absent from static HTML)."""
    import html as _html
    images: list[str] = []
    seen: set[str] = set()
    for pat in (r'"hiRes"\s*:\s*"(https?://[^"\\]+)"', r'"large"\s*:\s*"(https?://[^"\\]+)"'):
        if images:
            break
        for m in re.finditer(pat, html_text):
            u = m.group(1)
            if u not in seen:
                seen.add(u)
                images.append(u)
            if len(images) >= 7:
                break
    if not images:
        m = re.search(r'id="landingImage"[^>]*data-old-hires="(https?://[^"]+)"', html_text) \
            or re.search(r'id="landingImage"[^>]*src="(https?://[^"]+)"', html_text)
        if m:
            images.append(m.group(1))

    tm = re.search(r'id="productTitle"[^>]*>(.*?)</', html_text, re.S)
    title = _html.unescape(re.sub(r"\s+", " ", tm.group(1)).strip()) if tm else ""

    bullets: list[str] = []
    fb = re.search(r'id="feature-bullets"(.*?)</ul>', html_text, re.S)
    if fb:
        for bm in re.finditer(r'class="a-list-item[^"]*"[^>]*>(.*?)</span>', fb.group(1), re.S):
            t = _html.unescape(re.sub(r"<[^>]+>", "", bm.group(1)))
            t = re.sub(r"\s+", " ", t).strip()
            if t and t not in bullets:
                bullets.append(t)

    return {"title": title, "bullets": bullets[:5], "description": "", "imageUrls": images}


async def _scrape_amazon_native(asin: str, marketplace: str, attempts: int = 3) -> Optional[dict]:
    """Fetch the Amazon product page via curl and parse the full main-image set.
    Returns None when curl is unavailable or EVERY attempt hits an anti-bot
    challenge / captcha / image-less page — callers then fall back to sorftime.

    Amazon's anti-bot is intermittent: the same IP gets the full ~1.5MB page for
    one request and a ~2-5KB stub for the next, so we retry a few times before
    giving up. A blocked response is tiny and returns almost instantly, so the
    retries add little latency. Tested empirically: richer browser headers AND a
    newer Chrome UA both make the block WORSE, so we deliberately keep the
    request minimal (UA only) — do not "improve" the headers here.

    Uses a synchronous subprocess.run in a worker thread (NOT
    asyncio.create_subprocess_exec): the async variant needs a ProactorEventLoop
    on Windows and silently raised NotImplementedError under uvicorn's loop there,
    so EVERY Windows scrape fell back to the 1-image source. subprocess.run works
    regardless of the event loop — this is the project's Windows-safe pattern."""
    import shutil
    import subprocess
    import logging
    from app.core.proc import no_window_kwargs
    curl = shutil.which("curl")
    if not curl:
        logging.warning("[scrape-native] curl 不在 PATH 上 — 无法本机直连采集 (asin=%s)", asin)
        return None
    url = f"https://www.{_amazon_domain(marketplace)}/dp/{asin}"
    args = [curl, "-sS", "-L", "--max-time", "25", "--compressed", "-A", _REAL_UA, url]
    for i in range(attempts):
        try:
            cp = await asyncio.to_thread(
                subprocess.run, args,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30,
                **no_window_kwargs())
            out = cp.stdout or b""
        except Exception:
            out = b""
        html_text = (out or b"").decode("utf-8", "replace")
        blocked = (
            len(html_text) < 50_000  # anti-bot stub, not the real product page
            or bool(re.search(r"Type the characters you see in this image", html_text, re.I))
            or bool(re.search(r"we just need to make sure you're not a robot", html_text, re.I))
        )
        n_imgs = 0
        if not blocked:
            parsed = _parse_amazon_html(html_text)
            n_imgs = len(parsed.get("imageUrls", []))
            if n_imgs:
                logging.info("[scrape-native] %s 第%d次成功: %dB, %d图", asin, i + 1, len(html_text), n_imgs)
                return parsed
        logging.info("[scrape-native] %s 第%d次未果: %dB blocked=%s imgs=%d", asin, i + 1, len(html_text), blocked, n_imgs)
        if i < attempts - 1:
            await asyncio.sleep(2.0)  # brief backoff — blocks are often transient
    return None


def _apimart_key() -> str:
    """Return configured Apimart key, empty when unset. Image-generation
    callers should surface a clear 'not configured' error rather than
    falling back to a hardcoded shared key (those got banned upstream)."""
    from app.core import hub_settings
    val = hub_settings.get("apimart_key")
    return str(val) if val else ""


def _apimart_base() -> str:
    from app.core import hub_settings
    val = hub_settings.get("apimart_base")
    return str(val) if val else "https://api.apimart.ai/v1"


# ─── SQLite ───────────────────────────────────────────────────────────────────

def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS listing_projects (
        id TEXT PRIMARY KEY,
        asin TEXT NOT NULL,
        marketplace TEXT DEFAULT 'US',
        imgflow_project_id TEXT,
        status TEXT DEFAULT 'created',
        title TEXT,
        bullets TEXT,
        search_terms TEXT,
        aplus_copy TEXT,
        scrape_data TEXT,
        analysis_data TEXT,
        image_slots TEXT,
        created_at REAL,
        updated_at REAL
    )""")
    conn.commit()
    return conn

_db().close()

# Migration: add columns if missing
for _col in ["image_slots TEXT", "templates TEXT", "copy_result TEXT", "copy_job_id TEXT", "highlights TEXT"]:
    try:
        conn = _db()
        conn.execute(f"ALTER TABLE listing_projects ADD COLUMN {_col}")
        conn.commit()
        conn.close()
    except Exception:
        pass


# ─── Models ───────────────────────────────────────────────────────────────────

class CreateProjectReq(BaseModel):
    asin: str
    marketplace: str = "US"
    supplier_url: Optional[str] = None

class GenerateCopyReq(BaseModel):
    type: str
    context: Optional[str] = None

class ProductInfoReq(BaseModel):
    product_name: Optional[str] = None
    description: Optional[str] = None
    selling_points: Optional[str] = None
    target_audience: Optional[str] = None

class ImageGenReq(BaseModel):
    prompt: str
    slot: str
    size: str = "1024x1024"
    reference_urls: list[str] = []


# ─── Project CRUD ─────────────────────────────────────────────────────────────

@router.get("/projects")
def list_projects(_user: str = Depends(require_user)):
    conn = _db()
    rows = conn.execute(
        "SELECT id, asin, marketplace, status, title, created_at, updated_at "
        "FROM listing_projects ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/projects")
async def create_project(body: CreateProjectReq, _user: str = Depends(require_user)):
    pid = str(uuid.uuid4())[:8]
    now = time.time()
    # Try to create an imgflow project for auto-scraping. If the 采集 service
    # isn't running (no Docker / not deployed), fall back to a LOCAL-ONLY project
    # so the user can still fill product info manually + upload images + run AI
    # analysis / copy / prompts. Only the "auto-scrape competitor" step needs it.
    imgflow_id = None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_imgflow_base()}/projects", json={
                "asin": body.asin, "marketplace": body.marketplace,
                "supplierUrl": body.supplier_url or "",
            })
        if resp.status_code in (200, 201):
            data = resp.json()
            imgflow_id = data.get("id") or data.get("project", {}).get("id")
    except httpx.RequestError:
        imgflow_id = None  # 采集服务不可达 → 本地项目

    conn = _db()
    conn.execute(
        "INSERT INTO listing_projects (id,asin,marketplace,imgflow_project_id,status,created_at,updated_at) VALUES (?,?,?,?,'created',?,?)",
        (pid, body.asin, body.marketplace, str(imgflow_id or ""), now, now)
    )
    conn.commit()
    conn.close()
    return {"id": pid, "imgflow_id": imgflow_id, "asin": body.asin,
            "scrape_available": imgflow_id is not None}


@router.get("/projects/{project_id}")
def get_project(project_id: str, _user: str = Depends(require_user)):
    conn = _db()
    row = conn.execute("SELECT * FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "project not found")
    return dict(row)


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, _user: str = Depends(require_user)):
    conn = _db()
    conn.execute("DELETE FROM listing_projects WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ─── Scrape (enhanced: saves reference images) ───────────────────────────────

@router.post("/projects/{project_id}/scrape")
async def scrape(project_id: str, _user: str = Depends(require_user)):
    conn = _db()
    row = conn.execute("SELECT asin, marketplace, imgflow_project_id FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    asin = row["asin"]
    marketplace = row["marketplace"] or "US"
    imgflow_id = row["imgflow_project_id"]

    data = {}

    # 0) Native curl scrape — returns the FULL main-image set with no Docker / no
    #    extra service. This is the primary path now; curl ships with Windows 10+/
    #    Linux and works from the user's residential IP.
    native_ok = False
    try:
        nd = await _scrape_amazon_native(asin, marketplace)
        if nd and nd.get("imageUrls"):
            data = nd
            native_ok = True
    except Exception:
        pass

    # 1) Optional: imgflow scrape (amazon-image-workflow on :3001) — only used as a
    #    fallback now, for users who run the Docker service and where curl was
    #    blocked by anti-bot.
    imgflow_ok = False
    if not native_ok and imgflow_id:
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{_imgflow_base()}/scrape/{imgflow_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    imgflow_ok = bool(data.get("imageUrls") or data.get("images"))
        except Exception:
            pass

    # 2) If imgflow returned empty data, fall back to sorftime product_detail.
    #    NOTE: sorftime only carries ONE (white-background) main image, so this
    #    path can never recover the full set — the UI surfaces a hint to enable
    #    the scrape service (see `scrape_source` below).
    has_title = bool(data.get("title"))
    has_bullets = bool(data.get("bullets"))
    if not has_title and not has_bullets:
        try:
            from app.services import sorftime_service
            import re as _re
            async with sorftime_service._make_client() as client:
                _, raw, err = await sorftime_service._safe_call(
                    client, "product_detail",
                    {"asin": asin, "amzSite": marketplace}, 1,
                )
                if raw and not err and isinstance(raw, str):
                    # Parse structured text: "标题：xxx\n主图：xxx\n产品描述：xxx"
                    title_m = _re.search(r'标题[：:]\s*(.+)', raw)
                    if title_m:
                        data["title"] = title_m.group(1).strip()
                    img_m = _re.search(r'主图[：:]\s*(https?://\S+)', raw)
                    if img_m:
                        data["imageUrls"] = [img_m.group(1).strip()]
                    desc_m = _re.search(r'产品描述[：:]\s*(.+?)(?:\r?\n\r?\n|\r?\n[^\u4e00-\u9fff])', raw, _re.DOTALL)
                    if desc_m:
                        desc_text = desc_m.group(1).strip()
                        # Split by <br> or newlines into bullets
                        parts = _re.split(r'<br>|\n', desc_text)
                        parts = [p.strip() for p in parts if p.strip()]
                        if parts:
                            data["bullets"] = parts[:5]
                            data["description"] = desc_text
        except Exception:
            pass

    # Extract image URLs as reference images
    image_urls = data.get("imageUrls") or data.get("images") or []
    if image_urls:
        data["reference_images"] = image_urls

    # Tell the frontend where the data came from. The Docker-service hint only
    # shows on the sorftime fallback (i.e. native curl scrape was blocked).
    data["scrape_source"] = (
        "native" if native_ok else
        "imgflow" if imgflow_ok else
        "sorftime" if image_urls else "none"
    )
    # The full main-image set is available from native scrape or the imgflow service.
    data["full_images_available"] = native_ok or imgflow_ok

    conn = _db()
    conn.execute(
        "UPDATE listing_projects SET scrape_data = ?, status = 'scraped', updated_at = ? WHERE id = ?",
        (json.dumps(data, ensure_ascii=False), time.time(), project_id)
    )
    conn.commit()
    conn.close()
    return data


# ─── Product Info (manual) ────────────────────────────────────────────────────

@router.post("/projects/{project_id}/product-info")
def save_product_info(project_id: str, body: ProductInfoReq, _user: str = Depends(require_user)):
    conn = _db()
    row = conn.execute("SELECT scrape_data FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404)
    existing = json.loads(row["scrape_data"]) if row["scrape_data"] else {}
    existing["manual"] = {
        "product_name": body.product_name or "",
        "description": body.description or "",
        "selling_points": body.selling_points or "",
        "target_audience": body.target_audience or "",
    }
    conn.execute(
        "UPDATE listing_projects SET scrape_data = ?, updated_at = ? WHERE id = ?",
        (json.dumps(existing, ensure_ascii=False), time.time(), project_id)
    )
    conn.commit()
    conn.close()
    return {"ok": True}


# ─── Image Slots Persistence ──────────────────────────────────────────────────

@router.post("/projects/{project_id}/image-slots")
def save_image_slots(project_id: str, body: dict, _user: str = Depends(require_user)):
    """Save image slot data (prompts, urls, sizes) for cross-device sync."""
    conn = _db()
    row = conn.execute("SELECT id FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404)
    conn.execute(
        "UPDATE listing_projects SET image_slots = ?, updated_at = ? WHERE id = ?",
        (json.dumps(body, ensure_ascii=False), time.time(), project_id)
    )
    conn.commit()
    conn.close()
    return {"ok": True}


# ─── Image Upload & Reference ─────────────────────────────────────────────────

@router.post("/projects/{project_id}/upload-image")
async def upload_product_image(project_id: str, file: UploadFile = File(...), _user: str = Depends(require_user)):
    """Upload a product reference image."""
    conn = _db()
    row = conn.execute("SELECT id FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)

    proj_dir = IMAGES_DIR / project_id
    proj_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "img.jpg").suffix or ".jpg"
    fname = f"{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
    dest = proj_dir / fname
    content = await file.read()
    dest.write_bytes(content)

    # Add to scrape_data.uploaded_images
    conn = _db()
    row = conn.execute("SELECT scrape_data FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    existing = json.loads(row["scrape_data"]) if row and row["scrape_data"] else {}
    uploaded = existing.get("uploaded_images", [])
    uploaded.append(str(dest))
    existing["uploaded_images"] = uploaded
    conn.execute(
        "UPDATE listing_projects SET scrape_data = ?, updated_at = ? WHERE id = ?",
        (json.dumps(existing, ensure_ascii=False), time.time(), project_id)
    )
    conn.commit()
    conn.close()
    return {"path": str(dest), "filename": fname}


@router.get("/projects/{project_id}/reference-images")
def get_reference_images(project_id: str, _user: str = Depends(require_user)):
    """Get all reference images (scraped URLs + uploaded files)."""
    conn = _db()
    row = conn.execute("SELECT scrape_data FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    data = json.loads(row["scrape_data"]) if row["scrape_data"] else {}
    # Return uploaded images as serving URLs (not raw file paths)
    uploaded_paths = data.get("uploaded_images", [])
    uploaded_urls = []
    for p in uploaded_paths:
        path_obj = Path(p)
        if path_obj.exists():
            uploaded_urls.append({
                "filename": path_obj.name,
                "url": f"/api/listing/images/{project_id}/{path_obj.name}",
            })
    return {
        "scraped": data.get("reference_images", []),
        "uploaded": uploaded_urls,
    }


@router.delete("/projects/{project_id}/uploaded-image/{filename}")
def delete_uploaded_image(project_id: str, filename: str, _user: str = Depends(require_user)):
    """Delete an uploaded reference image."""
    path = IMAGES_DIR / project_id / filename
    if path.exists():
        path.unlink()
    # Remove from scrape_data
    conn = _db()
    row = conn.execute("SELECT scrape_data FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    if row:
        data = json.loads(row["scrape_data"]) if row["scrape_data"] else {}
        uploaded = [p for p in data.get("uploaded_images", []) if not p.endswith(f"/{filename}")]
        data["uploaded_images"] = uploaded
        conn.execute(
            "UPDATE listing_projects SET scrape_data=?, updated_at=? WHERE id=?",
            (json.dumps(data, ensure_ascii=False), time.time(), project_id),
        )
        conn.commit()
    conn.close()
    return {"ok": True}


# ─── AI Provider Fallback Chain ───────────────────────────────────────────────


async def _call_ai(prompt: str, max_tokens: int = 2000, web_search: bool = True) -> str:
    """Generate text via the standard fallback chain:
    Hermes → 全局兜底大模型 → Codex → Claude.

    Listing AI is a pure text engine (the prompt forbids tools/commands), so it
    rides the shared ``run_text_chain`` orchestrator — the exact same chain every
    other board uses, gaining the global fallback model and Claude automatically.
    """
    from app.services import ai_synthesis_service

    task_prompt = (
        "你正在作为 Listing 生成板块的纯文本生成引擎。"
        "禁止执行命令、禁止读写文件、禁止修改系统、禁止调用工具；只根据提示词内容返回最终文本。\n\n"
        + prompt
    )
    if not web_search:
        task_prompt = "不要联网搜索，不要调用工具，只基于下面提供的信息回答。\n\n" + task_prompt
    task_prompt = (
        f"{task_prompt}\n\n"
        "输出要求：直接输出最终内容，不要解释调用过程，不要添加 Markdown 代码块。"
    )

    # Use the user's configured provider order (text_ai_providers — hermes-first
    # by default). Hermes is slow for big generations but the long-request path
    # is sized for it (frontend 15-min axios timeout + nginx 900s on the listing
    # generate endpoints), so a slow hermes run completes instead of being cut.
    # (apimart is image-gen only and is already excluded from the text chain.)
    try:
        _provider, text = await ai_synthesis_service.run_text_chain(task_prompt)
        return text
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"AI 调用失败（Hermes / 全局兜底 / Codex / Claude 均不可用）：{e}")


async def _review_single_prompt(
    initial_prompt: str,
    slot_label: str,
    slot_size: str,
    color_scheme: str = "",
) -> str:
    """Self-check and optimize one generated image prompt — cosmetic fixes only, no invented content."""
    color_rule = (
        f"\n- MUST keep the color scheme '{color_scheme}' if already present; do not remove it."
        if color_scheme and color_scheme.strip().lower() != "auto" else ""
    )
    review_prompt = f"""You are a prompt EDITOR. Your ONLY job is cosmetic syntax improvements — you must NOT invent or change any facts.

SLOT: {slot_label}  |  CANVAS: {slot_size or "not specified"}

━━━ ABSOLUTE PROHIBITIONS (violation = output the draft unchanged) ━━━
• DO NOT add, change, or remove any reference image URL ("Reference: https://..." lines must appear verbatim)
• DO NOT modify the product's physical description — keep every color, material, shape, size, and feature exactly as written
• DO NOT invent any spec, feature, or scene detail that is not already present in the draft
• DO NOT change what the image is supposed to show or its scene/setting{color_rule}

━━━ ALLOWED FIXES (cosmetic only) ━━━
1. LIGHTING: Replace vague phrases ("good lighting", "bright light") with a concrete rig description ONLY if you can infer it from the existing scene context — e.g., white-background studio → "3-point studio lighting, 45° softbox key, fill reflector"
2. CANVAS: If "{slot_size}" is not already mentioned in the draft, append a sentence like "Compose for {slot_size} canvas." at the end
3. FILLER REMOVAL: Remove hollow adjectives ("beautiful", "stunning", "perfect", "amazing") — do NOT replace them with invented specifics, just remove them
4. COMPOSITION: If no camera angle is specified, add one neutral descriptor (e.g., "eye-level hero angle") that fits the existing scene

DRAFT PROMPT:
{initial_prompt}

OUTPUT: Edited prompt text only. If no valid fix is needed, return the draft unchanged. No prefix, no explanation."""

    try:
        reviewed = await _call_ai(review_prompt, max_tokens=1000, web_search=False)
        return reviewed.strip() if reviewed.strip() else initial_prompt
    except Exception:
        return initial_prompt


async def _review_batch_prompts(
    prompts: dict,
    slot_details: list,
    color_scheme: str = "",
) -> dict:
    """Batch self-check and optimize all generated prompts in a single AI call."""
    if not prompts:
        return prompts

    slot_map = {s["id"]: s for s in slot_details}
    color_rule = (
        f"\n- MANDATORY: Maintain the '{color_scheme}' color scheme throughout all prompts."
        if color_scheme and color_scheme.strip().lower() != "auto" else ""
    )

    prompts_block = "\n\n".join(
        f'SLOT: {sid}\nLABEL: {slot_map.get(sid, {}).get("label", sid)}\n'
        f'CANVAS: {slot_map.get(sid, {}).get("size", "")}\nDRAFT:\n{txt}'
        for sid, txt in prompts.items()
    )
    slot_ids_json = ", ".join(f'"{s}":"improved_prompt_here"' for s in prompts)

    review_prompt = f"""You are a prompt EDITOR reviewing {len(prompts)} image prompts. Your ONLY job is cosmetic syntax fixes — you must NOT invent or change any facts.

━━━ ABSOLUTE PROHIBITIONS (violation = output that prompt unchanged) ━━━
• DO NOT add, change, or remove any reference image URL ("Reference: https://..." lines must appear verbatim in every prompt that already has them)
• DO NOT modify the product's physical description — keep every color, material, shape, size, and feature exactly as written
• DO NOT invent any spec, feature, or scene detail that is not already present in the draft
• DO NOT change what any image is supposed to show or its scene/setting{color_rule}

━━━ ALLOWED FIXES (cosmetic only, apply to every prompt) ━━━
1. LIGHTING: Replace vague phrases ("good lighting", "bright light") with a concrete rig description ONLY if inferable from the existing scene context
2. CANVAS: If the slot's canvas size is not already mentioned, append a sentence like "Compose for <size> canvas." at the end
3. FILLER REMOVAL: Remove hollow adjectives ("beautiful", "stunning", "perfect", "amazing") — do NOT replace with invented specifics, just remove them
4. COMPOSITION: If no camera angle is specified, add one neutral descriptor that fits the existing scene

DRAFT PROMPTS:
{prompts_block}

OUTPUT FORMAT (valid JSON, no other text):
{{"prompts":{{{slot_ids_json}}}}}"""

    try:
        content = await _call_ai(review_prompt, max_tokens=8000, web_search=False)
        result = _parse_json_response(content)
        if result and isinstance(result.get("prompts"), dict):
            reviewed = result["prompts"]
            return {
                sid: str(reviewed[sid]).strip()
                if reviewed.get(sid) and str(reviewed[sid]).strip()
                else orig
                for sid, orig in prompts.items()
            }
    except Exception:
        pass

    return prompts


def _reference_images(scrape_data: dict) -> list[str]:
    refs = scrape_data.get("reference_images") or scrape_data.get("imageUrls") or []
    if isinstance(refs, str):
        return [refs]
    return [str(x) for x in refs if str(x).strip()] if isinstance(refs, list) else []


def _slot_purpose(slot_id: str, label: str) -> str:
    key = slot_id.lower()
    if key == "main":
        return "pure white Amazon main image, product centered, no text, shopper can inspect the full product"
    if key.startswith("sub1"):
        return "lifestyle scene showing the primary use case and buyer outcome"
    if key.startswith("sub2"):
        return "feature detail image with concise benefit callouts"
    if key.startswith("sub3"):
        return "size, scale, specification, or usage clarity image"
    if key.startswith("sub4"):
        return "multi-angle, structure, technology, or material detail image"
    if key.startswith("sub5"):
        return "package, accessories, kit contents, or value summary image"
    if key.startswith("sub6"):
        return "multi-scenario benefit summary image"
    if "banner" in key:
        return "Premium A+ hero banner with brand-level composition"
    if "compare" in key or key.endswith("_4"):
        return "A+ comparison, trust, specification, or advantage module"
    if "brand" in key:
        return "brand story and trust-building A+ module"
    return f"{label or slot_id} product image module"


def _fallback_image_prompt(
    slot_id: str,
    label: str,
    size: str,
    row,
    scrape_data: dict,
    analysis_data: dict,
    color_scheme: str = "",
    template_hint: str = "",
) -> str:
    src = _copy_source(row, scrape_data, analysis_data)
    refs = _reference_images(scrape_data)
    ref = refs[0] if refs else "no reference image available"
    product_lock = _clean_text(
        analysis_data.get("product_lock")
        or f"{src['title']} exactly as shown in the reference image; keep the real shape, color, materials, proportions, logo placement, and included accessories unchanged."
    )
    features = src["usp"] + src["bullets"]
    feature = _clean_text(features[0]) if features else _clean_text(src["description"] or src["title"])
    canvas = size or ("1400x1400 or larger square" if slot_id == "main" else "configured slot size")
    purpose = _slot_purpose(slot_id, label)
    color_line = f" Use a {color_scheme} palette for backgrounds, props, lighting, and typography." if color_scheme else ""
    template_line = f" Adapt this template direction without copying unsupported claims: {template_hint[:420]}." if template_hint else ""
    text_rule = "no text, no badges, no icons" if slot_id == "main" else "short English text callouts that communicate the benefit"
    return (
        f"{product_lock} Reference: {ref}. Create a {purpose} for slot \"{label or slot_id}\". "
        f"Target canvas: {canvas}; compose specifically for this size and orientation. "
        f"Image goal: communicate {feature[:220]}. "
        f"Use commercial Amazon product photography with accurate product rendering, controlled studio lighting, natural shadows, sharp focus, realistic materials, and clean premium composition.{color_line} "
        f"Composition: product remains visually dominant, with enough negative space for {text_rule}. "
        f"For A+ desktop modules use a wide 1464x600 layout when requested; for mobile modules use a compact 600x450 layout when requested. "
        f"Do not invent specs, certifications, accessories, colors, or features not present in the product data. {template_line}".strip()
    )


def _fallback_prompts_for_slots(row, scrape_data: dict, analysis_data: dict, slot_details: list[dict], color_scheme: str = "", template_hint: str = "") -> dict:
    prompts = {}
    for s in slot_details:
        prompts[s["id"]] = _fallback_image_prompt(
            s["id"],
            s.get("label") or s["id"],
            s.get("size") or "",
            row,
            scrape_data,
            analysis_data,
            color_scheme,
            template_hint,
        )
    return {
        "product_lock": analysis_data.get("product_lock") or _copy_source(row, scrape_data, analysis_data)["title"],
        "visual_style": analysis_data.get("visual_style") or "Premium Amazon commercial photography with consistent product appearance.",
        "prompts": prompts,
        "fallback": True,
        "warning": "Hermes/Codex 当前不可用，已用本地规则生成可编辑图片提示词；恢复额度后可重新智能生成。",
    }


def _fallback_template_content(content: str) -> str:
    text = content.strip()
    text = re.sub(r"https?://\S+", "{reference_url}", text)
    text = re.sub(r"\b(#[0-9a-fA-F]{3,8})\b", "{color_scheme}", text)
    if "{product_lock}" not in text:
        text = "{product_lock}\nReference: {reference_url}\n" + text
    if "{visual_style}" not in text:
        text += "\nVisual style: {visual_style}. Color direction: {color_scheme}."
    return text


def _fallback_analysis(row, scrape_data: dict, analysis_data: dict) -> dict:
    src = _copy_source(row, scrape_data, analysis_data)
    features = src["usp"] + src["bullets"] or [src["description"] or src["title"]]
    return {
        "usp": [f[:120] for f in features[:3]],
        "target_audience": src["audience"],
        "scenarios": ["Primary product use case", "Everyday comparison shopping", "Gift, home, work, travel, or category-relevant use"],
        "keywords": (src["keywords"] + _keywords_from_text(" ".join([src["title"], src["description"], " ".join(src["bullets"])])))[:15],
        "image_strategy": {
            "main": "Show the exact product clearly on a pure white Amazon-ready background.",
            "sub1": "Show the primary buyer outcome in context.",
            "sub2": "Highlight the strongest feature with concise callouts.",
            "sub3": "Clarify size, use, or compatibility from available data.",
            "sub4": "Show details, structure, or material quality.",
            "sub5": "Show included items or value summary.",
            "sub6": "Close with scenarios, trust, or benefit summary.",
        },
        "cosmo_score": "local-fallback",
        "optimization_suggestions": ["补充真实规格和卖点", "上传清晰参考图", "恢复 Hermes/Codex 后重新运行智能分析"],
    }


# ─── Skill-Enhanced AI Analysis ───────────────────────────────────────────────

def _load_skill_knowledge() -> str:
    """Load relevant skill knowledge for analysis prompts."""
    parts = []
    try:
        creative = get_skill("amazon/amazon-listing-creative")
        parts.append(f"[LISTING CREATIVE STRATEGY]\n{creative.content_body[:3000]}")
    except Exception:
        pass
    try:
        audit = get_skill("amazon/amazon-asin-cosmo-rufus-audit")
        parts.append(f"[ASIN AUDIT METHODOLOGY]\n{audit.content_body[:2000]}")
    except Exception:
        pass
    return "\n\n".join(parts)


# ─── Vision: analyze ALL product images (scraped + uploaded) ──────────────────
# The vision providers cap each call at 4 images, so we batch and aggregate.

_VISION_BATCH = 4


async def _img_datauri_from_url(url: str) -> Optional[str]:
    """Download an image URL and return a base64 data-URI, or None on failure."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=10), follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
        ct = (r.headers.get("content-type") or "").split(";")[0].strip()
        if not ct.startswith("image/"):
            ct = mimetypes.guess_type(url)[0] or "image/jpeg"
        return f"data:{ct};base64,{base64.b64encode(r.content).decode()}"
    except Exception:
        return None


def _img_datauri_from_path(path_str: str) -> Optional[str]:
    """Read a local uploaded image and return a base64 data-URI, or None."""
    try:
        p = Path(path_str)
        if not p.is_file():
            return None
        ct = mimetypes.guess_type(str(p))[0] or "image/jpeg"
        return f"data:{ct};base64,{base64.b64encode(p.read_bytes()).decode()}"
    except Exception:
        return None


async def _collect_vision(prompt: str, images_b64: list[str]) -> str:
    """Run the vision fallback chain and collect its text output."""
    from app.services import ai_synthesis_service
    parts: list[str] = []
    async for prov, chunk in ai_synthesis_service.stream_vision(prompt, images_b64):
        if prov != "error":
            parts.append(chunk)
    return "".join(parts).strip()


async def _analyze_all_images(scrape_data: dict) -> str:
    """Vision-analyze EVERY scraped + uploaded image and return an aggregated
    "图片卖点清单" used downstream by analysis / copy / image-prompt generation.

    Returns "" when no images or no vision model is configured.
    """
    from app.services import ai_synthesis_service
    if not ai_synthesis_service.has_vision_capability():
        return ""

    images: list[str] = []
    for url in _reference_images(scrape_data):
        d = await _img_datauri_from_url(url)
        if d:
            images.append(d)
    for path_str in scrape_data.get("uploaded_images", []) or []:
        d = _img_datauri_from_path(path_str)
        if d:
            images.append(d)
    if not images:
        return ""

    total = len(images)
    batch_notes: list[str] = []
    for i in range(0, total, _VISION_BATCH):
        batch = images[i:i + _VISION_BATCH]
        lo, hi = i + 1, i + len(batch)
        prompt = (
            f"这是某亚马逊产品的第 {lo}-{hi} 张图片（共 {total} 张）。请逐张分析并提取：\n"
            "① 体现的核心卖点 / 功能点；② 视觉风格 / 构图 / 配色；"
            "③ 使用场景 / 目标人群；④ 可直接复用到文案与图片提示词的要点。\n"
            "用简洁中文分点输出，每张图前标注其序号。"
        )
        try:
            text = await _collect_vision(prompt, batch)
        except Exception:
            text = ""
        if text:
            batch_notes.append(f"【图 {lo}-{hi}】\n{text}")

    if not batch_notes:
        return ""

    combined = "\n\n".join(batch_notes)
    # Aggregate the per-batch notes into one de-duplicated, prioritized list.
    try:
        summary = await _call_ai(
            "以下是对一组产品图片逐批的视觉分析。请汇总成一份『图片卖点清单』："
            "去重合并、按重要度排序，明确列出可用于 Listing 文案与图片提示词的"
            "卖点、视觉风格与使用场景。\n\n" + combined,
            web_search=False,
        )
        return (summary or "").strip() or combined
    except Exception:
        return combined


@router.post("/projects/{project_id}/ai-analyze")
async def ai_analyze(project_id: str, _user: str = Depends(require_user)):
    """Run skill-enhanced AI analysis + imgflow deep analysis (COSMO/Rufus/SIF)."""
    conn = _db()
    row = conn.execute("SELECT * FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)

    scrape_data = json.loads(row["scrape_data"]) if row["scrape_data"] else {}
    product_context = _build_product_context(row, scrape_data, {})
    skill_knowledge = _load_skill_knowledge()

    # 0. Vision-analyze EVERY scraped + uploaded image into a selling-point list,
    #    reused downstream by copy + image-prompt generation (stored on the project).
    image_insights = await _analyze_all_images(scrape_data)

    # 1. Call imgflow deep analysis (COSMO/Rufus/SIF/Sorftime)
    imgflow_analysis = {}
    imgflow_id = row["imgflow_project_id"]
    if imgflow_id:
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(f"{_imgflow_base()}/analysis/{imgflow_id}")
                if resp.status_code == 200:
                    imgflow_analysis = resp.json()
        except Exception:
            pass

    # 2. Skill-enhanced AI analysis
    prompt = f"""你是Amazon产品分析专家。基于以下专业知识和产品信息，进行深度分析。

## 专业知识参考
{skill_knowledge[:4000]}

## 产品信息
{product_context}

## 产品图片视觉分析（采集 + 上传的全部图片）
{image_insights or "（未配置视觉模型或暂无图片）"}

## imgflow深度分析数据
{json.dumps(imgflow_analysis, ensure_ascii=False)[:2000] if imgflow_analysis else "未获取到"}

请输出结构化分析（JSON格式）：
{{
  "usp": ["核心卖点1", "核心卖点2", "核心卖点3"],
  "target_audience": "目标受众描述",
  "scenarios": ["使用场景1", "使用场景2", "使用场景3"],
  "keywords": ["关键词1", "关键词2", ...最多15个],
  "image_strategy": {{
    "main": "主图策略建议",
    "sub1": "副图1策略(USP概览)",
    "sub2": "副图2策略(对比图)",
    "sub3": "副图3策略(场景图)",
    "sub4": "副图4策略(技术/细节)",
    "sub5": "副图5策略(效果展示)",
    "sub6": "副图6策略(包装/配件)"
  }},
  "cosmo_score": "基于分析的COSMO评分估计(0-100)",
  "optimization_suggestions": ["建议1", "建议2", "建议3"]
}}

直接输出JSON，不要其他文字。"""

    fallback_used = False
    warning = None
    try:
        content = await _call_ai(prompt, max_tokens=3000)
    except HTTPException as e:
        structured = _fallback_analysis(row, scrape_data, {})
        content = json.dumps(structured, ensure_ascii=False)
        fallback_used = True
        warning = f"AI 当前不可用（Hermes/全局兜底/Codex/Claude 均失败），已使用本地规则生成基础分析。原因：{str(e.detail)[:220]}"

    # Merge imgflow data with AI analysis
    combined = {"ai_analysis": content, "imgflow": imgflow_analysis, "image_insights": image_insights}
    if fallback_used:
        combined["fallback"] = True
        combined["warning"] = warning
    try:
        parsed = json.loads(content.strip().strip("```json").strip("```"))
        combined["structured"] = parsed
    except Exception:
        combined["structured"] = None

    conn = _db()
    conn.execute(
        "UPDATE listing_projects SET analysis_data = ?, status = 'analyzed', updated_at = ? WHERE id = ?",
        (json.dumps(combined, ensure_ascii=False), time.time(), project_id)
    )
    conn.commit()
    conn.close()
    return combined


# ─── Proxy: imgflow analysis (legacy) ────────────────────────────────────────

@router.post("/projects/{project_id}/analyze")
async def analyze(project_id: str, _user: str = Depends(require_user)):
    conn = _db()
    row = conn.execute("SELECT imgflow_project_id FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    imgflow_id = row["imgflow_project_id"]
    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(f"{_imgflow_base()}/analysis/{imgflow_id}")
        if resp.status_code != 200:
            raise HTTPException(502, f"analysis failed: {resp.text}")
        data = resp.json()
    conn = _db()
    conn.execute(
        "UPDATE listing_projects SET analysis_data = ?, status = 'analyzed', updated_at = ? WHERE id = ?",
        (json.dumps(data, ensure_ascii=False), time.time(), project_id)
    )
    conn.commit()
    conn.close()
    return data


# ─── Copy Generation ──────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/copy")
async def generate_copy(project_id: str, body: GenerateCopyReq, _user: str = Depends(require_user)):
    conn = _db()
    row = conn.execute("SELECT * FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)

    scrape_data = json.loads(row["scrape_data"]) if row["scrape_data"] else {}
    analysis_data = json.loads(row["analysis_data"]) if row["analysis_data"] else {}
    product_context = _build_product_context(row, scrape_data, analysis_data)
    # Fold the all-image vision selling-points (from ai-analyze) into the context
    # so every copy variant is grounded in what the product images actually show.
    _img_sp = analysis_data.get("image_insights", "")
    if _img_sp:
        product_context = f"{product_context}\n\n## 图片卖点（对采集+上传全部图片的视觉分析）\n{_img_sp}"

    prompts = {
        "title": f"""你是Amazon Listing优化专家。生成3个优化后的产品标题候选。
要求（亚马逊2026-07-27新规）：每个标题**不超过75个字符（含空格）**，所有分类统一上限。
结构：品牌 + 产品类型 + 1-2个最核心关键词/特性，前80字符内前置产品类型与主关键词（手机端友好）。
不要堆砌关键词、不要塞规格清单——次要关键词放到「商品亮点」和五点里。Title Case，英文输出。
产品信息：
{product_context}
{f"额外要求：{body.context}" if body.context else ""}
输出3个标题，数字编号，每个单独一行。每个标题后用括号标注字符数，如 (62 chars)。""",

        "highlights": f"""你是Amazon Listing优化专家。生成「商品亮点 Product Highlights」（亚马逊2026-07-27新增字段）。
要求：
- 一行短语串，**总长度不超过125个字符（含空格）**。
- 用「产品特性/优势」的**短语**，不是完整句子；多个短语用英文逗号 ", " 分隔。
- 覆盖材质、核心功能、使用场景、兼容性等关键信息（参考示例：Non-stick, Food Grade, Heat Resistant 220°C, Fits Ninja Crispi）。
- 该字段**可被搜索**，自然嵌入与标题不重复的核心关键词。
- 仅当标题<75字符时前台展示，所以要言之有物、信息密度高。英文输出。
产品信息：
{product_context}
{f"额外要求：{body.context}" if body.context else ""}
直接输出一行亮点短语串，并在末尾用括号标注字符数，如 (118 chars)。""",

        "bullets": f"""你是Amazon Listing优化专家。生成5条Bullet Points（五点描述，新规下保持不变）。
要求：大写关键词开头(如 PREMIUM QUALITY:)，每条150-250字符，英文输出。
覆盖产品细节、使用场景、材质说明、注意事项、售后信息。
产品信息：
{product_context}
{f"额外要求：{body.context}" if body.context else ""}""",

        "search_terms": f"""你是Amazon SEO专家。生成后台搜索词。
要求：≤250字节，不重复标题词，空格分隔，英文输出。
产品信息：
{product_context}
直接输出搜索词。""",

        "aplus": f"""你是Amazon A+内容策划专家。生成A+ Content文案。
输出：1.品牌故事 2.横幅标题 3.三个特性模块 4.对比图文案 5.三个场景描述。英文输出。
产品信息：
{product_context}
{f"额外要求：{body.context}" if body.context else ""}""",
    }

    if body.type not in prompts:
        raise HTTPException(400, f"type must be one of: {list(prompts.keys())}")

    fallback_used = False
    warning = None
    try:
        content = await _call_ai(prompts[body.type])
    except HTTPException as e:
        detail = str(e.detail)
        content = _fallback_copy(body.type, row, scrape_data, analysis_data)
        fallback_used = True
        warning = f"AI 当前不可用（Hermes/全局兜底/Codex/Claude 均失败），已使用本地规则生成一版可编辑文案。原因：{detail[:220]}"

    field_map = {"title": "title", "bullets": "bullets", "search_terms": "search_terms", "aplus": "aplus_copy", "highlights": "highlights"}
    conn = _db()
    conn.execute(
        f"UPDATE listing_projects SET {field_map[body.type]} = ?, updated_at = ? WHERE id = ?",
        (content, time.time(), project_id)
    )
    conn.commit()
    conn.close()
    return {"type": body.type, "content": content, "fallback": fallback_used, "warning": warning}


# ─── Generate ALL Prompts at Once (unified style) ─────────────────────────────

@router.post("/projects/{project_id}/generate-all-prompts")
async def generate_all_prompts(project_id: str, body: dict, _user: str = Depends(require_user)):
    """Generate image prompts using 8-step methodology in a single AI call."""
    conn = _db()
    row = conn.execute("SELECT * FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)

    scrape_data = json.loads(row["scrape_data"]) if row["scrape_data"] else {}
    analysis_data = json.loads(row["analysis_data"]) if row["analysis_data"] else {}
    product_context = _build_product_context(row, scrape_data, analysis_data)

    ref_images = scrape_data.get("reference_images", []) or scrape_data.get("imageUrls", [])
    ref_urls_text = "\n".join(ref_images[:3]) if ref_images else "No reference images."
    img_sp = analysis_data.get("image_insights", "")

    sizes = body.get("sizes", {})
    if isinstance(sizes, dict) and "sizes" in sizes:
        sizes = sizes["sizes"]
    color_scheme = body.get("color_scheme", "")
    all_slots = [
        ("main", "白底主图"), ("sub1", "副图1"), ("sub2", "副图2"), ("sub3", "副图3"),
        ("sub4", "副图4"), ("sub5", "副图5"), ("sub6", "副图6"),
        ("aplus_banner", "A+横幅"), ("aplus_1", "A+模块1"), ("aplus_2", "A+模块2"),
        ("aplus_3", "A+模块3"), ("aplus_4", "A+对比"), ("brand_story", "品牌故事"),
    ]

    color_directive = _color_directive(color_scheme, analysis_data)
    approved = _approved_copy(row)
    approved_block = (f"\n\n## APPROVED LISTING COPY (use these EXACT words for any on-image text / callouts)\n{approved}"
                      if approved else "")

    prompt = f"""You are an Amazon listing image strategist. Complete ALL steps below in one response.

IMPORTANT: Do NOT use web search. Do NOT look up any information online. Work ONLY with the product information provided below. Respond immediately with the JSON output.

## PRODUCT INFO
{product_context}{approved_block}

## REFERENCE IMAGES (only source of truth for product appearance)
{ref_urls_text}

## VISUAL ANALYSIS OF ALL IMAGES (selling points / style / scenes extracted from EVERY scraped + uploaded image)
{img_sp or "(not available — no vision model configured or no images)"}

## STEPS TO FOLLOW:
1. IDENTIFY product appearance from reference images (shape, color, material, features, accessories)
2. Determine CATEGORY and top 5 BUYER CONCERNS before purchase
3. Assign each of 7 main images a DIFFERENT sales task solving one buyer concern
4. Write PRODUCT LOCK: strict appearance description + what NOT to add/change
5. Choose VISUAL STYLE based on category buyer psychology
6. Write 13 PROMPTS (120-180 words each) with structure below

## VISUAL QUALITY RULES (apply to EVERY prompt):
- Use cinematic photography language: specify focal length (85mm, 50mm, 35mm), aperture (f/1.8, f/2.8), depth of field
- Specify lighting precisely: "soft key light from upper left with warm fill", "golden hour rim lighting", "three-point studio lighting with hair light"
- Include color grading: "warm amber tones", "cool teal shadows with warm highlights", "rich earth tones with orange accents"
- Add texture/material rendering: "visible surface texture", "light catching micro-details", "condensation droplets"
- For lifestyle scenes: "shot on Sony A7IV", "editorial photography", "National Geographic style"
- For main image: "commercial product photography", "phase-one medium format quality", "razor sharp focus"
- AVOID flat infographic style. Instead of "infographic background with callouts", use "premium 3D render environment with floating glass panels containing text" or "cinematic split-screen composition"
{color_directive}
{_TEXT_RULE}
{_FIDELITY_RULE}

## CRITICAL RULES:
- Main image: pure white background, product 85%, no text, studio lighting that reveals every texture
- Every prompt MUST start with the product appearance description (same across all 13)
- Do NOT invent specs not in product info
- For images with text: describe text as part of a premium graphic design composition, not cheap stickers
- Keep product consistent across ALL images
- Each scene should feel like a $10,000 commercial photoshoot, not a stock photo

## OUTPUT FORMAT (valid JSON, no other text):
{{"product_lock":"strict appearance description and prohibitions","visual_style":"style + color palette + why","category":"exact Amazon category","buyer_concerns":["c1","c2","c3","c4","c5"],"prompts":{{"main":"prompt...","sub1":"prompt...","sub2":"prompt...","sub3":"prompt...","sub4":"prompt...","sub5":"prompt...","sub6":"prompt...","aplus_banner":"prompt...","aplus_1":"prompt...","aplus_2":"prompt...","aplus_3":"prompt...","aplus_4":"prompt...","brand_story":"prompt..."}}}}"""

    try:
        content = await _call_ai(prompt, max_tokens=16000, web_search=False)
    except HTTPException:
        slot_details = _slot_details_from_body(body, [sid for sid, _ in all_slots])
        return _fallback_prompts_for_slots(row, scrape_data, analysis_data, slot_details, color_scheme)
    result = _parse_json_response(content)

    if not result or not result.get("prompts"):
        return {"raw": content[:2000], "error": "Failed to parse response"}

    # Save product_lock to DB
    if result.get("product_lock"):
        conn = _db()
        existing = json.loads(row["analysis_data"]) if row["analysis_data"] else {}
        existing["product_lock"] = result["product_lock"]
        existing["visual_style"] = result.get("visual_style", "")
        existing["category"] = result.get("category", "")
        conn.execute(
            "UPDATE listing_projects SET analysis_data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(existing, ensure_ascii=False), time.time(), project_id)
        )
        conn.commit()
        conn.close()

    return result


# ─── Separate Generation Endpoints ────────────────────────────────────────────

MAIN_SLOTS = ["main", "sub1", "sub2", "sub3", "sub4", "sub5", "sub6"]
APLUS_SLOTS = [
    "aplus_banner_desktop", "aplus_banner_mobile",
    "aplus_1_desktop", "aplus_1_mobile",
    "aplus_2_desktop", "aplus_2_mobile",
    "aplus_3_desktop", "aplus_3_mobile",
    "aplus_compare_desktop", "aplus_compare_mobile",
    "brand_story_desktop", "brand_story_mobile",
]


def _slot_details_from_body(body: dict, default_slots: list[str]) -> list[dict]:
    """Normalize dynamic frontend slot config while keeping legacy defaults."""
    raw_slots = body.get("slots")
    if isinstance(raw_slots, list) and raw_slots:
        details = []
        for item in raw_slots:
            if isinstance(item, str):
                sid = item.strip()
                if sid:
                    details.append({"id": sid, "label": sid, "size": ""})
            elif isinstance(item, dict):
                sid = str(item.get("id", "")).strip()
                if sid:
                    details.append({
                        "id": sid,
                        "label": str(item.get("label") or sid).strip(),
                        "size": str(item.get("size") or "").strip(),
                    })
        if details:
            return details

    sizes = body.get("sizes", {})
    if isinstance(sizes, dict) and "sizes" in sizes:
        sizes = sizes["sizes"]
    if not isinstance(sizes, dict):
        sizes = {}
    return [{"id": sid, "label": sid, "size": str(sizes.get(sid, "")).strip()} for sid in default_slots]


def _color_directive(color_scheme: str, analysis_data: dict) -> str:
    """Color instruction shared by all prompt builders.

    - explicit scheme  → lock to it across every slot
    - empty / 'auto'   → reuse a palette already locked on the project, else
                         tell the model to pick ONE palette and emit exact hex codes
    Keeping the palette identical across slots (and across single-slot
    regeneration) is what stops the color drift between generated images.
    """
    cs = (color_scheme or "").strip()
    saved = str((analysis_data or {}).get("palette") or (analysis_data or {}).get("visual_style") or "").strip()
    if cs and cs.lower() != "auto":
        return (f"- MANDATORY COLOR PALETTE: Use '{cs}' as the dominant palette for EVERY image — same "
                f"backgrounds, props, lighting and color grading. Express it as 4-6 explicit HEX codes in "
                f"visual_style and reuse those exact codes in every prompt.")
    if saved:
        return (f"- MANDATORY COLOR PALETTE (locked for consistency): {saved}\n  Reuse these EXACT colors in "
                f"every prompt. Do not introduce a new dominant color.")
    return ("- COLOR PALETTE: Choose ONE cohesive palette for the whole set, express it as 4-6 explicit HEX "
            "codes inside visual_style, and apply that SAME hex palette to EVERY image so the set looks unified.")


# Reproduce the real product instead of re-imagining it. The reference photos are
# sent to the image model as actual image inputs at generation time, so URLs in the
# text prompt are useless noise and are explicitly forbidden here.
_FIDELITY_RULE = (
    "- PRODUCT FIDELITY: The real product photos are supplied to the image model directly as image inputs. "
    "Reproduce the product EXACTLY as shown there — identical shape, proportions, colors, materials, logos and "
    "any text printed on the product. Do NOT redesign, recolor, relabel or restyle the product itself; only "
    "change the background, scene, props and lighting. Never put a URL inside the prompt."
)

# Make on-image text real and legible: pull verbatim callouts from the copy and tell
# the generator to render that exact string (gpt-image renders supplied text fairly
# reliably, but only when the exact words are given).
_TEXT_RULE = (
    "- ON-IMAGE TEXT: For feature / detail / specs / A+ slots, choose 2-5 word callouts taken VERBATIM from the "
    "APPROVED LISTING COPY section if present (otherwise from the selling points / bullets above), and embed them "
    "in the prompt as: render the exact text \"<words>\" in bold modern sans-serif, large, high-contrast, correctly "
    "spelled. Use the product's real attributes — do not invent claims. The white-background main image must have NO text."
)


def _persist_visual_anchor(project_id: str, row, result: dict) -> None:
    """Save product_lock / visual_style / palette onto the project's analysis_data
    so later single-slot regenerations reuse the same product description and colors."""
    if not isinstance(result, dict):
        return
    lock = str(result.get("product_lock") or "").strip()
    style = str(result.get("visual_style") or "").strip()
    if not lock and not style:
        return
    try:
        conn = _db()
        cur = conn.execute("SELECT analysis_data FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
        existing = json.loads(cur["analysis_data"]) if cur and cur["analysis_data"] else {}
        if lock:
            existing["product_lock"] = lock
        if style:
            existing["visual_style"] = style
            existing["palette"] = style
        conn.execute(
            "UPDATE listing_projects SET analysis_data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(existing, ensure_ascii=False), time.time(), project_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


async def _generate_prompts_for_slots(project_id: str, slots: list[str], body: dict):
    """Shared logic for generating prompts for a subset of slots."""
    conn = _db()
    row = conn.execute("SELECT * FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)

    scrape_data = json.loads(row["scrape_data"]) if row["scrape_data"] else {}
    analysis_data = json.loads(row["analysis_data"]) if row["analysis_data"] else {}
    product_context = _build_product_context(row, scrape_data, analysis_data)

    ref_images = scrape_data.get("reference_images", []) or scrape_data.get("imageUrls", [])
    ref_urls_text = "\n".join(ref_images[:3]) if ref_images else "No reference images."
    img_sp = analysis_data.get("image_insights", "")

    slot_details = _slot_details_from_body(body, slots)
    slot_ids = [s["id"] for s in slot_details]
    slot_text = "\n".join(
        f'- {s["id"]}: label="{s["label"]}", target_canvas="{s["size"] or "not specified"}"'
        for s in slot_details
    )

    color_scheme = body.get("color_scheme", "")
    color_directive = _color_directive(color_scheme, analysis_data)
    locked_product = str(analysis_data.get("product_lock") or "").strip()
    locked_hint = (f"\n## EXISTING PRODUCT LOCK (reuse this exact description, do not contradict it)\n{locked_product}"
                   if locked_product else "")
    approved = _approved_copy(row)
    approved_block = (f"\n\n## APPROVED LISTING COPY (use these EXACT words for any on-image text / callouts)\n{approved}"
                      if approved else "")

    slots_json = ", ".join(f'"{s}":"prompt..."' for s in slot_ids)

    prompt = f"""You are an Amazon listing image strategist. Generate prompts ONLY for these slots: {', '.join(slot_ids)}.

IMPORTANT: Do NOT use web search. Work ONLY with the product information provided below.

## PRODUCT INFO
{product_context}
{locked_hint}{approved_block}

## REFERENCE IMAGES (the real product — supplied to the image model as image inputs)
{ref_urls_text}

## VISUAL ANALYSIS OF ALL IMAGES (selling points / style / scenes from EVERY scraped + uploaded image)
{img_sp or "(not available)"}

## TARGET SLOTS, LABELS, AND CANVAS SIZES
{slot_text}

## VISUAL QUALITY RULES (apply to EVERY prompt):
- Use cinematic photography language: specify focal length, aperture, depth of field
- Specify lighting precisely
- Include color grading
- Add texture/material rendering
- For lifestyle scenes: "shot on Sony A7IV", "editorial photography"
- For main image: "commercial product photography", "phase-one medium format quality", "razor sharp focus"
- AVOID flat infographic style
{color_directive}
{_TEXT_RULE}
{_FIDELITY_RULE}

## CRITICAL RULES:
- Main image (if included): pure white background, product 85%, no text
- Every prompt MUST start with the product appearance description (identical wording across all slots)
- Each prompt MUST be composed for its target_canvas size. Mention the exact canvas size and layout orientation inside the prompt.
- For Amazon main images, use a high-resolution square canvas suitable for 1400x1400+ delivery when requested.
- For Premium A+ modules, respect desktop 1464x600 and mobile 600x450 layouts when requested.
- Do NOT invent specs not in product info
- Each scene should feel like a $10,000 commercial photoshoot

## OUTPUT FORMAT (valid JSON, no other text):
{{"product_lock":"strict appearance description","visual_style":"style + the 4-6 HEX color palette used everywhere","prompts":{{{slots_json}}}}}"""

    try:
        content = await _call_ai(prompt, max_tokens=8000, web_search=False)
    except HTTPException:
        return _fallback_prompts_for_slots(row, scrape_data, analysis_data, slot_details, color_scheme)
    result = _parse_json_response(content)

    if not result or not result.get("prompts"):
        raise HTTPException(502, f"提示词生成失败，AI没有返回可用JSON: {content[:500]}")

    # Persist the product lock + palette so single-slot regeneration and later
    # batches reuse the SAME product description and colors (consistency anchor).
    _persist_visual_anchor(project_id, row, result)

    prompts = result.get("prompts", {})
    if isinstance(prompts, dict):
        cleaned = {sid: str(prompts[sid]).strip() for sid in slot_ids if sid in prompts and str(prompts[sid]).strip()}
        if cleaned:
            reviewed = await _review_batch_prompts(cleaned, slot_details, color_scheme)
            result["prompts"] = reviewed
            return result

    raise HTTPException(502, f"提示词生成失败，AI没有返回当前图片位的提示词: {content[:500]}")


@router.post("/projects/{project_id}/generate-main-prompts")
async def generate_main_prompts(project_id: str, body: dict, _user: str = Depends(require_user)):
    """Generate prompts for 7 main images only (main + sub1-sub6)."""
    return await _generate_prompts_for_slots(project_id, MAIN_SLOTS, body)


@router.post("/projects/{project_id}/generate-aplus-prompts")
async def generate_aplus_prompts(project_id: str, body: dict, _user: str = Depends(require_user)):
    """Generate prompts for 6 A+ images only (aplus_banner + aplus_1-4 + brand_story)."""
    return await _generate_prompts_for_slots(project_id, APLUS_SLOTS, body)


# ─── Template CRUD ─────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/templates")
async def create_template(project_id: str, body: dict, _user: str = Depends(require_user)):
    """Upload a prompt text and have AI convert it to a reusable template."""
    conn = _db()
    row = conn.execute("SELECT id, templates FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404)

    name = body.get("name", "Untitled")
    content = body.get("content", "")
    if not content:
        conn.close()
        raise HTTPException(400, "content is required")

    # Use AI to convert specific prompt into a generic template
    ai_prompt = f"""Convert the following product image prompt into a REUSABLE TEMPLATE by replacing specific details with placeholders.

Replace:
- Specific product descriptions → {{product_lock}}
- Reference URLs → {{reference_url}}
- Visual style descriptions → {{visual_style}}
- Color scheme/palette mentions → {{color_scheme}}

Keep the structure, composition instructions, lighting, and camera settings intact.
Output ONLY the template text with placeholders, nothing else.

ORIGINAL PROMPT:
{content}"""

    fallback_used = False
    warning = None
    try:
        template_content = await _call_ai(ai_prompt, max_tokens=2000, web_search=False)
    except HTTPException as e:
        template_content = _fallback_template_content(content)
        fallback_used = True
        warning = f"Hermes/Codex 当前不可用，模板已按本地规则保存；恢复额度后可重新保存为 AI 泛化模板。原因：{str(e.detail)[:220]}"

    templates = json.loads(row["templates"]) if row["templates"] else []
    template_entry = {
        "id": str(uuid.uuid4())[:8],
        "name": name,
        "content": template_content.strip(),
        "original": content,
        "created_at": time.time(),
        "fallback": fallback_used,
        "warning": warning,
    }
    templates.append(template_entry)

    conn.execute(
        "UPDATE listing_projects SET templates = ?, updated_at = ? WHERE id = ?",
        (json.dumps(templates, ensure_ascii=False), time.time(), project_id)
    )
    conn.commit()
    conn.close()
    return template_entry


@router.get("/projects/{project_id}/templates")
def list_templates(project_id: str, _user: str = Depends(require_user)):
    """Get all templates for a project."""
    conn = _db()
    row = conn.execute("SELECT templates FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    return json.loads(row["templates"]) if row["templates"] else []


@router.post("/projects/{project_id}/apply-template")
async def apply_template(project_id: str, body: dict, _user: str = Depends(require_user)):
    """Apply a template intelligently to one slot or a full slot group."""
    conn = _db()
    row = conn.execute("SELECT * FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)

    template_id = body.get("template_id")
    slot = body.get("slot", "main")
    target_group = body.get("target_group", "main")
    color_scheme = body.get("color_scheme", "natural tones")
    if not template_id:
        raise HTTPException(400, "template_id is required")

    templates = json.loads(row["templates"]) if row["templates"] else []
    template = next((t for t in templates if t["id"] == template_id), None)
    if not template:
        raise HTTPException(404, "template not found")

    analysis_data = json.loads(row["analysis_data"]) if row["analysis_data"] else {}
    scrape_data = json.loads(row["scrape_data"]) if row["scrape_data"] else {}
    ref_images = scrape_data.get("reference_images", []) or scrape_data.get("imageUrls", [])
    product_context = _build_product_context(row, scrape_data, analysis_data)

    filled = template.get("content", "")
    filled = filled.replace("{product_lock}", analysis_data.get("product_lock", "product as shown in reference"))
    filled = filled.replace("{reference_url}", ref_images[0] if ref_images else "")
    filled = filled.replace("{visual_style}", analysis_data.get("visual_style", "professional product photography"))
    filled = filled.replace("{color_scheme}", color_scheme or "natural tones")

    default_main_defs = {
        "main": "Amazon main image: pure white background, product 85%, no text.",
        "sub1": "Lifestyle or use-case image.",
        "sub2": "Detail or key feature image.",
        "sub3": "Size, specification, or scale image.",
        "sub4": "Multi-angle, technology, or structure image.",
        "sub5": "Package, accessories, or what-is-in-the-box image.",
        "sub6": "Multi-scenario, benefits, or closing sales image.",
    }
    default_aplus_defs = {
        "aplus_banner": "Wide A+ hero banner.",
        "aplus_banner_desktop": "Premium A+ hero banner for desktop, wide 1464x600 layout.",
        "aplus_banner_mobile": "Premium A+ hero banner for mobile, compact 600x450 layout.",
        "aplus_1": "A+ module 1, primary feature.",
        "aplus_1_desktop": "A+ module 1 desktop version, primary feature in 1464x600 layout.",
        "aplus_1_mobile": "A+ module 1 mobile version, same feature adapted to 600x450 layout.",
        "aplus_2": "A+ module 2, secondary feature.",
        "aplus_2_desktop": "A+ module 2 desktop version, secondary feature in 1464x600 layout.",
        "aplus_2_mobile": "A+ module 2 mobile version, same feature adapted to 600x450 layout.",
        "aplus_3": "A+ module 3, tertiary feature.",
        "aplus_3_desktop": "A+ module 3 desktop version, tertiary feature in 1464x600 layout.",
        "aplus_3_mobile": "A+ module 3 mobile version, same feature adapted to 600x450 layout.",
        "aplus_4": "A+ comparison, trust, specs, or advantage module.",
        "aplus_compare_desktop": "A+ comparison, trust, specs, or advantage module for desktop 1464x600 layout.",
        "aplus_compare_mobile": "A+ comparison, trust, specs, or advantage module for mobile 600x450 layout.",
        "brand_story": "Brand story or final trust-building module.",
        "brand_story_desktop": "Brand story or final trust-building module for desktop 1464x600 layout.",
        "brand_story_mobile": "Brand story or final trust-building module for mobile 600x450 layout.",
    }
    default_defs = default_aplus_defs if target_group == "aplus" else default_main_defs
    default_slots = list(default_defs.keys())
    target_slot_details = _slot_details_from_body(body, default_slots)
    slot_defs = {
        s["id"]: f'{s["label"]}; target canvas {s["size"] or "not specified"}; {default_defs.get(s["id"], "")}'
        for s in target_slot_details
    }

    slot_lines = "\n".join(f"- {k}: {v}" for k, v in slot_defs.items())
    ref_text = "\n".join(ref_images[:3]) if ref_images else "No reference image URL available."

    ai_prompt = f"""You are an Amazon listing image prompt strategist.

Apply the user's reusable template to the CURRENT PRODUCT. Do NOT paste the template verbatim.

## CURRENT PRODUCT DATA
{product_context}

## PRODUCT LOCK
{analysis_data.get("product_lock", "Describe the product exactly as shown in reference images. Do not change appearance.")}

## VISUAL STYLE
{analysis_data.get("visual_style", "Professional Amazon product photography.")}

## REFERENCE IMAGES
{ref_text}

## TARGET SLOT GROUP
{target_group}

## AVAILABLE SLOTS
{slot_lines}

## REQUESTED SLOT
{slot}

## FILLED TEMPLATE TO ANALYZE
{filled}

## TASK
1. First decide whether the template describes a single image or a multi-image set / full A+ sequence.
2. If it is a multi-image set, split and adapt it across the relevant available slots. For a full A+ poster/template, create separate prompts for aplus_banner, aplus_1, aplus_2, aplus_3, aplus_4, and brand_story when possible.
3. If it is a single-image template, adapt it only to the requested slot.
4. Every output prompt must be for GPT Image generation, not instructions about the template itself.
5. Use real current product data, product_lock, visual_style, color scheme, and reference images.
6. Keep product appearance consistent. Do not invent unsupported specs.
7. For each prompt: 100-220 words, include the reference image URL when available, include clear composition/lighting/text instructions.
8. Respect each slot's target canvas and orientation. For Premium A+ desktop use 1464x600 when configured; for mobile use 600x450 when configured.
9. If the user's template is a full A+ desktop/mobile system, distribute it across all configured A+ desktop and mobile slots instead of putting everything into one prompt.
10. Do not output placeholder braces like {{product_lock}} or {{reference_url}}.

## OUTPUT FORMAT
Return valid JSON only:
{{"mode":"single_or_multi","prompts":{{"slot_id":"adapted prompt text"}}}}"""

    try:
        content = await _call_ai(ai_prompt, max_tokens=12000, web_search=False)
    except HTTPException:
        result = _fallback_prompts_for_slots(row, scrape_data, analysis_data, target_slot_details, color_scheme, filled)
        prompts = result["prompts"]
        return {
            "slot": slot,
            "prompt": prompts.get(slot, next(iter(prompts.values()))),
            "prompts": prompts,
            "mode": "fallback_multi" if len(prompts) > 1 else "fallback_single",
            "fallback": True,
            "warning": result["warning"],
        }
    result = _parse_json_response(content)
    prompts = result.get("prompts") if isinstance(result, dict) else None
    if isinstance(prompts, dict):
        cleaned = {k: str(v).strip() for k, v in prompts.items() if k in slot_defs and str(v).strip()}
        if cleaned:
            return {"slot": slot, "prompt": cleaned.get(slot, next(iter(cleaned.values()))), "prompts": cleaned, "mode": result.get("mode", "")}

    raise HTTPException(502, f"模板智能套用失败，AI没有返回可用的槽位提示词: {content[:500]}")


def _parse_json_response(content: str) -> Optional[dict]:
    """Robustly parse JSON from AI response, handling markdown fences and formatting issues."""
    if not content:
        return None

    # Strip markdown code fences
    cleaned = content.strip()
    if cleaned.startswith("```"):
        # Remove first line (```json or ```)
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1:]
        else:
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    # Try direct parse
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Try brace-depth matching to find the outermost JSON object
    depth = 0
    start_idx = None
    end_idx = None
    in_string = False
    escape_next = False
    for i, c in enumerate(content):
        if escape_next:
            escape_next = False
            continue
        if c == '\\' and in_string:
            escape_next = True
            continue
        if c == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            if depth == 0:
                start_idx = i
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0 and start_idx is not None:
                end_idx = i + 1
                break

    if start_idx is not None and end_idx is not None:
        try:
            return json.loads(content[start_idx:end_idx])
        except Exception:
            pass

    return None


# ─── Single Image Prompt ───────────────────────────────────────────────────────

@router.post("/projects/{project_id}/generate-image-prompt")
async def generate_image_prompt(project_id: str, body: dict, _user: str = Depends(require_user)):
    conn = _db()
    row = conn.execute("SELECT * FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)

    slot = body.get("slot", "main")
    slot_label = str(body.get("label") or slot).strip()
    slot_size = str(body.get("size") or "").strip()
    color_scheme = body.get("color_scheme", "")
    scrape_data = json.loads(row["scrape_data"]) if row["scrape_data"] else {}
    analysis_data = json.loads(row["analysis_data"]) if row["analysis_data"] else {}
    product_context = _build_product_context(row, scrape_data, analysis_data)

    # Get product_lock and image_tasks from previous pipeline analysis
    product_lock = analysis_data.get("product_lock", "")
    visual_style = analysis_data.get("visual_style", "")
    image_tasks = analysis_data.get("image_tasks", {})
    slot_task = image_tasks.get(slot, "")
    approved = _approved_copy(row)
    approved_block = (f"\n\n## APPROVED LISTING COPY (use these EXACT words for any on-image text / callouts)\n{approved}"
                      if approved else "")

    # Reference images
    ref_images = scrape_data.get("reference_images", []) or scrape_data.get("imageUrls", [])
    ref_urls_text = "\n".join(ref_images[:3]) if ref_images else ""

    slot_descriptions = {
        "main": "White background main image. Pure white (#FFFFFF), product centered 85%, no text, no icons. Include accessories if they show kit value. Soft studio shadow.",
        "sub1": "Outdoor/lifestyle scene showing the product in real use. Authentic environment, natural lighting.",
        "sub2": "Detail/feature image highlighting key selling point. Can include clean text callouts.",
        "sub3": "Specifications/size image. Clean infographic style with dimensions or scale reference.",
        "sub4": "Technology/multi-angle image. Show internal features or multiple views.",
        "sub5": "Package/accessories image. Show everything included in the kit.",
        "sub6": "Multi-scenario image. Show 3-4 different use cases around the product.",
        "aplus_banner": "Wide-format A+ banner. Cinematic, brand-level imagery.",
        "aplus_banner_desktop": "Wide-format Premium A+ desktop banner. Cinematic brand-level imagery composed for 1464x600.",
        "aplus_banner_mobile": "Mobile Premium A+ banner. Same hero idea adapted to a compact 600x450 layout.",
        "aplus_1": "A+ feature module highlighting primary selling point.",
        "aplus_1_desktop": "A+ desktop feature module highlighting primary selling point in a wide 1464x600 layout.",
        "aplus_1_mobile": "A+ mobile feature module highlighting primary selling point in a compact 600x450 layout.",
        "aplus_2": "A+ feature module highlighting secondary selling point.",
        "aplus_2_desktop": "A+ desktop feature module highlighting secondary selling point in a wide 1464x600 layout.",
        "aplus_2_mobile": "A+ mobile feature module highlighting secondary selling point in a compact 600x450 layout.",
        "aplus_3": "A+ feature module highlighting tertiary selling point.",
        "aplus_3_desktop": "A+ desktop feature module highlighting tertiary selling point in a wide 1464x600 layout.",
        "aplus_3_mobile": "A+ mobile feature module highlighting tertiary selling point in a compact 600x450 layout.",
        "aplus_4": "A+ comparison image showing product advantages.",
        "aplus_compare_desktop": "A+ desktop comparison, specs, trust, or advantage module in a wide 1464x600 layout.",
        "aplus_compare_mobile": "A+ mobile comparison, specs, trust, or advantage module in a compact 600x450 layout.",
        "brand_story": "Brand story image communicating brand values and mission.",
        "brand_story_desktop": "Desktop brand story or trust-building module in a wide 1464x600 layout.",
        "brand_story_mobile": "Mobile brand story or trust-building module in a compact 600x450 layout.",
    }
    slot_desc = slot_descriptions.get(slot, f"{slot_label}. Product showcase image.")

    prompt = f"""You are an Amazon product image prompt engineer. Write ONE image generation prompt for this slot.

## SLOT TYPE
{slot_desc}

## CURRENT SLOT CONFIG
- Slot id: {slot}
- Slot label: {slot_label}
- Target canvas: {slot_size or "not specified"}

## SALES TASK FOR THIS IMAGE
{slot_task if slot_task else "Attract buyer attention and communicate product value."}

## PRODUCT LOCK (you MUST start your prompt with this — do not change the product appearance)
{product_lock if product_lock else "Describe the product exactly as shown in the reference photos. Do not alter its design."}

## VISUAL STYLE (reuse exactly — this keeps every image in the set consistent)
{visual_style if visual_style else "Professional Amazon product photography style appropriate for this category."}
{_color_directive(color_scheme, analysis_data)}

## PRODUCT INFORMATION
{product_context}{approved_block}

## REFERENCE IMAGES (the real product — sent to the image model as image inputs, NOT as text)
{ref_urls_text if ref_urls_text else "No reference images available."}

{_FIDELITY_RULE}
{_TEXT_RULE}

## PROMPT STRUCTURE (follow this exactly):
1. Product appearance lock — exact physical description from product lock above (first sentence)
2. Image goal — what this image must communicate (from sales task)
3. Scene/background — specific environment or background
4. Composition — product position, size in frame, camera angle
5. Canvas/layout — compose for target canvas {slot_size or "not specified"} and mention that exact size in the prompt
6. On-image text — the EXACT words to render (taken verbatim from the selling points), OR "no text" for the white main image
7. Style/lighting — the locked color palette, lighting setup, mood
8. Negative constraints — what NOT to include

## RULES
- Output ONLY the prompt text, no explanations, no prefixes
- 100-200 words
- First sentence MUST be the product lock (exact appearance description)
- Never put a URL in the prompt — the reference photos are already given to the model
- MUST respect the current slot label and target canvas
- For feature/infographic images: include the EXACT short text to render (verbatim from selling points)
- Do NOT invent specs not mentioned in product info
- Do NOT use generic language like "high quality" or "professional" alone"""

    try:
        content = await _call_ai(prompt, max_tokens=16000)
    except HTTPException as e:
        content = _fallback_image_prompt(slot, slot_label, slot_size, row, scrape_data, analysis_data, color_scheme)
        return {
            "slot": slot,
            "prompt": content.strip(),
            "fallback": True,
            "warning": f"Hermes/Codex 当前不可用，已用本地规则生成可编辑图片提示词。原因：{str(e.detail)[:220]}",
        }
    return {"slot": slot, "prompt": content.strip()}


@router.post("/projects/{project_id}/review-image-prompt")
async def review_image_prompt(project_id: str, body: dict, _user: str = Depends(require_user)):
    """Accept a user-submitted prompt draft, self-review and return an improved version."""
    conn = _db()
    row = conn.execute("SELECT id FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)

    slot = body.get("slot", "main")
    draft = body.get("prompt", "").strip()
    label = str(body.get("label") or slot).strip()
    size = str(body.get("size") or "").strip()
    color_scheme = body.get("color_scheme", "")

    if not draft:
        raise HTTPException(400, "prompt is required")

    reviewed = await _review_single_prompt(draft, label, size, color_scheme)
    return {"slot": slot, "prompt": reviewed}


# ─── Image Generation ─────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/generate-image")
async def generate_single_image(project_id: str, body: ImageGenReq, _user: str = Depends(require_user)):
    conn = _db()
    row = conn.execute("SELECT id, scrape_data, analysis_data FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)

    # Build reference image list: uploaded files (base64) take priority over scraped URLs
    ref_urls = body.reference_urls
    if not ref_urls:
        import base64 as _b64
        import mimetypes as _mt
        scrape_data = json.loads(row["scrape_data"]) if row["scrape_data"] else {}
        scraped_urls = (scrape_data.get("reference_images") or scrape_data.get("imageUrls") or [])[:2]
        uploaded_paths = scrape_data.get("uploaded_images", [])
        b64_refs: list[str] = []
        for p in uploaded_paths[:4]:
            p_obj = Path(p)
            if p_obj.exists():
                try:
                    raw = p_obj.read_bytes()
                    mime = _mt.guess_type(str(p_obj))[0] or "image/jpeg"
                    b64_refs.append(f"data:{mime};base64,{_b64.b64encode(raw).decode()}")
                except Exception:
                    pass
        # Uploaded images first (user's own product photos), then scraped.
        # Cap at 2: passing many conflicting angles to gpt-image makes it blend
        # them into a deformed hybrid. Fewer, cleaner references = higher fidelity.
        ref_urls = (b64_refs + list(scraped_urls))[:2]

    if not _apimart_key():
        raise HTTPException(
            400,
            "Apimart 密钥未配置 — 请在「系统配置 → AI 服务」填入有 gpt-image-2 权限的密钥。",
        )

    # Deterministic product-fidelity preamble: the per-slot prompt is LLM-written and
    # may drift, so we always prepend a hard "reproduce the real product exactly"
    # instruction whenever reference photos are attached. This is the main lever for
    # keeping the generated product consistent with the user's real product.
    full_prompt = body.prompt
    if ref_urls:
        full_prompt = (
            "CRITICAL — PRODUCT FIDELITY: The attached reference image(s) show the EXACT real product. "
            "Reproduce that product identically — same shape, proportions, colors, materials, logos, buttons, "
            "labels and any printed text. Do NOT redesign, recolor, relabel, restyle, or add/remove parts. "
            "Only build the background, scene and lighting around the unchanged product.\n\n"
        ) + body.prompt

    base_body = {"model": "gpt-image-2", "prompt": full_prompt, "n": 1, "size": body.size}
    if ref_urls:
        base_body["image_urls"] = ref_urls[:2]

    # gpt-image's `input_fidelity:"high"` preserves details of the input image (the
    # real product). Apimart may or may not pass it through, so try high-fidelity
    # first and gracefully fall back to a plain request if it is rejected.
    attempts = [{**base_body, "input_fidelity": "high"}, base_body] if ref_urls else [base_body]

    try:
        async with httpx.AsyncClient(timeout=180) as client:
            import logging
            logging.info(f"[generate-image] slot={body.slot} ref_urls={len(ref_urls)} fidelity={'high' if ref_urls else 'n/a'}")

            resp = None
            for idx, attempt in enumerate(attempts):
                resp = await client.post(
                    f"{_apimart_base()}/images/generations",
                    headers={"Authorization": f"Bearer {_apimart_key()}", "Content-Type": "application/json"},
                    json=attempt,
                )
                if resp.status_code == 200:
                    if idx > 0:
                        logging.info(f"[generate-image] input_fidelity=high rejected, fell back to plain (slot={body.slot})")
                    elif "input_fidelity" in attempt:
                        logging.info(f"[generate-image] input_fidelity=high accepted (slot={body.slot})")
                    break  # accepted (with or without high fidelity)
                logging.info(f"[generate-image] attempt {idx} -> HTTP {resp.status_code}: {resp.text[:160]}")
            if resp is None or resp.status_code != 200:
                raise HTTPException(502, f"图片生成提交失败: {resp.text[:300] if resp is not None else 'no response'}")

            submit_data = resp.json()
            task_id = submit_data.get("data", [{}])[0].get("task_id")
            if not task_id:
                raise HTTPException(502, f"未返回task_id: {resp.text[:300]}")

            url = await _poll_task(client, task_id)
            # Auto-ingest into the 图片工作区 (一键图片翻译 sub-board) so generated
            # images are reusable for multi-site translation. Best-effort: never let
            # a workspace hiccup fail image generation.
            try:
                from app.routers.image_translate import ingest_url
                await ingest_url(url, source="listing", project_id=project_id)
            except Exception:
                pass
            return {"slot": body.slot, "url": url}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"图片生成失败: {str(e)}")


async def _poll_task(client: httpx.AsyncClient, task_id: str, max_polls: int = 54, interval: float = 5.0) -> str:
    """Poll APIMart task until completion.

    Budget = max_polls * interval = 270s, kept under the nginx proxy_read_timeout
    (300s) so our own 504 (with the task's last status) returns instead of nginx's
    generic gateway timeout. gpt-image-2 with reference images often needs >120s.
    """
    last_status = "unknown"
    for _ in range(max_polls):
        await asyncio.sleep(interval)
        poll = await client.get(
            f"{_apimart_base()}/tasks/{task_id}",
            headers={"Authorization": f"Bearer {_apimart_key()}"},
        )
        if poll.status_code != 200:
            continue
        task_data = poll.json().get("data", {})
        last_status = task_data.get("status") or last_status
        if last_status == "completed":
            images = task_data.get("result", {}).get("images", [])
            if images and images[0].get("url"):
                url = images[0]["url"]
                if isinstance(url, list):
                    url = url[0]
                return url
            raise HTTPException(502, "任务完成但未返回图片URL")
        elif last_status == "failed":
            raise HTTPException(502, f"图片生成失败: {task_data}")
    raise HTTPException(
        504,
        f"图片生成超时({int(max_polls * interval)}s)，任务最后状态: {last_status}。"
        f"多为模型仍在处理（尤其带参考图/大尺寸），可重试或稍后再生成。",
    )


# ─── PSD Download ─────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/download-psd")
async def download_psd(project_id: str, body: dict, _user: str = Depends(require_user)):
    """Download an image as PSD format."""
    image_url = body.get("url")
    slot = body.get("slot", "image")
    if not image_url:
        raise HTTPException(400, "url is required")

    # Download the image
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(image_url)
            if resp.status_code != 200:
                raise HTTPException(502, "Failed to download image")
            image_bytes = resp.content
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Download failed: {e}")

    # Convert to PSD
    from PIL import Image
    from psd_tools import PSDImage

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    psd = PSDImage.frompil(img)
    buf = io.BytesIO()
    psd.save(buf)
    buf.seek(0)

    filename = f"{project_id}_{slot}.psd"
    return StreamingResponse(
        buf,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ─── Serve uploaded images ─────────────────────────────────────────────────────

@router.get("/images/{project_id}/{filename}")
def serve_image(project_id: str, filename: str):
    """Serve uploaded listing images."""
    fpath = IMAGES_DIR / project_id / filename
    if not fpath.exists():
        raise HTTPException(404)
    import mimetypes
    mime = mimetypes.guess_type(str(fpath))[0] or "image/png"
    return StreamingResponse(open(fpath, "rb"), media_type=mime)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _clean_text(value) -> str:
    return " ".join(str(value or "").replace("\n", " ").split())


def _scrape_field(scrape_data: dict, key: str, default=""):
    product = scrape_data.get("product") if isinstance(scrape_data.get("product"), dict) else {}
    return scrape_data.get(key) or product.get(key) or default


def _copy_source(row, scrape_data: dict, analysis_data: dict) -> dict:
    title = _clean_text(_scrape_field(scrape_data, "title") or row["asin"])
    raw_bullets = _scrape_field(scrape_data, "bullets", [])
    if isinstance(raw_bullets, str):
        bullets = [raw_bullets]
    elif isinstance(raw_bullets, list):
        bullets = raw_bullets
    else:
        bullets = []
    manual = scrape_data.get("manual", {}) if isinstance(scrape_data.get("manual"), dict) else {}
    manual_points = [x.strip() for x in str(manual.get("selling_points") or "").splitlines() if x.strip()]
    bullets = [_clean_text(x) for x in (manual_points or bullets) if _clean_text(x)]
    description = _clean_text(manual.get("description") or _scrape_field(scrape_data, "description") or "")
    audience = _clean_text(manual.get("target_audience") or "Amazon shoppers looking for reliable, easy-to-use product performance")
    structured = analysis_data.get("structured") if isinstance(analysis_data.get("structured"), dict) else {}
    keywords = structured.get("keywords") if isinstance(structured.get("keywords"), list) else []
    usp = structured.get("usp") if isinstance(structured.get("usp"), list) else []
    return {
        "asin": row["asin"],
        "marketplace": row["marketplace"],
        "title": title,
        "bullets": bullets[:8],
        "description": description,
        "audience": audience,
        "keywords": [_clean_text(k).lower() for k in keywords if _clean_text(k)],
        "usp": [_clean_text(u) for u in usp if _clean_text(u)],
    }


def _keywords_from_text(text: str, limit: int = 36) -> list[str]:
    stop = {
        "with", "from", "that", "this", "your", "their", "and", "for", "the", "our",
        "are", "you", "not", "can", "has", "have", "will", "all", "new", "use",
        "amazon", "about", "choose", "quality", "product", "products",
    }
    words = []
    for raw in text.lower().replace("/", " ").replace("-", " ").replace(",", " ").split():
        word = "".join(ch for ch in raw if ch.isalnum())
        if len(word) < 3 or word in stop or word.isdigit():
            continue
        if word not in words:
            words.append(word)
        if len(words) >= limit:
            break
    return words


def _fallback_copy(copy_type: str, row, scrape_data: dict, analysis_data: dict) -> str:
    """Deterministic copy fallback for AI gateway 429. Output is intentionally editable."""
    src = _copy_source(row, scrape_data, analysis_data)
    title = src["title"]
    bullets = src["bullets"] or ([src["description"]] if src["description"] else [])
    feature_pool = src["usp"] + bullets
    if not feature_pool:
        feature_pool = [
            f"Designed for dependable everyday performance for ASIN {src['asin']}",
            "Built to help shoppers solve the core need shown in the product listing",
            "Easy to use, practical, and suitable for the intended Amazon marketplace",
        ]

    if copy_type == "title":
        base = title[:180].strip()
        return "\n".join([
            f"1. {base}",
            f"2. {base} for {src['audience'][:55]}".strip()[:200],
            f"3. {base} with Practical Features and Everyday Value".strip()[:200],
        ])

    if copy_type == "bullets":
        heads = ["CORE BENEFIT", "RELIABLE DESIGN", "EASY TO USE", "PRACTICAL VALUE", "BUYER READY"]
        lines = []
        for i, head in enumerate(heads):
            detail = feature_pool[i % len(feature_pool)]
            lines.append(f"{head}: {detail[:220]}")
        return "\n".join(lines)

    if copy_type == "search_terms":
        text = " ".join([title, src["description"], " ".join(bullets), " ".join(src["keywords"])])
        terms = src["keywords"] + _keywords_from_text(text)
        deduped = []
        for term in terms:
            if term and term not in deduped:
                deduped.append(term)
        out = " ".join(deduped)
        return out[:250].strip()

    if copy_type == "aplus":
        f1 = feature_pool[0]
        f2 = feature_pool[1 % len(feature_pool)]
        f3 = feature_pool[2 % len(feature_pool)]
        return f"""Brand Story
Built around practical performance and buyer confidence, this product is designed to support {src['audience']}.

Hero Banner
{title[:120]}

Feature Module 1
{f1[:260]}

Feature Module 2
{f2[:260]}

Feature Module 3
{f3[:260]}

Comparison / Advantage
Clear value, useful features, and a straightforward experience for shoppers comparing similar options.

Usage Scenarios
1. Everyday use for the primary product need.
2. Giftable or household-ready use where reliability matters.
3. Outdoor, work, travel, or category-relevant use depending on the product context."""

    raise HTTPException(400, f"type must be one of: title, bullets, search_terms, aplus")


def _build_product_context(row, scrape_data: dict, analysis_data: dict) -> str:
    """Build text context from scrape + analysis data for AI prompts."""
    parts = [f"ASIN: {row['asin']}", f"Marketplace: {row['marketplace']}"]

    if scrape_data:
        if scrape_data.get("title"):
            parts.append(f"Current Title: {scrape_data['title']}")
        if scrape_data.get("bullets"):
            bullets = scrape_data["bullets"]
            if isinstance(bullets, list):
                parts.append("Current Bullets:\n" + "\n".join(f"- {b}" for b in bullets))
        if scrape_data.get("description"):
            parts.append(f"Description: {scrape_data['description'][:500]}")
        manual = scrape_data.get("manual", {})
        if manual.get("product_name"):
            parts.append(f"Product Name: {manual['product_name']}")
        if manual.get("description"):
            parts.append(f"Product Description: {manual['description']}")
        if manual.get("selling_points"):
            parts.append(f"Selling Points: {manual['selling_points']}")
        if manual.get("target_audience"):
            parts.append(f"Target Audience: {manual['target_audience']}")

    if analysis_data:
        # Support both old format and new structured format
        if analysis_data.get("structured"):
            s = analysis_data["structured"]
            if s.get("usp"):
                parts.append(f"USP: {', '.join(s['usp'])}")
            if s.get("keywords"):
                parts.append(f"Keywords: {', '.join(s['keywords'][:15])}")
            if s.get("target_audience"):
                parts.append(f"Target Audience: {s['target_audience']}")
            if s.get("scenarios"):
                parts.append(f"Use Scenarios: {', '.join(s['scenarios'])}")
        elif analysis_data.get("analysis"):
            parts.append(f"AI Analysis: {str(analysis_data['analysis'])[:800]}")
        # imgflow data
        if analysis_data.get("imgflow"):
            imgf = analysis_data["imgflow"]
            if imgf.get("sifKeywords"):
                parts.append(f"SIF Keywords: {', '.join(imgf['sifKeywords'][:15])}")
            if imgf.get("uspExtraction"):
                parts.append(f"USP (imgflow): {imgf['uspExtraction'][:300]}")
            if imgf.get("sorftimeData"):
                parts.append(f"Sorftime Trends: {str(imgf['sorftimeData'])[:200]}")

    return "\n".join(parts)


def _approved_copy(row) -> str:
    """The user's confirmed listing copy, used as the verbatim source for on-image
    text/callouts so the images match the real listing. Prefers the advanced
    copy-job result, falls back to the simple per-field copy. Returns '' if none."""
    cols = set(row.keys()) if hasattr(row, "keys") else set()
    lines: list[str] = []

    cr = None
    if "copy_result" in cols and row["copy_result"]:
        try:
            cr = json.loads(row["copy_result"])
        except Exception:
            cr = None

    if isinstance(cr, dict):
        titles = cr.get("titles") or ([] if not cr.get("title") else [cr["title"]])
        if titles:
            lines.append(f"Title: {str(titles[0]).strip()}")
        bullets = (cr.get("bullets_a") or []) + (cr.get("bullets_b") or [])
        for b in bullets[:6]:
            if str(b).strip():
                lines.append(f"- {str(b).strip()}")
    else:
        if "title" in cols and row["title"]:
            first = str(row["title"]).splitlines()[0].strip()
            if first:
                lines.append(f"Title: {first}")
        if "bullets" in cols and row["bullets"]:
            for b in str(row["bullets"]).splitlines()[:6]:
                if b.strip():
                    lines.append(f"- {b.strip()}")

    return "\n".join(lines).strip()


# ═══════════════════════════════════════════════════════════════════════════════
# NEW-PRODUCT LISTING COPY GENERATOR
# Job-based workflow: images → vision analysis → competitor data → LLM copy
# ═══════════════════════════════════════════════════════════════════════════════

COPY_JOB_DB = settings.data_dir / "listing_copy_jobs.sqlite3"
COPY_IMAGES_DIR = settings.data_dir / "listing_copy_images"
COPY_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

_LISTING_RULES_FILE = Path(__file__).resolve().parents[2] / "app" / "services" / "amazon_listing_rules.md"

_AMAZON_LISTING_RULES = """# Amazon Listing Generation Rules

## Output Goal
The final deliverable must include:
- 1 generation plan/rationale
- 5 compliant Amazon titles
- 1 Product Highlights string (NEW Amazon field, effective 2026-07-27)
- 2 bullet point sets, each with exactly 5 bullet points
- 2 backend search term strings
- A compliance checklist

## Title Rules (Amazon policy effective 2026-07-27)
- Generate exactly 5 title options.
- Target the marketplace language. For US, write titles in English.
- Length: Every title MUST NOT exceed 75 characters INCLUDING spaces. This is the single hard limit for ALL categories. Count spaces.
- Mobile Optimization: Front-load the product type and 1-2 primary keywords within the first ~60 characters.
- Do NOT keyword-stuff or list specs in the title. Move secondary keywords/attributes to Product Highlights and bullets instead.
- Put "what the product IS" in the title; "what advantages it has" go in Product Highlights.
- Use title case. Do not use ALL CAPS.
- Forbidden Words: "Gift", "Free", "Bonus", "Warranty", "Hot Item", "Best Seller", "No.1", price/delivery promises.
- Do not include unsupported claims, medical claims, or subjective claims such as "best" or "top-rated".

## Product Highlights Rules (NEW field, effective 2026-07-27)
- Generate exactly 1 highlights string.
- Length: MUST NOT exceed 125 characters INCLUDING spaces.
- Use short, benefit/feature-driven PHRASES, NOT full sentences. Separate phrases with ", " (comma + space).
- Cover the most decisive of: material, core function, usage scenario, compatibility/fit, key spec.
  Example: "Non-stick, Food Grade, Heat Resistant 220°C, Fits Ninja Crispi, 100 PCS".
- This field IS searchable: naturally embed core keywords that are NOT already in the title (avoid duplication).
- It only displays on the storefront when the title is under 75 characters, so make it information-dense.

## Bullet Point Rules
- Generate exactly 2 bullet point sets.
- Set A (Conversion Focus): Focus on emotional benefits, usage scenarios, and persuasion. Use [Bold Header] for each bullet.
- Set B (Rufus/QA Focus): Focus on factual specifications, technical details, and answering shopper questions.
- Each set must contain exactly 5 bullets.
- Cover material/structure, core function, usage scenario, gift/audience fit, and risk-reducing details.
- Explicit Attributes: State key attributes in "[Attribute]: [Detail]" format.
- Avoid prohibited terms: medical claims, guaranteed outcomes, competitor attacks, prices, promotions, URLs.

## Search Term Rules
- Generate exactly 2 backend search term strings.
- Length Limit: Each string MUST be under 249 bytes. Use lowercase, space-separated terms. No commas.
- Do not repeat keywords already in the title. Prefer synonyms, spelling variants, use cases, long-tail terms.

## Output Format (JSON)
Return a JSON object with these fields:
{
  "rationale": "Strategy explanation",
  "titles": ["Title 1 (<=75 chars incl spaces)", "Title 2", "Title 3", "Title 4", "Title 5"],
  "highlights": "phrase1, phrase2, phrase3, ... (single string, <=125 chars incl spaces)",
  "bullets_a": ["Bullet 1", "Bullet 2", "Bullet 3", "Bullet 4", "Bullet 5"],
  "bullets_b": ["Bullet 1", "Bullet 2", "Bullet 3", "Bullet 4", "Bullet 5"],
  "search_terms": ["string 1 under 249 chars", "string 2 under 249 chars"],
  "compliance_notes": ["any compliance issues or notes"]
}"""


def _copy_job_db():
    conn = sqlite3.connect(str(COPY_JOB_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS copy_jobs (
        id TEXT PRIMARY KEY,
        project_id TEXT,
        status TEXT DEFAULT 'pending',
        stage INTEGER DEFAULT 0,
        stage_msg TEXT DEFAULT '',
        marketplace TEXT DEFAULT 'US',
        product_type TEXT DEFAULT '',
        asins TEXT DEFAULT '[]',
        product_notes TEXT DEFAULT '',
        image_paths TEXT DEFAULT '[]',
        vision_result TEXT,
        competitor_result TEXT,
        result TEXT,
        error TEXT,
        created_at REAL,
        updated_at REAL
    )""")
    conn.commit()
    return conn


_copy_job_db().close()

# Migration: link copy jobs to a listing project so the result can be restored
try:
    _cjc = _copy_job_db()
    _cjc.execute("ALTER TABLE copy_jobs ADD COLUMN project_id TEXT")
    _cjc.commit()
    _cjc.close()
except Exception:
    pass


class CopyJobReq(BaseModel):
    marketplace: str = "US"
    product_type: str
    asins: list[str] = []
    product_notes: str = ""
    project_id: Optional[str] = None


async def _analyze_images_vision(image_paths: list[str], product_type: str) -> dict:
    """Call vision API to analyze product images. Falls back gracefully."""
    from app.services.ai_synthesis_service import _apimart_key, _apimart_base
    import base64, httpx as hx

    if not image_paths:
        return {"mode": "skipped", "features": [], "reason": "No images provided"}

    key = _apimart_key()
    if not key:
        return {"mode": "skipped", "features": [], "reason": "No vision API configured"}

    # Build vision messages
    content: list[dict] = [
        {"type": "text", "text": (
            f"You are analyzing product images for an Amazon listing. "
            f"Product type: {product_type}. "
            f"Extract: materials, key features, dimensions/size cues, accessories included, "
            f"color options, usage scenarios visible in images. "
            f"Be specific and factual. Do not invent features not visible. "
            f"Return JSON: {{\"features\": [\"feature1\", ...], \"materials\": \"...\", "
            f"\"size_hints\": \"...\", \"accessories\": \"...\", \"scenarios\": [\"...\"]}}"
        )}
    ]
    for ip in image_paths[:6]:  # max 6 images for cost
        try:
            with open(ip, "rb") as f:
                data = base64.b64encode(f.read()).decode()
            ext = Path(ip).suffix.lower().lstrip(".")
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                    "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
        except Exception:
            pass

    if len(content) == 1:
        return {"mode": "skipped", "features": [], "reason": "Could not read image files"}

    try:
        async with hx.AsyncClient(timeout=hx.Timeout(60, connect=10)) as client:
            resp = await asyncio.wait_for(
                client.post(
                    f"{_apimart_base()}/messages",
                    json={
                        "model": "claude-sonnet-4-6",
                        "max_tokens": 1000,
                        "messages": [{"role": "user", "content": content}],
                    },
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                        "anthropic-version": "2023-06-01",
                    },
                ),
                timeout=65,
            )
        if resp.status_code == 200:
            body = resp.json()
            text = ""
            for block in body.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    text += block.get("text", "")
            # Try to parse JSON
            import re as _re
            m = _re.search(r"\{[\s\S]*\}", text)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                    parsed["mode"] = "vision"
                    return parsed
                except Exception:
                    pass
            return {"mode": "vision", "features": [text[:500]], "raw": text}
    except Exception as exc:
        return {"mode": "error", "features": [], "reason": str(exc)}

    return {"mode": "skipped", "features": [], "reason": "Vision call failed"}


async def _fetch_competitor_data(asins: list[str], marketplace: str) -> dict:
    """Fetch competitor product/keyword data via sorftime. Fails gracefully."""
    from app.services.sorftime_service import _make_client, _safe_call
    results = {}
    errors = []
    try:
        async with _make_client() as client:
            tasks = []
            for i, asin in enumerate(asins[:5]):
                tasks.append(_safe_call(client, "product_report", {"asin": asin, "amzSite": marketplace}, i + 1))
                tasks.append(_safe_call(client, "competitor_product_keywords",
                                        {"asin": asin, "keywordSupportSite": marketplace}, i + 10))
            gathered = await asyncio.gather(*tasks)
            for name, val, err in gathered:
                if err:
                    errors.append(err)
                elif val:
                    asin_key = name
                    if asin_key not in results:
                        results[asin_key] = []
                    results[asin_key].append(val)
    except Exception as exc:
        errors.append(str(exc))
    return {"data": results, "errors": errors, "available": bool(results)}


def _build_listing_prompt(
    marketplace: str,
    product_type: str,
    product_notes: str,
    vision_result: dict,
    competitor_result: dict,
) -> str:
    parts = [
        f"You are an Amazon listing copywriting expert.",
        f"Marketplace: {marketplace}",
        f"Product Type: {product_type}",
        "",
    ]

    if product_notes:
        parts.append(f"Seller Notes (product specs/features):\n{product_notes}")
        parts.append("")

    if vision_result.get("mode") == "vision":
        features = vision_result.get("features", [])
        if features:
            parts.append(f"Image Analysis - Observed Features:\n" + "\n".join(f"- {f}" for f in features[:20]))
        materials = vision_result.get("materials", "")
        if materials:
            parts.append(f"Materials: {materials}")
        scenarios = vision_result.get("scenarios", [])
        if scenarios:
            parts.append(f"Visible Use Scenarios: {', '.join(scenarios[:5])}")
        parts.append("")

    if competitor_result.get("available"):
        comp_data = competitor_result.get("data", {})
        parts.append("Competitor Data (from market research):")
        parts.append(json.dumps(comp_data, ensure_ascii=False)[:3000])
        parts.append("")

    parts.append(_AMAZON_LISTING_RULES)
    parts.append("")
    parts.append("Now generate the listing copy. Return ONLY valid JSON, no other text.")

    return "\n".join(parts)


async def _run_copy_job(job_id: str) -> None:
    """Background task: vision → competitor → LLM → save result."""
    import httpx as hx

    def update(stage: int, msg: str, **kwargs):
        conn = _copy_job_db()
        conn.execute(
            "UPDATE copy_jobs SET stage=?, stage_msg=?, updated_at=?, status=? WHERE id=?",
            (stage, msg, time.time(), kwargs.get("status", "running"), job_id),
        )
        conn.commit()
        conn.close()

    try:
        conn = _copy_job_db()
        row = dict(conn.execute("SELECT * FROM copy_jobs WHERE id=?", (job_id,)).fetchone())
        conn.close()

        image_paths = json.loads(row.get("image_paths", "[]"))
        asins = json.loads(row.get("asins", "[]"))
        marketplace = row.get("marketplace", "US")
        product_type = row.get("product_type", "")
        product_notes = row.get("product_notes", "")

        # Stage 0: Vision
        update(0, "正在分析产品图片…" if image_paths else "未上传图片，跳过图片识别")
        vision_result = await _analyze_images_vision(image_paths, product_type)

        conn = _copy_job_db()
        conn.execute("UPDATE copy_jobs SET vision_result=? WHERE id=?",
                     (json.dumps(vision_result, ensure_ascii=False), job_id))
        conn.commit()
        conn.close()

        # Stage 1: Competitor data
        update(1, "正在查询竞品数据…" if asins else "未填写竞品ASIN，跳过竞品查询")
        competitor_result = await _fetch_competitor_data(asins, marketplace) if asins else {
            "data": {}, "errors": [], "available": False
        }

        conn = _copy_job_db()
        conn.execute("UPDATE copy_jobs SET competitor_result=? WHERE id=?",
                     (json.dumps(competitor_result, ensure_ascii=False), job_id))
        conn.commit()
        conn.close()

        # Stage 2: LLM generation
        update(2, "正在生成文案…")
        prompt = _build_listing_prompt(marketplace, product_type, product_notes, vision_result, competitor_result)

        # Try DeepSeek first, then apimart
        from app.services.ai_synthesis_service import (
            _deepseek_key, _apimart_key, _apimart_base, _stream_openai_compat
        )
        result_text = ""

        dk = _deepseek_key()
        if dk:
            try:
                async for chunk in _stream_openai_compat(dk, "https://api.deepseek.com", "deepseek-chat", prompt):
                    result_text += chunk
            except Exception:
                result_text = ""

        if not result_text:
            ak = _apimart_key()
            if ak:
                try:
                    async with hx.AsyncClient(timeout=hx.Timeout(120, connect=10)) as client:
                        resp = await client.post(
                            f"{_apimart_base()}/messages",
                            json={"model": "claude-sonnet-4-6", "max_tokens": 4096,
                                  "messages": [{"role": "user", "content": prompt}]},
                            headers={"Authorization": f"Bearer {ak}", "Content-Type": "application/json",
                                     "anthropic-version": "2023-06-01"},
                        )
                        resp.raise_for_status()
                        body = resp.json()
                        for block in body.get("content", []):
                            if isinstance(block, dict) and block.get("type") == "text":
                                result_text += block.get("text", "")
                except Exception:
                    pass

        if not result_text:
            raise RuntimeError("所有AI提供商均不可用，请在系统配置中设置 deepseek_api_key 或 apimart_key")

        # Parse JSON from result
        import re as _re
        parsed_result = None
        m = _re.search(r"\{[\s\S]*\}", result_text)
        if m:
            try:
                parsed_result = json.loads(m.group(0))
            except Exception:
                pass
        if not parsed_result:
            parsed_result = {"raw": result_text}

        result_json = json.dumps(parsed_result, ensure_ascii=False)
        conn = _copy_job_db()
        conn.execute(
            "UPDATE copy_jobs SET status='done', stage=3, stage_msg='文案生成完成', result=?, updated_at=? WHERE id=?",
            (result_json, time.time(), job_id),
        )
        conn.commit()
        conn.close()

        # Persist the result onto the linked project so it survives page refresh.
        project_id = row.get("project_id")
        if project_id:
            try:
                pconn = _db()
                pconn.execute(
                    "UPDATE listing_projects SET copy_result=?, copy_job_id=?, updated_at=? WHERE id=?",
                    (result_json, job_id, time.time(), project_id),
                )
                pconn.commit()
                pconn.close()
            except Exception:
                pass

    except Exception as exc:
        conn = _copy_job_db()
        conn.execute(
            "UPDATE copy_jobs SET status='failed', stage_msg=?, error=?, updated_at=? WHERE id=?",
            (str(exc), str(exc), time.time(), job_id),
        )
        conn.commit()
        conn.close()


# ─── Copy Job API ────────────────────────────────────────────────────────────

@router.get("/copy-jobs")
def list_copy_jobs(_user: str = Depends(require_user)):
    conn = _copy_job_db()
    rows = conn.execute(
        "SELECT id, status, stage, stage_msg, marketplace, product_type, error, created_at, updated_at "
        "FROM copy_jobs ORDER BY created_at DESC LIMIT 30"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/copy-jobs")
async def create_copy_job(body: CopyJobReq, _user: str = Depends(require_user)):
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    asins = [a.strip().upper() for a in body.asins if a.strip()][:10]
    conn = _copy_job_db()
    conn.execute(
        "INSERT INTO copy_jobs (id, project_id, status, stage, stage_msg, marketplace, product_type, "
        "asins, product_notes, image_paths, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (job_id, body.project_id, "pending", 0, "等待开始", body.marketplace,
         body.product_type.strip(), json.dumps(asins),
         body.product_notes.strip(), "[]", now, now),
    )
    conn.commit()
    conn.close()
    return {"job_id": job_id, "status": "pending"}


@router.post("/copy-jobs/{job_id}/images")
async def upload_copy_job_images(
    job_id: str,
    files: list[UploadFile] = File(...),
    _user: str = Depends(require_user),
):
    conn = _copy_job_db()
    row = conn.execute("SELECT * FROM copy_jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "job not found")
    if row["status"] not in ("pending", "uploaded"):
        raise HTTPException(400, "job already started")

    img_dir = COPY_IMAGES_DIR / job_id
    img_dir.mkdir(exist_ok=True)
    saved_paths = json.loads(row["image_paths"] or "[]")

    for f in files[:10]:
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            continue
        content = await f.read()
        if len(content) > 10 * 1024 * 1024:
            continue
        fname = f"{uuid.uuid4().hex[:8]}{ext}"
        dest = img_dir / fname
        dest.write_bytes(content)
        saved_paths.append(str(dest))

    conn = _copy_job_db()
    conn.execute("UPDATE copy_jobs SET image_paths=?, status='uploaded', updated_at=? WHERE id=?",
                 (json.dumps(saved_paths), time.time(), job_id))
    conn.commit()
    conn.close()
    return {"job_id": job_id, "image_count": len(saved_paths)}


@router.post("/copy-jobs/{job_id}/start")
async def start_copy_job(job_id: str, _user: str = Depends(require_user)):
    conn = _copy_job_db()
    row = conn.execute("SELECT * FROM copy_jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "job not found")
    if row["status"] not in ("pending", "uploaded"):
        raise HTTPException(400, f"job status is {row['status']}, cannot start")

    conn = _copy_job_db()
    conn.execute("UPDATE copy_jobs SET status='running', stage=0, stage_msg='启动中…', updated_at=? WHERE id=?",
                 (time.time(), job_id))
    conn.commit()
    conn.close()

    asyncio.create_task(_run_copy_job(job_id))
    return {"job_id": job_id, "status": "running"}


@router.get("/copy-jobs/{job_id}")
def get_copy_job(job_id: str, _user: str = Depends(require_user)):
    conn = _copy_job_db()
    row = conn.execute("SELECT * FROM copy_jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "job not found")
    d = dict(row)
    for key in ("asins", "image_paths", "result", "vision_result", "competitor_result"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except Exception:
                pass
    return d


@router.delete("/copy-jobs/{job_id}")
def delete_copy_job(job_id: str, _user: str = Depends(require_user)):
    # Clean up images
    img_dir = COPY_IMAGES_DIR / job_id
    if img_dir.exists():
        import shutil
        shutil.rmtree(img_dir, ignore_errors=True)
    conn = _copy_job_db()
    conn.execute("DELETE FROM copy_jobs WHERE id=?", (job_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

    return "\n\n".join(parts)
