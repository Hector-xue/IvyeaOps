"""Skill Tools router — auto-generated visual panels from Skill SKILL.md.

Endpoints:
  GET  /api/skill-tools/list   → all executable skills with parsed inputs schema
  POST /api/skill-tools/run    → execute a skill with user-provided params
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.security import require_user
from app.services import skill_repo

router = APIRouter(dependencies=[Depends(require_user)])


# ── Models ────────────────────────────────────────────────────────────────

class SkillToolMeta(BaseModel):
    """A skill exposed as an executable tool."""
    name: str
    category: str | None
    description: str | None
    description_zh: str | None = None
    icon: str = "⊞"
    inputs: list[dict[str, Any]] = Field(default_factory=list)
    has_execution: bool = False  # whether the skill has a clear execution flow
    pinned: bool = False         # pinned skills get their own sidebar entry
    # --- Tool Spec (from the frontmatter `tool:` block, when present) -------
    # These let the visual layer faithfully render the skill instead of
    # guessing. Absent on legacy skills → sensible defaults.
    kind: str | None = None          # report | transform | lookup | workflow
    runtime: str | None = None       # llm-only | mcp
    output_format: str = "markdown"  # markdown | text | table
    exportable: bool = False
    sample_params: dict[str, Any] = Field(default_factory=dict)


class SkillToolListResponse(BaseModel):
    tools: list[SkillToolMeta]
    categories: dict[str, int]  # category → count


class RunToolBody(BaseModel):
    skill_name: str = Field(..., description="skill name")
    params: dict[str, Any] = Field(default_factory=dict, description="user-provided parameters")


# ── Input schema parsing ─────────────────────────────────────────────────

def _parse_inputs_from_body(body: str) -> list[dict[str, Any]]:
    """Extract input definitions from SKILL.md body.

    Looks for patterns like:
      - `{{asin}}` or `{{asin:placeholder}}` template variables
      - Explicit `inputs:` YAML block in frontmatter (preferred)

    Returns list of {name, type, label, required, placeholder, default, options}.
    """
    inputs = []
    seen = set()

    # Pattern 1: {{var}} or {{var:default}} template variables
    for m in re.finditer(r'\{\{(\w+)(?::([^}]*))?\}\}', body):
        name = m.group(1)
        default = m.group(2) or ""
        if name in seen or name in ("end", "else", "endif"):
            continue
        seen.add(name)
        inputs.append({
            "name": name,
            "type": "text",
            "label": name.replace("_", " ").title(),
            "required": not bool(default),
            "placeholder": default or f"Enter {name}",
            "default": default,
        })

    return inputs


def _normalize_input_list(raw: Any) -> list[dict[str, Any]]:
    """Normalize a raw inputs list into the shape the frontend form expects."""
    if not isinstance(raw, list):
        return []
    inputs = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        inputs.append({
            "name": item.get("name", ""),
            "type": item.get("type", "text"),
            "label": item.get("label", item.get("name", "")),
            "required": bool(item.get("required", False)),
            "placeholder": item.get("placeholder", ""),
            "default": item.get("default", ""),
            "options": item.get("options", []),
        })
    return inputs


def _parse_inputs_from_frontmatter(fm: dict) -> list[dict[str, Any]]:
    """Parse inputs from the top-level frontmatter 'inputs' key if present."""
    return _normalize_input_list(fm.get("inputs"))


def _tool_block(fm: dict) -> dict:
    """Return the explicit `tool:` spec block, or {} if absent/malformed."""
    t = fm.get("tool")
    return t if isinstance(t, dict) else {}


def _resolve_inputs(fm: dict, body: str) -> list[dict[str, Any]]:
    """Authoritative input schema, in precedence order:
    explicit tool.inputs → top-level inputs → {{var}} scraped from the body.
    Reading the explicit spec first is what kills the old "form silently
    degrades to no-params" failure mode."""
    tool = _tool_block(fm)
    from_spec = _normalize_input_list(tool.get("inputs"))
    if from_spec:
        return from_spec
    from_fm = _parse_inputs_from_frontmatter(fm)
    if from_fm:
        return from_fm
    return _parse_inputs_from_body(body)


