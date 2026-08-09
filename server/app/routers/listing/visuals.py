"""套图美术指导：产品画像 → 整套策划 → 提示词编译 → 质检门槛。

复核（单图/整套）走统一视觉链（stream_vision），不再直连 apimart /messages
（该端点恒 403，老实现导致复核永远"未返回"）。
"""
from __future__ import annotations

import asyncio
import logging
import base64
import io
import json
import re
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.security import require_user

from .ai import _call_ai, _collect_vision, has_vision
from .common import (
    IMAGES_DIR, _approved_copy, _build_product_context, _cached_white_product_source,
    _clean_text, _copy_source, _db, _detect_white_product_source, _fetch_image_bytes,
    _img_datauri_from_path, _img_datauri_from_url, _reference_images, _strip_json,
    project_row, update_project,
)
from .jobs import JobHandle, start_job

logger = logging.getLogger("ivyea.routers.listing.visuals")

router = APIRouter()


class PlanImageSetReq(BaseModel):
    target_count: int = 0           # 0 = let the AI pick (5–8); else exact count
    color_scheme: str = ""
    language: str = "en"            # callout language hint (for typography)
    deliverable: str = "gallery"    # gallery | aplus; both use the same engine
    visual_tone: str = "natural"    # natural | studio | editorial
    brief: str = ""


class ReviewRenderReq(BaseModel):
    url: str
    size: str = "1600x1600"
    slot: str = ""
    role: str = ""
    shot_type: str = ""
    layout_blueprint: str = ""
    eyebrow: str = ""
    headline: str = ""
    subline: str = ""
    big_number: str = ""
    callout: str = ""
    supporting_text: str = ""
    proof: str = ""
    source_url: str = ""
    show_product: bool = True
    product_presence: str = "supporting"   # hero|supporting|environmental|absent
    product_fidelity_anchors: list[str] = []


class ReviewSetReq(BaseModel):
    deliverable: str = "gallery"


# ─── 视觉词汇表 ───────────────────────────────────────────────────────────────

_DEFAULT_ROLES = ["主图", "核心利益", "真实使用", "关键细节", "对比证明", "规格/兼容", "包装清单", "信任收口"]
_APLUS_ROLES = ["品牌首屏", "核心利益", "使用方式", "技术/细节", "信任收口"]

_SHOT_TYPES = {
    "white_main", "hero_feature", "lifestyle", "detail", "comparison",
    "specs", "in_box", "trust", "aplus_banner",
}
_LEGACY_SHOT_TYPE = {"feature": "hero_feature", "scene": "lifestyle"}
_DEFAULT_TYPE_ORDER = [
    "white_main", "hero_feature", "lifestyle", "detail",
    "comparison", "specs", "in_box", "trust",
]
_APLUS_TYPE_ORDER = ["aplus_banner", "hero_feature", "lifestyle", "detail", "trust"]
_TEXT_ZONES = {
    "top-left", "top-center", "top-right", "center-left", "center-right",
    "bottom-left", "bottom-center", "bottom-right",
}
_LAYOUT_STYLES = {"editorial", "minimal", "split", "proof", "grid"}

# ─── 产品出场光谱（对标优秀 3C 套图：产品不必张张当主角）────────────────────
# hero: 产品主体 55-75% 画幅；supporting: 25-40% 与场景/面板共存；
# environmental: ≤15% 融入真实场景讲故事；absent: 产品不出现（成果样片/对比/纯规格）。
_PRESENCES = {"hero", "supporting", "environmental", "absent"}
_PRESENCE_SCALE = {"hero": 0.65, "supporting": 0.32, "environmental": 0.10, "absent": 0.0}
_PRESENCE_DEFAULT_BY_TYPE = {
    "white_main": "hero", "in_box": "hero", "detail": "supporting",
    "hero_feature": "supporting", "lifestyle": "environmental",
    "comparison": "absent", "specs": "supporting", "trust": "environmental",
    "aplus_banner": "supporting",
}

# ─── 版式家族（从 DJI/Anker 级 3C 套图语法提炼；服务端编译成确定性段落）──────
_LAYOUT_FAMILIES = {
    "white_main", "poster_hero", "result_showcase", "split_compare", "spec_grid",
    "scenario_mosaic", "human_context", "in_box_flatlay", "detail_macro", "trust_close",
}
_LAYOUT_DEFAULT_BY_TYPE = {
    "white_main": "white_main", "hero_feature": "poster_hero", "lifestyle": "human_context",
    "detail": "detail_macro", "comparison": "split_compare", "specs": "spec_grid",
    "in_box": "in_box_flatlay", "trust": "trust_close", "aplus_banner": "poster_hero",
}

_LAYOUT_PROMPTS = {
    "white_main": (
        "Pure #FFFFFF seamless Amazon main image. The complete product fills 80-90% of the frame, "
        "perfectly lit studio photography with a soft natural contact shadow. No text, no graphics, no props."
    ),
    "poster_hero": (
        "Premium brand poster layout: a clean title band across the {text_zone} area holds the headline in large "
        "confident type with the subline beneath it in one quiet line{big_number_clause}. The remaining canvas is a "
        "single striking commercial visual for this selling point{presence_clause}. Deliberate negative space, "
        "one clear focal point, editorial balance — designed like a top-tier consumer electronics brand page."
    ),
    "result_showcase": (
        "Outcome-first layout: the canvas is dominated by a beautiful, realistic example of what this product "
        "category delivers ({scene}), presented like professionally captured content{big_number_clause}. Title band "
        "in the {text_zone} area with the headline and subline. The physical product itself stays out of frame — "
        "the result is the hero."
    ),
    "split_compare": (
        "Two-panel comparison layout: the canvas splits into two equal side-by-side panels showing the same subject "
        "under the two contrasted conditions, with a small rounded corner tag on each panel and a subtle divider"
        "{big_number_clause}. Headline and subline sit in a title band across the {text_zone} area. Both panels are "
        "realistic photographic content, identical framing, only the contrasted condition differs."
    ),
    "spec_grid": (
        "Dark premium spec-panel layout: a 2x2 (or 1+2) grid of rounded rectangular panels on a deep neutral "
        "background. Each panel pairs one oversized statistic in bold type with a short caption; one panel may hold "
        "a small angled product render{big_number_clause}. Headline across the {text_zone} area. Clean, technical, "
        "high-contrast — like a flagship electronics brand's spec sheet."
    ),
    "scenario_mosaic": (
        "Use-case mosaic layout: one wide primary panel plus two smaller panels beneath it, each a realistic "
        "photograph of a different real usage scenario ({scene}), each with a tiny corner label chip. Headline and "
        "subline in a title band across the {text_zone} area{presence_clause}."
    ),
    "human_context": (
        "Authentic in-use photograph: a real person naturally using the product in {scene}. The environment and the "
        "human moment dominate the frame{presence_clause}; believable posture, natural light, honest scale. Title "
        "band with headline and subline in the {text_zone} area kept visually calm."
    ),
    "in_box_flatlay": (
        "What's-in-the-box flat lay: every included item arranged neatly on a clean seamless background with even "
        "spacing and soft shadows, the main unit largest{presence_clause}. Small caption under each item is allowed "
        "only if listed in the copy contract. Headline across the {text_zone} area."
    ),
    "detail_macro": (
        "Macro craftsmanship shot: an extreme close crop of the real product detail that proves this selling point, "
        "shallow depth of field, tactile material realism{presence_clause}{big_number_clause}. Headline and subline "
        "in the {text_zone} area over calm negative space."
    ),
    "trust_close": (
        "Trust closing layout: a warm, reassuring real-life scene ({scene}) that summarises the ownership "
        "experience{presence_clause}, with a restrained row of small outlined badges only for claims present in the "
        "copy contract. Headline and subline in the {text_zone} area."
    ),
}

# ─── 内置精品套图叙事库（借鉴优秀 3C 卖家；采不到竞品套图时的骨架）───────────
# 每项: (shot_type, presence, layout, 角色)
_GALLERY_NARRATIVES = {
    "rigid_device": [
        ("white_main", "hero", "white_main", "主图"),
        ("hero_feature", "supporting", "poster_hero", "核心利益"),
        ("comparison", "absent", "split_compare", "成果对比"),
        ("specs", "supporting", "spec_grid", "规格一览"),
        ("lifestyle", "environmental", "human_context", "真实使用"),
        ("detail", "supporting", "detail_macro", "关键细节"),
        ("trust", "absent", "scenario_mosaic", "多场景信任"),
        ("in_box", "hero", "in_box_flatlay", "包装清单"),
    ],
    "soft_goods": [
        ("white_main", "hero", "white_main", "主图"),
        ("hero_feature", "supporting", "poster_hero", "核心利益"),
        ("detail", "supporting", "detail_macro", "材质细节"),
        ("lifestyle", "environmental", "human_context", "真实使用"),
        ("comparison", "absent", "split_compare", "效果对比"),
        ("specs", "supporting", "spec_grid", "规格尺寸"),
        ("trust", "environmental", "trust_close", "信任收口"),
        ("in_box", "hero", "in_box_flatlay", "套装内容"),
    ],
    "spatial_gear": [
        ("white_main", "hero", "white_main", "主图"),
        ("lifestyle", "environmental", "human_context", "真实场景"),
        ("hero_feature", "supporting", "poster_hero", "核心利益"),
        ("specs", "absent", "spec_grid", "规格容量"),
        ("detail", "supporting", "detail_macro", "结构细节"),
        ("comparison", "absent", "split_compare", "环境对比"),
        ("trust", "environmental", "scenario_mosaic", "多场景信任"),
        ("in_box", "hero", "in_box_flatlay", "包装清单"),
    ],
}
_GALLERY_NARRATIVES["category_specific"] = _GALLERY_NARRATIVES["rigid_device"]
_APLUS_NARRATIVE = [
    ("aplus_banner", "supporting", "poster_hero", "品牌首屏"),
    ("hero_feature", "absent", "result_showcase", "核心利益"),
    ("lifestyle", "environmental", "human_context", "使用方式"),
    ("detail", "supporting", "detail_macro", "技术/细节"),
    ("trust", "absent", "scenario_mosaic", "信任收口"),
]

_LAYOUT_BLUEPRINTS = {
    "white_bundle", "media_proof_split", "connectivity_diagram",
    "environmental_proof", "coverage_diagram", "speed_comparison",
    "day_night_split", "use_case_mosaic",
}
_DEFAULT_BLUEPRINT_ORDER = [
    "white_bundle", "media_proof_split", "connectivity_diagram",
    "environmental_proof", "coverage_diagram", "speed_comparison",
    "day_night_split", "use_case_mosaic",
]
_BLUEPRINT_PANEL_COUNTS = {
    "white_bundle": 0, "media_proof_split": 2, "connectivity_diagram": 1,
    "environmental_proof": 1, "coverage_diagram": 1,
    "speed_comparison": 1, "day_night_split": 2, "use_case_mosaic": 6,
}
# The studio renders every planned card through the image model.  Keeping this
# set empty is important: review_render must compare generated main/in-box
# images with the product truth instead of treating them as untouched pixels.
_SOURCE_LOCKED_TYPES: set[str] = set()
_INTERNAL_PUBLIC_COPY_RE = re.compile(
    r"\b(approved copy|approved title|product facts?|claim(?:s)?|image should|"
    r"do not fabricate|source material|evidence|supported by|supports? (?:the|this))\b",
    re.I,
)
_HYPE_PROMPT_RE = re.compile(
    r"\b(neon|cyber|sci[- ]?fi|light trails?|floating (?:glass )?panels?|"
    r"hologra(?:m|phic)|futuristic platform|epic cinematic|hyperreal fantasy|"
    r"10,?000 commercial|national geographic style)\b",
    re.I,
)

# Reproduce the real product instead of re-imagining it. The reference photos are
# sent to the image model as actual image inputs at generation time, so URLs in the
# text prompt are useless noise and are explicitly forbidden here.
_FIDELITY_RULE = (
    "- PRODUCT FIDELITY: The real product photos are supplied to the image model directly as image inputs. "
    "Reproduce the product EXACTLY as shown there — identical shape, proportions, colors, materials, logos and "
    "any text printed on the product. Do NOT redesign, recolor, relabel or restyle the product itself; only "
    "change the background, scene, props and lighting. Never put a URL inside the prompt."
)


# ─── 提示词整地 ───────────────────────────────────────────────────────────────

