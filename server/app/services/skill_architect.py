"""Skill Architect — a rigorous, multi-stage pipeline that turns a one-line
idea into a validated SKILL.md.

Why this exists
---------------
The old ``/skill/generate-from-idea`` path asked the model to do everything in
one shot (understand intent + design steps + pick MCP tools + define the inputs
contract + emit valid YAML). Any weak link produced a skill that silently broke
in the visual tool layer, and the failure only surfaced when a user clicked
"execute" in production.

This module mirrors the user's own dev methodology onto the model:

    ① understand → ② plan → ③ review → ④ optimize → ⑤ render → ⑥ validate(+repair)

Orchestration, validation and repair all run **here in the backend** over the
stable HTTP provider chain (``generate_text`` → DeepSeek/Apimart, no hermes).
Each stage's *prompt* is an editable asset on disk under
``STUDIO_ROOT/architect/*.md`` (seeded from the defaults below on first use),
so the prompts can be tuned without redeploying code while the control flow
stays reliable.

The rendered SKILL.md keeps a top-level ``inputs:`` array (so the existing
visual layer in ``skill_tools.py`` / ``SkillTools.tsx`` works unchanged) and
additionally carries an explicit ``tool:`` spec block for the follow-up work.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.core.skill_paths import STUDIO_ROOT
from app.services import ai_synthesis_service, skill_repo

logger = logging.getLogger("ivyea.services.skill_architect")

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Runtime capability detection — injected into prompts so the model only
# promises what the current environment can actually deliver.
# ---------------------------------------------------------------------------

def _detect_capabilities() -> dict[str, bool]:
    """Return a snapshot of what the running environment supports."""
    from app.services.ai_synthesis_service import _deepseek_key, _apimart_key, has_vision_capability
    from app.services.runners import _find_bin
    return {
        "text_llm": bool(_deepseek_key() or _apimart_key()),
        "vision_llm": has_vision_capability(),
        "mcp_hermes": bool(_find_bin("hermes")),
    }


def _capabilities_block() -> str:
    from app.services.ai_synthesis_service import _vision_provider_chain
    caps = _detect_capabilities()
    vision_providers = _vision_provider_chain()
    vision_detail = "、".join(vision_providers) if vision_providers else "无"

    lines = ["【当前系统能力——生成 Skill 时必须严格遵守】"]
    lines.append(f"  · 文本生成（llm-only）：{'✓ 可用' if caps['text_llm'] else '✗ 不可用，禁止生成需要 LLM 的 Skill'}")
    lines.append(f"  · 图片/视觉分析（file 类型参数）：{'✓ 可用，已配置提供商：' + vision_detail if caps['vision_llm'] else '✗ 不可用（未配置 Apimart/OpenAI/assistant），禁止生成需要上传图片的 Skill'}")
    lines.append(f"  · 实时市场数据（mcp）：{'✓ 可用（hermes + sorftime MCP）' if caps['mcp_hermes'] else '✗ 不可用，禁止把 runtime 设为 mcp'}")
    lines.append("  ⚠ 如果某项能力不可用，你必须：①改用可用能力替代，或②在 clarifications 中告知用户此需求当前无法实现。禁止生成依赖不可用能力的 Skill。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shared knowledge injected into prompts
# ---------------------------------------------------------------------------

_MCP_TOOLS_DESC = """运行环境里有这些 MCP 工具可供该 Skill 被运行时使用（写步骤时可引用它们）：
  · Sorftime（`mcp_sorftime_*`）：关键词详情/趋势、商品报告/流量词/评论、类目报告等
  · SIF（`mcp_sif_*`）：关键词竞争、竞品关键词信号、流量异常
  · 卖家精灵 SellerSprite（`mcp_sellersprite_*`）：关键词流量、ASIN 关键词、竞品词分析
