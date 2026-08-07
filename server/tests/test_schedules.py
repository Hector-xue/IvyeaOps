"""定时任务：cron 解析 + 调度语义 + 无人值守的安全底线。

这条路的危险不在功能，在**没人看着的时候它做了什么**。所以三条硬规矩各有用例钉住：
只读、按创建者身份跑、不追跑。
"""
from __future__ import annotations

import time
from datetime import datetime

import pytest

from app.services import schedules as sc


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    # 只改本模块的落盘位置，不动全局 settings.data_dir（那会波及别的用例）
    monkeypatch.setattr(sc, "_db_path", lambda: tmp_path / "schedules.sqlite3")
    sc.init_db()
    yield


def _at(y, mo, d, h, mi) -> float:
    return datetime(y, mo, d, h, mi).timestamp()


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


# ── cron 解析 ───────────────────────────────────────────────────────────────

def test_every_day_at_time():
    assert _fmt(sc.next_fire("0 9 * * *", _at(2026, 8, 7, 8, 0))) == "2026-08-07 09:00"
    # 已经过了今天的点 → 明天
    assert _fmt(sc.next_fire("0 9 * * *", _at(2026, 8, 7, 9, 30))) == "2026-08-08 09:00"


def test_alignment_to_next_minute_prevents_double_fire():
    """同一分钟内不能再次命中，否则一分钟里会被触发好多次。"""
    base = _at(2026, 8, 7, 9, 0)
    assert sc.next_fire("* * * * *", base) == _at(2026, 8, 7, 9, 1)


def test_weekday_field():
    # 2026-08-07 是周五；下一个周一
    got = sc.next_fire("0 9 * * 1", _at(2026, 8, 7, 10, 0))
    assert _fmt(got) == "2026-08-10 09:00"
    assert datetime.fromtimestamp(got).weekday() == 0


def test_sunday_is_zero_not_six():
    """cron 里周日=0，Python 里周日=6 —— 弄反了会整整差一天。"""
    got = sc.next_fire("0 9 * * 0", _at(2026, 8, 7, 10, 0))   # 周五之后的周日
    assert _fmt(got) == "2026-08-09 09:00"
    assert datetime.fromtimestamp(got).weekday() == 6         # Python 的周日


def test_step_and_list_and_range():
    assert _fmt(sc.next_fire("*/15 * * * *", _at(2026, 8, 7, 9, 1))) == "2026-08-07 09:15"
    assert _fmt(sc.next_fire("0 9,18 * * *", _at(2026, 8, 7, 10, 0))) == "2026-08-07 18:00"
    assert _fmt(sc.next_fire("0 9-11 * * *", _at(2026, 8, 7, 9, 30))) == "2026-08-07 10:00"


def test_day_of_month():
    assert _fmt(sc.next_fire("0 9 1 * *", _at(2026, 8, 7, 10, 0))) == "2026-09-01 09:00"


def test_dom_and_dow_both_set_means_or():
    """两个都不是 * 时是「或」，这是 cron 的老规矩，反直觉但必须照做。"""
    # 每月 1 号 或 每周一；2026-08-07(周五) 之后最近的是 8/10 周一
    got = sc.next_fire("0 9 1 * 1", _at(2026, 8, 7, 10, 0))
    assert _fmt(got) == "2026-08-10 09:00"


def test_invalid_expressions_rejected():
    for bad in ("", "0 9 * *", "0 9 * * * *", "60 9 * * *", "0 24 * * *",
                "0 9 * * 9", "a 9 * * *", "0 9 * * 1-", "*/0 * * * *"):
        with pytest.raises(sc.CronError):
            sc.next_fire(bad)


def test_impossible_date_does_not_hang():
    """2 月 30 号这种在一年内永远不会到 —— 必须报错，不能死循环。"""
    with pytest.raises(sc.CronError):
        sc.next_fire("0 9 30 2 *")


# ── 任务 CRUD ───────────────────────────────────────────────────────────────

