"""agent 的 MCP 注册表（~/.ivyea/mcp.json）读写。

这份文件同时被三方碰：用户的 `ivyea mcp add`、IvyeaOps 的数据源密钥同步
（hermes_config_sync.sync_agent_mcp）、以及现在的能力市场页面。所以"合并而非替换"
不是风格问题，是数据安全问题 —— 谁都不能把别人写的条目抹掉。
"""
from __future__ import annotations

import json

import pytest

from app.services import agent_mcp


@pytest.fixture()
def mcp_file(tmp_path, monkeypatch):
    path = tmp_path / "mcp.json"
    monkeypatch.setattr(agent_mcp, "_agent_mcp_file", lambda: path)
    return path


def _read(path):
    return json.loads(path.read_text("utf-8"))["mcpServers"]


def test_add_http_server(mcp_file):
    agent_mcp.upsert_server("demo", {"transport": "http", "url": "https://x/mcp", "trusted": True})
    assert _read(mcp_file)["demo"] == {
        "transport": "http", "url": "https://x/mcp", "trusted": True}


def test_partial_update_keeps_the_rest(mcp_file):
    """只改 trusted 时不该被"缺 url"打回，也不该把 url 弄丢。

    必填校验必须对着合并后的结果做 —— 这正是第一版写错的地方：对着"这次传了
    什么"校验，导致局部更新永远失败，和承诺的合并语义自相矛盾。
    """
    agent_mcp.upsert_server("demo", {"transport": "http", "url": "https://x/mcp"})
    agent_mcp.upsert_server("demo", {"transport": "http", "trusted": True})
    row = _read(mcp_file)["demo"]
    assert row["url"] == "https://x/mcp"
    assert row["trusted"] is True


def test_user_added_entry_survives_other_writes(mcp_file):
    """用户手工加的条目，不能被后来的写入顺手抹掉。"""
    mcp_file.write_text(json.dumps({"mcpServers": {
        "mine": {"transport": "stdio", "command": "/usr/bin/my-server", "dataSource": {"tool": "x"}},
    }}), encoding="utf-8")
    agent_mcp.upsert_server("other", {"transport": "http", "url": "https://y/mcp"})
    servers = _read(mcp_file)
    assert set(servers) == {"mine", "other"}
    assert servers["mine"]["dataSource"] == {"tool": "x"}      # 附带配置也没丢


def test_switching_transport_clears_stale_fields(mcp_file):
    """http → stdio 之后不该留着一个已经没用的 url 误导人。"""
    agent_mcp.upsert_server("demo", {"transport": "http", "url": "https://x/mcp"})
    agent_mcp.upsert_server("demo", {"transport": "stdio", "command": "/bin/echo", "args": ["a"]})
    row = _read(mcp_file)["demo"]
    assert "url" not in row
    assert row["command"] == "/bin/echo" and row["args"] == ["a"]


def test_missing_required_fields_rejected(mcp_file):
    with pytest.raises(agent_mcp.AgentMCPError):
        agent_mcp.upsert_server("demo", {"transport": "stdio"})
    with pytest.raises(agent_mcp.AgentMCPError):
        agent_mcp.upsert_server("demo", {"transport": "http"})
    with pytest.raises(agent_mcp.AgentMCPError):
        agent_mcp.upsert_server("demo", {"transport": "ftp", "url": "u"})


def test_bad_names_rejected(mcp_file):
    for bad in ("", "  ", "a b", "../etc", "x;rm -rf /"):
        with pytest.raises(agent_mcp.AgentMCPError):
            agent_mcp.upsert_server(bad, {"transport": "http", "url": "https://x"})


def test_listing_redacts_secrets(mcp_file):
    """密钥不回给前端：URL 里的 key、Authorization 头、env 值都要挡掉。"""
    mcp_file.write_text(json.dumps({"mcpServers": {
        "a": {"transport": "http", "url": "https://x/mcp?key=SECRET"},
        "b": {"transport": "http", "url": "https://y", "headers": {"Authorization": "Bearer SECRET"}},
        "c": {"transport": "stdio", "command": "s", "env": {"TOKEN": "SECRET"}},
    }}), encoding="utf-8")
    rows = {r["name"]: r for r in agent_mcp.list_servers()}
    dumped = json.dumps(rows, ensure_ascii=False)
    assert "SECRET" not in dumped
    assert rows["a"]["spec"]["url"].endswith("key=***")
    assert rows["b"]["spec"]["headers"]["Authorization"] == "***"
    assert rows["c"]["spec"]["env"]["TOKEN"] == "***"
    # 但磁盘上必须仍是真值，否则 agent 就连不上了
    assert "SECRET" in mcp_file.read_text("utf-8")


def test_managed_servers_are_flagged(mcp_file):
    """由数据源密钥自动同步的那几台要标出来 —— 删了下次保存设置又会回来，
    UI 不说清楚用户会以为删除失败。"""
    agent_mcp.upsert_server("sorftime", {"transport": "http", "url": "https://s"})
    agent_mcp.upsert_server("mine", {"transport": "http", "url": "https://m"})
    rows = {r["name"]: r for r in agent_mcp.list_servers()}
    assert rows["sorftime"]["managed"] is True
    assert rows["mine"]["managed"] is False


def test_remove(mcp_file):
    agent_mcp.upsert_server("demo", {"transport": "http", "url": "https://x"})
    assert agent_mcp.remove_server("demo") is True
    assert agent_mcp.remove_server("demo") is False
    assert _read(mcp_file) == {}


def test_corrupt_file_does_not_explode(mcp_file):
    """配置文件坏掉时按空处理，别让整页 500。"""
    mcp_file.write_text("{ not json", encoding="utf-8")
    assert agent_mcp.list_servers() == []
    agent_mcp.upsert_server("demo", {"transport": "http", "url": "https://x"})
    assert set(_read(mcp_file)) == {"demo"}


def test_sync_agent_mcp_still_preserves_manual_entries(tmp_path, monkeypatch):
    """回归：数据源同步（每次保存设置和每次启动都会重放）不能冲掉手工条目。"""
    from app.services import hermes_config_sync

    path = tmp_path / "mcp.json"
    monkeypatch.setattr(hermes_config_sync, "_agent_mcp_file", lambda: path)
    path.write_text(json.dumps({"mcpServers": {
        "mine": {"transport": "http", "url": "https://mine"},
    }}), encoding="utf-8")

    hermes_config_sync.sync_agent_mcp({"sorftime_key": "k1"})
    servers = json.loads(path.read_text("utf-8"))["mcpServers"]
    assert "mine" in servers and servers["mine"]["url"] == "https://mine"
    assert servers["sorftime"]["trusted"] is True
