"""Regression tests for the news digest parser and batching.

The AI chain's answer is machine-parsed: providers have been observed emitting
the array twice, appending 引用说明 citation notes with stray brackets, using
unescaped ASCII quotes inside string values, and truncating the tail. All of
those used to silently fall back to untranslated English items.
"""
from app.services.news_digest import (
    _clean_text,
    _extract_json_array,
    _repair_inner_quotes,
)


def test_extract_plain_array():
    assert _extract_json_array('[{"i": 0, "summary_zh": "x"}]') == [
        {"i": 0, "summary_zh": "x"}
    ]


def test_extract_fenced_and_prose_wrapped():
    assert _extract_json_array('```json\n[{"i": 1}]\n```') == [{"i": 1}]
    got = _extract_json_array('好的，结果如下：[{"i": 2, "tags": ["x"]}] 以上。')
    assert got and got[0]["i"] == 2


def test_extract_duplicate_array_with_citation_note():
    # Real-world ivyea-agent shape: array emitted twice + citation postscript.
    text = (
        '[{"i": 0, "summary_zh": "第一版"}]\n'
        '[{"i": 0, "summary_zh": "第二版 [K3]"}]\n\n'
        "> **引用说明**：i=0 引用 [K3]，未被 [K1]–[K4] 覆盖。"
    )
    got = _extract_json_array(text)
    assert got == [{"i": 0, "summary_zh": "第一版"}]


def test_extract_repairs_unescaped_inner_quotes():
    text = '[{"i": 0, "summary_zh": "OpenAI提出"反向联邦制"治理思路，各州先行。"}]'
    got = _extract_json_array(text)
    assert got and got[0]["summary_zh"] == 'OpenAI提出"反向联邦制"治理思路，各州先行。'


def test_extract_salvages_truncated_array():
    text = '[{"i": 0, "summary_zh": "完整"}, {"i": 1, "summary_zh": "被截'
    got = _extract_json_array(text)
    assert got == [{"i": 0, "summary_zh": "完整"}]


def test_extract_rejects_garbage_and_string_arrays():
    assert _extract_json_array("no json here [broken") is None
    # A bare string array (e.g. a stray tags list) is not an item array.
    assert _extract_json_array('前言 ["a", "b"] 后记') is None


def test_repair_inner_quotes_keeps_valid_json_intact():
    src = '{"a": "x, y: z", "tags": ["p", "q"], "n": 3}'
    assert _repair_inner_quotes(src) == src


def test_clean_text_strips_citation_markers():
    assert _clean_text("摘要 [K3]，其余 【K12】。") == "摘要，其余。"
    assert _clean_text("无引用") == "无引用"


def test_round_robin_by_source_alternates():
    from app.services.news_digest import _round_robin_by_source

    items = (
        [{"source": "A", "n": i} for i in range(5)]
        + [{"source": "B", "n": i} for i in range(2)]
        + [{"source": "C", "n": i} for i in range(1)]
    )
    out = _round_robin_by_source(items)
    assert [x["source"] for x in out] == ["A", "B", "C", "A", "B", "A", "A", "A"]
    # newest-first order inside each source is preserved
    assert [x["n"] for x in out if x["source"] == "A"] == [0, 1, 2, 3, 4]