def _build_meta(detail, pinned: bool) -> "SkillToolMeta":
    """Build a SkillToolMeta from a SkillDetail, reading the Tool Spec block."""
    fm = detail.frontmatter
    tool = _tool_block(fm)
    inputs = _resolve_inputs(fm, detail.content_body)

    kind = tool.get("kind") or None
    runtime = tool.get("runtime") or None
    output = tool.get("output") if isinstance(tool.get("output"), dict) else {}
    output_format = str(output.get("format") or "markdown")
    exportable = bool(output.get("exportable", False))
    sample = tool.get("sample_params") if isinstance(tool.get("sample_params"), dict) else {}

    has_execution = bool(
        kind
        or fm.get("inputs")
        or re.search(r'\{\{\w+', detail.content_body)
        or "step" in detail.content_body.lower()[:500]
    )

    return SkillToolMeta(
        name=detail.name,
        category=detail.category,
        description=detail.description,
        description_zh=detail.description_zh,
        icon=_detect_icon(fm, detail.category),
        inputs=inputs,
        has_execution=has_execution,
        pinned=pinned,
        kind=kind,
        runtime=runtime,
        output_format=output_format,
        exportable=exportable,
        sample_params=sample,
    )


def _detect_icon(fm: dict, category: str | None) -> str:
    """Pick an icon based on category or frontmatter."""
    if fm.get("icon"):
        return str(fm["icon"])
    cat = (category or "").lower()
    if "amazon" in cat:
        return "◈"
    if "research" in cat:
        return "◎"
    if "creative" in cat:
        return "◇"
    if "devops" in cat or "software" in cat:
        return "⚙"
    if "data" in cat:
        return "▦"
    if "media" in cat:
        return "◉"
    if "mlops" in cat or "inference" in cat:
        return "▣"
    return "⊞"


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("/list", response_model=SkillToolListResponse)
def list_tools(
    category: str | None = None,
    q: str | None = None,
) -> SkillToolListResponse:
    """List all skills as executable tools, with parsed input schemas."""
    metas = skill_repo.list_skills()
    tools: list[SkillToolMeta] = []

    for m in metas:
        # Filter
        if category and (m.category or "") != category:
            continue
        if q:
            needle = q.lower()
            if (needle not in m.name.lower()
                and needle not in (m.description or "").lower()
                and needle not in (m.description_zh or "").lower()):
                continue

        # Load full detail to parse the Tool Spec / inputs schema.
        try:
            detail = skill_repo.get_skill(m.name)
        except Exception:
            continue

        tools.append(_build_meta(detail, bool(getattr(m, "pinned", False))))

    # Build category counts
    cats: dict[str, int] = {}
    for t in tools:
        key = t.category or "(uncategorized)"
        cats[key] = cats.get(key, 0) + 1

    return SkillToolListResponse(
        tools=tools,
        categories=dict(sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))),
    )


@router.get("/pinned", response_model=list[SkillToolMeta])
def list_pinned_tools() -> list[SkillToolMeta]:
    """Pinned skills only — drives the dynamic sidebar entries. Cheap: no body parse."""
    out: list[SkillToolMeta] = []
    for m in skill_repo.list_skills():
        if not getattr(m, "pinned", False):
            continue
        try:
            out.append(_build_meta(skill_repo.get_skill(m.name), True))
        except Exception:
            out.append(SkillToolMeta(
                name=m.name, category=m.category, description=m.description,
                description_zh=m.description_zh, icon="⊞", inputs=[],
                has_execution=True, pinned=True,
            ))
    return out


class PinBody(BaseModel):
    skill_name: str
    pinned: bool


@router.post("/pin", response_model=SkillToolMeta)
def pin_tool(body: PinBody) -> SkillToolMeta:
    """Pin/unpin a skill so it shows (or hides) as a dedicated sidebar tool."""
    try:
        skill_repo.set_pinned(body.skill_name, body.pinned)
        detail = skill_repo.get_skill(body.skill_name)
    except Exception as exc:
        raise HTTPException(404, f"Skill not found: {exc}")
    return _build_meta(detail, bool(body.pinned))


_ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