真实抓数据是 Skill **被运行时**才发生的事，由用户在工具页填入真实参数后触发。"""

_TOOL_KINDS = ("report", "transform", "lookup", "workflow")

# Input types the contract allows. `file` maps to a file-upload control in the
# visual layer and is only valid when vision_llm capability is available.
_ALLOWED_INPUT_TYPES = (
    "text", "textarea", "number", "select", "boolean",
    "asin", "marketplace", "keyword", "date", "file",
)

_IDENT_RE = re.compile(r"^[a-zA-Z_]\w*$")


# ---------------------------------------------------------------------------
# Editable prompt assets (STUDIO_ROOT/architect/*.md)
# ---------------------------------------------------------------------------

_STAGES = ("01_understand", "02_plan", "03_review", "04_optimize", "05_generate", "repair", "enrich")


_DEFAULT_PROMPTS: dict[str, str] = {
    "01_understand": """你是一位资深的需求分析师，擅长把模糊的一句话需求拆解清楚。
你现在的任务是**理解需求**，不是写 Skill，更不是执行任务——不要抓任何数据、不要调任何工具。

{{CAPABILITIES}}

{{MCP}}

用户的一句话需求：{{IDEA}}
目标分类：{{CATEGORY}}
{{REF}}

请深入理解这个需求，判断它想要的是哪一类工具，并输出一个 JSON 对象（只输出 JSON，不要解释）：
{
  "restated": "用你自己的话把需求复述清楚（含使用场景、目标用户、期望产出）",
  "tool_kind": "report | transform | lookup | workflow 之一",
  "runtime": "llm-only | mcp（需要调用 mcp_ 工具抓实时市场数据时才填 mcp，且必须确认 mcp_hermes 能力可用）",
  "needs_vision": false,
  "name": "建议的英文 skill 名（小写+连字符，符合 ^[a-z][a-z0-9_-]{1,63}$）",
  "category": "建议分类路径，如 amazon/listing",
  "icon": "一个贴切的 emoji",
  "description": "一句话英文描述",
  "description_zh": "一句话中文描述",
  "candidate_inputs": [{"name":"asin","label":"产品ASIN","type":"asin","required":true}],
  "output_format": "markdown | text | table",
  "clarifications": [
    {"question":"仅当需求真的有歧义或超出当前系统能力时才提；最多3条；没问题则给空数组",
     "options":["可选项A","可选项B"], "why":"为什么需要澄清"}
  ]
}

⚠ 若需求需要上传图片/视觉分析，必须先检查 vision_llm 能力是否可用，不可用则在 clarifications 中说明。
⚠ 若需求需要实时抓取市场数据，必须先检查 mcp_hermes 能力是否可用，不可用则 runtime 填 llm-only 并在步骤里说明。""",
    "02_plan": """你是一位 Skill 架构师。基于下面的需求理解，制定一份清晰、可执行的 Skill **方案**（还不是 SKILL.md）。

{{CAPABILITIES}}

{{MCP}}

需求理解（JSON）：
{{UNDERSTANDING}}

用户对澄清问题的回答：
{{CLARIFICATIONS}}

请输出一个 JSON 方案对象（只输出 JSON）：
{
  "name": "英文 skill 名（小写+连字符）",
  "category": "分类路径",
  "icon": "emoji",
  "description": "一句话英文描述",
  "description_zh": "一句话中文描述",
  "tool_kind": "report|transform|lookup|workflow",
  "runtime": "llm-only|mcp",
  "inputs": [
    {"name":"asin","label":"产品ASIN","type":"asin","required":true,"placeholder":"如 B0XXXXXXXX","default":"","options":[]}
  ],
  "steps": ["第1步：...","第2步：...（需要抓数据时写明调用哪个 mcp_ 工具；图片分析步骤写明使用 {{image}} 参数）"],
  "output_schema": "产出/报告的结构说明",
  "mcp_tools_used": [],
  "pitfalls": ["注意事项/易错点"]
}

