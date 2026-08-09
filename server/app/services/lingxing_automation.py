"""领星 weekly advisory automation (P2) — analyse ad/product performance and
produce **structured recommendations only**. It never writes to LingXing.

Split by design:
* deterministic **metric aggregation** (code) — pulls SP campaigns + the last
  N days of campaign reports through the gateway (cache-friendly: past dates are
  immutable), aggregates spend/sales/ACOS/CTR/CVR per campaign;
* **LLM judgement** (``ai_synthesis_service.generate_text``) — turns the metric
  table into a strict-JSON proposal list (action + before→after + rationale +
  expected impact + confidence + risk), bounded by the configured guardrails.

Proposals are persisted to ``lingxing_auto_runs`` and surfaced in the UI. When
the operate switch is on (P3) these same proposals feed the triple-review +
human-confirm execution path; in P2 they are advisory.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core import hub_settings as _hs
from app.services import ai_synthesis_service as _ai
from app.services import lingxing_data as _data
from app.services import lingxing_service as _gw

logger = logging.getLogger("ivyea.services.lingxing_automation")

_run_lock = asyncio.Lock()


# --- persistence ------------------------------------------------------------
def _save_run(run: Dict[str, Any]) -> None:
    conn = _gw.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO lingxing_auto_runs "
            "(id,started_at,finished_at,status,trigger,scope_json,metrics_json,"
            "proposals_json,summary,error) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run["id"], run.get("started_at"), run.get("finished_at"), run.get("status"),
             run.get("trigger"), json.dumps(run.get("scope"), ensure_ascii=False, default=str),
             json.dumps(run.get("metrics"), ensure_ascii=False, default=str),
             json.dumps(run.get("proposals"), ensure_ascii=False, default=str),
             run.get("summary", ""), run.get("error", "")))
        conn.commit()
    finally:
        conn.close()


def list_runs(limit: int = 30) -> List[Dict[str, Any]]:
    conn = _gw.connect()
    try:
        cur = conn.execute(
            "SELECT id,started_at,finished_at,status,trigger,summary,error "
            "FROM lingxing_auto_runs ORDER BY started_at DESC LIMIT ?", (int(limit),))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    conn = _gw.connect()
    try:
        cur = conn.execute("SELECT * FROM lingxing_auto_runs WHERE id=?", (run_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cur.description]
        d = dict(zip(cols, row))
    finally:
        conn.close()
    for k in ("scope_json", "metrics_json", "proposals_json"):
        try:
            d[k[:-5]] = json.loads(d.pop(k) or "null")
        except Exception:
            d[k[:-5]] = None
    return d


# --- metric collection (deterministic) --------------------------------------
def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


async def _collect_store(sid: int, days: int) -> List[Dict[str, Any]]:
    """Aggregate per-campaign metrics for one store over the last ``days`` days."""
    # campaign base data (budget/state/name)
    camps = await _data.fetch_dataset("sp_campaigns", {"sid": sid, "length": 200})
    base: Dict[str, Dict[str, Any]] = {}
    for c in (camps.get("rows") or []):
        cid = str(c.get("campaign_id"))
        base[cid] = {"campaign_id": cid, "name": c.get("name"),
                     "state": c.get("state"), "daily_budget": _f(c.get("daily_budget")),
                     "targeting_type": c.get("targeting_type")}
    # daily reports (past dates immutable → cache aggressively)
    agg: Dict[str, Dict[str, float]] = {}
    for d in range(1, days + 1):
        day = (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%d")
        try:
            rep = await _data.fetch_dataset(
                "sp_campaign_report", {"sid": sid, "report_date": day, "length": 300},
                ttl=7 * 86400)
        except Exception:
            continue
        for r in (rep.get("rows") or []):
            cid = str(r.get("campaign_id"))
            a = agg.setdefault(cid, {"spend": 0, "sales": 0, "orders": 0, "clicks": 0, "impressions": 0})
            a["spend"] += _f(r.get("cost"))
            a["sales"] += _f(r.get("sales"))
            a["orders"] += _f(r.get("orders"))
            a["clicks"] += _f(r.get("clicks"))
            a["impressions"] += _f(r.get("impressions"))
    out: List[Dict[str, Any]] = []
    for cid, m in agg.items():
        spend, sales, clicks, impr, orders = (m["spend"], m["sales"], m["clicks"],
                                              m["impressions"], m["orders"])
        b = base.get(cid, {"campaign_id": cid})
        out.append({
            "sid": sid, **b,
            "spend": round(spend, 2), "sales": round(sales, 2), "orders": int(orders),
            "clicks": int(clicks), "impressions": int(impr),
            "acos": round(spend / sales, 4) if sales else None,
            "roas": round(sales / spend, 2) if spend else None,
            "ctr": round(clicks / impr, 4) if impr else None,
            "cvr": round(orders / clicks, 4) if clicks else None,
        })
    # include campaigns with no spend too (state context), but spend-sorted first
    out.sort(key=lambda x: x["spend"], reverse=True)
    return out


async def _collect(sids: List[int], days: int, max_campaigns: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for sid in sids:
        try:
            rows.extend(await _collect_store(sid, days))
        except _gw.LingXingError:
            continue
    rows.sort(key=lambda x: x["spend"], reverse=True)
    return rows[:max_campaigns]


# --- LLM advisory -----------------------------------------------------------
def _build_prompt(metrics: List[Dict[str, Any]], days: int, max_pct: int) -> str:
    table = json.dumps(metrics, ensure_ascii=False, indent=1)
    return f"""你是资深亚马逊广告优化师。下面是最近 {days} 天、按花费排序的 SP 广告活动聚合数据（JSON）。
