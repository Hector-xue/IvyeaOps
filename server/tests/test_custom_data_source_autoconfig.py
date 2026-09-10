"""自动配置：探测 → 试调 → 按真实返回生成映射。

这份测试的对象是"用户什么都不填也能配好"这件事本身，所以断言盯的是
**生成出来的映射能不能真的跑出数据**，而不是"生成了一份配置"。
"""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.services import custom_source_autoconfig as auto
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


# 一台仿真的 MCP 服务器：工具名和字段名都**故意和内置两家都不一样**，
# 免得测出来的是"照抄了 Sorftime 的字段名"而不是"真能自动认"。
TOOLS = [
    {"name": "item_lookup", "description": "查询商品基础信息（标题、售价、排名、评分）",
     "inputSchema": {"type": "object", "required": ["asin"],
                     "properties": {"asin": {"type": "string"}, "site": {"type": "string"}}}},
    {"name": "item_sales_history", "description": "商品销量历史趋势",
     "inputSchema": {"type": "object", "required": ["asin"],
                     "properties": {"asin": {"type": "string"}, "site": {"type": "string"}}}},
    {"name": "term_metrics", "description": "关键词搜索指标详情",
     "inputSchema": {"type": "object", "required": ["query"],
                     "properties": {"query": {"type": "string"}, "site": {"type": "string"}}}},
    {"name": "term_history", "description": "关键词搜索量趋势历史",
     "inputSchema": {"type": "object", "required": ["query"],
                     "properties": {"query": {"type": "string"}, "site": {"type": "string"}}}},
    {"name": "term_related", "description": "相关拓展词列表",
     "inputSchema": {"type": "object", "required": ["query"],
                     "properties": {"query": {"type": "string"}, "site": {"type": "string"},
                                    "size": {"type": "integer"}}}},
    {"name": "category_top", "description": "类目热销商品排行",
     "inputSchema": {"type": "object", "required": ["category"],
                     "properties": {"category": {"type": "string"}, "site": {"type": "string"},
                                    "topN": {"type": "integer"}}}},
    # 必填参数认不出来 → 必须被跳过，不能拿它去乱调
    {"name": "internal_debug", "description": "内部调试",
     "inputSchema": {"type": "object", "required": ["secretFlag"],
                     "properties": {"secretFlag": {"type": "string"}}}},
]

ITEM = {"productName": "Widget Pro", "brandName": "ACME", "mainImage": "http://img/1.jpg",
        "sellPrice": "19.99", "bigRank": 1234, "mainCategory": "Electronics",
        "smallRank": 12, "smallCategory": "Earbuds", "monthlySales": 900,
        "star": 4.5, "reviewNum": 88, "variantCount": 3}

TERM = {"keywords": "wireless earbuds", "searches": 120000, "suggestedBid": "1.35",
        "buyRate": 0.12, "purchases": 4300}

RELATED = [{"keywords": "wireless earbuds cheap", "searches": 5400, "suggestedBid": 0.9,
            "marketPeriod": "Q4", "purchases": 220},
           {"keywords": "earbuds case", "searches": 3100, "suggestedBid": 0.7,
            "marketPeriod": "", "purchases": 90}]

TOP = [{"asin": "B0000000A1", "productName": "A", "brandName": "X", "mainImage": "u",
        "sellPrice": 10, "bigRank": 5, "monthlySales": 100, "star": 4.1, "reviewNum": 10},
       {"asin": "B0000000A2", "productName": "B", "brandName": "Y", "mainImage": "u",
        "sellPrice": 20, "bigRank": 6, "monthlySales": 200, "star": 4.2, "reviewNum": 20}]


