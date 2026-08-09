"""证据溯源：从一条结论回到它依据的原始数据行。

最要紧的一条：**"溯源没找到"和"这条结论没有证据"必须分得开**。混在一起会让
用户对整份报告失去判断力 —— 前者是我们的检索没命中，后者是结论本来就没依据。
"""
from __future__ import annotations

import csv

import pytest

from app.services import evidence_trace as et

HEADER = ["顾客搜索词", "关键词", "点击量", "总成本 (USD)", "购买量"]
ROWS = [
    ['"browning strike force trail camera"', "close-match", "312", "486.20", "0"],
    ["cheap trail camera", "broad", "58", "77.40", "0"],
    ["B087G8W2PY", "substitutes", "3", "3.32", "0"],
]


@pytest.fixture()
def src(tmp_path):
    p = tmp_path / "source_a.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(ROWS)
    return p


def test_finds_the_rows_behind_a_conclusion(src):
    out = et.trace([src], "cheap trail camera")
    assert out["ok"] is True
    assert out["total"] == 1
    assert out["rows"][0][3] == "77.40"      # 原文，没被换算过
    assert out["columns"] == HEADER


def test_match_is_case_and_quote_insensitive(src):
    """结论里的对象在报表里通常就是某列的原文，但大小写和引号经常对不上。"""
    out = et.trace([src], "BROWNING Strike Force")
    assert out["ok"] is True and out["total"] == 1


def test_asin_style_targets_work_too(src):
    assert et.trace([src], "b087g8w2py")["ok"] is True


def test_miss_is_reported_as_a_miss_not_as_no_evidence(src):
    """**这是这个模块最要紧的一条。** 回空列表会让界面显示成"没有证据"，
    而真相是"我们没检索到" —— 两件完全不同的事。"""
    out = et.trace([src], "从未出现过的词")
    assert out["ok"] is False
    assert "没有找到" in out["reason"]
    assert out["rows"] == []


def test_empty_target_is_refused(src):
    out = et.trace([src], "   ")
    assert out["ok"] is False and "没有给出" in out["reason"]


def test_rows_are_capped_but_the_true_count_is_reported(tmp_path):
    """几十行足够核对了，再多是在让人滚动。但**总数要如实说**，
    否则用户以为只命中了这么多。"""
    p = tmp_path / "source_big.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for i in range(120):
            w.writerow([f"trail camera {i}", "broad", "1", "1.00", "0"])
    out = et.trace([p], "trail camera")
    assert out["total"] == 120
    assert len(out["rows"]) == et.MAX_ROWS
    assert out["truncated"] is True


def test_an_unreadable_file_does_not_kill_the_whole_trace(tmp_path, src):
    """一份源文件坏了，其余的照常搜 —— 不能因为一个文件让溯源整个失效。"""
    bad = tmp_path / "source_bad.xlsx"
    bad.write_bytes("这不是一个 xlsx".encode("utf-8"))
    out = et.trace([bad, src], "cheap trail camera")
    assert out["ok"] is True and out["total"] == 1


def test_missing_files_are_skipped(tmp_path, src):
    out = et.trace([tmp_path / "nope.csv", src], "cheap trail camera")
    assert out["ok"] is True


def test_xlsx_sources_are_supported(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    p = tmp_path / "source_b.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADER)
    ws.append(["solar trail cam", "exact", 25, 40.0, 2])
    wb.save(p)
    out = et.trace([p], "solar trail cam")
    assert out["ok"] is True
    assert out["rows"][0][2] == 25
