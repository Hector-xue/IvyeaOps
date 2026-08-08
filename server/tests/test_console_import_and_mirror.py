"""三处会话收编的服务端契约：导入、知识库镜像、时区。

这几处此前**只有端到端跑过，没有测试兜底** —— 端到端要真账号、真模型、真余额，
不能天天跑；而这些逻辑一旦被后来的改动碰坏，左栏就会静默地少东西或多出重复条目。
"""
from __future__ import annotations

import pytest

from app.routers import ivyea_agent as mod
from app.routers import brain as br
from app.services import console_sessions as cs
from app.services import schedules as sc


ADMIN = {"email": "admin@x.com", "role": "admin", "id": "admin"}
ALICE = {"email": "alice@x.com", "role": "user", "id": 2}


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_db_path", lambda: tmp_path / "console_sessions.sqlite3")
    cs.init_db()
    yield


# ── 导入 ────────────────────────────────────────────────────────────────────

def _imported(monkeypatch):
    """让 chat_import 表现得像真的 agent：按 id 覆盖写，回传同一个 id。"""
    seen: dict[str, list] = {}

    def fake_import(payload):
        sid = payload["id"]
        seen[sid] = payload["messages"]        # 同 id 覆盖，正是 agent 的语义
        return {"ok": True, "id": sid}

    monkeypatch.setattr(mod.svc, "chat_import", fake_import)
    return seen


def test_import_is_idempotent_by_id(monkeypatch):
    """重复导入必须是覆盖而不是新增。用户会重复点、两个标签页会同时点。"""
    seen = _imported(monkeypatch)
    body = mod.ConsoleImportBody(source="assistant", sessions=[
        {"id": "abc123", "messages": [{"role": "user", "content": "旧对话"}]},
    ])
    first = mod.console_session_import(body, info=ALICE)
    second = mod.console_session_import(body, info=ALICE)
    assert first["imported"] == second["imported"] == ["imp-assistant-abc123"]
    assert len(seen) == 1
    assert len(cs.owned_sessions("alice@x.com", False, source="assistant")) == 1


def test_import_prefix_keeps_sources_from_colliding(monkeypatch):
    """两个板块可能有同样的本地 id，前缀让它们各归各的。"""
    _imported(monkeypatch)
    for src in ("assistant", "brain"):
        mod.console_session_import(
            mod.ConsoleImportBody(source=src, sessions=[
                {"id": "same", "messages": [{"role": "user", "content": "x"}]}]),
            info=ALICE)
    assert set(cs.owned_sessions("alice@x.com", False)) == {
        "imp-assistant-same", "imp-brain-same"}


def test_import_skips_empty_and_rejects_unsafe_ids(monkeypatch):
    _imported(monkeypatch)
    out = mod.console_session_import(
        mod.ConsoleImportBody(source="assistant", sessions=[
            {"id": "ok1", "messages": [{"role": "user", "content": "   "}]}]),
        info=ALICE)
    assert out["count"] == 0 and out["skipped"] == 1
    # id 会被拼进文件名，越界的一律进不来
    with pytest.raises(Exception):
        mod.ConsoleImportBody(source="assistant", sessions=[
            {"id": "../../etc/passwd", "messages": [{"role": "user", "content": "x"}]}])


def test_imported_session_belongs_to_the_importer(monkeypatch):
    """搬进来的历史是**谁的**要记清楚，否则同事就能看到别人的旧对话。"""
    _imported(monkeypatch)
    mod.console_session_import(
        mod.ConsoleImportBody(source="assistant", sessions=[
            {"id": "mine", "messages": [{"role": "user", "content": "x"}]}]),
        info=ALICE)
    assert set(cs.owned_sessions("alice@x.com", False)) == {"imp-assistant-mine"}
    assert cs.owned_sessions("bob@x.com", False) == {}


# ── 知识库镜像 ──────────────────────────────────────────────────────────────

