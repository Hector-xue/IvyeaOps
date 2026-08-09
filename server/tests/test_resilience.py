"""L0-2b：失败分类与自动恢复。

这里守的核心不是"能重试"，而是**该停的时候必须停**：
密钥过期、余额不足这两类，重试一百次也不会成功，只会把用户自己的 API 配额烧光。
自托管场景下这比多报一个错难受得多。
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.resilience import (
    Failure,
    RETRYABLE,
    backoff_delay,
    call_with_recovery,
    classify,
)


class _HTTPish(Exception):
    """模拟 httpx/urllib 那种带 status_code 的异常。"""

    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code


# ── 分类 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,expected", [
    (400, Failure.PARAM), (401, Failure.AUTH), (402, Failure.QUOTA),
    (403, Failure.AUTH), (422, Failure.PARAM), (429, Failure.RATE_LIMIT),
    (502, Failure.TRANSIENT), (503, Failure.TRANSIENT), (504, Failure.TRANSIENT),
])
def test_classify_by_status(status, expected):
    assert classify(_HTTPish(status)) is expected


@pytest.mark.parametrize("message,expected", [
    ("Rate limit exceeded, retry later", Failure.RATE_LIMIT),
    ("请求过于频繁", Failure.RATE_LIMIT),
    ("Insufficient balance", Failure.QUOTA),
    ("账户余额不足", Failure.QUOTA),
    ("Invalid api key provided", Failure.AUTH),
    ("signature verification failed", Failure.AUTH),
    ("connection reset by peer", Failure.TRANSIENT),
    ("上游超时", Failure.TRANSIENT),
])
def test_classify_by_text_when_no_status(message, expected):
    assert classify(Exception(message)) is expected


def test_status_wins_over_text():
    """网关常把上游超时包成鉴权失败的模板返回 —— 认文本会把"该停"误判成"该重试"，
    白烧用户配额。所以状态码优先。"""
    exc = _HTTPish(401, "upstream timeout while validating token")
    assert classify(exc) is Failure.AUTH
    assert classify(exc) not in RETRYABLE


def test_timeout_types_are_transient():
    assert classify(asyncio.TimeoutError()) is Failure.TRANSIENT
    assert classify(ConnectionError("boom")) is Failure.TRANSIENT


def test_unknown_is_fatal_not_retryable():
    assert classify(RuntimeError("说不清的错")) is Failure.FATAL
    assert Failure.FATAL not in RETRYABLE


# ── 退避 ────────────────────────────────────────────────────────────────

def test_backoff_grows_and_is_capped():
    lows = [backoff_delay(i, base=1.0, cap=10.0) for i in range(1, 8)]
    assert lows[0] < lows[3], "应该是指数增长"
    assert all(d <= 10.0 for d in lows), "必须封顶，否则一次抖动能让任务睡到天亮"


def test_backoff_is_jittered():
    """定时任务里一批调用常在同一秒发起；同步退避会让它们继续踩同一个节拍
    撞上去，把一次限流拖成一串限流。"""
    values = {backoff_delay(3, base=1.0) for _ in range(30)}
    assert len(values) > 1, "没有抖动"


# ── 恢复 ────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_transient_is_retried_until_success():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _HTTPish(503)
        return "拿到了"

    out = await call_with_recovery(flaky, retries=5, label="test", sleep=_nosleep)
    assert out.ok and out.value == "拿到了"
    assert out.attempts == 3
    assert out.degraded is False


@pytest.mark.anyio
async def test_auth_failure_is_not_retried():
    """这是这个文件里最重要的一条：密钥过期重试一百次也不会成功。"""
    calls = {"n": 0}

    def bad_key():
        calls["n"] += 1
        raise _HTTPish(401, "invalid api key")

    out = await call_with_recovery(bad_key, retries=5, label="test", sleep=_nosleep)
    assert out.ok is False
    assert out.kind is Failure.AUTH
    assert calls["n"] == 1, f"鉴权失败重试了 {calls['n']} 次 —— 白烧用户配额"


@pytest.mark.anyio
async def test_quota_failure_is_not_retried():
    calls = {"n": 0}

    def no_balance():
        calls["n"] += 1
        raise _HTTPish(402, "insufficient balance")

    out = await call_with_recovery(no_balance, retries=5, label="test", sleep=_nosleep)
    assert out.ok is False and out.kind is Failure.QUOTA
    assert calls["n"] == 1


@pytest.mark.anyio
async def test_retries_are_bounded():
    calls = {"n": 0}

    def always_timeout():
        calls["n"] += 1
        raise _HTTPish(504)

    out = await call_with_recovery(always_timeout, retries=3, label="test", sleep=_nosleep)
    assert out.ok is False
    assert calls["n"] == 3, "重试次数必须封顶"


@pytest.mark.anyio
async def test_fallback_is_used_and_flagged_as_degraded():
    """降级成功必须**标记出来** —— "这是缓存数据"不能假装成新鲜结果。"""
    out = await call_with_recovery(
        lambda: (_ for _ in ()).throw(_HTTPish(503)),
        retries=2, fallback=lambda: "缓存里的旧数据", label="test", sleep=_nosleep,
    )
    assert out.ok is True
    assert out.value == "缓存里的旧数据"
    assert out.degraded is True, "降级必须可见，否则用户会把旧数据当新数据用"
    assert out.kind is Failure.TRANSIENT


@pytest.mark.anyio
async def test_fallback_also_runs_for_non_retryable_failures():
    """密钥过期时不重试，但如果有缓存，仍然应该让用户看到点东西。"""
    out = await call_with_recovery(
        lambda: (_ for _ in ()).throw(_HTTPish(401)),
        retries=3, fallback=lambda: "缓存", label="test", sleep=_nosleep,
    )
    assert out.ok and out.degraded and out.kind is Failure.AUTH
    assert out.attempts == 1


@pytest.mark.anyio
async def test_failing_fallback_does_not_mask_the_original_failure():
    def bad_fallback():
        raise OSError("缓存也读不了")

    out = await call_with_recovery(
        lambda: (_ for _ in ()).throw(_HTTPish(503)),
        retries=1, fallback=bad_fallback, label="test", sleep=_nosleep,
    )
    assert out.ok is False
    assert out.kind is Failure.TRANSIENT
    assert isinstance(out.error, _HTTPish), "报出来的应该是原始失败，不是降级路径的失败"


@pytest.mark.anyio
async def test_async_callables_are_supported():
    async def works():
        return 42

    out = await call_with_recovery(works, label="test", sleep=_nosleep)
    assert out.ok and out.value == 42


async def _nosleep(_seconds: float) -> None:
    """测试里不真的睡 —— 否则退避会把测试拖成分钟级。"""
    return None


@pytest.fixture
def anyio_backend():
    return "asyncio"
