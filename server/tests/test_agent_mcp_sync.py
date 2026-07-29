"""IvyeaOps provisions IvyeaAgent's MCP registry (~/.ivyea/mcp.json).

Without this a fresh clone has the data-source keys in System Settings and an
agent with zero MCP servers. Every test points IVYEA_HOME at a tmp dir — the
real ~/.ivyea must never be touched.
"""
from __future__ import annotations

import json

from app.services import hermes_config_sync as sync


def _mcp(tmp_path):
    return json.loads((tmp_path / "mcp.json").read_text("utf-8"))["mcpServers"]


def test_sorftime_key_lands_in_agent_registry(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IVYEA_HOME", str(tmp_path))
    sync.sync_agent_mcp({"sorftime_key": "abc123"})

    entry = _mcp(tmp_path)["sorftime"]
    assert entry["url"] == "https://mcp.sorftime.com?key=abc123"
    assert entry["transport"] == "http"
    assert entry["trusted"] is True


def test_rewriting_a_key_replaces_the_query_param(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IVYEA_HOME", str(tmp_path))
    sync.sync_agent_mcp({"sorftime_key": "old"})
    sync.sync_agent_mcp({"sorftime_key": "new"})
    assert _mcp(tmp_path)["sorftime"]["url"] == "https://mcp.sorftime.com?key=new"


def test_sif_and_sellersprite_entries(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IVYEA_HOME", str(tmp_path))
    sync.sync_agent_mcp({"sif_key": "sif-key", "sellersprite_key": "sp-key"})

    servers = _mcp(tmp_path)
    assert servers["sif_mcp"]["headers"]["Authorization"] == "Bearer sif-key"
    assert servers["sellersprite"]["transport"] == "stdio"
    assert servers["sellersprite"]["env"] == {"SELLERSPRITE_KEY": "sp-key"}


def test_an_empty_key_never_deletes_an_existing_server(tmp_path, monkeypatch) -> None:
    """Boot replays the *full* settings dict, so an unset key looks exactly like
    a cleared one — deleting on empty wiped a hand-configured sif_mcp entry."""
    monkeypatch.setenv("IVYEA_HOME", str(tmp_path))
    sync.sync_agent_mcp({"sorftime_key": "abc", "sif_key": "sif"})
    sync.sync_agent_mcp({"sorftime_key": "abc", "sif_key": "", "sellersprite_key": ""})

    servers = _mcp(tmp_path)
    assert servers["sif_mcp"]["headers"]["Authorization"] == "Bearer sif"
    assert servers["sorftime"]["url"].endswith("key=abc")


def test_user_added_servers_are_left_alone(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IVYEA_HOME", str(tmp_path))
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {
        "lingxing": {"transport": "http", "url": "https://mcp.lingxing.test", "trusted": True},
    }}), "utf-8")

    sync.sync_agent_mcp({"sorftime_key": "abc"})

    servers = _mcp(tmp_path)
    assert servers["lingxing"]["url"] == "https://mcp.lingxing.test"
    assert "sorftime" in servers


def test_untouched_when_no_data_source_key_is_in_the_update(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IVYEA_HOME", str(tmp_path))
    sync.sync_agent_mcp({"hermes_model": "deepseek-chat"})
    assert not (tmp_path / "mcp.json").exists()
