"""预算分级与顶栏用的缓存路径。

盯三件事：**顶栏绝不能触发全盘扫描**、**分级各提醒一次**、
**超预算只停自动任务不停手动**。
"""
from __future__ import annotations

import time

import pytest

from app.services import budget


@pytest.fixture(autouse=True)
def _clean_cache():
    budget._cache["value"] = None
    budget._cache["at"] = 0.0
    yield
    budget._cache["value"] = None
    budget._cache["at"] = 0.0


def _settings(monkeypatch, store):
    from app.core import hub_settings
    monkeypatch.setattr(hub_settings, "get", lambda k, *a: store.get(k, ""))
    monkeypatch.setattr(hub_settings, "save", lambda d: store.update(d) or store)


# ── 顶栏路径：只读缓存 ───────────────────────────────────────────────────
def test_cached_read_never_computes_inline(monkeypatch):
    """完整聚合本机实测 8.8 秒。顶栏在每个页面都在，让它现场开算，
    等于用户每开一个页面就给自己的机器来一次全盘扫描。"""
    called = []
    monkeypatch.setattr(budget, "_compute", lambda: called.append(1) or 1.0)
    monkeypatch.setattr(budget, "_refresh_async", lambda: None)   # 不起后台线程

    assert budget.month_spend_usd(cached=True) is None   # 还没算过
    assert called == []                                   # **一次都没现场算**


def test_cold_cache_reports_unknown_not_zero(monkeypatch):
    """算不出来时显示 $0 比显示「—」危险得多 —— 用户会以为这个月没花钱。"""
    _settings(monkeypatch, {"ai_budget_monthly_usd": 100})
    monkeypatch.setattr(budget, "_refresh_async", lambda: None)
    st = budget.status(cached=True)
    assert st["known"] is False
    assert st["level"] == "ok"        # 不知道就不该报警


def test_cache_hit_is_used_and_age_reported(monkeypatch):
    _settings(monkeypatch, {"ai_budget_monthly_usd": 100})
    budget._cache["value"] = 42.0
    budget._cache["at"] = time.time() - 90
    st = budget.status(cached=True)
    assert st["known"] is True and st["spend_usd"] == 42.0
    assert 80 <= st["age_seconds"] <= 100     # 新鲜度如实报出来


def test_refresh_does_not_stack_up(monkeypatch):
    """十个页面同时打开，不该同时开十次全盘扫描。"""
    starts = []
    monkeypatch.setattr(budget, "_compute", lambda: (time.sleep(0.3), 1.0)[1])
    import threading
    real = threading.Thread

    def counting(*a, **kw):
        starts.append(1)
        return real(*a, **kw)
    monkeypatch.setattr(threading, "Thread", counting)

    for _ in range(10):
        budget.month_spend_usd(cached=True)
    time.sleep(0.6)
    assert len(starts) == 1


# ── 分级 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("spend,expect", [(0, "ok"), (79, "ok"), (80, "warn"),
                                          (99, "warn"), (100, "exceeded"), (250, "exceeded")])
def test_levels(monkeypatch, spend, expect):
    _settings(monkeypatch, {"ai_budget_monthly_usd": 100})
    monkeypatch.setattr(budget, "month_spend_usd", lambda **kw: float(spend))
    assert budget.status()["level"] == expect


def test_no_budget_means_never_alarmed(monkeypatch):
    _settings(monkeypatch, {"ai_budget_monthly_usd": 0})
    monkeypatch.setattr(budget, "month_spend_usd", lambda **kw: 9999.0)
    st = budget.status()
    assert st["enabled"] is False and st["level"] == "ok"


def test_each_tier_notifies_once_but_both_tiers_fire(monkeypatch):
    """到 80% 提醒过，冲到 100% 还要再提醒一次 —— 那才是真正要他动手的时刻。
    如果两档共用一个"本月已提醒"标记，最关键的那条就永远发不出来。"""
    store = {"ai_budget_monthly_usd": 100, "ai_budget_alerted_month": ""}
    _settings(monkeypatch, store)
    sent = []
    from app.services import notify
    monkeypatch.setattr(notify, "send_sync", lambda ev, title, body="", **kw: sent.append(title) or True)

    monkeypatch.setattr(budget, "month_spend_usd", lambda **kw: 85.0)
    budget.check_and_notify()
    budget.check_and_notify()          # 同一档不重复
    assert len(sent) == 1

    monkeypatch.setattr(budget, "month_spend_usd", lambda **kw: 120.0)
    budget.check_and_notify()
    budget.check_and_notify()
    assert len(sent) == 2
    assert "超预算" in sent[1]


# ── 只停自动，不停手动 ───────────────────────────────────────────────────
def test_auto_tasks_pause_only_when_over_and_known(monkeypatch):
    _settings(monkeypatch, {"ai_budget_monthly_usd": 100})

    budget._cache["value"] = 120.0
    budget._cache["at"] = time.time()
    assert budget.auto_tasks_paused() is True

    budget._cache["value"] = 20.0
    assert budget.auto_tasks_paused() is False


def test_unknown_spend_does_not_pause_anything(monkeypatch):
    """缓存还没算出来就把用户的定时任务全停了，是拿一个还不存在的数
    去做最重的决定。宁可多花一点。"""
    _settings(monkeypatch, {"ai_budget_monthly_usd": 100})
    monkeypatch.setattr(budget, "_refresh_async", lambda: None)
    assert budget.auto_tasks_paused() is False


def test_no_budget_never_pauses(monkeypatch):
    _settings(monkeypatch, {"ai_budget_monthly_usd": 0})
    budget._cache["value"] = 99999.0
    budget._cache["at"] = time.time()
    assert budget.auto_tasks_paused() is False


def test_scheduler_skip_is_recorded_not_silent(monkeypatch, tmp_path):
    """定时任务该跑没跑，用户第一反应是"坏了"。必须在历史里看到原因。"""
    from app.services import schedules
    calls = []
    monkeypatch.setattr(schedules, "_start_run", lambda tid, trig: calls.append(("start", tid)) or "r1")
    monkeypatch.setattr(schedules, "_finish_run",
                        lambda rid, tid, **kw: calls.append(("finish", kw.get("status"), kw.get("output"))))
    schedules._skip_run("t1", "本月 AI 花费已超预算")
    assert calls[0][0] == "start"
    assert calls[1][1] == "skipped" and "超预算" in calls[1][2]


def test_budget_failure_never_takes_down_the_scheduler(monkeypatch):
    """预算读不到就当没超 —— 它不该有能力停掉整个调度循环。"""
    from app.services import schedules
    monkeypatch.setattr(budget, "auto_tasks_paused",
                        lambda: (_ for _ in ()).throw(RuntimeError("挂了")))
    assert schedules._budget_paused() is False