def _skill_prompt(skill_name: str, params: dict) -> str:
    """把「技能 + 用户填的参数」组织成一次任务指令。"""
    # `task` is the universal free-form input the store shows for skills without
    # a declared input schema — surface it as the task itself, not a mere param.
    task = str((params or {}).get("task") or "").strip()
    rest = {k: v for k, v in (params or {}).items() if k != "task" and v}
    params_section = "\n".join(f"- {k}: {v}" for k, v in rest.items())
    return (
        f"请执行 skill「{skill_name}」。\n\n"
        + (f"## 任务要求\n{task}\n\n" if task else "")
        + f"## 用户提供的参数\n{params_section or '（无额外参数）'}\n\n"
        + "按该 skill 定义的步骤执行并输出结果。"
    )


async def _run_skill_agent(skill_name: str, params: dict, skill_body: str):
    """用 ivyea-agent 执行一个技能，边跑边把正文流出去。

    两处和老实现（`hermes -z --skills <name> --yolo`）不一样，都是有意的：

    1. **说明书用 system 上下文注入，而不是 `--skills <name>`。**
       Skill 中心的技能库（data_dir/skills，SKILL.md + frontmatter）和 agent 自己的
       技能库（~/.ivyea/skills，skill.json + SKILL.md）不是一套东西，按 id 去
       agent 那边查是查不到的。所以直接把 SKILL.md 正文当说明书交给它。

    2. **默认只读，不复刻 `--yolo`。**
       `--yolo` 等于网页上点一个按钮，就让 agent 在无人值守的情况下随意写文件、
       执行命令。这条路上线以来没有任何真实运行记录（历史里只有测试产物），
       没有兼容包袱，所以直接按现在的安全标准来：分析、给方案、产出文本可以，
       要落地写入时它会把计划摆出来，由人去对应板块执行。
    """
    from app.services import ivyea_agent_service as agent_svc

    status = agent_svc.ensure_available()
    if not status.get("available"):
        yield ("error", f"IvyeaAgent 不可用：{status.get('error') or '服务未连接'}")
        return

    payload = {
        "message": _skill_prompt(skill_name, params),
        "system": (
            "[必须遵循的技能说明书]\n" + (skill_body or "").strip()
            + "\n\n要求：\n"
            "1. 说明书全文已在上面，**不要去文件系统里找这个 skill 的目录**——它不在你的技能库里，找不到是正常的，白费步数。\n"
            "2. 严格按上面的步骤执行；需要事实依据时调用工具取真实数据，不要凭空编造。\n"
            "3. 当前为只读模式：需要写文件或改线上数据时，把要做什么写清楚交给用户，不要尝试直接执行。"
        ),
        "plan_mode": True,          # 只读：写操作只出计划，不落地
        "persist": False,           # 技能执行有自己的历史存储，不占会话库
        "inject_retrieval": True,
    }

    loop = asyncio.get_running_loop()
    queue: "asyncio.Queue[tuple[str, str] | None]" = asyncio.Queue()

    def _pump() -> None:
        """在线程里消费阻塞式 SSE，把事件搬进 asyncio 队列。"""
        try:
            for event, data in agent_svc.chat_stream_events(payload):
                if event == "token":
                    text = str((data or {}).get("text") or "")
                    if text:
                        loop.call_soon_threadsafe(queue.put_nowait, ("ivyea-agent", text))
                elif event == "error":
                    detail = str((data or {}).get("detail") or (data or {}).get("error") or "执行失败")
                    loop.call_soon_threadsafe(queue.put_nowait, ("error", detail))
                    break
                elif event == "final":
                    # final.text 是规范化后的完整答案。流式已经把正文吐过一遍，
                    # 这里只在一个字都没吐出来时兜底（例如引证门把正文压到了收尾）。
                    loop.call_soon_threadsafe(queue.put_nowait, ("__final__", str((data or {}).get("text") or "")))
        except Exception as exc:  # noqa: BLE001
            loop.call_soon_threadsafe(queue.put_nowait, ("error", f"IvyeaAgent 执行失败：{exc}"))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    task = loop.run_in_executor(None, _pump)
    streamed = False
    final_text = ""
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            kind, text = item
            if kind == "__final__":
                final_text = text
                continue
            if kind == "error":
                yield ("error", text)
                return
            streamed = True
            yield (kind, text)
    finally:
        with contextlib.suppress(Exception):
            await task

    if not streamed:
        if final_text.strip():
            yield ("ivyea-agent", final_text)
        else:
            yield ("error", "技能执行无输出（模型未产出内容，可稍后重试）")


