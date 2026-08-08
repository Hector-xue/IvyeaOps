"""Deep Analysis router — 5 diagnostic tools backed by SIF MCP + AI agents.

Tools:
  1. keyword    — 关键词竞争分析 (market_get_keyword_competition)
  2. competitor — 竞品反查 (market_get_asin_keyword_signals)
  3. traffic    — 流量异动诊断 (analyze_traffic_anomaly)
  4. reviews    — 评论聚类 (AI agent analysis)
  5. listing    — Listing 批量改写 (listing service)
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.security import require_user
from app.services import sif_service

router = APIRouter(dependencies=[Depends(require_user)])

# ── History (analysis reports) ────────────────────────────────────────────────
_HISTORY_MAX = 60
_HIST_INITED: set = set()
_DEEP_TOOLS = {"keyword": "关键词竞争分析", "competitor": "竞品反查", "traffic": "流量异动诊断"}


def _history_db_path() -> str:
    from app.core.security import user_data_dir
    return str(user_data_dir() / "deep_analysis_history.sqlite3")


def _history_connect() -> sqlite3.Connection:
    path = _history_db_path()
    conn = sqlite3.connect(path, isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    if path not in _HIST_INITED:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS deep_history (
                id        TEXT PRIMARY KEY,
                tool      TEXT NOT NULL,
                title     TEXT NOT NULL DEFAULT '',
                query     TEXT NOT NULL,
                country   TEXT NOT NULL DEFAULT 'US',
                provider  TEXT NOT NULL DEFAULT '',
                elapsed_s REAL NOT NULL DEFAULT 0,
                ts        INTEGER NOT NULL,
                report    TEXT NOT NULL DEFAULT ''
            )
        """)
        _HIST_INITED.add(path)
    return conn


def save_history(*, tool: str, title: str, query: str, country: str = "US", provider: str = "",
                 elapsed_s: float = 0.0, ts: int | None = None, report: str = "",
                 entry_id: str = "") -> str:
    ts = int(ts if ts is not None else time.time())
    entry_id = entry_id or uuid.uuid4().hex
    with _history_connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO deep_history "
            "(id,tool,title,query,country,provider,elapsed_s,ts,report) VALUES (?,?,?,?,?,?,?,?,?)",
            (entry_id, tool, title, query, country, provider, elapsed_s, ts, report))
        conn.execute("DELETE FROM deep_history WHERE id NOT IN "
                     "(SELECT id FROM deep_history ORDER BY ts DESC LIMIT ?)", (_HISTORY_MAX,))
    return entry_id


@router.get("/history")
def get_history(_user: str = Depends(require_user)) -> List[dict]:
    with _history_connect() as conn:
        rows = conn.execute(
            "SELECT id,tool,title,query,country,provider,elapsed_s,ts,report "
            "FROM deep_history ORDER BY ts DESC LIMIT ?", (_HISTORY_MAX,)).fetchall()
    return [dict(r) for r in rows]


@router.delete("/history/{entry_id}")
def delete_history_entry(entry_id: str, _user: str = Depends(require_user)) -> dict:
    with _history_connect() as conn:
        conn.execute("DELETE FROM deep_history WHERE id=?", (entry_id,))
    return {"ok": True}


async def generate_report(tool: str, query: str, country: str = "US") -> dict:
    """Run one structured analysis (keyword/competitor/traffic), synthesize a
    Markdown narrative, persist it to the 分析工具 history, and return it. Used by
    the IvyeaAgent bridge. Synthesis skips ivyea-agent (anti-recursion)."""
    tool = (tool or "").strip().lower()
    label = _DEEP_TOOLS.get(tool)
    if not label:
        raise ValueError(f"unsupported tool: {tool} (keyword/competitor/traffic)")
    if not (query or "").strip():
        raise ValueError("query (keyword or ASIN) is required")
    country = (country or "US").strip().upper()
    start = time.time()
    if tool == "keyword":
        data = await sif_service.keyword_competition(query, country, "")
    elif tool == "competitor":
        data = await sif_service.competitor_keyword_signals(query, country, "lately", "7")
    else:
        data = await sif_service.traffic_anomaly(query, country)
    from app.services import ai_synthesis_service
    prompt = (f"你是亚马逊数据分析专家。基于以下「{label}」的结构化数据，写一份简洁、有结论的中文分析报告"
              f"（对象：{query}，站点：{country}），用 Markdown 表格 + 要点呈现，只用数据里的真实数字，"
              f"缺失标 N/A。\n\n数据：\n{json.dumps(data, ensure_ascii=False)[:24000]}")
    report = (await ai_synthesis_service.generate_text(prompt, skip_agent=True)).strip()
    if not report:
        raise RuntimeError("AI 合成返回空")
    elapsed = round(time.time() - start, 1)
    entry_id = save_history(tool=tool, title=label, query=query, country=country,
                            elapsed_s=elapsed, report=report)
    return {"id": entry_id, "tool": tool, "title": label, "query": query,
            "country": country, "elapsed_s": elapsed, "report": report}


