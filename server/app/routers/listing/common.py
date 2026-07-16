"""Listing 工作台共享基础：数据库、Apimart 配置、文本/JSON 解析、产品上下文与白底检测。

所有子模块共用这一层；不放路由。
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import mimetypes
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

import httpx

from app.core.config import settings

DB_PATH = settings.data_dir / "listing.sqlite3"
IMAGES_DIR = settings.data_dir / "listing_images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)


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
    conn.execute("""CREATE TABLE IF NOT EXISTS listing_jobs (
        id TEXT PRIMARY KEY,
        project_id TEXT,
        kind TEXT,
        status TEXT DEFAULT 'running',
        stage TEXT DEFAULT '',
        message TEXT DEFAULT '',
        progress REAL DEFAULT 0,
        total INTEGER DEFAULT 0,
        done_count INTEGER DEFAULT 0,
        params TEXT,
        result TEXT,
        error TEXT,
        created_at REAL,
        updated_at REAL
    )""")
    conn.commit()
    return conn


_db().close()

# Migration: add columns if missing (kept from the single-file era so existing
# databases keep working without a manual step).
for _col in [
    "image_slots TEXT", "templates TEXT", "copy_result TEXT", "copy_job_id TEXT",
    "highlights TEXT", "shot_plan TEXT", "creative_sets TEXT",
]:
    try:
        _conn = _db()
        _conn.execute(f"ALTER TABLE listing_projects ADD COLUMN {_col}")
        _conn.commit()
        _conn.close()
    except Exception:
        pass


def project_row(project_id: str, columns: str = "*"):
    conn = _db()
    row = conn.execute(
        f"SELECT {columns} FROM listing_projects WHERE id = ?", (project_id,)
    ).fetchone()
    conn.close()
    return row


def update_project(project_id: str, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{key} = ?" for key in fields)
    conn = _db()
    conn.execute(
        f"UPDATE listing_projects SET {sets}, updated_at = ? WHERE id = ?",
        (*fields.values(), time.time(), project_id),
    )
    conn.commit()
    conn.close()


# ─── Apimart（生图专用 provider）──────────────────────────────────────────────

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


# ─── 文本 / JSON 解析 ─────────────────────────────────────────────────────────

def _clean_text(value) -> str:
    return " ".join(str(value or "").replace("\n", " ").split())


def _strip_json(text: str):
    """Best-effort pull a JSON object out of an LLM reply (strip fences/prose)."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json":
            t = t[4:]
    i, j = t.find("{"), t.rfind("}")
    if i != -1 and j > i:
        t = t[i:j + 1]
    for cand in (t, text):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return None


def _parse_copy_result(value) -> Optional[dict]:
    """Parse generated listing copy and repair a narrow class of model JSON slips.

    Copy is a structured product surface. A single malformed bullet must not
    collapse the entire UI into a raw JSON dump.
    """
    if isinstance(value, dict):
        if value.get("titles") or value.get("bullets_a") or value.get("bullets_b"):
            return value
        value = value.get("raw")
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    candidates = [text]
    # Common provider slip: ["Bullet heading": body" instead of
    # "[Bullet heading]: body". Repair the affected line only.
    candidates.append(re.sub(
        r'(?m)^(\s*)\["([^"\n]+)":\s*([^"\n]*)"(,?)$',
        r'\1"[\2]: \3"\4', text,
    ))
    candidates.append(text.replace("“", '"').replace("”", '"'))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return None


# ─── 产品上下文 ───────────────────────────────────────────────────────────────

def _scrape_field(scrape_data: dict, key: str, default=""):
    product = scrape_data.get("product") if isinstance(scrape_data.get("product"), dict) else {}
    return scrape_data.get(key) or product.get(key) or default


def _reference_images(scrape_data: dict) -> list[str]:
    refs = scrape_data.get("reference_images") or scrape_data.get("imageUrls") or []
    if isinstance(refs, str):
        return [refs]
    return [str(x) for x in refs if str(x).strip()] if isinstance(refs, list) else []


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
            cr = _parse_copy_result(json.loads(row["copy_result"]))
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


# ─── 白底产品真值检测 ─────────────────────────────────────────────────────────

