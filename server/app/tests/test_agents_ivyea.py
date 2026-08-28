"""/agents 的 ivyea 驱动：假 ivyea CLI emit Claude-aligned NDJSON → 归一消息。"""
from __future__ import annotations

import importlib
import stat
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_ORIGIN = "http://testserver"


def _fake_ivyea(tmp_path: Path) -> Path:
    """假 ivyea：emit init → assistant(tool_use) → user(tool_result) → assistant(text) → result。"""
    p = tmp_path / "fake_ivyea.py"
    p.write_text(
        "import json, sys\n"
        "def emit(d): print(json.dumps(d), flush=True)\n"
        "sid = 'ivyea-sess-001'\n"
        "emit({'type':'system','subtype':'init','session_id':sid,'model':'deepseek-chat',"
        "  'tools':['run_patrol','read_file'],'permissionMode':'default'})\n"
        "emit({'type':'assistant','session_id':sid,'message':{'role':'assistant','content':["
        "  {'type':'tool_use','id':'c1','name':'run_patrol','input':{'asin':'B0X'}}]}})\n"
        "emit({'type':'user','session_id':sid,'message':{'role':'user','content':["
        "  {'type':'tool_result','tool_use_id':'c1','content':'巡检完成','is_error':False}]}})\n"
        "emit({'type':'assistant','session_id':sid,'message':{'role':'assistant','content':["
        "  {'type':'text','text':'Hello from fake ivyea'}]}})\n"
        "emit({'type':'result','subtype':'success','session_id':sid,'is_error':False,"
        "  'result':'Hello from fake ivyea','total_cost_cny':0.01,"
        "  'usage':{'input_tokens':100,'output_tokens':20}})\n",
        encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("IVYEA_OPS_SECRET", "test-secret")
    monkeypatch.setenv("IVYEA_OPS_ALLOWED_ORIGINS", _ORIGIN)
    monkeypatch.setenv("AGENTS_DB_PATH", str(tmp_path / "agents.db"))
    from app.core import config as cfg_mod
    importlib.reload(cfg_mod)
    from app.core import security as sec_mod
    importlib.reload(sec_mod)
    from app.agents import db as db_mod
    importlib.reload(db_mod); db_mod.init_db()
    from app.agents import ivyea_driver as iv_mod
    importlib.reload(iv_mod)
    from app.agents import ws as ws_mod
    importlib.reload(ws_mod)
    from app.agents import router as router_mod
    importlib.reload(router_mod)
    from app import main as main_mod
    importlib.reload(main_mod)
    cookie = sec_mod.issue_session("admin", "admin")
    client = TestClient(main_mod.app)
    client.cookies.set(cfg_mod.settings.session_cookie_name, cookie)
    return client, iv_mod


def _patch_fake(monkeypatch, iv_mod, fake: Path):
    real = iv_mod._build_argv

    def _argv(command, options, *rest):
        # *rest 收住 stdin_channel 这类后加的参数：桩不该因为真实签名多一个开关就把
        # 整条链路弄成 TypeError（那会表现成"测试莫名其妙挂住"，查半天）。
        argv = real(command, options, *rest)
        return [sys.executable, str(fake)] + argv[1:]
    monkeypatch.setattr(iv_mod, "_build_argv", _argv)


def test_ivyea_command_streams_to_complete(env, tmp_path, monkeypatch):
    client, iv_mod = env
    _patch_fake(monkeypatch, iv_mod, _fake_ivyea(tmp_path))
    with client.websocket_connect("/api/agents/ws") as ws:
        ws.send_json({"type": "ivyea-command", "command": "看下广告", "options": {}})
        kinds, texts, new_sid = [], [], None
        tool_use = tool_result = budget = None
        for _ in range(20):
            msg = ws.receive_json()
            kinds.append(msg.get("kind") or msg.get("type"))
            if msg.get("kind") == "session_created":
                new_sid = msg.get("newSessionId")
            if msg.get("kind") == "text":
                texts.append(msg.get("content"))
            if msg.get("kind") == "tool_use":
                tool_use = msg
            if msg.get("kind") == "tool_result":
                tool_result = msg
            if msg.get("kind") == "status":
                budget = msg.get("tokenBudget")
            if msg.get("kind") == "complete":
                break
        assert new_sid == "ivyea-sess-001"
        assert "Hello from fake ivyea" in texts
        assert tool_use and tool_use["toolName"] == "run_patrol" and tool_use["toolId"] == "c1"
        assert tool_result and tool_result["toolId"] == "c1" and "巡检完成" in tool_result["content"]
        assert budget and budget["inputTokens"] == 100 and budget["outputTokens"] == 20
        assert "complete" in kinds


def test_ivyea_messages_carry_ivyea_provider(env, tmp_path, monkeypatch):
    client, iv_mod = env
    _patch_fake(monkeypatch, iv_mod, _fake_ivyea(tmp_path))
    with client.websocket_connect("/api/agents/ws") as ws:
        ws.send_json({"type": "ivyea-command", "command": "hi", "options": {}})
        providers = set()
        for _ in range(20):
            msg = ws.receive_json()
            if msg.get("provider"):
                providers.add(msg["provider"])
            if msg.get("kind") == "complete":
                break
        assert providers == {"ivyea"}


def test_ivyea_resume_and_permission_argv():
    from app.agents import ivyea_driver as iv
    argv = iv._build_argv("查一下", {"sessionId": "sid-9", "permissionMode": "bypassPermissions"})
    assert "--resume" in argv and "sid-9" in argv
    assert "--approve-all" in argv
    assert "--output-format" in argv and "stream-json" in argv
    argv2 = iv._build_argv("查一下", {})
    assert "--permission-mode" in argv2 and "policy" in argv2   # 默认走 policy 档
    assert "--resume" not in argv2


def test_ivyea_missing_binary_reports_error(env, monkeypatch):
    client, iv_mod = env
    monkeypatch.setattr(iv_mod, "_ivyea_bin", lambda: "/no/such/ivyea-bin")
    with client.websocket_connect("/api/agents/ws") as ws:
        ws.send_json({"type": "ivyea-command", "command": "hi", "options": {}})
        msg = ws.receive_json()
        assert msg.get("kind") == "error" and "not installed" in (msg.get("content") or "")


def test_ivyea_argv_opens_the_stdin_channel_only_when_supported():
    """输入通道是**探到了才给**：老 agent 收到 `--input-format` 会直接报参数错。"""
    from app.agents import ivyea_driver as iv
    assert "--input-format" not in iv._build_argv("hi", {})
    argv = iv._build_argv("hi", {}, True)
    assert argv[argv.index("--input-format") + 1] == "stream-json"


def test_ivyea_inject_says_no_when_nothing_is_running():
    """没有活轮就明说不收 —— 调用方据此把这句话当成下一轮发出去。"""
    import asyncio
    from app.agents import ivyea_driver as iv
    out = asyncio.get_event_loop().run_until_complete(iv.inject("nobody", "补一句"))
    assert out == {"ok": True, "accepted": False, "reason": "no_live_turn"}


def test_ivyea_inject_refuses_when_the_agent_has_no_input_channel():
    """老 agent（stdin 关着）也要明说，不能假装送到了。"""
    import asyncio
    from app.agents import ivyea_driver as iv
    iv._active_sessions["s-old"] = {"proc": object(), "status": "active", "stdin_channel": False}
    try:
        out = asyncio.get_event_loop().run_until_complete(iv.inject("s-old", "补一句"))
        assert out["accepted"] is False and out["reason"] == "no_input_channel"
    finally:
        iv._active_sessions.pop("s-old", None)


def test_ivyea_question_resolution_only_answers_known_cards():
    from app.agents import ivyea_driver as iv
    assert iv.resolve_question("no-such-card", {"q": "a"}) is False
