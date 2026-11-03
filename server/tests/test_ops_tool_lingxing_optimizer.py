"""Agent 工具 lingxing_optimizer：启动后台规则引擎并等结果；超时交回 run_id，续查不重复启动。"""
from __future__ import annotations

import asyncio

from app.services import ivyea_ops_tools as tools


def _patch(monkeypatch, states):
    from app.routers import lingxing
    from app.services import lingxing_optimizer as lxopt
    started = []

    async def fake_start(sid, days=0):
        started.append((sid, days))
        return {"id": "r1", "status": "running", "phase": "启动中"}

    seq = iter(states)

    def fake_get(run_id):
        assert run_id == "r1"
        try:
            return next(seq)
        except StopIteration:
            return states[-1]

    async def no_sleep(_):
        return None

    monkeypatch.setattr(lingxing, "optimizer_run_start", fake_start)
    monkeypatch.setattr(lxopt, "get_opt_run", fake_get)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    return started


def test_starts_and_waits_for_result(monkeypatch):
    started = _patch(monkeypatch, [
        {"id": "r1", "status": "running"},
        {"id": "r1", "status": "done", "result": {"count": 3}},
    ])
    out = asyncio.run(tools._lingxing_optimizer({"sid": 7, "days": 30}))
    assert out["status"] == "done" and out["result"]["count"] == 3
    assert started == [(7, 30)]


def test_resume_by_run_id_does_not_start_new_run(monkeypatch):
    started = _patch(monkeypatch, [{"id": "r1", "status": "done", "result": {"count": 1}}])
    out = asyncio.run(tools._lingxing_optimizer({"run_id": "r1"}))
    assert out["status"] == "done"
    assert started == []


def test_returns_run_id_when_deadline_passes(monkeypatch):
    import time as _time
    started = _patch(monkeypatch, [{"id": "r1", "status": "running", "phase": "拉报表"}])
    clock = iter([0.0, 0.0, 1000.0, 1000.0, 1000.0])
    monkeypatch.setattr(_time, "monotonic", lambda: next(clock, 1000.0))
    out = asyncio.run(tools._lingxing_optimizer({"sid": 7}))
    assert out["status"] == "running" and "run_id=r1" in out["note"]
    assert started == [(7, 0)]