def _ground_render_prompt(value: str) -> str:
    """Remove known prompt habits that systematically create synthetic ad art."""
    text = str(value or "").strip()
    replacements = {
        r"\bno neon(?: light trails?)?\b": "avoid stylized lighting",
        r"\bno floating (?:glass )?panels?\b": "avoid decorative overlays",
        r"\bneon light trails?\b": "subtle practical light",
        r"\bneon\b": "stylized colored lighting",
        r"\blight trails?\b": "natural motion blur",
        r"\bcyber(?:punk)?\b": "contemporary",
        r"\bsci[- ]?fi\b": "contemporary",
        r"\bfloating (?:glass )?panels?\b": "clean negative space",
        r"\bhologra(?:m|phic)\w*\b": "subtle graphic depth",
        r"\bfuturistic platform\b": "simple real surface",
        r"\bepic cinematic\b": "natural editorial",
        r"\bhyperreal fantasy\b": "realistic photography",
        r"\b(?:a )?\$?10,?000 commercial photoshoot\b": "restrained ecommerce photography",
        r"\bnational geographic style\b": "documentary photography",
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text, flags=re.I)
    return re.sub(r"\s{2,}", " ", text).strip()


def _remove_legacy_textless_directions(value: str) -> str:
    """Remove obsolete 'generate a blank plate' instructions before compiling
    an exact-text final-artwork prompt.

    Existing projects and the deterministic fallback can contain these phrases.
    Keeping them beside the new copy contract gives the image model mutually
    exclusive instructions and is a major source of missing or garbled type.
    """
    text = str(value or "")
    patterns = (
        r"[^.\n]*(?:typography|copy|text)\s+(?:is|will be)\s+(?:added|typeset)\s+(?:later|separately)[^.\n]*[.\n]?",
        r"[^.\n]*(?:reserve|keep)\s+[^.\n]*(?:for later typography|for separately typeset copy)[^.\n]*[.\n]?",
        r"[^.\n]*render\s+no\s+(?:added\s+marketing\s+)?(?:copy|text|words|letters)[^.\n]*[.\n]?",
        r"[^.\n]*\bno\s+(?:added\s+)?(?:text|words|letters|marketing copy)\b[^.\n]*[.\n]?",
        r"[^.\n]*no\s+words,?\s+no\s+icons[^.\n]*[.\n]?",
    )
    for pattern in patterns:
        text = re.sub(pattern, " ", text, flags=re.I)
    return re.sub(r"\s{2,}", " ", text).strip()


def _public_text(value, limit: int) -> Optional[str]:
    """Keep only concise shopper-facing copy; internal chain-of-thought is never art."""
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text or _INTERNAL_PUBLIC_COPY_RE.search(text):
        return None
    return text[:limit].rstrip()


def _public_proof(value) -> Optional[str]:
    from app.services.listing_typography import public_proof
    return public_proof(str(value or "")) or None


def _clamped_float(value, default: float, low: float, high: float) -> float:
    try:
        return max(low, min(float(value), high))
    except (TypeError, ValueError):
        return default


def _readable_accent(value: str) -> str:
    text = str(value or "#67C95B").strip().lstrip("#")
    if len(text) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", text):
        return "#67C95B"
    rgb = [int(text[index:index + 2], 16) for index in (0, 2, 4)]
    peak = max(rgb)
    if peak < 150:
        factor = 185 / max(1, peak)
        rgb = [min(230, round(channel * factor)) for channel in rgb]
    return "#" + "".join(f"{channel:02X}" for channel in rgb)


_DEFAULT_SET_PALETTES = {
    "soft_goods": ["#F4F0E8", "#D8C7B5", "#8A6F56", "#4E5B4B", "#282622"],
    "spatial_gear": ["#E9E3D6", "#A7AD8C", "#667151", "#B97645", "#252A23"],
    "rigid_device": ["#EEF1F3", "#C8D0D6", "#657581", "#6E8D72", "#22292E"],
    "category_specific": ["#F2EFE9", "#D7D0C5", "#8A7967", "#66736B", "#292A28"],
}


def _normalise_set_style(style: dict, product_profile: dict) -> dict:
    """Create one concrete, reusable colour grammar for the whole set."""
    source = dict(style) if isinstance(style, dict) else {}
    behaviour = _clean_text(product_profile.get("object_behavior")) or "category_specific"
    requested = " ".join(str(source.get(key) or "") for key in ("palette", "direction"))
    profile_palette = product_profile.get("supporting_palette") or []
    requested += " " + " ".join(str(value) for value in profile_palette)
    colours: list[str] = []
    for value in re.findall(r"#[0-9a-fA-F]{6}\b", requested):
        upper = value.upper()
        if upper not in colours:
            colours.append(upper)
    defaults = _DEFAULT_SET_PALETTES.get(behaviour, _DEFAULT_SET_PALETTES["category_specific"])
    for value in defaults:
        if len(colours) >= 5:
            break
        if value not in colours:
            colours.append(value)
    colours = colours[:5]
    product_colours = [
        _clean_text(value) for value in (product_profile.get("product_colours") or [])
        if _clean_text(value)
    ][:5]
    source.update({
        "direction": _clean_text(source.get("direction")) or (
            f"Product-led {behaviour.replace('_', ' ')} commercial photography with realistic materials"
        ),
        "palette": (
            f"background {colours[0]}; surface {colours[1]}; supporting tone {colours[2]}; "
            f"brand accent {colours[3]}; deep neutral {colours[4]}"
        ),
        "palette_hex": colours,
        "product_colours": product_colours,
        "lighting": _clean_text(source.get("lighting")) or "soft directional key light with consistent neutral white balance",
        "materials": _clean_text(source.get("materials")) or "real category-appropriate materials with restrained supporting props",
        "type_system": _clean_text(source.get("type_system")) or "clean high-contrast ecommerce typography",
        "accent_color": _readable_accent(source.get("accent_color") or colours[3]),
    })
    return source


def _replace_prompt_section(prompt: str, name: str, content: str) -> str:
    """Replace a canonical prompt section so repeated saves never grow prompts."""
    pattern = rf"\s*\[{re.escape(name)}\].*?\[/{re.escape(name)}\]\s*"
    base = re.sub(pattern, " ", str(prompt or ""), flags=re.I | re.S).strip()
    return _ground_render_prompt(f"{base}\n[{name}] {content.strip()} [/{name}]")


def _set_colour_prompt(style: dict, shot_type: str) -> str:
    palette = str(style.get("palette") or "")
    product_colours = ", ".join(style.get("product_colours") or []) or "the exact colours visible in the reference"
    if shot_type == "white_main":
        application = (
            "Amazon main-image exception: use a pure #FFFFFF seamless background with neutral light and a natural soft shadow. "
            "Do not tint the white background or recolour the product."
        )
    else:
        application = (
            "Use these same colours naturally across the background, supporting surface, restrained props, ambient grade and integrated typography. "
            "Not every colour must appear in every frame; preserve the same white balance and visual family without making the set look templated."
        )
    return (
        f"One locked colour system for the entire image set: {palette}. Lighting: {style.get('lighting')}. "
        f"Material direction: {style.get('materials')}. Product colours are immutable ({product_colours}) and are not replaced by the set palette. "
        f"{application}"
    )


# ─── Render Prompt 编译器 ─────────────────────────────────────────────────────
# LLM 只产出紧凑的结构字段（presence/layout/headline/…），最终生图提示词由这里
# 确定性编译。好处：① 策划输出体积降一个量级，截断绝迹；② 版式语言/保真规则/
# 文案合同集中可控，不再依赖模型每次自由发挥出 250 词长文。

def _presence_clause(presence: str, product_scale: float) -> str:
    if presence == "hero":
        return (
            "; the exact reference product is the dominant subject, shown complete and "
            "entirely within the frame — the whole product is visible and never cropped by "
            f"the canvas edges — filling roughly {int(product_scale * 100)}% of the frame"
        )
    if presence == "supporting":
        return (
            "; the exact reference product appears clearly but shares the stage, shown "
            "complete and entirely within the frame — the whole product is visible and never "
            f"cropped by the canvas edges — occupying roughly {int(product_scale * 100)}% of the frame"
        )
    if presence == "environmental":
        return (
            "; the exact reference product appears small and natural within the scene, "
            f"around {max(5, int(product_scale * 100))}% of the frame — the story is the environment, not the device"
        )
    return ""  # absent：版式模板自身已声明产品不出现


def _is_wide_banner(image: dict) -> bool:
    """成图是否为明显宽幅（宽高比≥1.9，如高级 A+ 1464×600）。方形画廊/主图恒为 False。"""
    try:
        width, height = (int(part) for part in str(image.get("size") or "").lower().split("x")[:2])
    except (ValueError, TypeError):
        return False
    return bool(height) and width / height >= 1.9


def _wide_banner_clause(image: dict) -> str:
    """宽幅 A+ 横幅（如高级 A+ 1464×600）的横向编排指令。

    版式模板（poster_hero 等）都是照 1600×1600 近方形画布写的——"顶部标题带 + 下方主视觉"。
    放到 600px 高的宽横幅上没有纵向空间，模型只能塌缩成一条"贴上去"的字幕条，还常把主标题
    整条丢掉（画廊图效果好正是因为方形画布放得下完整版式）。此子句仅在明显宽幅（宽高比≥1.9）
    时追加，方形画廊图完全不受影响。"""
    if not _is_wide_banner(image):
        return ""
    return (
        "WIDE BANNER FORMAT: this is a short, wide A+ hero module, not a square image and not a top caption bar. "
        "Compose it horizontally like a flagship consumer-brand hero banner — the product and its scene occupy one "
        "side of the frame while a fully integrated editorial type block fills the opposing negative space: the "
        "headline large and dominant, the subline one calm line beneath it, the big number oversized as a graphic "
        "anchor when present. The typography is generously sized, vertically centred and part of the composition — "
        "never a thin pasted strip. The photographic background fills the whole canvas edge-to-edge so every corner "
        "is completely covered — no blank, white or unfinished corner anywhere. Keep the headline, the complete "
        "product and every essential element a small safe margin clear of all four edges (a thin outer strip may be "
        "trimmed on delivery), never touching or bleeding past the top, bottom or side edges. Render the complete "
        "headline and subline; do not drop the headline."
    )


def _compile_layout_section(image: dict) -> str:
    layout = str(image.get("layout") or "")
    if layout not in _LAYOUT_FAMILIES:
        layout = _LAYOUT_DEFAULT_BY_TYPE.get(str(image.get("shot_type") or ""), "poster_hero")
    presence = str(image.get("product_presence") or "supporting")
    scale = _clamped_float(image.get("product_scale"), _PRESENCE_SCALE.get(presence, 0.32), 0.04, 0.9)
    big_number = _clean_text(image.get("big_number"))
    template = _LAYOUT_PROMPTS.get(layout, _LAYOUT_PROMPTS["poster_hero"])
    body = template.format(
        text_zone=str(image.get("text_zone") or "top-center"),
        scene=_clean_text(image.get("scene")) or "a realistic category-appropriate setting",
        presence_clause=_presence_clause(presence, scale),
        big_number_clause=(
            f", anchored by the oversized statistic \"{big_number}\" as a bold graphic element"
            if big_number else ""
        ),
    )
    extras = []
    concept = _clean_text(image.get("visual_concept"))
    if concept:
        extras.append(f"Creative concept: {concept}.")
    camera = _clean_text(image.get("camera_direction"))
    if camera:
        extras.append(f"Camera and composition: {camera}.")
    banner = _wide_banner_clause(image)
    if banner:
        extras.append(banner)
    return f"Layout family: {layout}. {body} " + " ".join(extras)


