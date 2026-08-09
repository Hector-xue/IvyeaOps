"""一键诊断包：把排障需要的东西打成一个 zip，**一个字节都不外发**。

为什么需要它
------------
IvyeaOps 是自托管的：用户把它装在自己的机器上。出问题时你既登不上他的服务器，
也不该往外发遥测（零遥测是这个产品的卖点）。在此之前，用户能提供的全部信息就是
一句"打不开"——于是每个 issue 都要来回问上十轮。

这个包把"你本来要问的十轮"一次性装齐：版本、平台、日志尾巴、**脱敏后的**配置、
各库的表和行数、纯本地的环境检查。用户自己点、自己看、自己决定要不要贴出来。

三条红线
--------
1. **不发请求**。这里没有任何 http 调用——连"测一下 key 还能不能用"都不做，
   因为诊断包正是隐私敏感用户会逐字审视的东西；
2. **不带密钥**。所有疑似凭据的字段一律替换成 ``***(len=N)``，只保留长度和前缀，
   够判断"是不是填错了/填了个空的"，但拿不去用；
3. **不带业务数据**。库只报表名和行数，不导出任何一行内容。
"""
from __future__ import annotations

import io
import json
import os
import platform
import re
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core import obs
from app.core.config import settings
from app.core.version import app_version

# 字段名命中这些词就当凭据处理。宁可多脱一个（大不了少一条排障线索），
# 也不能漏一个（漏了就是把用户的密钥发到 GitHub issue 上）。
_SECRET_HINT = re.compile(
    r"(key|secret|token|password|passwd|hash|credential|cookie|session|auth|sign)",
    re.IGNORECASE,
)

# 兜底：不管字段叫什么名字，长得像凭据的值也脱。比如某个 base_url 里塞了 token，
# 或者用户把 key 填进了一个名字完全无关的框里。
_SECRET_VALUE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{12,}"          # OpenAI 系
    r"|gh[pousr]_[A-Za-z0-9]{16,}"      # GitHub
    r"|xox[baprs]-[A-Za-z0-9\-]{10,}"   # Slack
    r"|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.)"  # JWT
)


def _mask(value: str) -> str:
    """``sk-abc…`` → ``sk-***(len=51)``：保留前缀与长度，够判断填没填错。"""
    if not value:
        return ""
    prefix = value[:3] if len(value) > 8 else ""
    return f"{prefix}***(len={len(value)})"


def redact(obj: Any, _key_hint: str = "") -> Any:
    """递归脱敏。字典、列表、字符串都要走到——配置里有嵌套的 provider 列表，
    只脱顶层等于没脱。"""
    if isinstance(obj, dict):
        return {k: redact(v, _key_hint=str(k)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v, _key_hint=_key_hint) for v in obj]
    if isinstance(obj, str):
        if obj and _SECRET_HINT.search(_key_hint or ""):
            return _mask(obj)
        if _SECRET_VALUE.search(obj):
            return _SECRET_VALUE.sub(lambda m: _mask(m.group(0)), obj)
        return obj
    return obj


