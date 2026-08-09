"""交付物导出。

这份文件的存在理由是**可被追问**：每条结论后面跟着指标、数值、时间窗和来源。
所以测试盯的不是"能不能生成文件"，而是"证据有没有真的落到纸上"。
"""
from __future__ import annotations

import pytest

from app.core.findings import Action, Evidence, Finding, FindingList
from app.services import deliverable

openpyxl = pytest.importorskip("openpyxl")


def _sample() -> dict:
    fl = FindingList(
        findings=[
            Finding(
                title="B0ABC 的 SP 广告 30 天花了 820 美元零出单",
                severity="critical",
                priority_score=95,
                reasoning="点击量足够大，转化为零，不是样本不足的问题。",
                evidence=[
                    Evidence(metric="spend_30d", value=820.5, unit="USD",
                             target="B0ABC", source="ads_api", as_of="2026-07-01~07-30"),
                    Evidence(metric="orders_30d", value=0, target="B0ABC",
                             source="ads_api", as_of="2026-07-01~07-30"),
                ],
                actions=[Action(type="pause_campaign", target="SP-B0ABC",
                                detail="暂停该广告活动", guardrail="点击≥15 且 0 单",
                                reversible=True, confidence=0.9)],
            ),
            Finding(
                title="建议整体下调竞价",
                severity="low",
                actions=[Action(type="adjust_bid", detail="全局降 10%",
                                reversible=False, confidence=0.3)],
            ),
        ],
        data_notes="仅覆盖 SP，未含 SB/SD。",
    )
    payload = fl.model_dump()
    payload["unsupported"] = fl.unsupported()
    return payload


def test_evidence_lands_on_its_own_sheet(tmp_path):
    """证据独立成页，是这份东西和"AI 生成的一段话"最大的区别。"""
    out = deliverable.build_xlsx(tmp_path / "d.xlsx", _sample(), {"job_id": "J1"})
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["结论", "证据", "说明"]

    ev = wb["证据"]
    flat = [[c.value for c in row] for row in ev.iter_rows(min_row=2)]
    metrics = {r[2] for r in flat}
    assert "spend_30d" in metrics and "orders_30d" in metrics
    spend = next(r for r in flat if r[2] == "spend_30d")
    assert spend[3] == 820.5              # 数值本身，不是"很高"
    assert spend[6] == "ads_api"          # 来源
    assert "2026-07-01~07-30" in str(spend[7])   # 时间窗


def test_a_finding_without_evidence_is_marked_not_silently_blank(tmp_path):
    """空证据必须显式写出来。留白会让人以为只是"碰巧没填"，
    而这恰恰是最该被质疑的一条。"""
    out = deliverable.build_xlsx(tmp_path / "d.xlsx", _sample(), {})
    ev = openpyxl.load_workbook(out)["证据"]
    marks = [c.value for row in ev.iter_rows(min_row=2) for c in row]
    assert "（无结构化证据）" in marks


def test_most_severe_finding_comes_first(tmp_path):
    out = deliverable.build_xlsx(tmp_path / "d.xlsx", _sample(), {})
    ws = openpyxl.load_workbook(out)["结论"]
    assert ws.cell(row=2, column=2).value == "紧急"
    assert "820" in str(ws.cell(row=2, column=4).value)


def test_irreversible_action_is_flagged(tmp_path):
    """用户决定"照做还是不照做"时，最先要知道的是做错了能不能撤回来。"""
    out = deliverable.build_xlsx(tmp_path / "d.xlsx", _sample(), {})
    ws = openpyxl.load_workbook(out)["结论"]
    col = {ws.cell(row=1, column=c).value: c for c in range(1, 9)}
    flags = [ws.cell(row=r, column=col["可回滚"]).value for r in (2, 3)]
    assert set(flags) == {"是", "否"}


def test_empty_findings_says_so_instead_of_producing_a_blank_sheet(tmp_path):
    """没结论时给一张空表，用户会以为是导出坏了。"""
    out = deliverable.build_xlsx(tmp_path / "d.xlsx",
                                 {"findings": [], "data_notes": ""}, {})
    ws = openpyxl.load_workbook(out)["结论"]
    assert "没有得出可支撑的结论" in str(ws.cell(row=2, column=4).value)


def test_info_sheet_records_where_this_came_from(tmp_path):
    out = deliverable.build_xlsx(tmp_path / "d.xlsx", _sample(),
                                 {"job_id": "J7", "kind": "广告审计"})
    info = openpyxl.load_workbook(out)["说明"]
    blob = " ".join(str(c.value) for row in info.iter_rows() for c in row)
    assert "J7" in blob and "广告审计" in blob
    assert "仅覆盖 SP" in blob                      # 数据边界要跟着交付物走
    assert "建议整体下调竞价" in blob                # 无证据的结论要点名


def test_markdown_carries_the_same_evidence():
    md = deliverable.build_markdown(_sample(), {"job_id": "J1", "kind": "广告审计"})
    assert "spend_30d = 820.5USD" in md
    assert "来源：ads_api" in md
    assert "护栏：点击≥15 且 0 单" in md
    assert "可回滚：否" in md                        # 第二条动作
    assert "这条没有结构化证据支撑" in md


def test_markdown_labels_target_as_object_not_goal():
    """target 是「这条证据说的是哪个对象」，不是对比目标值 ——
    标成"目标 B0ABC"会让读者以为那是个要达成的数。"""
    md = deliverable.build_markdown(_sample(), {})
    assert "对象 B0ABC" in md
    assert "目标 B0ABC" not in md
