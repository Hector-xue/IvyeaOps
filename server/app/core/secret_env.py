"""把 IvyeaOps 自己的凭据从 ``os.environ`` 里摘走。

**为什么不是逐个调用点加 ``env=``**：全仓约 60 处 spawn，其中 29 处继承全量环境。
逐个补 ``env=child_env()`` 有两个问题：一是 28 处散改本身就容易漏；二是**挡不住
以后新增的调用点** —— 明年谁加一行 ``subprocess.run(["git", ...])``，洞就又开了。

在源头摘走则是一次改动覆盖全部现有和未来的 spawn：变量根本不在环境里，
子进程自然读不到。``core/proc.child_env()`` 仍然保留，作为第二层（挡住第三方
工具自己设的凭据变量），两层互不替代。

摘哪些、不摘哪些
----------------
**摘**：IvyeaOps 自己的凭据 —— 会话签名密钥、管理员密码哈希，以及各集成的 key。
这些只有 IvyeaOps 后端会读，没有任何子进程需要它们。

**不摘** ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY`` 这类**行业标准变量**：
hermes 这些子进程正是靠它们工作的，摘掉会直接把功能弄坏。它们由
``child_env()`` 在高危路径（终端、AI 工具调用）上单独剥离 —— 分层处理，
而不是一刀切。

读取方式
--------
摘走之后，原先 ``os.getenv("IVYEA_OPS_SECRET")`` 那些地方要改用 ``get()``。
``get()`` 会先查内部字典再回落 ``os.environ``，所以**导入顺序无关紧要**：
harvest 之前调它读到的是环境里的值，之后读到的是字典里的值，都对。
"""
from __future__ import annotations

import logging
import os
from typing import Dict

logger = logging.getLogger("ivyea.core.secret_env")

# 摘走的名字。刻意写成显式清单而不是正则：这是一份"哪些东西是秘密"的声明，
# 应该看得见、可审查，而不是靠一条正则去猜。
HARVESTED = (
    "IVYEA_OPS_SECRET",            # 会话签名密钥 —— 泄漏 = 可伪造管理员 cookie
    "IVYEA_OPS_PASSWORD_HASH",     # 管理员密码哈希 —— 可离线爆破
    "IVYEA_OPS_ALERT_APP_SECRET",
    "IVYEA_OPS_KIRO_GATEWAY_KEY",
    "APIMART_KEY",
    "DEEPSEEK_API_KEY",
    "SORFTIME_KEY",
    "SIF_KEY",
    "SELLERSPRITE_KEY",
    "LINGXING_OPENAPI_SECRET",
    "LINGXING_MCP_KEY",
    "IVYEA_AGENT_API_KEY",
    "IVYEA_AGENT_TOKEN",
    "HERMES_API_KEY",
    "ADMIN_PASSWORD",
)

_STORE: Dict[str, str] = {}
_harvested = False


def harvest() -> int:
    """把上面这些从 ``os.environ`` 搬进内部字典，返回搬了几个。

    幂等；在 ``load_dotenv()`` 之后立刻调用。
    """
    global _harvested
    moved = 0
    for name in HARVESTED:
        if name in os.environ:
            _STORE[name] = os.environ.pop(name)
            moved += 1
    _harvested = True
    if moved:
        logger.info("已从进程环境中摘走 %d 个凭据变量（子进程不再能读到）", moved)
    return moved


def get(name: str, default: str = "") -> str:
    """读一个凭据。先查内部字典，再回落环境 —— 因此不依赖导入顺序。"""
    if name in _STORE:
        return _STORE[name]
    return os.environ.get(name, default)


def names() -> tuple:
    """已摘走的变量名（只给诊断用，**不含值**）。"""
    return tuple(sorted(_STORE))
