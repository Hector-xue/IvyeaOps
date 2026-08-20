"""会话自动起名 + 待审批对账 —— 两件"列表里看得见"的事。

自动起名：左栏此前显示用户打的第一句话。可第一句常常是"帮我看下这个""继续"
"这个报错怎么回事" —— 十条会话六条重名，列表变成一堆认不出来的重复项。所以标题
改成按**这段对话在做什么**来起。这里钉住的是它的边界：手动改过的名字绝不覆盖、
模型吐一整段时宁可不改、引用标记不许漏进标题。

待审批对账：console_approvals 是流水账，只有决策/超时帧回到 ops 才销账。页面关掉、
断链、agent 重启这三种情况下没有任何帧会来，于是会话都结束几天了，待审批里还挂着
一张点了只会 409 的僵尸卡片。真相在 agent 那边（还阻塞着的队列），这里只做对账。
"""
from __future__ import annotations

from app.routers import ivyea_agent as router
from app.services import console_sessions


# 库路径不用在这里 patch：server/conftest.py 已经把 settings.data_dir 整个指向临时
# 目录（那份文件的注释讲了为什么要一次性钉死）。在这儿再 patch 一个名字对不上的属性，
# 只会造出"看起来隔离了"的假象。


# ── 自动起名 ──────────────────────────────────────────────────────────────

def _fake_detail(ask: str, answer: str = "好的，我看一下。"):
    return {"session": {"messages": [
        {"role": "user", "content": ask},
        {"role": "assistant", "content": answer},
    ]}}


def test_title_is_generated_when_empty(monkeypatch):
    saved = {}
    monkeypatch.setattr(console_sessions, "session_row", lambda sid: {"session_id": sid, "title": ""})
    monkeypatch.setattr(router.svc, "chat_session",
                        lambda sid, turns=1, before=1: _fake_detail("这个报错怎么回事"))
    monkeypatch.setattr(console_sessions, "update_session",
                        lambda sid, **kw: saved.update(kw))
    monkeypatch.setattr("app.services.ai_synthesis_service.generate_text",
                        _fake_generate("排查登录接口 500"))
    router._auto_title_session("s1")
    assert saved["title"] == "排查登录接口 500"


def test_manual_title_is_never_overwritten(monkeypatch):
    calls = []
    monkeypatch.setattr(console_sessions, "session_row",
                        lambda sid: {"session_id": sid, "title": "我自己起的名字"})
    monkeypatch.setattr(console_sessions, "update_session",
                        lambda sid, **kw: calls.append(kw))
    monkeypatch.setattr("app.services.ai_synthesis_service.generate_text",
                        _fake_generate("模型起的名字"))
    router._auto_title_session("s2")
    assert calls == [], "用户手动改过的名字，模型不许动"


def test_citation_marker_and_wrapping_punctuation_are_stripped(monkeypatch):
    saved = {}
    monkeypatch.setattr(console_sessions, "session_row", lambda sid: {"session_id": sid, "title": ""})
    monkeypatch.setattr(router.svc, "chat_session",
                        lambda sid, turns=1, before=1: _fake_detail("描述一下这张图片"))
    monkeypatch.setattr(console_sessions, "update_session", lambda sid, **kw: saved.update(kw))
    # 检索注入漏出来的引用标记（实测起出来过"…无法读取[K2"这种半截标题）
    monkeypatch.setattr("app.services.ai_synthesis_service.generate_text",
                        _fake_generate('「无法读取图片」 [K2]'))
    router._auto_title_session("s3")
    assert saved["title"] == "无法读取图片"


def test_a_whole_paragraph_is_refused(monkeypatch):
    saved = {}
    monkeypatch.setattr(console_sessions, "session_row", lambda sid: {"session_id": sid, "title": ""})
    monkeypatch.setattr(router.svc, "chat_session",
                        lambda sid, turns=1, before=1: _fake_detail("帮我看下这个"))
    monkeypatch.setattr(console_sessions, "update_session", lambda sid, **kw: saved.update(kw))
    monkeypatch.setattr("app.services.ai_synthesis_service.generate_text",
                        _fake_generate("好的，这段对话主要在讨论如何排查一个登录接口返回 500 的问题，"
                                       "涉及日志、数据库连接与超时配置等多个方面。"))
    router._auto_title_session("s4")
    assert saved == {}, "吐了一整段就不是标题 —— 宁可留着第一句话"


def test_model_failure_leaves_the_title_alone(monkeypatch):
    saved = {}
    monkeypatch.setattr(console_sessions, "session_row", lambda sid: {"session_id": sid, "title": ""})
    monkeypatch.setattr(router.svc, "chat_session",
                        lambda sid, turns=1, before=1: _fake_detail("帮我看下这个"))
    monkeypatch.setattr(console_sessions, "update_session", lambda sid, **kw: saved.update(kw))

    async def _boom(*a, **kw):
        raise RuntimeError("模型不可用")
    monkeypatch.setattr("app.services.ai_synthesis_service.generate_text", _boom)
    router._auto_title_session("s5")
    assert saved == {}, "起名失败只是列表里显示第一句话，不该炸也不该写脏数据"


def _fake_generate(text: str):
    async def _gen(prompt, skip_agent=False, inject_retrieval=True):
        assert inject_retrieval is False, "起名是给机器读的输出，必须关掉检索注入"
        return text
    return _gen


# ── 待审批对账 ────────────────────────────────────────────────────────────

def test_stale_approvals_expire():
    console_sessions.record_approval_request("live1", "sX", "admin", "改配置", "write")
    console_sessions.record_approval_request("dead1", "sY", "admin", "删文件", "write")

    # agent 说只有 live1 还在等 → dead1 是关页面/断链/重启留下的僵尸
    n = console_sessions.expire_stale_approvals(["live1"])
    assert n >= 1
    pending = {r["request_id"] for r in console_sessions.pending_approvals("admin")}
    assert "live1" in pending and "dead1" not in pending


def test_unreachable_agent_never_clears_pending():
    console_sessions.record_approval_request("waiting", "sZ", "admin", "改配置", "write")
    # live=None 表示"问不到"（老 agent / agent 没起）。绝不能当成空集，
    # 那会把真正等着人点的审批一把清掉。
    console_sessions.expire_stale_approvals(None)
    pending = {r["request_id"] for r in console_sessions.pending_approvals("admin")}
    assert "waiting" in pending