def _white_metrics_ready(metrics: dict, *, allow_legacy_bundle: bool = False) -> bool:
    """Return whether measured pixels describe a usable white ecommerce source.

    A normal product photo leaves a mostly-white border.  Large bundles are a
    legitimate second shape: the items can touch one canvas edge and occupy most
    of the frame while the white background is still one continuous region that
    spans the other three sides.  Keeping these as separate acceptance paths
    avoids lowering the normal white-border threshold for bright lifestyle art.
    """
    border = float(metrics.get("border_white") or 0)
    overall = float(metrics.get("overall_white") or 0)
    connected = float(metrics.get("edge_connected_white") or 0)
    white_sides = int(metrics.get("white_sides") or 0)
    sides = [float(v) for v in (metrics.get("side_white") or []) if isinstance(v, (int, float))]
    classic_white_main = border >= .62 and overall >= .30
    connected_bundle = overall >= .34 and connected >= .30 and white_sides >= 3
    # Tightly cropped white mains are a third legitimate shape: the product
    # fills nearly the whole canvas, so total white is small (可低至 ~17%)
    # and even the border band is mostly product. Their unmistakable signature
    # is that every canvas edge LINE is almost entirely strict-white and that
    # white forms one edge-connected sweep (lifestyle photos never light all
    # four edges pure #F8+ white). Validated against 138 real workspace images
    # with zero false accepts.
    tight_white_main = (
        len(sides) == 4 and min(sides) >= .80
        and connected >= .12 and overall > 0 and connected >= overall * .8
    )
    # Reports written before edge-connectivity was introduced cannot be
    # re-scored without downloading the image again.  Only the first scraped
    # gallery image may use this narrow migration path; callers control that
    # condition with allow_legacy_bundle.
    legacy_bundle = (
        allow_legacy_bundle and "edge_connected_white" not in metrics
        and border >= .30 and overall >= .35
    )
    return classic_white_main or connected_bundle or tight_white_main or legacy_bundle


