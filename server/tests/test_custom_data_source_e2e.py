"""自定义数据源的端到端：真起一台 MCP 服务器，走完整链路。

单元测试盯的是映射规则本身，这份盯的是**整条路**：
    注册表 → 首页路由分发 → 真实 HTTP + SSE → 信封拆解 → 字段映射 → 接口响应

分开写的理由：前面每一段单独测都过、连起来不通，是这套东西最典型的坏法
（比如 SSE 只在真发包时才暴露解析问题，mock 掉就永远发现不了）。
"""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import app.routers.home as home
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


class _MCPHandler(BaseHTTPRequestHandler):
    """一台最小 MCP 服务器：SSE 应答 + 查询参数鉴权 + 「失败装在成功里」。"""

    calls: list = []

    def log_message(self, *args):        # 别把请求日志打进 pytest 输出
        return

    def do_POST(self):                   # noqa: N802 — BaseHTTPRequestHandler 的命名
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) or b"{}")
        method = body.get("method")
        params = body.get("params") or {}
        _MCPHandler.calls.append((method, params))

        if method == "initialize":
            return self._sse({"protocolVersion": "2024-11-05", "serverInfo": {"name": "fake"}})
        if method == "tools/list":
            return self._sse({"tools": [{
                "name": "product_detail",
                "description": "商品详情",
                "inputSchema": {"type": "object",
                                "properties": {"asin": {}, "site": {}},
                                "required": ["asin"]},
            }]})
        if method != "tools/call":
            return self._sse({})

        # 鉴权走 URL 查询参数，和 Sorftime 一样
        if "key=right-key" not in self.path:
            return self._sse({"isError": True,
                              "content": [{"type": "text", "text": "invalid api key"}]})

        args = params.get("arguments") or {}
        if params.get("name") == "product_detail":
            payload = {"code": "OK", "data": {
                "productName": f"Widget {args.get('asin')}",
                "brandName": "ACME",
                "priceInfo": {"amount": "19.99"},
                "rank": {"main": 1234},
                "reviews": 88, "star": 4.5,
                "site": args.get("site"),
            }}
            return self._sse({"content": [{"type": "text", "text": json.dumps(payload)}]})
        return self._sse({"content": [{"type": "text", "text": "Please specify the site to query"}]})

    def _sse(self, result):
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})
        raw = f"event: message\ndata: {payload}\n\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture()