def _compile_presence_section(image: dict, product_profile: dict, product_lock: str) -> str:
    presence = str(image.get("product_presence") or "supporting")
    anchors = ", ".join(
        _clean_text(v) for v in (product_profile.get("fidelity_anchors") or []) if _clean_text(v)
    ) or "silhouette, proportions, colour, material, visible construction, markings"
    behaviour = _clean_text(product_profile.get("object_behavior")) or "category-specific physical behaviour"
    lock = f"{_clean_text(product_lock)}. " if _clean_text(product_lock) else ""
    if presence == "absent":
        return (
            "The physical product does NOT appear in this image. Do not sneak the device, its packaging or its "
            "accessories into the frame. This image shows the outcome, comparison, scenario or information the "
            "buyer cares about — a standard practice on premium listings. Keep every visual physically plausible."
        )
    accessory_rule = (
        "Include every item that belongs to the purchased set, arranged deliberately."
        if str(image.get("shot_type")) == "in_box" else
        "Show ONLY the primary product itself. Do NOT carry packaging, memory cards, cables, manuals or any "
        "loose accessory items from the reference photo into this scene — the reference may be a bundle shot; "
        "everything except the main unit is reference noise here."
    )
    treatment = _clean_text(image.get("product_treatment"))
    treatment_rule = f"Product treatment: {treatment}. " if treatment else ""
    return (
        f"REFERENCE IMAGE 1 is the only immutable product truth. {lock}"
        f"Preserve exactly: {anchors}. Physical behaviour: {behaviour}. {treatment_rule}{accessory_rule} "
        "The complete product must sit fully inside the frame with comfortable margin on every side — show the "
        "whole product, never crop, cut off or let any part of it bleed past the canvas edges, even on wide or "
        "short banner canvases. Change only the environment, camera, composition, lighting and supporting design. "
        "Do not redesign, recolour, relabel, simplify or add parts. Existing logos, labels and printed markings on "
        "the product must remain unchanged."
    )


def _compile_copy_section(image: dict, plan_style: dict) -> str:
    if not image.get("text_on_image"):
        return (
            "This artwork intentionally contains no added marketing copy. Do not render headlines, captions, "
            "letters, numbers, badges, icons, diagrams, app UI or watermarks."
        )
    exact_copy = {
        key: value for key, value in (
            ("eyebrow", image.get("eyebrow")), ("headline", image.get("headline")),
            ("subline", image.get("subline")), ("callout", image.get("callout")),
            ("supporting_text", image.get("supporting_text")),
            ("big_number", image.get("big_number")), ("proof", image.get("proof")),
        ) if _clean_text(value)
    }
    contract = json.dumps(exact_copy, ensure_ascii=False)
    return (
        f"This is final pixel artwork. Render every string in this JSON exactly once and character-for-character: "
        f"{contract}. Do not translate, paraphrase, abbreviate, add, omit or repeat any character. "
        f"Type system: {plan_style.get('type_system')}. Hierarchy: headline dominant, subline one quiet line "
        "beneath it, big_number oversized as a graphic anchor when present, other strings small. Keep all type "
        "inside an 8% safe margin, high contrast, unobstructed, readable at mobile thumbnail size. The typography "
        "is part of the designed composition, never a pasted caption."
    )


def _compile_render_prompt(image: dict, plan_style: dict, product_profile: dict,
                           plan: dict, deliverable: str) -> str:
    """从结构字段确定性编译最终生图提示词。

    用户手改过的 render_prompt 会被保留为开头的自定义描述（剥掉旧 [SECTION] 后的
    余文），其余全部按当前字段重编译——改字段永远生效，改提示词也不丢。
    """
    existing = str(image.get("render_prompt") or "")
    base = existing
    for name in ("SHOT DESIGN", "PRODUCT IDENTITY LOCK", "PRODUCT PRESENCE", "SET COLOR SYSTEM",
                 "USER CREATIVE DIRECTION", "FINAL ARTWORK COPY", "OUTPUT SAFETY"):
        base = re.sub(rf"\s*\[{re.escape(name)}\].*?\[/{re.escape(name)}\]\s*", " ", base, flags=re.I | re.S)
    base = _ground_render_prompt(base)
    if not base:
        size = str(image.get("size") or ("1464x600" if deliverable == "aplus" else "1600x1600"))
        base = (
            f"Create one finished, commercially polished Amazon {'A+ module' if deliverable == 'aplus' else 'listing image'} "
            f"on a {size} canvas. Photorealistic materials, believable light and physics, restrained premium design."
        )
    creative_brief = _clean_text(plan.get("creative_brief"))
    language = _clean_text(plan.get("language")) or "en"
    prompt = base
    prompt = _replace_prompt_section(prompt, "SHOT DESIGN", _compile_layout_section(image))
    prompt = _replace_prompt_section(
        prompt, "PRODUCT PRESENCE",
        _compile_presence_section(image, product_profile, str(plan.get("product_lock") or "")),
    )
    prompt = _replace_prompt_section(
        prompt, "SET COLOR SYSTEM", _set_colour_prompt(plan_style, str(image.get("shot_type") or "")),
    )
    prompt = _replace_prompt_section(
        prompt, "USER CREATIVE DIRECTION",
        (
            f"Highest-priority creative requirement after product identity, factual accuracy and marketplace rules: "
            f"{creative_brief}. Follow it for audience, mood, scene, emphasis, exclusions and design character. "
            if creative_brief else
            "No additional manual creative requirement was supplied; follow the product-specific set direction. "
        ) + f"The requested language for added artwork copy is {language}.",
    )
    if image.get("text_on_image"):
        prompt = _remove_legacy_textless_directions(prompt)
    prompt = _replace_prompt_section(prompt, "FINAL ARTWORK COPY", _compile_copy_section(image, plan_style))
    prompt = _replace_prompt_section(
        prompt, "OUTPUT SAFETY",
        "Do not invent additional copy, claims, certifications, logos, labels, icons, badges, diagrams, app UI or "
        "watermarks. The exact public copy in FINAL ARTWORK COPY is the only added text allowed. Product labels "
        "already visible on the reference product are part of the product and must remain unchanged.",
    )
    return prompt


def _infer_layout_blueprint(item: dict, index: int, shot_type: str) -> str:
    explicit = str(item.get("layout_blueprint") or "").strip()
    if explicit in _LAYOUT_BLUEPRINTS:
        return explicit
    facts = " ".join(str(item.get(key) or "") for key in (
        "headline", "supporting_text", "proof", "selling_point", "buyer_question", "evidence", "scene",
    )).lower()
    if shot_type in {"white_main", "in_box"}:
        return "white_bundle"
    if re.search(r"\b(wi[ -]?fi|bluetooth|app control|remote app)\b", facts) and not re.search(r"\b(no app|no wi[ -]?fi)\b", facts):
        return "connectivity_diagram"
    if re.search(r"\b(night vision|low[- ]?glow|infrared|\d+\s*nm|day (?:or|and) night)\b", facts):
        return "day_night_split"
    if re.search(r"\b(0\.\d+\s*s|trigger speed|burst|fast[- ]?moving|response time)\b", facts):
        return "speed_comparison"
    if re.search(r"\b(ip\d{2}|waterproof|weatherproof|rain|snow|dust|mud|temperature)\b", facts):
        return "environmental_proof"
    if re.search(r"\b(\d+\s*°(?!\s*[fc])|\d+\s*degree|detection angle|pir|field of view|coverage)\b", facts):
        return "coverage_diagram"
    if re.search(r"\b(\d+\s*(?:mp|k)|photo|video|resolution|image quality|detail)\b", facts):
        return "media_proof_split"
    if re.search(r"\b(use cases?|scenarios?|wildlife|hunting|farm|camping|home security|garden|monitoring)\b", facts):
        return "use_case_mosaic"
    return {
        "hero_feature": "media_proof_split", "comparison": "speed_comparison",
        "specs": "coverage_diagram", "lifestyle": "use_case_mosaic",
        "detail": "environmental_proof", "trust": "environmental_proof",
    }.get(shot_type, _DEFAULT_BLUEPRINT_ORDER[min(index, len(_DEFAULT_BLUEPRINT_ORDER) - 1)])


# ─── 产品视觉画像 ─────────────────────────────────────────────────────────────

def _fallback_product_visual_profile(product_context: str) -> dict:
    """Build a category-aware visual identity when the vision call is unavailable.

    The profile describes how the physical product behaves in an image.  It is
    deliberately not a gallery template: a soft towel, an occupied tent and a
    rigid camera require different scale cues, contact physics and useful shots.
    """
    text = str(product_context or "").lower()
    if re.search(r"\b(towel|bath towel|hand towel|washcloth|microfiber cloth)\b|毛巾|浴巾", text):
        return {
            "category_family": "towel / soft home textile",
            "object_behavior": "soft_goods",
            "form_and_scale": "Flexible rectangular textile shown folded, draped or naturally handled; thickness and edge finish must stay credible.",
            "materials_and_finish": ["visible weave or pile", "soft compressible volume", "real stitched edges"],
            "product_colours": ["preserve the exact textile colour from the reference"],
            "supporting_palette": ["#F4F0E8", "#D8C7B5", "#8A6F56", "#4E5B4B", "#282622"],
            "fidelity_anchors": ["exact colour", "weave or pile character", "border and stitching", "set quantity", "true proportions"],
            "natural_interactions": ["folding", "draping", "drying", "gentle hand contact", "stacked storage"],
            "scene_families": ["quiet bathroom", "linen shelf", "spa-like but lived-in home", "pool or travel bag when supported"],
            "visual_opportunities": ["tactile macro", "layered folds", "absorbency context without fake test data", "colour-coordinated stack", "human scale cue"],
            "avoid": ["rigid floating slab", "impossible folds", "plastic fibres", "invented embroidery", "luxury marble cliché"],
        }
    if re.search(r"\b(tent|camping tent|backpacking tent|canopy|shelter)\b|帐篷|天幕", text):
        return {
            "category_family": "tent / spatial outdoor gear",
            "object_behavior": "spatial_gear",
            "form_and_scale": "Large occupiable shelter whose panel geometry, poles, doors, windows, guy lines and footprint define product identity.",
            "materials_and_finish": ["tensioned technical fabric", "credible seams", "real poles and stakes", "ground contact"],
            "product_colours": ["preserve every fabric and pole colour block from the reference"],
            "supporting_palette": ["#E9E3D6", "#A7AD8C", "#667151", "#B97645", "#252A23"],
            "fidelity_anchors": ["exact silhouette", "panel and door count", "pole architecture", "window placement", "colour blocking", "capacity and packed items only when supported"],
            "natural_interactions": ["pitching", "entering", "resting inside", "ventilating", "packing"],
            "scene_families": ["real campsite", "forest clearing", "open grassland", "car-camping pitch", "interior sleeping setup"],
            "visual_opportunities": ["environmental hero", "human scale", "interior spatial view", "setup sequence", "weather readiness", "packed-versus-pitched story"],
            "avoid": ["changing the tent architecture", "impossible interior volume", "missing guy lines", "floating shelter", "extreme fantasy landscape"],
        }
    if re.search(r"\b(camera|trail cam|action camera|security camera)\b|相机|摄像机|监控", text):
        return {
            "category_family": "camera / compact rigid device",
            "object_behavior": "rigid_device",
            "form_and_scale": "Compact precision device; controls, lenses, screens, ports and mounting orientation are identity-critical.",
            "materials_and_finish": ["real glass optics", "controlled matte surfaces", "precise seams", "credible screen reflections"],
            "product_colours": ["preserve the exact body, lens, control and logo colours from the reference"],
            "supporting_palette": ["#EEF1F3", "#C8D0D6", "#657581", "#6E8D72", "#22292E"],
            "fidelity_anchors": ["lens count and position", "body silhouette", "buttons and ports", "screen and logo placement", "mounting hardware", "included quantity"],
            "natural_interactions": ["mounting", "handheld operation", "field use", "screen review", "packing"],
            "scene_families": ["category-appropriate field location", "restrained technical studio", "real use environment", "gear preparation surface"],
            "visual_opportunities": ["precision hero", "operational close-up", "credible use scale", "captured-result context", "day/night context when supported"],
            "avoid": ["extra lenses", "invented ports", "fake app UI", "oversized device", "neon technology effects"],
        }
    return {
        "category_family": "general consumer product",
        "object_behavior": "category_specific",
        "form_and_scale": "Infer the real product's rigidity, scale, contact points and normal orientation from the white-background source.",
        "materials_and_finish": ["physically accurate material", "credible surface response", "real construction details"],
        "product_colours": ["preserve every exact product colour from the reference"],
        "supporting_palette": ["#F2EFE9", "#D7D0C5", "#8A7967", "#66736B", "#292A28"],
        "fidelity_anchors": ["silhouette", "proportions", "colour", "material", "visible controls or construction", "included quantity"],
        "natural_interactions": ["normal handling", "primary real-world use", "storage or setup"],
        "scene_families": ["real home, workplace or outdoor setting appropriate to the category", "restrained studio"],
        "visual_opportunities": ["material detail", "human scale", "primary use", "benefit-led hero", "configuration or storage"],
        "avoid": ["generic pedestal ad", "impossible physics", "invented features", "decorative AI effects", "unrelated luxury props"],
    }


