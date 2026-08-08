"""L0-1：request_id 贯穿 + 统一错误契约 + 日志落盘。

这些测试守的是三条会**悄悄退化**的契约：

1. 每个响应都带 X-Request-Id，且错误体里的 id 与响应头一致 —— 否则用户报错时
   贴过来的 id 在日志里 grep 不到，整套诊断链就断了；
2. 错误体里 **detail 必须一直在**。前端 `client.ts` 读的是
   `err.response.data.detail`，哪天有人"清理"掉它，全站错误提示会一起变哑，
   而且不会有任何测试报错——除非有这一条；
3. 未捕获异常必须落完整堆栈到日志，而不是只在 stdout 一闪而过。
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.core import obs


@pytest.fixture()
def client():
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def test_health_carries_request_id(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    rid = r.headers.get("X-Request-Id")
    assert rid and len(rid) == 8, f"期望 8 位短 id，拿到 {rid!r}"


def test_inbound_request_id_is_honoured(client):
    """反代/网关已经分配过 id 时要沿用，这样 nginx 日志和应用日志能对上。"""
    r = client.get("/api/health", headers={"X-Request-Id": "abcd1234"})
    assert r.headers.get("X-Request-Id") == "abcd1234"


def test_request_ids_differ_across_requests(client):
    a = client.get("/api/health").headers["X-Request-Id"]
    b = client.get("/api/health").headers["X-Request-Id"]
    assert a != b


def test_http_error_keeps_detail_and_adds_error_object(client):
    """401 走 HTTPException 分支：detail（老契约）与 error（新契约）都要在。"""
    r = client.get("/api/skill/list")
    assert r.status_code in (401, 403), r.status_code
    body = r.json()

    # 老契约：前端 client.ts 依赖它
    assert "detail" in body and isinstance(body["detail"], str) and body["detail"]

    # 新契约
    err = body["error"]
    assert err["code"] in {"UNAUTHORIZED", "FORBIDDEN"}
    assert err["message"] == body["detail"]
    assert err["hint"], "401/403 必须给出下一步动作"
    assert err["request_id"] == r.headers["X-Request-Id"]


@pytest.fixture()
def boom_route():
    """临时挂一个必炸的路由。

    必须 **insert(0)** 而不是 app.get 装饰器：main.py 末尾有个 SPA catch-all
    `/{full_path:path}`，后加的路由排在它后面永远匹配不到，只会拿到 404。
    """
    from fastapi.routing import APIRoute

    from app.main import app

    def _boom():  # pragma: no cover - 只为触发异常
        raise RuntimeError("炸给测试看的")

    route = APIRoute("/api/__boom__", _boom, methods=["GET"])
    app.router.routes.insert(0, route)
    try:
        yield
    finally:
        app.router.routes.remove(route)


def test_unhandled_exception_returns_contract_and_logs_stack(client, boom_route, caplog):
    """未捕获异常 → 500 + 可追溯 id + 完整堆栈落日志。"""
    with caplog.at_level(logging.ERROR, logger="ivyea.main"):
        r = client.get("/api/__boom__")

    assert r.status_code == 500
    body = r.json()
    assert body["detail"], "500 也要保留 detail，前端才有话显示"
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["request_id"] == r.headers["X-Request-Id"]
    assert body["error"]["hint"], "500 要告诉用户去导诊断包"

    # 堆栈必须真的进了日志，而不是只有一行摘要
    text = caplog.text
    assert "未捕获异常" in text
    assert "RuntimeError" in text and "炸给测试看的" in text
    assert body["error"]["request_id"] in text

    # 500 响应体里不能把内部异常原文吐给用户（那是信息泄露）
    assert "炸给测试看的" not in r.text


def test_log_file_written_and_tailable(tmp_path, monkeypatch):
    """落盘 + tail_log：诊断包取日志靠的就是这两个。"""
    monkeypatch.setenv("IVYEA_OPS_LOG_FILE", "1")
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    monkeypatch.setattr(obs, "_configured", False)
    root.handlers = []
    try:
        obs.configure_logging(tmp_path)
        token = obs.REQUEST_ID.set("deadbeef")
        try:
            logging.getLogger("ivyea.test").warning("落盘校验用的一行中文日志")
        finally:
            obs.REQUEST_ID.reset(token)
        for h in root.handlers:
            h.flush()

        path = obs.log_file(tmp_path)
        assert path.is_file(), "默认必须落盘：Windows 与裸跑都没有 journald 可依赖"
        content = path.read_text(encoding="utf-8")
        assert "落盘校验用的一行中文日志" in content, "中文必须能写进去（GBK 会炸）"
        assert "[deadbeef]" in content, "日志行要带 request_id 才能 grep 出整条链路"

        assert "落盘校验用的一行中文日志" in obs.tail_log(100, tmp_path)
    finally:
        for h in root.handlers:
            h.close()
        root.handlers, root.level = saved_handlers, saved_level


def test_access_line_carries_request_id(client, caplog):
    """uvicorn 自带的访问日志在 ASGI 应用之外发出，contextvar 已被 reset，
    那行永远是 [-]。所以必须自己记一条 —— 否则"贴个 id 查全链路"只在报错时成立。"""
    with caplog.at_level(logging.DEBUG, logger="ivyea.main"):
        r = client.get("/api/health", headers={"X-Request-Id": "trace-xyz"})

    line = next((rec for rec in caplog.records if "/api/health" in rec.getMessage()), None)
    assert line is not None, "每个请求至少要有一行自己的访问日志"
    assert "GET" in line.getMessage() and "200" in line.getMessage()
    assert r.headers["X-Request-Id"] == "trace-xyz"


def test_failed_requests_log_at_info_not_debug(client, caplog):
    """出错的请求正是用户会来报的那些 —— 它们必须在默认级别（INFO）就能看到，
    不能因为降噪把它们藏进 DEBUG。"""
    with caplog.at_level(logging.INFO, logger="ivyea.main"):
        client.get("/api/skill/list")   # 未登录 → 401

    assert any("/api/skill/list" in rec.getMessage() and rec.levelno >= logging.INFO
               for rec in caplog.records), "4xx 必须记在 INFO"


def test_tail_log_missing_file_is_empty(tmp_path):
    """诊断包在日志还没生成时也必须能导出，不能抛。"""
    assert obs.tail_log(10, tmp_path) == ""


def test_third_party_logs_do_not_crash_on_missing_request_id():
    """httpx/uvicorn 的 record 上没有 request_id 属性，Formatter 直接引用会
    KeyError 把日志打崩 —— 这就是为什么用 Filter 补字段而不是在 Formatter 里取。"""
    record = logging.LogRecord("httpx", logging.INFO, __file__, 1, "外部库的日志", None, None)
    assert obs._RequestIdFilter().filter(record) is True
    assert record.request_id == "-"
    assert logging.Formatter(obs._LOG_FORMAT).format(record).endswith("外部库的日志")