def _fill_params(body: str, params: dict) -> str:
    """Substitute {{name}} placeholders in the body with provided param values."""
    out = body
    for k, v in (params or {}).items():
        out = out.replace("{{" + str(k) + "}}", str(v))
    return out


async def _run_llm_only(detail, params: dict):
    """Execute a runtime=llm-only skill.

    If any param value is a base64 data-URI image, routes to Claude Vision via
    IvyeaAgent / 全局兜底 / DeepSeek. Otherwise uses the standard text chain.
    """
    from app.services import ai_synthesis_service

    # Separate image params from text params.
    images_b64 = [v for v in (params or {}).values()
                  if isinstance(v, str) and v.startswith("data:image/")]
    text_params = {k: v for k, v in (params or {}).items()
                   if not (isinstance(v, str) and v.startswith("data:"))}

    filled = _fill_params(detail.content_body, text_params)
    task = str((params or {}).get("task") or "").strip()
    params_section = "\n".join(
        f"- {k}: {'[图片已附]' if isinstance(v, str) and v.startswith('data:') else v}"
        for k, v in (params or {}).items() if v and k != "task"
    )
    prompt = (
        "你是一个严格按说明书执行任务的助手。请按下面这份 Skill 说明完成任务，"
        "直接输出最终结果（Markdown），不要解释你在做什么。\n\n"
        + (f"## 任务要求\n{task}\n\n" if task else "")
        + f"## 用户提供的参数\n{params_section or '（无额外参数）'}\n\n"
        + f"## Skill 说明\n{filled}\n"
    )

    if images_b64:
        async for prov, chunk in ai_synthesis_service.stream_vision(prompt, images_b64):
            yield prov, chunk
    else:
        async for prov, chunk in ai_synthesis_service.stream_text(prompt):
            yield prov, chunk


@router.post("/run")
async def run_tool(
    body: RunToolBody,
    user: str = Depends(require_user),
) -> StreamingResponse:
    """Execute a skill with user-provided parameters.

    Runtime routing (from the Tool Spec `tool.runtime`):
      · llm-only → stable embedded text chain (IvyeaAgent→全局兜底→DeepSeek), 纯文本
      · mcp / unset → ivyea-agent 带工具执行（说明书注入 system，默认只读）
    Every run is recorded to the lightweight history store.
    """
    from app.services import skill_runs

    try:
        detail = skill_repo.get_skill(body.skill_name)
    except Exception as exc:
        raise HTTPException(404, f"Skill not found: {exc}")

    tool = _tool_block(detail.frontmatter)
    runtime = str(tool.get("runtime") or "").lower()
    skill_basename = detail.name.rsplit("/", 1)[-1]

    async def generator():
        start = time.time()
        chunks: list[str] = []
        provider_used = ""
        err: str | None = None
        yield f'data: {json.dumps({"type": "phase", "phase": "executing"}, ensure_ascii=False)}\n\n'
        try:
            if runtime == "llm-only":
                gen = _run_llm_only(detail, body.params)
            else:
                gen = _run_skill_agent(skill_basename, body.params, detail.content_body)

            async for prov, chunk in gen:
                if prov == "error":
                    err = chunk
                    yield f'data: {json.dumps({"type": "error", "detail": chunk}, ensure_ascii=False)}\n\n'
                    break
                provider_used = prov
                chunks.append(chunk)
                yield f'data: {json.dumps({"type": "token", "text": chunk, "provider": prov}, ensure_ascii=False)}\n\n'

            elapsed = round(time.time() - start, 1)
            output = "".join(chunks)
            status = "error" if err else ("done" if output.strip() else "empty")

            run_id = None
            try:
                rec = skill_runs.record_run(
                    skill_name=detail.name, user=user, params=body.params,
                    output=output, provider=provider_used or (runtime or "ivyea-agent"),
                    runtime=runtime or "mcp", status=status,
                    started_at=start, elapsed_s=elapsed, error=err,
                )
                run_id = rec["id"]
            except Exception:
                pass

            if not err:
                yield f'data: {json.dumps({"type": "done", "provider": provider_used or "ivyea-agent", "elapsed_s": elapsed, "run_id": run_id}, ensure_ascii=False)}\n\n'
        except Exception as exc:
            yield f'data: {json.dumps({"type": "error", "detail": str(exc)}, ensure_ascii=False)}\n\n'
            try:
                skill_runs.record_run(
                    skill_name=detail.name, user=user, params=body.params,
                    output="".join(chunks), provider=provider_used or (runtime or "ivyea-agent"),
                    runtime=runtime or "mcp", status="error",
                    started_at=start, elapsed_s=round(time.time() - start, 1),
                    error=str(exc),
                )
            except Exception:
                pass

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Execution history ───────────────────────────────────────────────────────