inputs 的 name 必须是合法标识符；type 取值范围：text,textarea,number,select,boolean,asin,marketplace,keyword,date,file
（file 类型 = 图片/文件上传，只有 vision_llm 能力可用时才能用）
步骤要具体可执行，且只引用当前环境能力范围内的工具。""",
    "03_review": """你是一位严格的方案评审专家。请挑刺下面这份 Skill 方案，找出会导致"生成后跑不通/体验差"的问题。

{{CAPABILITIES}}

{{MCP}}

待评审方案（JSON）：
{{PLAN}}

重点检查：
①引用的 mcp_ 工具是否真实存在、参数是否对；
②inputs 是否齐全、type 是否合理（允许：text,textarea,number,select,boolean,asin,marketplace,keyword,date,file）；
③步骤是否可执行、有无缺口；
④runtime 判断：需要实时数据但 mcp_hermes 不可用 → 必须改 llm-only；需要图片但 vision_llm 不可用 → 必须标注无法实现；
⑤file 类型输入只有 vision_llm 可用时才合法；
⑥命名/分类/描述是否准确。

只输出 JSON：
{
  "issues": [{"severity":"high|medium|low","field":"inputs|steps|runtime|...","problem":"...","suggestion":"..."}],
  "must_fix": ["必须修复的关键问题（含能力越界问题）"],
  "score": 0
}""",
    "04_optimize": """你是 Skill 架构师。根据评审意见优化下面的方案，修复所有 must_fix 和 high 问题。

原方案（JSON）：
{{PLAN}}

评审意见（JSON）：
{{REVIEW}}

只输出优化后的**完整方案 JSON**（结构与原方案一致，不要解释）。""",
    "05_generate": """你是 Hermes Skill 编写专家。请把下面这份已定稿的方案，**渲染**成一份标准、可直接运行的 SKILL.md。

【重要】你是在"写说明书"，不是执行任务：不要现在抓数据、不要调工具。

{{MCP}}

定稿方案（JSON）：
{{PLAN}}

输出要求——严格生成一份 SKILL.md（从 --- 开始，只输出 SKILL.md 内容）：

1. YAML frontmatter（--- 包裹），必须包含：
   - name / description / description_zh / category / icon
   - inputs: 顶层数组，每项 {name,label,type,required,placeholder,default,options}
     · type 只能是：text | textarea | number | select | boolean | asin | marketplace | keyword | date | file
     · file 类型用于图片/文件上传，渲染层会生成文件选择器并自动 base64 编码
     · 不得使用 image、images、upload 等非标准类型
   - tool: 显式规格块，形如
       tool:
         kind: <report|transform|lookup|workflow>
         runtime: <llm-only|mcp>
         inputs: <同上 inputs>
         output: {format: <markdown|text|table>, persist: true, exportable: true}
         sample_params: {<每个非 file 的 input 给一个示例值>}

2. Markdown body：
   - 用途与使用场景
   - 编号步骤，每步用 {{参数名}} 引用 inputs（每个 input 至少被引用一次）
   - 输出/报告结构
   - 注意事项

只输出 SKILL.md 完整内容，不要加任何额外解释或代码块标记以外的文字。""",
    "repair": """你之前生成的 SKILL.md 没有通过校验。请**只修复**下列错误，保持其余内容不变，重新输出完整的 SKILL.md。

校验错误：
{{ERRORS}}

待修复的 SKILL.md：
{{SKILL_MD}}

修复要点提醒：frontmatter 必须有合法 name（^[a-z][a-z0-9_-]{1,63}$）、description、tool.kind（report/transform/lookup/workflow）、以及顶层 inputs 数组（每项有合法 name）。只输出修复后的完整 SKILL.md（从 --- 开始）。""",
    "enrich": """你是 Hermes Skill 工具化专家。下面是一份**文档型** Skill（只有说明文档，没有参数表单），
请把它「工具化」：补全 Tool Spec，让它在网页商店里变成一个带表单、可直接执行的可视化工具。

