"""生图执行层（后台 job 化）。

老架构：浏览器提交任务后自己每 5 秒轮询 8 分钟、批量生成是前端 for 循环 ——
刷新/断网/切页全部前功尽弃。新架构：提交 + 轮询 + 画布规范化 + 成图质检 +
按质检意见自动重画一次，全部在服务端 job 里跑完并逐张落库；前端只订阅进度。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import re
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.security import require_user

from .common import (
    _apimart_base, _apimart_key, _fetch_image_bytes, project_row,
)
from .jobs import JobHandle, start_job
from .visuals import (
    ReviewRenderReq, _persist_shot_plan, review_render_core,
)

logger = logging.getLogger("ivyea.routers.listing.images")

router = APIRouter()


class RenderImageReq(BaseModel):
    deliverable: str = "gallery"     # gallery | aplus
    index: int                       # 分镜序号（0 起）
    prompt_override: str = ""        # 编辑过的最终提示词（可选）


class RenderSetReq(BaseModel):
    deliverable: str = "gallery"
    only_missing: bool = False       # True = 只补没生成的


# ─── Apimart 提交与轮询 ───────────────────────────────────────────────────────

def _parse_image_task_payload(payload: dict) -> dict:
    """Convert Apimart's task payload into a stable client-facing state."""
    task_data = payload.get("data") if isinstance(payload, dict) else {}
    task_data = task_data if isinstance(task_data, dict) else {}
    status = str(task_data.get("status") or "processing").strip().lower()
    if status == "completed":
        result = task_data.get("result") if isinstance(task_data.get("result"), dict) else {}
        images = result.get("images") if isinstance(result.get("images"), list) else []
        url = images[0].get("url") if images and isinstance(images[0], dict) else ""
        if isinstance(url, list):
            url = url[0] if url else ""
        if url:
            return {"status": "completed", "provider_url": str(url)}
        return {"status": "failed", "error": "任务完成但未返回图片 URL"}
    if status == "failed":
        detail = task_data.get("error") or task_data.get("message") or task_data.get("result") or "上游生图任务失败"
        return {"status": "failed", "error": str(detail)[:500]}
    return {"status": status or "processing"}


async def _materialise_refs(ref_urls: list[str]) -> list[str]:
    """Browser-facing workspace URLs are not reachable by the external image
    provider. Materialise local references as data URIs while preserving list
    order (product truth first)."""
    materialised: list[str] = []
    async with httpx.AsyncClient(timeout=60) as ref_client:
        for url in ref_urls[:2]:
            value = str(url or "").strip()
            if value.startswith("/api/"):
                try:
                    raw_ref = await _fetch_image_bytes(ref_client, value)
                    mime = mimetypes.guess_type(value.split("?", 1)[0])[0] or "image/png"
                    materialised.append(f"data:{mime};base64,{base64.b64encode(raw_ref).decode()}")
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(502, f"本地参考图读取失败：{exc}") from exc
            elif value:
                materialised.append(value)
    return materialised


