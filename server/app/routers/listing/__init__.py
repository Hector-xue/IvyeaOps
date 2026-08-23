"""Listing 工作台后端（包化重构）。

模块划分：
- common       共享基础（DB / 解析 / 产品上下文 / 白底检测 / 图片字节）
- ai           统一文本链 + 统一视觉链入口
- jobs         通用后台任务引擎（sqlite 持久化 + SSE 进度）
- projects     项目 CRUD / 产品信息 / 素材图 / 图片服务
- scrape       ASIN 采集（curl 直连 → 本机无头浏览器 → sorftime，全程零 Docker）
- analyze      AI 深度分析
- copywriting  文案管线（项目 job + 兼容 copy-jobs）
- visuals      套图策划 / 计划规范化 / 质检复核
- images       生图执行（提交 / 轮询 / 质检 / 自动重画 / 批量）
- export       成图导出（PSD 分层文件）

`router` 由 main.py 以 prefix="/api/listing" 挂载。下方 re-export 维持
ivyea_ops_tools（agent 桥接）与测试对旧单文件符号的引用不变。
"""
from fastapi import APIRouter

from . import analyze, copywriting, export, images, jobs, projects, scrape, visuals

router = APIRouter()
router.include_router(jobs.router)
router.include_router(projects.router)
router.include_router(scrape.router)
router.include_router(analyze.router)
router.include_router(copywriting.router)
router.include_router(visuals.router)
router.include_router(images.router)
router.include_router(export.router)

# ─── 兼容 re-export（agent 桥接 ivyea_ops_tools + 存量测试）─────────────────
from .common import (  # noqa: E402,F401
    _approved_copy, _build_product_context, _cached_white_product_source,
    _clean_text, _copy_source, _db, _detect_white_product_source,
    _fetch_image_bytes, _parse_copy_result, _reference_images, _strip_json,
    _white_background_score, _white_metrics_ready,
)
from .copywriting import (  # noqa: E402,F401
    CopyJobReq, create_copy_job, get_copy_job, list_copy_jobs, start_copy_job,
)
from .images import _parse_image_task_payload, generate_image_core  # noqa: E402,F401
from .projects import (  # noqa: E402,F401
    CreateProjectReq, create_project, get_project, list_projects,
)
from .scrape import _parse_amazon_html, _scrape_amazon_native, scrape  # noqa: E402,F401
from .common import IMAGES_DIR  # noqa: E402,F401
from .visuals import (  # noqa: E402,F401
    PlanImageSetReq, ReviewRenderReq, ReviewSetReq, _SHOT_TYPES,
    _auto_product_source, _bind_reference_templates, _creative_plan_quality,
    _fallback_product_visual_profile, _ground_render_prompt,
    _infer_layout_blueprint, _normalize_shot_plan, _persist_shot_plan,
    _readable_accent, _shot_plan_fallback, save_creative_set,
)