请基于数据给出**针对性的调整建议**（仅建议，不执行）。

数据：
{table}

要求：
1. 只针对数据充分、有明确问题/机会的活动提建议；数据太少或无明显信号的不要硬提。
2. 每条建议的预算/出价改动幅度**不得超过 ±{max_pct}%**（护栏）。
3. action 取值：decrease_budget | increase_budget | pause | enable | keep。
4. 给出量化依据（引用 ACOS/ROAS/CTR/CVR/花费/订单）与预期影响、置信度(0~1)、风险(low/medium/high)。
5. **只输出 JSON**，不要任何额外文字，结构如下：
{{
  "summary": "一句话总体判断",
  "proposals": [
    {{
      "sid": <int>, "campaign_id": "<str>", "campaign_name": "<str>",
      "metric": {{"spend":..,"sales":..,"acos":..,"orders":..}},
      "current": {{"daily_budget":..,"state":".."}},
      "action": "decrease_budget|increase_budget|pause|enable|keep",
      "proposed": {{"daily_budget":..,"state":".."}},
      "change_pct": <number>,
      "rationale": "..", "expected_impact": "..",
      "confidence": <0~1>, "risk": "low|medium|high"
    }}
  ]
}}
若没有任何值得调整的活动，proposals 返回 []。"""


def _parse_json(text: str) -> Dict[str, Any]:
    """Extract the JSON object from a model response (tolerates code fences)."""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.DOTALL)
    if m:
        t = m.group(1)
    else:
        i, j = t.find("{"), t.rfind("}")
        if i != -1 and j != -1:
            t = t[i:j + 1]
    return json.loads(t)


def _enforce_guardrails(proposals: List[Dict[str, Any]], max_pct: int) -> List[Dict[str, Any]]:
    """Clamp/flag proposals that violate the hard change cap (advisory backstop)."""
    out = []
    for p in proposals or []:
        try:
            cp = abs(float(p.get("change_pct") or 0))
            if cp > max_pct:
                p["guardrail_flag"] = f"超出幅度上限 {max_pct}%（建议被标记，执行时会被拦截）"
        except (TypeError, ValueError):
            logger.debug("abs 失败（旁路，已忽略）", exc_info=True)
        out.append(p)
    return out


# --- orchestration ----------------------------------------------------------
async def _resolve_sids() -> List[int]:
    raw = (_hs.get("lingxing_auto_stores") or "").replace("，", ",").strip()
    if raw:
        return [int(x) for x in raw.split(",") if x.strip().isdigit()]
    sellers = await _data.fetch_dataset("sellers")
    return [int(s["sid"]) for s in (sellers.get("rows") or []) if str(s.get("sid", "")).isdigit()]


async def run_once(trigger: str = "manual") -> Dict[str, Any]:
    """Execute one advisory run. Serialised; returns the persisted run dict."""
    async with _run_lock:
        run: Dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None, "status": "collecting", "trigger": trigger,
            "scope": None, "metrics": None, "proposals": None, "summary": "", "error": "",
        }
        _save_run(run)
        try:
            if not _gw.is_master_enabled():
                raise _gw.LingXingError("领星集成未启用（总开关关闭）")
            days = int(_hs.get("lingxing_auto_report_days") or 7)
            max_c = int(_hs.get("lingxing_auto_max_campaigns") or 40)
            max_pct = int(_hs.get("lingxing_max_change_pct") or 20)
            sids = await _resolve_sids()
            run["scope"] = {"sids": sids, "days": days, "max_campaigns": max_c}
            metrics = await _collect(sids, days, max_c)
            run["metrics"] = metrics
            run["status"] = "analyzing"
            _save_run(run)

            if not metrics:
                run.update(status="done", summary="窗口内无广告数据，无建议。",
                           proposals=[], finished_at=datetime.now(timezone.utc).isoformat())
                _save_run(run)
                await _notify_run(run)
                return run

            from app.services import lingxing_operate as _op
            prompt = _build_prompt(metrics, days, max_pct)
            provider = _hs.get("lingxing_analysis_provider") or "ivyea-agent"
            try:
                raw = await _op._review_generate(provider, prompt)
            except Exception:  # noqa: BLE001 — configured provider unavailable → default chain
                raw = await _ai.generate_text(prompt)
            parsed = _parse_json(raw)
            proposals = _enforce_guardrails(parsed.get("proposals") or [], max_pct)
            run.update(status="done", summary=parsed.get("summary", ""),
                       proposals=proposals, finished_at=datetime.now(timezone.utc).isoformat())
            _save_run(run)
            await _notify_run(run)
            return run
        except Exception as e:  # noqa: BLE001
            run.update(status="failed", error=str(e)[:500],
                       finished_at=datetime.now(timezone.utc).isoformat())
            _save_run(run)
            await _notify_run(run)
            return run


async def _notify_run(run: Dict[str, Any]) -> None:
    """Best-effort webhook summary when an advisory run finishes (esp. the
    scheduled weekly run, which no one is watching)."""
    try:
        from app.services import lingxing_operate as _op
        if run.get("status") == "failed":
            await _op.send_alert(f"自动化建议运行失败：{run.get('error', '')[:200]}")
        else:
            n = len(run.get("proposals") or [])
            await _op.send_alert(
                f"自动化建议完成（{'定时' if run.get('trigger') == 'scheduled' else '手动'}）："
                f"{n} 条建议。{(run.get('summary') or '')[:150]}")
    except Exception:  # noqa: BLE001
        logger.debug("_op.send_alert 失败（旁路，已忽略）", exc_info=True)


def start_background_run(trigger: str = "manual") -> str:
    """Kick a run without blocking the request; returns the run id immediately."""
    run_id = uuid.uuid4().hex[:12]

    async def _go():
        await run_once(trigger=trigger)

    asyncio.create_task(_go(), name=f"lingxing-auto-{run_id}")
    return run_id


# --- scheduler --------------------------------------------------------------
async def scheduler_loop() -> None:
    """Weekly trigger: wakes every ~20 min, fires once on the configured
    weekday+hour. Best-effort; gated by ``lingxing_auto_enabled``."""
    last_fired_date: Optional[str] = None
    while True:
        try:
            if _hs.get("lingxing_auto_enabled") and _gw.is_master_enabled():
                now = datetime.now()
                today = now.strftime("%Y-%m-%d")
                wd = int(_hs.get("lingxing_auto_weekday") or 0)
                hr = int(_hs.get("lingxing_auto_hour") or 9)
                if now.weekday() == wd and now.hour == hr and last_fired_date != today:
                    last_fired_date = today
                    logger.info("lingxing auto run firing (%s)", today)
                    await run_once(trigger="scheduled")
        except Exception as e:  # noqa: BLE001
            logger.warning("lingxing auto scheduler error: %s", e)
        await asyncio.sleep(1200)