def _fidelity_preamble(prompt: str, ref_count: int, reference_mode: str,
                       presence: str = "supporting", product_scale: float = 0.32,
                       include_accessories: bool = False) -> str:
    """Deterministic product-fidelity preamble, graded by product presence.

    - hero/supporting：完整保真锁定，但默认只锁主产品本体——参考图常是"产品+
      配件"的捆绑白底图，老版本连 accessories and quantity 一起锁导致 SD 卡、
      数据线漂浮在每张图里；只有 in_box 类（include_accessories=True）才要求配件。
    - environmental：保真 + 明确"产品在画面中很小、场景才是主角"。
    - absent（ref_count=0）：无参考，直接返回。
    """
    if not ref_count:
        return prompt
    if reference_mode == "template" and ref_count >= 2:
        return (
            "CRITICAL REFERENCE ROLES — DO NOT MIX THEM. REFERENCE 1 is the ONLY PRODUCT TRUTH: reproduce its "
            "product identically, including shape, proportions, color, material, controls and ports. "
            "REFERENCE 2 is ONLY A VISUAL TEMPLATE: reuse its content hierarchy, shot category, camera/view type, "
            "subject scale, negative-space map and design density, but do not copy its product identity, brand, logo, "
            "written text, person identity or exact pixels. Replace the template's product with REFERENCE 1. "
            "Create one coherent commercial image with believable perspective, contact, shadow and lighting.\n\n"
        ) + prompt
    accessory_rule = (
        "Include every item that belongs to the purchased set exactly as shown. "
        if include_accessories else
        "Reproduce ONLY the primary product unit. If the reference photo is a bundle shot, IGNORE the loose extras "
        "(memory cards, cables, straps, manuals, packaging) — do NOT carry them into this scene. "
    )
    scale_rule = ""
    if presence == "environmental":
        scale_rule = (
            f"In this composition the product intentionally appears SMALL — roughly {max(5, int(product_scale * 100))}% "
            "of the frame, naturally placed in a believable real scene. The environment and the story dominate; "
            "do not enlarge the product into a hero close-up. "
        )
    elif presence == "supporting":
        scale_rule = (
            f"The product shares the stage, occupying roughly {int(product_scale * 100)}% of the frame as one element "
            "of a designed composition — not a full-frame hero close-up. "
        )
    return (
        "CRITICAL — IMMUTABLE PRODUCT TRUTH: REFERENCE IMAGE 1 shows the EXACT sellable product. "
        "Reproduce it identically: same silhouette, geometry, proportions, colors, color blocking, materials, "
        "surface finish, seams, openings, lenses, controls, ports, logos, labels and printed product markings. "
        f"{accessory_rule}{scale_rule}"
        "Change ONLY the environment, camera, composition, lighting, supporting graphic design and the exact "
        "artwork copy explicitly requested by the prompt. Do NOT redesign, recolor, relabel or simplify the product. "
        "When the prompt bans text, that means no ADDED marketing text; existing markings on the reference product "
        "must remain unchanged. If a requested composition conflicts with product fidelity, preserve the product.\n\n"
    ) + prompt


def _crop_safe_zone_note(target_size: str, provider_size: str) -> str:
    """告知模型成图会被中心裁剪，让它把关键内容放进会保留的安全区。

    供应商 gpt-image-2 只支持 1024×1024 / 1536×1024 / 1024×1536 三种画布，比例与目标
    不符时 `_await_generation` 用 `normalise_canvas(mode="cover")`（即 ImageOps.fit 中心
    裁剪）把成图裁成目标尺寸。高级 A+ 1464×600（2.44:1）在 1536×1024 上生成后会被上下各
    裁掉约 19%——模型放在顶部的主标题正好被切掉，只剩中间像"贴上去"的字幕条。这里按 cover
    数学算出会裁掉哪个方向、各裁多少，提示模型只在中心带内构图。

    仅在明显裁剪（>10%）时返回指令；方形主图/画廊图（1:1→1024²，同比例几乎不裁）恒返回
    空串，对其零影响。"""
    from app.services.listing_image_compositor import parse_size
    tw, th = parse_size(target_size)
    pw, ph = parse_size(provider_size)
    if not (tw and th and pw and ph):
        return ""
    scale = max(tw / pw, th / ph)          # cover：缩放到刚好盖住目标
    disp_w, disp_h = pw * scale, ph * scale
    crop_v = (disp_h - th) / disp_h if disp_h else 0.0   # 上下各裁比例之和
    crop_h = (disp_w - tw) / disp_w if disp_w else 0.0   # 左右各裁比例之和
    if max(crop_v, crop_h) <= 0.10:
        return ""
    if crop_v >= crop_h:
        each = round(crop_v * 100 / 2)
        keep = 100 - each * 2
        edge = "top and bottom"
        band = "central horizontal band"
        override = ("Any other instruction to place a title band, headline or text across the TOP is OVERRIDDEN "
                    "by this notice — the top strip is cropped away, so the headline must live inside the central "
                    "band, not at the top edge")
    else:
        each = round(crop_h * 100 / 2)
        keep = 100 - each * 2
        edge = "left and right"
        band = "central vertical band"
        override = ("Any other instruction to push the headline or key elements to a side edge is OVERRIDDEN by "
                    "this notice — those side strips are cropped away")
    return (
        f"CANVAS CROP NOTICE (read first, highest priority): you are painting on a {pw}×{ph} canvas, but the "
        f"delivered Amazon module is a {tw}×{th} banner produced by CENTER-CROPPING this canvas. The {edge} "
        f"~{each}% of whatever you generate WILL BE CUT OFF and never seen. Compose the ENTIRE finished banner — "
        f"headline, subline, big number, the complete product and every essential element — inside the {band} (the "
        f"surviving middle ~{keep}% of the image), laid out as one complete, edge-to-edge wide hero banner with clear "
        f"typographic hierarchy and the headline large and dominant. The whole product must sit inside this "
        f"surviving band with margin — no part of the product may extend into the {edge} ~{each}% margins, or it "
        f"will be cut off. The background must extend fully to the far left and right edges of the delivered banner "
        f"so every corner of the final image is completely filled — no blank, white or unfinished corner. Leave only "
        f"plain background extension in the {edge} ~{each}% margins; put nothing important there. Do NOT build a tall "
        f"vertical poster. {override}."
    )