{{CAPABILITIES}}

{{MCP}}

待工具化的 SKILL.md：
{{SKILL_MD}}

要求——输出补全后的完整 SKILL.md（从 --- 开始，只输出 SKILL.md 内容）：

1. YAML frontmatter：
   - **保留**原有的 name、category（不得改名）；补全缺失的 description / description_zh / icon
   - 新增顶层 inputs 数组：从文档内容里提炼用户执行该 Skill 时真正需要提供的 2-5 个参数，
     每项 {name,label,type,required,placeholder,default,options}
     · type 只能是：text | textarea | number | select | boolean | asin | marketplace | keyword | date | file
     · file 类型只有 vision_llm 能力可用时才能用
     · 参数要贴合文档实际用途——宁少勿滥，一个 textarea 型的核心输入也完全可以
   - 新增 tool: 规格块：
       tool:
         kind: <report|transform|lookup|workflow，按文档用途判断>
         runtime: <llm-only|mcp；只有文档明确需要实时市场数据且 mcp_hermes 可用才填 mcp>
         inputs: <同上 inputs>
         output: {format: markdown, persist: true, exportable: true}
         sample_params: {<每个非 file 的 input 给一个示例值>}

2. Markdown body：**尽量保留原文档内容**，只做两件事：
   - 在开头补一小节「## 使用参数」，逐条说明每个 {{参数名}} 的用途
   - 在文档的执行流程/步骤处自然引用 {{参数名}}（每个 input 至少被引用一次）；
     若原文没有明确步骤，补一节简短的「## 执行步骤」把文档意图串成 2-4 步

