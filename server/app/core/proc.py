"""子进程统一入口：跨平台细节 + **凭据不外泄**。

原来这个文件只管一件事（Windows 上别弹黑窗）。现在它还管更要紧的一件：

**子进程默认读不到 IvyeaOps 的凭据。**

为什么必须做（实测，不是假设）
------------------------------
``core/config`` 启动时 ``load_dotenv()`` 会把 ``server/.env`` 灌进 ``os.environ``，
而 README 正是建议把密钥放那儿。于是任何子进程都能直接读到：

    IVYEA_OPS_SECRET          ← **会话签名密钥**，拿到就能伪造管理员 cookie
    IVYEA_OPS_PASSWORD_HASH   ← 管理员密码哈希，可离线爆破
    IVYEA_OPS_ALERT_APP_SECRET
    SORFTIME_KEY

而全仓有 ~69 处 spawn，其中不少跑的是**不完全可控的代码**：终端里用户敲的命令、
agents 里 AI 自己决定的工具调用、skills 目录下 63 个可执行脚本、外部 MCP server。
也就是说，装一个社区 skill 就等于把会话签名密钥交出去。

（顺带说明为什么 ``/proc/<pid>/environ`` 看不出这个问题：那是 exec 时的快照，
反映不出 ``load_dotenv`` 之后 putenv 的改动。判据只有一个 —— 真起一个子进程看
它读到什么。）

设计
----
* **默认剥离**：名字像凭据的一律不传给子进程。
* **要用就显式声明**：``child_env(allow=["IVYEA_API_TOKEN"])`` —— 谁需要谁自己写
  出来，读代码的人一眼能看见"这个子进程能碰到哪些秘密"。
* **留逃生舱**：``IVYEA_OPS_CHILD_ENV_ALLOW`` 逗号分隔，给"我的某个工具就是靠
  环境变量拿 key"的用户兜底，不至于升级后突然坏掉且无法自救。
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from typing import Iterable, Mapping, Optional

logger = logging.getLogger("ivyea.core.proc")

# subprocess.CREATE_NO_WINDOW exists only on the Windows build of the stdlib.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

# 名字像凭据就不往下传。判据和 secrets / diagnostics 那两处保持一致 ——
# 三处口径不一样的话，就会出现"日志里脱了、备份里脱了、子进程却拿得到"这种
# 最难发现的漏法。
_SECRET_NAME = re.compile(
    r"(KEY|SECRET|TOKEN|PASSWORD|PASSWD|HASH|CREDENTIAL|COOKIE)", re.IGNORECASE
)

# 这些名字里带敏感词，但剥掉会把正常功能弄坏，且本身不是凭据。
_KEEP_ANYWAY = {
    "SSH_AUTH_SOCK",     # 转发的是 agent socket，不是密钥本身；git 推送要用
    "GPG_TTY",
    "KEYBOARD",          # 罕见但确实有环境这么设
    "XDG_SESSION_COOKIE",  # 桌面会话标识，不是凭据
}


def no_window_kwargs() -> dict:
    """kwargs to merge into subprocess.run / Popen / asyncio.create_subprocess_exec
    so the child process has no visible console window on Windows. {} elsewhere."""
    if sys.platform == "win32":
        return {"creationflags": _CREATE_NO_WINDOW}
    return {}


def _extra_allow() -> set:
    raw = os.environ.get("IVYEA_OPS_CHILD_ENV_ALLOW", "")
    return {n.strip() for n in raw.split(",") if n.strip()}


def is_secret_env(name: str) -> bool:
    if name in _KEEP_ANYWAY:
        return False
    return bool(_SECRET_NAME.search(name or ""))


def child_env(
    *,
    allow: Iterable[str] = (),
    base: Optional[Mapping[str, str]] = None,
    extra: Optional[Mapping[str, str]] = None,
) -> dict:
    """给子进程用的环境变量：默认剥掉所有像凭据的名字。

    ``allow``  这个子进程确实需要的凭据变量名（显式写出来，读代码能一眼看见）。
    ``extra``  额外注入的变量（比如给 agent 的一次性 token），不受剥离影响 ——
               显式塞进去的就是调用方有意为之。
    """
    source = os.environ if base is None else base
    allowed = set(allow) | _extra_allow()
    env = {k: v for k, v in source.items() if k in allowed or not is_secret_env(k)}
    if extra:
        env.update(extra)
    return env


def run(
    args,
    *,
    allow_env: Iterable[str] = (),
    extra_env: Optional[Mapping[str, str]] = None,
    timeout: Optional[float] = 60,
    audit_module: str = "",
    audit_action: str = "exec",
    **kwargs,
):
    """``subprocess.run`` 的收口版本：默认脱敏环境 + 必须有超时 + 可留痕。

    超时默认 60 秒而不是"永不超时"：外部工具挂住会把请求线程一起挂住，
    这个仓库里已经因为这个吃过亏（CI 作业转 6 小时才被杀，日志什么都拿不到）。
    真需要长跑的显式传 ``timeout=None``。
    """
    if "env" not in kwargs:
        kwargs["env"] = child_env(allow=allow_env, extra=extra_env)
    kwargs.setdefault("timeout", timeout)
    for key, value in no_window_kwargs().items():
        kwargs.setdefault(key, value)

    if audit_module:
        from app.core import audit
        audit.record(audit_module, audit_action,
                     target=" ".join(str(a) for a in args)[:500])
    return subprocess.run(args, **kwargs)