async def _submit_generation(prompt: str, size: str, ref_urls: list[str],
                             slot: str) -> str:
    """提交生图任务，返回 task_id。"""
    if not _apimart_key():
        raise HTTPException(
            400,
            "Apimart 密钥未配置 — 请在「系统配置 → AI 服务」填入有 gpt-image-2 权限的密钥。",
        )
    from app.services.listing_image_compositor import parse_size
    target_w, target_h = parse_size(size)
    ratio = target_w / target_h if target_h else 1.0
    if abs(ratio - 1) < .12:
        provider_size = "1024x1024"
    elif ratio >= 1.9:
        # 供应商 gpt-image-2 能原生渲染宽幅（实测：请求 1464×600 → 出图约 1915×821，
        # 2.33:1）。老逻辑硬套 1536×1024(1.5:1) 再 cover 裁掉上下各 ~19%，正是 A+ 顶部
        # 标题、底部主体被切的根因。宽幅 A+ 直接按目标宽幅下单，出图已接近目标比例，
        # 末端 normalise 仅需 ~2% 微裁。方形主图/画廊(<1.12) 与中度横图分支保持不变。
        provider_size = f"{target_w}x{target_h}"
    elif ratio > 1:
        provider_size = "1536x1024"
    else:
        provider_size = "1024x1536"
    crop_note = _crop_safe_zone_note(size, provider_size)
    if crop_note:
        prompt = f"{crop_note}\n\n{prompt}"
    base_body = {"model": "gpt-image-2", "prompt": prompt, "n": 1, "size": provider_size}
    if ref_urls:
        base_body["image_urls"] = ref_urls[:2]

    # gpt-image's `input_fidelity:"high"` preserves details of the input image (the
    # real product). Apimart may or may not pass it through, so try high-fidelity
    # first and gracefully fall back to a plain request if it is rejected.
    attempts = [{**base_body, "input_fidelity": "high"}, base_body] if ref_urls else [base_body]

    async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=20)) as client:
        logger.info("[generate-image] slot=%s ref_urls=%s fidelity=%s",
                    slot, len(ref_urls), "high" if ref_urls else "n/a")
        resp = None
        for idx, attempt in enumerate(attempts):
            resp = await client.post(
                f"{_apimart_base()}/images/generations",
                headers={"Authorization": f"Bearer {_apimart_key()}", "Content-Type": "application/json"},
                json=attempt,
            )
            if resp.status_code == 200:
                break  # accepted (with or without high fidelity)
            logger.info("[generate-image] attempt %s -> HTTP %s: %s",
                        idx, resp.status_code, resp.text[:160])
            # The plain retry exists only for providers that reject the optional
            # input_fidelity field. Retrying 5xx/rate-limit errors with the same
            # payload just doubles the request time.
            if not (idx == 0 and resp.status_code in {400, 422}):
                break
        if resp is None or resp.status_code != 200:
            raise HTTPException(502, f"图片生成提交失败: {resp.text[:300] if resp is not None else 'no response'}")
        submit_data = resp.json()
        task_id = submit_data.get("data", [{}])[0].get("task_id")
        if not task_id:
            raise HTTPException(502, f"未返回task_id: {resp.text[:300]}")
        return str(task_id)


