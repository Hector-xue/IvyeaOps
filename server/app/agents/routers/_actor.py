"""agents 板块的"调用者身份"绑定。

**为什么需要单独一个模块**：agents 的文件与项目接口里有若干"整机范围"的能力
（浏览任意目录、在任意位置建项目、读项目外的绝对路径）。这些能力原本的正当性
写在各自的注释里 —— "the agents board is admin-only"。这个前提**已经不成立**：
main.py 用的是 ``require_module("agents")`` 而不是 ``require_admin``，而
core/permissions.py 的「技术助理」预设默认就授予 agents。所以要在这些能力上
真正区分管理员与普通成员，而判断依据必须可靠。

**为什么不用 core.security.is_admin()**：它读的是 ``current_user`` ContextVar，
而设置它的 ``require_user`` / ``require_module`` 都是**同步**依赖 —— FastAPI 把
同步依赖丢进线程池执行，在那里写的 ContextVar 传不回事件循环里的异步路由。
（security.py 自己的注释也提过这一点，那正是 require_admin 要重新解析一遍
session 的原因。）

这里的 ``bind_actor`` 是 **async** 依赖：它跑在事件循环上，与路由同一个上下文，
写进去的值路由一定读得到。反过来，同步路由由线程池执行时会**复制**一份上下文
进去，所以读同样是准的。
"""
from __future__ import annotations

from contextvars import ContextVar

from fastapi import Depends, HTTPException

from app.core.security import require_user_info

_actor_is_admin: ContextVar[bool] = ContextVar("agents_actor_is_admin", default=False)


async def bind_actor(info: dict = Depends(require_user_info)) -> None:
    """挂在 router 上的依赖：把调用者是否管理员绑进当前请求上下文。"""
    _actor_is_admin.set(str(info.get("role") or "") == "admin")


def actor_is_admin() -> bool:
    return _actor_is_admin.get()


def require_admin_actor(action: str) -> None:
    """整机范围的动作只留给管理员。"""
    if not _actor_is_admin.get():
        raise HTTPException(403, f"只有管理员可以{action}")