# ── Request / Response models ─────────────────────────────────────────────

class KeywordReq(BaseModel):
    keyword: str = Field(..., min_length=1, description="关键词")
    country: str = Field("US", description="站点代码")
    asin: str = Field("", description="可选：对标 ASIN")


class CompetitorReq(BaseModel):
    asin: str = Field(..., min_length=1, description="目标 ASIN")
    country: str = Field("US", description="站点代码")
    time_type: str = Field("lately", description="时间类型: lately/week/month")
    time_value: str = Field("7", description="时间值")


class TrafficReq(BaseModel):
    asin: str = Field(..., min_length=1, description="目标 ASIN")
    country: str = Field("US", description="站点代码")


class ReviewsReq(BaseModel):
    asin: str = Field(..., min_length=1, description="目标 ASIN")
    country: str = Field("US", description="站点代码")
    marketplace: str = Field("US", description="站点（兼容字段）")


class ListingRewriteReq(BaseModel):
    asins: list[str] = Field(..., min_length=1, description="ASIN 列表")
    marketplace: str = Field("US", description="站点")
    fields: list[str] = Field(
        default=["title", "bullets"],
        description="要改写的字段: title, bullets, description, qa"
    )
    style: str = Field("professional", description="改写风格")


# ── SSE helpers ───────────────────────────────────────────────────────────

def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


_SSE_HEARTBEAT = ":hb\n\n"


# ── 1. 关键词竞争分析 ────────────────────────────────────────────────────

def _save_structured_history(tool: str, query: str, country: str,
                             elapsed_s: float, data: dict) -> None:
    """Persist a structured (JSON) tool result. provider='sorftime' + JSON
    report is what the frontend history view keys on to re-render the
    structured result view instead of a Markdown report."""
    try:
        save_history(tool=tool, title=_DEEP_TOOLS.get(tool, tool), query=query,
                     country=country, provider="sorftime", elapsed_s=elapsed_s,
                     report=json.dumps(data, ensure_ascii=False))
    except Exception:
        pass  # history is best-effort; never fail the analysis over it


@router.post("/keyword")
async def keyword_competition(req: KeywordReq) -> dict:
    """关键词竞争格局分析，返回 Top ASIN、集中度、可进入性评估。"""
    start = time.time()
    try:
        data = await sif_service.keyword_competition(
            req.keyword, req.country, req.asin
        )
        _save_structured_history("keyword", req.keyword, req.country,
                                 round(time.time() - start, 1), data)
        return {"ok": True, "data": data}
    except Exception as exc:
        raise HTTPException(502, f"SIF MCP 调用失败: {exc}")


# ── 2. 竞品反查 ──────────────────────────────────────────────────────────

@router.post("/competitor")
async def competitor_lookup(req: CompetitorReq) -> dict:
    """竞品 ASIN 流量词反查，返回关键词信号、排名演变、健康分级。"""
    start = time.time()
    try:
        data = await sif_service.competitor_keyword_signals(
            req.asin, req.country, req.time_type, req.time_value
        )
        _save_structured_history("competitor", req.asin, req.country,
                                 round(time.time() - start, 1), data)
        return {"ok": True, "data": data}
    except Exception as exc:
        raise HTTPException(502, f"SIF MCP 调用失败: {exc}")


# ── 3. 流量异动诊断 ─────────────────────────────────────────────────────

@router.post("/traffic")
async def traffic_diagnosis(req: TrafficReq) -> dict:
    """ASIN 流量下跌根因分析，自动识别异常窗口并逐层拆因。"""
    start = time.time()
    try:
        data = await sif_service.traffic_anomaly(req.asin, req.country)
        _save_structured_history("traffic", req.asin, req.country,
                                 round(time.time() - start, 1), data)
        return {"ok": True, "data": data}
    except Exception as exc:
        raise HTTPException(502, f"SIF MCP 调用失败: {exc}")


# ── 4. 评论聚类 ──────────────────────────────────────────────────────────