class _Handler(BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, *args):
        return

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) or b"{}")
        method, params = body.get("method"), body.get("params") or {}
        if method == "initialize":
            return self._sse({"protocolVersion": "2024-11-05"})
        if method == "tools/list":
            return self._sse({"tools": TOOLS})
        if method != "tools/call":
            return self._sse({})

        name, args = params.get("name"), params.get("arguments") or {}
        _Handler.calls.append((name, args))
        data = {
            "item_lookup": ITEM,
            "item_sales_history": {"points": [{"date": "202405", "units": 800},
                                              {"date": "202406", "units": 900}]},
            "term_metrics": TERM,
            "term_history": {"points": [{"date": "202405", "searchVolume": 110000},
                                        {"date": "202406", "searchVolume": 120000}]},
            "term_related": RELATED,
            "category_top": {"nodeName": "Earbuds", "nodeId": "12345",
                             "avgPrice": 15.0, "totalUnits": 300, "items": TOP},
        }.get(name)
        if data is None:
            return self._sse({"isError": True,
                              "content": [{"type": "text", "text": f"unknown tool {name}"}]})
        return self._sse({"content": [{"type": "text",
                                       "text": json.dumps({"code": "OK", "data": data})}]})

    def _sse(self, result):
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})
        raw = f"event: message\ndata: {payload}\n\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture()
def server():
    _Handler.calls = []
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/mcp"
    srv.shutdown()
    srv.server_close()


def _cfg(url: str) -> dict:
    return registry.validate({
        "id": "auto", "name": "自动源", "url": url, "surfaces": [],
        "auth": {"mode": "none", "name": "", "value": ""},
        "capabilities": {},
    })


def _auto(url: str) -> dict:
    return asyncio.run(auto.autoconfigure(_cfg(url), "wireless earbuds", "B08N5WRWNW"))


# ── 入参推断 ─────────────────────────────────────────────────────────────────

def test_build_args_maps_params_to_placeholders():
    args, missing = auto.build_args({
        "type": "object", "required": ["asin"],
        "properties": {"asin": {}, "amz_site": {}, "pageSize": {}, "page": {}},
    })
    assert args == {"asin": "{asin}", "amz_site": "{marketplace}",
                    "pageSize": "{top_n}", "page": 1}
    assert missing == []


def test_build_args_handles_nested_request_object():
    """卖家精灵那种 {"request": {...}} 的包法不认，它家所有工具都会被跳过。"""
    args, missing = auto.build_args({
        "type": "object",
        "properties": {"request": {"type": "object", "required": ["keywords"],
                                   "properties": {"keywords": {}, "marketplace": {}}}},
    })
    assert args == {"request": {"keywords": "{keyword}", "marketplace": "{marketplace}"}}
    assert missing == []


def test_build_args_reports_unfillable_required_param():
    _, missing = auto.build_args({"type": "object", "required": ["secretFlag"],
                                  "properties": {"secretFlag": {}}})
    assert missing == ["secretFlag"]


# ── 字段匹配 ─────────────────────────────────────────────────────────────────

def test_match_fields_uses_real_response_names():
    mapping, missing = auto.match_fields(ITEM, ("title", "price", "bsr", "rating", "coupon"))
    assert mapping["title"] == "productName"
    assert mapping["price"] == "sellPrice"
    assert mapping["bsr"] == "bigRank"
    assert missing == ["coupon"]          # 返回里真没有的字段要如实报缺


def test_match_fields_rejects_type_mismatch():
    """名字像但类型不对的，宁可不映射 —— 映上去就是卡片上一个诡异的字符串。"""
    mapping, missing = auto.match_fields({"price": "面议"}, ("price",))
    assert mapping == {} and missing == ["price"]


def test_match_fields_prefers_shallow_path():
    sample = {"price": 9.9, "detail": {"extra": {"price": 100}}}
    mapping, _ = auto.match_fields(sample, ("price",))
    assert mapping["price"] == "price"


# ── 端到端：自动配完就能跑 ───────────────────────────────────────────────────

def test_autoconfigure_picks_right_tool_per_capability(server):
    out = _auto(server)
    picked = {r["id"]: r.get("tool") for r in out["report"]["capabilities"] if r.get("ok")}
    assert picked["home_asin_pulse"] == "item_lookup"
    assert picked["home_product_trend_series"] == "item_sales_history"
    assert picked["home_keyword_pulse"] == "term_metrics"
    assert picked["home_keyword_trend_series"] == "term_history"
    assert picked["home_keyword_extends"] == "term_related"
    assert picked["home_category"] == "category_top"