def _mk(**kw):
    base = dict(name="每日巡检", cron="0 9 * * *", prompt="跑一遍广告巡检",
                principal="alice@x.com", role="user")
    base.update(kw)
    return sc.create_task(**base)


def test_create_computes_next_run():
    t = _mk()
    assert t["next_run"] > time.time()
    assert t["enabled"] is True


def test_create_rejects_bad_input():
    with pytest.raises(ValueError):
        _mk(name="")
    with pytest.raises(ValueError):
        _mk(prompt="")
    with pytest.raises(sc.CronError):
        _mk(cron="不是 cron")


def test_disable_clears_next_run():
    """停用后 next_run 归零，别留个过期时间在界面上误导人。"""
    t = _mk()
    t2 = sc.update_task(t["id"], enabled=False)
    assert t2["enabled"] is False and t2["next_run"] == 0
    assert sc.due_tasks(now=time.time() + 10 ** 7) == []      # 永远不会被捞起来


def test_tasks_are_scoped_to_owner():
    _mk(principal="alice@x.com")
    _mk(principal="bob@x.com", name="别人的")
    assert [t["name"] for t in sc.list_tasks("alice@x.com", False)] == ["每日巡检"]
    assert len(sc.list_tasks("anyone", True)) == 2


def test_delete_removes_runs_too():
    t = _mk()
    sc._start_run(t["id"], "manual")
    assert sc.delete_task(t["id"]) is True
    assert sc.list_runs(t["id"]) == []


# ── 调度语义 ────────────────────────────────────────────────────────────────

def test_due_tasks_picks_only_overdue_enabled():
    t = _mk()
    assert sc.due_tasks(now=time.time()) == []
    assert [x["id"] for x in sc.due_tasks(now=t["next_run"] + 1)] == [t["id"]]


def test_reschedule_does_not_backfill_missed_runs():
    """服务停了两天再起来，不该把欠下的几十次一口气补跑完。"""
    t = _mk(cron="*/5 * * * *")
    # 假装 next_run 停在两天前
    with sc._conn() as conn:
        conn.execute("UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
                     (time.time() - 2 * 86400, t["id"]))
    assert len(sc.due_tasks()) == 1          # 只会被捞起来一次
    nxt = sc.reschedule(t["id"], t["cron"])
    assert nxt > time.time()                 # 从"现在"往后排，不是从两天前补
    assert sc.due_tasks() == []


def test_broken_cron_stops_scheduling_instead_of_looping():
    t = _mk()
    assert sc.reschedule(t["id"], "彻底坏掉的表达式") == 0.0
    assert sc.due_tasks(now=time.time() + 10 ** 7) == []


# ── 无人值守的安全底线 ──────────────────────────────────────────────────────

def test_run_is_always_read_only(monkeypatch):
    """核心：没人在屏幕前时，永远不许让 Agent 真的动线上数据。"""
    seen: dict = {}
    monkeypatch.setattr(sc, "_start_run", lambda tid, trg: "run1")
    monkeypatch.setattr(sc, "_finish_run", lambda *a, **k: None)
    monkeypatch.setattr(sc, "list_runs", lambda tid, limit=1: [{"id": "run1", "status": "done"}])

    from app.services import ivyea_agent_service as svc
    monkeypatch.setattr(svc, "ensure_available", lambda: {"available": True})
    monkeypatch.setattr(svc, "chat", lambda p: seen.update(p) or {"ok": True, "text": "ok"})

    t = _mk()
    sc.run_task_now(t)
    assert seen["plan_mode"] is True
    assert seen["approval"] == "none"


def test_run_uses_the_owners_identity(monkeypatch):
    """后台没有请求上下文时 principal 会回落成管理员 —— 普通用户建的任务
    照那样跑就拿到了管理员权限。执行期间必须是任务所有者。"""
    from app.core.security import current_user
    from app.services import ivyea_agent_service as svc

    monkeypatch.setattr(svc, "ensure_available", lambda: {"available": True})
    captured: dict = {}

    def _fake_chat(payload):
        cu = current_user.get() or {}
        captured["email"] = cu.get("email")
        captured["role"] = cu.get("role")
        return {"ok": True, "text": "ok"}

    monkeypatch.setattr(svc, "chat", _fake_chat)
    t = _mk(principal="alice@x.com", role="user")
    sc.run_task_now(t)
    assert captured == {"email": "alice@x.com", "role": "user"}
    # 跑完要还原，别把身份泄漏给后面的调用
    assert (current_user.get() or {}).get("email") != "alice@x.com"


