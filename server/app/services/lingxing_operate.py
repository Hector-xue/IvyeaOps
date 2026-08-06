"""领星 controlled write execution (P3) — the safety-critical path.

A write never happens casually. Each proposed change becomes a **ticket** that
must clear, in order:

1. **Triple independent review** — three fresh LLM passes with distinct personas
   (data-rigour / devil's-advocate / business-balance); ALL must approve and the
   worst risk score must stay under threshold. (generate_text is single-provider,
   so independence = separate calls + adversarial framing — honest about that.)
2. **Deterministic guardrails** (code, not LLM, cannot be reasoned around):
   operate switch active, store in scope (empty scope = nothing writable),
   magnitude ≤ max_change_pct, sane budget/state.
3. **Human final confirmation** in the UI (locked on by decision).

Only then does it execute via the gateway (``allow_write=True``), after capturing
a rollback snapshot. Failures trip a circuit breaker (auto-disable operate +
alert). Everything is audited.
"""
from __future__ import annotations

from app.core.proc import no_window_kwargs

import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.core import hub_settings as _hs
from app.services import ai_synthesis_service as _ai
from app.services import lingxing_data as _data
from app.services import lingxing_service as _gw

PUT_SP_CAMPAIGN_ROUTE = "/basicOpen/adReport/manage/putSpCampaign"
_RISK_THRESHOLD = 0.5

# Supported write operations. All SP `put*` endpoints share one shape:
# {sid, <array>:[{<id_field>, isBaseValue:0, state?, <num_field>/budget}]}. Each
# op carries its numeric field (for magnitude guardrail + snapshot/rollback) and,
# where a read exists, the dataset to snapshot the live value before executing.
_F_STATE = {"name": "new_state", "label": "状态", "type": "select", "options": ["", "enabled", "paused"]}


def _modify_fields(id_label: str, val_label: str) -> List[Dict[str, Any]]:
    return [
        {"name": "target_id", "label": id_label, "type": "text", "required": True},
        {"name": "target_name", "label": "名称(可选)", "type": "text"},
        {"name": "new_value", "label": f"目标{val_label}", "type": "number"},
        _F_STATE,
    ]


OP_TYPES: Dict[str, Dict[str, Any]] = {
    "campaign_budget": {
        "category": "modify", "label": "广告活动·预算/启停", "route": PUT_SP_CAMPAIGN_ROUTE,
        "array": "campaigns", "id_field": "campaignId", "num_field": "daily_budget", "num_label": "日预算",
        "snapshot_dataset": "sp_campaigns", "snapshot_id": "campaign_id", "snapshot_value": "daily_budget",
        "reversible": True, "fields": _modify_fields("活动ID", "日预算"),
    },
    "keyword_bid": {
        "category": "modify", "label": "关键词·竞价/启停", "route": "/basicOpen/adReport/manage/putSpKeyword",
        "array": "keywords", "id_field": "keywordId", "num_field": "bid", "num_label": "竞价bid",
        "snapshot_dataset": "sp_keywords", "snapshot_id": "keyword_id", "snapshot_value": "bid",
        "reversible": True, "fields": _modify_fields("关键词ID", "竞价"),
    },
    "target_bid": {
        "category": "modify", "label": "定向·竞价/启停", "route": "/basicOpen/adReport/manage/putSpTarget",
        "array": "targetingClauses", "id_field": "targetId", "num_field": "bid", "num_label": "竞价bid",
        "snapshot_dataset": "sp_targets", "snapshot_id": "target_id", "snapshot_value": "bid",
        "reversible": True, "fields": _modify_fields("定向ID", "竞价"),
    },
    "adgroup_bid": {
        "category": "modify", "label": "广告组·默认竞价/启停", "route": "/basicOpen/adReport/manage/putSpAdGroup",
        "array": "adGroups", "id_field": "adGroupId", "num_field": "defaultBid", "num_label": "默认竞价",
        "snapshot_dataset": "sp_adgroups", "snapshot_id": "ad_group_id", "snapshot_value": "default_bid",
        "reversible": True, "fields": _modify_fields("广告组ID", "默认竞价"),
    },
    # --- add-type ops (create entities; reversal differs) -------------------
    "add_keyword": {
        "category": "add", "label": "加词(投放关键词)", "route": "/basicOpen/adReport/spTarget/addKeywords",
        "body_key": "keywords", "has_bid": True, "reversible": False,
        "match_options": ["EXACT", "PHRASE", "BROAD"],
        "fields": [
            {"name": "campaign_id", "label": "活动ID", "type": "text", "required": True},
            {"name": "ad_group_id", "label": "广告组ID", "type": "text", "required": True},
            {"name": "keyword_text", "label": "关键词", "type": "text", "required": True},
            {"name": "match_type", "label": "匹配", "type": "select", "options": ["EXACT", "PHRASE", "BROAD"]},
            {"name": "bid", "label": "竞价", "type": "number"},
        ],
    },
    "negate_keyword": {
        "category": "add", "label": "否词(加否定关键词)", "route": "/basicOpen/adReport/spTarget/addNegativeKeywords",
        "body_key": "negativeKeywords", "has_bid": False, "reversible": True,
        "archive_route": "/basicOpen/adReport/spTarget/archiveNegatives",
        "match_options": ["negativeExact", "negativePhrase"],
        "fields": [
            {"name": "campaign_id", "label": "活动ID", "type": "text", "required": True},
            {"name": "ad_group_id", "label": "广告组ID(空=活动级)", "type": "text"},
            {"name": "keyword_text", "label": "否定词", "type": "text", "required": True},
            {"name": "match_type", "label": "匹配", "type": "select", "options": ["negativeExact", "negativePhrase"]},
        ],
    },
}