async def _await_generation(project_id: str, task_id: str, size: str,
                            on_tick=None, deadline_seconds: int = 8 * 60) -> dict:
    """服务端轮询直到任务完成，画布规范化后保存到工作区，返回 {url, technical_qa}。"""
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,160}", task_id):
        raise HTTPException(400, "无效的生图任务 ID")
    loop = asyncio.get_event_loop()
    deadline = loop.time() + deadline_seconds
    tick = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=20)) as client:
        while loop.time() < deadline:
            await asyncio.sleep(5)
            tick += 1
            if on_tick:
                on_tick(tick)
            try:
                poll = await client.get(
                    f"{_apimart_base()}/tasks/{task_id}",
                    headers={"Authorization": f"Bearer {_apimart_key()}"},
                )
            except Exception:
                continue  # transient network failure — keep waiting, task is paid
            # Rate limits and transient provider failures are retryable.
            if poll.status_code == 429 or poll.status_code >= 500:
                continue
            if poll.status_code != 200:
                raise HTTPException(502, f"生图任务查询失败 HTTP {poll.status_code}: {poll.text[:240]}")
            state = _parse_image_task_payload(poll.json())
            if state["status"] == "failed":
                raise HTTPException(502, state.get("error") or "上游生图任务失败")
            if state["status"] != "completed":
                continue
            # Providers may return a different aspect ratio. Normalise locally.
            raw = await _fetch_image_bytes(client, state["provider_url"])
            from app.services.listing_image_compositor import normalise_canvas, technical_quality
            normalised = normalise_canvas(raw, size, mode="cover")
            from app.routers.image_translate import save_bytes_to_workspace
            item = save_bytes_to_workspace(normalised, source="listing", project_id=project_id)
            return {
                "url": item["url"],
                "provider_url": state["provider_url"],
                "technical_qa": technical_quality(normalised, size),
            }
    raise HTTPException(504, "生图任务等待超过 8 分钟，请稍后在历史里重试（任务未重复提交）")


async def generate_image_core(project_id: str, prompt: str, slot: str, size: str,
                              ref_urls: list[str], reference_mode: str = "product",
                              on_tick=None, presence: str = "supporting",
                              product_scale: float = 0.32,
                              include_accessories: bool = False) -> dict:
    """一次完整生成：物化参考 → 按 presence 分级保真前置 → 提交 → 轮询 → 规范化。

    presence="absent" 时调用方传空 ref_urls —— 无参考纯 prompt 生成（成果样片/
    对比/规格面板等产品缺席画面）。
    """
    refs = await _materialise_refs(ref_urls)
    if reference_mode != "template":
        # Direct studio generation has one immutable product truth. Multiple
        # angles/competitor images encourage the model to blend identities.
        refs = refs[:1]
    full_prompt = _fidelity_preamble(prompt, len(refs), reference_mode,
                                     presence=presence, product_scale=product_scale,
                                     include_accessories=include_accessories)
    task_id = await _submit_generation(full_prompt, size, refs, slot)
    return await _await_generation(project_id, task_id, size, on_tick=on_tick)


# ─── 单张分镜渲染（生成 + 质检 + 自动重画一次）────────────────────────────────

