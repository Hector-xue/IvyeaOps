"""集中式可观测性：日志配置、落盘、request_id 贯穿。

为什么要有这个文件
------------------
IvyeaOps 是**自托管**产品：用户把它装在自己的机器上，出问题时你既拿不到他的
服务器，也不该往外发任何遥测（零遥测是这个产品的卖点，不是缺陷）。在此之前
6.4 万行后端只有 43 处 logger 调用、69 处 print，日志既不落盘也不带任何请求
标识 —— 于是用户能给你的全部信息就是一句"打不开"。

这里做三件事，全部在用户本机完成：

1. **一处配置日志**（此前散落在 main.py 的 basicConfig），级别可由
   ``IVYEA_OPS_LOG_LEVEL`` 覆盖；
2. **落盘**到 ``data/logs/ivyea-ops.log``（10MB × 5 轮转）。默认开启：Windows
   没有 journald，systemd 之外的启动方式（双击 bat、Docker 之外的裸跑）也一样
   拿不到 stdout。可用 ``IVYEA_OPS_LOG_FILE=0`` 关掉；
3. **request_id**：每个请求一个短 id，写进每一行日志、回给前端（``X-Request-Id``
   响应头 + 错误体），用户报错时只要贴这一个 id，就能在日志里 grep 到整条链路。

配套的是 ``/api/health/diagnostic-bundle``（诊断包）—— 用户主动点一下才导出，
仍然一个字节都不外发。
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import uuid
from contextvars import ContextVar
from pathlib import Path

# 当前请求的 id。中间件在每个请求入口 set，日志过滤器读它。
# 非请求上下文（启动、后台任务、定时调度）读到空串，日志里显示为 "-"。
REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="")

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s"
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5

_configured = False


def new_request_id() -> str:
    """短 id：8 位十六进制。够区分并发请求，又短到用户愿意手打/复制。"""
    return uuid.uuid4().hex[:8]


def get_request_id() -> str:
    return REQUEST_ID.get() or ""


class _RequestIdFilter(logging.Filter):
    """给每条日志补上 request_id 字段。

    必须是 Filter 而不是 Formatter 里取 —— 第三方库（uvicorn、httpx）的 record
    上没有这个属性，Formatter 直接引用会 KeyError 把日志打崩。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id() or "-"
        return True


def log_dir(data_dir: Path | None = None) -> Path:
    if data_dir is None:
        from app.core.config import settings
        data_dir = settings.data_dir
    return Path(data_dir) / "logs"


def log_file(data_dir: Path | None = None) -> Path:
    return log_dir(data_dir) / "ivyea-ops.log"


def _file_logging_enabled() -> bool:
    return os.environ.get("IVYEA_OPS_LOG_FILE", "1").lower() not in {"0", "false", "no"}


def configure_logging(data_dir: Path | None = None) -> None:
    """幂等地配置根 logger。重复调用只会重建一次 handler。"""
    global _configured
    if _configured:
        return

    level_name = os.environ.get("IVYEA_OPS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(_LOG_FORMAT)
    rid_filter = _RequestIdFilter()

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream.addFilter(rid_filter)
    root.addHandler(stream)

    if _file_logging_enabled():
        try:
            directory = log_dir(data_dir)
            directory.mkdir(parents=True, exist_ok=True)
            # encoding 必须显式给：中文 Windows 默认 GBK，日志里但凡有中文就
            # UnicodeEncodeError（这个仓库为此已经统一过一轮编码纪律）。
            file_handler = logging.handlers.RotatingFileHandler(
                str(log_file(data_dir)),
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            file_handler.addFilter(rid_filter)
            root.addHandler(file_handler)
        except OSError as exc:
            # 只读挂载 / 权限不足 / 磁盘满：日志落不了盘不该让服务起不来，
            # 降级为只输出到 stdout，并把原因说清楚。
            root.warning("日志落盘不可用，仅输出到控制台：%s", exc)

    # uvicorn 自带 handler，不关掉会导致每条访问日志打印两遍。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True

    _configured = True


def tail_log(lines: int = 2000, data_dir: Path | None = None) -> str:
    """取日志尾部若干行，供诊断包使用。文件不存在时返回空串而不是抛错。"""
    path = log_file(data_dir)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-lines:])
    except FileNotFoundError:
        return ""
    except OSError as exc:
        return f"(读取日志失败: {exc})\n"
