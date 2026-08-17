"""IvyeaOps FastAPI backend entry point."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

# Central logging config lives in app.core.obs: level + format + 落盘 + request_id。
# 级别用 IVYEA_OPS_LOG_LEVEL 覆盖，落盘用 IVYEA_OPS_LOG_FILE=0 关掉。
from app.core.obs import configure_logging  # noqa: E402

configure_logging()

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core import obs
from app.core.config import settings
from app.core.security import require_admin, require_module
from app.core.skill_paths import (
    SKILLS_ROOT,
    ensure_studio_dirs,
    studio_paths_summary,
)
from app.routers import ad_audit, amazon, auth, brain, health, ivyea_agent, monitor, news, skill, terminal
from app.routers import listing as listing_router
from app.routers import image_translate as image_translate_router
from app.routers import market as market_router
from app.routers import playbook as playbook_router
from app.routers import home as home_router
from app.routers import schedules as schedules_router
from app.routers import assistant as assistant_router
from app.routers import help as help_router
from app.routers import hub_settings as hub_settings_router
from app.routers import projects as projects_router
from app.routers import git as git_router
from app.routers import setup as setup_router
from app.routers import freight as freight_router
from app.routers import deep_analysis as deep_analysis_router
from app.routers import skill_tools as skill_tools_router
from app.routers import autofix as autofix_router
from app.routers import lingxing as lingxing_router
from app.routers import skill_market as skill_market_router
from app.routers import notify as notify_router
from app.routers import mcp_server as mcp_server_router
from app.routers import mcp_tokens as mcp_tokens_router
from app.agents.router import api_router as agents_api_router, ws_router as agents_ws_router

logger = logging.getLogger("ivyea.main")


# Methods that can mutate state; anything not in this set is exempt from the
# Origin check (GET/HEAD/OPTIONS are considered safe per RFC 9110 §9.2.1).
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    # 测试里把后台任务关掉。它们（探活各 agent 二进制、起终端采集、拉起几个
    # 调度循环）跟被测逻辑无关，却占掉每个测试 setup 的大半时间 —— app/tests 有
    # 224 个测试，每个都完整跑一遍 lifespan。**建表仍然照做**，否则测试会撞
    # "no such table"。
    skip_bg = os.environ.get("IVYEA_OPS_SKIP_STARTUP_TASKS", "").lower() in {"1", "true", "yes"}

    # Rebuild runner-side config from the authoritative IvyeaOps settings on
    # every boot. This is required on a fresh Windows installation where the
    # key may already exist in hub settings but ~/.hermes/config.yaml has not
    # yet been created for that OS user.
    try:
        from app.core import hub_settings as _hub_settings
        from app.services.hermes_config_sync import on_settings_saved as _sync_runner_settings
        _sync_runner_settings(_hub_settings.load())
    except Exception as e:
        logger.warning("runner settings sync skipped: %s", e)

    # Skill Studio directories: we provision our own state dir
    # (~/.hermes/skill-studio/) but intentionally NEVER touch SKILLS_ROOT —
    # that's Hermes' territory. If it doesn't exist we just warn; the Skill
    # Studio API will surface a clear error on first call.
    ensure_studio_dirs()
    if not SKILLS_ROOT.exists():
        logger.warning("skills root missing: %s", SKILLS_ROOT)
    for key, value in studio_paths_summary().items():
        logger.info("%s: %s", key, value)

    # Best-effort: sweep expired trash entries on startup. Failure here must
    # never block the server from coming up — the API will retry on demand.
    try:
        from app.services.trash import purge_expired
        purged = purge_expired()
        if purged:
            logger.info("purged %s expired trash entries", purged)
    except Exception as e:
        logger.warning("trash purge skipped: %s", e)

    # Best-effort: 把 Skill 中心的技能库挂给 IvyeaAgent（见 services/agent_skills.py），
    # 任务台才匹配得到这些技能。**是挂目录不是复制**，Skill 中心里改完立即生效。
    # 幂等；失败绝不能拦住启动 —— 大不了这轮少几个可匹配的技能。
    try:
        from app.services.agent_skills import register_roots
        res = register_roots()
        logger.info("skill roots → agent: %s (changed=%s)",
                    ", ".join(res.get("roots") or []) or "(none)", res.get("changed"))
    except Exception as e:
        logger.warning("skill roots mount skipped: %s", e)

    # Best-effort: sweep expired ASIN audit artifacts (30-day retention).
    try:
        from app.services.asin_audit import sweep_expired as _sweep_audits
        n = _sweep_audits()
        if n:
            logger.info("purged %s expired audit dirs", n)
    except Exception as e:
        logger.warning("audit sweep skipped: %s", e)

    # Rescue ghost "running" jobs left behind by a prior crash/restart:
    # _live_jobs is empty on boot, so anything status=running on disk is stale.
    try:
        from app.services.asin_audit import sweep_stale_running
        n = sweep_stale_running()
        if n:
            logger.warning("marked %s stale running jobs as failed", n)
    except Exception as e:
        logger.warning("stale running sweep skipped: %s", e)

    # Same pair of sweeps for ad-audit jobs.
    try:
        from app.services.ad_audit import sweep_expired as _sweep_ad
        n = _sweep_ad()
        if n:
            logger.info("purged %s expired ad-audit dirs", n)
    except Exception as e:
        logger.warning("ad-audit expired sweep skipped: %s", e)

    try:
        from app.services.ad_audit import sweep_stale_running as _sweep_ad_stale
        n = _sweep_ad_stale()
        if n:
            logger.warning("marked %s stale ad-audit jobs as failed", n)
    except Exception as e:
        logger.warning("ad-audit stale sweep skipped: %s", e)

    # Best-effort: when IvyeaOps boots on a new version, refresh the bundled
    # IvyeaAgent once in the background so the two stay in sync (skipped for
    # editable/source installs and when auto-start is off).
    try:
        from app.services.ivyea_agent_service import maybe_sync_agent_on_upgrade
        maybe_sync_agent_on_upgrade()
    except Exception as e:
        logger.warning("agent auto-sync skipped: %s", e)

    # Market research history DB.
    try:
        from app.routers.market import _init_history_db as _init_market_hist
        _init_market_hist()
        logger.info("market history DB ready")
    except Exception as e:
        logger.warning("market history DB init skipped: %s", e)

    # Agents native backend: ensure its metadata tables exist (no-op against
    # the live ~/.agents/auth.db the old Node service shared).
    try:
        from app.agents.db import init_db as _init_agents_db
        _init_agents_db()
        logger.info("agents DB ready")
    except Exception as e:
        logger.warning("agents DB init skipped: %s", e)

    # Launch-playbook history DB.
    try:
        from app.routers.playbook import _init_history_db as _init_playbook_hist
        _init_playbook_hist()
        logger.info("playbook history DB ready")
    except Exception as e:
        logger.warning("playbook history DB init skipped: %s", e)

    # Home monitor (watchlist + snapshots) DB.
    try:
        from app.routers.home import _init_db as _init_home_db
        _init_home_db()
        logger.info("home monitor DB ready")
    except Exception as e:
        logger.warning("home monitor DB init skipped: %s", e)

    # Registered-users DB (multi-user mode).
    try:
        from app.services import users_service
        users_service.init_db()
        logger.info("users DB ready")
    except Exception as e:
        logger.warning("users DB init skipped: %s", e)

    # 统一审计流水（谁在哪个板块做了什么）。
    try:
        from app.core import audit
        audit.init_db()
        logger.info("audit log DB ready")
    except Exception as e:
        logger.warning("audit log DB init skipped: %s", e)

    # 对外 MCP 的令牌表。
    try:
        from app.services import mcp_tokens as _mcp_tokens
        _mcp_tokens.init_db()
        logger.info("MCP tokens DB ready")
    except Exception as e:
        logger.warning("MCP tokens DB init skipped: %s", e)

    # 任务账本 + 开机自愈。被上一次重启打断的任务：可重入的重新排队，不可重入的
    # 标成 orphaned 留在列表里等人处理 —— **绝不静默改成 failed**，
    # 那会让用户以为任务是自己跑挂的。
    try:
        from app.core import jobs
        jobs.init_db()
        healed = jobs.recover_orphans()
        jobs.purge(older_than_days=30)
        logger.info("jobs DB ready（重排队 %d、孤儿 %d）",
                    healed["requeued"], healed["orphaned"])
    except Exception as e:
        logger.warning("jobs DB init skipped: %s", e)

    # Brain chat/upload metadata DB is local SQLite; initialize eagerly so
    # schema problems are visible at boot, while keeping the service lightweight.
    try:
        from app.services.brain_chat_service import init_db as _init_brain_chat
        _init_brain_chat()
        logger.info("brain chat DB ready")
    except Exception as e:
        logger.warning("brain chat DB init skipped: %s", e)

    # Multi-agent hub: schema + agent discovery + PTY reaper.  All best-effort
    # so a misconfigured agent (e.g. missing binary) never blocks server boot.
    try:
        from app.services import agent_session_service as _agent_db
        from app.services import agent_registry as _agent_reg
        from app.services.pty_manager import manager as _pty_mgr

        _agent_db.init_db()
        # 任务台的会话索引（归属 / 工作区 / 自定义标题）；正文仍在 agent 那边。
        from app.services import console_sessions as _console_sessions
        _console_sessions.init_db()
        from app.services import schedules as _schedules
        _schedules.init_db()
        if not skip_bg:
            agents = _agent_reg.discover_agents()
            ok = sum(1 for a in agents if a.get("enabled"))
            logger.info("agent registry: %s/%s enabled", ok, len(agents))
            _pty_mgr.start_background_tasks()
    except Exception as e:
        logger.warning("agent hub init skipped: %s", e)

    logger.info("starting on %s:%s", settings.host, settings.port)
    logger.info("data dir: %s", settings.data_dir)
    logger.info("dev_mode: %s", settings.dev_mode)

    # Terminal subsystem: live multi-terminal session manager.
    #
    # 「会话内容快照」连同它每 5 分钟一次的后台自动采集已于 2026-08-17 移除 ——
    # 终端会话的留存归「外部智能体」板块管，这里再存一份 tmux 面板截图既重复又没人看。
    try:
        terminal.init_live_sessions()
        logger.info("live terminal sessions ready")
    except Exception as e:
        logger.warning("live terminal init skipped: %s", e)

    # systemd integration: announce READY and start the watchdog ping
    # loop. Both are no-ops when running outside systemd (NOTIFY_SOCKET
    # / WATCHDOG_USEC absent), so dev workflows are unaffected.
    from app.services.watchdog import notify_ready, notify_status, watchdog_loop
    notify_ready()
    notify_status("ready")
    _watchdog_task = None if skip_bg else asyncio.create_task(watchdog_loop(), name="sd-watchdog")

    # Home market-traffic daily recorder: wakes every 30 min and records a
    # daily point for each tracked baseline / watched ASIN that lacks one.
    # Best-effort, never blocks boot or shutdown.
    async def _market_daily_loop():
        while True:
            try:
                from app.routers.home import run_due_recordings
                summary = await run_due_recordings()
                if summary.get("recorded_market") or summary.get("recorded_asin"):
                    logger.info("market recorder: %s", summary)
            except Exception as e:
                logger.warning("market recorder error: %s", e)
            await asyncio.sleep(1800)

    _market_task = None if skip_bg else asyncio.create_task(_market_daily_loop(), name="market-recorder")

    # Token-usage archiver: snapshot each tool's token data into IvyeaOps's own
    # DB once a day so history survives even after a tool is uninstalled.
    # Runs once shortly after boot, then every 24h. Best-effort.
    try:
        from app.services import token_archive
        token_archive.init_db()
        logger.info("token archive DB ready")
    except Exception as e:
        logger.warning("token archive init skipped: %s", e)

    try:
        from app.services import lingxing_service
        lingxing_service.init_db()
        logger.info("lingxing audit DB ready")
    except Exception as e:
        logger.warning("lingxing audit init skipped: %s", e)

    async def _token_archive_loop():
        await asyncio.sleep(120)  # let boot settle before first snapshot
        while True:
            try:
                from app.services import token_archive
                summary = await asyncio.to_thread(token_archive.archive_run, 7)
                logger.info("token archive: %s", summary)
            except Exception as e:
                logger.warning("token archive error: %s", e)
            await asyncio.sleep(86400)  # daily

    _archive_task = None if skip_bg else asyncio.create_task(_token_archive_loop(), name="token-archiver")

    # 领星 weekly advisory automation scheduler (gated by lingxing_auto_enabled).
    try:
        from app.services.lingxing_automation import scheduler_loop as _lx_auto_loop
        _lingxing_auto_task = None if skip_bg else asyncio.create_task(_lx_auto_loop(), name="lingxing-auto")
    except Exception as e:
        _lingxing_auto_task = None
        logger.warning("lingxing auto scheduler skipped: %s", e)

    # IvyeaAgent daemon 是 IvyeaOps 的子进程，没有独立的 systemd 单元 —— 也就是说
    # **每次 `systemctl restart ivyea-ops` 都会把它一起带走**，之后只有当某个请求
    # 恰好调到 ensure_available() 才会被重新拉起。结果就是重启后一段时间里
    # 会话列表是空的、删除会话失败、定时任务到点跑不起来。
    # 这里在启动时主动拉一次（后台线程，不拖慢 boot），把这一类"重启后短暂不可用"
    # 一次性解决掉。
    async def _warm_agent() -> None:
        try:
            from app.services.ivyea_agent_service import ensure_available
            status = await asyncio.to_thread(ensure_available)
            logger.info("ivyea-agent available: %s", status.get('available'))
        except Exception as e:  # noqa: BLE001 — 拉不起来不该挡住 ops 启动
            logger.warning("ivyea-agent warmup skipped: %s", e)

    _warm_task = None if skip_bg else asyncio.create_task(_warm_agent(), name="agent-warmup")

    # 定时任务调度器：每 30s 看一眼有没有到点的任务。
    try:
        from app.services.schedules import scheduler_loop as _sched_loop
        _scheduler_task = asyncio.create_task(_sched_loop(), name="agent-scheduler")
    except Exception as e:
        _scheduler_task = None
        logger.warning("agent scheduler skipped: %s", e)

    yield
    # skip_bg 时这些任务压根没起，值是 None —— 统一按"有才取消/等待"处理，
    # 免得关停路径上冒 AttributeError（那会让每个测试的 teardown 都吐一堆噪音）。
    for _task in (_warm_task, _scheduler_task, _watchdog_task, _market_task,
                  _archive_task, _lingxing_auto_task):
        if _task is not None:
            _task.cancel()
    for _task in (_watchdog_task, _market_task, _archive_task):
        if _task is None:
            continue
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            logger.debug("_task 失败（旁路，已忽略）", exc_info=True)
    try:
        await terminal.shutdown_live_sessions()
    except Exception as e:
        logger.warning("live terminal shutdown error: %s", e)
    try:
        from app.services.pty_manager import manager as _pty_mgr
        await _pty_mgr.shutdown()
    except Exception as e:
        logger.warning("pty manager shutdown error: %s", e)
    logger.info("stopped")


app = FastAPI(
    title="IvyeaOps",
    description="Personal Amazon operations hub",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS: only needed in dev mode when Vite dev server (5174) calls us at 8001.
# In production the SPA is served by FastAPI itself, same origin.
if settings.dev_mode:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# --- CSRF: Origin allow-list for state-changing /api/* requests ---
# Cookie-based sessions are vulnerable to CSRF, so we require that unsafe
# requests carry an Origin header pointing at one of our trusted hosts. In
# dev_mode we extend the list with the Vite dev server origins automatically.
_ALLOWED = set(settings.allowed_origins)
if settings.dev_mode:
    _ALLOWED.update({"http://localhost:5174", "http://127.0.0.1:5174"})


@app.middleware("http")
async def _user_context(request: Request, call_next):
    """Set the current-user contextvar in the request's async context so it
    reliably reaches async streaming endpoints (e.g. AI synthesis must be
    HTTP-only for non-admin users). Best-effort: never raises — real auth
    enforcement stays in the require_user/require_admin dependencies."""
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        try:
            from app.core.security import _resolve_session_principal, current_user
            current_user.set(_resolve_session_principal(token))
        except Exception:
            logger.debug("current_user.set 失败（旁路，已忽略）", exc_info=True)
    return await call_next(request)


@app.middleware("http")
async def _origin_guard(request: Request, call_next):
    # Only guard API writes; GETs and non-API routes (SPA) are unaffected.
    if request.method in _UNSAFE_METHODS and request.url.path.startswith("/api/"):
        # The IvyeaAgent↔Ops bridge is a server-to-server callback authenticated
        # by an explicit bridge token (not a cookie session), so it carries no
        # Origin/Referer and is NOT CSRF-vulnerable. Without this exemption the
        # whole agent→board flow (market_generate_report etc.) gets 403'd.
        if request.url.path.startswith("/api/ivyea-agent-bridge/"):
            return await call_next(request)

        # 对外 MCP 同理：连过来的是 Claude Desktop / Cursor，不是浏览器，
        # 不带 Origin/Referer。它认的是 Authorization 头里的 Bearer 令牌而不是
        # Cookie 会话 —— 浏览器没法在跨站请求上凭空加一个 Bearer 头，
        # 所以这条路径本来就不在 CSRF 的攻击面里。不豁免的话整个功能直接 403。
        if request.url.path == "/api/mcp" or request.url.path.startswith("/api/mcp/"):
            return await call_next(request)

        # Native app requests (no browser CSRF risk) — skip origin check.
        ua = request.headers.get("user-agent", "")
        if "IvyeaOpsAndroid" in ua:
            return await call_next(request)

        origin = request.headers.get("origin")
        # Fall back to Referer when Origin is absent (some older browsers or
        # form submissions strip Origin on same-origin POSTs).
        if not origin:
            referer = request.headers.get("referer", "")
            if referer:
                # Strip path: keep scheme://host[:port].
                from urllib.parse import urlsplit

                parts = urlsplit(referer)
                if parts.scheme and parts.netloc:
                    origin = f"{parts.scheme}://{parts.netloc}"
        if _ALLOWED and origin not in _ALLOWED:
            return JSONResponse(
                status_code=403,
                content={"detail": "origin not allowed"},
            )
    return await call_next(request)


# --- 可观测性：request_id 贯穿 + 统一错误契约 ---
# 注册顺序即洋葱顺序：**最后注册的最先执行**。request_id 必须是最外层，
# 这样它下面每一层（用户上下文、CSRF、路由、异常处理）打的日志都带得上 id。
@app.middleware("http")
async def _request_id(request: Request, call_next):
    # 反代/网关可能已经分配过 id，有就沿用，方便和 nginx 日志对上。
    rid = (request.headers.get("x-request-id") or "").strip()[:64] or obs.new_request_id()
    request.state.request_id = rid
    token = obs.REQUEST_ID.set(rid)
    started = time.monotonic()
    try:
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        _log_access(request, response.status_code, time.monotonic() - started)
        return response
    finally:
        obs.REQUEST_ID.reset(token)


def _log_access(request: Request, status: int, elapsed: float) -> None:
    """自己记一条访问日志。

    **为什么不靠 uvicorn 自带的那条**：uvicorn 在协议层记录访问日志，那已经在
    ASGI 应用之外 —— 我们的 contextvar 早被 reset 了，所以它那行永远是 [-]，
    带不上 request_id。而"让用户贴一个 id 就能查到整条链路"这件事，恰恰要求
    每个请求至少有一行带 id 的日志。

    分级是为了别把日志刷爆：前端有大量轮询，全 INFO 会让真正有用的那几行淹掉。
    出错的、慢的走 INFO（这两类正是用户会来报的），其余降到 DEBUG。
    """
    level = logging.INFO if (status >= 400 or elapsed >= 1.0) else logging.DEBUG
    logger.log(level, "%s %s -> %s (%.0fms)",
               request.method, request.url.path, status, elapsed * 1000)


# status → 稳定错误码 + 给用户的下一步动作。code 是给前端做分支的，
# 不随文案改动；hint 是说人话的"我该怎么办"。
_ERROR_CODES: dict[int, tuple[str, str]] = {
    400: ("BAD_REQUEST", ""),
    401: ("UNAUTHORIZED", "会话已过期，请重新登录。"),
    403: ("FORBIDDEN", "当前账号没有该模块的权限，请让管理员在「用户管理」里开通。"),
    404: ("NOT_FOUND", ""),
    409: ("CONFLICT", ""),
    422: ("INVALID_PARAMS", ""),
    429: ("RATE_LIMITED", "请求过于频繁，请稍候再试。"),
    500: ("INTERNAL_ERROR", "完整堆栈已记录在本机日志里；可在「系统配置」导出诊断包附到 issue。"),
    502: ("UPSTREAM_ERROR", "上游服务（模型或数据源）没有正常返回，稍后重试或换一个 provider。"),
    503: ("UNAVAILABLE", "依赖的服务暂时不可用。"),
    504: ("UPSTREAM_TIMEOUT", "上游服务超时，稍后重试。"),
}


def _error_body(request: Request, status_code: int, message: str) -> dict:
    """错误响应体。

    **detail 必须保留**：前端 client.ts 读的就是 `err.response.data.detail`
    （见 client/src/api/client.ts 的错误归一化），改成只给 error 会让全站错误
    提示同时变哑。所以这里是**增量**的 —— 老字段原样留着，新增 error 对象。
    """
    code, hint = _ERROR_CODES.get(status_code, ("ERROR", ""))
    # 对外 MCP 认的是 Bearer 令牌，不是浏览器会话。给对面的 Agent 一句
    # "请重新登录"，它既没有登录这个动作可做，也会把这句话原样转述给用户。
    path = request.url.path
    if status_code in (401, 403) and (path == "/api/mcp" or path.startswith("/api/mcp/")):
        hint = "MCP 令牌无效、已过期、已撤销，或缺少该工具需要的权限。请在 IvyeaOps「系统配置 → 对外 MCP」重新生成。"
    return {
        "detail": message,
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(request.state, "request_id", ""),
            "hint": hint,
        },
    }


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    headers = dict(getattr(exc, "headers", None) or {})
    rid = getattr(request.state, "request_id", "")
    if rid:
        headers["X-Request-Id"] = rid
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(request, exc.status_code, message),
        headers=headers,
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """未捕获异常：**先把完整堆栈落盘**，再给用户一个能追溯的 id。

    在此之前这类错误只会出现在 stdout（自托管用户多数看不到），用户能反馈的
    只有"报错了"。现在他贴一个 request_id，你就能在 data/logs 里 grep 到全链路。
    """
    rid = getattr(request.state, "request_id", "")
    logger.exception(
        "未捕获异常 %s %s (request_id=%s)", request.method, request.url.path, rid or "-"
    )
    headers = {"X-Request-Id": rid} if rid else None
    return JSONResponse(
        status_code=500,
        content=_error_body(request, 500, "服务器内部错误，请把 request_id 反馈给我们。"),
        headers=headers,
    )


# --- API routes (prefixed /api) ---
# IMPORTANT: must be registered BEFORE the SPA catch-all below.
# Admin-only dependency: locks routers that can execute code / touch the
# filesystem / change config. Registered (non-admin) users get 403.
_ADMIN = [Depends(require_admin)]

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
# --- Admin-only (code-exec / filesystem / config / server) ---
# Grantable modules: admin OR a user granted the matching module key. The four
# "分析工具" backends share the "tools" key.
app.include_router(amazon.router, prefix="/api/amazon", tags=["amazon"], dependencies=[Depends(require_module("tools"))])
app.include_router(ad_audit.router, prefix="/api/ad-audit", tags=["ad-audit"], dependencies=[Depends(require_module("tools"))])
app.include_router(monitor.router, prefix="/api/monitor", tags=["monitor"], dependencies=[Depends(require_module("servmon"))])
app.include_router(skill.router, prefix="/api/skill", tags=["skill"], dependencies=[Depends(require_module("skill-hub"))])
# 能力市场：与 Skill 中心同属一个板块权限。默认关闭（会外联），见 services/skill_market。
app.include_router(skill_market_router.router, prefix="/api/skill-market", tags=["skill-market"], dependencies=[Depends(require_module("skill-hub"))])
# 对外 MCP：**故意不挂 require_user** —— 它认的是 Bearer 令牌（见 services/mcp_tokens），
# 因为连过来的是 Claude Desktop / Cursor，不是浏览器，没有会话 Cookie 可用。
# 令牌的签发与撤销走下面这组管理接口，只有管理员能动。
app.include_router(mcp_server_router.router, prefix="/api/mcp", tags=["mcp"])
app.include_router(mcp_tokens_router.router, prefix="/api/mcp-admin", tags=["mcp"])
# 通知渠道与 AI 预算：改的是"这台机器往哪儿发请求"，只有管理员能动。
app.include_router(notify_router.router, prefix="/api/notify", tags=["notify"])
app.include_router(news.router, prefix="/api/news", tags=["news"], dependencies=[Depends(require_module("news"))])
app.include_router(brain.router, prefix="/api/brain", tags=["brain"], dependencies=[Depends(require_module("brain"))])
app.include_router(listing_router.router, prefix="/api/listing", tags=["listing"], dependencies=[Depends(require_module("listing"))])
app.include_router(image_translate_router.router, prefix="/api/image-translate", tags=["image-translate"], dependencies=[Depends(require_module("image-translate"))])
app.include_router(terminal.router, prefix="/api/terminal", tags=["terminal"], dependencies=[Depends(require_module("terminal"))])
# /agents (old native Workspace agent hub) retired — superseded by the native
# Agents backend below. 老的 agent_hub / mcp 路由已随前端 AgentChat/workspace 一起
# 退役（对应前端文件本次已删）；/agents 路由由 agents UI 承担，
# agent 的 MCP 走 /api/ivyea-agent/mcp/servers。
# Agents native backend (replaces the external Node :3002 service). REST is
# gated by the same "agents" board permission; WS does its own cookie auth.
app.include_router(agents_api_router, prefix="/api/agents", tags=["agents"], dependencies=[Depends(require_module("agents"))])
app.include_router(agents_ws_router, prefix="/api/agents", tags=["agents-ws"])
app.include_router(ivyea_agent.router, prefix="/api/ivyea-agent", tags=["ivyea-agent"], dependencies=[Depends(require_module("agents"))])
app.include_router(ivyea_agent.bridge_router, prefix="/api/ivyea-agent-bridge", tags=["ivyea-agent-bridge"])
app.include_router(deep_analysis_router.router, prefix="/api/deep-analysis", tags=["deep-analysis"], dependencies=[Depends(require_module("tools"))])
app.include_router(skill_tools_router.router, prefix="/api/skill-tools", tags=["skill-tools"], dependencies=[Depends(require_module("tools"))])
# 定时任务：与任务台同属 agents 模块授权（本质是"让 Agent 到点自己跑一轮"）。
app.include_router(schedules_router.router, prefix="/api", tags=["schedules"], dependencies=[Depends(require_module("agents"))])
# --- Admin-only: config / other users / infra (never grantable) ---
app.include_router(hub_settings_router.router, prefix="/api", tags=["settings"], dependencies=_ADMIN)
app.include_router(projects_router.router, prefix="/api", tags=["projects"], dependencies=_ADMIN)
app.include_router(git_router.router, prefix="/api", tags=["git"], dependencies=_ADMIN)
app.include_router(setup_router.router, prefix="/api", tags=["setup"], dependencies=_ADMIN)
app.include_router(autofix_router.router, prefix="/api", tags=["autofix"], dependencies=_ADMIN)
app.include_router(lingxing_router.router, prefix="/api/lingxing", tags=["lingxing"], dependencies=_ADMIN)
# --- Open to all registered users (analytical; AI forced HTTP-only) ---
app.include_router(market_router.router, prefix="/api/market", tags=["market"])
app.include_router(playbook_router.router, prefix="/api/playbook", tags=["playbook"])
app.include_router(home_router.router, prefix="/api/home", tags=["home"])
app.include_router(freight_router.router, prefix="/api/freight", tags=["freight"])
app.include_router(assistant_router.router, prefix="/api/assistant", tags=["assistant"])
app.include_router(help_router.router, prefix="/api", tags=["help"])


# --- Frontend: serve React SPA (client/dist) ---
# Strategy:
#   /assets/*           -> static files (JS/CSS chunks hashed by Vite)
#   /favicon.ico        -> static file if exists
#   everything else     -> index.html (SPA fallback for React Router)
_CLIENT_DIST = settings.root_dir / "client" / "dist"


if _CLIENT_DIST.exists():
    _ASSETS = _CLIENT_DIST / "assets"
    if _ASSETS.exists():
        app.mount("/assets", StaticFiles(directory=_ASSETS), name="assets")

    @app.get("/favicon.ico", include_in_schema=False)
    async def _favicon() -> FileResponse:
        fp = _CLIENT_DIST / "favicon.ico"
        if fp.is_file():
            return FileResponse(fp)
        raise HTTPException(status_code=404)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str) -> FileResponse:
        # Don't fall back for /api/* — those should 404 cleanly.
        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(status_code=404)
        # Serve any real file in dist root (e.g. robots.txt), otherwise
        # fall back to index.html so React Router handles the URL.
        candidate = _CLIENT_DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        index = _CLIENT_DIST / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404, detail="frontend not built")
        # index.html must always be fresh (it references hashed asset URLs).
        # no-store is the strongest guarantee — stubborn mobile browsers / proxies
        # honor it where they ignore no-cache, so updates appear without a manual
        # cache clear. The hashed /assets/* can still be cached forever.
        return FileResponse(index, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache", "Expires": "0",
        })


if __name__ == "__main__":
    # Lets the launcher run a short `python -m app.main` instead of the long
    # uvicorn invocation; host/port come from .env via settings (single source).
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