def _now_version(image: dict) -> Optional[dict]:
    if not image.get("final_url"):
        return None
    import datetime
    return {
        "url": image["final_url"],
        "base_url": image.get("base_url") or "",
        "render_qa": image.get("render_qa"),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def _load_plan(project_id: str, deliverable: str) -> dict:
    row = project_row(project_id, "creative_sets")
    if not row:
        raise HTTPException(404, "project not found")
    try:
        sets = json.loads(row["creative_sets"] or "{}")
    except Exception:
        sets = {}
    plan = sets.get("aplus" if deliverable == "aplus" else "gallery")
    if not isinstance(plan, dict) or not plan.get("images"):
        raise HTTPException(400, "请先生成整套方案")
    return plan


async def _render_card(project_id: str, plan: dict, index: int, deliverable: str,
                       handle: Optional[JobHandle], *, prompt_override: str = "",
                       progress_base: float = 0.0, progress_span: float = 1.0) -> dict:
    """渲染一张分镜卡：生成 → 复核 → 需要时按质检意见自动重画一次 → 落库。
    返回更新后的 plan。"""
    images = plan.get("images") or []
    if index < 0 or index >= len(images):
        raise HTTPException(400, f"分镜序号越界：{index}")
    image = images[index]
    presence = str(image.get("product_presence")
                   or ("absent" if image.get("show_product") is False else "supporting"))
    product_source = str(image.get("product_source_url") or plan.get("product_source_url") or "")
    if presence == "absent":
        product_source = ""  # 产品缺席画面：无参考纯 prompt 生成
    elif not product_source:
        raise HTTPException(400, f"「{image.get('role') or index + 1}」未找到产品真值素材，请先上传产品图或采集可用主图")
    template_url = str(image.get("template_url") or "")
    # 双参考通路：产品真值 + 竞品版式模板（逆向学习绑定时才有）
    ref_urls = ([product_source, template_url] if product_source and template_url
                else [product_source] if product_source else [])
    reference_mode = "template" if (product_source and template_url) else "product"
    product_scale = float(image.get("product_scale") or 0.32)
    include_accessories = str(image.get("shot_type")) in ("in_box", "white_main")
    size = str(image.get("size") or ("1464x600" if deliverable == "aplus" else "1600x1600"))
    render_prompt = prompt_override.strip() or str(image.get("render_prompt") or "")
    role = str(image.get("role") or f"第 {index + 1} 张")

    def report(message: str, fraction: float) -> None:
        if handle:
            handle.update(stage=f"card-{index}", message=message,
                          progress=progress_base + progress_span * fraction)

    async def review(final_url: str) -> dict:
        from app.services.listing_typography import public_proof
        return await review_render_core(project_id, ReviewRenderReq(
            url=final_url,
            size=size,
            slot=str(image.get("slot") or ""),
            role=role,
            shot_type=str(image.get("shot_type") or ""),
            layout_blueprint=str(image.get("layout_blueprint") or ""),
            eyebrow=str(image.get("eyebrow") or ""),
            headline=str(image.get("headline") or ""),
            callout=str(image.get("callout") or ""),
            supporting_text=str(image.get("supporting_text") or ""),
            subline=str(image.get("subline") or ""),
            big_number=str(image.get("big_number") or ""),
            proof=public_proof(str(image.get("proof") or "")) or "",
            source_url=product_source,
            show_product=presence != "absent",
            product_presence=presence,
            product_fidelity_anchors=(plan.get("product_profile") or {}).get("fidelity_anchors") or [],
        ))

    report(f"{role}：生成中…", 0.05)
    generated = await generate_image_core(
        project_id, render_prompt, str(image.get("slot") or f"slot{index}"), size,
        ref_urls, reference_mode,
        on_tick=lambda t: report(f"{role}：生成中（约 {t * 5} 秒）…", min(0.05 + t * 0.02, 0.5)),
        presence=presence, product_scale=product_scale,
        include_accessories=include_accessories,
    )
    base_url = generated["url"]
    final_url = base_url
    report(f"{role}：成图质检中…", 0.6)
    render_qa = await review(final_url)

    generated_history: list[dict] = []
    auto_retry_count = 0
    retry_guidance = [g for g in (render_qa.get("retry_guidance") or []) if g][:6]
    if not render_qa.get("ready") and retry_guidance:
        auto_retry_count = 1
        report(f"{role}：按质检意见自动重画一次…", 0.65)
        fidelity_note = (
            "Reference image 1 remains the only immutable product truth. "
            "Do not solve a fidelity issue by hiding, cropping away, redesigning or replacing the product."
            if product_source else
            "The physical product must stay absent from this frame."
        )
        retry_prompt = (
            f"{render_prompt}\n\nMANDATORY QA REVISION — this is the single repair attempt. "
            "Keep the original buyer question and art direction, but correct every issue below. "
            f"{fidelity_note}\n- "
            + "\n- ".join(retry_guidance)
        )
        try:
            retried = await generate_image_core(
                project_id, retry_prompt, f"{image.get('slot')}_qa_retry", size,
                ref_urls, reference_mode,
                on_tick=lambda t: report(f"{role}：重画中（约 {t * 5} 秒）…", min(0.65 + t * 0.015, 0.9)),
                presence=presence, product_scale=product_scale,
                include_accessories=include_accessories,
            )
            report(f"{role}：重画质检中…", 0.92)
            retry_qa = await review(retried["url"])
            use_retry = retry_qa.get("ready") or float(retry_qa.get("score") or 0) >= float(render_qa.get("score") or 0)
            import datetime
            stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
            if use_retry:
                generated_history.append({"url": final_url, "base_url": base_url,
                                          "render_qa": render_qa, "created_at": stamp})
                base_url = retried["url"]
                final_url = retried["url"]
                render_qa = retry_qa
            else:
                generated_history.append({"url": retried["url"], "base_url": retried["url"],
                                          "render_qa": retry_qa, "created_at": stamp})
        except HTTPException:
            pass  # 重画失败不吞掉首图 —— 保留第一次结果与其质检结论

    old_version = _now_version(image)
    versions = [
        *(image.get("versions") or []),
        *([old_version] if old_version else []),
        *generated_history,
    ][-8:]
    # 并行 job 防覆盖：落库前重读最新 plan，只合并本卡的结果。
    try:
        latest = _load_plan(project_id, deliverable)
        if len(latest.get("images") or []) == len(images):
            plan = latest
            images = plan["images"]
            image = images[index]
    except HTTPException:
        pass
    images[index] = {
        **image,
        "asset_mode": "generate",
        "show_product": presence != "absent",
        "product_presence": presence,
        "requires_source": False,
        "source_url": "",
        "product_source_url": product_source,
        "template_url": template_url,
        "layout_blueprint": "",
        "base_url": base_url,
        "final_url": final_url,
        "render_qa": render_qa,
        "auto_retry_count": auto_retry_count,
        "last_retry_guidance": retry_guidance,
        "versions": versions,
        "human_reviewed": False,
    }
    plan["images"] = images
    plan["set_qa"] = None
    # Image generation is slow and paid. Persist after each completed card so a
    # later-card failure or restart never loses the work already paid for.
    _persist_shot_plan(project_id, plan, deliverable)
    report(f"{role}：完成", 1.0)
    return plan


async def run_render_image(project_id: str, body: RenderImageReq,
                           handle: Optional[JobHandle] = None) -> dict:
    deliverable = "aplus" if body.deliverable == "aplus" else "gallery"
    plan = _load_plan(project_id, deliverable)
    plan = await _render_card(project_id, plan, body.index, deliverable, handle,
                              prompt_override=body.prompt_override)
    image = plan["images"][body.index]
    return {"plan": plan, "deliverable": deliverable, "index": body.index,
            "ready": bool((image.get("render_qa") or {}).get("ready"))}


async def run_render_set(project_id: str, body: RenderSetReq,
                         handle: Optional[JobHandle] = None) -> dict:
    """整套顺序生成（服务端编排），逐张落库、失败继续，最后自动整套复核。"""
    from .visuals import run_review_image_set
    deliverable = "aplus" if body.deliverable == "aplus" else "gallery"
    plan = _load_plan(project_id, deliverable)
    images = plan.get("images") or []
    targets = [i for i, item in enumerate(images)
               if not (body.only_missing and item.get("final_url"))]
    total = len(targets)
    if handle:
        handle.update(total=total, done_count=0)
    succeeded = 0
    failures: list[str] = []
    for order, index in enumerate(targets):
        try:
            plan = await _render_card(
                project_id, plan, index, deliverable, handle,
                progress_base=order / max(1, total) * 0.9,
                progress_span=0.9 / max(1, total),
            )
            succeeded += 1
        except HTTPException as exc:
            failures.append(f"第 {index + 1} 张：{exc.detail}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"第 {index + 1} 张：{exc}")
        if handle:
            handle.update(done_count=order + 1)

    set_qa = None
    if images and all(item.get("final_url") and (item.get("render_qa") or {}).get("ready")
                      for item in plan.get("images") or []):
        if handle:
            handle.update(stage="set-review", message="整套一致性复核中…", progress=0.92)
        try:
            outcome = await run_review_image_set(project_id, deliverable)
            plan = outcome["plan"]
            set_qa = outcome["set_qa"]
        except Exception as exc:  # noqa: BLE001
            failures.append(f"整套复核失败：{exc}")
    return {
        "plan": plan, "deliverable": deliverable,
        "succeeded": succeeded, "total": total,
        "failures": failures, "set_qa": set_qa,
    }


@router.post("/projects/{project_id}/render-image")
async def render_image_endpoint(project_id: str, body: RenderImageReq,
                                _user: str = Depends(require_user)):
    """单张分镜后台渲染 job。singleton=False：不同分镜可以并行各自的 job。"""
    if not project_row(project_id, "id"):
        raise HTTPException(404)
    return start_job(
        "render_image", project_id, body.model_dump(),
        lambda handle: run_render_image(project_id, body, handle),
        singleton=False,
    )


@router.post("/projects/{project_id}/render-set")
async def render_set_endpoint(project_id: str, body: RenderSetReq,
                              _user: str = Depends(require_user)):
    if not project_row(project_id, "id"):
        raise HTTPException(404)
    return start_job(
        "render_set", project_id, body.model_dump(),
        lambda handle: run_render_set(project_id, body, handle),
    )