@router.get("/runs")
def list_runs(skill_name: str, limit: int = 50) -> list[dict]:
    """List recent runs for a skill (newest first). skill_name in query because
    skill names contain forward slashes."""
    from app.services import skill_runs
    return skill_runs.list_runs(skill_name, limit=max(1, min(limit, 200)))


@router.get("/runs/{run_id}")
def get_run(run_id: str, skill_name: str) -> dict:
    from app.services import skill_runs
    rec = skill_runs.get_run(skill_name, run_id)
    if rec is None:
        raise HTTPException(404, "run not found")
    return rec


@router.delete("/runs/{run_id}")
def delete_run(run_id: str, skill_name: str) -> dict:
    from app.services import skill_runs
    return {"ok": skill_runs.delete_run(skill_name, run_id)}


# ── One-click 工具化 (propose a Tool Spec for a doc-style skill) ─────────────

class EnrichBody(BaseModel):
    skill_name: str


@router.post("/enrich")
async def enrich_tool(body: EnrichBody) -> dict:
    """Propose a Tool Spec (inputs + tool block) for a doc-style skill so it
    becomes a parameterized visual tool. Review-first: returns a preview, the
    frontend applies it via the skill update endpoint after user confirmation."""
    from app.services import skill_architect

    try:
        detail = skill_repo.get_skill(body.skill_name)
    except Exception as exc:
        raise HTTPException(404, f"Skill not found: {exc}")

    md = skill_repo._serialize_skill_md(dict(detail.frontmatter or {}), detail.content_body)
    try:
        return await skill_architect.enrich_and_validate(detail.name, md)
    except Exception as exc:
        raise HTTPException(502, f"AI 工具化失败: {exc}")


# ── One-click AI repair (propose, review-first — does NOT auto-write) ────────

class RepairBody(BaseModel):
    skill_name: str
    error: str = Field("", description="execution error to feed the repair model")


@router.post("/repair")
async def repair_tool(body: RepairBody) -> dict:
    """Propose a fixed SKILL.md for a tool that failed to run. Returns a preview
    for the user to review; it does NOT write — the frontend applies it via the
    skill update endpoint after the user confirms (审核制)."""
    from app.services import skill_architect

    try:
        detail = skill_repo.get_skill(body.skill_name)
    except Exception as exc:
        raise HTTPException(404, f"Skill not found: {exc}")

    fm0 = dict(detail.frontmatter or {})
    md = skill_repo._serialize_skill_md(fm0, detail.content_body)
    errors = [body.error] if body.error.strip() else ["执行报错，请修复该 Skill 使其能正常执行"]

    try:
        fixed = await skill_architect._repair(md, errors)
    except Exception as exc:
        raise HTTPException(502, f"AI 修复失败: {exc}")

    fm, bd = skill_repo._parse_skill_md(fixed)
    # Preserve identity: never let repair rename the skill.
    fm["name"] = fm0.get("name") or detail.name.rsplit("/", 1)[-1]
    verrs, vwarn = skill_architect.validate_skill_md(fm, bd)
    preview = skill_repo._serialize_skill_md(fm, bd)
    return {
        "name": detail.name,
        "frontmatter": fm,
        "body": bd,
        "preview": preview,
        "validation": {"ok": not verrs, "errors": verrs, "warnings": vwarn},
    }
