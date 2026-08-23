"""驾驶舱直调「快车道」的边界。

快车道让**小幅止血**动作免掉三重 LLM 复核。它省掉的只有复核这一道；确定性护栏
和人工确认一道没少。这份测试守的就是"到底省了哪一道、什么情况下不许省"：

  ① 只有调小 / 暂停能走 —— 提预算、加 bid、enable 一律全复核；
  ② 幅度超过上限就不许走；
  ③ **自主执行档下一律不许走** —— 那时候没有人在闸口，复核是最后一道。

第 ③ 条是这里最重要的一条：快车道 + 自主执行如果能叠加，就等于"无人监督地
自动改线上广告"，正好是整个受控写通道当初要消灭的东西。
"""
from __future__ import annotations

import pytest

from app.services import lingxing_operate as lxo


BASE = {
    "op_type": "campaign_budget",
    "sid": 1863,
    "target_id": "111",
    "target_name": "主力手动",
}


@pytest.fixture
def settings(monkeypatch):
    cfg = {
        "lingxing_fast_lane_enabled": True,
        "lingxing_fast_lane_max_pct": 15,
        "lingxing_operate_require_human": True,
    }
    monkeypatch.setattr(lxo._hs, "load", lambda: dict(cfg))
    return cfg


def decide(**intent):
    return lxo.fast_lane_decision({**BASE, **intent})


def test_small_budget_decrease_is_eligible(settings):
    d = decide(change={"daily_budget": 17.0}, before={"daily_budget": 20.0})  # -15%
    assert d["eligible"] is True
    assert "免 AI 复核" in d["reason"]


def test_budget_increase_always_needs_full_review(settings):
    """花钱的方向上，慢十几秒不算代价。"""
    d = decide(change={"daily_budget": 21.0}, before={"daily_budget": 20.0})  # +5%，幅度很小
    assert d["eligible"] is False
    assert "不是调小" in d["reason"]


def test_pause_is_stanching_and_eligible(settings):
    d = decide(change={"state": "paused"}, before={"state": "enabled"})
    assert d["eligible"] is True


def test_enable_is_not_eligible(settings):
    d = decide(change={"state": "enabled"}, before={"state": "paused"})
    assert d["eligible"] is False
    assert "放量" in d["reason"]


def test_large_decrease_needs_full_review(settings):
    d = decide(change={"daily_budget": 10.0}, before={"daily_budget": 20.0})  # -50%
    assert d["eligible"] is False
    assert "上限" in d["reason"]


def test_boundary_is_inclusive(settings):
    """正好等于上限算通过 —— 边界含糊会让人反复试。"""
    d = decide(change={"daily_budget": 17.0}, before={"daily_budget": 20.0})  # 恰好 15%
    assert d["eligible"] is True


def test_disabled_switch_blocks_everything(settings):
    settings["lingxing_fast_lane_enabled"] = False
    d = decide(change={"daily_budget": 19.0}, before={"daily_budget": 20.0})
    assert d["eligible"] is False
    assert "未开启" in d["reason"]


def test_autonomous_tier_never_uses_the_fast_lane(settings):
    """自主执行档：没有人在闸口，复核不能省。这条是安全模型的核心。"""
    settings["lingxing_operate_require_human"] = False
    d = decide(change={"daily_budget": 19.0}, before={"daily_budget": 20.0})
    assert d["eligible"] is False
    assert "无人确认" in d["reason"]


def test_keyword_add_ops_are_not_eligible(settings):
    d = lxo.fast_lane_decision({"op_type": "negative_keyword", "sid": 1863,
                                "campaign_id": "1", "keyword_text": "cheap",
                                "match_type": "negativeExact"})
    assert d["eligible"] is False


def test_missing_current_value_blocks_the_fast_lane(settings):
    """拿不到当前值就无法确认这是"调小" —— 不确定时走全复核。"""
    d = decide(change={"daily_budget": 17.0}, before={})
    assert d["eligible"] is False
    assert "无法确认" in d["reason"]


def test_bid_decrease_also_eligible(settings):
    d = lxo.fast_lane_decision({
        "op_type": "keyword_bid", "sid": 1863, "target_id": "k1",
        "change": {"bid": 0.9}, "before": {"bid": 1.0},   # -10%
    })
    assert d["eligible"] is True


def test_checks_are_reported_for_the_ui(settings):
    d = decide(change={"daily_budget": 5.0}, before={"daily_budget": 20.0})
    names = {c["name"] for c in d["checks"]}
    assert {"switch", "human_gate", "op_supported", "stanch_direction", "magnitude"} <= names
