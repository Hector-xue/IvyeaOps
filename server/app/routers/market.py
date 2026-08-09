"""Market research router — SSE streaming endpoint + history persistence."""
from __future__ import annotations

import asyncio
import logging
import json
import sqlite3
import time
import uuid
from typing import AsyncGenerator, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.security import require_user
from app.services import sorftime_service, ai_synthesis_service

logger = logging.getLogger("ivyea.routers.market")

router = APIRouter()

# ── History DB ────────────────────────────────────────────────────────────────

_HISTORY_MAX = 60
_INITED: set = set()   # db paths whose schema has been ensured


def _history_db_path() -> str:
    from app.core.security import user_data_dir
    return str(user_data_dir() / "market_history.sqlite3")


def _history_connect() -> sqlite3.Connection:
    path = _history_db_path()
    conn = sqlite3.connect(path, isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    if path not in _INITED:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_history (
                id          TEXT PRIMARY KEY,
                mode        TEXT NOT NULL,
                query       TEXT NOT NULL,
                marketplace TEXT NOT NULL,
                provider    TEXT NOT NULL DEFAULT '',
                data_source TEXT NOT NULL DEFAULT 'sorftime',
                elapsed_s   REAL NOT NULL DEFAULT 0,
                ts          INTEGER NOT NULL,
                report      TEXT NOT NULL DEFAULT ''
            )
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(market_history)").fetchall()}
        if "data_source" not in columns:
            conn.execute("ALTER TABLE market_history ADD COLUMN data_source TEXT NOT NULL DEFAULT 'sorftime'")
        _INITED.add(path)
    return conn


def _init_history_db() -> None:
    # Initialize the admin (shared) DB at startup; per-user DBs are created
    # lazily on first access via _history_connect().
    _history_connect().close()


class HistoryEntryIn(BaseModel):
    id: str = ""
    mode: str
    query: str
    marketplace: str
    provider: str = ""
    data_source: str = "sorftime"
    elapsed_s: float = 0.0
    ts: int
    report: str = ""


@router.get("/history")
def get_history(_user: str = Depends(require_user)) -> List[dict]:
    with _history_connect() as conn:
        rows = conn.execute(
            "SELECT id,mode,query,marketplace,provider,data_source,elapsed_s,ts,report "
            "FROM market_history ORDER BY ts DESC LIMIT ?",
            (_HISTORY_MAX,),
        ).fetchall()
    return [dict(r) for r in rows]


def save_history(*, mode: str, query: str, marketplace: str, provider: str = "",
                 data_source: str = "sorftime",
                 elapsed_s: float = 0.0, ts: int | None = None, report: str = "",
                 entry_id: str = "") -> str:
    """Persist one market report row; returns its id.
    Shared by POST /history (frontend) and the IvyeaAgent panel bridge."""
    ts = int(ts if ts is not None else time.time())
    entry_id = entry_id or str(ts) or uuid.uuid4().hex
    with _history_connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO market_history "
            "(id,mode,query,marketplace,provider,data_source,elapsed_s,ts,report) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (entry_id, mode, query, marketplace, provider, _normalize_data_source(data_source), elapsed_s, ts, report),
        )
        conn.execute(   # keep most recent only
            "DELETE FROM market_history WHERE id NOT IN "
            "(SELECT id FROM market_history ORDER BY ts DESC LIMIT ?)",
            (_HISTORY_MAX,),
        )
    return entry_id


@router.post("/history")
def add_history(entry: HistoryEntryIn, _user: str = Depends(require_user)) -> dict:
    return {"id": save_history(
        mode=entry.mode, query=entry.query, marketplace=entry.marketplace,
        provider=entry.provider, data_source=entry.data_source, elapsed_s=entry.elapsed_s, ts=entry.ts,
        report=entry.report, entry_id=entry.id)}