只输出 SKILL.md 完整内容，不要任何解释。""",
}


def _prompts_dir() -> Path:
    return STUDIO_ROOT / "architect"


def _load_prompt(stage: str) -> str:
    """Read a stage prompt from disk, seeding the default on first access.

    Any read error falls back to the built-in default so a corrupted/edited
    file can never take the pipeline down.
    """
    default = _DEFAULT_PROMPTS[stage]
    path = _prompts_dir() / f"{stage}.md"
    try:
        if not path.exists():
            _prompts_dir().mkdir(parents=True, exist_ok=True)
            path.write_text(default, encoding="utf-8")
            return default
        text = path.read_text(encoding="utf-8").strip()
        return text or default
    except OSError:
        return default


def list_prompts() -> dict[str, str]:
    """Return every stage prompt (seeding defaults as needed)."""
    return {stage: _load_prompt(stage) for stage in _STAGES}


def save_prompt(stage: str, text: str) -> None:
    if stage not in _STAGES:
        raise ValueError(f"unknown stage: {stage}")
    _prompts_dir().mkdir(parents=True, exist_ok=True)
    (_prompts_dir() / f"{stage}.md").write_text(text or "", encoding="utf-8")


def _fill(template: str, mapping: dict[str, str]) -> str:
    """Replace only the known ``{{KEY}}`` tokens. Unknown braces (e.g. the
    ``{{param}}`` examples we want the model to emit) are left untouched."""
    out = template
    for key, val in mapping.items():
        out = out.replace("{{" + key + "}}", val)
    return out


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> Any:
    """Best-effort JSON extraction from an LLM response."""
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)```", t, re.DOTALL)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except Exception:
        logger.debug("json.loads 失败（旁路，已忽略）", exc_info=True)
    # Fall back to the outermost {...} span.
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(t[start:end + 1])
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"无法解析 JSON：{exc}；原文片段：{t[:300]}") from exc
    raise ValueError(f"响应中未找到 JSON：{t[:300]}")


def _extract_skill_md(text: str) -> str:
    t = (text or "").strip()
    # Models sometimes wrap the whole SKILL.md in a code fence with ANY language
    # tag (```yaml / ```markdown / ```md / ```). Strip a wrapping fence.
    if t.startswith("```"):
        m = re.match(r"```[a-zA-Z0-9]*[ \t]*\r?\n(.*?)\r?\n?```[ \t]*$", t, re.DOTALL)
        if m:
            t = m.group(1).strip()
    # Defensive: if there's leading prose before the frontmatter, trim to the
    # first standalone '---' line near the top so _parse_skill_md (anchored at
    # start) can find the frontmatter.
    if not t.startswith("---"):
        m2 = re.search(r"(?m)^---[ \t]*$", t)
        if m2 and m2.start() <= 200:
            t = t[m2.start():].strip()
    return t


# ---------------------------------------------------------------------------
# Stage runners (each one LLM call over the stable text provider chain)
# ---------------------------------------------------------------------------

async def _gen(prompt: str) -> str:
    return (await ai_synthesis_service.generate_text(prompt)).strip()


def _ref_context(ref_skill: str | None) -> str:
    if not ref_skill:
        return ""
    try:
        ref = skill_repo.get_skill(ref_skill)
        return f"\n参考 Skill（{ref_skill}）的结构片段：\n{ref.content_body[:1500]}"
    except Exception:
        return ""


async def understand(idea: str, category: str | None, ref_skill: str | None) -> dict:
    prompt = _fill(_load_prompt("01_understand"), {
        "CAPABILITIES": _capabilities_block(),
        "MCP": _MCP_TOOLS_DESC,
        "IDEA": idea,
        "CATEGORY": category or "自行判断最合适的分类",
        "REF": _ref_context(ref_skill),
    })
    data = _extract_json(await _gen(prompt))
    if not isinstance(data, dict):
        raise ValueError("理解阶段返回的不是 JSON 对象")
    return data


async def plan(understanding: dict, clarifications: dict[str, str] | None) -> dict:
    clar_text = "（无）"
    if clarifications:
        clar_text = "\n".join(f"- {q}：{a}" for q, a in clarifications.items() if a)
    prompt = _fill(_load_prompt("02_plan"), {
        "CAPABILITIES": _capabilities_block(),
        "MCP": _MCP_TOOLS_DESC,
        "UNDERSTANDING": json.dumps(understanding, ensure_ascii=False, indent=2),
        "CLARIFICATIONS": clar_text or "（无）",
    })
    data = _extract_json(await _gen(prompt))
    if not isinstance(data, dict):
        raise ValueError("制定方案阶段返回的不是 JSON 对象")
    return data


async def review(plan_obj: dict) -> dict:
    prompt = _fill(_load_prompt("03_review"), {
        "CAPABILITIES": _capabilities_block(),
        "MCP": _MCP_TOOLS_DESC,
        "PLAN": json.dumps(plan_obj, ensure_ascii=False, indent=2),
    })
    try:
        data = _extract_json(await _gen(prompt))
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001 — review is advisory; never block on it
        _log.warning("review stage failed, skipping: %s", exc)
        return {}


async def optimize(plan_obj: dict, review_obj: dict) -> dict:
    if not review_obj or not (review_obj.get("must_fix") or review_obj.get("issues")):
        return plan_obj
    prompt = _fill(_load_prompt("04_optimize"), {
        "PLAN": json.dumps(plan_obj, ensure_ascii=False, indent=2),
        "REVIEW": json.dumps(review_obj, ensure_ascii=False, indent=2),
    })
    try:
        data = _extract_json(await _gen(prompt))
        return data if isinstance(data, dict) and data.get("name") else plan_obj
    except Exception as exc:  # noqa: BLE001
        _log.warning("optimize stage failed, keeping original plan: %s", exc)
        return plan_obj


async def _render(plan_obj: dict) -> str:
    prompt = _fill(_load_prompt("05_generate"), {
        "MCP": _MCP_TOOLS_DESC,
        "PLAN": json.dumps(plan_obj, ensure_ascii=False, indent=2),
    })
    return _extract_skill_md(await _gen(prompt))


async def _repair(skill_md: str, errors: list[str]) -> str:
    prompt = _fill(_load_prompt("repair"), {
        "ERRORS": "\n".join(f"- {e}" for e in errors),
        "SKILL_MD": skill_md,
    })
    return _extract_skill_md(await _gen(prompt))


async def _enrich(skill_md: str) -> str:
    prompt = _fill(_load_prompt("enrich"), {
        "CAPABILITIES": _capabilities_block(),
        "MCP": _MCP_TOOLS_DESC,
        "SKILL_MD": skill_md,
    })
    return _extract_skill_md(await _gen(prompt))


async def enrich_and_validate(original_name: str, skill_md: str) -> dict:
    """工具化 a doc-style skill: derive a Tool Spec (inputs + tool block) while
    keeping the body content. Validate (+ repair up to twice), never rename.
    Returns the same review-first shape as the repair endpoint."""
    basename = original_name.rsplit("/", 1)[-1]
    md = await _enrich(skill_md)
    fm, body = skill_repo._parse_skill_md(md)
    fm = dict(fm or {})
    fm["name"] = basename                        # identity is not negotiable
    errors, warnings = validate_skill_md(fm, body)

    attempts = 0
    while errors and attempts < 2:
        attempts += 1
        md = await _repair(skill_repo._serialize_skill_md(fm, body), errors)
        fm, body = skill_repo._parse_skill_md(md)
        fm = dict(fm or {})
        fm["name"] = basename
        errors, warnings = validate_skill_md(fm, body)

    preview = skill_repo._serialize_skill_md(fm, body)
    return {
        "name": original_name,
        "frontmatter": fm,
        "body": body,
        "preview": preview,
        "validation": {"ok": not errors, "attempts": attempts,
                       "errors": errors, "warnings": warnings},
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_skill_md(fm: dict, body: str) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors trigger a repair pass; warnings are
    surfaced to the user but never block."""
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(fm, dict) or not fm:
        return (["SKILL.md frontmatter 缺失或无法解析"], warnings)

    name = fm.get("name")
    if not name or not skill_repo._SEGMENT_RE.match(str(name)):
        errors.append("frontmatter.name 缺失或不合法（需匹配 ^[a-z][a-z0-9_-]{1,63}$）")

    if not (str(fm.get("description") or "")).strip():
        errors.append("frontmatter.description 缺失")

    tool = fm.get("tool")
    if not isinstance(tool, dict):
        errors.append("frontmatter.tool 规格块缺失")
    elif tool.get("kind") not in _TOOL_KINDS:
        errors.append(f"tool.kind 缺失或非法（应为 {'/'.join(_TOOL_KINDS)} 之一）")

    inputs = fm.get("inputs")
    if inputs is None:
        errors.append("frontmatter.inputs 缺失（即使无参数也应为空数组）")
    elif not isinstance(inputs, list):
        errors.append("frontmatter.inputs 必须是数组")
    else:
        for i, item in enumerate(inputs):
            if not isinstance(item, dict) or not item.get("name"):
                errors.append(f"inputs[{i}] 缺少 name")
                continue
            nm = str(item["name"])
            if not _IDENT_RE.match(nm):
                errors.append(f"inputs[{i}].name '{nm}' 不是合法标识符")
            t = str(item.get("type") or "text")
            if t not in _ALLOWED_INPUT_TYPES:
                # Non-standard types like "image"/"images"/"upload" are a
                # contract violation — the visual layer can't render them.
                # Coerce to "file" if the name suggests an image, else "text".
                hint = nm.lower()
                coerced = "file" if any(k in hint for k in ("image", "img", "photo", "pic", "file", "upload")) else "text"
                errors.append(
                    f"inputs[{i}].type '{t}' 不是合法类型（已被修复为 '{coerced}'）；"
                    f"合法类型：{', '.join(_ALLOWED_INPUT_TYPES)}"
                )
                item["type"] = coerced  # fix in-place so repair pass inherits it
            if t == "file" and not _detect_capabilities().get("vision_llm"):
                warnings.append(f"inputs[{i}] 是 file 类型但当前环境无视觉模型（需在系统配置中配置 Apimart/OpenAI/assistant key）")
            if nm and t != "file" and ("{{" + nm + "}}") not in (body or ""):
                warnings.append(f"body 未用 {{{{{nm}}}}} 引用参数 {nm}")

    if not (str(fm.get("description_zh") or "")).strip():
        warnings.append("缺少 description_zh（中文描述）")
    if len((body or "").strip()) < 80:
        warnings.append("body 内容偏短，可能缺少执行步骤")

    return errors, warnings


