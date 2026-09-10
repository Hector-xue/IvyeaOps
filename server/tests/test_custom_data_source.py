"""自定义 MCP 数据源：映射引擎 + 注册表 + provider 契约。

这一层错了不会报错，只会让每张卡片上的数字变成空 —— 所以断言要盯住
"翻译出来的字段名和内置源一模一样"，而不是"没抛异常"。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.core import hub_settings
from app.services import custom_source_mcp as mcp
from app.services import custom_source_provider as provider
from app.services import custom_source_registry as registry


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个用例开跑前清空注册表。

    conftest 把 data_dir 指到临时目录是**整轮共享**的，而注册表落在
    hub_settings 里 —— 不清的话，上一个文件存的源会被下一个文件数进去，
    表现为"单跑全绿、一起跑就挂"。
    """
    from app.core import hub_settings
    hub_settings.save({"custom_data_sources": ""})
    yield
    hub_settings.save({"custom_data_sources": ""})


# ── 路径取值 ─────────────────────────────────────────────────────────────────

def test_resolve_path_dotted_and_index():
    node = {"a": {"b": [{"c": 7}, {"c": 8}]}}
    assert provider.resolve_path(node, "a.b[0].c") == 7
    assert provider.resolve_path(node, "a.b[1].c") == 8
    assert provider.resolve_path(node, "a.b[9].c") is None
    assert provider.resolve_path(node, "a.missing.c") is None


def test_resolve_path_alternatives_skip_empty():
    """同一个概念在不同工具里字段名不一样是常态，备选路径必须真的往后试。"""
    assert provider.resolve_path({"reviews": 12}, "ratings||reviews||reviewCount") == 12
    assert provider.resolve_path({"ratings": "", "reviews": 3}, "ratings||reviews") == 3
    assert provider.resolve_path({"ratings": 0}, "ratings||reviews") == 0


def test_map_fields_infers_numeric_by_target_name():
    row = {"p": "1,234.5", "t": "标题", "cat": "Home & Kitchen"}
    out = provider.map_fields(row, {"price": "p", "title": "t", "bsr_category": "cat"})
    assert out["price"] == 1234.5          # 带千分位的字符串要变成数字
    assert out["title"] == "标题"
    assert out["bsr_category"] == "Home & Kitchen"   # 名字里有 category 但不是数值


def test_map_fields_explicit_type_and_default():
    out = provider.map_fields({}, {
        "title": {"path": "nope", "default": "无"},
        "price": {"path": "nope", "type": "number", "default": None},
    })
    assert out == {"title": "无", "price": None}


# ── 入参模板 ─────────────────────────────────────────────────────────────────

def test_render_args_keeps_type_for_exact_placeholder():
    """整值占位符必须保留原类型 —— size 传成字符串会被服务器静默忽略或报错。"""
    rendered = provider.render_args(
        {"request": {"keywords": "{keyword}", "size": "{top_n}", "page": 1}},
        provider.template_vars(keyword="ipad case", top_n=30),
    )
    assert rendered["request"]["size"] == 30
    assert isinstance(rendered["request"]["size"], int)
    assert rendered["request"]["keywords"] == "ipad case"


def test_render_args_interpolates_inside_string():
    rendered = provider.render_args("{marketplace}:{keyword}",
                                    provider.template_vars(keyword="kw", marketplace="US"))
    assert rendered == "US:kw"


def test_template_vars_month_is_last_completed_month():
    import datetime
    month = provider.template_vars()["month"]
    assert len(month) == 6 and month.isdigit()
    assert month < datetime.date.today().strftime("%Y%m") or datetime.date.today().day == 1


def test_day_normalization_variants():
    assert provider._day("2024-05-07") == "2024-05-07"
    assert provider._day("202405") == "2024-05-01"       # 只到月份补 1 号
    assert provider._day("2024年05月") == "2024-05-01"
    assert provider._day("garbage") is None


# ── 信封拆解 ─────────────────────────────────────────────────────────────────

