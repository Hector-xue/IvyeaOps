"""领星规则引擎的候选 → 统一结论契约。

这条翻译值得测，是因为它决定了**证据页上写的是什么**。规则引擎手里握着接口取回
的真实指标；译丢了，交付物就退化成又一段"AI 说花费很高"。
"""
from __future__ import annotations

from app.services import lingxing_findings as lxf

RUN = {
    "sid": 7, "window_days": 30, "margin": 0.32,
    "breakeven_acos": 0.32, "target_acos": 0.224, "per_campaign": False,
    "note": "已剔除最近 3 天归因延迟",
    "candidates": [
        {
            "lever": "否词", "op_type": "negate_keyword", "target_id": "st-1",
            "target_name": "cheap trail camera",
            "metrics": {"spend": 820.50, "sales": 0, "orders": 0, "clicks": 312,
                        "impressions": 18400, "acos": None, "cpc": 2.63},
            "rule": "点击≥15 且 0 单", "significance": "312点击/0单",
            "rationale": "长期投放零转化，属确定性浪费",
        },
        {
            "lever": "降bid", "op_type": "keyword_bid", "target_id": "kw-2",
            "target_name": "trail camera solar",
            "metrics": {"spend": 40.0, "sales": 80.0, "orders": 2, "clicks": 25,
                        "impressions": 900, "acos": 0.5, "cpc": 1.6},
            "current": {"bid": 1.20}, "proposed": {"bid": 1.02}, "change_pct": -15.0,
            "rule": "ACOS 高于目标，按 RPC×目标 重算", "significance": "25点击/2单",
            "rationale": "ACOS 50% 高于目标 22.4%",
        },
        {
            "lever": "收割", "op_type": "add_keyword", "advisory": True,
            "target_id": "st-3", "target_name": "solar trail cam 4k",
            "metrics": {"spend": 60.0, "sales": 400.0, "orders": 8, "clicks": 40,
                        "impressions": 1200, "acos": 0.15, "cpc": 1.5},
            "rule": "订单≥5 且 ACOS 低于目标", "significance": "40点击/8单",
            "rationale": "已被验证的高效搜索词",
        },
    ],
}


def _fl():
    return lxf.to_findings(RUN)


# ── 证据必须是真实指标 ───────────────────────────────────────────────────
def test_evidence_carries_real_numbers_not_adjectives():
    """这是它比 LLM 那条链路"硬"的全部理由。"""
    f = _fl()["findings"][0]
    ev = {e["metric"]: e for e in f["evidence"]}
    assert ev["花费"]["value"] == 820.5 and ev["花费"]["unit"] == "USD"
    assert ev["点击"]["value"] == 312
    assert ev["订单"]["value"] == 0
    assert all(e["source"] == "lingxing" for e in f["evidence"])
    assert all(e["as_of"] == "近 30 天" for e in f["evidence"])


def test_ratio_metrics_are_shown_as_percent():
    """ACOS 存的是 0.5，写到证据页上要是 50%，不然读的人得自己换算。"""
    f = _fl()["findings"][1]
    acos = next(e for e in f["evidence"] if e["metric"] == "ACOS")
    assert acos["value"] == 50.0 and acos["unit"] == "%"


def test_null_metrics_are_dropped_not_rendered_as_none():
    """零转化的词 ACOS 是 None。写成 "ACOS: None" 会让人以为数据缺了。"""
    f = _fl()["findings"][0]
    assert all(e["value"] is not None for e in f["evidence"])
    assert "ACOS" not in {e["metric"] for e in f["evidence"]}


# ── 优先级按钱排，不按杠杆排 ─────────────────────────────────────────────
def test_severity_follows_money_not_lever_type():
    """一条花了 820 美元零出单的否词，和一条花了 8 美元的，不该同一个优先级 ——
    后者哪怕规则完全成立，也不值得占用人的注意力。"""
    big = lxf.to_findings({"candidates": [dict(RUN["candidates"][0])]})["findings"][0]
    small = dict(RUN["candidates"][0])
    small["metrics"] = dict(small["metrics"], spend=8.0)
    tiny = lxf.to_findings({"candidates": [small]})["findings"][0]
    assert big["severity"] == "critical"
    assert tiny["severity"] == "low"
    assert big["priority_score"] > tiny["priority_score"]


def test_upside_levers_are_not_marked_urgent():
    """加价/收割是"再多赚一点"，不是"正在流血"。"""
    harvest = _fl()["findings"][2]
    assert harvest["severity"] in ("low", "medium")


# ── 可回滚要如实标 ───────────────────────────────────────────────────────
def test_reversibility_is_honest_about_harvest():
    """调价、否词能改回来；收割会新建投放单元，撤回不是改回一个数。
    这一列是用户决定"照不照做"时最先看的，标错比不标更糟。"""
    out = _fl()["findings"]
    by_lever = {f["title"].split("]")[0].lstrip("["): f for f in out}
    assert by_lever["否词"]["actions"][0]["reversible"] is True
    assert by_lever["降bid"]["actions"][0]["reversible"] is True
    assert by_lever["收割"]["actions"][0]["reversible"] is False


# ── 护栏与数据边界 ───────────────────────────────────────────────────────
def test_the_rule_itself_becomes_the_guardrail():
    """规则写明了"在什么条件下才该做"，那正是护栏；样本量依据也一并带上。"""
    g = _fl()["findings"][0]["actions"][0]["guardrail"]
    assert "点击≥15 且 0 单" in g and "312点击/0单" in g


def test_bid_change_reads_as_a_before_after():
    d = _fl()["findings"][1]["actions"][0]["detail"]
    assert "1.2" in d and "1.02" in d and "-15.0%" in d


def test_data_boundary_travels_with_the_conclusions():
    """目标 ACOS 怎么来的、窗口多长 —— 这些不跟着结论走，结论就没法被复核。"""
    notes = _fl()["data_notes"]
    for expect in ("30 天", "毛利率 32%", "保本 ACOS 32%", "目标 ACOS 22%",
                   "已剔除最近 3 天归因延迟"):
        assert expect in notes, notes


# ── 坏输入不能毁掉整份 ───────────────────────────────────────────────────
def test_one_broken_candidate_does_not_kill_the_rest():
    """接口偶尔会回来一个数值字段是文本的记录。译不动的那条跳过，
    其余照常出 —— 一条脏数据把整份报告打空，比少一条糟得多。"""
    broken = {"lever": "否词", "target_id": "x", "metrics": {"spend": "很多"}}
    out = lxf.to_findings({"candidates": [broken, RUN["candidates"][0]]})
    assert len(out["findings"]) == 1
    assert "cheap trail camera" in out["findings"][0]["title"]


def test_empty_run_gives_an_empty_list_not_an_error():
    assert lxf.to_findings({})["findings"] == []
    assert lxf.to_findings({"candidates": []})["findings"] == []