def _white_background_score(raw: bytes) -> dict:
    """Identify a white-background ecommerce source, including dense bundles."""
    from PIL import Image
    try:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        image.thumbnail((240, 240))
    except Exception:
        return {"ready": False, "score": 0, "border_white": 0, "overall_white": 0}
    width, height = image.size
    if width < 20 or height < 20:
        return {"ready": False, "score": 0, "border_white": 0, "overall_white": 0}
    pixels = image.load()
    margin_x, margin_y = max(2, width // 12), max(2, height // 12)

    def is_white(pixel) -> bool:
        return min(pixel) >= 238 and max(pixel) - min(pixel) <= 16

    total = white = border_total = border_white = 0
    white_mask = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            value = pixels[x, y]
            pixel_white = is_white(value)
            white_mask[y * width + x] = int(pixel_white)
            total += 1
            white += int(pixel_white)
            if x < margin_x or x >= width - margin_x or y < margin_y or y >= height - margin_y:
                border_total += 1
                border_white += int(pixel_white)
    overall_ratio = white / max(1, total)
    border_ratio = border_white / max(1, border_total)

    # Count the strict-white region connected to the outside of the canvas.
    # This distinguishes a genuine white sweep surrounding a dense bundle from
    # disconnected pale labels, screens, clouds or highlights inside a scene.
    seen = bytearray(total)
    stack: list[int] = []

    def seed(index: int) -> None:
        if white_mask[index] and not seen[index]:
            seen[index] = 1
            stack.append(index)

    for x in range(width):
        seed(x)
        seed((height - 1) * width + x)
    for y in range(height):
        seed(y * width)
        seed(y * width + width - 1)
    connected_white = 0
    while stack:
        index = stack.pop()
        connected_white += 1
        x = index % width
        for neighbor in (
            index - width if index >= width else -1,
            index + width if index < total - width else -1,
            index - 1 if x else -1,
            index + 1 if x < width - 1 else -1,
        ):
            if neighbor >= 0 and white_mask[neighbor] and not seen[neighbor]:
                seen[neighbor] = 1
                stack.append(neighbor)
    connected_ratio = connected_white / max(1, total)
    side_ratios = (
        sum(white_mask[x] for x in range(width)) / width,
        sum(white_mask[(height - 1) * width + x] for x in range(width)) / width,
        sum(white_mask[y * width] for y in range(height)) / height,
        sum(white_mask[y * width + width - 1] for y in range(height)) / height,
    )
    white_sides = sum(ratio >= .55 for ratio in side_ratios)
    legacy_score = (border_ratio * .68 + overall_ratio * .32) * 100
    connected_score = (connected_ratio * .55 + (white_sides / 4) * .45) * 100
    metrics = {
        "score": round(max(legacy_score, connected_score)),
        "border_white": round(border_ratio, 3),
        "overall_white": round(overall_ratio, 3),
        "edge_connected_white": round(connected_ratio, 3),
        "white_sides": white_sides,
        "side_white": [round(ratio, 3) for ratio in side_ratios],
    }
    return {
        "ready": _white_metrics_ready(metrics),
        **metrics,
    }


def _cached_white_product_source(scrape_data: dict) -> str:
    selected = str(scrape_data.get("white_product_source") or "").strip()
    if selected:
        return selected
    check = scrape_data.get("white_product_source_check")
    candidates = check.get("candidates") if isinstance(check, dict) else []
    valid: list[tuple[int, dict]] = []
    for index, item in enumerate(candidates or []):
        if not isinstance(item, dict):
            continue
        allow_legacy = index == 0 and item.get("kind") == "scraped"
        if _white_metrics_ready(item, allow_legacy_bundle=allow_legacy):
            valid.append((index, item))
    # Uploaded product truth wins.  Otherwise preserve Amazon gallery order:
    # the first qualifying image is the main source, not a later infographic
    # that happens to contain more white pixels.
    valid.sort(key=lambda pair: (pair[1].get("kind") != "uploaded", pair[0]))
    return str(valid[0][1].get("url") or "") if valid else ""


async def _detect_white_product_source(project_id: str, scrape_data: dict) -> tuple[str, list[dict]]:
    candidates: list[tuple[str, str]] = []
    for path_str in scrape_data.get("uploaded_images", []) or []:
        path_obj = Path(path_str)
        if path_obj.exists():
            candidates.append((f"/api/listing/images/{project_id}/{path_obj.name}", "uploaded"))
    for url in scrape_data.get("reference_images") or scrape_data.get("imageUrls") or []:
        if str(url).strip():
            candidates.append((str(url), "scraped"))

    async def inspect(url: str, kind: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                raw = await _fetch_image_bytes(client, url)
            metrics = _white_background_score(raw)
        except Exception:
            metrics = {"ready": False, "score": 0, "border_white": 0, "overall_white": 0}
        return {"url": url, "kind": kind, **metrics}

    report = await asyncio.gather(*(inspect(url, kind) for url, kind in candidates)) if candidates else []
    valid = [(index, item) for index, item in enumerate(report) if item.get("ready")]
    valid.sort(key=lambda pair: (pair[1].get("kind") != "uploaded", pair[0]))
    return (str(valid[0][1]["url"]) if valid else ""), report


# ─── 图片字节读取（本地工作区 / 上传目录 / data URI / 外链）──────────────────

async def _fetch_image_bytes(client: httpx.AsyncClient, url: str) -> bytes:
    """Load image bytes from a workspace url (file read) or an external url (http)."""
    from app.routers.image_translate import WORKSPACE_DIR
    prefix = "/api/image-translate/images/"
    if prefix in url:
        fn = url.split(prefix, 1)[1].split("?")[0]
        p = WORKSPACE_DIR / fn
        if p.exists():
            return p.read_bytes()
    upload_prefix = "/api/listing/images/"
    if upload_prefix in url:
        rel = url.split(upload_prefix, 1)[1].split("?")[0]
        parts = [part for part in rel.split("/") if part and part not in {".", ".."}]
        if len(parts) >= 2:
            p = IMAGES_DIR / parts[0] / parts[-1]
            if p.exists():
                return p.read_bytes()
    if url.startswith("data:") and "," in url:
        return base64.b64decode(url.split(",", 1)[1])
    if "m.media-amazon." in url:
        import shutil
        import subprocess
        curl = shutil.which("curl")
        if curl:
            from app.core.proc import no_window_kwargs
            completed = await asyncio.to_thread(
                subprocess.run,
                [curl, "-fsSL", "--max-time", "25", url],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30,
                **no_window_kwargs(),
            )
            if completed.returncode == 0 and completed.stdout:
                return completed.stdout
    r = await client.get(url)
    r.raise_for_status()
    return r.content


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
