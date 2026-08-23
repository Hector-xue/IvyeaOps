"""ASIN 采集：本机 curl 直连 Amazon（主路径）→ 本机无头浏览器兜底 →
sorftime 单图兜底。整条链**零 Docker、零外部服务**，采集以后台 job 形式运行，
进度实时可见。

历史：这里曾把 amazon-image-workflow（一套 docker-compose 应用）当兜底采集服务，
用户为此得先装 Docker Desktop。看过它的实现后拆掉了 —— 它的免费路径就是同一份
curl + puppeteer，唯一独有的是「真浏览器」这一层，现在由本机已装的
Chrome/Edge/Chromium 无头模式顶上（Windows 自带 Edge，等于零安装）。"""
from __future__ import annotations

import asyncio
import logging
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import require_user

from .common import project_row, update_project
from .jobs import JobHandle, start_job

logger = logging.getLogger("ivyea.routers.listing.scrape")

router = APIRouter()


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


# Amazon serves the same photo at many sizes under one media id:
#   .../images/I/71abc._AC_SX679_.jpg  ← thumbnail modifier
#   .../images/I/71abc.jpg             ← the original upload (full resolution)
# Keying on the media id keeps one photo from filling the set at 4 sizes, and
# dropping the modifier upgrades any thumbnail we found to the original.
_MEDIA_RE = re.compile(
    r"^(https?://.+/images/I/)([A-Za-z0-9_+%-]+?)(?:\._[^./]*)?\.(jpg|jpeg|png)$", re.I)


def _media_key(url: str) -> str:
    m = _MEDIA_RE.match(url.split("?")[0])
    return m.group(2) if m else url


def _hires_url(url: str) -> str:
    m = _MEDIA_RE.match(url.split("?")[0])
    return f"{m.group(1)}{m.group(2)}.{m.group(3)}" if m else url


def _parse_amazon_html(html_text: str) -> dict:
    """Extract title / bullets / full main-image set from raw Amazon product HTML.
    Images come from the inline "hiRes" (then "large") JSON — the static HTML has
    no rendered thumbnails. The data-a-dynamic-image pass below only ever fires on
    a rendered DOM (the headless-browser fallback), where that JSON may be gone."""
    import html as _html
    images: list[str] = []
    seen: set[str] = set()
    for pat in (r'"hiRes"\s*:\s*"(https?://[^"\\]+)"', r'"large"\s*:\s*"(https?://[^"\\]+)"'):
        if images:
            break
        for m in re.finditer(pat, html_text):
            u = m.group(1)
            key = _media_key(u)
            if key not in seen:
                seen.add(key)
                images.append(u)
            if len(images) >= 7:
                break

    # Rendered-DOM path (headless browser): the carousel thumbnails carry the
    # whole main-image set in data-a-dynamic-image={url: [w,h]} — mine it when
    # the inline hiRes JSON isn't present, upgrading each hit to the original.
    if not images:
        for m in re.finditer(r'data-a-dynamic-image="([^"]*)"', html_text):
            blob = _html.unescape(m.group(1))
            for um in re.finditer(r'"(https?://[^"]+)"\s*:\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]', blob):
                if min(int(um.group(2)), int(um.group(3))) < 500:
                    continue  # icon / swatch, not a main image
                full = _hires_url(um.group(1))
                key = _media_key(full)
                if key not in seen:
                    seen.add(key)
                    images.append(full)
                if len(images) >= 7:
                    break
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
            logger.debug("os.remove 失败（旁路，已忽略）", exc_info=True)


# ─── Headless-browser fallback (still no Docker) ───────────────────────────────
# Replaces what the old amazon-image-workflow container contributed (puppeteer).
# We drive a browser the machine ALREADY has: Chrome / Edge / Chromium. Windows
# 10/11 ship Edge, so for the target user this needs no install at all — and
# unlike curl it executes JS and presents a genuine browser fingerprint, which is
# what gets through when the plain request is challenged.