def test_brain_mirror_copies_text_only_and_stays_idempotent(monkeypatch, tmp_path):
    """镜像不是搬家：agent 只拿正文，引证/告警留在 brain 自己的库里。

    而且每轮都会重镜像一次，所以必须按 id 覆盖 —— 否则聊十轮就长出十条会话。
    """
    calls: list[dict] = []
    monkeypatch.setattr(br.ia, "chat_import", lambda p: (calls.append(p), {"ok": True, "id": p["id"]})[1])
    monkeypatch.setattr(br.bc, "get_session", lambda sid: {"messages": [
        {"role": "user", "content": "问题", "citations": [{"title": "某卡"}]},
        {"role": "assistant", "content": "回答", "citations": [{"title": "某卡"}]},
    ]})
    monkeypatch.setattr(br, "_load_migration_map", lambda: {})
    monkeypatch.setattr(br, "_save_migration_map", lambda m: None)

    br._mirror_to_agent("brainsid", "alice@x.com")
    br._mirror_to_agent("brainsid", "alice@x.com")

    assert [c["id"] for c in calls] == ["imp-brain-brainsid"] * 2
    # 送过去的只有 role/content，引证没跟着走
    assert all(set(m) == {"role", "content"} for c in calls for m in c["messages"])
    rows = cs.owned_sessions("alice@x.com", False, source="brain")
    assert list(rows) == ["imp-brain-brainsid"]


def test_brain_mirror_reuses_the_id_from_the_old_migration(monkeypatch):
    """被 /chat/migrate-to-agent 搬过的老会话有自己的随机 id。
    不复用它的话，同一条对话会在 agent 库里躺两份。"""
    calls: list[dict] = []
    monkeypatch.setattr(br.ia, "chat_import", lambda p: (calls.append(p), {"ok": True, "id": p["id"]})[1])
    monkeypatch.setattr(br.bc, "get_session", lambda sid: {"messages": [
        {"role": "user", "content": "问题"}]})
    monkeypatch.setattr(br, "_load_migration_map", lambda: {"brainsid": "20260101-000000-000-aaaa"})
    monkeypatch.setattr(br, "_save_migration_map", lambda m: None)

    br._mirror_to_agent("brainsid", "alice@x.com")
    assert calls[0]["id"] == "20260101-000000-000-aaaa"


def test_brain_mirror_never_breaks_the_turn(monkeypatch):
    """镜像是附加价值。agent 挂了、库锁了，都不能把这一轮知识库对话弄失败。"""
    def boom(_):
        raise RuntimeError("agent down")

    monkeypatch.setattr(br.ia, "chat_import", boom)
    monkeypatch.setattr(br.bc, "get_session", lambda sid: {"messages": [
        {"role": "user", "content": "问题"}]})
    br._mirror_to_agent("brainsid", "alice@x.com")      # 不抛就算过
    assert cs.owned_sessions("alice@x.com", False, source="brain") == {}


def test_brain_mirror_skips_empty_sessions(monkeypatch):
    """刚建还没说话的会话不该出现在左栏。"""
    monkeypatch.setattr(br.ia, "chat_import", lambda p: {"ok": True, "id": p["id"]})
    monkeypatch.setattr(br.bc, "get_session", lambda sid: {"messages": []})
    br._mirror_to_agent("brainsid", "alice@x.com")
    assert cs.owned_sessions("alice@x.com", False, source="brain") == {}


# ── 时区 ────────────────────────────────────────────────────────────────────

def test_timezone_label_reports_the_server_offset():
    """cron 走服务器本地时区。标签必须真实反映它，否则界面上那句说明就是错的。"""
    import time as _t
    from datetime import datetime

    label = sc.timezone_label()
    assert _t.tzname[0] in label
    offset = datetime.now().astimezone().utcoffset()
    hours = int(offset.total_seconds() // 3600)
    assert f"UTC{'+' if hours >= 0 else '-'}{abs(hours):02d}:" in label
