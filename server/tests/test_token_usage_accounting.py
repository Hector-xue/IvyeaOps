"""Token 统计的口径。

盯的是一件**曾经真出过事**的事：`total_tokens` 少算缓存。

这个 bug 不会报错、不会崩，页面照常出图 —— 它只是把结论悄悄反过来：Claude Code 把
上下文记在 cache_read 里、裸 input 只有几个 token，而 Codex 上报的 input 本身就含
缓存。少算缓存 = 抹掉 Claude、照单全收 Codex，于是"谁用得多"整个反了。见 ADR-0018。
"""
from __future__ import annotations

import pytest

from app.routers import monitor


def _fake_source(records):
    """把一组 _rec 包成 _TOKEN_SOURCES 认的扫描器。"""
    def scan(_since):
        return list(records), {"source": "T", "path": None, "status": "included",
                               "sessions": len(records), "total": 0}
    return scan


@pytest.fixture()
def only_source(monkeypatch):
    """只留一个假源，并掐掉归档回填 —— 否则真机的历史数据会混进断言。"""
    def install(records):
        monkeypatch.setattr(monitor, "_TOKEN_SOURCES", [("T", _fake_source(records))])
        from app.services import token_archive
        monkeypatch.setattr(token_archive, "load_records", lambda _since: [])
        return monitor.token_usage(_user="t")
    return install


# ── 口径：缓存计入总量 ──────────────────────────────────────────────────
def test_total_tokens_includes_cache(only_source):
    """Claude Code 的典型形状：几乎全部量都在 cache_read 上。"""
    rec = monitor._rec(ts=monitor.time.time(), model="claude-opus-5",
                       inp=10, out=90, agent="A", source="S",
                       cache_read=900_000, cache_write=100_000)
    d = only_source([rec])
    assert d["totals"]["total_tokens"] == 10 + 90 + 900_000 + 100_000
    assert d["totals"]["cache_read_tokens"] == 900_000
    assert d["totals"]["cache_write_tokens"] == 100_000
    # 三张聚合表必须同口径，否则排行榜和明细互相打架
    assert d["agents"][0]["total_tokens"] == 1_000_100
    assert d["models"][0]["total_tokens"] == 1_000_100
    assert d["daily"][0]["total_tokens"] == 1_000_100
    # 明细表要能自己对上账
    row = d["daily"][0]
    assert (row["input_tokens"] + row["output_tokens"]
            + row["cache_read_tokens"] + row["cache_write_tokens"]) == row["total_tokens"]


def test_cache_heavy_agent_outranks_raw_input_agent(only_source):
    """这条就是当初被搞反的那个结论，钉死它。

    A 走缓存（Claude Code 形状），B 把缓存算在 input 里（Codex 形状）。A 的真实用量
    是 B 的 10 倍，排行榜就必须是 A 在前。
    """
    now = monitor.time.time()
    a = monitor._rec(now, "claude-opus-5", 10, 90, "Claude Code", "S", cache_read=10_000_000)
    b = monitor._rec(now, "gpt-5.5", 1_000_000, 1_000, "Codex", "S")
    d = only_source([a, b])
    assert [x["agent"] for x in d["agents"]] == ["Claude Code", "Codex"]


# ── 价目表 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("model,expect", [
    ("claude-opus-5", (5, 25)),
    ("claude-opus-4-8", (5, 25)),      # 曾经写成作废的 (15, 75)
    ("claude-fable-5", (10, 50)),
    ("claude-sonnet-5", (3, 15)),
    ("claude-haiku-4-5-20251001", (1, 5)),
])
def test_current_models_are_priced(model, expect):
    assert monitor._price_for(model) == expect
    assert monitor._price_for(model) != monitor._DEFAULT_PRICE or expect == monitor._DEFAULT_PRICE


def test_cache_is_cheaper_than_fresh_input():
    """缓存读按 0.1× 输入价 —— 等价计费会让"含缓存"的成本估算离谱地高。"""
    fresh = monitor._estimate_cost("claude-opus-5", 1_000_000, 0)
    cached = monitor._estimate_cost("claude-opus-5", 0, 0, cache_read=1_000_000)
    assert cached == pytest.approx(fresh * 0.1)


# ── 模型归属 ────────────────────────────────────────────────────────────
def test_synthetic_model_name_is_not_used(tmp_path, monkeypatch):
    """`<synthetic>` 是 Claude Code 本地构造的假模型名，不能拿它当归属。

    它排在真实回合前面，旧实现"取第一个 model"就会把整个会话记到它名下。
    """
    import json
    sess = tmp_path / "proj"
    sess.mkdir()
    f = sess / "s.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in [
        {"message": {"model": "<synthetic>", "usage": {"input_tokens": 1, "output_tokens": 1}}},
        {"message": {"model": "claude-opus-5",
                     "usage": {"input_tokens": 5, "output_tokens": 50,
                               "cache_read_input_tokens": 500_000}}},
    ]), encoding="utf-8")
    monkeypatch.setattr(monitor, "_claude_projects", lambda: tmp_path)
    got = monitor._scan_claude_sessions(0)
    assert len(got) == 1
    assert got[0]["model"] == "claude-opus-5"
    assert got[0]["cache_read"] == 500_000