async def generate_report(mode: str, query: str, marketplace: str,
                          data_source: str = "sorftime") -> dict:
    """Collect + synthesize + persist one market report (no SSE), then return it.
    Used by the IvyeaAgent panel bridge so an agent-driven 市场调研 lands in the
    panel's 历史. Synthesis skips the ivyea-agent provider to avoid agent→ops→agent
    nesting (the caller is already IvyeaAgent)."""
    mode = mode if mode in ("keyword", "asin") else "keyword"
    marketplace = (marketplace or "US").strip().upper()
    data_source = _normalize_data_source(data_source)

    async def _noop(*_a) -> None:
        return None

    start = time.time()
    _svc = _pipeline_for(data_source)
    if mode == "keyword":
        data, errors = await _svc.keyword_pipeline(query, marketplace, _noop)
    else:
        data, errors = await _svc.asin_pipeline(query, marketplace, _noop)
    if not _has_collected_data(data):
        detail = "; ".join(errors[:3]) or "未返回任何数据"
        raise RuntimeError(f"{_source_label(data_source)} 数据采集失败：{detail}")
    parts: list[str] = []
    provider = "unknown"
    async for prov, chunk in ai_synthesis_service.synthesize(mode, query, marketplace, data, skip_agent=True, source=_source_label(data_source)):
        if prov == "_attempt":
            continue
        if prov == "error":
            raise RuntimeError(chunk)
        provider = prov
        parts.append(chunk)
    report = "".join(parts).strip()
    if not report:
        raise RuntimeError("AI 合成返回空")
    elapsed = round(time.time() - start, 1)
    entry_id = save_history(mode=mode, query=query, marketplace=marketplace, provider=provider,
                            data_source=data_source,
                            elapsed_s=elapsed, ts=int(time.time() * 1000), report=report,
                            entry_id=uuid.uuid4().hex)
    return {"id": entry_id, "mode": mode, "query": query, "marketplace": marketplace,
            "provider": provider, "data_source": data_source, "data_source_label": _source_label(data_source),
            "elapsed_s": elapsed, "warnings": errors, "report": report}


@router.delete("/history/{entry_id}")
def delete_history_entry(entry_id: str, _user: str = Depends(require_user)) -> dict:
    with _history_connect() as conn:
        conn.execute("DELETE FROM market_history WHERE id=?", (entry_id,))
    return {"ok": True}


@router.delete("/history")
def clear_history(_user: str = Depends(require_user)) -> dict:
    with _history_connect() as conn:
        conn.execute("DELETE FROM market_history")
    return {"ok": True}


class ResearchReq(BaseModel):
    mode: str = "keyword"       # "keyword" | "asin"
    query: str
    marketplace: str = "US"
    data_source: str = "sorftime"   # "sorftime" | "sellersprite"


_DATA_SOURCES = {"sorftime", "sellersprite"}


def _normalize_data_source(data_source: str) -> str:
    value = (data_source or "sorftime").strip().lower()
    if value not in _DATA_SOURCES:
        raise ValueError(f"unsupported data source: {value}")
    return value


def _pipeline_for(data_source: str):
    """Data-collection service for the selected source. Both expose
    keyword_pipeline / asin_pipeline(query, marketplace, on_progress) -> (data, errors)."""
    if _normalize_data_source(data_source) == "sellersprite":
        from app.services import sellersprite_service
        return sellersprite_service
    return sorftime_service


def _source_label(data_source: str) -> str:
    """Human name of the data source for the report's 数据声明."""
    return "卖家精灵" if _normalize_data_source(data_source) == "sellersprite" else "Sorftime"


def _has_collected_data(data: object) -> bool:
    if isinstance(data, dict):
        return any(_has_collected_data(value) for value in data.values())
    if isinstance(data, (list, tuple)):
        return any(_has_collected_data(value) for value in data)
    return data not in (None, "")


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# SSE comment line; clients ignore it but it keeps proxy/browser idle
# timers from killing the connection while we wait on slow CLI runners.
_SSE_HEARTBEAT = ":hb\n\n"
_HEARTBEAT_INTERVAL_S = 10.0


