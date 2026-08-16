"""Listing 文案引擎：图片识别 → 竞品数据 → 统一 AI 链生成 → 结构化解析。

一条管线两个入口：
- 新入口 `POST /projects/{id}/copy`：项目级后台 job，产品信息/素材图/竞品 ASIN
  全部由服务端从项目里取，结果落到项目上（刷新不丢）。
- 旧入口 `/copy-jobs*`：保留给 agent 桥接（ivyea_ops_tools）和历史调用，管线同源。

整改点：生成阶段走统一降级链 run_text_chain；视觉识别走统一视觉链
stream_vision —— 老实现直连 apimart /messages（该端点恒 403）导致这两步长期
处于坏死状态。
"""
from __future__ import annotations

import asyncio
import logging
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.config import settings
from app.core.security import require_user

from .ai import (
    _call_ai, _collect_vision, has_semantic_vision, has_vision, vision_tier, vision_tier_label,
)
from .common import (
    _parse_copy_result, _strip_json, project_row, update_project,
)
from .jobs import JobHandle, start_job

logger = logging.getLogger("ivyea.routers.listing.copywriting")

router = APIRouter()

COPY_JOB_DB = settings.data_dir / "listing_copy_jobs.sqlite3"
COPY_IMAGES_DIR = settings.data_dir / "listing_copy_images"
COPY_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

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
    logger.debug("_copy_job_db 失败（旁路，已忽略）", exc_info=True)


class CopyJobReq(BaseModel):
    marketplace: str = "US"
    product_type: str
    asins: list[str] = []
    product_notes: str = ""
    project_id: Optional[str] = None


class ProjectCopyReq(BaseModel):
    extra_notes: str = ""
    competitor_asins: list[str] = []


# ─── 管线 ─────────────────────────────────────────────────────────────────────

async def _analyze_images_vision(image_paths: list[str], product_type: str) -> dict:
    """统一视觉链识别产品图。Falls back gracefully.

    image_paths 支持本地路径和 http(s) URL 混合——无上传素材时调用方会传采集图
    URL，识别阶段不再"跳过还打绿勾"。"""
    if not image_paths:
        return {"mode": "skipped", "features": [], "reason": "No images provided"}
    if not has_vision():
        return {"mode": "skipped", "features": [], "reason": "No vision provider configured"}

    import base64
    from .common import _img_datauri_from_url
    images_b64: list[str] = []
    for ip in image_paths[:6]:  # max 6 images for cost
        try:
            if str(ip).startswith(("http://", "https://")):
                uri = await _img_datauri_from_url(str(ip))
                if uri:
                    images_b64.append(uri)
                continue
            with open(ip, "rb") as f:
                data = base64.b64encode(f.read()).decode()
            ext = Path(ip).suffix.lower().lstrip(".")
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                    "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")
            images_b64.append(f"data:{mime};base64,{data}")
        except Exception:
            logger.debug("uri = await _img_datauri_from_url 失败（旁路，已忽略）", exc_info=True)
    if not images_b64:
        return {"mode": "skipped", "features": [], "reason": "Could not read image files"}

    semantic = has_semantic_vision()
    if semantic:
        prompt = (
            f"You are analyzing product images for an Amazon listing. "
            f"Product type: {product_type}. "
            f"Extract: materials, key features, dimensions/size cues, accessories included, "
            f"color options, usage scenarios visible in images. "
            f"Be specific and factual. Do not invent features not visible. "
            f"Return JSON: {{\"features\": [\"feature1\", ...], \"materials\": \"...\", "
            f"\"size_hints\": \"...\", \"accessories\": \"...\", \"scenarios\": [\"...\"]}}"
        )
    else:
        # T3：拿到的是本地 CV 读数 + OCR 文本，不是画面。素材图上的文字（包装标注、
        # 参数表、卖点条）恰恰是文案最有用的原料，所以这一步在 T3 下仍有价值——
        # 但只能提取"图上写了什么"，不能提取"图上看起来是什么材质"。
        prompt = (
            f"你在为亚马逊 listing 分析产品图，但**没有看到画面**，只拿到了客观测量读数"
            f"（尺寸、白底判定、主体占比、主色板）和 OCR 识别出的图上文字。\n"
            f"产品类型：{product_type}\n\n"
            f"只返回 JSON，且**只填能从 OCR 文字或测量读数确证的内容，其余留空数组/空字符串**：\n"
            f'{{"features": ["仅来自 OCR 文字的卖点/参数，逐条注明是图上文字"], '
            f'"materials": "仅当 OCR 文字明确写了材质时填写，否则空字符串", '
            f'"size_hints": "仅当 OCR 文字明确写了尺寸时填写，否则空字符串", '
            f'"accessories": "仅当 OCR 文字明确列出配件时填写，否则空字符串", '
            f'"scenarios": [], "colour_hints": ["主色板里属于产品的颜色"]}}\n\n'
            f"严禁根据颜色或占比数字推测材质、功能、使用场景。推不出来就留空。"
        )
    try:
        text = await asyncio.wait_for(_collect_vision(prompt, images_b64), timeout=180)
    except Exception as exc:  # noqa: BLE001
        return {"mode": "error", "features": [], "reason": str(exc)}
    if not text:
        return {"mode": "skipped", "features": [], "reason": "Vision call failed"}
    mode = "vision" if semantic else "local_cv"
    parsed = _strip_json(text)
    if parsed:
        parsed["mode"] = mode
        parsed["vision_tier"] = vision_tier()
        if not semantic:
            parsed["reason"] = (f"当前视觉能力为「{vision_tier_label()}」，"
                                "仅从图上文字与测量读数提取，未做画面语义识别。")
        return parsed
    return {"mode": mode, "features": [text[:500]], "raw": text, "vision_tier": vision_tier()}