def test_scan_claude_falls_back_when_only_synthetic(tmp_path, monkeypatch):
    """全是合成名时不能瞎猜一个型号，落到中性的 claude-code。"""
    import json
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "s.jsonl").write_text(json.dumps(
        {"message": {"model": "<synthetic>", "usage": {"input_tokens": 3, "output_tokens": 4}}}
    ), encoding="utf-8")
    monkeypatch.setattr(monitor, "_claude_projects", lambda: tmp_path)
    assert monitor._scan_claude_sessions(0)[0]["model"] == "claude-code"


# ── 新接入的源 ──────────────────────────────────────────────────────────
def test_ivyea_agent_sessions_are_counted(tmp_path, monkeypatch):
    """ivyea-agent 的会话账本。目录里混着 MCP 结果转储，要靠 usage 筛掉。"""
    import json
    (tmp_path / "a.json").write_text(json.dumps(
        {"model": "deepseek-v4-pro", "updated": 1_700_000_000,
         "usage": {"prompt": 12_000, "completion": 300, "cost": 0.1, "turns": 2}}
    ), encoding="utf-8")
    (tmp_path / "dump.json").write_text(json.dumps({"doc": "x", "data": []}), encoding="utf-8")
    monkeypatch.setattr(monitor, "_ivyea_sessions_dir", lambda: tmp_path)
    recs, cov = monitor._scan_ivyea_agent(0)
    assert cov["status"] == "included"
    assert len(recs) == 1
    assert (recs[0]["input"], recs[0]["output"]) == (12_000, 300)
    assert recs[0]["model"] == "deepseek-v4-pro"


def test_ivyea_agent_serve_path_ledger_is_counted(tmp_path, monkeypatch):
    """serve/HTTP 那条路只写 stats.usage，顶层 usage 恒为 {} —— 也必须算上。

    真出过事：工作台/agents/ops 自动链路跑出来的会话全走这条路，只认顶层 usage 时
    它们被整个跳过，页面上"Ivyea Agent"一栏近乎为 0，看着像没在用。
    """
    import json
    (tmp_path / "serve.json").write_text(json.dumps(
        {"model": "glm-5.3-flash", "updated": 1_700_000_000, "usage": {},
         "stats": {"turns": 3, "usage": {"prompt_tokens": 100_000,
                                         "prompt_cache_hit_tokens": 90_000,
                                         "completion_tokens": 5_000}}}
    ), encoding="utf-8")
    monkeypatch.setattr(monitor, "_ivyea_sessions_dir", lambda: tmp_path)
    recs, cov = monitor._scan_ivyea_agent(0)
    assert len(recs) == 1
    r = recs[0]
    # prompt_tokens 含缓存命中：拆成 input / cache_read，总量不变
    assert (r["input"], r["cache_read"], r["output"]) == (10_000, 90_000, 5_000)
    assert cov["total"] == 105_000


def test_ivyea_agent_cache_hit_over_prompt_never_goes_negative(tmp_path, monkeypatch):
    """provider 报了个比 prompt 还大的缓存数时，input 不能变成负数把总量算小。"""
    import json
    (tmp_path / "weird.json").write_text(json.dumps(
        {"model": "x", "updated": 1_700_000_000,
         "stats": {"usage": {"prompt_tokens": 100, "prompt_cache_hit_tokens": 999,
                             "completion_tokens": 10}}}
    ), encoding="utf-8")
    monkeypatch.setattr(monitor, "_ivyea_sessions_dir", lambda: tmp_path)
    recs, _ = monitor._scan_ivyea_agent(0)
    assert (recs[0]["input"], recs[0]["cache_read"]) == (0, 100)


def test_missing_source_reports_missing_not_zero(monkeypatch):
    """路径没配就说"没配"，不能装成"用量为 0" —— 后者会被当成真相。"""
    monkeypatch.setattr(monitor, "_ivyea_sessions_dir", lambda: None)
    monkeypatch.setattr(monitor, "_dsh_sessions_dir", lambda: None)
    assert monitor._scan_ivyea_agent(0)[1]["status"] == "missing"
    assert monitor._scan_dsh(0)[1]["status"] == "missing"


@pytest.mark.skipif(__import__("shutil").which("zstd") is None,
                    reason="本机没有 zstd 命令")
def test_dsh_sessions_are_counted(tmp_path, monkeypatch):
    """DeepSeek Harness 的会话是 zstd 压的，且 usage 用的是 camelCase。

    reasoning 归到 output（思考 token 按输出计价），cacheRead 独立成缓存列。
    """
    import json, subprocess
    d = tmp_path / "proj" / "session-1"
    d.mkdir(parents=True)
    lines = [
        {"type": "request/header", "time": 1_787_000_000_000, "data": {"model": "deepseek-v4-flash"}},
        {"type": "assistant/message", "time": 1_787_000_000_000,
         "data": {"usage": {"inputTokens": 100, "outputTokens": 20,
                            "reasoningTokens": 5, "cacheReadTokens": 9_000}}},
    ]
    raw = "\n".join(json.dumps(x) for x in lines).encode()
    subprocess.run(["zstd", "-q", "-o", str(d / "session.jsonl.zstd"), "-"],
                   input=raw, check=True)
    monkeypatch.setattr(monitor, "_dsh_sessions_dir", lambda: tmp_path)
    recs, cov = monitor._scan_dsh(0)
    assert cov["status"] == "included"
    assert len(recs) == 1
    assert recs[0]["input"] == 100
    assert recs[0]["output"] == 25          # output + reasoning
    assert recs[0]["cache_read"] == 9_000
    assert recs[0]["model"] == "deepseek-v4-flash"
