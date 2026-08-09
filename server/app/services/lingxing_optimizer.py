"""领星 广告优化规则引擎 (deterministic) — the rigorous core.

Pulls keyword + search-term reports over a window (cache-friendly; drops the most
recent N days for attribution lag), derives the target ACOS from product margin
(break-even ACOS = margin; target = factor × margin), and emits **rule-backed
candidate operations** for the four levers:

  否词  — search term with ≥N clicks and 0 orders (data-confirmed loser)
  降bid — keyword high ACOS (≥min clicks) → new bid = RPC × target, step-capped
  加bid — winner (≥min orders, ACOS ≤ 0.8×target) → +step, ≤ RPC × target
  加预算 — campaign budget-capped AND profitable → +step
  收割  — search term with ≥N orders (profitable) → advisory: promote to exact

Every candidate carries the rule fired, supporting metrics, the significance
check, and a ready payload for the controlled-write path (/operate/manual). This
is deterministic and auditable — the LLM only reviews, it doesn't invent changes.
"""
from __future__ import annotations

import asyncio
import logging
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core import hub_settings as _hs
from app.services import lingxing_data as _data
from app.services import lingxing_service as _gw

logger = logging.getLogger("ivyea.services.lingxing_optimizer")

_REPORT_TTL_S = 7 * 86400


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _cfg() -> Dict[str, Any]:
    return _hs.load()