def test_autoconfigure_skips_tools_with_unfillable_params(server):
    _auto(server)
    assert not any(name == "internal_debug" for name, _ in _Handler.calls)


def test_autoconfigure_lights_up_surfaces(server):
    out = _auto(server)
    assert set(out["report"]["surfaces"]) == {"home", "market", "playbook"}


def test_generated_config_actually_returns_data(server):
    """最重要的一条：自动生成的映射，拿去真跑必须出得来数据。"""
    out = _auto(server)
    cfg = registry.validate(out["source"])
    p = provider.CustomProvider(cfg)

    pulse = asyncio.run(p.home_asin_pulse("B08N5WRWNW", "US"))
    assert pulse["error"] is None
    assert pulse["title"] == "Widget Pro" and pulse["price"] == 19.99
    assert pulse["bsr"] == 1234 and pulse["review_count"] == 88

    kw = asyncio.run(p.home_keyword_pulse("wireless earbuds", "US"))
    assert kw["detail"]["monthly_search_volume"] == 120000
    assert kw["detail"]["searchVolume"] == 120000        # 前端读的别名也要在

    items, err = asyncio.run(p.home_keyword_extends("wireless earbuds", "US"))
    assert err is None and items[0]["keyword"] == "wireless earbuds cheap"
    assert items[0]["monthly_search"] == 5400

    series, err = asyncio.run(p.home_keyword_trend_series("wireless earbuds", "US"))
    assert err is None and series == [("2024-05-01", 110000.0), ("2024-06-01", 120000.0)]

    cat = asyncio.run(p.home_category("earbuds", "US"))
    assert cat["error"] is None and len(cat["top"]) == 2
    assert cat["top"][0]["asin"] == "B0000000A1" and cat["top"][0]["price"] == 10.0
    assert cat["summary"]["count"] == 2

    data, errors = asyncio.run(p.keyword_pipeline("wireless earbuds", "US", _noop))
    assert not errors and data


async def _noop(step, done, total):
    return None


def test_report_explains_what_failed(server):
    """没配成的能力要说清是为什么，用户才知道该去高级里改哪一项。"""
    out = _auto(server)
    failed = [r for r in out["report"]["capabilities"] if not r.get("ok")]
    for item in failed:
        assert item.get("error"), f"{item['id']} 没说明失败原因"
        assert item.get("label")


def test_autoconfigure_on_empty_server_raises():
    from app.services import custom_source_mcp as mcp

    class _Empty(BaseHTTPRequestHandler):
        def log_message(self, *a): return
        def do_POST(self):  # noqa: N802
            payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})
            raw = f"data: {payload}\n\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    srv = HTTPServer(("127.0.0.1", 0), _Empty)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(mcp.CustomSourceError):
            asyncio.run(auto.autoconfigure(
                _cfg(f"http://127.0.0.1:{srv.server_port}/mcp"), "kw", "B0"))
    finally:
        srv.shutdown(); srv.server_close()


def test_autoconfig_route_does_not_echo_the_secret(server, monkeypatch):
    """自动配置的响应体里不能带明文密钥 —— 列表接口摘了，这条路径也得摘。"""
    import asyncio as _asyncio

    import app.routers.data_sources as route

    registry.save({
        "id": "auto", "name": "自动源", "url": server, "surfaces": [],
        "auth": {"mode": "query", "name": "key", "value": "super-secret"},
        "capabilities": {},
    })
    body = route.AutoBody(source={"id": "auto", "name": "自动源", "url": server,
                                  "auth": {"mode": "query", "name": "key", "value": ""}},
                          sample_keyword="wireless earbuds", sample_asin="B08N5WRWNW")
    out = _asyncio.run(route.autoconfig_source(body, _u="test"))
    assert out["source"]["auth"]["value"] == ""
    assert out["source"]["auth"]["value_set"] is True
    assert "super-secret" not in json.dumps(out, ensure_ascii=False)


# ── 鉴权自动探测 ─────────────────────────────────────────────────────────────

