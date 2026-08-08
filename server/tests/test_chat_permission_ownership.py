"""写操作审批的归属校验。

批准一个 permission_request 就是**授权一次真实写入**。request_id 由 agent daemon
现场生成、在 SSE 里明文发给发起人，所以必须确认按下确认的就是发起这轮对话的人 ——
否则任何登录用户拿到一个 request_id 就能替别人批准改广告、开领星可写开关。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers import ivyea_agent as mod


@pytest.fixture(autouse=True)
def _clean_owners():
    with mod._APPROVAL_OWNERS_LOCK:
        mod._APPROVAL_OWNERS.clear()
    yield
    with mod._APPROVAL_OWNERS_LOCK:
        mod._APPROVAL_OWNERS.clear()


def _sse(*frames: bytes):
    """把若干 SSE 帧当成上游 chunk 序列。"""
    return iter(frames)


def test_tee_records_owner_and_passes_bytes_through_untouched():
    """转发必须逐字节原样 —— 心跳注释行少一个字都可能让慢轮次被中间层掐断。"""
    frames = [
        b"event: start\ndata: {\"ok\": true}\n\n",
        b": ping\n\n",
        b'event: permission_request\ndata: {"request_id": "r1", "op_type": "write_file"}\n\n',
        b"event: final\ndata: {\"ok\": true}\n\n",
    ]
    out = list(mod._tee_session_events(_sse(*frames), "alice@example.com"))
    assert out == frames                       # 一个字节都没改
    assert mod._approval_owner("r1") == "alice@example.com"


def test_tee_handles_frame_split_across_chunks():
    """一帧被拆在两个 chunk 里也要认得出来（真实网络里很常见）。"""
    frames = [
        b'event: permission_request\ndata: {"request_i',
        b'd": "r2", "op_type": "run_command"}\n\n',
    ]
    out = list(mod._tee_session_events(_sse(*frames), "bob@example.com"))
    assert b"".join(out) == b"".join(frames)
    assert mod._approval_owner("r2") == "bob@example.com"


def test_tee_survives_garbage_without_breaking_the_stream():
    """记账失败最坏是让用户点确认时被判失效；绝不能因此毁掉整轮对话。"""
    frames = [
        b"event: permission_request\ndata: {not valid json}\n\n",
        b"event: token\ndata: {\"text\": \"hi\"}\n\n",
    ]
    out = list(mod._tee_session_events(_sse(*frames), "carol@example.com"))
    assert out == frames
    assert mod._approval_owner("nope") is None


def test_owner_expires_after_ttl(monkeypatch):
    mod._remember_approval_owner("r3", "dave@example.com")
    assert mod._approval_owner("r3") == "dave@example.com"
    monkeypatch.setattr(mod._time, "time", lambda: 10 ** 12)   # 远未来
    assert mod._approval_owner("r3") is None


def _body(request_id="r1", choice="approve"):
    return mod.ChatPermissionBody(request_id=request_id, choice=choice)


def test_other_user_cannot_approve_someone_elses_write(monkeypatch):
    """核心：别人的审批请求，登录了也不能替他批。"""
    called: list = []
    monkeypatch.setattr(mod.svc, "chat_permission", lambda p: called.append(p) or {"ok": True})
    mod._remember_approval_owner("r1", "alice@example.com")

    with pytest.raises(HTTPException) as exc:
        mod.chat_permission(_body(), user="mallory@example.com")
    assert exc.value.status_code == 403
    assert not called                     # 决策根本没被转发给 daemon


def test_unknown_request_is_rejected_not_forwarded(monkeypatch):
    """没登记过的 request_id 一律当失效 —— 失败方向必须是"不放行"。"""
    called: list = []
    monkeypatch.setattr(mod.svc, "chat_permission", lambda p: called.append(p) or {"ok": True})
    with pytest.raises(HTTPException) as exc:
        mod.chat_permission(_body(request_id="never-seen"), user="alice@example.com")
    assert exc.value.status_code == 404
    assert not called


def test_owner_can_approve(monkeypatch):
    called: list = []
    monkeypatch.setattr(mod.svc, "chat_permission", lambda p: called.append(p) or {"ok": True})
    mod._remember_approval_owner("r1", "alice@example.com")
    out = mod.chat_permission(_body(), user="alice@example.com")
    assert out == {"ok": True}
    assert called == [{"request_id": "r1", "choice": "approve"}]


def test_choice_is_restricted_to_known_decisions():
    """只认审批引擎认得的那几个决策，别让调用方塞个奇怪的值改变语义。"""
    for good in ("approve", "session", "deny", "abort"):
        assert mod.ChatPermissionBody(request_id="r", choice=good).choice == good
    for bad in ("yolo", "", "APPROVE", "edit"):
        with pytest.raises(Exception):
            mod.ChatPermissionBody(request_id="r", choice=bad)


def test_default_chat_payload_is_unchanged_by_the_new_fields():
    """新增的可选字段取默认值时必须从 payload 里消失 ——
    普通一轮对话发给 daemon 的东西要和加字段之前逐字一致。"""
    payload = mod._chat_payload(mod.ChatBody(message="hi"))
    assert set(payload) == {
        "message", "session_id", "workspace", "asin", "ops_context",
        "persist", "plan_mode", "inject_retrieval",
    }


def test_opt_in_fields_reach_the_daemon_when_set():
    payload = mod._chat_payload(mod.ChatBody(
        message="hi", skill="amazon.budget_pacing", auto_skill=True,
        approval="remote", plan_mode=False, use_tools=False))
    assert payload["skill"] == "amazon.budget_pacing"
    assert payload["auto_skill"] is True
    assert payload["approval"] == "remote"
    assert payload["use_tools"] is False
    assert payload["plan_mode"] is False


def test_duplicate_click_reads_as_already_handled_not_a_server_error(monkeypatch):
    """两个页签同时点同一条审批：一个成功，另一个必须收到**说人话的 409**。

    daemon 对"未知/已过期"回 404，而 `_call` 把 agent 的一切非 2xx 都翻成 502。
    502 在界面上读作"服务器坏了"，可真相通常只是另一个页签先点了 —— 实测并发点
    两次确实一次 200 一次 502。
    """
    def already_answered(payload):
        raise HTTPException(status_code=502,
                            detail="IvyeaAgent HTTP 404: unknown_or_expired_request")

    monkeypatch.setattr(mod, "_call", lambda fn, *a, **k: already_answered(a[0] if a else {}))
    mod._remember_approval_owner("dup1", "alice@example.com")
    with pytest.raises(HTTPException) as exc:
        mod.chat_permission(_body(request_id="dup1"), user="alice@example.com")
    assert exc.value.status_code == 409
    assert "已经被处理" in str(exc.value.detail)


def test_real_gateway_errors_are_still_502(monkeypatch):
    """别把所有 502 都当成"已处理" —— agent 真的崩了要照实说。"""
    def boom(fn, *a, **k):
        raise HTTPException(status_code=502, detail="IvyeaAgent HTTP 500: boom")

    monkeypatch.setattr(mod, "_call", boom)
    mod._remember_approval_owner("dup2", "alice@example.com")
    with pytest.raises(HTTPException) as exc:
        mod.chat_permission(_body(request_id="dup2"), user="alice@example.com")
    assert exc.value.status_code == 502
