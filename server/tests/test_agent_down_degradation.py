"""agent 掉线时，工作台该怎么退。

这几条的价值不在"功能"，在**失败的样子**：agent 是整台机器上的单点，它一停，
如果各处只是静默变空或直接白屏，用户会以为是自己的数据没了。所以每一处退化都要
么给出真话，要么退到还能用的通道 —— 而且要说清楚退了。

已用真实停机验过一次（停 daemon + 关自动拉起）：左栏报 agent_available=false、
AI 问答退回老通道并明确提示。这里把结论钉住，以后不必再停生产。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers import ivyea_agent as mod
from app.services import console_sessions as cs


ALICE = {"email": "alice@x.com", "role": "user", "id": 2}


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_db_path", lambda: tmp_path / "console_sessions.sqlite3")
    cs.init_db()
    yield


def _agent_down(monkeypatch):
    def down(fn, *a, **k):
        raise HTTPException(status_code=503, detail="IvyeaAgent 不可用")

    monkeypatch.setattr(mod, "_call", down)


def test_rail_says_agent_is_down_instead_of_pretending_zero(monkeypatch):
    """**这条最要紧**：左栏静默变成"0 条会话"，看着就像会话全没了。

    必须端出 agent_available=false，让前端能说"读不到"而不是"没有"。
    """
    cs.register_session("s1", "alice@x.com")
    _agent_down(monkeypatch)
    out = mod.console_session_list(info=ALICE)
    assert out["ok"] is True
    assert out["agent_available"] is False
    assert out["sessions"] == []
    # 工作区是 ops 自己的数据，agent 挂了也该照常给
    assert out["workspaces"][0]["name"] == cs.DEFAULT_WORKSPACE


def test_paging_fields_stay_sane_when_agent_is_down(monkeypatch):
    """前端拿 has_more 决定要不要显示"加载更多"。agent 挂了还说 has_more，
    就会摆一个点了永远没反应的按钮。"""
    _agent_down(monkeypatch)
    out = mod.console_session_list(limit=5, info=ALICE)
    assert out["total"] == 0 and out["has_more"] is False


def test_stream_refuses_up_front_rather_than_dying_mid_stream(monkeypatch):
    """/chat/stream 一旦开了流就退不回 HTTP 状态码了，所以必须在开流**之前**
    就把 503 抛出来 —— 否则前端只看到一个开了又空掉的流，分不清是"没答"还是"挂了"。
    """
    monkeypatch.setattr(mod.svc, "ensure_available",
                        lambda: {"available": False, "error": "Connection refused"})
    with pytest.raises(HTTPException) as exc:
        mod.chat_stream(mod.ChatBody(message="hi"), request=None, user="alice@x.com")
    assert exc.value.status_code == 503
    assert "不可用" in str(exc.value.detail)


def test_deleting_a_session_while_agent_is_down_does_not_lie(monkeypatch):
    """删除必须报失败。报成功的话左栏条目消失、正文还在磁盘上，
    之后还会在管理员列表里再冒出来。"""
    cs.register_session("s1", "alice@x.com")
    _agent_down(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        mod.console_session_delete("s1", info=ALICE)
    assert exc.value.status_code == 503
    assert "s1" in cs.owned_sessions("alice@x.com", False)      # 索引保住，主人没丢


def test_presets_survive_agent_being_down(monkeypatch):
    """预设是 ops 自己的表，不该被 agent 拖下水。"""
    _agent_down(monkeypatch)
    cs.save_preset("广告周检", "alice@x.com", approval="remote")
    out = mod.console_preset_list(info=ALICE)
    assert [p["name"] for p in out["presets"]] == ["广告周检"]


def test_non_persisted_turns_do_not_leave_index_rows():
    """persist=False 的轮次 agent 不落盘，ops 也就不该建索引。

    实测在生产库里攒出过 3 行指向不存在会话的孤儿 —— 界面上看不见（列表要和
    agent 实存的对得上），所以只增不减，谁也不会发现。
    """
    frame = (b'event: start\ndata: {"session_id": "20260808-000000-000-aaaa"}\n\n')
    list(mod._tee_session_events(iter([frame]), "alice@x.com", "", "console", False))
    assert cs.owned_sessions("alice@x.com", False) == {}

    list(mod._tee_session_events(iter([frame]), "alice@x.com", "", "console", True))
    assert list(cs.owned_sessions("alice@x.com", False)) == ["20260808-000000-000-aaaa"]