def op_types_catalog() -> List[Dict[str, Any]]:
    return [{"key": k, "label": v["label"], "category": v["category"],
             "num_label": v.get("num_label"), "reversible": v.get("reversible", False),
             "fields": v["fields"]} for k, v in OP_TYPES.items()]

_REVIEWERS = [
    ("数据严谨派", "你是只看数据、最严谨的审核员。只有当数据充分支撑该调整、且改动幅度与依据匹配时才批准。"),
    ("魔鬼代言人", "你是风险厌恶的魔鬼代言人。先假设这个调整是有害的，竭力找出它可能造成的负面后果、被数据噪声误导的可能、以及最坏情况；只有在找不到重大风险时才勉强批准。"),
    ("业务平衡派", "你是资深运营，权衡投入产出与业务目标，判断该调整是否真正划算、是否符合常识。"),
]

_op_lock = asyncio.Lock()


# --- persistence ------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save(t: Dict[str, Any]) -> None:
    t["updated_at"] = _now()
    conn = _gw.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO lingxing_op_ticket "
            "(id,created_at,updated_at,source,status,intent_json,reviews_json,"
            "guardrail_json,snapshot_json,result_json,decided_by,error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (t["id"], t.get("created_at"), t["updated_at"], t.get("source"), t.get("status"),
             _j(t.get("intent")), _j(t.get("reviews")), _j(t.get("guardrail")),
             _j(t.get("snapshot")), _j(t.get("result")), t.get("decided_by", ""), t.get("error", "")))
        conn.commit()
    finally:
        conn.close()


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str) if v is not None else ""


def list_tickets(limit: int = 50) -> List[Dict[str, Any]]:
    conn = _gw.connect()
    try:
        cur = conn.execute(
            "SELECT id,created_at,source,status,intent_json,decided_by,error "
            "FROM lingxing_op_ticket ORDER BY created_at DESC LIMIT ?", (int(limit),))
        cols = [c[0] for c in cur.description]
        out = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            try:
                d["intent"] = json.loads(d.pop("intent_json") or "null")
            except Exception:
                d["intent"] = None
            out.append(d)
        return out
    finally:
        conn.close()


