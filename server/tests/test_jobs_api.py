"""任务账本的接口。

单独一个文件：同一个模块里混 anyio 用例和 TestClient，在 py3.9 上会因为事件
循环已经被关掉而报 "There is no current event loop"。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import jobs


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    jobs.init_db()

    # anyio 的用例（同一次 pytest 里跑的 test_jobs_engine）结束时会关掉事件循环
    # 并把 current loop 置空；py3.9 的 TestClient 走 asyncio.get_event_loop()，
    # 这时候就抛 "There is no current event loop"。这里补一个干净的循环，
    # 让本文件不依赖测试执行顺序。
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    yield tmp_path


# ── 用例 ────────────────────────────────────────────────────────────────

def test_jobs_endpoint_requires_admin():
    from app.main import app

    assert TestClient(app).get("/api/admin/jobs").status_code in (401, 403)


def test_jobs_endpoint_surfaces_orphans(_isolated):
    """孤儿必须在接口里显形——这正是"任务凭空消失"的解药。"""
    from app.core import security as sec
    from app.main import app

    jid = jobs.create("lingxing.write", retriable=False)
    jobs.claim(jid, lease_seconds=-1)
    jobs.recover_orphans()

    app.dependency_overrides[sec.require_admin] = lambda: "admin"
    try:
        body = TestClient(app).get("/api/admin/jobs").json()
        assert body["orphaned"] == 1
        row = next(r for r in body["rows"] if r["id"] == jid)
        assert row["status"] == jobs.ORPHANED
        assert "重启" in row["error"]
    finally:
        app.dependency_overrides.pop(sec.require_admin, None)