def test_unwrap_handles_known_envelopes():
    assert mcp.unwrap({"doc": {"f": "说明"}, "data": [1, 2]}) == [1, 2]       # Sorftime 型
    assert mcp.unwrap({"code": "OK", "message": "成功", "data": {"x": 1}}) == {"x": 1}
    assert mcp.unwrap({"x": 1}) == {"x": 1}                                   # 裸对象原样


def test_unwrap_explicit_envelope_path():
    payload = {"result": {"items": [{"a": 1}]}}
    assert mcp.unwrap(payload, "result.items") == [{"a": 1}]


def test_rows_finds_list_in_any_container():
    assert mcp.rows({"doc": {}, "data": {"top100_products": []}}) == []
    assert mcp.rows({"data": [{"a": 1}]}) == [{"a": 1}]
    assert mcp.rows([{"a": 1}, "junk"]) == [{"a": 1}]


def test_parse_body_accepts_sse_and_plain_json():
    assert mcp.parse_body('event: message\ndata: {"result": {"k": 1}}\n\n')["result"] == {"k": 1}
    assert mcp.parse_body('{"result": {"k": 2}}')["result"] == {"k": 2}
    assert mcp.parse_body("not json at all") == {}


# ── 「失败装在成功响应里」 ───────────────────────────────────────────────────

class _FakeRPC:
    """替掉 _rpc，直接喂一个 tools/call 的 result。"""

    def __init__(self, result):
        self.result = result

    async def __call__(self, state, method, params):
        return self.result


def _call(monkeypatch, result):
    monkeypatch.setattr(mcp, "_rpc", _FakeRPC(result))
    # 用 asyncio.run 而不是 get_event_loop：同一轮跑多个测试文件时，别的文件
    # 关掉了主线程的 loop，get_event_loop 就直接抛 "no current event loop"。
    return asyncio.run(mcp.call_tool({}, "some_tool", {}))


def test_call_tool_raises_on_is_error(monkeypatch):
    with pytest.raises(mcp.CustomSourceError):
        _call(monkeypatch, {"isError": True, "content": [{"type": "text", "text": "bad key"}]})


def test_call_tool_raises_on_business_code(monkeypatch):
    """HTTP 200 + code=ERROR 是最阴的一种：不拦就当数据喂给模型了。"""
    body = json.dumps({"code": "ERROR", "message": "额度不足"})
    with pytest.raises(mcp.CustomSourceError):
        _call(monkeypatch, {"content": [{"type": "text", "text": body}]})


def test_call_tool_raises_on_prompt_text(monkeypatch):
    with pytest.raises(mcp.CustomSourceError):
        _call(monkeypatch, {"content": [{"type": "text", "text": "Please specify the site to query"}]})


def test_call_tool_accepts_ok_envelope(monkeypatch):
    body = json.dumps({"code": "OK", "data": {"x": 1}})
    assert _call(monkeypatch, {"content": [{"type": "text", "text": body}]}) == {"code": "OK", "data": {"x": 1}}


# ── 注册表 ───────────────────────────────────────────────────────────────────

def _cfg(**over):
    base = {
        "id": "myerp", "name": "我的源", "url": "https://mcp.example.com/mcp",
        "auth": {"mode": "query", "name": "key", "value": "s3cret"},
        "surfaces": ["home"],
        "capabilities": {"home_asin_pulse": {"tool": "product_detail",
                                             "args": {"asin": "{asin}"},
                                             "fields": {"title": "title"}}},
    }
    base.update(over)
    return base


def test_validate_rejects_bad_slug_and_url():
    with pytest.raises(registry.RegistryError):
        registry.validate(_cfg(id="Bad Slug!"))
    with pytest.raises(registry.RegistryError):
        registry.validate(_cfg(url="mcp.example.com"))