def _browser_bin() -> Optional[str]:
    """Locate a local Chromium-family browser, or None when the machine has none."""
    import shutil
    for name in ("google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    candidates: list[Path] = []
    if os.name == "nt":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        for root in (pf, pf86, local):
            if not root:
                continue
            candidates += [Path(root) / "Google/Chrome/Application/chrome.exe",
                           Path(root) / "Microsoft/Edge/Application/msedge.exe"]
    elif sys.platform == "darwin":
        candidates += [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                       Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                       Path("/Applications/Chromium.app/Contents/MacOS/Chromium")]
    for c in candidates:
        try:
            if c.is_file():
                return str(c)
        except OSError:
            continue
    return None


async def _scrape_amazon_browser(asin: str, marketplace: str, on_progress=None) -> Optional[dict]:
    """Render the product page in the local browser (headless) and parse the DOM.

    Returns None when no browser is installed or the page still came back as an
    anti-bot challenge. Uses --dump-dom (a plain stdout dump — no CDP client, no
    node, no extra dependency) with a throwaway profile dir so it can never touch
    the user's real browser session.

    subprocess.run in a worker thread, NOT asyncio.create_subprocess_exec — the
    async variant needs a ProactorEventLoop on Windows and raises
    NotImplementedError under uvicorn's loop there (same trap as the curl path)."""
    import shutil
    import subprocess
    import tempfile
    from app.core.proc import no_window_kwargs

    browser = _browser_bin()
    if not browser:
        logger.info("[scrape-browser] 本机未找到 Chrome/Edge/Chromium — 跳过浏览器兜底")
        return None
    if on_progress:
        on_progress(browser)
    url = f"https://www.{_amazon_domain(marketplace)}/dp/{asin}"
    profile = tempfile.mkdtemp(prefix="ivyea_cdp_")
    args = [browser, "--headless=new", "--disable-gpu", "--disable-extensions",
            "--disable-background-networking", "--no-first-run",
            "--no-default-browser-check", "--mute-audio",
            f"--user-data-dir={profile}", "--window-size=1280,2400",
            "--virtual-time-budget=15000", f"--user-agent={_REAL_UA}",
            "--dump-dom", url]
    if os.name != "nt":
        # Self-hosted installs commonly run as root, where Chrome refuses to start
        # with its sandbox on. We only ever load one fixed URL in a throwaway profile.
        args.insert(1, "--no-sandbox")
    try:
        cp = await asyncio.to_thread(
            subprocess.run, args,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=90,
            **no_window_kwargs())
        dom = (cp.stdout or b"").decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 — browser missing/crashed is just a miss
        logger.info("[scrape-browser] %s 启动失败：%s", asin, exc)
        return None
    finally:
        shutil.rmtree(profile, ignore_errors=True)

    blocked = bool(re.search(r"Type the characters you see in this image", dom, re.I)) or \
        bool(re.search(r"we just need to make sure you're not a robot", dom, re.I))
    parsed = _parse_amazon_html(dom) if not blocked else {}
    n_imgs = len(parsed.get("imageUrls") or [])
    logger.info("[scrape-browser] %s: %dB blocked=%s imgs=%d", asin, len(dom), blocked, n_imgs)
    return parsed if n_imgs else None


async def run_scrape(project_id: str, handle: Optional[JobHandle] = None) -> dict:
    """采集管线：curl 直连 → 本机无头浏览器 → sorftime，最后写回项目。
    可被 job 引擎或 agent 桥接直接 await。"""

    def progress(stage: str, message: str, value: float) -> None:
        if handle:
            handle.update(stage=stage, message=message, progress=value)

    row = project_row(project_id, "asin, marketplace")
    if not row:
        raise HTTPException(404)
    asin = row["asin"]
    marketplace = row["marketplace"] or "US"

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
        logger.debug("nd = await _scrape_amazon_native 失败（旁路，已忽略）", exc_info=True)

    # 1) Headless local browser — same full main-image set, no Docker. Only runs
    #    when the plain request was challenged, since it costs a few seconds.
    browser_ok = False
    if not native_ok:
        progress("browser", "直连被拦截，改用本机浏览器渲染采集…", 0.6)
        try:
            bd = await _scrape_amazon_browser(
                asin, marketplace,
                on_progress=lambda exe: progress(
                    "browser", f"本机浏览器渲染采集中（{Path(exe).name}）…", 0.65),
            )
            if bd and bd.get("imageUrls"):
                data = bd
                browser_ok = True
        except Exception:
            logger.debug("_scrape_amazon_browser 失败（旁路，已忽略）", exc_info=True)

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
            logger.debug("_ 失败（旁路，已忽略）", exc_info=True)

    image_urls = data.get("imageUrls") or data.get("images") or []
    if image_urls:
        data["reference_images"] = image_urls

    data["scrape_source"] = (
        "native" if native_ok else
        "browser" if browser_ok else
        "sorftime" if image_urls else "none"
    )
    data["full_images_available"] = native_ok or browser_ok
    data["browser_available"] = bool(_browser_bin())

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