# ---------------------------------------------------------------------------
# Finalisation
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40]
    return s if skill_repo._SEGMENT_RE.match(s) else "generated-skill"


def _finalize(fm: dict, body: str, plan_obj: dict) -> tuple[str, str | None, dict, str]:
    """Reconcile name/category between the rendered frontmatter and the plan,
    guaranteeing a valid, persistable result."""
    fm = dict(fm or {})

    name = str(fm.get("name") or plan_obj.get("name") or "").strip()
    if not name or not skill_repo._SEGMENT_RE.match(name):
        name = _slugify(plan_obj.get("name") or plan_obj.get("description_zh") or "")
    fm["name"] = name

    category = (fm.get("category") or plan_obj.get("category") or "").strip() or None
    if category:
        fm["category"] = category
    return name, category, fm, body


# ---------------------------------------------------------------------------
# Public orchestration
# ---------------------------------------------------------------------------

async def generate_and_validate(plan_obj: dict) -> dict:
    """Render + validate (+ repair up to twice). Returns a response dict shaped
    like the legacy GenerateFromIdeaResponse plus a ``validation`` block."""
    md = await _render(plan_obj)
    fm, body = skill_repo._parse_skill_md(md)
    errors, warnings = validate_skill_md(fm, body)

    attempts = 0
    while errors and attempts < 2:
        attempts += 1
        md = await _repair(md, errors)
        fm, body = skill_repo._parse_skill_md(md)
        errors, warnings = validate_skill_md(fm, body)

    name, category, fm, body = _finalize(fm, body, plan_obj)
    preview = skill_repo._serialize_skill_md(fm, body)

    return {
        "name": name,
        "category": category,
        "frontmatter": fm,
        "body": body,
        "preview": preview,
        "validation": {
            "ok": not errors,
            "attempts": attempts,
            "errors": errors,
            "warnings": warnings,
        },
    }


