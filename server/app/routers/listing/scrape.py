"""ASIN 采集：本机 curl 直连 Amazon（主路径）→ imgflow Docker 服务（兜底）→
sorftime 单图兜底。采集以后台 job 形式运行，进度实时可见。"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.core.config import settings
from app.core.security import require_user

from .common import _db, project_row, update_project
from .jobs import JobHandle, start_job

router = APIRouter()


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
    # repo root from source — using __file__.parents[N] would point inside the
    # PyInstaller _MEIPASS temp dir for the exe and never find the shipped folder.
    from app.core.version import runtime_root
    root = runtime_root()
    candidates += [root / "amazon-image-workflow", root.parent / "amazon-image-workflow",
                   Path(__file__).resolve().parents[4] / "amazon-image-workflow"]
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


async def _scrape_amazon_native(asin: str, marketplace: str, attempts: int = 5,
                                on_attempt=None) -> Optional[dict]:
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
    regardless of the event loop — this is the project's Windows-safe pattern.

    Anti-bot busting via a per-scrape COOKIE JAR (-c/-b): a cold curl request is
    erratically served Amazon's ~5KB challenge stub, but once any request sets
    session cookies the following requests reliably pass. So we carry cookies
    across the retry attempts (the challenge itself seeds them) — verified to turn
    a flaky 'block/ok/block/block' pattern into 'block→ok→ok→ok'."""
    import shutil
    import subprocess
    import logging
    import tempfile
    from app.core.proc import no_window_kwargs
    curl = shutil.which("curl")
    if not curl:
        logging.warning("[scrape-native] curl 不在 PATH 上 — 无法本机直连采集 (asin=%s)", asin)
        return None
    url = f"https://www.{_amazon_domain(marketplace)}/dp/{asin}"
    fd, jar = tempfile.mkstemp(prefix="ivyea_ck_", suffix=".txt")
    os.close(fd)
    args = [curl, "-sS", "-L", "--max-time", "25", "--compressed",
            "-A", _REAL_UA, "-c", jar, "-b", jar, url]
    try:
        for i in range(attempts):
            if on_attempt:
                on_attempt(i + 1, attempts)
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
    finally:
        try:
            os.remove(jar)
        except OSError:
            pass


async def run_scrape(project_id: str, handle: Optional[JobHandle] = None) -> dict:
    """采集管线：native → imgflow → sorftime，最后写回项目。可被 job 引擎或
    agent 桥接直接 await。"""

    def progress(stage: str, message: str, value: float) -> None:
        if handle:
            handle.update(stage=stage, message=message, progress=value)

    row = project_row(project_id, "asin, marketplace, imgflow_project_id")
    if not row:
        raise HTTPException(404)
    asin = row["asin"]
    marketplace = row["marketplace"] or "US"
    imgflow_id = row["imgflow_project_id"]

    data: dict = {}

    # 0) Native curl scrape — returns the FULL main-image set with no Docker.
    native_ok = False
    try:
        nd = await _scrape_amazon_native(
            asin, marketplace,
            on_attempt=lambda i, n: progress(
                "native", f"本机直连 Amazon 采集中（第 {i}/{n} 次尝试）…", 0.05 + 0.5 * i / n),
        )
        if nd and nd.get("imageUrls"):
            data = nd
            native_ok = True
    except Exception:
        pass

    # 1) Optional: imgflow scrape (amazon-image-workflow on :3001) — fallback for
    #    users who run the Docker service and where curl was blocked by anti-bot.
    imgflow_ok = False
    if not native_ok and imgflow_id:
        progress("imgflow", "直连被拦截，尝试 Docker 采集服务…", 0.6)
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{_imgflow_base()}/scrape/{imgflow_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    imgflow_ok = bool(data.get("imageUrls") or data.get("images"))
        except Exception:
            pass

    # 2) If both returned nothing, fall back to sorftime product_detail.
    #    NOTE: sorftime only carries ONE (white-background) main image, so this
    #    path can never recover the full set — the UI surfaces a hint via
    #    `scrape_source` below.
    has_title = bool(data.get("title"))
    has_bullets = bool(data.get("bullets"))
    if not has_title and not has_bullets:
        progress("sorftime", "尝试 Sorftime 数据兜底…", 0.8)
        try:
            from app.services import sorftime_service
            async with sorftime_service._make_client() as client:
                _, raw, err = await sorftime_service._safe_call(
                    client, "product_detail",
                    {"asin": asin, "amz_site": marketplace}, 1,
                )
                rec = sorftime_service.record(raw) if (raw and not err) else {}
                if rec:
                    # {"doc": …, "data": {"title", "main_image", "description"}}
                    if rec.get("title"):
                        data["title"] = str(rec["title"]).strip()
                    if rec.get("main_image"):
                        data["imageUrls"] = [str(rec["main_image"]).strip()]
                    desc_text = str(rec.get("description") or "").strip()
                    if desc_text:
                        parts = [p.strip() for p in re.split(r'<br>|\n', desc_text) if p.strip()]
                        if parts:
                            data["bullets"] = parts[:5]
                            data["description"] = desc_text
                elif raw and not err and isinstance(raw, str):
                    # Legacy plain text: "标题：xxx\n主图：xxx\n产品描述：xxx"
                    title_m = re.search(r'标题[：:]\s*(.+)', raw)
                    if title_m:
                        data["title"] = title_m.group(1).strip()
                    img_m = re.search(r'主图[：:]\s*(https?://\S+)', raw)
                    if img_m:
                        data["imageUrls"] = [img_m.group(1).strip()]
                    desc_m = re.search(r'产品描述[：:]\s*(.+?)(?:\r?\n\r?\n|\r?\n[^\u4e00-\u9fff])', raw, re.DOTALL)
                    if desc_m:
                        desc_text = desc_m.group(1).strip()
                        parts = re.split(r'<br>|\n', desc_text)
                        parts = [p.strip() for p in parts if p.strip()]
                        if parts:
                            data["bullets"] = parts[:5]
                            data["description"] = desc_text
        except Exception:
            pass

    image_urls = data.get("imageUrls") or data.get("images") or []
    if image_urls:
        data["reference_images"] = image_urls

    data["scrape_source"] = (
        "native" if native_ok else
        "imgflow" if imgflow_ok else
        "sorftime" if image_urls else "none"
    )
    data["full_images_available"] = native_ok or imgflow_ok

    progress("save", "写入采集结果…", 0.95)
    # 保留 manual / uploaded_images / 白底检测等既有字段，采集只更新采集面。
    prev_row = project_row(project_id, "scrape_data")
    previous = {}
    if prev_row and prev_row["scrape_data"]:
        try:
            previous = json.loads(prev_row["scrape_data"])
        except Exception:
            previous = {}
    for keep in ("manual", "uploaded_images", "white_product_source", "white_product_source_check"):
        if keep in previous and keep not in data:
            data[keep] = previous[keep]
    update_project(project_id, scrape_data=json.dumps(data, ensure_ascii=False), status="scraped")
    return data


async def scrape(project_id: str, _user: str = "bridge") -> dict:
    """兼容入口（agent 桥接 ivyea_ops_tools 直接 await）：同步跑完采集并返回结果。"""
    return await run_scrape(project_id)


@router.post("/projects/{project_id}/scrape")
async def scrape_endpoint(project_id: str, _user: str = Depends(require_user)):
    """启动采集后台任务，立即返回 job。"""
    if not project_row(project_id, "id"):
        raise HTTPException(404)
    return start_job("scrape", project_id, {}, lambda handle: run_scrape(project_id, handle))
