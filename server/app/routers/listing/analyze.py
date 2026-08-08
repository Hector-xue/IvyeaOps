"""AI 深度分析：全量图片视觉洞察 + 技能增强的结构化产品分析（后台 job）。"""
from __future__ import annotations

import json
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.core.security import require_user

from .ai import _call_ai, _collect_vision, _load_skill_knowledge, has_vision
from .common import (
    _build_product_context, _copy_source, _img_datauri_from_path,
    _img_datauri_from_url, _keywords_from_text, _reference_images,
    _strip_json, project_row, update_project,
)
from .jobs import JobHandle, start_job
from .scrape import _imgflow_base

router = APIRouter()

# The vision providers cap each call at 4 images, so we batch and aggregate.
_VISION_BATCH = 4


async def _analyze_all_images(scrape_data: dict, on_progress=None) -> str:
    """Vision-analyze EVERY scraped + uploaded image and return an aggregated
    "图片卖点清单" used downstream by analysis / copy / image-prompt generation.

    Returns "" when no images or no vision model is configured.
    """
    if not has_vision():
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
        if on_progress:
            on_progress(lo, hi, total)
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


async def run_analyze(project_id: str, handle: Optional[JobHandle] = None) -> dict:
    """Run skill-enhanced AI analysis + imgflow deep analysis (COSMO/Rufus/SIF)."""

    def progress(stage: str, message: str, value: float) -> None:
        if handle:
            handle.update(stage=stage, message=message, progress=value)

    row = project_row(project_id)
    if not row:
        raise HTTPException(404)

    scrape_data = json.loads(row["scrape_data"]) if row["scrape_data"] else {}
    product_context = _build_product_context(row, scrape_data, {})
    skill_knowledge = _load_skill_knowledge()

    # 0. Vision-analyze EVERY scraped + uploaded image into a selling-point list,
    #    reused downstream by copy + image-prompt generation (stored on the project).
    progress("vision", "视觉分析全部产品图…", 0.1)
    image_insights = await _analyze_all_images(
        scrape_data,
        on_progress=lambda lo, hi, total: progress(
            "vision", f"视觉分析第 {lo}-{hi} 张（共 {total} 张）…", 0.1 + 0.4 * hi / total),
    )

    # 1. Call imgflow deep analysis (COSMO/Rufus/SIF/Sorftime)
    imgflow_analysis = {}
    imgflow_id = row["imgflow_project_id"]
    if imgflow_id:
        progress("imgflow", "获取 imgflow 深度分析…", 0.55)
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(f"{_imgflow_base()}/analysis/{imgflow_id}")
                if resp.status_code == 200:
                    imgflow_analysis = resp.json()
        except Exception:
            pass

    # 2. Skill-enhanced AI analysis
    progress("analyze", "AI 结构化分析中（走统一降级链）…", 0.65)
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
        content = await _call_ai(prompt)
    except HTTPException as e:
        structured = _fallback_analysis(row, scrape_data, {})
        content = json.dumps(structured, ensure_ascii=False)
        fallback_used = True
        from .ai import text_chain_label
        warning = f"AI 当前不可用（{text_chain_label()} 均失败），已使用本地规则生成基础分析。原因：{str(e.detail)[:220]}"

    combined = {"ai_analysis": content, "imgflow": imgflow_analysis, "image_insights": image_insights}
    if fallback_used:
        combined["fallback"] = True
        combined["warning"] = warning
    # 这里原本是 content.strip().strip("```json").strip("```") —— str.strip(chars)
    # 按**字符集**剥离而不是去前缀，等于把两端所有的 ` j s o n 字符都啃掉，
    # 模型回复稍一变形就会把正文吃掉。同包已有更稳的 _strip_json（定位 {...} 区间），
    # 直接复用。
    combined["structured"] = _strip_json(content)

    progress("save", "保存分析结果…", 0.95)
    update_project(project_id, analysis_data=json.dumps(combined, ensure_ascii=False), status="analyzed")
    return combined


@router.post("/projects/{project_id}/ai-analyze")
async def ai_analyze_endpoint(project_id: str, _user: str = Depends(require_user)):
    """启动 AI 分析后台任务，立即返回 job。"""
    if not project_row(project_id, "id"):
        raise HTTPException(404)
    return start_job("analyze", project_id, {}, lambda handle: run_analyze(project_id, handle))