def _normalise_product_visual_profile(value: dict, fallback: dict) -> dict:
    if not isinstance(value, dict):
        return fallback
    result = dict(fallback)
    for key in ("category_family", "object_behavior", "form_and_scale"):
        cleaned = _clean_text(value.get(key))
        if cleaned:
            result[key] = cleaned[:500]
    for key in (
        "materials_and_finish", "product_colours", "supporting_palette",
        "fidelity_anchors", "natural_interactions", "scene_families", "visual_opportunities", "avoid",
    ):
        items = value.get(key)
        if isinstance(items, list):
            cleaned = [_clean_text(item)[:220] for item in items if _clean_text(item)]
            if cleaned:
                result[key] = cleaned[:10]
    return result


async def _analyze_product_visual_identity(
    scrape_data: dict, product_context: str, white_source: str,
) -> dict:
    """Stage 1 of visual planning: understand this product before designing shots."""
    fallback = _fallback_product_visual_profile(product_context)
    if not has_vision():
        return fallback

    candidates: list[str] = []
    for value in [white_source, *_reference_images(scrape_data)[:4]]:
        value = str(value or "").strip()
        if value and value not in candidates:
            candidates.append(value)
    images: list[str] = []
    # User uploads are deliberate product truth and take priority even when the
    # background is not pure white. Feed them to visual identity analysis before
    # scraped gallery images.
    for path_str in (scrape_data.get("uploaded_images") or [])[:2]:
        data_uri = _img_datauri_from_path(str(path_str))
        if data_uri and data_uri not in images:
            images.append(data_uri)
    for value in candidates[:4]:
        data_uri: Optional[str] = None
        if value.startswith("data:"):
            data_uri = value
        elif value.startswith("/api/listing/images/"):
            rel = value.split("/api/listing/images/", 1)[1].split("?", 1)[0]
            parts = [part for part in rel.split("/") if part and part not in {".", ".."}]
            if len(parts) >= 2:
                data_uri = _img_datauri_from_path(str(IMAGES_DIR / parts[0] / parts[-1]))
        else:
            data_uri = await _img_datauri_from_url(value)
        if data_uri and data_uri not in images:
            images.append(data_uri)
    if not images:
        return fallback

    prompt = f"""You are stage 1 of an ecommerce visual-director pipeline. Analyze the PHYSICAL PRODUCT before any gallery is designed.
Image 1 is the white-background product truth when available. Other images are evidence of use and features only.

PRODUCT FACTS:
{product_context[:9000]}

Return JSON only:
{{"category_family":"specific product family","object_behavior":"soft_goods|rigid_device|spatial_gear|wearable|furniture|consumable|other",
"form_and_scale":"physical form, orientation, flexibility and real scale behaviour",
"materials_and_finish":["visible material/finish"],
"product_colours":["exact product colours and colour blocking visible in the references"],
"supporting_palette":["five product-compatible #RRGGBB colours for background, surface, supporting tone, accent and deep neutral"],
"fidelity_anchors":["details that must remain exactly unchanged"],
"natural_interactions":["physically normal ways people/environment contact the product"],
"scene_families":["real settings that reveal value"],
"visual_opportunities":["category-specific photographic ideas, not fixed layouts"],
"avoid":["category-specific visual mistakes and AI failure modes"]}}

Be concrete. A towel must be treated as flexible textile, a tent as occupiable architecture, and a camera as a rigid precision device.
Do not design image slots, do not copy the collected gallery's composition, and do not infer unsupported specifications."""
    try:
        parsed = _strip_json(await _collect_vision(prompt, images)) or {}
    except Exception:
        parsed = {}
    return _normalise_product_visual_profile(parsed, fallback)


_VISION_BATCH = 4


async def _analyze_reference_templates(scrape_data: dict) -> list[dict]:
    """把采集到的竞品套图逐张逆向成可复用的版式语法（借鉴优秀卖家，通道 2）。

    只提取可迁移的销售任务与版式结构（presence/layout/视觉结构），从不复制像素、
    品牌与文案。产出喂给策划器作参考语法，并可为对应卡绑定 template_url 走
    双参考生图（REFERENCE 2 = 仅版式模板）。
    """
    refs = _reference_images(scrape_data)[:8]
    if len(refs) < 4 or not has_vision():
        return []
    images: list[tuple[int, str]] = []
    for index, url in enumerate(refs, 1):
        data_uri = await _img_datauri_from_url(url)
        if data_uri:
            images.append((index, data_uri))
    story: list[dict] = []
    allowed_types = _SHOT_TYPES - {"aplus_banner"}
    for offset in range(0, len(images), _VISION_BATCH):
        batch = images[offset:offset + _VISION_BATCH]
        indices = [item[0] for item in batch]
        prompt = f"""你是电商视觉总监。下面是某优秀亚马逊卖家套图中的原始第 {indices[0]}–{indices[-1]} 张。
逐张反推设计逻辑，不评价产品好坏。只返回 JSON：
{{"templates":[{{"index":1,"role":"简短中文角色","shot_type":"white_main|hero_feature|lifestyle|detail|comparison|specs|in_box|trust",
"product_presence":"hero|supporting|environmental|absent","layout":"white_main|poster_hero|result_showcase|split_compare|spec_grid|scenario_mosaic|human_context|in_box_flatlay|detail_macro|trust_close",
"buyer_question":"这张图回答的购买问题","visual_structure":"主体位置/大小、镜头类型、分栏或网格、背景层次、标题区位置","text_zone":"top-left|top-center|top-right|center-left|center-right|bottom-left|bottom-center|bottom-right"}}]}}
要求：index 必须使用原始序号 {indices}；识别图片实际内容；product_presence 按产品在画面中的真实占比判断
（产品不在画面=absent，产品很小=environmental）；忽略图中品牌文案措辞，只提取可迁移的销售任务和版式结构。"""
        try:
            parsed = _strip_json(await _collect_vision(prompt, [item[1] for item in batch])) or {}
        except Exception:
            parsed = {}
        for item in parsed.get("templates", []) if isinstance(parsed, dict) else []:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            if index not in indices:
                continue
            shot_type = str(item.get("shot_type") or "hero_feature").strip().lower()
            if shot_type not in allowed_types:
                shot_type = "hero_feature"
            presence = str(item.get("product_presence") or "").strip().lower()
            if presence not in _PRESENCES:
                presence = _PRESENCE_DEFAULT_BY_TYPE.get(shot_type, "supporting")
            layout = str(item.get("layout") or "").strip().lower()
            if layout not in _LAYOUT_FAMILIES:
                layout = _LAYOUT_DEFAULT_BY_TYPE.get(shot_type, "poster_hero")
            text_zone = str(item.get("text_zone") or "top-center")
            if text_zone not in _TEXT_ZONES:
                text_zone = "top-center"
            story.append({
                "index": index,
                "url": refs[index - 1] if 0 < index <= len(refs) else "",
                "role": _clean_text(item.get("role"))[:24],
                "shot_type": shot_type,
                "product_presence": presence,
                "layout": layout,
                "buyer_question": _clean_text(item.get("buyer_question"))[:160],
                "visual_structure": _clean_text(item.get("visual_structure"))[:500],
                "text_zone": text_zone,
            })
    return sorted(story, key=lambda item: item["index"])


# ─── 策略质检 ─────────────────────────────────────────────────────────────────

def _creative_plan_quality(images: list[dict], deliverable: str) -> dict:
    """Deterministic strategy QA.  This intentionally does not claim the rendered
    pixels are commercially safe; product fidelity remains a human delivery gate."""
    issues: list[dict] = []

    def add(code: str, message: str, severity: str = "warning") -> None:
        issues.append({"code": code, "message": message, "severity": severity})

    if not images:
        add("empty", "方案中没有可生成的分镜", "error")
    if deliverable == "gallery" and images:
        main = images[0]
        if main.get("shot_type") != "white_main" or main.get("text_on_image"):
            add("main_compliance", "首图必须是纯白底、无文字主图", "error")
        if not 5 <= len(images) <= 8:
            add("gallery_count", "商品套图建议保留 5–8 张", "warning")
    presences = [str(img.get("product_presence") or "supporting") for img in images]
    hero_count = sum(p == "hero" for p in presences)
    if hero_count > 2:
        add("presence_monotony", f"{hero_count} 张都以产品为绝对主体，构图同质化——优秀套图的产品大特写不超过 2 张", "error")
    if deliverable == "gallery" and len(images) >= 5 and not any(p in ("absent", "environmental") for p in presences):
        add("presence_no_story", "整套没有任何成果/场景叙事图（产品缺席或极小的画面），不符合优秀 3C 套图语法", "error")
    layouts = [str(img.get("layout") or "") for img in images]
    for layout in set(layouts):
        if layout and layout != "white_main" and layouts.count(layout) > 2:
            add("layout_repetition", f"版式 {layout} 使用了 {layouts.count(layout)} 次，建议不超过 2 次")
    seen_points: set[str] = set()
    seen_concepts: set[str] = set()
    seen_cameras: set[str] = set()
    for idx, image in enumerate(images):
        point = str(image.get("selling_point") or "").strip().lower()
        if point and point in seen_points:
            add("duplicate_story", f"第 {idx + 1} 张与其他分镜重复表达同一卖点")
        seen_points.add(point)
        if image.get("asset_mode") == "generate":
            concept = re.sub(r"\s+", " ", str(image.get("visual_concept") or "").strip().lower())
            camera = re.sub(r"\s+", " ", str(image.get("camera_direction") or "").strip().lower())
            treatment = str(image.get("product_treatment") or "").strip()
            if not concept or not camera or not treatment:
                add("missing_art_direction", f"第 {idx + 1} 张缺少独立视觉概念、镜头或产品物理处理说明", "error")
            if concept and concept in seen_concepts:
                add("duplicate_visual_concept", f"第 {idx + 1} 张重复使用同一视觉概念", "error")
            if camera and camera in seen_cameras:
                add("duplicate_camera_direction", f"第 {idx + 1} 张重复使用同一镜头构图", "error")
            seen_concepts.add(concept)
            seen_cameras.add(camera)
        if _HYPE_PROMPT_RE.search(str(image.get("render_prompt") or "")):
            add("ai_aesthetic", f"第 {idx + 1} 张仍包含容易产生 AI 感的夸张场景词", "error")
        if len(str(image.get("headline") or "")) > 48:
            add("headline_length", f"第 {idx + 1} 张标题偏长，手机端可读性较差")
        for field in ("headline", "subline", "big_number", "eyebrow", "supporting_text", "proof", "callout"):
            if _INTERNAL_PUBLIC_COPY_RE.search(str(image.get(field) or "")):
                add("internal_copy", f"第 {idx + 1} 张把内部审核说明写进了消费者文案", "error")
                break
        public_copy = [
            str(image.get(field) or "").strip()
            for field in ("eyebrow", "headline", "subline", "big_number", "callout", "supporting_text", "proof")
            if str(image.get(field) or "").strip()
        ]
        if image.get("text_on_image"):
            prompt = str(image.get("render_prompt") or "")
            if not public_copy:
                add("missing_artwork_copy", f"第 {idx + 1} 张启用了图上文字但没有可生成的公开文案", "error")
            else:
                def _in_prompt(value: str) -> bool:
                    # 文案合同以 JSON 形式编译进提示词，含引号/反斜杠的文案会被
                    # 转义（2.0" → 2.0\"）——按转义后形态比对，避免误报。
                    return value in prompt or json.dumps(value, ensure_ascii=False)[1:-1] in prompt
                if "[FINAL ARTWORK COPY]" not in prompt or any(not _in_prompt(v) for v in public_copy):
                    add("copy_not_compiled", f"第 {idx + 1} 张公开文案尚未完整编译进最终生图提示词", "error")
        if image.get("proof") and not _public_proof(image.get("proof")):
            add("invalid_public_proof", f"第 {idx + 1} 张的证明数字不是简短、可公开的事实", "error")
        expected_size = "1464x600" if deliverable == "aplus" else "1600x1600"
        if image.get("size") != expected_size:
            add("canvas_mismatch", f"第 {idx + 1} 张画布必须统一为 {expected_size}", "error")
        if image.get("asset_mode") != "generate":
            add("direct_generation_required", f"第 {idx + 1} 张未使用统一的模型直出策略", "error")
        presence = str(image.get("product_presence") or ("absent" if image.get("show_product") is False else "supporting"))
        if presence != "absent" and not image.get("product_source_url"):
            add("product_pending", f"第 {idx + 1} 张需要上传或采集的产品真值图，禁止无参考生成", "error")
        headline_text = str(image.get("headline") or "").strip()
        if headline_text and (
            headline_text.endswith((":", "-", ",", "：")) or headline_text.startswith("[")
            or re.search(r"\b(?:the|a|an|with|and|to|of|for)$", headline_text, re.I)
        ):
            add("headline_fragment", f"第 {idx + 1} 张标题是截断碎片（\"{headline_text[:30]}\"），必须是完整短语", "error")
        if image.get("requires_source"):
            add("missing_evidence", f"第 {idx + 1} 张依赖未提供的真实证据，应换成有事实支撑的直出画面", "error")
        if idx and not image.get("evidence"):
            add("missing_claim_source", f"第 {idx + 1} 张缺少卖点依据")
        if image.get("final_url"):
            render_qa = image.get("render_qa") if isinstance(image.get("render_qa"), dict) else {}
            if not render_qa:
                add("render_unreviewed", f"第 {idx + 1} 张尚未通过成图质检", "error")
            elif render_qa.get("ready") is not True:
                add("render_failed", f"第 {idx + 1} 张成图质检未通过", "error")
    error_count = sum(i["severity"] == "error" for i in issues)
    score = max(0, 100 - error_count * 22 - (len(issues) - error_count) * 6)
    return {
        "score": score,
        "ready": not error_count and bool(images),
        "issues": issues,
        "note": "策略、公开文案、画布和已生成图片的质检结果；生成图还需通过产品一致性硬门槛并由人工终审。",
    }


