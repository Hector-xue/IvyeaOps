"""Skill Studio HTTP API.

All endpoints require an authenticated session via ``require_user``. Path
components come in through ``{name:path}`` so forward slashes (e.g.
``research/arxiv``) travel transparently; every path is validated inside
the service layer before any filesystem access.

This file is intentionally thin — heavy lifting lives in
``app.services.*``. The router's job is request parsing and response
shaping only.
"""
from __future__ import annotations

import re
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.security import require_user
from app.services import skill_repo
from app.services import agent_skills
from app.services import snapshot as snapshot_svc
from app.services import git_import
from app.services import studio_audit
from app.services import trash as trash_svc


router = APIRouter(dependencies=[Depends(require_user)])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class StatsResponse(BaseModel):
    total_skills: int
    total_size_bytes: int
    categories: dict[str, int]         # category → count
    recently_edited: list[skill_repo.SkillMeta]


class ListResponse(BaseModel):
    skills: list[skill_repo.SkillMeta]
    total: int


class FileContentResponse(BaseModel):
    skill_name: str
    path: str                           # relative to the skill dir
    content: str
    size: int
    is_binary: bool


# ---------------------------------------------------------------------------
# GET /api/skill/stats
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=StatsResponse)
def get_stats() -> StatsResponse:
    """Overview: total count, bytes, category histogram, recently edited top 5."""
    metas = skill_repo.list_skills()
    total_bytes = sum(m.size_bytes for m in metas)

    cats: dict[str, int] = {}
    for m in metas:
        key = m.category or "(uncategorized)"
        cats[key] = cats.get(key, 0) + 1

    return StatsResponse(
        total_skills=len(metas),
        total_size_bytes=total_bytes,
        categories=dict(sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))),
        recently_edited=metas[:5],
    )


# ---------------------------------------------------------------------------
# GET /api/skill/list
# ---------------------------------------------------------------------------


@router.get("/list", response_model=ListResponse)
def list_skills_route(
    q: str | None = Query(None, description="filter by name substring (case-insensitive)"),
    category: str | None = Query(None, description="exact category match"),
    limit: int = Query(500, ge=1, le=2000),
) -> ListResponse:
    """List skills, optionally filtered. Ordering: most recently edited first."""
    metas = skill_repo.list_skills()

    if q:
        needle = q.lower()
        metas = [m for m in metas if needle in m.name.lower()
                 or (m.description and needle in m.description.lower())
                 or (m.description_zh and needle in m.description_zh.lower())]
    if category:
        metas = [m for m in metas if (m.category or "") == category]

    return ListResponse(skills=metas[:limit], total=len(metas))


# ---------------------------------------------------------------------------
# GET /api/skill/{name:path}
# ---------------------------------------------------------------------------


@router.get("/item/{name:path}", response_model=skill_repo.SkillDetail)
def get_skill_route(name: str) -> skill_repo.SkillDetail:
    """Fetch a single skill's full detail (frontmatter + body + linked files)."""
    return skill_repo.get_skill(name)


# ---------------------------------------------------------------------------
# GET /api/skill/file/{name:path}?path=references/notes.md
# ---------------------------------------------------------------------------

# Use a distinct /file/ prefix so the path param doesn't collide with /item/.
# Putting the relative file path in a query string keeps FastAPI's path
# grammar simple and lets us reuse validate_skill_name untouched.


_MAX_FILE_READ_BYTES = 2 * 1024 * 1024  # 2 MB — editor would choke anyway


@router.get("/file/{name:path}", response_model=FileContentResponse)
def get_skill_file(
    name: str,
    path: str = Query(..., description="path relative to the skill directory"),
) -> FileContentResponse:
    """Read a file inside a skill. Binary files return a placeholder body."""
    target = skill_repo._safe_path(name, path)
    if not target.is_file():
        raise HTTPException(404, f"file not found: {path}")

    try:
        size = target.stat().st_size
    except OSError as e:
        raise HTTPException(500, f"stat failed: {e}") from e

    if size > _MAX_FILE_READ_BYTES:
        raise HTTPException(
            413,
            f"file too large to open in editor: {size} bytes "
            f"(> {_MAX_FILE_READ_BYTES} limit)",
        )

    try:
        with target.open("rb") as f:
            raw = f.read()
    except OSError as e:
        raise HTTPException(500, f"read failed: {e}") from e

    is_binary = b"\x00" in raw[:8192]
    if is_binary:
        return FileContentResponse(
            skill_name=name,
            path=path,
            content="[binary file — not displayable]",
            size=size,
            is_binary=True,
        )
    return FileContentResponse(
        skill_name=name,
        path=path,
        content=raw.decode("utf-8", errors="replace"),
        size=size,
        is_binary=False,
    )