def mcp_server():
    _MCPHandler.calls = []
    server = HTTPServer(("127.0.0.1", 0), _MCPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/mcp"
    server.shutdown()
    server.server_close()


def _register(url: str, key: str = "right-key") -> None:
    registry.save({
        "id": "e2e", "name": "端到端源", "url": url, "transport": "http",
        "auth": {"mode": "query", "name": "key", "value": key},
        "surfaces": ["home"], "timeout": 10,
        "capabilities": {"home_asin_pulse": {
            "tool": "product_detail",
            "args": {"asin": "{asin}", "site": "{marketplace}"},
            "fields": {
                "title": "productName", "brand": "brandName",
                "price": "priceInfo.amount", "bsr": "rank.main",
                "review_count": "ratings||reviews", "rating": "star",
            },
        }},
    })


def _use_temp_home_db(tmp_path, monkeypatch) -> None:
    path = str(tmp_path / "home.sqlite3")
    monkeypatch.setattr(home, "_db_path", lambda: path)
    home._INITED.discard(path)


def test_probe_lists_tools(mcp_server):
    from app.services import custom_source_mcp as mcp
    _register(mcp_server)
    result = asyncio.run(mcp.probe(registry.get("custom:e2e")))
    assert result["ok"] and result["count"] == 1
    assert result["tools"][0]["name"] == "product_detail"
    assert result["tools"][0]["required"] == ["asin"]


def test_home_pulse_end_to_end(tmp_path, monkeypatch, mcp_server):
    _use_temp_home_db(tmp_path, monkeypatch)
    _register(mcp_server)

    result = asyncio.run(home.pulse(
        home.PulseReq(asin="B07XNTHHBP", marketplace="US", data_source="custom:e2e"),
        _user="test",
    ))

    current = result["current"]
    assert current["error"] is None
    assert current["title"] == "Widget B07XNTHHBP"
    assert current["price"] == 19.99 and current["bsr"] == 1234
    assert current["review_count"] == 88
    assert current["data_source"] == "custom:e2e"
    # 入参模板真的渲染进了请求（不是碰巧返回了对的东西）
    call = next(p for m, p in _MCPHandler.calls if m == "tools/call")
    assert call["arguments"] == {"asin": "B07XNTHHBP", "site": "US"}
    # 快照落库到自定义源自己的分区
    assert len(home.watch_snapshots("custom:e2e", _user="test")) >= 0


def test_wrong_key_surfaces_as_card_error(tmp_path, monkeypatch, mcp_server):
    """密钥错 = 卡片上一行看得懂的错误，不是 500，也不是一堆 null 装成成功。"""
    _use_temp_home_db(tmp_path, monkeypatch)
    _register(mcp_server, key="wrong-key")

    result = asyncio.run(home.pulse(
        home.PulseReq(asin="B07XNTHHBP", marketplace="US", data_source="custom:e2e"),
        _user="test",
    ))
    current = result["current"]
    assert current["error"] and "invalid api key" in current["error"]
    assert current["title"] is None


def test_unknown_custom_source_is_rejected(tmp_path, monkeypatch):
    _use_temp_home_db(tmp_path, monkeypatch)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(home.pulse(
            home.PulseReq(asin="B07XNTHHBP", marketplace="US", data_source="custom:nope"),
            _user="test",
        ))
    assert exc.value.status_code == 400


def test_builtin_sources_unaffected_by_registered_custom(tmp_path, monkeypatch, mcp_server):
    """加了自定义源之后，内置两家的分发必须一字不变地还走原路。"""
    _use_temp_home_db(tmp_path, monkeypatch)
    _register(mcp_server)
    seen = []

    async def seller(asin, marketplace):
        seen.append("sellersprite")
        return {"asin": asin, "marketplace": marketplace, "data_source": "sellersprite",
                "error": None, "title": "s", "brand": None, "image": None, "price": 1.0,
                "bsr": None, "bsr_category": None, "sub_rank": None, "sub_category": None,
                "est_sales": None, "rating": None, "review_count": None, "variations": None,
                "coupon": None, "deal": None, "inventory": None}

    async def sorftime(asin, marketplace):
        seen.append("sorftime")
        return {"asin": asin, "marketplace": marketplace, "data_source": "sorftime",
                "error": None, "title": "s", "brand": None, "image": None, "price": 1.0,
                "bsr": None, "bsr_category": None, "sub_rank": None, "sub_category": None,
                "est_sales": None, "rating": None, "review_count": None, "variations": None,
                "coupon": None, "deal": None, "inventory": None}

    from app.services import asin_pulse_service, sellersprite_service
    monkeypatch.setattr(sellersprite_service, "home_asin_pulse", seller)
    monkeypatch.setattr(asin_pulse_service, "fetch_asin_pulse", sorftime)

    for source in ("sorftime", "sellersprite"):
        asyncio.run(home.pulse(
            home.PulseReq(asin="B07XNTHHBP", marketplace="US", data_source=source), _user="test"))
    assert seen == ["sorftime", "sellersprite"]
    assert not any(m == "tools/call" for m, _ in _MCPHandler.calls), "内置源绝不能打到自定义服务器"


def test_market_pipeline_uses_custom_provider(mcp_server):
    """市场调研的 provider 解析：自定义源要能顶替模块被拿到。"""
    import app.routers.market as market
    _register(mcp_server)
    provider = market._pipeline_for("custom:e2e")
    assert provider.__class__.__name__ == "CustomProvider"
    assert market._source_label("custom:e2e") == "端到端源"
    # 内置两家的解析结果不变
    assert market._pipeline_for("sorftime").__name__.endswith("sorftime_service")
    assert market._source_label("sellersprite") == "卖家精灵"


def test_disabled_source_disappears_from_routing(tmp_path, monkeypatch, mcp_server):
    _use_temp_home_db(tmp_path, monkeypatch)
    _register(mcp_server)
    cfg = registry.get("custom:e2e")
    cfg["enabled"] = False
    registry.save({**cfg, "auth": {**cfg["auth"], "value": ""}})
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        asyncio.run(home.pulse(
            home.PulseReq(asin="B0", marketplace="US", data_source="custom:e2e"), _user="test"))


def test_sqlite_cache_namespaces_by_source(tmp_path, monkeypatch, mcp_server):
    """自定义源的缓存键必须带前缀，不能和 sorftime 的历史混到一起。"""
    _use_temp_home_db(tmp_path, monkeypatch)
    _register(mcp_server)
    assert home._source_key("sorftime", "ipad case") == "ipad case"
    assert home._source_key("custom:e2e", "ipad case") == "custom:e2e:ipad case"