# ─── 计划规范化 ───────────────────────────────────────────────────────────────

def _normalize_shot_plan(plan: dict, target_count: int, deliverable: str = "gallery") -> dict:
    """Clamp/repair an LLM visual plan into the shape used by the studio."""
    deliverable = "aplus" if deliverable == "aplus" else "gallery"
    plan_style = plan.get("style") if isinstance(plan.get("style"), dict) else {}
    product_profile = plan.get("product_profile") if isinstance(plan.get("product_profile"), dict) else {}
    if not product_profile and _clean_text(plan.get("product_lock")):
        product_profile = _fallback_product_visual_profile(_clean_text(plan.get("product_lock")))
    profile_anchors = [
        _clean_text(value) for value in (product_profile.get("fidelity_anchors") or [])
        if _clean_text(value)
    ][:8]
    profile_opportunities = [
        _clean_text(value) for value in (product_profile.get("visual_opportunities") or []) if _clean_text(value)
    ]
    profile_interactions = [
        _clean_text(value) for value in (product_profile.get("natural_interactions") or []) if _clean_text(value)
    ]
    default_cameras = [
        "eye-level three-quarter hero with accurate scale",
        "close three-quarter feature view with selective depth of field",
        "honest human-scale use view with natural contact",
        "macro detail connected to the complete product context",
        "slightly elevated comparison view with clear spatial hierarchy",
        "orthographic configuration view with realistic perspective",
        "wide environmental view with deliberate negative space",
        "alternate-side trust close with restrained depth",
    ]
    plan_style = _normalise_set_style(plan_style, product_profile)
    creative_brief = _clean_text(plan.get("creative_brief"))
    artwork_language = _clean_text(plan.get("language")) or "en"
    raw = plan.get("images") if isinstance(plan, dict) else None
    accent_candidates = [
        str(item.get("accent_color")) for item in (raw or [])
        if isinstance(item, dict) and str(item.get("accent_color") or "").strip()
    ]
    set_accent_raw = str(plan_style.get("accent_color") or (
        max(set(accent_candidates), key=accent_candidates.count) if accent_candidates else "#4F8CFF"
    ))
    clean: list[dict] = []
    for i, it in enumerate(raw or []):
        if not isinstance(it, dict):
            continue
        callout = _public_text(it.get("callout"), 90)
        headline = _public_text(it.get("headline"), 48)
        subline = _public_text(it.get("subline"), 110)
        supporting_text = _public_text(it.get("supporting_text"), 90)
        eyebrow = _public_text(it.get("eyebrow"), 24)
        big_number = _public_text(it.get("big_number"), 20)
        proof = _public_proof(it.get("proof"))
        stype = str(it.get("shot_type") or "").strip().lower()
        stype = _LEGACY_SHOT_TYPE.get(stype, stype)
        if stype not in _SHOT_TYPES:
            order = _APLUS_TYPE_ORDER if deliverable == "aplus" else _DEFAULT_TYPE_ORDER
            stype = order[i] if i < len(order) else "hero_feature"
        # 产品出场光谱：策划可指定 hero/supporting/environmental/absent；
        # 不再强制每张图都以产品为主体（对标优秀 3C 套图语法）。
        presence = str(it.get("product_presence") or "").strip().lower()
        if presence not in _PRESENCES:
            presence = _PRESENCE_DEFAULT_BY_TYPE.get(stype, "supporting")
        layout = str(it.get("layout") or "").strip().lower()
        if layout not in _LAYOUT_FAMILIES:
            layout = _LAYOUT_DEFAULT_BY_TYPE.get(stype, "poster_hero")
        if layout == "white_main":
            presence = "hero"
        if layout in {"result_showcase"}:
            presence = "absent"
        show_product = presence != "absent"
        text_zone = str(it.get("text_zone") or it.get("text_pos") or "top-left")
        if text_zone not in _TEXT_ZONES:
            text_zone = "top-left"
        layout_style = str(it.get("layout_style") or "editorial")
        if layout_style not in _LAYOUT_STYLES:
            layout_style = "editorial"
        roles = _APLUS_ROLES if deliverable == "aplus" else _DEFAULT_ROLES
        role = str(it.get("role") or roles[min(i, len(roles) - 1)])
        if deliverable == "gallery" and i == 0:
            role = "主图"
        text_on_image = bool(it.get(
            "text_on_image",
            bool(callout or headline or subline or supporting_text or eyebrow or proof or big_number),
        )) and not (deliverable == "gallery" and i == 0)
        opportunity = profile_opportunities[i % len(profile_opportunities)] if profile_opportunities else f"product-specific {stype}"
        interaction = profile_interactions[i % len(profile_interactions)] if profile_interactions else "natural real-world use"
        visual_concept = _clean_text(it.get("visual_concept")) or f"{opportunity} for {role}"
        camera_direction = _clean_text(it.get("camera_direction")) or default_cameras[i % len(default_cameras)]
        product_treatment = _clean_text(it.get("product_treatment")) or (
            f"{_clean_text(product_profile.get('form_and_scale')) or 'accurate real-world scale and orientation'}; {interaction}"
        )
        expected_size = "1464x600" if deliverable == "aplus" else "1600x1600"
        graphic_labels = [
            _public_text(value, 34) for value in (it.get("graphic_labels") or [])
            if _public_text(value, 34)
        ][:6]
        if not graphic_labels:
            graphic_labels = [value for value in (
                proof,
                _public_text(supporting_text, 34),
            ) if value][:6]
        accent_color = set_accent_raw
        fact_text = " ".join(str(it.get(key) or "") for key in ("scene", "headline", "evidence", "selling_point")).lower()
        if accent_color.upper() == "#4F8CFF" and re.search(r"\b(outdoor|forest|wildlife|hunting|garden|farm|nature)\b", fact_text):
            accent_color = "#67C95B"
        accent_color = _readable_accent(accent_color)
        versions = [v for v in (it.get("versions") or []) if isinstance(v, dict)][-8:]
        base_url = str(it.get("base_url") or "")
        final_url = str(it.get("final_url") or "")
        render_qa = it.get("render_qa") if isinstance(it.get("render_qa"), dict) else None
        human_reviewed = bool(it.get("human_reviewed", False)) and bool((render_qa or {}).get("ready"))
        clean.append({
            "slot": str(it.get("slot") or (("main" if i == 0 else f"sub{len(clean)}") if deliverable == "gallery" else f"aplus_{len(clean) + 1}")),
            "role": role,
            "shot_type": stype,
            "product_presence": presence,
            "layout": layout,
            "show_product": show_product,
            "angle": str(it.get("angle") or ""),
            "scene": str(it.get("scene") or ""),
            "selling_point": (str(it.get("selling_point")).strip() or None) if it.get("selling_point") else None,
            "buyer_question": str(it.get("buyer_question") or ""),
            "evidence": str(it.get("evidence") or ""),
            "headline": headline,
            "subline": subline,
            "big_number": big_number,
            "callout": callout,
            "supporting_text": supporting_text,
            "eyebrow": eyebrow,
            "proof": proof,
            "text_on_image": text_on_image,
            "text_pos": text_zone,  # legacy consumer alias
            "text_zone": text_zone,
            "layout_style": layout_style,
            "layout_blueprint": "",
            "panel_prompts": [],
            "graphic_labels": graphic_labels,
            "theme": str(it.get("theme") or "auto"),
            "accent_color": accent_color,
            "composition": str(it.get("composition") or ""),
            "visual_concept": visual_concept,
            "camera_direction": camera_direction,
            "product_treatment": product_treatment,
            "asset_mode": "generate",
            "manual_template": False,
            "requires_source": False,
            "source_requirement": "使用上传图优先的产品真值参考；仅允许改变场景、镜头、构图、光线、辅助设计和指定图上文案",
            "source_url": "",
            "product_source_url": str(it.get("product_source_url") or ""),
            "template_url": str(it.get("template_url") or ""),
            "template_index": 0,
            "template_analysis": None,
            "background_prompt": "",
            "product_scale": _clamped_float(
                it.get("product_scale"), _PRESENCE_SCALE.get(presence, 0.32), 0.0, 0.9),
            "acceptance_criteria": [str(v) for v in (it.get("acceptance_criteria") or []) if str(v).strip()][:6],
            "size": expected_size,
            "render_prompt": str(it.get("render_prompt") or ""),
            "base_url": base_url,
            "final_url": final_url,
            "versions": versions,
            "render_qa": render_qa,
            "auto_retry_count": 1 if it.get("auto_retry_count") else 0,
            "last_retry_guidance": [
                str(value) for value in (it.get("last_retry_guidance") or []) if str(value).strip()
            ][:6],
            "human_reviewed": human_reviewed,
        })
    if clean and deliverable == "gallery":  # 第一张永远是纯白底无字主图
        clean[0].update(slot="main", role="主图", shot_type="white_main", show_product=True,
                        product_presence="hero", layout="white_main", product_scale=0.85,
                        text_on_image=False, callout=None, headline=None, subline=None,
                        big_number=None, supporting_text=None,
                        eyebrow=None, proof=None, text_zone="top-left", text_pos="top-left",
                        layout_style="minimal", layout_blueprint="",
                        panel_prompts=[], graphic_labels=[], requires_source=False, asset_mode="generate",
                        source_requirement="以上传或采集主图为不可变产品真值，模型直接生成合规纯白底主图")
    # ── 构图配额：防止"张张都是产品大特写"的同质化 ─────────────────────────
    # hero 上限 2（含 white_main）；≥5 张的整套至少 1 张 absent/environmental。
    hero_indexes = [idx for idx, card in enumerate(clean) if card["product_presence"] == "hero"]
    for idx in hero_indexes[2:]:
        clean[idx]["product_presence"] = "supporting"
        clean[idx]["product_scale"] = _PRESENCE_SCALE["supporting"]
    if len(clean) >= 5 and not any(
        card["product_presence"] in ("absent", "environmental") for card in clean
    ):
        # 把最适合"成果/对比"叙事的一张转成 absent（优先 comparison/specs/trust）
        for prefer in ("comparison", "specs", "trust", "lifestyle"):
            for idx, card in enumerate(clean):
                if idx and card["shot_type"] == prefer and not card.get("final_url"):
                    card["product_presence"] = "absent"
                    card["show_product"] = False
                    card["layout"] = "result_showcase" if prefer != "specs" else "spec_grid"
                    card["product_scale"] = 0.0
                    break
            else:
                continue
            break
    # ── 统一编译最终生图提示词（结构字段 → 确定性 prompt）──────────────────
    plan_ctx = {
        "creative_brief": creative_brief,
        "language": artwork_language,
        "product_lock": str(plan.get("product_lock") or "").strip(),
    }
    for card in clean:
        card["render_prompt"] = _compile_render_prompt(
            card, plan_style, product_profile, plan_ctx, deliverable,
        )
    clean = clean[:target_count] if target_count and target_count > 0 else clean[:8]
    normalized = {
        "deliverable": deliverable,
        "planning_mode": "adaptive_direct_text",
        "style": plan_style,
        "product_profile": product_profile,
        "product_lock": str(plan.get("product_lock") or "").strip(),
        "story": str(plan.get("story") or "").strip(),
        "creative_brief": creative_brief,
        "language": artwork_language,
        "template_mode": False,
        "product_source_url": str(plan.get("product_source_url") or ""),
        "template_images": [],
        "template_story": plan.get("template_story") if isinstance(plan.get("template_story"), list) else [],
        "planner": str(plan.get("planner") or ""),
        "images": clean,
        "set_qa": plan.get("set_qa") if isinstance(plan.get("set_qa"), dict) else None,
    }
    if normalized["style"] is not None:
        normalized["style"]["accent_color"] = clean[0]["accent_color"] if clean else _readable_accent(set_accent_raw)
    normalized["quality"] = _creative_plan_quality(clean, deliverable)
    return normalized