class _AuthHandler(BaseHTTPRequestHandler):
    """只认 ``?secret-key=right`` 的服务器，且 tools/list **不鉴权**。

    工具清单不鉴权是真实世界的常态，也正是"能列出工具 ≠ 密钥有效"这条坑的来源。
    """

    def log_message(self, *a):
        return

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) or b"{}")
        method = body.get("method")
        if method == "initialize":
            return self._sse({"protocolVersion": "2024-11-05"})
        if method == "tools/list":
            return self._sse({"tools": [TOOLS[0], TOOLS[2]]})     # 不看密钥，照列
        if method != "tools/call":
            return self._sse({})
        if "secret-key=right" not in self.path:
            return self._sse({"isError": True,
                              "content": [{"type": "text", "text": "unauthorized"}]})
        name = (body.get("params") or {}).get("name")
        data = ITEM if name == "item_lookup" else TERM
        return self._sse({"content": [{"type": "text",
                                       "text": json.dumps({"code": "OK", "data": data})}]})

    def _sse(self, result):
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})
        raw = f"event: message\ndata: {payload}\n\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture()
def auth_server():
    srv = HTTPServer(("127.0.0.1", 0), _AuthHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/mcp"
    srv.shutdown()
    srv.server_close()


def _auth_cfg(url: str, key: str) -> dict:
    return registry.validate({
        "id": "auth", "name": "鉴权源", "url": url,
        "auth": {"mode": "auto", "name": "", "value": key},
        "capabilities": {},
    })


def test_auth_is_detected_without_asking_the_user(auth_server):
    """用户只填密钥，不选"鉴权方式"、不填"参数名" —— 那是机器试几次就知道的事。"""
    out = asyncio.run(auto.autoconfigure(
        _auth_cfg(auth_server, "right"), "wireless earbuds", "B08N5WRWNW"))
    assert out["source"]["auth"]["mode"] == "query"
    assert out["source"]["auth"]["name"] == "secret-key"
    assert out["report"]["auth"] == "URL 参数 secret-key"
    assert out["report"]["ok"]


def test_wrong_key_is_not_mistaken_for_a_working_auth(auth_server):
    """密钥错了必须说密钥错，不能因为 tools/list 能列就当配好了。"""
    from app.services import custom_source_mcp as mcp
    with pytest.raises(mcp.CustomSourceError) as exc:
        asyncio.run(auto.autoconfigure(
            _auth_cfg(auth_server, "wrong"), "wireless earbuds", "B08N5WRWNW"))
    assert "密钥" in str(exc.value)


def test_no_key_means_no_auth(server):
    out = asyncio.run(auto.autoconfigure(
        _auth_cfg(server, ""), "wireless earbuds", "B08N5WRWNW"))
    assert out["source"]["auth"]["mode"] == "none"


# ── 没配上时，说清是哪一种"没有" ─────────────────────────────────────────────

class _VagueHandler(BaseHTTPRequestHandler):
    """工具**有**，参数也填得上，但名字和描述里没有任何认得出的线索。

    这正是"它有、我没认出来"那一种 —— 报成"没有找到合适的工具"会让人以为
    这台服务器不支持，直接放弃。
    """

    def log_message(self, *a):
        return

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) or b"{}")
        method = body.get("method")
        if method == "initialize":
            return self._sse({"protocolVersion": "2024-11-05"})
        if method == "tools/list":
            return self._sse({"tools": [
                {"name": "f_001", "description": "接口一",
                 "inputSchema": {"type": "object", "required": ["asin"],
                                 "properties": {"asin": {}, "site": {}}}},
                {"name": "f_002", "description": "接口二",
                 "inputSchema": {"type": "object", "required": ["query"],
                                 "properties": {"query": {}, "site": {}}}},
                {"name": "f_003", "description": "接口三",
                 "inputSchema": {"type": "object", "required": ["mysteryParam"],
                                 "properties": {"mysteryParam": {}}}},
            ]})
        if method != "tools/call":
            return self._sse({})
        name = (body.get("params") or {}).get("name")
        data = ITEM if name == "f_001" else TERM
        return self._sse({"content": [{"type": "text",
                                       "text": json.dumps({"code": "OK", "data": data})}]})

    def _sse(self, result):
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})
        raw = f"event: message\ndata: {payload}\n\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture()