def test_surfaces_are_derived_not_taken_from_input():
    """板块是**推导**出来的：配了什么能力就在什么板块出现。

    上一版让用户自己勾板块，勾了却还没配对应能力就直接报错 —— 连"自动配置"
    那个按钮都被这条校验拦死了。板块是结果，不是输入。
    """
    # 调用方乱指定也不作数
    cleaned = registry.validate(_cfg(surfaces=["market", "playbook", "home"], capabilities={}))
    assert cleaned["surfaces"] == []

    only_home = registry.validate(_cfg(surfaces=[], capabilities={
        "home_asin_pulse": {"tool": "t", "args": {}, "fields": {"title": "t"}}}))
    assert only_home["surfaces"] == ["home"]

    both = registry.validate(_cfg(surfaces=[], capabilities={
        "home_asin_pulse": {"tool": "t", "args": {}, "fields": {"title": "t"}},
        "keyword_pipeline": {"steps": [{"label": "a", "tool": "t", "args": {}}]}}))
    assert both["surfaces"] == ["home", "market", "playbook"]


def test_errors_never_leak_internal_identifiers():
    """报错是给人看的 —— 用户没见过 keyword_pipeline 这种名字。"""
    with pytest.raises(registry.RegistryError) as exc:
        registry.validate(_cfg(capabilities={"keyword_pipeline": {"steps": []}}))
    assert "keyword_pipeline" not in str(exc.value)
    assert "关键词采集" in str(exc.value)


def test_save_roundtrip_masks_secret_but_keeps_it(tmp_path, monkeypatch):
    registry.save(_cfg())
    listed = registry.list_all(redact=True)
    assert len(listed) == 1
    assert listed[0]["auth"]["value"] == ""          # 不回传明文
    assert listed[0]["auth"]["value_set"] is True    # 但告诉前端"已设置"
    live = registry.get("custom:myerp")
    assert live["auth"]["value"] == "s3cret"


def test_resaving_with_blank_secret_keeps_previous(tmp_path):
    registry.save(_cfg())
    registry.save(_cfg(name="改个名", auth={"mode": "query", "name": "key", "value": ""}))
    live = registry.get("custom:myerp")
    assert live["name"] == "改个名"
    assert live["auth"]["value"] == "s3cret"         # 改名不该把 key 冲掉


def test_secret_is_encrypted_on_disk(tmp_path):
    registry.save(_cfg())
    raw = hub_settings._read_file().get("custom_data_sources") or ""
    assert "s3cret" not in raw, "凭据不能明文落盘"


def test_disabled_source_is_not_resolvable():
    registry.save(_cfg(enabled=False))
    assert registry.get("custom:myerp") is None
    assert registry.is_custom("custom:myerp") is False


def test_builtin_ids_never_match_custom():
    """内置三家的 id 不带前缀，绝不能被自定义解析器接管。"""
    registry.save(_cfg())
    for builtin in ("sorftime", "sellersprite", "sif"):
        assert registry.get(builtin) is None
        assert provider.provider_for(builtin) is None


def test_delete_source():
    registry.save(_cfg())
    assert registry.delete("custom:myerp") is True
    assert registry.delete("custom:myerp") is False


# ── Provider 契约 ────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def test_asin_pulse_maps_to_neutral_fields(monkeypatch):
    cfg = registry.validate(_cfg(capabilities={
        "home_asin_pulse": {
            "tool": "detail", "args": {"asin": "{asin}", "site": "{marketplace}"},
            "fields": {
                "title": "productName", "brand": "brandName",
                "price": "priceInfo.amount", "bsr": "rank.main",
                "review_count": "ratings||reviews", "rating": "star",
            },
        }}))
    payload = {"code": "OK", "data": {
        "productName": "Widget", "brandName": "ACME",
        "priceInfo": {"amount": "19.99"}, "rank": {"main": 1234},
        "reviews": 88, "star": 4.5,
    }}

    async def fake_call(self, spec, variables):
        assert variables["asin"] == "B00TEST1234"
        assert variables["marketplace"] == "US"
        return payload

    monkeypatch.setattr(provider.CustomProvider, "_call", fake_call)
    out = _run(provider.CustomProvider(cfg).home_asin_pulse("B00TEST1234", "US"))
    assert out["error"] is None
    assert out["title"] == "Widget" and out["brand"] == "ACME"
    assert out["price"] == 19.99 and out["bsr"] == 1234
    assert out["review_count"] == 88 and out["rating"] == 4.5
    assert out["data_source"] == "custom:myerp"
    # 契约里的字段一个都不能少，缺了前端读到 undefined 会直接不渲染那一行
    for key in ("sub_rank", "sub_category", "est_sales", "variations", "coupon", "inventory"):
        assert key in out