def _bind_reference_templates(plan: dict, project_id: str, scrape_data: dict,
                              deliverable: str) -> dict:
    """给每张卡绑定产品真值（presence≠absent 时），并按逆向学习结果绑版式模板。

    - product_source_url：上传优先的白底产品真值；absent 卡不需要（产品不出现）。
    - template_url：template_story 里 shot_type/layout 匹配的竞品图 → 渲染层走
      双参考（REFERENCE 2 = 仅版式模板）。absent 卡不绑（无产品真值可当 REF 1）。
    """
    product_source = _cached_white_product_source(scrape_data)
    images = plan.get("images") if isinstance(plan.get("images"), list) else []
    story = plan.get("template_story") if isinstance(plan.get("template_story"), list) else []
    plan["product_source_url"] = product_source
    plan["template_images"] = [t.get("url") for t in story if t.get("url")]
    plan["template_mode"] = bool(plan["template_images"])
    used_template_indexes: set[int] = set()

    def _match_template(image: dict) -> dict | None:
        for t in story:
            if t.get("index") in used_template_indexes or not t.get("url"):
                continue
            if t.get("shot_type") == image.get("shot_type") or t.get("layout") == image.get("layout"):
                return t
        return None

    for image in images:
        if not isinstance(image, dict):
            continue
        previous_product = str(image.get("product_source_url") or "")
        presence = str(image.get("product_presence") or "supporting")

        def invalidate_render() -> None:
            if image.get("final_url"):
                history = list(image.get("versions") or [])
                history.append({
                    "url": image.get("final_url"), "base_url": image.get("base_url") or "",
                    "render_qa": image.get("render_qa"), "created_at": "invalidated-by-source-migration",
                })
                image["versions"] = history[-8:]
            image["base_url"] = ""
            image["final_url"] = ""
            image["render_qa"] = None
            image["human_reviewed"] = False

        image["asset_mode"] = "generate"
        image["show_product"] = presence != "absent"
        image["requires_source"] = False
        image["source_url"] = ""
        image["source_requirement"] = (
            "产品不出现在本张画面（成果/对比/信息版式），文字与数字只能来自已确认文案"
            if presence == "absent" else
            "以上传图优先的产品真值为高保真参考；仅改变场景、镜头、构图、光线、辅助设计和指定图上文案"
        )
        image["product_source_url"] = "" if presence == "absent" else product_source
        template = None
        if presence != "absent" and str(image.get("shot_type")) != "white_main":
            template = _match_template(image)
        if template:
            used_template_indexes.add(template.get("index"))
            image["template_url"] = str(template.get("url") or "")
            image["template_index"] = int(template.get("index") or 0)
            image["template_analysis"] = {
                "visual_structure": template.get("visual_structure", ""),
                "role": template.get("role", ""),
            }
        else:
            image["template_url"] = ""
            image["template_index"] = 0
            image["template_analysis"] = None
        image["layout_blueprint"] = ""
        image["panel_prompts"] = []
        image["manual_template"] = False
        if (image.get("final_url") and presence != "absent"
                and previous_product and previous_product != product_source):
            invalidate_render()

    plan["quality"] = _creative_plan_quality(images, deliverable)
    return plan


# ─── 兜底方案（AI 不可用时依然给出可编辑整套）─────────────────────────────────

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
        logger.debug("_db 失败（旁路，已忽略）", exc_info=True)


def _auto_product_source(project_id: str, scrape_data: dict) -> str:
    """Choose the one source that passed white-background verification.

    Uploaded files are not automatically trusted: a lifestyle or infographic
    upload is useful evidence but is not the clean product identity anchor this
    workflow requires. upload_product_image stores a passing upload as the
    selected white_product_source, so deliberate verified uploads still win.
    """
    return _cached_white_product_source(scrape_data)


def _extract_fallback_copy(bullet: str) -> tuple[str, str, str]:
    """从一条五点提取 (headline, subline, big_number)。

    优先取 [Bold Header] / "HEADER:" 头部做标题（完整短语），正文首句截为副文案；
    再用正则捞一个可作大数字锚的规格（0.1s / 36MP / IP66 / 120° …）。
    老实现按词数硬切（前 6 词做标题）产出"[Trigger Speed]: A"式碎片，禁止回归。
    """
    text = _clean_text(bullet)
    headline, body = "", text
    m = re.match(r"^\[([^\]]{2,40})\]\s*:?\s*(.*)$", text)
    if not m:
        m = re.match(r"^([A-Z][A-Z0-9 &/\-]{2,34})\s*[:：]\s*(.*)$", text)
    if m:
        headline = _clean_text(m.group(1)).title() if m.group(1).isupper() else _clean_text(m.group(1))
        body = _clean_text(m.group(2))
    else:
        first = re.split(r"[.。;；]", text, 1)[0]
        words = first.split()
        if len(words) <= 5:
            headline = first
    subline = ""
    if body:
        first_clause = re.split(r"[.。;；]", body, 1)[0]
        words = first_clause.split()
        subline = " ".join(words[:14]).rstrip(",，")
        if len(words) > 14:
            subline = ""  # 截不出完整短句就宁可不要
    number = ""
    nm = re.search(
        r"\b(IP\d{2}|\d+(?:\.\d+)?\s?(?:MP|K|fps|s|sec|ft|m|mm|°|%|GB|TB|h|hr|min|mAh|W|Hz|x|X)\b[\w/]*)",
        text,
    )
    if nm:
        number = _clean_text(nm.group(1))[:16]
    return headline[:48], subline[:110], number


def _shot_plan_fallback(row, scrape_data: dict, analysis_data: dict, target_count: int,
                        color_scheme: str, deliverable: str = "gallery",
                        product_profile: Optional[dict] = None) -> dict:
    """LLM 策划不可用时的确定性方案：按内置精品叙事库排布 presence/版式，
    文案从五点结构化提取，提示词交给同一个编译器——兜底也不再千篇一律。"""
    deliverable = "aplus" if deliverable == "aplus" else "gallery"
    n = target_count if target_count and target_count > 0 else (5 if deliverable == "aplus" else 7)
    bullets = [ln[2:] for ln in _approved_copy(row).splitlines() if ln.startswith("- ")]
    if not bullets:
        src_copy = _copy_source(row, scrape_data, analysis_data)
        bullets = src_copy["usp"] + src_copy["bullets"]
    profile = product_profile if isinstance(product_profile, dict) and product_profile else _fallback_product_visual_profile(
        _build_product_context(row, scrape_data, analysis_data)
    )
    behaviour = _clean_text(profile.get("object_behavior")) or "category_specific"
    narrative = (_APLUS_NARRATIVE if deliverable == "aplus"
                 else _GALLERY_NARRATIVES.get(behaviour, _GALLERY_NARRATIVES["category_specific"]))
    steps = list(narrative[:n])
    while len(steps) < n:
        steps.append(("hero_feature", "supporting", "poster_hero", f"卖点 {len(steps)}"))
    scenes = [str(v) for v in (profile.get("scene_families") or []) if str(v).strip()]
    opportunities = [str(v) for v in (profile.get("visual_opportunities") or []) if str(v).strip()]
    camera_directions = [
        "eye-level environmental view with a clear foreground-to-background path",
        "close tactile three-quarter view with selective depth of field",
        "honest human-scale use view with natural contact and posture",
        "quiet editorial wide shot with asymmetrical negative space",
        "macro construction detail anchored to the complete product context",
        "slightly elevated view with clear spatial hierarchy",
        "alternate-side view that does not repeat the hero angle",
    ]
    imgs = []
    copy_cursor = 0
    for i, (stype, presence, layout, role) in enumerate(steps):
        slot = ("main" if i == 0 else f"sub{i}") if deliverable == "gallery" else f"aplus_{i + 1}"
        headline = subline = number = ""
        sp = None
        if not (deliverable == "gallery" and i == 0):
            sp = bullets[copy_cursor % len(bullets)] if bullets else None
            copy_cursor += 1
            if sp:
                headline, subline, number = _extract_fallback_copy(sp)
        imgs.append({
            "slot": slot, "role": role, "shot_type": stype,
            "product_presence": presence, "layout": layout,
            "selling_point": _clean_text(sp) or None,
            "evidence": _clean_text(sp),
            "headline": headline or None, "subline": subline or None,
            "big_number": number or None,
            "text_on_image": bool(headline or subline or number),
            "text_zone": "top-center",
            "scene": scenes[i % len(scenes)] if scenes else "",
            "visual_concept": (opportunities[i % len(opportunities)] if opportunities else f"{role} storytelling"),
            "camera_direction": camera_directions[i % len(camera_directions)],
            "acceptance_criteria": ["产品外观与参考图一致", "版式与整套统一", "手机端文字可读"],
            "size": "1464x600" if deliverable == "aplus" else "1600x1600",
        })
    return _normalize_shot_plan({
        "images": imgs,
        "style": {"palette": color_scheme} if color_scheme else {},
        "product_lock": analysis_data.get("product_lock", ""),
        "product_profile": profile, "planning_mode": "adaptive_direct_text",
    }, n, deliverable)


def _persist_shot_plan(project_id: str, plan: dict, deliverable: str = "gallery") -> None:
    try:
        conn = _db()
        row = conn.execute("SELECT creative_sets FROM listing_projects WHERE id = ?", (project_id,)).fetchone()
        sets = {}
        if row and row["creative_sets"]:
            try:
                sets = json.loads(row["creative_sets"])
            except Exception:
                sets = {}
        deliverable = "aplus" if deliverable == "aplus" else "gallery"
        sets[deliverable] = plan
        if deliverable == "gallery":
            conn.execute(
                "UPDATE listing_projects SET creative_sets=?, shot_plan=?, updated_at=? WHERE id=?",
                (json.dumps(sets, ensure_ascii=False), json.dumps(plan, ensure_ascii=False), time.time(), project_id),
            )
        else:
            conn.execute(
                "UPDATE listing_projects SET creative_sets=?, updated_at=? WHERE id=?",
                (json.dumps(sets, ensure_ascii=False), time.time(), project_id),
            )
        conn.commit()
        conn.close()
    except Exception:
        logger.debug("_db 失败（旁路，已忽略）", exc_info=True)


# ─── 整套策划（后台 job）─────────────────────────────────────────────────────