def _versions() -> dict:
    info = {
        "ivyea_ops": app_version(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "data_dir": str(settings.data_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    # IvyeaAgent 的版本只从**本地**拿：读它的包元数据，不去 ping 它的 HTTP 服务。
    try:
        import ivyea_agent  # type: ignore

        info["ivyea_agent"] = getattr(ivyea_agent, "__version__", "unknown")
    except Exception as exc:  # 没装 agent 是正常情况，不是错误
        info["ivyea_agent"] = f"(未安装或不可导入: {type(exc).__name__})"
    return info


def _db_stats(data_dir: Path) -> dict:
    """每个库有哪些表、各多少行。**只报结构与计数，不导出任何一行内容。**"""
    out: dict[str, Any] = {}
    for path in sorted(list(data_dir.glob("*.sqlite3")) + list(data_dir.glob("*.db"))):
        entry: dict[str, Any] = {"size_bytes": path.stat().st_size}
        try:
            # 只读打开：诊断动作绝不能反过来改用户的库，也不该跟正在跑的服务抢锁。
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
            try:
                names = [
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                    )
                ]
                tables = {}
                for name in names:
                    try:
                        # 表名来自 sqlite_master，不是用户输入，但仍然加引号防怪名字。
                        cur = conn.execute(f'SELECT COUNT(*) FROM "{name}"')
                        tables[name] = int(cur.fetchone()[0])
                    except sqlite3.Error as exc:
                        tables[name] = f"(计数失败: {exc})"
                entry["tables"] = tables
            finally:
                conn.close()
        except sqlite3.Error as exc:
            entry["error"] = f"打不开: {exc}"
        out[path.name] = entry
    return out


def _local_checks(data_dir: Path) -> dict:
    """纯本地环境检查：**没有一个网络调用**。

    刻意不做"测一下 key 还通不通"这类探活——那会让诊断包本身产生外发请求，
    而这个包恰恰是隐私敏感用户会逐字检查的东西。
    """
    checks: dict[str, Any] = {}
    checks["data_dir_writable"] = os.access(data_dir, os.W_OK)
    checks["log_file_present"] = obs.log_file(data_dir).is_file()
    checks["file_logging_enabled"] = os.environ.get("IVYEA_OPS_LOG_FILE", "1")
    checks["log_level"] = os.environ.get("IVYEA_OPS_LOG_LEVEL", "INFO")

    try:
        from app.core.skill_paths import SKILLS_ROOT

        checks["skills_root"] = {"path": str(SKILLS_ROOT), "exists": SKILLS_ROOT.exists()}
    except Exception as exc:
        checks["skills_root"] = f"(读取失败: {type(exc).__name__})"

    import shutil

    checks["binaries"] = {
        name: (shutil.which(name) or "(不在 PATH 里)")
        for name in ("git", "ivyea", "node", "npm")
    }
    return checks


_README = """\
IvyeaOps 诊断包
================

这个 zip 是你在自己的机器上主动导出的，导出过程**没有向任何服务器发送数据**。

里面有什么
----------
  versions.json        版本、Python、操作系统、架构
  logs/                应用日志的尾部（默认最后 2000 行）
  config.redacted.json 配置项。所有密钥/令牌/密码已替换为 ***(len=N)，
                       只剩长度和前缀，用来判断"是不是填错了"，拿不去用
  db_stats.json        每个数据库有哪些表、各多少行 —— 不含任何一行内容
  local_checks.json    纯本地检查（目录可写、日志在不在、git/node 在不在 PATH）

里面没有什么
------------
  · 没有 API 密钥、令牌、密码原文
  · 没有你的店铺数据、ASIN、订单、广告报表
  · 没有聊天记录或知识库内容

贴到 GitHub issue 之前，建议你自己打开 config.redacted.json 扫一眼确认。
如果发现任何未被脱敏的敏感信息，那是 bug，请直接报给我们。
"""


def build_bundle(log_lines: int = 2000, data_dir: Path | None = None) -> bytes:
    """生成诊断包（内存 zip）。任一部分取不到都不该让整个包导不出来。"""
    directory = Path(data_dir) if data_dir is not None else settings.data_dir

    def _safe(label: str, fn):
        try:
            return fn()
        except Exception as exc:  # 单项失败不能连累整包
            return {"error": f"{label} 采集失败: {type(exc).__name__}: {exc}"}

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README-给开发者.txt", _README)
        zf.writestr("versions.json", json.dumps(_safe("versions", _versions),
                                                ensure_ascii=False, indent=2))

        def _config():
            from app.core import hub_settings

            return redact(hub_settings.load())

        zf.writestr("config.redacted.json", json.dumps(_safe("config", _config),
                                                       ensure_ascii=False, indent=2))
        zf.writestr("db_stats.json", json.dumps(_safe("db", lambda: _db_stats(directory)),
                                                ensure_ascii=False, indent=2))
        zf.writestr("local_checks.json",
                    json.dumps(_safe("checks", lambda: _local_checks(directory)),
                               ensure_ascii=False, indent=2))
        zf.writestr("logs/ivyea-ops.log", obs.tail_log(log_lines, directory))
    return buffer.getvalue()


def bundle_filename() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"ivyea-ops-diagnostic-{stamp}.zip"
