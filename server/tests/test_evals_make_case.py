"""评测案例的脱敏工具。

盯两件事：**发出去的东西不能带标识**，以及**脱敏不能把数据改成自相矛盾的**。
后者比前者隐蔽 —— 一份内部对不上的报表会让评测测出假结论，而没人会怀疑是脱敏干的。
"""
from __future__ import annotations

from app.evals.make_case import deidentify

HEADER = ["顾客搜索词", "关键词", "展示量", "点击量", "点击率",
          "总成本 (USD)", "CPC (USD)", "购买量", "销售额 (USD)", "ACOS"]
ROWS = [
    ["mybrand trail camera 4k", "broad", "18400", "300", "0.0163",
     "486.00", "1.62", "0", "0.00", "0"],
    ["B087G8W2PY", "substitutes", "5200", "100", "0.0192",
     "150.00", "1.50", "10", "500.00", "0.30"],
]


def _run(**kw):
    return deidentify(HEADER, [r[:] for r in ROWS],
                      factor=kw.pop("factor", 0.37), salt="s1", mask=kw.pop("mask", {}))


# ── 数据不能被改成自相矛盾的 ─────────────────────────────────────────────
def test_cpc_stays_consistent_with_spend_over_clicks():
    """CPC 是派生比值（花费÷点击）。花费和点击同乘一个系数之后 CPC 本来就不变，
    如果把 CPC 也当金额缩放，就会得到 CPC ≠ 花费÷点击 —— 模型一做交叉校验就会
    读出"这份报表是假的"，评测从此测不出真东西。"""
    rows, _ = _run(factor=0.37)
    for row in rows:
        clicks = row["点击量"]
        if not clicks:
            continue
        assert abs(row["总成本 (USD)"] / clicks - float(row["CPC (USD)"])) < 0.02, row


def test_ratios_are_never_scaled():
    """ACOS / 点击率 缩放了就毁了它的意义 —— 那是结论赖以成立的东西。"""
    rows, _ = _run(factor=0.37)
    assert rows[1]["ACOS"] == 0.30
    assert rows[0]["点击率"] == 0.0163


def test_totals_are_scaled_by_the_same_factor():
    """同一个系数，比值才守恒。"""
    rows, _ = _run(factor=0.5)
    assert rows[0]["总成本 (USD)"] == 243.0        # 486 × 0.5
    assert rows[0]["点击量"] == 150                 # 300 × 0.5
    assert rows[1]["销售额 (USD)"] == 250.0


def test_acos_survives_the_scaling_end_to_end():
    """ACOS = 花费 / 销售额。缩放后必须还对得上，否则数据自己打自己。"""
    rows, _ = _run(factor=0.37)
    r = rows[1]
    assert abs(r["总成本 (USD)"] / r["销售额 (USD)"] - r["ACOS"]) < 0.01


# ── 标识必须真的去掉 ─────────────────────────────────────────────────────
def test_asin_is_pseudonymized_stably():
    rows, _ = _run()
    got = rows[1]["顾客搜索词"]
    assert "B087G8W2PY" not in got.upper()
    assert got.startswith("B0") and len(got) == 10
    again, _ = _run()
    assert again[1]["顾客搜索词"] == got      # 同一案例内稳定，关联关系不被打乱


def test_declared_brand_is_masked_and_counted():
    rows, warns = _run(mask={"mybrand": "某品牌1"})
    assert "mybrand" not in rows[0]["顾客搜索词"].lower()
    assert "某品牌1" in rows[0]["顾客搜索词"]
    assert any("已替换 1 处" in w for w in warns)


def test_a_brand_that_never_matches_is_reported_loudly():
    """用户以为遮住了、实际没遮，是这个工具能造成的最坏结果 —— 必须说出来。"""
    _, warns = _run(mask={"拼错了的牌子": "某品牌1"})
    assert any("一处都没匹配到" in w for w in warns)


def test_competitor_terms_are_left_alone():
    """竞品品牌任何人的搜索词报表里都有，不是隐私；去掉反而让案例失真。"""
    rows, _ = _run(mask={"mybrand": "某品牌1"})
    assert "trail camera 4k" in rows[0]["顾客搜索词"]


# ── 生成的骨架不能被误当成可用案例 ───────────────────────────────────────
def test_generated_skeleton_is_refused_until_a_human_labels_it(tmp_path):
    """要点为空的案例会让判官在没有评判标准的情况下打分，结果必然虚高。
    宁可加载时报错，也不能让半成品悄悄计入通过率。"""
    import json

    import pytest

    from app.evals.runner import load_cases

    folder = tmp_path / "ads"
    folder.mkdir(parents=True)
    (folder / "skeleton.json").write_text(json.dumps({
        "prompt": "分析", "input": {}, "expected_points": [],
    }, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="expected_points"):
        load_cases("ads", root=tmp_path)


# ── 案例集本身的完整性 ───────────────────────────────────────────────────
def test_duplicate_case_ids_are_refused(tmp_path):
    """报告是按 id 列行的。两个案例同名，看报告的人就分不清哪一行对应哪个 ——
    而评测报告的全部价值就是"哪里退步了"。"""
    import json

    import pytest

    from app.evals.runner import load_cases

    folder = tmp_path / "ads"
    folder.mkdir(parents=True)
    for name in ("a.json", "b.json"):
        (folder / name).write_text(json.dumps({
            "id": "same-id", "prompt": "x", "input": {}, "expected_points": ["y"],
        }, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="重复"):
        load_cases("ads", root=tmp_path)


def test_shipped_ads_cases_all_load():
    """仓库里自带的案例集必须始终可加载 —— 它是被测系统的基准线。"""
    from app.evals.runner import load_cases
    cases = load_cases("ads")
    assert len(cases) >= 6
    assert all(c["expected_points"] for c in cases)
    assert len({c["id"] for c in cases}) == len(cases)


def test_expected_points_state_both_must_and_must_not():
    """只写"必须指出 X 在浪费"的话，一个把所有词都判成浪费的模型也能满分 ——
    而那正是最危险的输出。"""
    from app.evals.runner import load_cases
    joined = " ".join(p for c in load_cases("ads") for p in c["expected_points"])
    assert "必须" in joined
    assert "不得" in joined


def test_thresholds_match_the_production_rules():
    """评测标准和生产规则必须是同一套阈值，否则测出来的"合格"跟线上行为无关。"""
    from app.evals import build_ads_cases as b
    assert b.MIN_CLICKS_TO_NEGATE == 15
    assert b.MAX_BID_STEP == 0.15
