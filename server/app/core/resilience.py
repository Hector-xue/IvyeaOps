"""失败分类与自动恢复。

为什么需要它
------------
IvyeaOps 的每一条业务链路都要跨出去：模型 API、领星 OpenAPI、Sorftime、卖家精灵、
本地 agent。这些调用的失败**性质完全不同**，但代码里现在一律是
``except Exception: pass`` 或者 ``except Exception as e: return {"error": str(e)}``
—— 于是"网络抖了一下"和"密钥过期了"对用户来说长得一模一样，而前者重试一次就好，
后者重试一百次也没用，只会白烧配额。

这个模块把失败分成五类，每类给一个确定的处置：

===========  ==========================================  ====================
类别          典型情形                                    处置
===========  ==========================================  ====================
TRANSIENT    连接重置、超时、502/503/504                  指数退避重试
RATE_LIMIT   429、"rate limit"、"too many requests"       按 Retry-After 退避重试
AUTH         401/403、"invalid api key"、签名失败          **立刻停**，重试无意义
QUOTA        402、"insufficient balance"、余额不足         **立刻停**，重试只会更糟
PARAM        400/422、schema 校验失败                      交调用方自修一次，不盲目重试
FATAL        其它                                          上抛
===========  ==========================================  ====================

对标 MyLevis 公开的"识别参数错误、调用超时和服务异常，自动进行修正、重试或降级"。
区别在于我们把"哪些**不该**重试"也写死了 —— 对自托管用户来说，把他自己的 API 余额
在一个必然失败的调用上重试烧光，比直接报错难受得多。
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from enum import Enum
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger("ivyea.resilience")


class Failure(str, Enum):
    TRANSIENT = "transient"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    QUOTA = "quota"
    PARAM = "param"
    FATAL = "fatal"


#: 重试这几类是有意义的；其余重试只是浪费时间和用户的配额。
RETRYABLE = frozenset({Failure.TRANSIENT, Failure.RATE_LIMIT})

# 文本特征。上游返回的 HTTP 码经常不老实（见过 rate limit 用 200 包一个 error 体
# 送回来的），所以码和文本都要看。
_PATTERNS: Tuple[Tuple[Failure, re.Pattern], ...] = (
    (Failure.RATE_LIMIT, re.compile(r"rate.?limit|too many requests|429|请求过于频繁|限流", re.I)),
    (Failure.QUOTA, re.compile(r"insufficient|quota exceeded|balance|欠费|余额不足|额度不足", re.I)),
    (Failure.AUTH, re.compile(r"unauthor|forbidden|invalid.{0,10}(api.?key|token|secret)"
                              r"|signature|鉴权|密钥无效|未授权", re.I)),
    (Failure.PARAM, re.compile(r"invalid.{0,15}(param|argument|request|schema)|validation"
                               r"|missing required|参数.{0,4}(错误|无效|缺)", re.I)),
    (Failure.TRANSIENT, re.compile(r"timed? ?out|timeout|connection (reset|aborted|refused)"
                                   r"|temporarily|unavailable|bad gateway|超时|连接失败", re.I)),
)

_STATUS_MAP = {
    400: Failure.PARAM, 401: Failure.AUTH, 403: Failure.AUTH,
    402: Failure.QUOTA, 422: Failure.PARAM, 429: Failure.RATE_LIMIT,
    500: Failure.TRANSIENT, 502: Failure.TRANSIENT,
    503: Failure.TRANSIENT, 504: Failure.TRANSIENT,
}


def _status_of(exc: BaseException) -> Optional[int]:
    """从各家异常里把 HTTP 状态码抠出来（httpx / urllib / FastAPI 各不相同）。"""
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value < 600:
            return value
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def classify(exc: BaseException) -> Failure:
    """把异常归到五类之一。

    **状态码优先于文本**：一个 401 的响应体里完全可能带着 "timeout" 字样
    （比如网关把上游超时包成鉴权失败的模板），认文本会把"该停"误判成"该重试"。
    """
    status = _status_of(exc)
    if status is not None and status in _STATUS_MAP:
        return _STATUS_MAP[status]

    # asyncio/socket 的超时类异常没有状态码，但语义明确。
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return Failure.TRANSIENT
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return Failure.PARAM

    text = f"{type(exc).__name__}: {exc}"
    for kind, pattern in _PATTERNS:
        if pattern.search(text):
            return kind
    return Failure.FATAL


def backoff_delay(attempt: int, *, base: float = 0.5, cap: float = 30.0) -> float:
    """指数退避 + 抖动。

    抖动不是装饰：定时任务里一批调用往往在同一秒发起，同步退避会让它们
    继续踩着同一个节拍撞上去，把一次限流拖成一串限流。
    """
    raw = min(cap, base * (2 ** max(0, attempt - 1)))
    return round(raw * (0.5 + random.random() / 2), 3)


class Outcome:
    """一次带恢复的调用结果。``ok`` 为假时，``kind`` 说明为什么放弃。"""

    __slots__ = ("ok", "value", "kind", "error", "attempts", "degraded")

    def __init__(self, ok: bool, value: Any = None, kind: Optional[Failure] = None,
                 error: Optional[BaseException] = None, attempts: int = 0,
                 degraded: bool = False) -> None:
        self.ok, self.value, self.kind = ok, value, kind
        self.error, self.attempts, self.degraded = error, attempts, degraded

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (f"Outcome(ok={self.ok}, kind={self.kind}, attempts={self.attempts}, "
                f"degraded={self.degraded})")


async def call_with_recovery(
    fn: Callable[[], Any],
    *,
    retries: int = 3,
    fallback: Optional[Callable[[], Any]] = None,
    label: str = "",
    sleep: Optional[Callable[[float], Any]] = None,
) -> Outcome:
    """调用 ``fn``，按失败类别决定重试 / 降级 / 立即放弃。

    ``fn`` 同步异步都行。``fallback`` 是降级路径（例如"读本地缓存"），只有在
    重试用尽或失败类别不可重试时才会走 —— 而且**降级成功要标记出来**，
    因为"用的是缓存数据"这件事必须让用户看得见，不能假装是新鲜结果。
    """
    napper = sleep or asyncio.sleep
    last_exc: Optional[BaseException] = None
    kind = Failure.FATAL
    attempts = 0

    for attempt in range(1, max(1, retries) + 1):
        attempts = attempt
        try:
            result = fn()
            if asyncio.iscoroutine(result):
                result = await result
            return Outcome(True, result, attempts=attempt)
        except Exception as exc:  # noqa: BLE001 - 分类器就是干这个的
            last_exc, kind = exc, classify(exc)
            if kind not in RETRYABLE:
                logger.warning("%s 失败(%s)，该类别重试无意义，直接停：%s",
                               label or fn.__class__.__name__, kind.value, exc)
                break
            if attempt >= retries:
                logger.warning("%s 重试 %s 次仍失败(%s)：%s", label, attempt, kind.value, exc)
                break
            delay = backoff_delay(attempt)
            logger.info("%s 第 %s 次失败(%s)，%.2fs 后重试：%s",
                        label, attempt, kind.value, delay, exc)
            await napper(delay)

    if fallback is not None:
        try:
            fb = fallback()
            if asyncio.iscoroutine(fb):
                fb = await fb
            logger.info("%s 走降级路径返回（数据可能不是最新的）", label)
            return Outcome(True, fb, kind=kind, error=last_exc,
                           attempts=attempts, degraded=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s 降级路径也失败了：%s", label, exc)

    return Outcome(False, None, kind=kind, error=last_exc, attempts=attempts)


def call_with_recovery_sync(fn: Callable[[], Any], **kwargs: Any) -> Outcome:
    """同步版本，给还没 async 化的调用点用。"""
    kwargs.setdefault("sleep", time.sleep)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(call_with_recovery(fn, **kwargs))
    finally:
        loop.close()
