"""记忆管理路由：读要登录、写要管理员。

记忆里装的是个人信息与经营数据，而写入会直接改变 agent 以后的行为
（核心记忆每轮都进它的上下文）。所以两档权限必须钉住 —— 这类保护
一旦哪天被顺手改成 require_user，不会有人发现。
"""
from __future__ import annotations

from app.routers import ivyea_agent as router_mod


def _route(path: str, method: str):
    for r in router_mod.router.routes:
        if r.path == path and method in getattr(r, "methods", set()):
            return r
    raise AssertionError(f"没有注册 {method} {path}")


def _deps(route) -> str:
    return " ".join(str(d.call) for d in route.dependant.dependencies)


def test_read_routes_require_login():
    for path in ("/memory/list", "/memory/get", "/memory/history",
                 "/memory/pending", "/memory/stats", "/memory/core", "/memory/episodes"):
        assert "require_user" in _deps(_route(path, "GET")), path


def test_write_routes_require_admin():
    """写入会改变 agent 以后的行为，不能只有登录态就放行。"""
    for path in ("/memory/write", "/memory/confirm", "/memory/reject",
                 "/memory/core", "/memory/reflect", "/memory/prune"):
        assert "require_admin" in _deps(_route(path, "POST")), path


def test_prune_defaults_to_dry_run():
    """清理对话记录是不可逆的：默认必须是"只看不删"。"""
    assert router_mod.MemoryPruneBody().dry_run is True


def test_recall_marker_is_stripped_from_previews():
    """召回块是拼在用户消息尾巴上跟着落盘的。

    漏掉这个标记，用户会在自己的绿气泡里看到一大段记忆 ——
    前后端各有一份剥离清单，历史上就是没同步才漏出去的。
    """
    from app.services import console_sessions
    raw = "领星广告怎么优化\n\n[Ivyea 记忆召回]\n  · [domain/领星广告方法论] 怎么做\n（以上是你的长期记忆…）"
    assert console_sessions.clean_preview(raw) == "领星广告怎么优化"
    # 被截断的半截标记也要切掉（列表接口会先把首条消息砍到 50 字）
    assert console_sessions.clean_preview("一句话总结。\n\n[Ivyea 记忆") == "一句话总结。"
