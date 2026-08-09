"""任务持久化引擎。

这批里最要紧的是**启动自愈**那几条：在此之前，服务一重启，正在跑的 listing 渲染
/ 深度分析 / 广告审计就静默消失（子进程没了、磁盘状态永远停在 running，开机扫描
再把它们改成 failed）。用户看到的是"任务凭空不见了"，而且无从查起。

所以这里钉死两件事：
  · 被重启打断 ≠ 执行失败，状态必须是 orphaned 而不是 failed；
  · 改了外部系统的任务（写操作）**绝不能**自动重跑。
"""
from __future__ import annotations

import time

import pytest

from app.core import jobs


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    jobs.init_db()
    yield tmp_path


# ── 基本流转 ────────────────────────────────────────────────────────────

def test_create_and_get():
    jid = jobs.create("listing.render", {"project": "p1"})
    job = jobs.get(jid)
    assert job["status"] == jobs.QUEUED
    assert job["kind"] == "listing.render"
    assert job["payload"] == {"project": "p1"}
    assert job["progress"] == 0


def test_claim_then_finish():
    jid = jobs.create("x")
    assert jobs.claim(jid) is True
    assert jobs.get(jid)["status"] == jobs.RUNNING
    jobs.finish(jid, result={"rows": 3})
    job = jobs.get(jid)
    assert job["status"] == jobs.SUCCEEDED
    assert job["result"] == {"rows": 3}
    assert job["progress"] == 100
    assert job["finished_at"]


def test_a_running_job_cannot_be_claimed_twice():
    jid = jobs.create("x")
    assert jobs.claim(jid) is True
    assert jobs.claim(jid) is False, "同一个任务不能被两个执行者同时认领"


def test_progress_also_renews_the_lease():
    """有进度就说明还活着 —— 让调用方不必记得"上报进度"和"续租"两件事。"""
    jid = jobs.create("x")
    jobs.claim(jid, lease_seconds=5)
    before = jobs.get(jid)["lease_until"]
    time.sleep(1.1)
    jobs.progress(jid, 42, "跑到一半", lease_seconds=60)
    job = jobs.get(jid)
    assert job["progress"] == 42
    assert job["message"] == "跑到一半"
    assert job["lease_until"] > before


def test_fail_records_the_error():
    jid = jobs.create("x")
    jobs.claim(jid)
    jobs.fail(jid, "上游 502")
    job = jobs.get(jid)
    assert job["status"] == jobs.FAILED and "502" in job["error"]


def test_cancel_does_not_touch_terminal_jobs():
    jid = jobs.create("x")
    jobs.claim(jid)
    jobs.finish(jid)
    assert jobs.cancel(jid) is False, "已完成的任务不该被改成取消"
    assert jobs.get(jid)["status"] == jobs.SUCCEEDED


# ── 启动自愈（这一节是重点）────────────────────────────────────────────

def test_expired_lease_is_requeued_when_retriable():
    jid = jobs.create("listing.render", max_attempts=3)
    jobs.claim(jid, lease_seconds=-1)          # 造一个"租约已过期"的现场

    assert jobs.recover_orphans() == {"requeued": 1, "orphaned": 0}
    assert jobs.get(jid)["status"] == jobs.QUEUED


def test_write_operations_are_never_auto_retried():
    """改了外部系统的任务（比如往领星写否定关键词）被重启打断后绝不能自动重跑
    —— 那是在用户不知情的情况下把同一个写操作又执行一遍。"""
    jid = jobs.create("lingxing.write", retriable=False)
    jobs.claim(jid, lease_seconds=-1)

    assert jobs.recover_orphans() == {"requeued": 0, "orphaned": 1}
    assert jobs.get(jid)["status"] == jobs.ORPHANED


def test_interrupted_is_orphaned_not_failed():
    """"被重启打断"和"执行失败"是两件事，混为一谈用户就永远查不出原因。"""
    jid = jobs.create("x", retriable=False)
    jobs.claim(jid, lease_seconds=-1)
    jobs.recover_orphans()

    job = jobs.get(jid)
    assert job["status"] == jobs.ORPHANED
    assert job["status"] != jobs.FAILED
    assert "重启" in job["error"], "错误信息要说清是被打断，不是跑挂了"


def test_exhausted_attempts_become_orphans_rather_than_looping():
    jid = jobs.create("x", max_attempts=1)
    jobs.claim(jid, lease_seconds=-1)
    assert jobs.recover_orphans() == {"requeued": 0, "orphaned": 1}


def test_live_jobs_are_left_alone():
    """租约还没过期的任务是**别的进程正在跑的**，自愈绝不能碰。"""
    jid = jobs.create("x")
    jobs.claim(jid, lease_seconds=300)
    assert jobs.recover_orphans() == {"requeued": 0, "orphaned": 0}
    assert jobs.get(jid)["status"] == jobs.RUNNING


def test_a_takeover_is_possible_after_the_lease_expires():
    """租约过期后别的 worker 能安全接管 —— 判断"还有人在跑吗"从此有依据，
    而不是靠"内存里的 _live_jobs 是空的"这种只在单进程下成立的假设。"""
    jid = jobs.create("x", max_attempts=3)
    jobs.claim(jid, lease_seconds=-1)
    assert jobs.claim(jid) is True
    assert jobs.get(jid)["attempt"] == 2


# ── 执行封装 ────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_run_finishes_and_stores_result():
    jid = jobs.create("x")

    def work(job_id):
        jobs.progress(job_id, 50, "半程")
        return {"ok": 1}

    out = await jobs.run(jid, work)
    assert out["ok"] is True
    assert jobs.get(jid)["result"] == {"ok": 1}


@pytest.mark.anyio
async def test_run_turns_exceptions_into_failed_jobs():
    """一个任务的异常不能把调度循环带走。"""
    jid = jobs.create("x")

    def boom(_job_id):
        raise ValueError("炸了")

    out = await jobs.run(jid, boom)
    assert out["ok"] is False
    job = jobs.get(jid)
    assert job["status"] == jobs.FAILED
    assert "ValueError" in job["error"]


@pytest.mark.anyio
async def test_run_supports_async_work():
    jid = jobs.create("x")

    async def work(_job_id):
        return "done"

    assert (await jobs.run(jid, work))["result"] == "done"


# ── 查询与清理 ──────────────────────────────────────────────────────────

def test_list_filters():
    jobs.create("a")
    b = jobs.create("b")
    jobs.claim(b)
    assert len(jobs.list_jobs(kind="a")) == 1
    assert len(jobs.list_jobs(status=jobs.RUNNING)) == 1


def test_purge_only_removes_old_terminal_jobs():
    keep_running = jobs.create("x")
    jobs.claim(keep_running)

    old = jobs.create("x")
    jobs.claim(old)
    jobs.finish(old)
    import sqlite3
    conn = sqlite3.connect(jobs._db_path())
    conn.execute("UPDATE jobs SET finished_at=? WHERE id=?",
                 (int(time.time()) - 40 * 86400, old))
    conn.commit(); conn.close()

    assert jobs.purge(older_than_days=30) == 1
    assert jobs.get(old) is None
    assert jobs.get(keep_running) is not None, "在跑的任务不能被清掉"


@pytest.fixture
def anyio_backend():
    return "asyncio"

