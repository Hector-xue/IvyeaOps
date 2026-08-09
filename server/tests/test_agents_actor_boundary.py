"""agents 板块的越权边界（管理员 vs 被授予该板块的普通成员）。

**背景**：agents 的文件/项目接口里有几处"整机范围"的能力 —— 读项目外的绝对
路径、浏览任意目录、在任意位置建目录/项目。它们原本的正当性写在代码注释里：
"the agents board is admin-only"。这个前提在模块权限引入后就不成立了：
main.py 用的是 ``require_module("agents")``，而 permissions.py 的「技术助理」
预设**默认就授予 agents**。于是一个普通成员被授予该板块后，可以

    GET /api/agents/projects/{id}/file?filePath=/etc/shadow

读走整台机器上的任何文件 —— 而服务在不少装机上是 root 跑的。

这个文件放在 tests/ 下（真的会进 CI 的那批），钉死修好之后的边界。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.agents.routers import _actor
from app.agents.routers import files as files_mod
from app.agents.routers import projects as projects_mod


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    (root / "inside.txt").write_text("项目内的文件", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("项目外的秘密", encoding="utf-8")
    return root


def _client(as_admin: bool) -> TestClient:
    """挂一个只带 bind_actor 的迷你 app，专测边界本身。"""
    app = FastAPI()

    @app.get("/probe")
    async def probe(_bound: None = Depends(_actor.bind_actor)) -> dict:
        return {"is_admin": _actor.actor_is_admin()}

    role = "admin" if as_admin else "user"
    # override 的 key 必须是 **_actor 模块捕获的那个函数对象** —— bind_actor 的
    # `Depends(require_user_info)` 指的就是它。不能写 sec.require_user_info：
    # 别的测试文件（app/tests/test_agents_p3）会 importlib.reload(app.core.security)，
    # reload 造出的是新函数对象，按它注册的 override 对不上，请求会 401，
    # 表现成这里莫名其妙的 KeyError。
    app.dependency_overrides[_actor.require_user_info] = lambda: {
        "id": 1 if not as_admin else "admin", "role": role,
        "email": "u@x.com", "permissions": ["agents"],
    }
    return TestClient(app)


def test_bind_actor_reports_role():
    assert _client(True).get("/probe").json()["is_admin"] is True
    assert _client(False).get("/probe").json()["is_admin"] is False


# ── 文件路径边界 ─────────────────────────────────────────────────────────

def _with_actor(is_admin: bool):
    """直接设 ContextVar：这几条测的是解析函数本身，不必起 HTTP。"""
    return _actor._actor_is_admin.set(is_admin)


def test_member_cannot_read_absolute_paths_outside_project(project):
    token = _with_actor(False)
    try:
        with pytest.raises(Exception) as exc:
            files_mod._resolve_in_project(str(project), "/etc/shadow")
        assert "403" in str(exc.value) or "管理员" in str(exc.value)
    finally:
        _actor._actor_is_admin.reset(token)


def test_member_cannot_escape_with_dotdot(project):
    token = _with_actor(False)
    try:
        with pytest.raises(Exception):
            files_mod._resolve_in_project(str(project), "../outside.txt")
    finally:
        _actor._actor_is_admin.reset(token)


def test_member_can_still_use_paths_inside_the_project(project):
    """收紧不能把正常用法也收掉——普通成员在项目内该照常读写。"""
    token = _with_actor(False)
    try:
        got = files_mod._resolve_in_project(str(project), "sub/../inside.txt")
        assert got == os.path.abspath(str(project / "inside.txt"))
        assert files_mod._resolve_in_project(str(project), "sub") == os.path.abspath(str(project / "sub"))
    finally:
        _actor._actor_is_admin.reset(token)


def test_admin_keeps_browsing_outside_the_project(project):
    """管理员的"上一级"导航是真实需求，不能被这次收紧误伤。"""
    token = _with_actor(True)
    try:
        # 用一个**当前平台上真实存在的**项目外绝对路径。原先这里硬写
        # "/etc/hostname" 并断言原样返回 —— 在 Windows 上 abspath 会把它补成
        # "D:\etc\hostname"，断言必挂，但代码行为是对的。
        outside_abs = os.path.abspath(os.path.join(os.sep, "etc", "hostname"))
        assert files_mod._resolve_in_project(str(project), outside_abs) == outside_abs
        assert files_mod._resolve_in_project(str(project), "../outside.txt") == \
            os.path.abspath(str(project.parent / "outside.txt"))
    finally:
        _actor._actor_is_admin.reset(token)


# ── 工作区/项目创建边界 ──────────────────────────────────────────────────

def test_member_cannot_create_projects_outside_the_workspace_root(monkeypatch, tmp_path):
    monkeypatch.setattr(projects_mod, "WORKSPACES_ROOT", str(tmp_path))
    token = _with_actor(False)
    try:
        # 根之内可以。**比的是"指向同一个位置"而不是字符串相等** —— 这个函数
        # 会把路径分隔符统一成正斜杠（Windows 上必须这么做，否则包含检查里的
        # startswith(root + "/") 永远不成立），所以回来的串和传进去的不一样。
        inside = str(tmp_path / "my-project")
        assert Path(projects_mod._validate_workspace_path(inside)) == Path(inside)
        # 根之外不行——否则他可以把项目建到任意目录，再借文件接口读写那整棵树，
        # 等于绕开 _resolve_in_project 的项目内包含检查。
        outside = str(Path(os.path.abspath(os.sep)) / "home" / "someone-else" / "p")
        with pytest.raises(Exception):
            projects_mod._validate_workspace_path(outside)
    finally:
        _actor._actor_is_admin.reset(token)


def test_admin_can_still_create_projects_anywhere_but_system_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(projects_mod, "WORKSPACES_ROOT", str(tmp_path))
    token = _with_actor(True)
    try:
        # /home/... 既在工作区根之外，又不在 _FORBIDDEN 系统目录词表里 ——
        # 这里要验的是管理员可以在根之外建，别顺手选个会被系统目录规则挡掉的路径。
        outside = str(Path(os.path.abspath(os.sep)) / "home" / "someone-else" / "p")
        assert Path(projects_mod._validate_workspace_path(outside)) == Path(outside)
        # /etc 与根目录属于系统关键目录，管理员也不许在这儿建项目。
        # （Windows 上没有 /etc 这层语义，_FORBIDDEN 是 POSIX 词表，跳过。）
        if os.name != "nt":
            for bad in ("/etc/passwd", "/"):
                with pytest.raises(Exception):
                    projects_mod._validate_workspace_path(bad)
    finally:
        _actor._actor_is_admin.reset(token)


# ── 整机范围的动作 ───────────────────────────────────────────────────────

def test_whole_machine_actions_are_admin_only():
    token = _with_actor(False)
    try:
        with pytest.raises(Exception):
            _actor.require_admin_actor("浏览文件系统")
    finally:
        _actor._actor_is_admin.reset(token)

    token = _with_actor(True)
    try:
        _actor.require_admin_actor("浏览文件系统")  # 不该抛
    finally:
        _actor._actor_is_admin.reset(token)


def test_default_is_deny():
    """没绑定过身份时默认按普通成员算 —— 漏挂依赖时应该是关门而不是开门。"""
    assert _actor.actor_is_admin() is False
