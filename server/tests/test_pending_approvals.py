"""跨会话待审批 —— 手机上审批的数据面。

盯的是**归属**：审批是能改真实数据的开关，看错人比看不到严重得多。
"""
from __future__ import annotations

import importlib
import time

import pytest


@pytest.fixture()
def cs(tmp_path, monkeypatch):
    monkeypatch.setenv("IVYEA_OPS_DATA_DIR", str(tmp_path))
    from app.core import config
    importlib.reload(config)
    from app.services import console_sessions
    importlib.reload(console_sessions)
    console_sessions.init_db()
    return console_sessions


def _req(cs, rid, principal, session="s1", title="改竞价", op="adjust_bid"):
    cs.record_approval_request(rid, session, principal, title, op)


def test_pending_lists_only_my_own(cs):
    """看得到别人的待审批，等于可以替别人放行一个改真实投放的操作。"""
    _req(cs, "r1", "alice@x.com")
    _req(cs, "r2", "bob@x.com")
    mine = cs.pending_approvals("alice@x.com")
    assert [a["request_id"] for a in mine] == ["r1"]


def test_decided_ones_drop_off(cs):
    """点过的立刻消失 —— 一个还挂着已处理项的列表，用户会重复点。"""
    _req(cs, "r1", "alice@x.com")
    assert len(cs.pending_approvals("alice@x.com")) == 1
    cs.record_approval_decision("r1", "approve")
    assert cs.pending_approvals("alice@x.com") == []


def test_timeout_also_drops_off(cs):
    """超时被自动拒也是一个决定。留在列表里会让用户对着一条早已失效的卡片纠结。"""
    _req(cs, "r1", "alice@x.com")
    cs.record_approval_decision("r1", "timeout")
    assert cs.pending_approvals("alice@x.com") == []


def test_crosses_sessions(cs):
    """跨会话正是这个查询存在的理由：按会话查在手机上用不了。"""
    _req(cs, "r1", "alice@x.com", session="s1")
    _req(cs, "r2", "alice@x.com", session="s2")
    got = {a["session_id"] for a in cs.pending_approvals("alice@x.com")}
    assert got == {"s1", "s2"}


def test_newest_first(cs):
    _req(cs, "old", "alice@x.com")
    time.sleep(0.02)
    _req(cs, "new", "alice@x.com")
    assert cs.pending_approvals("alice@x.com")[0]["request_id"] == "new"


def test_decision_without_a_request_creates_nothing(cs):
    """没有对应请求的决定是伪造的，不该凭空长出一条记录。"""
    cs.record_approval_decision("ghost", "approve")
    assert cs.pending_approvals("alice@x.com") == []


def test_replayed_request_keeps_the_first(cs):
    """同一个 request_id 重来说明是重放，保留最早那条，不能因此复活一条已决定的。"""
    _req(cs, "r1", "alice@x.com", title="第一次")
    cs.record_approval_decision("r1", "approve")
    _req(cs, "r1", "alice@x.com", title="重放")
    assert cs.pending_approvals("alice@x.com") == []


def test_empty_principal_does_not_match_everything(cs):
    """空 principal 不能变成通配符 —— 那会让未登录态看到所有人的审批。"""
    _req(cs, "r1", "alice@x.com")
    assert cs.pending_approvals("") == []