# ---------------------------------------------------------------------------
# Write endpoints
# ---------------------------------------------------------------------------


class CreateSkillBody(BaseModel):
    name: str = Field(..., description="forward-slash skill name, e.g. 'research/arxiv'")
    description: str | None = None
    body: str = Field("", description="SKILL.md body (below the frontmatter)")
    frontmatter_extras: dict[str, Any] | None = Field(
        None, description="optional extra frontmatter keys (name is always overridden)"
    )


class UpdateSkillBody(BaseModel):
    frontmatter: dict[str, Any] = Field(..., description="complete frontmatter dict to serialize")
    body: str = Field(..., description="SKILL.md body below the frontmatter")


class WriteFileBody(BaseModel):
    path: str = Field(..., description="relative path inside the skill dir")
    content: str = Field(..., description="UTF-8 file content (max 2 MB)")


class RenameBody(BaseModel):
    new_name: str = Field(..., description="target skill name (may include '/')")


class OkResponse(BaseModel):
    ok: bool = True


@router.post("/item", response_model=skill_repo.SkillMeta, status_code=201)
def create_skill_route(
    body: CreateSkillBody,
    user: str = Depends(require_user),
) -> skill_repo.SkillMeta:
    """Create a new skill. 409 if the name already exists."""
    meta = skill_repo.create_skill(
        name=body.name,
        description=body.description,
        body=body.body,
        frontmatter_extras=body.frontmatter_extras,
    )
    studio_audit.record(
        "skill.create", actor=user, skill_name=body.name,
        details={"description": body.description or ""},
    )
    return meta


@router.put("/item/{name:path}", response_model=skill_repo.SkillMeta)
def update_skill_route(
    name: str,
    body: UpdateSkillBody,
    user: str = Depends(require_user),
) -> skill_repo.SkillMeta:
    """Overwrite SKILL.md frontmatter + body. No implicit snapshot — the UI
    calls the snapshot endpoint explicitly when the user asks."""
    meta = skill_repo.update_skill_md(name, body.frontmatter, body.body)
    studio_audit.record(
        "skill.update", actor=user, skill_name=name,
        details={"body_bytes": len(body.body or "")},
    )
    return meta


@router.post("/item/{name:path}/rename", response_model=skill_repo.SkillMeta)
def rename_skill_route(
    name: str,
    body: RenameBody,
    user: str = Depends(require_user),
) -> skill_repo.SkillMeta:
    """Rename a skill dir (may also move across categories)."""
    meta = skill_repo.rename_skill(name, body.new_name)
    studio_audit.record(
        "skill.rename", actor=user, skill_name=name,
        details={"new_name": body.new_name},
    )
    return meta


@router.delete("/item/{name:path}", response_model=trash_svc.TrashEntry)
def delete_skill_route(
    name: str,
    user: str = Depends(require_user),
) -> trash_svc.TrashEntry:
    """Delete (trash) a skill. 7-day recoverable window."""
    entry = trash_svc.trash_skill(name)
    studio_audit.record(
        "skill.delete", actor=user, skill_name=name,
        details={"trash_id": entry.id},
    )
    return entry


# ---------------------------------------------------------------------------
# File write / delete (for references/templates/scripts)
# ---------------------------------------------------------------------------


@router.put("/file/{name:path}", response_model=skill_repo.FileEntry)
def write_skill_file(
    name: str,
    body: WriteFileBody,
    user: str = Depends(require_user),
) -> skill_repo.FileEntry:
    """Create or overwrite a file inside a skill (NOT SKILL.md)."""
    entry = skill_repo.write_file(name, body.path, body.content)
    studio_audit.record(
        "skill.file_write", actor=user, skill_name=name,
        details={"path": body.path, "size": entry.size},
    )
    return entry


@router.delete("/file/{name:path}", response_model=OkResponse)
def delete_skill_file(
    name: str,
    path: str = Query(..., description="relative path inside the skill dir"),
    user: str = Depends(require_user),
) -> OkResponse:
    """Delete a non-SKILL.md file inside a skill."""
    skill_repo.delete_file(name, path)
    studio_audit.record(
        "skill.file_delete", actor=user, skill_name=name,
        details={"path": path},
    )
    return OkResponse()