def test_failure_is_recorded_not_raised(monkeypatch):
    """一个任务炸了不该掀翻调度器。"""
    from app.services import ivyea_agent_service as svc
    monkeypatch.setattr(svc, "ensure_available", lambda: {"available": True})
    monkeypatch.setattr(svc, "chat", lambda p: (_ for _ in ()).throw(RuntimeError("模型挂了")))
    t = _mk()
    sc.run_task_now(t)
    runs = sc.list_runs(t["id"])
    assert runs and runs[0]["status"] == "error" and "模型挂了" in runs[0]["error"]


def test_agent_error_response_is_recorded(monkeypatch):
    from app.services import ivyea_agent_service as svc
    monkeypatch.setattr(svc, "ensure_available", lambda: {"available": True})
    monkeypatch.setattr(svc, "chat", lambda p: {"ok": False, "error": "model_error",
                                                "detail": "额度不足"})
    t = _mk()
    sc.run_task_now(t)
    runs = sc.list_runs(t["id"])
    assert runs[0]["status"] == "error" and "额度不足" in runs[0]["error"]


def test_successful_run_keeps_output_and_session(monkeypatch):
    from app.services import ivyea_agent_service as svc
    monkeypatch.setattr(svc, "ensure_available", lambda: {"available": True})
    monkeypatch.setattr(svc, "chat", lambda p: {"ok": True, "text": "巡检完成", "session_id": "s9"})
    t = _mk()
    sc.run_task_now(t, trigger="manual")
    run = sc.list_runs(t["id"])[0]
    assert run["status"] == "done" and run["output"] == "巡检完成"
    assert run["session_id"] == "s9" and run["trigger"] == "manual"
    assert sc.get_task(t["id"])["last_run"] > 0


def test_run_history_is_capped(monkeypatch):
    from app.services import ivyea_agent_service as svc
    monkeypatch.setattr(svc, "ensure_available", lambda: {"available": True})
    monkeypatch.setattr(svc, "chat", lambda p: {"ok": True, "text": "x"})
    monkeypatch.setattr(sc, "MAX_RUNS_KEPT", 3)
    t = _mk()
    for _ in range(5):
        sc.run_task_now(t)
    assert len(sc.list_runs(t["id"], limit=100)) == 3


def test_unavailable_agent_is_started_before_running(monkeypatch):
    """定时任务常在半夜触发，那时 daemon 未必还活着 —— 必须先尝试拉起来。

    实测踩过：手动触发只留下一条 "Connection refused"，因为 svc.chat 不像
    /chat/stream 那样先调 ensure_available。
    """
    from app.services import ivyea_agent_service as svc
    calls: list[str] = []
    monkeypatch.setattr(svc, "ensure_available",
                        lambda: calls.append("ensure") or {"available": True})
    monkeypatch.setattr(svc, "chat", lambda p: calls.append("chat") or {"ok": True, "text": "ok"})
    t = _mk()
    sc.run_task_now(t)
    assert calls == ["ensure", "chat"]      # 先确保可用，再跑


def test_unavailable_agent_records_a_clear_error(monkeypatch):
    """拉不起来就把话说清楚，别让用户对着 Connection refused 猜。"""
    from app.services import ivyea_agent_service as svc
    monkeypatch.setattr(svc, "ensure_available",
                        lambda: {"available": False, "error": "端口未监听"})
    monkeypatch.setattr(svc, "chat", lambda p: pytest.fail("agent 不可用时不该发起对话"))
    t = _mk()
    sc.run_task_now(t)
    run = sc.list_runs(t["id"])[0]
    assert run["status"] == "error" and "端口未监听" in run["error"]
