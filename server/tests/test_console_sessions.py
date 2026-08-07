"""任务台会话索引与工作区。

重点不在功能，在**归属**：agent 的会话库是整机共享的一个目录，
`/api/ivyea-agent/chat/sessions` 原样返回机器上所有会话。以前它只在右下角悬浮球
的历史里露一下，现在会话要常驻左栏 —— 不按归属过滤就等于把同事的对话摆在每个
人眼前。所以这里把"谁能看到/改到哪条会话"钉死。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services import console_sessions as cs
from app.routers import ivyea_agent as mod


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    # 只改这个模块的落盘位置，**不动全局 settings.data_dir** —— 那是所有模块共享的
    # 同一个对象，改它会波及别的测试（实测撞坏了 test_auto_sync 那个起后台线程
    # 往 data_dir 写标记的用例）。
    monkeypatch.setattr(cs, "_db_path", lambda: tmp_path / "console_sessions.sqlite3")
    cs.init_db()
    yield


ALICE = {"email": "alice@x.com", "role": "user", "id": 2}
BOB = {"email": "bob@x.com", "role": "user", "id": 3}
ADMIN = {"email": "admin@x.com", "role": "admin", "id": "admin"}


def test_register_records_owner_and_workspace():
    cs.register_session("s1", "alice@x.com", "选品")
    rows = cs.owned_sessions("alice@x.com", is_admin=False)
    assert rows["s1"]["principal"] == "alice@x.com"
    assert rows["s1"]["workspace"] == "选品"


def test_owner_is_not_overwritten_by_later_turns():
    """后续轮次只更新时间戳 —— 一条会话的主人就是开它的那个人。"""
    cs.register_session("s1", "alice@x.com", "选品")
    cs.register_session("s1", "bob@x.com", "别的")
    row = cs.owned_sessions("alice@x.com", is_admin=False)["s1"]
    assert row["principal"] == "alice@x.com"
    assert row["workspace"] == "选品"


def test_users_only_see_their_own():
    cs.register_session("s1", "alice@x.com")
    cs.register_session("s2", "bob@x.com")
    assert set(cs.owned_sessions("alice@x.com", is_admin=False)) == {"s1"}
    assert set(cs.owned_sessions("bob@x.com", is_admin=False)) == {"s2"}
    assert set(cs.owned_sessions("admin@x.com", is_admin=True)) == {"s1", "s2"}


def test_can_access_rules():
    cs.register_session("s1", "alice@x.com")
    assert cs.can_access("s1", "alice@x.com", False) is True
    assert cs.can_access("s1", "bob@x.com", False) is False
    assert cs.can_access("s1", "anyone", True) is True
    # 索引里没有的（悬浮球/CLI 开的、装这套之前就有的）对普通用户一律不可见
    assert cs.can_access("unknown", "alice@x.com", False) is False
    assert cs.can_access("unknown", "admin@x.com", True) is True


def test_patch_and_delete_reject_other_peoples_sessions(monkeypatch):
    cs.register_session("s1", "alice@x.com")
    with pytest.raises(HTTPException) as exc:
        mod.console_session_patch("s1", mod.ConsoleSessionPatch(title="改个名"), info=BOB)
    assert exc.value.status_code == 403

    called: list = []
    monkeypatch.setattr(mod.svc, "chat_session_delete", lambda sid: called.append(sid))
    with pytest.raises(HTTPException) as exc:
        mod.console_session_delete("s1", info=BOB)
    assert exc.value.status_code == 403
    assert not called          # 正文没被删


def test_owner_can_rename_and_move():
    cs.register_session("s1", "alice@x.com")
    mod.console_session_patch("s1", mod.ConsoleSessionPatch(title="广告复盘", workspace="选品"),
                              info=ALICE)
    row = cs.owned_sessions("alice@x.com", is_admin=False)["s1"]
    assert row["title"] == "广告复盘" and row["workspace"] == "选品"


def test_delete_reports_failure_when_agent_is_unreachable(monkeypatch):
    """agent 不可达时**绝不能报成功**。

    实测踩过：agent 短暂不可达 → 删除返回 ok → 左栏条目消失 → 用户以为删干净了，
    其实正文还原封不动躺在磁盘上，之后还会在管理员列表里再冒出来。
    索引也不能删 —— 否则那条会话就彻底失去主人了。
    """
    cs.register_session("s1", "alice@x.com")

    def _down(sid):
        raise HTTPException(status_code=503, detail="IvyeaAgent 不可用")

    monkeypatch.setattr(mod.svc, "chat_session_delete", _down)
    with pytest.raises(HTTPException) as exc:
        mod.console_session_delete("s1", info=ALICE)
    assert exc.value.status_code == 503
    assert "没有被删除" in str(exc.value.detail)
    assert "s1" in cs.owned_sessions("alice@x.com", is_admin=False)   # 索引保留


def test_delete_clears_index_even_when_agent_already_lost_it(monkeypatch):
    """agent 那边本来就没有（手工删过），索引照样要清干净，别留幽灵条目。"""
    cs.register_session("s1", "alice@x.com")

    def _boom(sid):
        raise HTTPException(status_code=404, detail="会话不存在")

    monkeypatch.setattr(mod.svc, "chat_session_delete", _boom)
    out = mod.console_session_delete("s1", info=ALICE)
    assert out["ok"] is True
    assert cs.owned_sessions("alice@x.com", is_admin=False) == {}


def test_list_hides_unindexed_sessions_from_regular_users(monkeypatch):
    """未登记的历史会话：管理员看得到，普通用户看不到。"""
    cs.register_session("mine", "alice@x.com")
    monkeypatch.setattr(mod, "_call", lambda fn, *a, **k: {"sessions": [
        {"id": "mine", "preview": "我的", "turns": 2, "updated": 200},
        {"id": "legacy", "preview": "别人的老会话", "turns": 1, "updated": 100},
    ]})

    out = mod.console_session_list(workspace="", source="", limit=60, info=ALICE)
    assert [s["id"] for s in out["sessions"]] == ["mine"]

    out_admin = mod.console_session_list(workspace="", source="", limit=60, info=ADMIN)
    assert {s["id"] for s in out_admin["sessions"]} == {"mine", "legacy"}
    legacy = next(s for s in out_admin["sessions"] if s["id"] == "legacy")
    assert legacy["indexed"] is False


def test_list_survives_agent_being_down(monkeypatch):
    """agent 不在时左栏不该整个空掉 —— 至少还能列出索引里的会话。"""
    cs.register_session("s1", "alice@x.com")

    def _down(fn, *a, **k):
        raise HTTPException(status_code=503, detail="IvyeaAgent 不可用")

    monkeypatch.setattr(mod, "_call", _down)
    out = mod.console_session_list(workspace="", source="", limit=60, info=ALICE)
    assert out["ok"] is True
    assert out["sessions"] == []            # 没有正文摘要就列不出条目
    assert out["workspaces"][0]["name"] == cs.DEFAULT_WORKSPACE


def test_custom_title_wins_over_preview(monkeypatch):
    cs.register_session("s1", "alice@x.com")
    cs.update_session("s1", title="广告复盘")
    monkeypatch.setattr(mod, "_call", lambda fn, *a, **k: {"sessions": [
        {"id": "s1", "preview": "原始第一句", "turns": 1, "updated": 1},
    ]})
    out = mod.console_session_list(workspace="", source="", limit=60, info=ALICE)
    assert out["sessions"][0]["title"] == "广告复盘"
    assert out["sessions"][0]["preview"] == "原始第一句"


# ── 工作区 ──────────────────────────────────────────────────────────────────

def test_default_workspace_always_present_and_first():
    ws = cs.list_workspaces("alice@x.com", is_admin=False)
    assert ws[0]["name"] == cs.DEFAULT_WORKSPACE and ws[0]["builtin"] is True


def test_create_and_scope_workspaces(tmp_path):
    real = tmp_path / "xuanpin"
    real.mkdir()
    cs.create_workspace("选品", "alice@x.com", path=str(real), is_admin=True)
    assert [w["name"] for w in cs.list_workspaces("alice@x.com", False)] == [cs.DEFAULT_WORKSPACE, "选品"]
    assert [w["name"] for w in cs.list_workspaces("bob@x.com", False)] == [cs.DEFAULT_WORKSPACE]
    assert cs.workspace_path("选品", "alice@x.com") == str(real.resolve())


def test_cannot_create_or_delete_builtin_workspace():
    with pytest.raises(ValueError):
        cs.create_workspace(cs.DEFAULT_WORKSPACE, "alice@x.com")
    with pytest.raises(ValueError):
        cs.delete_workspace(cs.DEFAULT_WORKSPACE, "alice@x.com", False)


def test_deleting_a_workspace_keeps_its_sessions():
    """解散分组 ≠ 毁掉一堆对话。"""
    cs.create_workspace("选品", "alice@x.com")
    cs.register_session("s1", "alice@x.com", "选品")
    moved = cs.delete_workspace("选品", "alice@x.com", False)
    assert moved == 1
    rows = cs.owned_sessions("alice@x.com", False)
    assert "s1" in rows and rows["s1"]["workspace"] == ""


def test_workspace_filter():
    cs.register_session("s1", "alice@x.com", "选品")
    cs.register_session("s2", "alice@x.com", "")
    assert set(cs.owned_sessions("alice@x.com", False, workspace="选品")) == {"s1"}


def test_preview_strips_injected_context():
    """每轮注入给模型的技能/知识块和用户原话存在同一条消息里 ——
    左栏标题只该显示用户真正打的那句。"""
    raw = ("SKU001 为什么不转化？\n\n[Ivyea Skill：本轮相关可复用流程]\n"
           "[skill:amazon.listing_conversion_audit score=9] ...\n\n"
           "[Ivyea 本地知识检索 / 亚马逊知识证据]\n[K1] ...")
    assert cs.clean_preview(raw) == "SKU001 为什么不转化？"
    assert cs.clean_preview("干净的一句话") == "干净的一句话"
    assert cs.clean_preview("") == ""


def test_list_uses_cleaned_preview(monkeypatch):
    cs.register_session("s1", "alice@x.com")
    monkeypatch.setattr(mod, "_call", lambda fn, *a, **k: {"sessions": [
        {"id": "s1", "preview": "查广告\n\n[Ivyea Skill：本轮相关可复用流程]\nxxx",
         "turns": 1, "updated": 1},
    ]})
    out = mod.console_session_list(workspace="", source="", limit=60, info=ALICE)
    assert out["sessions"][0]["title"] == "查广告"
    assert "[Ivyea Skill" not in out["sessions"][0]["preview"]


def test_preview_strips_truncated_marker():
    """agent 先把首条消息砍到 50 字才给我们，标记常常是半截的。

    实测左栏出现过 `…一句话总结。\n\n[Iv` —— 完整标记匹配不上，半个标记留在了标题里。
    """
    assert cs.clean_preview("一句话总结。\n\n[Iv") == "一句话总结。"
    assert cs.clean_preview("查广告\n\n[Ivyea Sk") == "查广告"
    assert cs.clean_preview("查广告\n\n[") == "查广告"
    # 别误伤正常内容
    assert cs.clean_preview("看看 [K1] 这条证据") == "看看 [K1] 这条证据"
    assert cs.clean_preview("第一行\n\n第二行") == "第一行\n\n第二行"


# ── 工作区名 → 目录 的换算 ──────────────────────────────────────────────────

def test_workspace_name_is_not_a_directory(tmp_path):
    """核心回归：工作区名（可能是中文）绝不能被当成目录路径发给 agent。

    实测踩过：前端把「选品调研」当 workspace 送下去，落到 ToolContext.workspace
    （agent 文件工具的工作目录），相对路径的文件操作全指向一个不存在的地方。
    """
    cs.create_workspace("选品调研", "alice@x.com")            # 没绑目录
    assert cs.workspace_path("选品调研", "alice@x.com") == ""  # → 用 agent 默认 cwd
    assert cs.workspace_path(cs.DEFAULT_WORKSPACE, "alice@x.com") == ""
    assert cs.workspace_path("不存在的工作区", "alice@x.com") == ""

    real = tmp_path / "proj"
    real.mkdir()
    cs.create_workspace("有目录的", "alice@x.com", str(real), is_admin=True)
    assert cs.workspace_path("有目录的", "alice@x.com") == str(real.resolve())


def test_binding_a_directory_is_admin_only(tmp_path):
    """绑目录 = 给 Agent 一片文件系统访问面，和 MCP 的 stdio command 同一类授权。"""
    real = tmp_path / "proj"
    real.mkdir()
    with pytest.raises(ValueError, match="管理员"):
        cs.create_workspace("越权", "bob@x.com", str(real), is_admin=False)
    # 不绑目录的普通工作区，谁都能建
    assert cs.create_workspace("普通分组", "bob@x.com")["path"] == ""


def test_bad_directory_rejected_at_creation(tmp_path):
    with pytest.raises(ValueError, match="绝对路径"):
        cs.create_workspace("相对路径", "a@x.com", "relative/dir", is_admin=True)
    with pytest.raises(ValueError, match="不存在"):
        cs.create_workspace("不存在", "a@x.com", str(tmp_path / "nope"), is_admin=True)


def test_vanished_directory_falls_back_to_default(tmp_path):
    """目录后来被删了就当没绑，别把 agent 的工作目录指到一个不存在的地方。"""
    real = tmp_path / "gone"
    real.mkdir()
    cs.create_workspace("会消失的", "a@x.com", str(real), is_admin=True)
    real.rmdir()
    assert cs.workspace_path("会消失的", "a@x.com") == ""


def test_router_translates_name_to_directory(tmp_path, monkeypatch):
    """路由层必须换算：payload 里给 agent 的是目录，登记分组用的是名字。"""
    real = tmp_path / "ws"
    real.mkdir()
    cs.create_workspace("选品", "alice@x.com", str(real), is_admin=True)

    payload, name = mod._resolve_workspace(
        mod.ChatBody(message="hi", workspace="选品"), "alice@x.com")
    assert payload["workspace"] == str(real.resolve())    # 发给 agent 的是目录
    assert name == "选品"                                  # 记分组用的是名字

    # 没绑目录的工作区：workspace 直接从 payload 拿掉，让 agent 用默认 cwd
    cs.create_workspace("纯分组", "alice@x.com")
    payload2, name2 = mod._resolve_workspace(
        mod.ChatBody(message="hi", workspace="纯分组"), "alice@x.com")
    assert "workspace" not in payload2 and name2 == "纯分组"


# ── 会话来源（三个板块共用一个会话库）────────────────────────────────────────

def test_source_defaults_to_console_and_filters():
    """来源筛选必须走 SQL，不能只在前端过滤 —— 列表有条数上限，
    前端过滤会把上限之外的条目静默藏掉，看着像"会话丢了"。"""
    cs.register_session("s1", "alice@x.com")                      # 不传 = 任务台
    cs.register_session("s2", "alice@x.com", "", "assistant")
    cs.register_session("s3", "alice@x.com", "", "brain")
    rows = cs.owned_sessions("alice@x.com", False)
    assert rows["s1"]["source"] == "console"
    assert set(cs.owned_sessions("alice@x.com", False, source="assistant")) == {"s2"}
    assert set(cs.owned_sessions("alice@x.com", False, source="brain")) == {"s3"}
    assert set(cs.owned_sessions("alice@x.com", False)) == {"s1", "s2", "s3"}


def test_unknown_source_falls_back_to_console():
    """来源是 ops 自己写的枚举，脏值不该落进库里变成第四种来源。"""
    cs.register_session("s1", "alice@x.com", "", "../etc/passwd")
    assert cs.owned_sessions("alice@x.com", False)["s1"]["source"] == "console"


# ── 智能体预设 ──────────────────────────────────────────────────────────────

def test_presets_are_per_user():
    """预设里带着工作区（可能绑到某个目录），共享等于把别人的目录摆进你的下拉框。"""
    cs.save_preset("广告周检", "alice@x.com", skill="ads", approval="remote")
    assert [p["name"] for p in cs.list_presets("alice@x.com")] == ["广告周检"]
    assert cs.list_presets("bob@x.com") == []


def test_saving_same_name_updates_instead_of_duplicating():
    cs.save_preset("广告周检", "alice@x.com", skill="ads", approval="remote")
    cs.save_preset("广告周检", "alice@x.com", skill="ads-v2", approval="none")
    rows = cs.list_presets("alice@x.com")
    assert len(rows) == 1 and rows[0]["skill"] == "ads-v2" and rows[0]["approval"] == "none"


def test_preset_rejects_blank_name_and_bad_approval():
    with pytest.raises(ValueError):
        cs.save_preset("   ", "alice@x.com")
    with pytest.raises(ValueError):
        cs.save_preset("x", "alice@x.com", approval="yolo")


def test_delete_preset_is_idempotent_and_scoped():
    cs.save_preset("x", "alice@x.com")
    assert cs.delete_preset("x", "bob@x.com") is False      # 删不到别人的
    assert cs.delete_preset("x", "alice@x.com") is True
    assert cs.delete_preset("x", "alice@x.com") is False