@router.post("/reviews")
async def review_clustering(req: ReviewsReq) -> StreamingResponse:
    """评论聚类分析 — 差评差异化成因识别与修复建议。SSE 流式返回。"""
    from app.services import ai_synthesis_service

    prompt = f"""你是一位 Amazon 产品评论分析专家。
请对 ASIN {req.asin}（站点 {req.country}）的评论进行聚类分析：

1. 收集该 ASIN 的评论数据（使用 sorftime product_reviews 工具）
2. 对差评（1-3星）进行主题聚类，识别 Top 5 差评原因
3. 对好评（4-5星）进行主题聚类，识别 Top 3 产品优势
4. 针对每个差评原因，给出具体的改进建议（Listing 文案优化 / 产品改进 / 售后策略）
5. 输出格式：
   - 差评聚类表格：主题 | 占比 | 典型评论摘要 | 改进建议
   - 好评聚类表格：主题 | 占比 | 可利用的卖点
   - 优先级排序的行动建议

请用中文输出，数据尽量用表格呈现。"""

    async def generator():
        start = time.time()
        yield _sse({"type": "phase", "phase": "collecting"})
        collected: list[str] = []
        provider_used = ""
        try:
            async for prov, chunk in ai_synthesis_service.synthesize_native(
                "asin", req.asin, req.country, prompt_override=prompt
            ):
                if prov == "_attempt":
                    yield _sse({"type": "attempt", "provider": chunk})
                elif prov == "error":
                    yield _sse({"type": "error", "detail": chunk})
                    return
                else:
                    collected.append(chunk)
                    provider_used = prov
                    yield _sse({"type": "token", "text": chunk, "provider": prov})
            elapsed = round(time.time() - start, 1)
            report = "".join(collected).strip()
            if report:
                save_history(tool="reviews", title="评论聚类", query=req.asin,
                             country=req.country, provider=provider_used,
                             elapsed_s=elapsed, report=report)
            yield _sse({"type": "done", "provider": provider_used or "hermes", "elapsed_s": elapsed})
        except Exception as exc:
            yield _sse({"type": "error", "detail": str(exc)})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 5. Listing 批量改写 ─────────────────────────────────────────────────

@router.post("/listing-rewrite")
async def listing_rewrite(req: ListingRewriteReq) -> StreamingResponse:
    """批量 Listing 改写 — 多 ASIN 标题/五点/QA 批量生成。SSE 流式返回。"""
    from app.services import ai_synthesis_service

    asin_list = ", ".join(req.asins)
    fields_str = ", ".join(req.fields)

    prompt = f"""你是一位 Amazon Listing 文案专家。
请对以下 ASIN 进行批量文案改写：{asin_list}
站点：{req.marketplace}
改写字段：{fields_str}
改写风格：{req.style}

要求：
1. 先抓取每个 ASIN 的现有 Listing 内容
2. 分析各 ASIN 的卖点差异和目标受众
3. 针对每个 ASIN 逐一改写指定字段
4. 改写原则：
   - 标题：核心关键词前置，200字符内，包含品牌+核心卖点+关键属性
   - 五点：每点150-200字符，以利益点开头，融入关键词
   - 描述：情感化叙述，突出使用场景
   - QA：预测买家常见问题并给出专业回答
5. 输出格式：每个 ASIN 独立一节，包含改写前后对比

请用中文输出分析过程，英文输出改写文案。"""

    async def generator():
        start = time.time()
        yield _sse({"type": "phase", "phase": "rewriting"})
        collected: list[str] = []
        provider_used = ""
        try:
            async for prov, chunk in ai_synthesis_service.synthesize_native(
                "asin", asin_list, req.marketplace, prompt_override=prompt
            ):
                if prov == "_attempt":
                    yield _sse({"type": "attempt", "provider": chunk})
                elif prov == "error":
                    yield _sse({"type": "error", "detail": chunk})
                    return
                else:
                    collected.append(chunk)
                    provider_used = prov
                    yield _sse({"type": "token", "text": chunk, "provider": prov})
            elapsed = round(time.time() - start, 1)
            report = "".join(collected).strip()
            if report:
                save_history(tool="listing_rewrite", title="Listing 批量改写", query=asin_list,
                             country=req.marketplace, provider=provider_used,
                             elapsed_s=elapsed, report=report)
            yield _sse({"type": "done", "provider": provider_used or "hermes", "elapsed_s": elapsed})
        except Exception as exc:
            yield _sse({"type": "error", "detail": str(exc)})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