async def _fetch_competitor_data(asins: list[str], marketplace: str) -> dict:
    """Fetch competitor product/keyword data via sorftime. Fails gracefully."""
    from app.services.sorftime_service import _make_client, _safe_call
    results = {}
    errors = []
    try:
        async with _make_client() as client:
            tasks = []
            for i, asin in enumerate(asins[:5]):
                # product_detail, not product_report — the latter only ever answers
                # with "call the following tools to combine their data".
                tasks.append(_safe_call(client, "product_detail", {"asin": asin, "amz_site": marketplace}, i + 1))
                tasks.append(_safe_call(client, "competitor_product_keywords",
                                        {"asin": asin, "keyword_support_site": marketplace}, i + 10))
            gathered = await asyncio.gather(*tasks)
            for name, val, err in gathered:
                if err:
                    errors.append(err)
                elif val:
                    # sorftime 某些工具会返回"如何分析"的方法论指引而非数据，
                    # 混进文案 prompt 只会稀释真实竞品信息，直接丢弃。
                    text = str(val)
                    if "请调用对应的工具" in text or text.strip().startswith("分析一个产品的方法"):
                        continue
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
        "You are an Amazon listing copywriting expert.",
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
            parts.append("Image Analysis - Observed Features:\n" + "\n".join(f"- {f}" for f in features[:20]))
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


async def run_copy_pipeline(*, marketplace: str, product_type: str, asins: list[str],
                            product_notes: str, image_paths: list[str],
                            progress=None) -> dict:
    """完整文案管线，返回 {result, vision_result, competitor_result}。

    progress(stage:int, message:str) —— stage 与前端四步进度条对齐：
    0=图片识别 1=竞品数据 2=文案生成 3=完成
    """

    def report(stage: int, message: str) -> None:
        if progress:
            progress(stage, message)

    report(0, "正在分析产品图片…" if image_paths else "无可用产品图（未上传且未采集到），跳过图片识别")
    vision_result = await _analyze_images_vision(image_paths, product_type)

    report(1, "正在查询竞品数据…" if asins else "未填写竞品ASIN，跳过竞品查询")
    competitor_result = await _fetch_competitor_data(asins, marketplace) if asins else {
        "data": {}, "errors": [], "available": False
    }

    from .ai import text_chain_label
    report(2, f"正在生成文案（{text_chain_label()}）…")
    prompt = _build_listing_prompt(marketplace, product_type, product_notes, vision_result, competitor_result)
    result_text = await _call_ai(prompt, web_search=False)
    if not str(result_text or "").strip():
        raise RuntimeError("AI 未返回内容，请稍后重试")

    parsed_result = _parse_copy_result(result_text) or {"raw": result_text}
    report(3, "文案生成完成")
    return {
        "result": parsed_result,
        "vision_result": vision_result,
        "competitor_result": competitor_result,
    }


# ─── 新入口：项目级文案 job ───────────────────────────────────────────────────