def test_missing_capability_returns_contract_shape_not_500():
    cfg = registry.validate(_cfg(capabilities={"home_asin_pulse": {
        "tool": "t", "args": {}, "fields": {}}}))
    out = _run(provider.CustomProvider(cfg).home_market_metrics("kw", "US"))
    assert out["error"] and "home_market_metrics" in out["error"]
    assert out["search_volume"] is None and out["data_source"] == "custom:myerp"


def test_category_builds_bands_and_summary(monkeypatch):
    cfg = registry.validate(_cfg(surfaces=[], capabilities={"home_category": {
        "tool": "cat", "args": {}, "rows": "top",
        "row_fields": {"asin": "asin", "title": "t", "price": "p", "est_sales": "s"},
    }}))
    rows = [{"asin": f"B{i:09d}", "t": f"P{i}", "p": 10 + i, "s": 100} for i in range(5)]

    async def fake_call(self, spec, variables):
        return {"data": {"top": rows}}

    monkeypatch.setattr(provider.CustomProvider, "_call", fake_call)
    out = _run(provider.CustomProvider(cfg).home_category("kw", "US"))
    assert out["error"] is None
    assert len(out["top"]) == 5 and out["top"][0]["rank"] == 1
    assert out["summary"]["count"] == 5 and out["summary"]["avg_price"] == 12.0
    assert out["summary"]["total_sales"] == 500.0
    assert out["bands"] and sum(b["count"] for b in out["bands"]) == 5


def test_trend_series_normalizes_days(monkeypatch):
    cfg = registry.validate(_cfg(surfaces=[], capabilities={"home_keyword_trend_series": {
        "tool": "trend", "args": {}, "row_fields": {"day": "month", "value": "search"},
    }}))

    async def fake_call(self, spec, variables):
        return {"data": [{"month": "202405", "search": "1,000"},
                         {"month": "bad", "search": 5},
                         {"month": "2024-06-01", "search": 2000}]}

    monkeypatch.setattr(provider.CustomProvider, "_call", fake_call)
    series, err = _run(provider.CustomProvider(cfg).home_keyword_trend_series("kw", "US"))
    assert err is None
    assert series == [("2024-05-01", 1000.0), ("2024-06-01", 2000.0)]   # 坏点丢掉不报错


def test_pipeline_collects_all_steps_and_reports_partial_failure(monkeypatch):
    cfg = registry.validate(_cfg(surfaces=[], capabilities={"keyword_pipeline": {"steps": [
        {"label": "详情", "tool": "a", "args": {"kw": "{keyword}"}},
        {"label": "趋势", "tool": "b", "args": {}},
    ]}}))
    calls = []

    async def fake_call_tool(state, tool, args):
        calls.append((tool, args))
        if tool == "b":
            raise mcp.CustomSourceError("boom")
        return {"ok": 1}

    class _FakeSession:
        async def __aenter__(self): return {}
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(mcp, "session", lambda cfg: _FakeSession())
    monkeypatch.setattr(mcp, "call_tool", fake_call_tool)

    steps = []

    async def progress(step, done, total):
        steps.append(step)

    data, errors = _run(provider.CustomProvider(cfg).keyword_pipeline("ipad", "US", progress))
    assert data == {"详情": {"ok": 1}}                 # 成功的步骤照常收进来
    assert errors and "趋势" in errors[0]              # 失败的步骤明确报出来，不静默
    assert calls[0][1] == {"kw": "ipad"}
    assert steps[-1] == "完成"