def vague_server():
    srv = HTTPServer(("127.0.0.1", 0), _VagueHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/mcp"
    srv.shutdown()
    srv.server_close()


def _by_id(report, cap_id):
    return next(c for c in report["capabilities"] if c["id"] == cap_id)


def test_unrecognized_tools_are_offered_not_denied(vague_server):
    """认不出 ≠ 没有。必须把工具名端出来让用户自己挑。"""
    out = asyncio.run(auto.autoconfigure(_cfg(vague_server), "wireless earbuds", "B08N5WRWNW"))
    item = _by_id(out["report"], "home_asin_pulse")
    assert item["ok"] is False
    assert item["reason"] == "not_recognized"
    assert "认不出哪个" in item["error"]
    assert [c["tool"] for c in item["candidates"]] == ["f_001"]
    assert item["candidates"][0]["args"] == {"asin": "{asin}", "site": "{marketplace}"}


def test_reason_distinguishes_missing_tool_from_unfillable(vague_server, server):
    """三种"没有"要能分开：真没有 / 参数认不出 / 名字认不出。"""
    vague = _by_id(asyncio.run(auto.autoconfigure(
        _cfg(vague_server), "kw", "B0"))["report"], "home_asin_pulse")
    assert vague["reason"] == "not_recognized"

    # 正常那台服务器上，每项能力要么配成了，要么给得出理由
    full = asyncio.run(auto.autoconfigure(_cfg(server), "wireless earbuds", "B08N5WRWNW"))
    for item in full["report"]["capabilities"]:
        if not item["ok"]:
            assert item["reason"] in ("no_tool", "not_recognized", "unfillable",
                                      "no_data", "call_failed")
            assert item["error"]


def test_failed_call_reports_which_tool(server, monkeypatch):
    """调用失败要说是哪个工具失败的，不然没法排查。"""
    from app.services import custom_source_mcp as mcp
    real = mcp.call_tool

    async def flaky(state, tool, args):
        if tool == "item_lookup":
            raise mcp.CustomSourceError("quota exceeded")
        return await real(state, tool, args)

    monkeypatch.setattr(mcp, "call_tool", flaky)
    out = asyncio.run(auto.autoconfigure(_cfg(server), "wireless earbuds", "B08N5WRWNW"))
    item = _by_id(out["report"], "home_asin_pulse")
    assert item["ok"] is False
    assert "item_lookup" in item["error"] and "quota exceeded" in item["error"]


# ── 指定工具重新推断 ─────────────────────────────────────────────────────────

def test_remap_infers_mapping_for_a_hand_picked_tool(vague_server):
    """用户只指工具名，字段映射仍然由系统推断 —— 不该退回手填路径。"""
    out = asyncio.run(auto.remap_capability(
        _cfg(vague_server), "home_asin_pulse", "f_001", "wireless earbuds", "B08N5WRWNW"))
    assert out["ok"] is True
    assert out["spec"]["tool"] == "f_001"
    assert out["spec"]["fields"]["title"] == "productName"
    assert out["spec"]["fields"]["price"] == "sellPrice"
    assert out["matched"] >= 8


def test_remap_rejects_unknown_tool(vague_server):
    from app.services import custom_source_mcp as mcp
    with pytest.raises(mcp.CustomSourceError) as exc:
        asyncio.run(auto.remap_capability(
            _cfg(vague_server), "home_asin_pulse", "nope", "kw", "B0"))
    assert "没有名为" in str(exc.value)


def test_remap_explains_unfillable_params(vague_server):
    from app.services import custom_source_mcp as mcp
    with pytest.raises(mcp.CustomSourceError) as exc:
        asyncio.run(auto.remap_capability(
            _cfg(vague_server), "home_asin_pulse", "f_003", "kw", "B0"))
    assert "mysteryParam" in str(exc.value)


def test_remap_reports_when_the_picked_tool_has_no_usable_fields(vague_server):
    """指了个不对的工具要如实说，不能生成一份全空的映射假装配好了。"""
    out = asyncio.run(auto.remap_capability(
        _cfg(vague_server), "home_asin_pulse", "f_002", "wireless earbuds", "B08N5WRWNW"))
    assert out["ok"] is False and out["error"]
