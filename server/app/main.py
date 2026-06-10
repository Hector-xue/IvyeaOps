"""IvyeaOps FastAPI backend entry point."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

# Central logging config: one place sets level + format (was previously left to
# per-module getLogger with no root setup). Override level via IVYEA_OPS_LOG_LEVEL.
_log_level = os.environ.get("IVYEA_OPS_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.security import require_admin, require_module
from app.core.skill_paths import (
    SKILLS_ROOT,
    ensure_studio_dirs,
    studio_paths_summary,
)
from app.routers import ad_audit, agent_hub, amazon, auth, brain, health, monitor, news, skill, terminal
from app.routers import listing as listing_router
from app.routers import market as market_router
from app.routers import playbook as playbook_router
from app.routers import home as home_router
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
from app.routers import mcp as mcp_router
from app.agents.router import api_router as agents_api_router, ws_router as agents_ws_router


# Methods that can mutate state; anything not in this set is exempt from the
# Origin check (GET/HEAD/OPTIONS are considered safe per RFC 9110 §9.2.1).
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    # Skill Studio directories: we provision our own state dir
    # (~/.hermes/skill-studio/) but intentionally NEVER touch SKILLS_ROOT —
    # that's Hermes' territory. If it doesn't exist we just warn; the Skill
    # Studio API will surface a clear error on first call.
    ensure_studio_dirs()
    if not SKILLS_ROOT.exists():
        print(f"[IvyeaOps] WARNING skills root missing: {SKILLS_ROOT}")
    for key, value in studio_paths_summary().items():
        print(f"[IvyeaOps] {key}: {value}")

    # Best-effort: sweep expired trash entries on startup. Failure here must
    # never block the server from coming up — the API will retry on demand.
    try:
        from app.services.trash import purge_expired
        purged = purge_expired()
        if purged:
            print(f"[IvyeaOps] purged {purged} expired trash entries")
    except Exception as e:
        print(f"[IvyeaOps] trash purge skipped: {e}")

    # Best-effort: sweep expired ASIN audit artifacts (30-day retention).
    try:
        from app.services.asin_audit import sweep_expired as _sweep_audits
        n = _sweep_audits()
        if n:
            print(f"[IvyeaOps] purged {n} expired audit dirs")
    except Exception as e:
        print(f"[IvyeaOps] audit sweep skipped: {e}")

    # Rescue ghost "running" jobs left behind by a prior crash/restart:
    # _live_jobs is empty on boot, so anything status=running on disk is stale.
    try:
        from app.services.asin_audit import sweep_stale_running
        n = sweep_stale_running()
        if n:
            print(f"[IvyeaOps] marked {n} stale running jobs as failed")
    except Exception as e:
        print(f"[IvyeaOps] stale running sweep skipped: {e}")

    # Same pair of sweeps for ad-audit jobs.
    try:
        from app.services.ad_audit import sweep_expired as _sweep_ad
        n = _sweep_ad()
        if n:
            print(f"[IvyeaOps] purged {n} expired ad-audit dirs")
    except Exception as e:
        print(f"[IvyeaOps] ad-audit expired sweep skipped: {e}")

    try:
        from app.services.ad_audit import sweep_stale_running as _sweep_ad_stale
        n = _sweep_ad_stale()
        if n:
            print(f"[IvyeaOps] marked {n} stale ad-audit jobs as failed")
    except Exception as e:
        print(f"[IvyeaOps] ad-audit stale sweep skipped: {e}")

    # Market research history DB.
    try:
        from app.routers.market import _init_history_db as _init_market_hist
        _init_market_hist()
        print("[IvyeaOps] market history DB ready")
    except Exception as e:
        print(f"[IvyeaOps] market history DB init skipped: {e}")

    # Agents native backend: ensure its metadata tables exist (no-op against
    # the live ~/.agents/auth.db the old Node service shared).
    try:
        from app.agents.db import init_db as _init_agents_db
        _init_agents_db()
        print("[IvyeaOps] agents DB ready")
    except Exception as e:
        print(f"[IvyeaOps] agents DB init skipped: {e}")

    # Launch-playbook history DB.
    try:
        from app.routers.playbook import _init_history_db as _init_playbook_hist
        _init_playbook_hist()
        print("[IvyeaOps] playbook history DB ready")
    except Exception as e:
        print(f"[IvyeaOps] playbook history DB init skipped: {e}")

    # Home monitor (watchlist + snapshots) DB.
    try:
        from app.routers.home import _init_db as _init_home_db
        _init_home_db()
        print("[IvyeaOps] home monitor DB ready")
    except Exception as e:
        print(f"[IvyeaOps] home monitor DB init skipped: {e}")

    # Registered-users DB (multi-user mode).
    try:
        from app.services import users_service
        users_service.init_db()
        print("[IvyeaOps] users DB ready")
    except Exception as e:
        print(f"[IvyeaOps] users DB init skipped: {e}")

    # Brain chat/upload metadata DB is local SQLite; initialize eagerly so
    # schema problems are visible at boot, while keeping the service lightweight.
    try:
        from app.services.brain_chat_service import init_db as _init_brain_chat
        _init_brain_chat()
        print("[IvyeaOps] brain chat DB ready")
    except Exception as e:
        print(f"[IvyeaOps] brain chat DB init skipped: {e}")

    # Multi-agent hub: schema + agent discovery + PTY reaper.  All best-effort
    # so a misconfigured agent (e.g. missing binary) never blocks server boot.
    try:
        from app.services import agent_session_service as _agent_db
        from app.services import agent_registry as _agent_reg
        from app.services.pty_manager import manager as _pty_mgr

        _agent_db.init_db()
        agents = _agent_reg.discover_agents()
        ok = sum(1 for a in agents if a.get("enabled"))
        print(f"[IvyeaOps] agent registry: {ok}/{len(agents)} enabled")
        _pty_mgr.start_background_tasks()
    except Exception as e:
        print(f"[IvyeaOps] agent hub init skipped: {e}")

    print(f"[IvyeaOps] starting on {settings.host}:{settings.port}")
    print(f"[IvyeaOps] data dir: {settings.data_dir}")
    print(f"[IvyeaOps] dev_mode: {settings.dev_mode}")

    # Terminal subsystem:
    # (1) legacy tmux auto-capture for the old shared terminal page
    # (2) new live multi-terminal session manager for the native workbench
    try:
        terminal.start_autocapture()
    except Exception as e:
        print(f"[IvyeaOps] terminal auto-capture not started: {e}")
    try:
        terminal.init_live_sessions()
        print("[IvyeaOps] live terminal sessions ready")
    except Exception as e:
        print(f"[IvyeaOps] live terminal init skipped: {e}")

    # systemd integration: announce READY and start the watchdog ping
    # loop. Both are no-ops when running outside systemd (NOTIFY_SOCKET
    # / WATCHDOG_USEC absent), so dev workflows are unaffected.
    from app.services.watchdog import notify_ready, notify_status, watchdog_loop
    notify_ready()
    notify_status("ready")
    _watchdog_task = asyncio.create_task(watchdog_loop(), name="sd-watchdog")

    # Home market-traffic daily recorder: wakes every 30 min and records a
    # daily point for each tracked baseline / watched ASIN that lacks one.
    # Best-effort, never blocks boot or shutdown.
    async def _market_daily_loop():
        while True:
            try:
                from app.routers.home import run_due_recordings
                summary = await run_due_recordings()
                if summary.get("recorded_market") or summary.get("recorded_asin"):
                    print(f"[IvyeaOps] market recorder: {summary}")
            except Exception as e:
                print(f"[IvyeaOps] market recorder error: {e}")
            await asyncio.sleep(1800)

    _market_task = asyncio.create_task(_market_daily_loop(), name="market-recorder")

    # Token-usage archiver: snapshot each tool's token data into IvyeaOps's own
    # DB once a day so history survives even after a tool is uninstalled.
    # Runs once shortly after boot, then every 24h. Best-effort.
    try:
        from app.services import token_archive
        token_archive.init_db()
        print("[IvyeaOps] token archive DB ready")
    except Exception as e:
        print(f"[IvyeaOps] token archive init skipped: {e}")

    try:
        from app.services import lingxing_service
        lingxing_service.init_db()
        print("[IvyeaOps] lingxing audit DB ready")
    except Exception as e:
        print(f"[IvyeaOps] lingxing audit init skipped: {e}")

    async def _token_archive_loop():
        await asyncio.sleep(120)  # let boot settle before first snapshot
        while True:
            try:
                from app.services import token_archive
                summary = await asyncio.to_thread(token_archive.archive_run, 7)
                print(f"[IvyeaOps] token archive: {summary}")
            except Exception as e:
                print(f"[IvyeaOps] token archive error: {e}")
            await asyncio.sleep(86400)  # daily

    _archive_task = asyncio.create_task(_token_archive_loop(), name="token-archiver")

    # 领星 weekly advisory automation scheduler (gated by lingxing_auto_enabled).
    try:
        from app.services.lingxing_automation import scheduler_loop as _lx_auto_loop
        _lingxing_auto_task = asyncio.create_task(_lx_auto_loop(), name="lingxing-auto")
    except Exception as e:
        _lingxing_auto_task = None
        print(f"[IvyeaOps] lingxing auto scheduler skipped: {e}")

    yield
    _watchdog_task.cancel()
    _market_task.cancel()
    _archive_task.cancel()
    if _lingxing_auto_task:
        _lingxing_auto_task.cancel()
    try:
        await _watchdog_task
    except (asyncio.CancelledError, Exception):
        pass
    try:
        await _market_task
    except (asyncio.CancelledError, Exception):
        pass
    try:
        await _archive_task
    except (asyncio.CancelledError, Exception):
        pass
    try:
        await terminal.stop_autocapture()
    except Exception as e:
        print(f"[IvyeaOps] terminal auto-capture stop error: {e}")
    try:
        await terminal.shutdown_live_sessions()
    except Exception as e:
        print(f"[IvyeaOps] live terminal shutdown error: {e}")
    try:
        from app.services.pty_manager import manager as _pty_mgr
        await _pty_mgr.shutdown()
    except Exception as e:
        print(f"[IvyeaOps] pty manager shutdown error: {e}")
    print("[IvyeaOps] stopped")


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
            pass
    return await call_next(request)


@app.middleware("http")
async def _origin_guard(request: Request, call_next):
    # Only guard API writes; GETs and non-API routes (SPA) are unaffected.
    if request.method in _UNSAFE_METHODS and request.url.path.startswith("/api/"):
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
app.include_router(news.router, prefix="/api/news", tags=["news"], dependencies=[Depends(require_module("news"))])
app.include_router(brain.router, prefix="/api/brain", tags=["brain"], dependencies=[Depends(require_module("brain"))])
app.include_router(listing_router.router, prefix="/api/listing", tags=["listing"], dependencies=[Depends(require_module("listing"))])
app.include_router(terminal.router, prefix="/api/terminal", tags=["terminal"], dependencies=[Depends(require_module("terminal"))])
# /agents (old native Workspace agent hub) retired — superseded by the native
# Agents backend below. agent_hub/mcp routers are no longer mounted; the
# /agents route now serves the agents UI. (Service files kept for now.)
# app.include_router(agent_hub.router, prefix="/api", tags=["agent-hub"], dependencies=[Depends(require_module("agents"))])
# app.include_router(mcp_router.router, prefix="/api", tags=["mcp"], dependencies=[Depends(require_module("agents"))])
# Agents native backend (replaces the external Node :3002 service). REST is
# gated by the same "agents" board permission; WS does its own cookie auth.
app.include_router(agents_api_router, prefix="/api/agents", tags=["agents"], dependencies=[Depends(require_module("agents"))])
app.include_router(agents_ws_router, prefix="/api/agents", tags=["agents-ws"])
app.include_router(deep_analysis_router.router, prefix="/api/deep-analysis", tags=["deep-analysis"], dependencies=[Depends(require_module("tools"))])
app.include_router(skill_tools_router.router, prefix="/api/skill-tools", tags=["skill-tools"], dependencies=[Depends(require_module("tools"))])
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