# ---------------------------------------------------------------------------
# Snapshots
#
# NOTE on URL shape: we cannot use ``/snapshot/{name:path}/{snapshot_id}/...``
# because the ``{name:path}`` converter is greedy and swallows the
# snapshot_id and action segments. So snapshot endpoints take ``name`` as
# a query parameter and keep the snapshot_id/action in the path instead.
# ---------------------------------------------------------------------------


class CreateSnapshotBody(BaseModel):
    name: str = Field(..., description="skill name")
    label: str | None = Field(None, description="optional human label")


@router.post("/snapshots", response_model=snapshot_svc.SnapshotMeta, status_code=201)
def create_snapshot_route(
    body: CreateSnapshotBody,
    user: str = Depends(require_user),
) -> snapshot_svc.SnapshotMeta:
    """Freeze the current state of a skill. Retention prune runs automatically."""
    meta = snapshot_svc.create_snapshot(body.name, label=body.label)
    studio_audit.record(
        "snapshot.create", actor=user, skill_name=body.name,
        details={"snapshot_id": meta.id, "label": meta.label},
    )
    return meta


@router.get("/snapshots", response_model=list[snapshot_svc.SnapshotMeta])
def list_snapshots_route(
    name: str = Query(..., description="skill name"),
) -> list[snapshot_svc.SnapshotMeta]:
    return snapshot_svc.list_snapshots(name)


@router.get(
    "/snapshots/{snapshot_id}/diff",
    response_model=snapshot_svc.SnapshotDiff,
)
def diff_snapshot_route(
    snapshot_id: str,
    name: str = Query(..., description="skill name"),
    file: str | None = Query(None, description="limit diff to a single file"),
) -> snapshot_svc.SnapshotDiff:
    return snapshot_svc.diff_snapshot(name, snapshot_id, only_path=file)


class RestoreSnapshotBody(BaseModel):
    name: str = Field(..., description="skill name")


@router.post("/snapshots/{snapshot_id}/restore", response_model=dict)
def restore_snapshot_route(
    snapshot_id: str,
    body: RestoreSnapshotBody,
    user: str = Depends(require_user),
) -> dict:
    """Roll the skill back to a snapshot. A pre-restore snapshot is taken
    first so the user can undo the undo."""
    result = snapshot_svc.restore_snapshot(
        body.name, snapshot_id, create_pre_restore_snapshot=True
    )
    studio_audit.record(
        "snapshot.restore", actor=user, skill_name=body.name,
        details=result,
    )
    return result


@router.delete("/snapshots/{snapshot_id}", response_model=OkResponse)
def delete_snapshot_route(
    snapshot_id: str,
    name: str = Query(..., description="skill name"),
    user: str = Depends(require_user),
) -> OkResponse:
    snapshot_svc.delete_snapshot(name, snapshot_id)
    studio_audit.record(
        "snapshot.delete", actor=user, skill_name=name,
        details={"snapshot_id": snapshot_id},
    )
    return OkResponse()


# ---------------------------------------------------------------------------
# GitHub import
# ---------------------------------------------------------------------------


@router.post(
    "/import/github",
    response_model=git_import.GitHubImportResult,
    status_code=201,
)
def import_github_route(
    body: git_import.GitHubImportRequest,
    user: str = Depends(require_user),
) -> git_import.GitHubImportResult:
    """Import a skill from a public GitHub repository via tarball."""
    result = git_import.import_from_github(body)
    studio_audit.record(
        "import.github", actor=user, skill_name=result.imported_name,
        details={
            "source_url": result.source_url,
            "branch": result.branch,
            "snapshot_id": result.snapshot_id,
        },
    )
    return result


# ---------------------------------------------------------------------------
# Trash (recycle bin)
# ---------------------------------------------------------------------------


class TrashRestoreBody(BaseModel):
    target_name: str | None = Field(
        None, description="optional new name if original is taken"
    )


@router.get("/trash", response_model=list[trash_svc.TrashEntry])
def list_trash_route() -> list[trash_svc.TrashEntry]:
    return trash_svc.list_trash()