async def run_plan(
    idea: str,
    category: str | None,
    ref_skill: str | None,
    clarifications: dict[str, str] | None,
) -> dict:
    """Rigorous mode, phase 1: understand → (clarify?) → plan → review → optimize.

    Returns either {stage:"clarify", ...} when the idea is ambiguous and no
    answers were supplied, or {stage:"plan", plan, review}.
    """
    understanding = await understand(idea, category, ref_skill)
    clar = understanding.get("clarifications") or []
    if isinstance(clar, list) and clar and not clarifications:
        return {"stage": "clarify", "clarifications": clar, "understanding": understanding}

    plan_obj = await plan(understanding, clarifications)
    review_obj = await review(plan_obj)
    plan_obj = await optimize(plan_obj, review_obj)
    return {"stage": "plan", "plan": plan_obj, "review": review_obj}


async def run_oneshot(idea: str, category: str | None, ref_skill: str | None) -> dict:
    """Fast mode: run every stage end-to-end and return the generated skill."""
    understanding = await understand(idea, category, ref_skill)
    plan_obj = await plan(understanding, None)
    review_obj = await review(plan_obj)
    plan_obj = await optimize(plan_obj, review_obj)
    result = await generate_and_validate(plan_obj)
    result["plan"] = plan_obj
    return result