def _window_dates() -> List[str]:
    c = _cfg()
    excl = int(c.get("lingxing_opt_exclude_recent_days") or 2)
    win = int(c.get("lingxing_opt_window_days") or 30)
    return [(datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%d")
            for d in range(excl + 1, excl + 1 + win)]


def _bucket() -> Dict[str, float]:
    return {"spend": 0.0, "sales": 0.0, "orders": 0.0, "clicks": 0.0, "impressions": 0.0}


def _add(b: Dict[str, float], r: Dict[str, Any]) -> None:
    b["spend"] += _f(r.get("cost")); b["sales"] += _f(r.get("sales"))
    b["orders"] += _f(r.get("orders")); b["clicks"] += _f(r.get("clicks"))
    b["impressions"] += _f(r.get("impressions"))


def _metrics(b: Dict[str, float]) -> Dict[str, Any]:
    s, sa, ck, im, od = b["spend"], b["sales"], b["clicks"], b["impressions"], b["orders"]
    return {"spend": round(s, 2), "sales": round(sa, 2), "orders": int(od), "clicks": int(ck),
            "impressions": int(im), "acos": (s / sa) if sa else None, "cpc": (s / ck) if ck else None,
            "rpc": (sa / ck) if ck else None, "cvr": (od / ck) if ck else None,
            "aov": (sa / od) if od else None}


async def _agg(sid: int, dataset: str, key_fn: Callable[[Dict[str, Any]], Any],
               capture: Tuple[str, ...],
               on_day: Optional[Callable[[], None]] = None) -> Dict[Any, Dict[str, Any]]:
    """Sum a per-day report over the window, bucketed by key_fn; capture static fields."""
    out: Dict[Any, Dict[str, Any]] = {}
    for day in _window_dates():
        if on_day:
            on_day()
        try:
            rep = await _data.fetch_dataset(dataset, {"sid": sid, "report_date": day, "length": 300}, ttl=_REPORT_TTL_S)
        except _gw.LingXingError:
            continue
        for r in (rep.get("rows") or []):
            k = key_fn(r)
            if k is None:
                continue
            b = out.get(k)
            if b is None:
                b = {"_b": _bucket()}
                for c in capture:
                    b[c] = r.get(c)
                out[k] = b
            _add(b["_b"], r)
    return out


async def _bid_map(sid: int) -> Dict[str, Dict[str, Any]]:
    m: Dict[str, Dict[str, Any]] = {}
    for offset in range(0, 3000, 300):
        try:
            res = await _data.fetch_dataset("sp_keywords", {"sid": sid, "length": 300, "offset": offset}, force=True)
        except _gw.LingXingError:
            break
        rows = res.get("rows") or []
        for k in rows:
            m[str(k.get("keyword_id"))] = {"bid": _f(k.get("bid")), "state": k.get("state")}
        if len(rows) < 300:
            break
    return m


async def _campaign_budgets(sid: int) -> Dict[str, Dict[str, Any]]:
    m: Dict[str, Dict[str, Any]] = {}
    try:
        res = await _data.fetch_dataset("sp_campaigns", {"sid": sid, "length": 300}, force=True)
        for c in (res.get("rows") or []):
            m[str(c.get("campaign_id"))] = {"daily_budget": _f(c.get("daily_budget")),
                                            "state": c.get("state"), "name": c.get("name")}
    except _gw.LingXingError:
        logger.debug("res = await _data.fetch_dataset 失败（旁路，已忽略）", exc_info=True)
    return m


async def _store_margin(sid: int) -> Optional[float]:
    """Average gross margin (fraction) from the profit report (7-day span)."""
    end = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    start = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    try:
        res = await _data.fetch_dataset("asin_profit", {"sids": str(sid), "startDate": start, "endDate": end, "length": 500})
    except _gw.LingXingError:
        return None
    rates = []
    for r in (res.get("rows") or []):
        gr = r.get("grossRate")
        if gr in (None, ""):
            continue
        v = _f(gr)
        if v > 1:  # percent → fraction
            v /= 100.0
        if 0 < v < 1:
            rates.append(v)
    return (sum(rates) / len(rates)) if rates else None


async def _campaign_margins(sid: int) -> Dict[str, float]:
    """campaign_id → avg gross margin (fraction) of its advertised ASINs.
    Best-effort: spProductAds (campaign→ASIN) joined with profit (ASIN→margin)."""
    camp_asins: Dict[str, set] = {}
    try:
        for offset in range(0, 4000, 200):
            res = await _data.fetch_dataset("sp_product_ads", {"sid": sid, "length": 200, "offset": offset}, force=True)
            rows = res.get("rows") or []
            for r in rows:
                a, cid = r.get("asin"), str(r.get("campaign_id"))
                if a and cid and cid != "None":
                    camp_asins.setdefault(cid, set()).add(str(a))
            if len(rows) < 200:
                break
    except _gw.LingXingError:
        return {}
    if not camp_asins:
        return {}
    end = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    start = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    asin_m: Dict[str, float] = {}
    try:
        res = await _data.fetch_dataset("asin_profit", {"sids": str(sid), "startDate": start, "endDate": end, "length": 1000})
        for r in (res.get("rows") or []):
            a, gr = str(r.get("asin") or ""), r.get("grossRate")
            if not a or gr in (None, ""):
                continue
            v = _f(gr)
            if v > 1:
                v /= 100.0
            if 0 < v < 1:
                asin_m[a] = v
    except _gw.LingXingError:
        return {}
    out: Dict[str, float] = {}
    for cid, asins in camp_asins.items():
        ms = [asin_m[a] for a in asins if a in asin_m]
        if ms:
            out[cid] = sum(ms) / len(ms)
    return out


def _targets() -> Tuple[Optional[float], float, str]:
    """Resolve (margin, target_acos, note). Manual overrides win; else derived."""
    c = _cfg()
    if _f(c.get("lingxing_target_acos_override")) > 0:
        return None, _f(c["lingxing_target_acos_override"]), "目标ACOS=手动设定"
    return None, -1, ""  # filled by caller with margin


async def _recent_touched(sid: int) -> set:
    """target_ids touched by executed/rolled_back tickets within the cooldown."""
    from app.services import lingxing_operate as _op
    days = int(_cfg().get("lingxing_cooldown_days") or 7)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    touched = set()
    for t in _op.list_tickets(200):
        if t.get("status") not in ("executed", "rolled_back"):
            continue
        intent = t.get("intent") or {}
        if str(intent.get("sid")) != str(sid):
            continue
        try:
            if datetime.fromisoformat(t.get("created_at")) < cutoff:
                continue
        except Exception:
            logger.debug("if datetime.fromisoformat 失败（旁路，已忽略）", exc_info=True)
        tid = intent.get("target_id") or intent.get("keyword_text")
        if tid:
            touched.add(str(tid))
    return touched


async def run_store(sid: int,
                    progress: Optional[Callable[[str, int, int], None]] = None) -> Dict[str, Any]:
    c = _cfg()
    factor = _f(c.get("lingxing_target_acos_factor")) or 0.7
    neg_clicks = int(c.get("lingxing_neg_min_clicks") or 15)
    bid_clicks = int(c.get("lingxing_bid_min_clicks") or 15)
    scale_orders = int(c.get("lingxing_scale_min_orders") or 3)
    harvest_orders = int(c.get("lingxing_harvest_min_orders") or 3)
    step = (_f(c.get("lingxing_bid_step_pct")) or 15) / 100.0
    floor = _f(c.get("lingxing_bid_floor")) or 0.02
    win = int(c.get("lingxing_opt_window_days") or 30)

    # progress: 3 report datasets × win days + 4 fixed lookups
    total_steps = 3 * win + 4
    state = {"done": 0}

    def tick(phase: str) -> None:
        state["done"] += 1
        if progress:
            try:
                progress(phase, min(state["done"], total_steps), total_steps)
            except Exception:  # noqa: BLE001 — progress must never break the run
                logger.debug("progress 失败（旁路，已忽略）", exc_info=True)

    margin = await _store_margin(sid)
    tick("读取店铺毛利")
    cmargins = await _campaign_margins(sid)
    tick("读取活动毛利")
    t_over, m_over = _f(c.get("lingxing_target_acos_override")), _f(c.get("lingxing_margin_override"))

    def margin_of(cid: Any) -> Optional[float]:
        if m_over > 0:
            return m_over
        cm = cmargins.get(str(cid))
        return cm if cm else margin

    def tgt(cid: Any) -> float:
        if t_over > 0:
            return t_over
        mm = margin_of(cid)
        return factor * mm if mm else 0.30

    def brk(cid: Any) -> Optional[float]:
        return None if t_over > 0 else margin_of(cid)

    if t_over > 0:
        target = t_over; breakeven = margin; note = "目标ACOS=手动设定"
    elif m_over > 0:
        margin = m_over; breakeven = margin; target = factor * margin
        note = f"毛利率=手动 {margin:.0%}，目标ACOS={target:.0%}"
    elif margin:
        breakeven = margin; target = factor * margin
        note = (f"毛利率≈{margin:.0%}(店铺均值)，目标ACOS={target:.0%}(={factor:g}×毛利)"
                + (f"；已对 {len(cmargins)} 个活动用各自产品毛利做 per-campaign 目标" if cmargins else ""))
    else:
        breakeven = None; target = 0.30; note = "未取到毛利数据，暂用默认目标ACOS 30%"

    touched = await _recent_touched(sid)
    cands: List[Dict[str, Any]] = []

    def sig_ok(target_id: Any) -> bool:
        return str(target_id) not in touched  # cooldown

    # ---- 否词 + 收割：search term report ----
    st = await _agg(sid, "sp_search_term_report",
                    lambda r: (str(r.get("campaign_id")), str(r.get("query") or "")) if r.get("query") else None,
                    capture=("query", "campaign_id", "ad_group_id", "match_type"),
                    on_day=lambda: tick("聚合搜索词报表"))
    for (cid, q), b in st.items():
        m = _metrics(b["_b"])
        if m["clicks"] >= neg_clicks and m["orders"] == 0 and sig_ok(q):
            cands.append({
                "lever": "否词", "op_type": "negate_keyword", "sid": sid,
                "target_name": q, "metrics": m, "opt_target": tgt(cid), "opt_breakeven": brk(cid),
                "rule": f"搜索词「{q}」{m['clicks']}点击/0单（≥{neg_clicks}点击）→ 否定(negativeExact)",
                "significance": f"{m['clicks']}点击 0单 · 花费{m['spend']}",
                "rationale": f"近{win}天该搜索词 {m['clicks']} 次点击 0 转化、花费 {m['spend']}，纯无效花费，建议否定。",
                "payload": {"op_type": "negate_keyword", "sid": sid, "campaign_id": cid,
                            "keyword_text": q, "match_type": "negativeExact",
                            "rationale": f"{m['clicks']}点击0单纯烧钱，否定"},
            })
        elif m["orders"] >= harvest_orders and m["acos"] is not None and (brk(cid) is None or m["acos"] <= brk(cid)):
            sug = round((m["rpc"] or 0) * tgt(cid), 2)
            cands.append({
                "lever": "收割", "op_type": "add_keyword", "advisory": True, "sid": sid,
                "target_name": q, "metrics": m, "opt_target": tgt(cid), "opt_breakeven": brk(cid),
                "harvest": {"query": q, "source_campaign_id": cid, "suggested_bid": sug, "match_type": "EXACT"},
                "rule": f"搜索词「{q}」{m['orders']}单（≥{harvest_orders}）、ACOS {m['acos']:.0%} → 收割成精准词",
                "significance": f"{m['orders']}单 ACOS {m['acos']:.0%}",
                "rationale": f"该搜索词 {m['orders']} 单、ACOS {m['acos']:.0%} 健康，建议加入精准活动（建议bid≈{sug}）并在原活动否定它（毕业）。",
            })

    # ---- 降bid / 加bid：keyword report + live bids ----
    kr = await _agg(sid, "sp_keyword_report", lambda r: str(r.get("keyword_id")) if r.get("keyword_id") else None,
                    capture=("keyword_id", "keyword_text", "match_type", "campaign_id"),
                    on_day=lambda: tick("聚合关键词报表"))
    bids = await _bid_map(sid) if kr else {}
    tick("读取当前竞价")
    for kid, b in kr.items():
        m = _metrics(b["_b"])
        cur = bids.get(kid, {}).get("bid")
        name = b.get("keyword_text") or kid
        if m["clicks"] < bid_clicks or not sig_ok(kid):
            continue
        T, B = tgt(b.get("campaign_id")), brk(b.get("campaign_id"))
        # 降bid: high ACOS or high-spend-no-convert
        if m["acos"] is not None and m["acos"] > T and cur:
            ideal = (m["rpc"] or 0) * T
            new_bid = max(floor, min(ideal, cur * (1 - step)))
            if new_bid < cur * 0.98:
                cands.append(_bid_cand("降bid", sid, kid, name, cur, round(new_bid, 2), m, T, B,
                    f"ACOS {m['acos']:.0%} > 目标 {T:.0%}（{m['clicks']}点击≥{bid_clicks}）→ 降bid至 RPC×目标，单步≤{int(step*100)}%",
                    f"高ACOS控本：{cur}→{round(new_bid,2)}"))
        elif m["orders"] == 0 and cur:  # spent enough, no order
            new_bid = max(floor, cur * (1 - step))
            if new_bid < cur * 0.98:
                cands.append(_bid_cand("降bid", sid, kid, name, cur, round(new_bid, 2), m, T, B,
                    f"{m['clicks']}点击 0单（花费{m['spend']}）→ 降bid {int(step*100)}%",
                    f"高点击0单：{cur}→{round(new_bid,2)}（持续无效可考虑暂停）"))
        # 加bid: winner below target
        elif m["orders"] >= scale_orders and m["acos"] is not None and m["acos"] <= 0.8 * T and cur:
            ideal = (m["rpc"] or 0) * T
            new_bid = min(cur * (1 + step), ideal)
            if new_bid > cur * 1.02:
                cands.append(_bid_cand("加bid", sid, kid, name, cur, round(new_bid, 2), m, T, B,
                    f"ACOS {m['acos']:.0%} ≤ 0.8×目标、{m['orders']}单 → 放量 +≤{int(step*100)}%（不超 RPC×目标）",
                    f"赢家放量：{cur}→{round(new_bid,2)}"))

    # ---- 加预算：campaign report + budgets ----
    cr = await _agg(sid, "sp_campaign_report", lambda r: str(r.get("campaign_id")) if r.get("campaign_id") else None,
                    capture=("campaign_id",),
                    on_day=lambda: tick("聚合活动报表"))
    budgets = await _campaign_budgets(sid) if cr else {}
    tick("读取活动预算")
    for cid, b in cr.items():
        m = _metrics(b["_b"])
        info = budgets.get(cid) or {}
        bud = info.get("daily_budget")
        if not bud or not sig_ok(cid):
            continue
        avg_daily = m["spend"] / max(1, win)
        if avg_daily >= 0.85 * bud and m["acos"] is not None and m["acos"] <= tgt(cid):
            new_bud = round(bud * (1 + step), 2)
            cands.append({
                "lever": "加预算", "op_type": "campaign_budget", "sid": sid,
                "target_name": info.get("name") or cid, "metrics": m,
                "opt_target": tgt(cid), "opt_breakeven": brk(cid),
                "current": {"daily_budget": bud}, "proposed": {"daily_budget": new_bud},
                "change_pct": round(step * 100, 1),
                "rule": f"日均花费 {avg_daily:.1f} ≈ 打满预算 {bud}、ACOS {m['acos']:.0%} ≤ 目标 {tgt(cid):.0%} → 预算 +{int(step*100)}%",
                "significance": f"利用率≈{min(100, int(avg_daily/bud*100))}% ACOS {m['acos']:.0%}",
                "rationale": f"活动预算打满且盈利（ACOS {m['acos']:.0%}≤目标 {tgt(cid):.0%}），扩量。",
                "payload": {"op_type": "campaign_budget", "sid": sid, "target_id": cid,
                            "target_name": info.get("name") or cid, "new_value": new_bud,
                            "rationale": f"预算打满且ACOS {m['acos']:.0%}达标，扩量+{int(step*100)}%"},
            })

    # carry the rule trail into each ticket payload (for review rubric + report)
    for cc in cands:
        if cc.get("payload"):
            cc["payload"]["opt"] = {"lever": cc["lever"], "rule": cc["rule"],
                                    "significance": cc.get("significance"), "metrics": cc.get("metrics"),
                                    "target_acos": round(cc.get("opt_target", target) or target, 4),
                                    "breakeven_acos": cc.get("opt_breakeven", breakeven)}

    order = {"否词": 0, "收割": 1, "降bid": 2, "加bid": 3, "加预算": 4}
    cands.sort(key=lambda c: (order.get(c["lever"], 9), -(c["metrics"].get("spend") or 0)))
    return {
        "sid": sid, "window_days": win, "margin": margin, "target_acos": target,
        "breakeven_acos": breakeven, "per_campaign": bool(cmargins), "note": note,
        "count": len(cands), "candidates": cands,
    }


# --- background runs (persisted, with progress) ------------------------------
def _save_opt_run(run: Dict[str, Any]) -> None:
    conn = _gw.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO lingxing_optimizer_runs "
            "(id,sid,started_at,finished_at,status,phase,done,total,summary,result_json,error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run["id"], run.get("sid"), run.get("started_at"), run.get("finished_at"),
             run.get("status"), run.get("phase", ""), int(run.get("done") or 0),
             int(run.get("total") or 0), run.get("summary", ""),
             json.dumps(run.get("result"), ensure_ascii=False, default=str) if run.get("result") is not None else "",
             run.get("error", "")))
        conn.commit()
    finally:
        conn.close()


