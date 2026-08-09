"""结论契约：证据 → 诊断 → 带优先级的动作。

守的核心是一句话：**能核对的才叫证据。** 现有产出里 evidence 是自由文本，
"花费很高转化很差"也能塞进去 —— 读的人无法核对，也没法点开看原始数据行。

另一条同等重要：**升级契约不能让老产出变哑**。这个仓库改契约的纪律是
"加字段可以、让老消费方失效不行"。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.findings import Action, Evidence, Finding, FindingList, normalize, schema


# ── 证据 ────────────────────────────────────────────────────────────────

def test_evidence_requires_a_metric_name():
    """没有指标名的不是证据，是感想。"""
    with pytest.raises(ValidationError):
        Evidence(metric="  ", value=1)


def test_evidence_carries_what_makes_it_checkable():
    e = Evidence(metric="spend_30d", value=486.2, unit="USD", target="trail camera 4k",
                 source="ads_api", as_of="2026-07-01~2026-07-30")
    assert e.metric == "spend_30d" and e.source == "ads_api" and e.as_of


# ── 结论 ────────────────────────────────────────────────────────────────

def test_unknown_severity_degrades_instead_of_raising():
    """模型偶尔会返回 'urgent' 这类没定义的档位。为此整条结论丢掉是因小失大，
    降级成 medium 并保留内容更合理。"""
    assert Finding(title="x", severity="urgent").severity == "medium"


def test_findings_without_evidence_are_surfaced_not_rejected():
    """无证据不是校验错误（有时确实只是一条提示），但必须能被点出来 ——
    评测的'证据完整性'维度和 UI 上的'此结论无证据'标记都靠它。"""
    fl = FindingList(findings=[
        Finding(title="有据", evidence=[Evidence(metric="clicks", value=312)]),
        Finding(id="bare", title="没据"),
    ])
    assert fl.unsupported() == ["bare"]


def test_action_guardrail_is_part_of_the_contract():
    """不写清'在什么条件下才该做'的建议，执行者无从判断该不该照做，
    也就没法把责任交给自动化。"""
    a = Action(type="negate_keyword", target="trail camera 4k",
               guardrail="点击≥15 且 0 单", confidence=0.86)
    assert a.guardrail and a.reversible is True


def test_confidence_is_bounded():
    with pytest.raises(ValidationError):
        Action(type="x", confidence=1.5)


# ── 向后兼容（这一节是重点）─────────────────────────────────────────────

LEGACY = {
    "insight_type": "black_hole_campaign",
    "summary": "SP-Auto 长尾在烧钱",
    "detail": "45% 花费落在无转化长尾",
    "evidence": "spend=486.20 USD clicks=312 orders=0",
    "action": "对高点击零单词加否定短语",
    "priority": "P0",
    "from_campaign": "SP-Auto-主推",
}


def test_legacy_insight_is_upgraded_not_dropped():
    fl = normalize([LEGACY], source="ads_api", as_of="2026-07")
    assert len(fl.findings) == 1
    f = fl.findings[0]
    assert f.title == "SP-Auto 长尾在烧钱"
    assert f.severity == "critical", "P0 应映射为 critical"
    assert f.actions and f.actions[0].detail.startswith("对高点击")


def test_legacy_text_evidence_is_parsed_into_metrics():
    f = normalize([LEGACY], source="ads_api", as_of="2026-07").findings[0]
    metrics = {e.metric: str(e.value) for e in f.evidence}
    assert metrics.get("spend") == "486.20"
    assert metrics.get("clicks") == "312"
    assert all(e.source == "ads_api" for e in f.evidence), "来源要补上，否则仍然无法核对"


def test_vague_legacy_evidence_is_kept_whole_not_invented():
    """'花费很高转化很差'抠不出指标 —— 原样留着并标明未结构化。
    硬解析成 spend=? 会造出**假证据**，那比没有证据更糟。"""
    f = normalize([{**LEGACY, "evidence": "花费很高转化很差"}]).findings[0]
    assert len(f.evidence) == 1
    assert f.evidence[0].metric == "legacy_note"
    assert "未能结构化" in f.evidence[0].note


def test_new_shape_passes_through():
    payload = {"findings": [{
        "id": "ad-waste-001", "severity": "high", "title": "高花费零转化",
        "evidence": [{"metric": "spend_30d", "value": 486.2, "unit": "USD"}],
        "actions": [{"type": "negate_keyword", "guardrail": "点击≥15 且 0 单"}],
        "priority_score": 92,
    }], "data_notes": "样本 30 天"}
    fl = normalize(payload)
    assert fl.findings[0].priority_score == 92
    assert fl.data_notes == "样本 30 天"


@pytest.mark.parametrize("junk", [None, "", 123, [], {}])
def test_garbage_input_yields_an_empty_list_not_a_crash(junk):
    """上游模型偶尔返回垃圾。契约层要吸收掉，不能把整个报告流程带崩。"""
    assert isinstance(normalize(junk), FindingList)


def test_schema_is_usable_for_prompts_and_assertions():
    """一份 schema 两处用（提示词里贴、测试里断言），避免'提示词写的'和
    '代码校验的'各说各话。"""
    s = schema()
    assert "findings" in s["properties"]
    assert "$defs" in s and "Evidence" in s["$defs"]


def test_evidence_list_actually_enforces_the_shape():
    """类型化之前 evidence: list 什么都收 —— 契约宣称"证据必须可核对"却不强制，
    等于没有契约。这条钉住它。"""
    with pytest.raises(ValidationError):
        Finding(title="x", evidence=["花费很高"])          # 字符串不是证据
    with pytest.raises(ValidationError):
        Finding(title="x", evidence=[{"value": 1}])        # 缺 metric


def test_one_malformed_item_does_not_sink_the_whole_report():
    fl = normalize({"findings": [
        {"title": "好的那条", "evidence": [{"metric": "clicks", "value": 10}]},
        {"severity": "high"},                              # 缺 title
    ]})
    assert [f.title for f in fl.findings] == ["好的那条"]