async def run_plan_image_set(project_id: str, body: PlanImageSetReq,
                             handle: Optional[JobHandle] = None) -> dict:
    """Plan either a gallery or A+ set through the same evidence-led engine."""

    def progress(stage: str, message: str, value: float) -> None:
        if handle:
            handle.update(stage=stage, message=message, progress=value)

    row = project_row(project_id)
    if not row:
        raise HTTPException(404)
    scrape_data = json.loads(row["scrape_data"]) if row["scrape_data"] else {}
    analysis_data = json.loads(row["analysis_data"]) if row["analysis_data"] else {}
    product_context = _build_product_context(row, scrape_data, analysis_data)
    ref_images = scrape_data.get("reference_images", []) or scrape_data.get("imageUrls", [])
    ref_text = "\n".join(ref_images[:3]) if ref_images else "(no reference images)"
    progress("white", "校验白底产品真值…", 0.08)
    try:
        white_source, white_report = await asyncio.wait_for(
            _detect_white_product_source(project_id, scrape_data), timeout=120,
        )
    except Exception:
        white_source, white_report = "", []
    scrape_data["white_product_source"] = white_source
    scrape_data["white_product_source_check"] = {
        "ready": bool(white_source),
        "selected": white_source,
        "candidates": white_report,
    }
    try:
        update_project(project_id, scrape_data=json.dumps(scrape_data, ensure_ascii=False))
    except Exception:
        logger.debug("update_project 失败（旁路，已忽略）", exc_info=True)
    progress("identity", "分析产品视觉身份…", 0.25)
    try:
        product_profile = await asyncio.wait_for(
            _analyze_product_visual_identity(scrape_data, product_context, white_source), timeout=150,
        )
    except Exception:
        product_profile = _fallback_product_visual_profile(product_context)
    product_profile_text = json.dumps(product_profile, ensure_ascii=False, indent=2)
    img_sp = analysis_data.get("image_insights", "")
    approved = _approved_copy(row)
    color_directive = _color_directive(body.color_scheme, analysis_data)
    deliverable = "aplus" if body.deliverable == "aplus" else "gallery"
    # 通道 2：竞品套图逆向学习。采到 ≥4 张（通常是优秀卖家的完整套图）且视觉可用
    # 时，把每张的版式语法逆向出来喂给策划器——借结构，不借像素。
    plan_template_story: list[dict] = []
    if deliverable == "gallery" and len(_reference_images(scrape_data)) >= 4 and has_vision():
        progress("templates", "逆向学习竞品套图版式…", 0.32)
        try:
            plan_template_story = await asyncio.wait_for(
                _analyze_reference_templates(scrape_data), timeout=180,
            )
        except Exception:
            plan_template_story = []
    max_count = 6 if deliverable == "aplus" else 8
    n = max(0, min(int(body.target_count or 0), max_count))
    count_rule = (
        f"Produce EXACTLY {n} modules." if n else
        ("Produce 4–6 A+ modules." if deliverable == "aplus" else "Choose 5–8 images; default to 7 when the facts support it.")
    )
    deliverable_rules = (
        "A+ modules use a wide 1464x600 canvas. Build one brand story across a banner, primary benefit, "
        "usage/education, detail or comparison, and trust close. Do not create a white Amazon main image."
        if deliverable == "aplus" else
        "The first image is the Amazon white main image. The remaining images form a mobile-first sales story."
    )

    behaviour = _clean_text(product_profile.get("object_behavior")) or "category_specific"
    narrative = (_APLUS_NARRATIVE if deliverable == "aplus"
                 else _GALLERY_NARRATIVES.get(behaviour, _GALLERY_NARRATIVES["category_specific"]))
    narrative_text = "\n".join(
        f"  {i + 1}. shot_type={s} · product_presence={p} · layout={l} · 角色={r}"
        for i, (s, p, l, r) in enumerate(narrative[: n or len(narrative)])
    )
    template_story = plan_template_story or []
    template_text = ""
    if template_story:
        template_text = "\n## COMPETITOR GALLERY GRAMMAR (reverse-engineered from a top seller — reuse the STRUCTURE, never the pixels/brand)\n" + "\n".join(
            f"  {t.get('index')}. {t.get('role')} · {t.get('shot_type')} · presence≈{t.get('product_presence', 'supporting')} · {t.get('visual_structure', '')[:120]}"
            for t in template_story[:8]
        )

    prompt = f"""You are a senior ecommerce creative director planning a commercially usable Amazon {deliverable} set. {count_rule}

Study how flagship 3C brands (DJI / Anker level) build galleries: only 1-2 images show the product as the dominant
subject. The rest rotate the spotlight — outcome showcases, A/B comparisons, oversized spec numbers, real humans in
real scenes, use-case mosaics — under ONE consistent title band, type system and palette. Plan that calibre of set.

## PRODUCT INFO
{product_context}

## APPROVED LISTING COPY (the only source for exact on-image text and numbers)
{approved or "(none — derive concise phrases from the bullets/selling points above; never invent numbers)"}

## PRODUCT VISUAL IDENTITY
{product_profile_text}

## VISUAL ANALYSIS OF COLLECTED IMAGES
{img_sp or "(not available)"}
{template_text}

## RECOMMENDED NARRATIVE SKELETON (adapt to the facts; reorder/replace when justified)
{narrative_text}

## FIELD RULES
- product_presence: hero (product dominant, ≤2 per set incl. the white main) | supporting (25-40% of frame) |
  environmental (small in a real scene) | absent (product not in frame — outcome/comparison/spec panels).
  At least one image must be absent or environmental. {deliverable_rules}
- layout: white_main | poster_hero | result_showcase | split_compare | spec_grid | scenario_mosaic |
  human_context | in_box_flatlay | detail_macro | trust_close. Do not use the same layout more than twice.
- headline: a COMPLETE punchy phrase of 2-5 words (like "Stunning Low-Light Performance"). Never a truncated
  sentence fragment. subline: one supporting line ≤14 words. big_number: an exact spec anchor from the approved
  copy (e.g. "0.1s", "36MP", "IP66") or empty. proof: optional short public numeric fact.
- evidence: the exact product fact this image relies on. Do not plan images whose claims have no supporting fact.
- scene / visual_concept / camera_direction: ≤20 words each, concrete and different for every image.
- text_zone: top-left|top-center|top-right|center-left|center-right|bottom-left|bottom-center|bottom-right.
- Tone requested: {body.visual_tone}. Manual requirement (highest priority after facts): {body.brief or "none"}.
  Artwork language: {body.language}.
{color_directive}

## OUTPUT — return ONLY this JSON, no commentary. Keep every string SHORT; do not write long render prompts —
the rendering pipeline compiles final image prompts from these fields.
{{"style":{{"direction":"...","palette":"background #RRGGBB; surface #RRGGBB; supporting tone #RRGGBB; brand accent #RRGGBB; deep neutral #RRGGBB","lighting":"...","materials":"...","type_system":"...","accent_color":"#RRGGBB"}},
 "story":"one sentence sales narrative",
 "product_lock":"strict appearance description of the sellable product",
 "images":[
   {{"slot":"...","role":"...","shot_type":"...","product_presence":"hero|supporting|environmental|absent",
    "layout":"...","buyer_question":"...","selling_point":"...","evidence":"...",
    "headline":"...","subline":"...","big_number":"...","proof":"",
    "scene":"...","visual_concept":"...","camera_direction":"...","text_zone":"top-center"}}
 ]}}"""

    progress("plan", "AI 创意总监策划整套方案…", 0.4)
    parsed = None
    for attempt in range(2):
        try:
            raw = await asyncio.wait_for(
                _call_ai(prompt if attempt == 0 else (
                    prompt + "\n\nREMINDER: your previous reply could not be parsed. "
                    "Return ONLY the JSON object — no explanations, no markdown fences."
                ), web_search=False),
                timeout=240,
            )
        except (HTTPException, asyncio.TimeoutError):
            raw = ""
        parsed = _strip_json(raw)
        if parsed and isinstance(parsed.get("images"), list) and parsed["images"]:
            break
        if attempt == 0 and handle:
            handle.update(stage="plan", message="策划输出解析失败，重试一次…", progress=0.55)
        parsed = None
    progress("compile", "编译与质检方案…", 0.85)
    used_fallback = True
    plan = None
    if parsed:
        parsed["product_profile"] = product_profile
        parsed["planning_mode"] = "adaptive_direct_text"
        parsed["creative_brief"] = body.brief
        parsed["language"] = body.language
        parsed["template_story"] = template_story
        parsed["template_images"] = []
        plan = _normalize_shot_plan(parsed, n, deliverable)
        used_fallback = not plan["images"]
    if used_fallback:
        plan = _shot_plan_fallback(
            row, scrape_data, analysis_data, n, body.color_scheme, deliverable, product_profile,
        )
        plan["creative_brief"] = body.brief
        plan["language"] = body.language
        plan["template_story"] = template_story
        plan["template_images"] = []
        # 兜底同样把手动创意与语言编译进每张提示词，而不是静默丢弃最高优先级输入
        plan = _normalize_shot_plan(plan, n, deliverable)
    plan["planner"] = "fallback" if used_fallback else "ai"
    plan = _bind_reference_templates(plan, project_id, scrape_data, deliverable)
    _persist_shot_plan(project_id, plan, deliverable)
    _persist_visual_anchor(project_id, row, {"product_lock": plan.get("product_lock"),
                                             "visual_style": json.dumps(plan.get("style") or {}, ensure_ascii=False)})
    return {"ok": True, "plan": plan, "fallback": used_fallback, "deliverable": deliverable}


@router.post("/projects/{project_id}/plan-image-set")
async def plan_image_set_endpoint(project_id: str, body: PlanImageSetReq,
                                  _user: str = Depends(require_user)):
    """启动整套策划后台任务，立即返回 job。"""
    if not project_row(project_id, "id"):
        raise HTTPException(404)
    return start_job(
        "plan", project_id, body.model_dump(),
        lambda handle: run_plan_image_set(project_id, body, handle),
    )


@router.post("/projects/{project_id}/creative-set")
async def save_creative_set(project_id: str, body: dict, _user: str = Depends(require_user)):
    """Persist storyboard edits, generated URLs, versions and review state."""
    if not project_row(project_id, "id"):
        raise HTTPException(404, "project not found")
    deliverable = "aplus" if body.get("deliverable") == "aplus" else "gallery"
    raw_plan = body.get("plan") if isinstance(body.get("plan"), dict) else {}
    target_count = len(raw_plan.get("images") or [])
    plan = _normalize_shot_plan(raw_plan, target_count, deliverable)
    _persist_shot_plan(project_id, plan, deliverable)
    return {"ok": True, "plan": plan}


# ─── 成图复核（统一视觉链）────────────────────────────────────────────────────

def _vision_datauri(raw: bytes) -> str:
    """Downsize review inputs so visual QA is fast and bounded."""
    from PIL import Image
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=86, optimize=True)
    return f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}"


async def _render_vision_review(candidate: bytes, source: bytes | None, body: ReviewRenderReq) -> dict:
    """Use a vision model as an art director, not as the generator grading itself."""
    if not has_vision():
        return {"available": False, "reason": "visual_review_provider_unconfigured"}
    expected_copy = {
        key: value for key, value in (
            ("eyebrow", body.eyebrow), ("headline", body.headline), ("subline", body.subline),
            ("big_number", body.big_number), ("callout", body.callout),
            ("supporting_text", body.supporting_text), ("proof", body.proof),
        ) if str(value or "").strip()
    }
    images = ([_vision_datauri(source)] if source else []) + [_vision_datauri(candidate)]
    prompt = (
        "You are a strict senior ecommerce art director and Amazon image QA reviewer. "
        "Reject generic AI-looking scenes, pasted typography, incorrect product geometry, unreadable copy, "
        "weak selling-point communication, fake evidence and layouts that do not look commercially designed. "
        + ("Image 1 is the exact product/source truth; image 2 is the candidate listing image. "
           if source else "The single image below is the candidate listing image (no source reference supplied). ")
        + "Product fidelity is an identity check, not a general similarity score. OCR every added artwork string carefully.\n\n"
        f"Role: {body.role}; shot type: {body.shot_type}; product should appear: {body.show_product}; "
        f"intended product presence: {body.product_presence} "
        f"({'the product is intentionally small in a real scene — do not penalize its modest size, but its visible identity must still match the source' if body.product_presence == 'environmental' else 'the product is intentionally absent from this frame — treat any appearance of the device as an error' if body.product_presence == 'absent' else 'the product is a primary subject'}). "
        f"legacy structured blueprint: {body.layout_blueprint or 'none'}. "
        f"EXPECTED ADDED ARTWORK COPY (exact JSON): {json.dumps(expected_copy, ensure_ascii=False)}. "
        "Every expected string must appear exactly once, character-for-character, with no misspelling, paraphrase, "
        "translation, omission, duplication or extra marketing text. Existing labels printed on the source product are "
        "immutable product details and are not unexpected artwork copy. If expected JSON is empty, there must be no "
        "added headline, caption, number, badge or marketing text. "
        f"Product fidelity anchors: {', '.join(body.product_fidelity_anchors[:8]) or 'silhouette, proportions, colour, material and visible details'}. "
        + ("The source photo may be a bundle shot; loose accessory items in it (memory cards, cables, straps, manuals, "
           "packaging) are reference noise for this shot — do NOT penalize their absence; judge only the primary product. "
           if body.shot_type not in ("in_box", "white_main") else
           "This shot represents the purchased set: included accessories and their quantity must match the source. ")
        + "When product should appear is true, compare every visible primary-product part against the source: silhouette, proportions, "
        "colour blocking, material, seams, openings, controls, logo/label placement. Any redesign, "
        "missing/extra part, changed geometry or materially wrong detail is a fatal issue. When product should appear is false, "
        "do not penalize its intentional absence and return product_fidelity 100. "
        "Return JSON only: {\"scores\":{\"product_fidelity\":0-100,\"realism\":0-100,"
        "\"composition\":0-100,\"typography\":0-100,\"copy_accuracy\":0-100,\"commercial_readiness\":0-100},"
        "\"copy_check\":{\"exact\":true|false,\"unexpected_copy\":true|false,\"transcribed\":[\"...\"]},"
        "\"fatal_issues\":[\"...\"],\"improvements\":[\"...\"],\"verdict\":\"pass|fail\"}. "
        "Use pass only when every visible product detail matches the source, copy is concise and consumer-facing, "
        "all expected copy is exact, the layout has deliberate hierarchy, and the image could ship on a strong brand "
        "listing without repair."
    )
    text = await asyncio.wait_for(_collect_vision(prompt, images), timeout=180)
    parsed = _strip_json(text)
    return {"available": bool(parsed), "result": parsed or {}, "raw": (text or "")[:500]}