@router.post("/trash/{trash_id}/restore", response_model=OkResponse)
def restore_trash_route(
    trash_id: str,
    body: TrashRestoreBody | None = None,
    user: str = Depends(require_user),
) -> OkResponse:
    target = body.target_name if body else None
    restored = trash_svc.restore_from_trash(trash_id, target_name=target)
    studio_audit.record(
        "skill.restore_trash", actor=user, skill_name=restored,
        details={"trash_id": trash_id},
    )
    return OkResponse()


@router.delete("/trash/{trash_id}", response_model=OkResponse)
def delete_trash_route(
    trash_id: str,
    user: str = Depends(require_user),
) -> OkResponse:
    trash_svc.delete_permanently(trash_id)
    studio_audit.record(
        "trash.purge", actor=user, skill_name=None,
        details={"trash_id": trash_id, "scope": "single"},
    )
    return OkResponse()


# ---------------------------------------------------------------------------
# Audit log tail
# ---------------------------------------------------------------------------


@router.get("/audit", response_model=list[dict])
def audit_tail_route(
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    return studio_audit.tail(limit=limit)


# ---------------------------------------------------------------------------
# Studio settings
# ---------------------------------------------------------------------------


import json
from app.core.skill_paths import SETTINGS_FILE

logger = logging.getLogger("ivyea.routers.skill")


_DEFAULT_SETTINGS: dict[str, Any] = {
    "snapshot_retention": 20,          # keep N per-skill snapshots
    "trash_ttl_days": 7,
    "autosave_debounce_ms": 600,
    "theme": "dark",
}


def _load_settings() -> dict[str, Any]:
    if not SETTINGS_FILE.is_file():
        return dict(_DEFAULT_SETTINGS)
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(_DEFAULT_SETTINGS)
    merged = dict(_DEFAULT_SETTINGS)
    if isinstance(data, dict):
        merged.update({k: v for k, v in data.items() if k in _DEFAULT_SETTINGS})
    return merged


def _save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    merged = _load_settings()
    # Only accept known keys to keep the surface area small and prevent
    # arbitrary-write attempts via JSON injection.
    for k, v in (payload or {}).items():
        if k not in _DEFAULT_SETTINGS:
            continue
        merged[k] = v
    SETTINGS_FILE.write_text(
        json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8"
    )
    return merged


@router.get("/settings", response_model=dict)
def get_settings_route() -> dict:
    return _load_settings()


class UpdateSettingsBody(BaseModel):
    snapshot_retention: int | None = Field(None, ge=1, le=200)
    trash_ttl_days: int | None = Field(None, ge=1, le=365)
    autosave_debounce_ms: int | None = Field(None, ge=100, le=10_000)
    theme: str | None = Field(None, pattern=r"^(dark|light)$")


@router.put("/settings", response_model=dict)
def put_settings_route(
    body: UpdateSettingsBody,
    user: str = Depends(require_user),
) -> dict:
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    merged = _save_settings(payload)
    studio_audit.record(
        "settings.update", actor=user, skill_name=None,
        details={"keys": list(payload.keys())},
    )
    return merged


# ---------------------------------------------------------------------------
# Generate Skill from Idea (AI-powered)
# ---------------------------------------------------------------------------

class GenerateFromIdeaBody(BaseModel):
    idea: str = Field(..., min_length=2, description="一句话描述你的想法")
    category: str | None = Field(None, description="目标分类，如 amazon/research")
    ref_skill: str | None = Field(None, description="参考 Skill 名称（可选）")


class GenerateFromIdeaResponse(BaseModel):
    name: str
    category: str | None
    frontmatter: dict
    body: str
    preview: str  # full SKILL.md content for preview


@router.post("/generate-from-idea", response_model=GenerateFromIdeaResponse)
async def generate_from_idea(
    body: GenerateFromIdeaBody,
    user: str = Depends(require_user),
) -> GenerateFromIdeaResponse:
    """Use AI to generate a complete SKILL.md from a one-sentence idea."""
    from app.services import ai_synthesis_service

    ref_context = ""
    if body.ref_skill:
        try:
            ref = skill_repo.get_skill(body.ref_skill)
            ref_context = f"\n\n参考 Skill（{body.ref_skill}）的结构：\n---\n{ref.content_body[:2000]}\n---"
        except Exception:
            logger.debug("skill_repo.get_skill 失败（旁路，已忽略）", exc_info=True)

    prompt = f"""你是一位 Hermes Skill 编写专家。用户描述了一个需求，请帮他**编写**一份完整的 SKILL.md。

【重要】你现在的任务是"写说明书"，不是"执行任务"。
- 不要现在去抓取任何数据、不要调用任何工具、不要要求用户提供真实 ASIN 或关键词。
- 你只需写出这份 SKILL.md 文档——它描述这个 Skill 将来被运行时该怎么做。
- 运行环境里有这些 MCP 工具可供该 Skill 使用（写步骤时可引用它们）：
  · Sorftime（`mcp_sorftime_*`）：关键词详情/趋势、商品报告/流量词/评论、类目报告等
  · SIF（`mcp_sif_*`）：关键词竞争、竞品关键词信号、流量异常
  · 卖家精灵 SellerSprite（`mcp_sellersprite_*`）：关键词流量、ASIN 关键词、竞品词分析
  真实抓数据是 Skill **被运行时**才发生的事，由用户在工具页填入真实参数后触发。

用户需求：{body.idea}
{f"目标分类：{body.category}" if body.category else "请自行判断最合适的分类。"}
{ref_context}

请生成一个标准的 Hermes SKILL.md，包含：

1. YAML frontmatter（--- 包裹）：
   - name: skill 名称（小写+连字符）
   - description: 一句话英文描述
   - description_zh: 一句话中文描述
   - category: 分类路径
   - icon: 一个合适的 emoji 图标
   - inputs: 该 Skill 运行时需要用户填的参数（数组，每项含 name/label/type/required），
     例如抓评论类通常需要 asin、marketplace 等。

2. Markdown body：
   - 简要说明这个 Skill 的用途和使用场景
   - 具体步骤（numbered steps）：明确每步该调哪个 MCP 工具、传什么参数（用 {{{{param}}}} 引用 inputs）
   - 输出/报告的结构
   - 注意事项 / pitfalls

只输出 SKILL.md 的完整内容（从 --- 开始），不要加其他解释。"""

    # Plain text-only generation: we want the model to WRITE a SKILL.md, not
    # execute it. synthesize_native() would inject sorftime tool-calling and
    # try to fetch market data — wrong for authoring. generate_text has no tools.
    try:
        full_text = (await ai_synthesis_service.generate_text(prompt)).strip()
    except Exception as exc:
        raise HTTPException(502, f"AI 生成失败: {exc}")
    if not full_text:
        raise HTTPException(502, "AI 生成失败: 返回空内容")

    # Extract SKILL.md from markdown code block if wrapped
    if "```" in full_text:
        m = re.search(r'```(?:markdown|md)?\s*\n(.*?)```', full_text, re.DOTALL)
        if m:
            full_text = m.group(1).strip()

    # Parse the generated SKILL.md
    fm, body_text = skill_repo._parse_skill_md(full_text)

    # Derive name
    name = fm.get("name", "")
    if not name:
        # Generate from idea
        name = re.sub(r'[^a-z0-9]+', '-', body.idea.lower())[:40].strip('-')
        if not name:
            name = "generated-skill"
    # Ensure valid segment
    if not skill_repo._SEGMENT_RE.match(name):
        name = re.sub(r'[^a-z0-9_-]', '', name.lower())[:40]
        if not name or not skill_repo._SEGMENT_RE.match(name):
            name = "generated-skill"

    category = body.category or fm.get("category", "")
    # Fallback auto-categorization when neither the user nor the AI set one:
    # map keywords in the idea to a sensible top-level category so the skill
    # doesn't land in "(未分类)".
    if not category:
        idea_l = body.idea.lower()
        _CAT_RULES = [
            ("amazon/reviews",   ["评论", "review", "差评", "好评", "口碑"]),
            ("amazon/ads",       ["广告", "投放", "ad ", "ppc", "acos", "竞价", "出价"]),
            ("amazon/listing",   ["listing", "标题", "五点", "描述", "文案", "a+", "卖点"]),
            ("amazon/keywords",  ["关键词", "keyword", "搜索量", "长尾"]),
            ("amazon/competitor",["竞品", "对手", "competitor", "对比"]),
            ("amazon/market",    ["市场", "选品", "类目", "趋势", "market", "调研"]),
            ("amazon",           ["asin", "亚马逊", "amazon", "产品", "商品"]),
        ]
        for cat, kws in _CAT_RULES:
            if any(k in idea_l for k in kws):
                category = cat
                break

    # Keep frontmatter's category in sync so the saved SKILL.md carries it.
    if category:
        fm["category"] = category

    return GenerateFromIdeaResponse(
        name=name,
        category=category or None,
        frontmatter=fm,
        body=body_text,
        preview=full_text,
    )


# ---------------------------------------------------------------------------
# Skill Architect — rigorous multi-stage generation pipeline
#
# Replaces the single-shot generate_from_idea above with a staged pipeline
# (understand → plan → review → optimize → render → validate+repair). Heavy
# lifting lives in app.services.skill_architect; this is request shaping only.
# ---------------------------------------------------------------------------


class ArchitectValidation(BaseModel):
    ok: bool
    attempts: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ArchitectGeneratedResponse(GenerateFromIdeaResponse):
    """Generated skill plus self-check results."""
    validation: ArchitectValidation
    plan: dict | None = None


class ArchitectPlanBody(BaseModel):
    idea: str = Field(..., min_length=2, description="一句话描述你的想法")
    category: str | None = None
    ref_skill: str | None = None
    clarifications: dict[str, str] | None = Field(
        None, description="用户对澄清问题的回答：{question: answer}"
    )


class ArchitectPlanResponse(BaseModel):
    stage: str                       # "clarify" | "plan"
    clarifications: list[dict] | None = None
    understanding: dict | None = None
    plan: dict | None = None
    review: dict | None = None


class ArchitectGenerateBody(BaseModel):
    plan: dict = Field(..., description="确认后的方案对象")


class ArchitectOneshotBody(BaseModel):
    idea: str = Field(..., min_length=2)
    category: str | None = None
    ref_skill: str | None = None


@router.post("/architect/plan", response_model=ArchitectPlanResponse)
async def architect_plan(body: ArchitectPlanBody) -> ArchitectPlanResponse:
    """Rigorous mode phase 1: understand → (clarify?) → plan → review → optimize."""
    from app.services import skill_architect
    try:
        result = await skill_architect.run_plan(
            body.idea.strip(), body.category or None,
            body.ref_skill or None, body.clarifications,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"方案生成失败: {exc}")
    return ArchitectPlanResponse(**result)


@router.post("/architect/generate", response_model=ArchitectGeneratedResponse)
async def architect_generate(body: ArchitectGenerateBody) -> ArchitectGeneratedResponse:
    """Rigorous mode phase 2: render the confirmed plan into a validated SKILL.md."""
    from app.services import skill_architect
    if not body.plan or not isinstance(body.plan, dict):
        raise HTTPException(400, "plan 不能为空")
    try:
        result = await skill_architect.generate_and_validate(body.plan)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"生成失败: {exc}")
    return ArchitectGeneratedResponse(**result)


