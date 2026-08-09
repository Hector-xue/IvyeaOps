"""对外 MCP 的边界测试。

这一组盯的是**权限边界**，不是功能是否好用：一个签错的令牌能不能读到东西、
一个只读令牌能不能列出写工具、撤销之后是不是真的立刻失效。
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("IVYEA_OPS_DATA_DIR", str(tmp_path))
    from app.core import config
    importlib.reload(config)
    from app.services import mcp_tokens
    importlib.reload(mcp_tokens)
    mcp_tokens.init_db()

    from fastapi import FastAPI
    from app.routers import mcp_server
    importlib.reload(mcp_server)
    app = FastAPI()
    app.include_router(mcp_server.router, prefix="/api/mcp")
    return TestClient(app), mcp_tokens, mcp_server


def _rpc(client, method, *, token="", params=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/api/mcp", headers=headers,
                       json={"jsonrpc": "2.0", "id": 1,
                             "method": method, "params": params or {}})


def test_no_token_is_rejected(api):
    client, _, _ = api
    assert _rpc(client, "tools/list").status_code == 401


def test_garbage_token_is_rejected(api):
    client, _, _ = api
    assert _rpc(client, "tools/list", token="ivmcp_nope").status_code == 401
    assert _rpc(client, "tools/list", token="not-even-prefixed").status_code == 401


def test_read_token_lists_only_read_tools(api):
    client, tokens, mcp = api
    t = tokens.issue("读令牌")["token"]
    r = _rpc(client, "tools/list", token=t)
    assert r.status_code == 200
    names = {x["name"] for x in r.json()["result"]["tools"]}
    read_only = {n for n, s in mcp.TOOLS.items() if s["scope"] == "read"}
    assert names == read_only


def test_write_scope_is_not_granted_by_default(api):
    """默认发出去的令牌不带 write —— 这条一旦松掉，一个用来做分析的令牌
    就顺带具备了改人家真实广告投放的能力。"""
    _, tokens, _ = api
    row = tokens.issue("默认令牌")
    assert row["scopes"] == "read"
    assert tokens.verify(row["token"], need="write") is None
    assert tokens.verify(row["token"], need="read") is not None


def test_unknown_scope_is_dropped_not_honored(api):
    _, tokens, _ = api
    row = tokens.issue("乱写", scopes=["admin", "root"])
    assert row["scopes"] == "read"      # 认不出的 scope 退回 read，不是全给


def test_revoked_token_stops_working_immediately(api):
    client, tokens, _ = api
    row = tokens.issue("待撤销")
    assert _rpc(client, "tools/list", token=row["token"]).status_code == 200
    assert tokens.revoke(row["id"]) is True
    assert _rpc(client, "tools/list", token=row["token"]).status_code == 401


def test_expired_token_stops_working(api):
    _, tokens, _ = api
    row = tokens.issue("短命", ttl_days=1)
    assert tokens.verify(row["token"]) is not None
    # 直接把过期时间改到过去，比 mock 掉 time.time 可靠：verify 读的是库里的值。
    import sqlite3
    conn = sqlite3.connect(str(tokens._db()))
    conn.execute("UPDATE mcp_tokens SET expires_at = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    assert tokens.verify(row["token"]) is None


def test_plaintext_token_is_never_stored(api):
    """库里只存哈希。被拖库也拿不到能用的令牌。"""
    _, tokens, _ = api
    row = tokens.issue("检查存储")
    import sqlite3
    conn = sqlite3.connect(str(tokens._db()))
    stored = conn.execute("SELECT * FROM mcp_tokens").fetchall()
    conn.close()
    flat = " ".join(str(c) for r in stored for c in r)
    assert row["token"] not in flat


def test_listing_never_leaks_the_hash(api):
    _, tokens, _ = api
    tokens.issue("给界面看的")
    assert all("token_hash" not in row for row in tokens.listing())


def test_calling_a_tool_the_scope_forbids_is_rejected(api):
    """能不能调，看的是**工具自己声明的 scope**，不是入口处放行了就万事大吉。"""
    client, tokens, mcp = api
    t = tokens.issue("只读")["token"]
    mcp.TOOLS["_test_write_tool"] = {
        "description": "假的写工具", "inputSchema": {"type": "object"}, "scope": "write"}
    try:
        r = _rpc(client, "tools/call", token=t,
                 params={"name": "_test_write_tool", "arguments": {}})
        assert r.status_code == 401
    finally:
        mcp.TOOLS.pop("_test_write_tool", None)


def test_unknown_tool_is_404_not_a_silent_success(api):
    client, tokens, _ = api
    t = tokens.issue("只读")["token"]
    r = _rpc(client, "tools/call", token=t,
             params={"name": "rm_rf_slash", "arguments": {}})
    assert r.status_code == 404


def test_health_tool_returns_content(api):
    client, tokens, _ = api
    t = tokens.issue("只读")["token"]
    r = _rpc(client, "tools/call", token=t,
             params={"name": "ivyea_health", "arguments": {}})
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"


def test_no_call_home(api, monkeypatch):
    """整条 MCP 链路不联我们的服务器。这是这个功能存在的**全部理由** ——
    对面的 Agent 之所以敢连，是因为数据没离开用户自己的机器。
    把外联 socket 全掐掉，握手与本地工具必须照常工作。"""
    import socket
    def refuse(*a, **kw):
        raise AssertionError("MCP 链路发起了外部连接")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    client, tokens, _ = api
    t = tokens.issue("离线")["token"]
    assert _rpc(client, "initialize", token=t).status_code == 200
    assert _rpc(client, "tools/list", token=t).status_code == 200
    r = _rpc(client, "tools/call", token=t,
             params={"name": "ivyea_health", "arguments": {}})
    assert r.json()["result"]["isError"] is False


def test_tool_failure_becomes_an_mcp_error_not_a_500(api, monkeypatch):
    """工具炸了要变成 MCP 的错误结果。回 500 的话，对面的 Agent 只会当成
    传输故障反复重试，而不是把失败原因转述给用户。"""
    client, tokens, mcp = api
    t = tokens.issue("只读")["token"]

    async def boom(name, args):
        raise RuntimeError("上游挂了")
    monkeypatch.setattr(mcp, "_call_tool", boom)

    r = _rpc(client, "tools/call", token=t,
             params={"name": "ivyea_health", "arguments": {}})
    assert r.status_code == 200
    assert r.json()["result"]["isError"] is True
    assert "上游挂了" in r.json()["result"]["content"][0]["text"]


def test_rate_limit_kicks_in(api):
    """挡的是失控的 Agent 循环 —— 对面一个写坏的 while 能把这台机器的
    上游 API 配额烧光。"""
    client, tokens, mcp = api
    t = tokens.issue("刷子")["token"]
    codes = {_rpc(client, "ping", token=t).status_code
             for _ in range(mcp._RATE_PER_MIN + 5)}
    assert 429 in codes


def test_rate_limit_is_per_token(api):
    client, tokens, mcp = api
    hot = tokens.issue("刷子")["token"]
    calm = tokens.issue("正常人")["token"]
    for _ in range(mcp._RATE_PER_MIN + 2):
        _rpc(client, "ping", token=hot)
    assert _rpc(client, "ping", token=calm).status_code == 200


def test_last_used_is_tracked(api):
    """界面上要能看出哪个令牌还活着 —— 一个永远列着十个令牌却不知道谁在用的
    列表，等于没有撤销能力。"""
    client, tokens, _ = api
    row = tokens.issue("用一下")
    assert tokens.listing()[0]["last_used_at"] is None
    _rpc(client, "ping", token=row["token"])
    assert tokens.listing()[0]["last_used_at"] is not None