def get_ticket(tid: str) -> Optional[Dict[str, Any]]:
    conn = _gw.connect()
    try:
        cur = conn.execute("SELECT * FROM lingxing_op_ticket WHERE id=?", (tid,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cur.description]
        d = dict(zip(cols, row))
    finally:
        conn.close()
    for k in ("intent", "reviews", "guardrail", "snapshot", "result"):
        try:
            d[k] = json.loads(d.pop(k + "_json") or "null")
        except Exception:
            d[k] = None
    return d


# --- best-effort alert ------------------------------------------------------
async def send_alert(text: str) -> None:
    url = (_hs.get("alert_webhook") or "").strip()
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            await c.post(url, json={"msg_type": "text", "content": {"text": f"[领星操作] {text}"}})
    except Exception:
        pass


# --- operate switch ---------------------------------------------------------
def enable_operate() -> Dict[str, Any]:
    ttl = int(_hs.get("lingxing_operate_ttl_minutes") or 120)
    exp = (datetime.now(timezone.utc) + timedelta(minutes=ttl)).isoformat()
    # re-enabling acknowledges + clears any tripped circuit breaker
    _hs.save({"lingxing_operate_enabled": True, "lingxing_operate_expires_at": exp,
              "lingxing_circuit_reason": ""})
    return _gw.status()


def disable_operate() -> Dict[str, Any]:
    _hs.save({"lingxing_operate_enabled": False, "lingxing_operate_expires_at": ""})
    return _gw.status()


def _recently_touched_sync(sid: Any, days: int) -> Dict[str, str]:
    """target_id/keyword → last op time, for entities executed within ``days``."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(days))
    out: Dict[str, str] = {}
    for t in list_tickets(200):
        if t.get("status") not in ("executed", "rolled_back"):
            continue
        intent = t.get("intent") or {}
        if str(intent.get("sid")) != str(sid):
            continue
        ts = t.get("created_at") or ""
        try:
            if datetime.fromisoformat(ts) < cutoff:
                continue
        except Exception:
            pass
        k = str(intent.get("target_id") or intent.get("keyword_text") or "")
        if k:
            out[k] = ts
    return out


# --- deterministic guardrails ----------------------------------------------
def check_guardrails(intent: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _hs.load()
    checks: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = ""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    # store scope (empty whitelist = nothing writable — fail closed)
    scope = str(cfg.get("lingxing_scope_stores") or "").replace("，", ",")
    allowed = {s.strip() for s in scope.split(",") if s.strip()}
    sid = str(intent.get("sid"))
    add("store_scope", bool(allowed) and sid in allowed,
        "店铺在白名单" if (allowed and sid in allowed) else
        ("scope 为空，默认禁止所有写操作" if not allowed else f"店铺 {sid} 不在白名单"))

    op = OP_TYPES.get(intent.get("op_type") or "")
    add("op_type_known", bool(op), op["label"] if op else f"未知操作类型 {intent.get('op_type')}")

    if op and op["category"] == "add":
        kw = (intent.get("keyword_text") or "").strip()
        add("keyword_present", bool(kw), f"词「{kw}」" if kw else "缺少词")
        add("campaign_present", bool(intent.get("campaign_id")),
            "有活动ID" if intent.get("campaign_id") else "缺少活动ID")
        mt = intent.get("match_type")
        add("match_type_valid", mt in op["match_options"], f"匹配 {mt}")
    else:
        nf = op["num_field"] if op else "daily_budget"
        nlabel = op["num_label"] if op else "数值"
        max_pct = float(cfg.get("lingxing_max_change_pct") or 20)
        change = intent.get("change") or {}
        before = intent.get("before") or {}
        pct_ok, pct_detail = True, f"无{nlabel}变更"
        if change.get(nf) is not None and before.get(nf):
            try:
                old, new = float(before[nf]), float(change[nf])
                pct = abs(new - old) / old * 100 if old else 999
                pct_ok = pct <= max_pct
                pct_detail = f"{nlabel}幅度 {pct:.1f}% ≤ {max_pct}%" if pct_ok else f"{nlabel}幅度 {pct:.1f}% 超过上限 {max_pct}%"
            except (TypeError, ValueError, ZeroDivisionError):
                pct_ok, pct_detail = False, "无法计算幅度"
        add("change_magnitude", pct_ok, pct_detail)
        nv = change.get(nf)
        add("value_positive", nv is None or float(nv) > 0, "" if nv is None else f"新{nlabel} {nv}")
        ns = change.get("state")
        add("state_valid", ns is None or ns in ("enabled", "paused"), "" if ns is None else f"state={ns}")
        # bid floor/ceiling (ad guardrail)
        if op and nf in ("bid", "defaultBid") and nv is not None:
            try:
                floor = float(cfg.get("lingxing_bid_floor") or 0.02)
                ceil = float(cfg.get("lingxing_bid_ceiling") or 0)
                bok = (float(nv) >= floor) and (ceil <= 0 or float(nv) <= ceil)
                add("bid_bounds", bok, f"bid {nv} ∈ [{floor}, {ceil or '∞'}]")
            except (TypeError, ValueError):
                add("bid_bounds", False, "bid 非法")

    # cooldown — don't re-touch the same entity within N days (anti-thrash)
    cool_days = int(cfg.get("lingxing_cooldown_days") or 7)
    tkey = str(intent.get("target_id") or intent.get("keyword_text") or "")
    if tkey and cool_days > 0:
        hit = _recently_touched_sync(intent.get("sid"), cool_days).get(tkey)
        add("cooldown", hit is None,
            f"近{cool_days}天已操作过该对象（{hit[:10]}）" if hit else f"近{cool_days}天未重复操作")

    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks}


# --- triple independent review ---------------------------------------------
def _parse_review(text: str) -> Dict[str, Any]:
    t = text.strip()
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if m:
        t = m.group(0)
    try:
        obj = json.loads(t)
        return {"approve": bool(obj.get("approve")),
                "risk_score": float(obj.get("risk_score", 1)),
                "reasons": str(obj.get("reasons", ""))[:600]}
    except Exception:
        return {"approve": False, "risk_score": 1.0, "reasons": "复核响应解析失败（fail-closed 视为不通过）"}


# 可手动选择的 CLI 复核 agent。2026-08-06：ivyea-agent 走上面的 http/agent 分支，
# 这里只列外部 CLI；hermes 保留为手动可选项，但已不是任何默认。
_CLI_AGENTS = ("claude", "codex", "hermes")


def _custom_models() -> Dict[str, Dict[str, Any]]:
    try:
        arr = json.loads(_hs.get("lingxing_custom_models") or "[]")
        return {str(m.get("id")): m for m in arr if m.get("id")}
    except Exception:
        return {}


def available_providers() -> List[Dict[str, Any]]:
    """All selectable review providers + availability (for the config UI)."""
    from app.services.runners import _find_bin
    from app.services import ivyea_agent_service as _ivyea
    ivyea_status = _ivyea.availability()
    out = [
        {"id": "ivyea-agent", "label": "IvyeaAgent", "kind": "agent", "ok": bool(ivyea_status.get("available"))},
        {"id": "assistant", "label": "全局兜底大模型", "kind": "http", "ok": bool(_ai.assistant_text_cfg().get("api_key"))},
        {"id": "deepseek", "label": "DeepSeek", "kind": "http", "ok": bool(_ai._deepseek_key())},
        {"id": "apimart", "label": "Apimart(Claude)", "kind": "http", "ok": bool(_ai._apimart_key())},
    ]
    for a in _CLI_AGENTS:
        out.append({"id": a, "label": f"{a}(智能体)", "kind": "cli", "ok": bool(_find_bin(a))})
    for cid, m in _custom_models().items():
        out.append({"id": f"custom:{cid}", "label": m.get("label") or cid, "kind": "custom",
                    "ok": bool(m.get("base_url") and m.get("model"))})
    return out


async def _run_cli_agent(runner: str, prompt: str) -> str:
    from app.services.runners import _find_bin, _build_runner_cmd, build_child_env
    binary = _find_bin(runner)
    if not binary:
        raise RuntimeError(f"{runner} 未安装")
    cmd = _build_runner_cmd(runner, binary, prompt)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL, env=build_child_env(binary),
        **no_window_kwargs())
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"{runner} 复核超时")
    text = (out or b"").decode("utf-8", "replace").strip()
    if not text:
        raise RuntimeError(f"{runner} 返回空")
    return text


async def _review_generate(provider: str, prompt: str) -> str:
    """Route a review prompt to its provider: http builtin / CLI agent / custom."""
    p = (provider or "").strip()
    if p in _CLI_AGENTS:
        return await _run_cli_agent(p, prompt)
    if p.startswith("custom:"):
        m = _custom_models().get(p.split(":", 1)[1])
        if not m:
            raise RuntimeError(f"未找到自定义模型 {p}")
        return await _ai.generate_openai_compat(m.get("base_url"), m.get("api_key"), m.get("model"), prompt)
    return await _ai.generate_text_provider(p, prompt)


async def _one_review(persona: str, framing: str, intent: Dict[str, Any],
                      provider: str = "deepseek") -> Dict[str, Any]:
    prompt = f"""{framing}

待审操作（仅审核，不要执行）：
{json.dumps(intent, ensure_ascii=False, indent=1)}

请严格对照下述「优化方法论/规则」，逐条审视该操作是否成立、风险多大（数据不足 → 否决）：
────────
{_hs.get("lingxing_rules_doc") or ""}
────────
若 intent 带 opt(规则依据/指标)，核对规则是否被正确应用、阈值是否真的达到。

只输出 JSON：{{"approve": true/false, "risk_score": 0~1, "reasons": "中文理由"}}
approve=是否批准；risk_score=重大风险概率(越高越危险)；理由要具体、点名关键指标。"""
    used = provider
    try:
        raw = await _review_generate(provider, prompt)
        r = _parse_review(raw)
    except Exception:  # noqa: BLE001 — that provider unavailable → fall back to chain
        try:
            raw = await _ai.generate_text(prompt)
            used = "fallback"
            r = _parse_review(raw)
        except Exception as e:  # noqa: BLE001
            r = {"approve": False, "risk_score": 1.0, "reasons": f"复核模型不可用：{e}（fail-closed）"}
            used = "none"
    r["reviewer"] = persona
    r["provider"] = used
    return r


async def review_intent(intent: Dict[str, Any]) -> Dict[str, Any]:
    provs = str(_hs.get("lingxing_review_providers") or "ivyea-agent,deepseek,assistant").replace("，", ",").split(",")
    # the three personas are independent by design — run them concurrently
    tasks = []
    for i, (persona, framing) in enumerate(_REVIEWERS):
        prov = (provs[i].strip() if i < len(provs) and provs[i].strip() else "deepseek")
        tasks.append(_one_review(persona, framing, intent, prov))
    reviews = list(await asyncio.gather(*tasks))
    approved = all(r["approve"] for r in reviews) and max(r["risk_score"] for r in reviews) <= _RISK_THRESHOLD
    return {"approved": approved, "reviews": reviews,
            "max_risk": max(r["risk_score"] for r in reviews)}


# --- ticket lifecycle -------------------------------------------------------
def _verdict_status(reviewed_ok: bool, guard_ok: bool) -> str:
    if not guard_ok:
        return "guardrail_blocked"
    if not reviewed_ok:
        return "review_rejected"
    return "awaiting_human"


async def _refresh_live_before(intent: Dict[str, Any]) -> None:
    """For modify-type ops, replace the recorded ``before`` with the LIVE value
    read from LingXing (don't trust hand-typed/stale numbers for the magnitude
    guardrail), and recompute change_pct. Best-effort: master off / not found
    keeps the recorded value."""
    op = OP_TYPES.get(intent.get("op_type") or "")
    if not op or op["category"] == "add":
        return
    nf = op["num_field"]
    try:
        live = await _current_value(intent)
    except Exception:  # noqa: BLE001
        return
    before = dict(intent.get("before") or {})
    if live.get(nf) is not None:
        before[nf] = live[nf]
    if live.get("state"):
        before.setdefault("state", live["state"])
    intent["before"] = before
    change = intent.get("change") or {}
    if change.get(nf) is not None and before.get(nf):
        try:
            intent["change_pct"] = round(
                (float(change[nf]) - float(before[nf])) / float(before[nf]) * 100, 1)
        except (TypeError, ValueError, ZeroDivisionError):
            pass


async def _process_ticket(tid: str) -> None:
    """Background pipeline for a fresh ticket: live-before refresh → guardrails
    → triple review → verdict. Runs after create_ticket() has already returned,
    so the HTTP caller never blocks on LLM latency."""
    t = get_ticket(tid)
    if not t or t.get("status") != "reviewing":
        return
    intent = t["intent"] or {}
    try:
        await _refresh_live_before(intent)
        t["intent"] = intent
        guard = check_guardrails(intent)
        t["guardrail"] = guard
        if not guard["ok"]:
            # hard-blocked by code — no point spending LLM reviews
            t["status"] = "guardrail_blocked"
            _save(t)
            return
        _save(t)
        rev = await review_intent(intent)
        t["reviews"] = rev
        t["status"] = _verdict_status(rev["approved"], guard["ok"])
        _save(t)
    except Exception as e:  # noqa: BLE001 — never leave a ticket stuck in reviewing
        t["status"] = "failed"
        t["error"] = f"复核流程异常：{e}"
        _save(t)


async def create_ticket(intent: Dict[str, Any], source: str = "manual") -> Dict[str, Any]:
    """Create a ticket and kick its review pipeline in the background.
    Returns immediately with status ``reviewing``; guardrails + triple review
    update it to awaiting_human / guardrail_blocked / review_rejected. Nothing
    executes here."""
    t: Dict[str, Any] = {
        "id": uuid.uuid4().hex[:12], "created_at": _now(), "source": source,
        "status": "reviewing", "intent": intent, "reviews": None, "guardrail": None,
        "snapshot": None, "result": None, "decided_by": "", "error": "",
    }
    _save(t)
    asyncio.create_task(_process_ticket(t["id"]), name=f"lingxing-ticket-{t['id']}")
    return t


async def create_tickets_from_run(run_id: str) -> Dict[str, Any]:
    from app.services import lingxing_automation as _auto
    run = _auto.get_run(run_id)
    if not run:
        raise _gw.LingXingError("未找到该分析运行")
    max_ops = int(_hs.get("lingxing_max_ops_per_run") or 10)
    created = []
    for p in (run.get("proposals") or [])[:max_ops]:
        action = p.get("action")
        if action in (None, "keep"):
            continue
        change: Dict[str, Any] = {}
        prop = p.get("proposed") or {}
        if action in ("increase_budget", "decrease_budget") and prop.get("daily_budget") is not None:
            change["daily_budget"] = prop["daily_budget"]
        if action in ("pause", "enable"):
            change["state"] = "paused" if action == "pause" else "enabled"
        if not change:
            continue
        intent = {
            "op_type": "campaign_budget", "op_label": OP_TYPES["campaign_budget"]["label"],
            "sid": p.get("sid"), "target_id": str(p.get("campaign_id")),
            "target_name": p.get("campaign_name"),
            "change": change, "before": p.get("current") or {},
            "change_pct": p.get("change_pct"), "rationale": p.get("rationale"),
            "source_proposal": p,
        }
        created.append(await create_ticket(intent, source=f"run:{run_id}"))
    return {"created": len(created), "tickets": [t["id"] for t in created]}


async def _current_value(intent: Dict[str, Any]) -> Dict[str, Any]:
    """Live snapshot of the target's numeric value + state, if a read exists for
    this op type; otherwise fall back to the intent's recorded ``before``."""
    op = OP_TYPES.get(intent.get("op_type") or "")
    if not op or not op.get("snapshot_dataset"):
        return dict(intent.get("before") or {})
    nf, vf = op["num_field"], op.get("snapshot_value", op["num_field"])
    # page through the entity list to find the target (large accounts)
    for offset in range(0, 2000, 300):
        res = await _data.fetch_dataset(
            op["snapshot_dataset"], {"sid": int(intent["sid"]), "length": 300, "offset": offset}, force=True)
        rows = res.get("rows") or []
        for c in rows:
            if str(c.get(op["snapshot_id"])) == str(intent["target_id"]):
                return {nf: c.get(vf), "state": c.get("state")}
        if len(rows) < 300:
            break
    return dict(intent.get("before") or {})


def build_body(intent: Dict[str, Any]) -> Dict[str, Any]:
    """Construct the request body for any supported op type."""
    op = OP_TYPES.get(intent.get("op_type") or "")
    if not op:
        raise _gw.LingXingError(f"未知操作类型: {intent.get('op_type')}")
    if op["category"] == "add":
        item: Dict[str, Any] = {
            "campaignId": str(intent["campaign_id"]),
            "keyword": intent["keyword_text"],
            "matchType": intent["match_type"], "state": "ENABLED",
        }
        if intent.get("ad_group_id"):
            item["adGroupId"] = str(intent["ad_group_id"])
        if op.get("has_bid") and intent.get("bid") is not None:
            item["bid"] = float(intent["bid"])
        return {"sid": int(intent["sid"]), op["body_key"]: [item]}
    ch = intent.get("change") or {}
    nf = op["num_field"]
    item: Dict[str, Any] = {op["id_field"]: int(intent["target_id"]), "isBaseValue": 0}
    if ch.get("state"):
        item["state"] = ch["state"]
    if ch.get(nf) is not None:
        if nf == "daily_budget":  # campaign budget is a nested object
            item["budget"] = {"budgetType": "DAILY", "budget": float(ch[nf])}
        else:                      # keyword/target bid, adgroup defaultBid
            item[nf] = float(ch[nf])
    return {"sid": int(intent["sid"]), op["array"]: [item]}


def _extract_target_ids(res: Any) -> List[str]:
    """Pull the created targetId(s) from an add-keyword/negative response."""
    ids: List[str] = []
    data = (res or {}).get("data") if isinstance(res, dict) else None
    if isinstance(data, dict):
        for key in ("success", "successTargets", "results", "successKeywords"):
            v = data.get(key)
            if isinstance(v, list):
                for it in v:
                    tid = (it or {}).get("targetId") or (it or {}).get("keywordId")
                    if tid:
                        ids.append(str(tid))
    return ids


def _op_summary(intent: Dict[str, Any]) -> str:
    op = OP_TYPES.get(intent.get("op_type") or "")
    if op and op["category"] == "add":
        return f"{op['label']}「{intent.get('keyword_text')}」({intent.get('match_type')}) 活动{intent.get('campaign_id')}"
    return f"{intent.get('change')}"


async def create_manual_ticket(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build a ticket from a hand-entered operation (any supported op type)."""
    op_type = payload.get("op_type")
    op = OP_TYPES.get(op_type or "")
    if not op:
        raise _gw.LingXingError(f"不支持的操作类型: {op_type}")
    if not payload.get("sid"):
        raise _gw.LingXingError("缺少 sid")

    if op["category"] == "add":
        kw = (payload.get("keyword_text") or "").strip()
        if not payload.get("campaign_id") or not kw:
            raise _gw.LingXingError("加词/否词需 活动ID + 词")
        intent: Dict[str, Any] = {
            "op_type": op_type, "op_label": op["label"], "sid": payload["sid"],
            "campaign_id": str(payload["campaign_id"]),
            "ad_group_id": str(payload["ad_group_id"]) if payload.get("ad_group_id") else None,
            "keyword_text": kw, "match_type": payload.get("match_type") or op["match_options"][0],
            "target_name": kw, "rationale": payload.get("rationale") or "(人工新建)",
        }
        if op.get("has_bid") and payload.get("bid") not in (None, ""):
            intent["bid"] = float(payload["bid"])
        if payload.get("opt"):
            intent["opt"] = payload["opt"]
        return await create_ticket(intent, source="manual")

    nf = op["num_field"]
    if not payload.get("target_id"):
        raise _gw.LingXingError("缺少 目标ID")
    change: Dict[str, Any] = {}
    before: Dict[str, Any] = {}
    if payload.get("new_value") not in (None, ""):
        change[nf] = float(payload["new_value"])
    if payload.get("new_state"):
        change["state"] = payload["new_state"]
    if payload.get("cur_value") not in (None, ""):
        before[nf] = float(payload["cur_value"])
    if payload.get("cur_state"):
        before["state"] = payload["cur_state"]
    if not change:
        raise _gw.LingXingError("未指定任何改动（数值或状态）")
    # NOTE: the LIVE current value is fetched in the background pipeline
    # (_refresh_live_before) so ticket creation stays instant; the hand-typed
    # value below is only the provisional display until then.
    change_pct = None
    if change.get(nf) is not None and before.get(nf):
        try:
            change_pct = round((float(change[nf]) - float(before[nf])) / float(before[nf]) * 100, 1)
        except (TypeError, ValueError, ZeroDivisionError):
            change_pct = None
    intent = {
        "op_type": op_type, "op_label": op["label"], "sid": payload["sid"],
        "target_id": str(payload["target_id"]), "target_name": payload.get("target_name") or str(payload["target_id"]),
        "change": change, "before": before, "change_pct": change_pct,
        "rationale": payload.get("rationale") or "(人工新建)",
    }
    if payload.get("opt"):
        intent["opt"] = payload["opt"]
    return await create_ticket(intent, source="manual")


async def create_tickets_batch(payloads: List[Dict[str, Any]],
                               source: str = "manual") -> Dict[str, Any]:
    """Create many tickets at once (e.g. selected optimizer candidates). Each
    ticket's review pipeline runs in the background; invalid payloads are
    collected instead of failing the whole batch."""
    created: List[str] = []
    errors: List[Dict[str, Any]] = []
    for i, p in enumerate((payloads or [])[:50]):
        try:
            t = await create_manual_ticket(dict(p))
            created.append(t["id"])
        except _gw.LingXingError as e:
            errors.append({"index": i, "error": str(e),
                           "target": p.get("target_name") or p.get("keyword_text") or p.get("target_id")})
    return {"created": len(created), "tickets": created, "errors": errors}


async def batch_tickets_action(action: str, ids: List[str], *,
                               dry_run: bool = False) -> Dict[str, Any]:
    """Confirm/reject a human-selected set of tickets. Confirms run strictly one
    by one through the normal gate (every gate re-checked per ticket); a real
    write failure stops the rest — the circuit breaker has already disabled the
    operate switch at that point."""
    if action not in ("confirm", "reject"):
        raise _gw.LingXingError(f"未知批量动作: {action}")
    results: List[Dict[str, Any]] = []
    for tid in (ids or [])[:50]:
        try:
            if action == "confirm":
                t = await confirm_ticket(tid, decided_by="human-batch", dry_run=dry_run)
            else:
                t = await reject_ticket(tid, decided_by="human-batch")
            results.append({"id": tid, "ok": True, "status": t["status"]})
        except _gw.LingXingError as e:
            results.append({"id": tid, "ok": False, "error": str(e)})
            if action == "confirm" and not dry_run:
                break
    return {"results": results,
            "done": sum(1 for r in results if r["ok"]), "total": len(ids or [])}


async def confirm_ticket(tid: str, decided_by: str = "human", dry_run: bool = False) -> Dict[str, Any]:
    """Human-confirm + execute. Re-checks every gate at execution time."""
    async with _op_lock:
        t = get_ticket(tid)
        if not t:
            raise _gw.LingXingError("未找到工单")
        if t["status"] != "awaiting_human":
            raise _gw.LingXingError(f"工单状态 {t['status']} 不可确认")
        if not _gw.is_operate_active():
            raise _gw.LingXingError("操作开关未开启（或已超时失效）")
        # re-verify guardrails at execution time (defence in depth)
        guard = check_guardrails(t["intent"])
        if not guard["ok"]:
            t["status"] = "guardrail_blocked"; t["guardrail"] = guard
            _save(t)
            raise _gw.LingXingError("执行前护栏复检未通过")

        t["decided_by"] = decided_by
        intent = t["intent"]
        route = OP_TYPES[intent["op_type"]]["route"]
        # capture rollback snapshot from live state (or recorded before)
        t["snapshot"] = await _current_value(intent)
        body = build_body(intent)

        if dry_run:
            t["status"] = "awaiting_human"  # unchanged; this is a preview
            t["result"] = {"dry_run": True, "route": route, "body": body}
            _save(t)
            return t

        t["status"] = "executing"
        _save(t)
        try:
            res = await _gw.call_openapi(route, body, method="POST",
                                         caller="operate", allow_write=True)
            t["result"] = res
            if OP_TYPES[intent["op_type"]]["category"] == "add":
                t["snapshot"] = {"target_ids": _extract_target_ids(res), "add": True}
            t["status"] = "executed"
            _save(t)
            await send_alert(f"已执行：店铺{intent['sid']} {_op_summary(intent)}")
        except _gw.LingXingError as e:
            t["status"] = "failed"; t["error"] = str(e)
            _save(t)
            # circuit breaker: API-level failure auto-disables the operate switch
            disable_operate()
            await send_alert(f"执行失败已熔断（操作开关已关闭）：{e}")
            raise
        return t


async def reject_ticket(tid: str, decided_by: str = "human") -> Dict[str, Any]:
    t = get_ticket(tid)
    if not t:
        raise _gw.LingXingError("未找到工单")
    t["status"] = "rejected"; t["decided_by"] = decided_by
    _save(t)
    return t


async def rollback_ticket(tid: str, decided_by: str = "human") -> Dict[str, Any]:
    """Revert an executed ticket to its captured pre-execution snapshot."""
    async with _op_lock:
        t = get_ticket(tid)
        if not t:
            raise _gw.LingXingError("未找到工单")
        if t["status"] != "executed":
            raise _gw.LingXingError(f"工单状态 {t['status']} 不可回滚")
        if not _gw.is_operate_active():
            raise _gw.LingXingError("操作开关未开启，无法回滚")
        snap = t.get("snapshot") or {}
        if not snap:
            raise _gw.LingXingError("无回滚快照")
        intent = t["intent"]
        op = OP_TYPES[intent["op_type"]]

        if op["category"] == "add":
            if not op.get("reversible"):
                raise _gw.LingXingError(f"{op['label']} 不支持一键回滚（请到领星暂停/归档该词）")
            tids = snap.get("target_ids") or []
            if not tids:
                raise _gw.LingXingError("无可归档的 targetId（执行响应未返回ID）")
            res = await _gw.call_openapi(op["archive_route"],
                                         {"sid": int(intent["sid"]), "targetIds": tids},
                                         method="POST", caller="operate-rollback", allow_write=True)
            t["status"] = "rolled_back"; t["result"] = {"rollback": res, "prev": t.get("result")}
            t["decided_by"] = decided_by
            _save(t)
            await send_alert(f"已回滚(归档)：店铺{intent['sid']}「{intent.get('keyword_text')}」")
            return t

        nf = op["num_field"]
        change = {}
        if snap.get(nf) is not None:
            change[nf] = snap[nf]
        if snap.get("state"):
            change["state"] = snap["state"]
        body = build_body({"op_type": intent["op_type"], "sid": intent["sid"],
                           "target_id": intent["target_id"], "change": change})
        res = await _gw.call_openapi(op["route"], body, method="POST",
                                     caller="operate-rollback", allow_write=True)
        t["status"] = "rolled_back"; t["result"] = {"rollback": res, "prev": t.get("result")}
        t["decided_by"] = decided_by
        _save(t)
        await send_alert(f"已回滚：店铺{intent['sid']} {intent.get('target_name') or intent.get('target_id')} → {change}")
        return t