def _compose_project_notes(row, scrape_data: dict) -> str:
    manual = scrape_data.get("manual", {}) if isinstance(scrape_data.get("manual"), dict) else {}
    bullets = scrape_data.get("bullets") or []
    if isinstance(bullets, str):
        bullets = [bullets]
    parts = [
        manual.get("product_name") and f"产品: {manual['product_name']}",
        manual.get("target_audience") and f"目标受众: {manual['target_audience']}",
        manual.get("selling_points") and f"卖点: {manual['selling_points']}",
        manual.get("description") and f"描述: {manual['description']}",
        scrape_data.get("title") and f"参考标题: {scrape_data['title']}",
        bullets and "参考五点:\n" + "\n".join(str(b) for b in bullets[:5]),
    ]
    return "\n".join(str(p) for p in parts if p)


async def run_project_copy(project_id: str, body: ProjectCopyReq,
                           handle: Optional[JobHandle] = None) -> dict:
    row = project_row(project_id)
    if not row:
        raise HTTPException(404)
    scrape_data = json.loads(row["scrape_data"]) if row["scrape_data"] else {}
    manual = scrape_data.get("manual", {}) if isinstance(scrape_data.get("manual"), dict) else {}
    notes = _compose_project_notes(row, scrape_data)
    if body.extra_notes.strip():
        notes = f"{notes}\n补充要求: {body.extra_notes.strip()}" if notes else body.extra_notes.strip()
    image_paths = [p for p in (scrape_data.get("uploaded_images") or []) if Path(str(p)).exists()]
    if not image_paths:
        # 没有上传素材时用采集图做视觉识别——识别阶段要么真跑、要么明说跳过
        image_paths = [str(u) for u in (scrape_data.get("reference_images") or [])[:4]]
    asins = [a.strip().upper() for a in ([row["asin"]] + list(body.competitor_asins)) if str(a).strip()][:10]

    stages = ["vision", "competitor", "generate", "done"]

    def progress(stage: int, message: str) -> None:
        if handle:
            handle.update(stage=stages[min(stage, 3)], message=message,
                          progress=[0.1, 0.35, 0.6, 1.0][min(stage, 3)])

    outcome = await run_copy_pipeline(
        marketplace=row["marketplace"] or "US",
        product_type=manual.get("product_name") or row["asin"] or "product",
        asins=asins,
        product_notes=notes,
        image_paths=image_paths,
        progress=progress,
    )
    update_project(
        project_id,
        copy_result=json.dumps(outcome["result"], ensure_ascii=False),
        copy_job_id=handle.job_id if handle else None,
        status="copywritten",
    )
    return outcome


@router.post("/projects/{project_id}/copy")
async def project_copy_endpoint(project_id: str, body: ProjectCopyReq,
                                _user: str = Depends(require_user)):
    """一键生成 Listing 文案（后台 job，立即返回）。"""
    if not project_row(project_id, "id"):
        raise HTTPException(404)
    return start_job(
        "copy", project_id, body.model_dump(),
        lambda handle: run_project_copy(project_id, body, handle),
    )


# ─── 旧入口：copy-jobs（agent 桥接 / 历史兼容）────────────────────────────────

async def _run_copy_job(job_id: str) -> None:
    """Background task driving the legacy copy_jobs table through the shared pipeline."""

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

        outcome = await run_copy_pipeline(
            marketplace=row.get("marketplace", "US"),
            product_type=row.get("product_type", ""),
            asins=json.loads(row.get("asins", "[]")),
            product_notes=row.get("product_notes", ""),
            image_paths=json.loads(row.get("image_paths", "[]")),
            progress=update,
        )

        result_json = json.dumps(outcome["result"], ensure_ascii=False)
        conn = _copy_job_db()
        conn.execute(
            "UPDATE copy_jobs SET status='done', stage=3, stage_msg='文案生成完成', result=?, "
            "vision_result=?, competitor_result=?, updated_at=? WHERE id=?",
            (result_json,
             json.dumps(outcome["vision_result"], ensure_ascii=False),
             json.dumps(outcome["competitor_result"], ensure_ascii=False),
             time.time(), job_id),
        )
        conn.commit()
        conn.close()

        # Persist the result onto the linked project so it survives page refresh.
        project_id = row.get("project_id")
        if project_id:
            try:
                update_project(project_id, copy_result=result_json, copy_job_id=job_id)
            except Exception:
                logger.debug("update_project 失败（旁路，已忽略）", exc_info=True)

    except Exception as exc:
        conn = _copy_job_db()
        conn.execute(
            "UPDATE copy_jobs SET status='failed', stage_msg=?, error=?, updated_at=? WHERE id=?",
            (str(exc), str(exc), time.time(), job_id),
        )
        conn.commit()
        conn.close()


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
                logger.debug("d 失败（旁路，已忽略）", exc_info=True)
    if d.get("result"):
        d["result"] = _parse_copy_result(d["result"]) or d["result"]
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