async def _stream_synthesis(
    gen_factory,
    heartbeat_interval: float = _HEARTBEAT_INTERVAL_S,
) -> AsyncGenerator[tuple[str, str, str], None]:
    """Drive an async synthesis generator via a queue, interleaving SSE
    heartbeats.  Yields (kind, a, b) tuples where kind is 'chunk' or 'exc';
    the sentinel is signalled by StopAsyncIteration on the outer loop."""
    out_q: asyncio.Queue = asyncio.Queue()
    _SENTINEL = object()

    async def _producer() -> None:
        try:
            async for prov, chunk in gen_factory():
                await out_q.put(("chunk", prov, chunk))
        except Exception as exc:
            await out_q.put(("exc", exc, None))
        finally:
            await out_q.put((_SENTINEL, None, None))

    task = asyncio.create_task(_producer())
    try:
        while True:
            try:
                item = await asyncio.wait_for(out_q.get(), timeout=heartbeat_interval)
            except asyncio.TimeoutError:
                yield ("hb", None, None)
                continue
            kind, a, b = item
            if kind is _SENTINEL:
                return
            yield (kind, a, b)
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                logger.debug("task 失败（旁路，已忽略）", exc_info=True)


async def _run_research(req: ResearchReq) -> AsyncGenerator[str, None]:
    start = time.time()
    data_source = _normalize_data_source(req.data_source)
    source_label = _source_label(data_source)
    yield _sse({"type": "source", "requested": data_source, "actual": data_source, "label": source_label})
    # Always collect market data through the server-side MCP client. It reads the
    # single IvyeaOps system setting and works for every signed-in user. Depending
    # on a per-OS/per-user Hermes config caused first-run Windows sessions to call
    # Sorftime without a key even though the key was present in System Settings.
    hermes_first = False

    # ── Path A: hermes-native ─────────────────────────────────────────────────
    # hermes has sorftime MCP configured; give it tool-calling instructions so
    # it collects and synthesises in one pass — no sorftime pre-fetch needed.
    if hermes_first and data_source == "sorftime":
        yield _sse({"type": "phase", "phase": "synthesizing"})
        provider = "unknown"
        hermes_ok = False
        async for kind, a, b in _stream_synthesis(
            lambda: ai_synthesis_service.synthesize_native(req.mode, req.query, req.marketplace)
        ):
            if kind == "hb":
                yield _SSE_HEARTBEAT
            elif kind == "exc":
                yield _sse({"type": "error", "detail": f"AI 合成失败: {a}"})
                return
            else:
                prov, chunk = a, b
                if prov == "_attempt":
                    yield _sse({"type": "attempt", "provider": chunk})
                elif prov == "error":
                    # hermes failed — fall through to Path B below
                    break
                else:
                    provider = prov
                    hermes_ok = True
                    yield _sse({"type": "token", "text": chunk, "provider": prov})
        if hermes_ok:
            elapsed = round(time.time() - start, 1)
            yield _sse({"type": "done", "provider": provider, "elapsed_s": elapsed,
                        "data_source": data_source, "data_source_label": source_label})
            return
        # hermes failed → fall back to Path B (sorftime pre-fetch + other providers)
        yield _sse({"type": "warn", "detail": "hermes 原生调用失败，回退到数据预采集模式"})

    # ── Path B: pre-fetch sorftime data, then synthesise ─────────────────────
    progress_queue: asyncio.Queue = asyncio.Queue()

    async def on_progress(step: str, done: int, total: int) -> None:
        await progress_queue.put({"type": "progress", "step": step, "done": done, "total": total})

    async def drain_progress() -> None:
        while not progress_queue.empty():
            evt = progress_queue.get_nowait()
            yield _sse(evt)

    yield _sse({"type": "phase", "phase": "collecting"})

    _svc = _pipeline_for(data_source)
    if req.mode == "keyword":
        pipeline_task = asyncio.create_task(
            _svc.keyword_pipeline(req.query, req.marketplace, on_progress)
        )
    else:
        pipeline_task = asyncio.create_task(
            _svc.asin_pipeline(req.query, req.marketplace, on_progress)
        )

    last_yield = time.time()
    while not pipeline_task.done():
        await asyncio.sleep(0.2)
        emitted = False
        async for chunk in drain_progress():
            yield chunk
            emitted = True
            last_yield = time.time()
        if not emitted and (time.time() - last_yield) >= _HEARTBEAT_INTERVAL_S:
            yield _SSE_HEARTBEAT
            last_yield = time.time()

    async for chunk in drain_progress():
        yield chunk

    try:
        data, pipe_errors = pipeline_task.result()
    except Exception as exc:
        yield _sse({"type": "error", "detail": f"数据采集失败: {exc}"})
        return

    if not _has_collected_data(data):
        detail = "; ".join(pipe_errors[:3]) or "未返回任何数据"
        yield _sse({"type": "error", "detail": f"{source_label} 数据采集失败：{detail}"})
        return

    for err in pipe_errors:
        yield _sse({"type": "warn", "detail": err})

    yield _sse({"type": "phase", "phase": "synthesizing"})

    # Determine provider chain for Path B: skip hermes (already failed in
    # Path A native mode, or hermes wasn't first so skip it here too since
    # it would just receive a 40KB dump without MCP benefit).
    provider = "unknown"
    async for kind, a, b in _stream_synthesis(
        lambda: ai_synthesis_service.synthesize(req.mode, req.query, req.marketplace, data, source=source_label)
    ):
        if kind == "hb":
            yield _SSE_HEARTBEAT
        elif kind == "exc":
            yield _sse({"type": "error", "detail": f"AI 合成失败: {a}"})
            return
        else:
            prov, chunk = a, b
            if prov == "_attempt":
                yield _sse({"type": "attempt", "provider": chunk})
                continue
            provider = prov
            if prov == "error":
                yield _sse({"type": "error", "detail": chunk})
                return
            yield _sse({"type": "token", "text": chunk, "provider": prov})

    elapsed = round(time.time() - start, 1)
    yield _sse({"type": "done", "provider": provider, "elapsed_s": elapsed,
                "data_source": data_source, "data_source_label": source_label})