@router.post("/architect/oneshot", response_model=ArchitectGeneratedResponse)
async def architect_oneshot(body: ArchitectOneshotBody) -> ArchitectGeneratedResponse:
    """Fast mode: run the whole pipeline end-to-end in one request."""
    from app.services import skill_architect
    try:
        result = await skill_architect.run_oneshot(
            body.idea.strip(), body.category or None, body.ref_skill or None,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"生成失败: {exc}")
    return ArchitectGeneratedResponse(**result)


@router.get("/architect/prompts", response_model=dict)
def architect_prompts() -> dict:
    """Return the editable stage prompts (seeding defaults on first access)."""
    from app.services import skill_architect
    return skill_architect.list_prompts()


# ---------------------------------------------------------------------------
# Agent 技能库同步
# ---------------------------------------------------------------------------


@router.get("/agent-sync", response_model=dict)
def agent_skills_status() -> dict:
    """技能库有没有挂给 IvyeaAgent（挂上的技能任务台才能自动匹配到）。"""
    return agent_skills.status()


@router.post("/agent-sync", response_model=dict)
def agent_skills_register(user: str = Depends(require_user)) -> dict:
    """重新把技能库挂给 IvyeaAgent。

    正常情况下开机自动挂好，**技能改完立即生效、不需要同步**。这个入口是给
    "手工动过 agent 的 settings.json"之后重新挂上用的，幂等。
    """
    res = agent_skills.register_roots()
    studio_audit.record(
        "skill.agent_mount", actor=user, skill_name="",
        details={"changed": res.get("changed"), "roots": res.get("roots")},
    )
    return {**res, **agent_skills.status()}