async def review_render_core(project_id: str, body: ReviewRenderReq) -> dict:
    """Technical + visual review.  A generated image cannot self-declare success."""
    source_locked = body.shot_type in _SOURCE_LOCKED_TYPES and bool(body.source_url)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            candidate = await _fetch_image_bytes(client, body.url)
            source = await _fetch_image_bytes(client, body.source_url) if body.source_url and not source_locked else None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"读取成图失败：{exc}") from exc

    from app.services.listing_image_compositor import technical_quality
    technical = technical_quality(candidate, body.size)
    issues = list(technical["issues"])
    public_values = [body.eyebrow, body.headline, body.callout, body.supporting_text, body.proof]
    if any(_INTERNAL_PUBLIC_COPY_RE.search(str(value or "")) for value in public_values):
        issues.append({"code": "internal_copy", "severity": "error",
                       "message": "成图文案包含内部审核说明，禁止交付"})
    if body.proof and not _public_proof(body.proof):
        issues.append({"code": "invalid_proof", "severity": "error",
                       "message": "证明数字不是简短、面向消费者的事实"})

    # Exact source artwork needs technical checks only; no model should second-
    # guess seller-owned pixels. All generated/composited art receives vision QA.
    vision = {"available": False, "reason": "exact_source_asset"}
    if not source_locked and not any(issue.get("severity") == "error" for issue in issues):
        try:
            vision = await _render_vision_review(candidate, source, body)
        except Exception as exc:  # noqa: BLE001
            vision = {"available": False, "reason": str(exc)[:240]}
        if vision.get("available"):
            result = vision.get("result") or {}
            scores = result.get("scores") if isinstance(result.get("scores"), dict) else {}
            fatal = [str(value) for value in (result.get("fatal_issues") or []) if str(value).strip()]
            improvements = [str(value) for value in (result.get("improvements") or []) if str(value).strip()]

            def score_value(key: str) -> int:
                try:
                    return max(0, min(100, round(float(scores.get(key, 0)))))
                except (TypeError, ValueError):
                    return 0

            product_fidelity = score_value("product_fidelity")
            visual_scores = [score_value(key) for key in (
                "realism", "composition", "typography", "commercial_readiness",
            )]
            retry_notes = [*fatal, *improvements]
            fidelity_floor = 80 if body.product_presence == "environmental" else 92
            if source and body.show_product and product_fidelity < fidelity_floor:
                issues.append({"code": "product_fidelity_failed", "severity": "error",
                               "message": f"产品外观一致性 {product_fidelity}/100，低于硬门槛 {fidelity_floor}"})
                retry_notes.append(
                    "Rebuild the product from the source reference without changing silhouette, proportions, colour, material, visible construction, labels or part count."
                )
            expected_copy = [
                str(value).strip() for value in public_values if str(value or "").strip()
            ]
            copy_check = result.get("copy_check") if isinstance(result.get("copy_check"), dict) else {}
            copy_accuracy = score_value("copy_accuracy")
            copy_failed = (
                (bool(expected_copy) and (copy_accuracy < 96 or copy_check.get("exact") is not True))
                or (not expected_copy and copy_check.get("unexpected_copy") is True)
            )
            if copy_failed:
                issues.append({"code": "artwork_copy_failed", "severity": "error",
                               "message": "图中文字与目标文案不完全一致，存在错字、漏字、重复或额外文字"})
                if expected_copy:
                    retry_notes.append(
                        "Preserve the composition but redraw the typography. Render exactly once and character-for-character: "
                        + " | ".join(expected_copy)
                    )
                else:
                    retry_notes.append("Remove every added headline, caption, number and marketing badge; preserve source-product labels only.")
            if result.get("verdict") != "pass" or fatal or min(visual_scores or [0]) < 80:
                issues.append({"code": "visual_review_failed", "severity": "error",
                               "message": fatal[0] if fatal else "成图审美、真实性或商业完成度未达标"})
                if not fatal and not improvements:
                    retry_notes.append("Improve realism, visual hierarchy, intentional negative space and commercial finish while preserving the product exactly.")
            vision["retry_guidance"] = retry_notes[:6]
        else:
            fidelity_required = bool(source and body.show_product)
            # 机审不可用不再一票否决：降级为人工复核门槛（勾选"已核对"即视为通过），
            # 否则视觉 provider 断供时整个工作台会像之前那样被 70 分 error 卡死。
            issues.append({
                "code": "product_fidelity_unverified" if fidelity_required else "visual_review_unavailable",
                "severity": "warning",
                "message": ("产品一致性机审未运行，请务必人工核对产品外观后勾选「已核对」"
                            if fidelity_required else "远程审美复核未返回，必须由人工完成审美复核"),
            })
    elif not source_locked:
        vision = {"available": False, "reason": "deterministic_quality_failure"}

    errors = [issue for issue in issues if issue.get("severity") == "error"]
    score_values = (vision.get("result") or {}).get("scores") if vision.get("available") else {}
    score_numbers = []
    for value in (score_values or {}).values():
        try:
            score_numbers.append(max(0, min(100, round(float(value)))))
        except (TypeError, ValueError):
            continue
    score = round(sum(score_numbers) / len(score_numbers)) if score_numbers else (
        100 if source_locked and technical["ready"] else (70 if technical["ready"] else 0)
    )
    return {
        "ready": not errors,
        "score": score,
        "issues": issues,
        "technical": technical,
        "vision": vision,
        "retry_guidance": vision.get("retry_guidance") or [],
        "manual_visual_review_required": not source_locked and not vision.get("available"),
        "reviewed_at": time.time(),
    }


@router.post("/projects/{project_id}/review-render")
async def review_render(project_id: str, body: ReviewRenderReq, _user: str = Depends(require_user)):
    return await review_render_core(project_id, body)


def _contact_sheet(items: list[bytes]) -> bytes:
    from PIL import Image, ImageDraw, ImageOps
    tile = 360
    cols = min(3, max(1, len(items)))
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tile, rows * tile), "#E9EAEC")
    draw = ImageDraw.Draw(sheet)
    for index, raw in enumerate(items):
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        fitted = ImageOps.fit(image, (tile - 12, tile - 12), Image.Resampling.LANCZOS)
        x, y = (index % cols) * tile + 6, (index // cols) * tile + 6
        sheet.paste(fitted, (x, y))
        draw.rectangle((x + 8, y + 8, x + 42, y + 38), fill=(0, 0, 0))
        draw.text((x + 19, y + 14), str(index + 1), fill=(255, 255, 255))
    output = io.BytesIO()
    sheet.save(output, "JPEG", quality=88, optimize=True)
    return output.getvalue()


async def _set_vision_review(sheet: bytes, plan: dict) -> dict:
    if not has_vision():
        return {"available": False, "reason": "visual_review_provider_unconfigured"}
    roles = [str(item.get("role") or item.get("slot") or "") for item in plan.get("images") or []]
    prompt = (
        "Act as a demanding ecommerce creative director reviewing this numbered contact sheet as ONE Amazon "
        "image set. Judge it against strong premium-brand listings. Reject sets that are merely unrelated AI "
        "renders, repeat the same product pose, use inconsistent aspect ratios/type scales/colour grading, have "
        "no visual narrative, contain pasted-looking copy, or fail to demonstrate the promised buyer benefit. "
        f"Intended sequence: {' | '.join(roles)}. Story: {plan.get('story') or ''}.\n\n"
        "Return JSON only: {\"scores\":{\"suite_cohesion\":0-100,\"design_system\":0-100,"
        "\"story_progression\":0-100,\"commercial_readiness\":0-100},\"fatal_issues\":[\"...\"],"
        "\"improvements\":[\"...\"],\"verdict\":\"pass|fail\"}. Pass requires every score >=80 and no "
        "fatal issue. Do not be generous."
    )
    text = await asyncio.wait_for(_collect_vision(prompt, [_vision_datauri(sheet)]), timeout=180)
    parsed = _strip_json(text)
    return {"available": bool(parsed), "result": parsed or {}, "raw": (text or "")[:500]}


async def run_review_image_set(project_id: str, deliverable: str,
                               handle: Optional[JobHandle] = None) -> dict:
    """Review the contact sheet so isolated acceptable images cannot masquerade as a coherent set."""
    deliverable = "aplus" if deliverable == "aplus" else "gallery"
    row = project_row(project_id, "creative_sets")
    if not row:
        raise HTTPException(404, "project not found")
    try:
        sets = json.loads(row["creative_sets"] or "{}")
        plan = sets.get(deliverable) or {}
    except Exception:
        plan = {}
    images = plan.get("images") or []
    complete = [item for item in images if item.get("final_url")]
    failed_individual = [item for item in complete if not (item.get("render_qa") or {}).get("ready")]
    issues: list[dict] = []
    if len(complete) != len(images) or not images:
        issues.append({"code": "incomplete_set", "severity": "error", "message": "整套图片尚未全部完成"})
    if failed_individual:
        issues.append({"code": "individual_failures", "severity": "error",
                       "message": f"仍有 {len(failed_individual)} 张未通过单图质检"})
    vision = {"available": False, "reason": "set_not_ready"}
    if not issues:
        if handle:
            handle.update(stage="review", message="AI 创意总监整套审阅中…", progress=0.5)
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                raws = await asyncio.gather(*[_fetch_image_bytes(client, item["final_url"]) for item in complete])
            vision = await _set_vision_review(_contact_sheet(raws), plan)
        except Exception as exc:  # noqa: BLE001
            vision = {"available": False, "reason": str(exc)[:240]}
        if vision.get("available"):
            result = vision.get("result") or {}
            scores = result.get("scores") if isinstance(result.get("scores"), dict) else {}
            values = [int(value) for value in scores.values() if str(value).isdigit()]
            fatal = [str(value) for value in (result.get("fatal_issues") or []) if str(value).strip()]
            if result.get("verdict") != "pass" or fatal or min(values or [0]) < 80:
                issues.append({"code": "suite_review_failed", "severity": "error",
                               "message": fatal[0] if fatal else "整套设计一致性和叙事未达到商业标准"})
        else:
            issues.append({"code": "suite_review_unavailable", "severity": "warning",
                           "message": "远程整套审美复核未返回，必须由人工检查套图一致性"})
    scores = (vision.get("result") or {}).get("scores") if vision.get("available") else {}
    values = [int(value) for value in (scores or {}).values() if str(value).isdigit()]
    set_qa = {
        "ready": not any(issue.get("severity") == "error" for issue in issues),
        "score": round(sum(values) / len(values)) if values else (70 if not any(
            issue.get("severity") == "error" for issue in issues) else 0),
        "issues": issues,
        "vision": vision,
        "reviewed_at": time.time(),
    }
    plan["set_qa"] = set_qa
    _persist_shot_plan(project_id, plan, deliverable)
    return {"set_qa": set_qa, "plan": plan, "deliverable": deliverable}


@router.post("/projects/{project_id}/review-image-set")
async def review_image_set_endpoint(project_id: str, body: ReviewSetReq,
                                    _user: str = Depends(require_user)):
    """整套复核也走后台 job（视觉链可能要几十秒）。"""
    if not project_row(project_id, "id"):
        raise HTTPException(404)
    return start_job(
        "review_set", project_id, body.model_dump(),
        lambda handle: run_review_image_set(project_id, body.deliverable, handle),
    )