def list_opt_runs(sid: Optional[int] = None, limit: int = 20) -> List[Dict[str, Any]]:
    conn = _gw.connect()
    try:
        q = ("SELECT id,sid,started_at,finished_at,status,phase,done,total,summary,error "
             "FROM lingxing_optimizer_runs ")
        args: List[Any] = []
        if sid is not None:
            q += "WHERE sid=? "
            args.append(int(sid))
        q += "ORDER BY started_at DESC LIMIT ?"
        args.append(int(limit))
        cur = conn.execute(q, args)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def get_opt_run(run_id: str) -> Optional[Dict[str, Any]]:
    conn = _gw.connect()
    try:
        cur = conn.execute("SELECT * FROM lingxing_optimizer_runs WHERE id=?", (run_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cur.description]
        d = dict(zip(cols, row))
    finally:
        conn.close()
    try:
        d["result"] = json.loads(d.pop("result_json") or "null")
    except Exception:
        d["result"] = None
    return d


def start_background_run(sid: int) -> Dict[str, Any]:
    """Kick one rule-engine run in the background; returns the run row
    immediately so the UI can poll ``get_opt_run`` for progress + result."""
    run: Dict[str, Any] = {
        "id": uuid.uuid4().hex[:12], "sid": int(sid),
        "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
        "status": "running", "phase": "启动中", "done": 0, "total": 0,
        "summary": "", "result": None, "error": "",
    }
    _save_opt_run(run)

    def _progress(phase: str, done: int, total: int) -> None:
        run.update(phase=phase, done=done, total=total)
        _save_opt_run(run)

    async def _go() -> None:
        try:
            res = await run_store(int(sid), progress=_progress)
            run.update(status="done", phase="完成", result=res,
                       summary=f"候选 {res.get('count', 0)} 条 · {res.get('note', '')}"[:200],
                       finished_at=datetime.now(timezone.utc).isoformat())
        except Exception as e:  # noqa: BLE001
            run.update(status="failed", error=str(e)[:500], phase="失败",
                       finished_at=datetime.now(timezone.utc).isoformat())
        _save_opt_run(run)

    asyncio.create_task(_go(), name=f"lingxing-opt-{run['id']}")
    return run


def _bid_cand(lever, sid, kid, name, cur, new_bid, m, target, breakeven, rule, rationale):
    return {
        "lever": lever, "op_type": "keyword_bid", "sid": sid, "target_id": kid, "target_name": name,
        "metrics": m, "current": {"bid": cur}, "proposed": {"bid": new_bid},
        "opt_target": target, "opt_breakeven": breakeven,
        "change_pct": round((new_bid - cur) / cur * 100, 1) if cur else None,
        "rule": rule, "significance": f"{m['clicks']}点击/{m['orders']}单",
        "rationale": rationale,
        "payload": {"op_type": "keyword_bid", "sid": sid, "target_id": kid, "target_name": name,
                    "new_value": new_bid, "rationale": rationale},
    }
