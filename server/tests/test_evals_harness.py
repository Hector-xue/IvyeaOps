"""评测框架自身的测试。

**最要紧的是"对评测的评测"**：一套永远给绿灯的评测比没有评测更糟 —— 它会让人
以为有护栏。所以这里专门验证：注入一个会产生幻觉的输出，评测**必须**变红。
"""
from __future__ import annotations

import json

import pytest

from app.evals import rubric, runner


# ── 打分逻辑 ────────────────────────────────────────────────────────────

def _judge(payload: dict):
    """造一个固定回复的判官。"""
    return lambda _prompt: json.dumps(payload, ensure_ascii=False)


def test_good_output_passes():
    r = rubric.score_case({"id": "c1"}, "输出",
                          _judge({"reason": "有据可依", "evidence": 4, "correctness": 4,
                                  "actionable": 3, "safety": 4, "hallucination": 0}))
    assert r.average >= rubric.PASS_THRESHOLD
    assert r.passed is True


def test_hallucination_is_a_veto(caplog):
    """一份编造了 ASIN 或数字的报告，其余维度分再高也是负资产 ——
    它会让用户按不存在的数据去改真实的广告活动。"""
    r = rubric.score_case({"id": "c1"}, "输出",
                          _judge({"reason": "编了一个竞品 ASIN", "evidence": 4,
                                  "correctness": 4, "actionable": 4, "safety": 4,
                                  "hallucination": 3}))
    assert r.fatal_hits == ["hallucination"]
    assert r.average == 0.0, "致命项命中还能靠其余维度拉平均分，就等于没有一票否决"
    assert r.passed is False


def test_mediocre_output_fails_the_threshold():
    r = rubric.score_case({"id": "c1"}, "输出",
                          _judge({"reason": "泛泛而谈", "evidence": 2, "correctness": 3,
                                  "actionable": 1, "safety": 3, "hallucination": 0}))
    assert r.passed is False


def test_median_absorbs_a_single_wild_judgement():
    """判官偶尔会给出离谱的一次（漏读输入、被某句话带偏）。中位数对这种单点异常
    免疫，平均值不免疫 —— 这正是取中位数的理由。"""
    replies = [
        {"reason": "好", "evidence": 4, "correctness": 4, "actionable": 4, "safety": 4, "hallucination": 0},
        {"reason": "好", "evidence": 4, "correctness": 4, "actionable": 4, "safety": 4, "hallucination": 0},
        {"reason": "抽风", "evidence": 0, "correctness": 0, "actionable": 0, "safety": 0, "hallucination": 0},
    ]
    seq = iter(replies)
    r = rubric.score_case({"id": "c1"}, "输出",
                          lambda _p: json.dumps(next(seq), ensure_ascii=False), runs=3)
    assert r.average == 4.0


def test_judge_failures_do_not_kill_the_run():
    def flaky(_prompt):
        raise RuntimeError("判官超时")

    r = rubric.score_case({"id": "c1"}, "输出", flaky)
    assert r.scores == {} and r.passed is False


def test_unparseable_judgement_is_skipped_not_guessed():
    r = rubric.score_case({"id": "c1"}, "输出", lambda _p: "我觉得挺好的")
    assert r.scores == {}, "解析不了就该算没评，不能瞎猜一个分数"


def test_parse_handles_code_fenced_replies():
    """模型很爱把 JSON 裹进代码块 —— 解析要宽松，否则大量有效评分被丢掉。"""
    text = '思考…\n```json\n{"reason":"x","evidence":3,"correctness":3,' \
           '"actionable":3,"safety":3,"hallucination":0}\n```'
    assert rubric.parse_judgement(text)["correctness"] == 3


# ── 案例加载 ────────────────────────────────────────────────────────────

def test_broken_case_file_fails_loudly(tmp_path):
    """少跑了几个案例却显示"全过"，比直接失败更糟。"""
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "bad.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError, match="坏了"):
        runner.load_cases("s", root=tmp_path)


def test_case_missing_required_fields_fails_loudly(tmp_path):
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "x.json").write_text('{"prompt": "只有提示词"}', encoding="utf-8")
    with pytest.raises(ValueError, match="缺字段"):
        runner.load_cases("s", root=tmp_path)


def test_shipped_ads_cases_are_valid():
    """仓库里带的种子案例本身要合法，否则第一个来贡献案例的人会照着抄错。"""
    cases = runner.load_cases("ads")
    assert len(cases) >= 2
    for c in cases:
        assert c["expected_points"] and c["prompt"]
        assert "input" in c


# ── 整轮 ────────────────────────────────────────────────────────────────

def test_missing_judge_is_reported_not_silently_passed(tmp_path):
    """没配判官时不能一片绿 —— 那会让人以为有护栏。"""
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "a.json").write_text(
        json.dumps({"prompt": "p", "expected_points": ["x"]}), encoding="utf-8")
    report = runner.run_suite("s", lambda c: "out", judge=None, root=tmp_path)
    assert report["status"] == "skipped"
    assert "判官" in report["detail"]


def test_a_hallucinating_system_turns_the_suite_red(tmp_path):
    """**对评测的评测**：一套永远给绿灯的评测比没有评测更糟。"""
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "a.json").write_text(
        json.dumps({"prompt": "p", "expected_points": ["x"]}), encoding="utf-8")

    report = runner.run_suite(
        "s",
        lambda c: "根据 B0FAKEASIN 的数据……",          # 编造
        judge=_judge({"reason": "编了 ASIN", "evidence": 4, "correctness": 4,
                      "actionable": 4, "safety": 4, "hallucination": 4}),
        root=tmp_path, judge_runs=1)
    assert report["status"] == "fail"
    assert report["hallucinations"] == 1


def test_a_broken_system_does_not_abort_the_whole_suite(tmp_path):
    (tmp_path / "s").mkdir()
    for name in ("a", "b"):
        (tmp_path / "s" / f"{name}.json").write_text(
            json.dumps({"prompt": "p", "expected_points": ["x"]}), encoding="utf-8")

    calls = {"n": 0}

    def flaky(_case):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("第一个案例炸了")
        return "正常输出"

    report = runner.run_suite(
        "s", flaky,
        judge=_judge({"reason": "ok", "evidence": 4, "correctness": 4,
                      "actionable": 4, "safety": 4, "hallucination": 0}),
        root=tmp_path, judge_runs=1)
    assert report["total"] == 2
    assert any("error" in r for r in report["results"])
    assert any(r.get("passed") for r in report["results"])


def test_render_is_readable(tmp_path):
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "a.json").write_text(
        json.dumps({"prompt": "p", "expected_points": ["x"]}), encoding="utf-8")
    report = runner.run_suite("s", lambda c: "out", judge=None, root=tmp_path)
    assert "评测 s" in runner.render(report)
