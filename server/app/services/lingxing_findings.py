"""把领星规则引擎的候选操作译成统一的结论契约（core/findings）。

为什么值得单独做这一层
----------------------
统一契约那套东西（结论卡片、带证据页的交付物、对外 MCP）此前只有广告审计那条
LLM 链路在用，而**日常真正在跑的是领星这条**。更要紧的是：规则引擎的证据是
接口取回来的真实指标，不是模型转述的 —— 同一份"证据页"，一边写着「花费很高」，
一边写着「spend=820.50 USD / clicks=312 / orders=0，取自 lingxing，窗口
2026-07-01~07-30」，可被追问的程度完全不同。

翻译时的两个判断
----------------
* **严重度按浪费的钱排，不按杠杆类型排。** 一条花了 800 美元零出单的否词建议，
  和一条花了 8 美元的，不该是同一个优先级 —— 后者哪怕规则完全成立，也不值得
  占用人的注意力。
* **可回滚要如实标。** 调价、调预算、否词都能改回来；"收割"（把搜索词提成
  独立精准投放）会新建投放单元，撤回不是改回一个数那么简单。这一列是用户
  决定"照不照做"时最先看的，标错比不标更糟。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ivyea.services.lingxing_findings")

# 杠杆 → (动作类型, 是否可回滚)。回滚性以"能不能改回原来的一个值"为准。
_LEVER: Dict[str, tuple] = {
    "否词": ("negate_keyword", True),
    "降bid": ("adjust_bid", True),
    "加bid": ("adjust_bid", True),
    "加预算": ("adjust_budget", True),
    # 新建投放单元，撤回不是改回一个数。见模块开头第二条。
    "收割": ("harvest_search_term", False),
}

# 指标 → (中文名, 单位)。只译**证据里真正要用到的**几个，
# 不做一张大而全的映射表 —— 那种表迟早和数据源对不上。
_METRIC: Dict[str, tuple] = {
    "spend": ("花费", "USD"),
    "sales": ("销售额", "USD"),
    "clicks": ("点击", "次"),
    "orders": ("订单", "单"),
    "impressions": ("展示", "次"),
    "acos": ("ACOS", ""),
    "cpc": ("CPC", "USD"),
    "rpc": ("每次点击收入", "USD"),
    "cvr": ("转化率", ""),
}

# 证据只取这几项，且**按信息量排序**：花费和订单是判断成立与否的核心，
# 展示量放最后。一条塞了 9 个指标的证据，读的人一个都不会看。
_EVIDENCE_ORDER = ("spend", "clicks", "orders", "sales", "acos", "cpc")


def _severity(spend: float, lever: str) -> tuple:
    """返回 (severity, priority_score)。按**浪费/影响的金额**排，不按杠杆排。"""
    if lever in ("否词", "降bid"):
        # 花出去没回报的钱，越多越急。
        if spend >= 500:
            return "critical", min(100, 70 + int(spend / 100))
        if spend >= 150:
            return "high", 55 + int(spend / 20)
        if spend >= 30:
            return "medium", 30 + int(spend / 10)
        return "low", 10 + int(spend)
    # 加价/加预算/收割是"再多赚一点"，本质上不紧急。
    if spend >= 300:
        return "medium", 40
    return "low", 20


def _evidence(metrics: Dict[str, Any], target: str, as_of: str) -> List[dict]:
    out: List[dict] = []
    for key in _EVIDENCE_ORDER:
        if key not in metrics:
            continue
        value = metrics[key]
        if value is None:
            continue
        name, unit = _METRIC.get(key, (key, ""))
        if key in ("acos", "cvr") and isinstance(value, (int, float)):
            value = round(value * 100, 1)
            unit = "%"
        elif isinstance(value, float):
            value = round(value, 2)
        out.append({"metric": name, "value": value, "unit": unit,
                    "target": target, "source": "lingxing", "as_of": as_of})
    return out


def _detail(cand: Dict[str, Any]) -> str:
    lever = cand.get("lever") or ""
    cur = (cand.get("current") or {})
    prop = (cand.get("proposed") or {})
    name = cand.get("target_name") or cand.get("target_id") or ""
    if lever == "否词":
        return f"把「{name}」加为否定关键词"
    if lever == "收割":
        return f"把「{name}」提为独立精准投放"
    for field, label in (("bid", "竞价"), ("budget", "预算")):
        if field in prop:
            pct = cand.get("change_pct")
            arrow = f"{cur.get(field)} → {prop[field]}"
            return (f"「{name}」{label} {arrow}"
                    + (f"（{pct:+.1f}%）" if isinstance(pct, (int, float)) else ""))
    return f"{lever}：{name}"


def to_findings(run: Dict[str, Any]) -> Dict[str, Any]:
    """把一次规则引擎运行的结果译成 FindingList 的 dict 形态。

    取不到就给空列表 —— 这条路径绝不能因为格式问题让整个报告接口失败
    （与 ad_audit._as_findings 同一条原则）。
    """
    from app.core.findings import Action, Evidence, Finding, FindingList

    cands = (run or {}).get("candidates") or []
    window = f"近 {run.get('window_days')} 天" if run.get("window_days") else ""
    findings: List[Finding] = []

    for cand in cands:
        try:
            lever = str(cand.get("lever") or "")
            op_type, reversible = _LEVER.get(lever, ("review", True))
            metrics = cand.get("metrics") or {}
            spend = float(metrics.get("spend") or 0)
            severity, score = _severity(spend, lever)
            name = str(cand.get("target_name") or cand.get("target_id") or "")

            findings.append(Finding(
                id=f"{lever}:{cand.get('target_id')}",
                title=f"[{lever}] {name}",
                severity=severity,
                priority_score=min(100, max(0, score)),
                reasoning=str(cand.get("rationale") or ""),
                evidence=[Evidence(**e) for e in
                          _evidence(metrics, name, window)],
                actions=[Action(
                    type=op_type,
                    target=str(cand.get("target_id") or ""),
                    detail=_detail(cand),
                    # 规则本身就是护栏：它写明了"在什么条件下才该做"。
                    # significance（几点击/几单）是这条规则站得住的样本量依据。
                    guardrail="；".join(x for x in [str(cand.get("rule") or ""),
                                                   str(cand.get("significance") or "")] if x),
                    reversible=reversible,
                    # 规则引擎是确定性的：命中即成立。给固定高置信度，
                    # 而不是编一个看起来很精确的小数。
                    confidence=0.9,
                )],
            ))
        except Exception as exc:  # noqa: BLE001 — 单条译不动不该毁掉整份
            logger.warning("候选译成结论失败，已跳过：%s", exc)

    fl = FindingList(
        findings=findings,
        data_notes=_notes(run),
    )
    payload = fl.model_dump()
    payload["unsupported"] = fl.unsupported()
    return payload


def _notes(run: Dict[str, Any]) -> str:
    """数据边界要跟着结论走 —— 目标 ACOS 是怎么来的、窗口多长、是否按活动分别取毛利。"""
    bits: List[str] = []
    if run.get("window_days"):
        bits.append(f"统计窗口 {run['window_days']} 天")
    margin = run.get("margin")
    if isinstance(margin, (int, float)):
        bits.append(f"毛利率 {margin * 100:.0f}%")
    for key, label in (("breakeven_acos", "保本 ACOS"), ("target_acos", "目标 ACOS")):
        v = run.get(key)
        if isinstance(v, (int, float)):
            bits.append(f"{label} {v * 100:.0f}%")
    if run.get("per_campaign"):
        bits.append("按广告活动分别取毛利")
    if run.get("note"):
        bits.append(str(run["note"]))
    return "；".join(bits)


def findings_for_run(run_id: str) -> Optional[Dict[str, Any]]:
    from app.services import lingxing_optimizer as lxopt
    run = lxopt.get_opt_run(run_id)
    if not run:
        return None
    # 落库时结果可能存在 result 字段下，也可能就是整条记录。两种都认。
    payload = run.get("result") if isinstance(run.get("result"), dict) else run
    return to_findings(payload)