@router.post("/research")
async def market_research(
    req: ResearchReq,
    _user: str = Depends(require_user),
) -> StreamingResponse:
    if not req.query.strip():
        from fastapi import HTTPException
        raise HTTPException(400, "query cannot be empty")
    if req.mode not in ("keyword", "asin"):
        from fastapi import HTTPException
        raise HTTPException(400, "mode must be keyword or asin")
    try:
        req.data_source = _normalize_data_source(req.data_source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    async def generator():
        async for chunk in _run_research(req):
            yield chunk

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Pulse endpoint (lightweight: keyword_detail + keyword_trend only) ─────────

class PulseReq(BaseModel):
    keyword: str
    marketplace: str = "US"


@router.post("/pulse")
async def market_pulse(req: PulseReq, _user: str = Depends(require_user)) -> dict:
    """Fetch keyword_detail + keyword_trend for a single keyword.
    Returns a flat dict with the key metrics — fast (~2-4s, 2 concurrent calls).
    """
    if not req.keyword.strip():
        raise HTTPException(400, "keyword cannot be empty")

    from app.services.sorftime_service import _make_client, _safe_call
    import asyncio

    async with _make_client() as client:
        detail_task = _safe_call(
            client, "keyword_detail",
            {"keyword": req.keyword, "keyword_support_site": req.marketplace}, 1,
        )
        trend_task = _safe_call(
            client, "keyword_trend",
            {"keyword": req.keyword, "keyword_support_site": req.marketplace}, 2,
        )
        (_, detail, detail_err), (_, trend, trend_err) = await asyncio.gather(
            detail_task, trend_task
        )

    return {
        "keyword": req.keyword,
        "marketplace": req.marketplace,
        "detail": detail,
        "detail_error": detail_err,
        "trend": trend,
        "trend_error": trend_err,
    }
