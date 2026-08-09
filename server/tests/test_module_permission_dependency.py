"""板块权限依赖 `require_module` 的行为护栏。

**为什么这个文件必须存在**：`require_module()` 返回的 `_dep` 原本在无 cookie 时
直接 `return require_user(session)` —— 那是普通的 Python 函数调用，**绕过
FastAPI 的 dependency_overrides**，所以它上面那句"trust an upstream require_user
override (test fixtures)"的注释描述的行为，从来就没有真的发生过。

后果是 `app/tests` 里 61 项常年红着（大面积 `assert 401 == 200`），而因为
CI 的 Test 步骤只跑 `tests/` 不跑 `app/tests/`，没有任何人知道。

所以这几条断言放在 `tests/` 下 —— 它们是真的会在 CI 里跑的那批。
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core import security as sec
from app.core.config import settings


def _app_with_module(module_key: str) -> FastAPI:
    app = FastAPI()

    @app.get("/guarded")
    def guarded(_who: str = Depends(sec.require_module(module_key))) -> dict:
        return {"ok": True}

    return app


@pytest.fixture()
def app_and_client():
    app = _app_with_module("tools")
    return app, TestClient(app)


# ── 生产行为：这三条是真正保护线上的 ────────────────────────────────────

def test_no_cookie_is_rejected(app_and_client):
    _app, client = app_and_client
    assert client.get("/guarded").status_code == 401


def test_admin_passes_any_module(app_and_client):
    _app, client = app_and_client
    token = sec.issue_session(sec.ADMIN_ID, "admin")
    client.cookies.set(settings.session_cookie_name, token)
    assert client.get("/guarded").status_code == 200


def test_garbage_cookie_is_rejected(app_and_client):
    _app, client = app_and_client
    client.cookies.set(settings.session_cookie_name, "not-a-real-token")
    assert client.get("/guarded").status_code == 401


def test_registered_user_needs_the_module_granted(monkeypatch):
    """有 session 但没被授予该板块 → 403，不是 401，也不是放行。"""
    granted_app = _app_with_module("tools")
    client = TestClient(granted_app)

    def _principal(session: str) -> dict:
        return {"id": 7, "role": "user", "email": "u@x.com", "permissions": ["listing"]}

    monkeypatch.setattr(sec, "_resolve_session_principal", _principal)
    client.cookies.set(settings.session_cookie_name, "whatever")
    assert client.get("/guarded").status_code == 403

    allowed = TestClient(_app_with_module("listing"))
    allowed.cookies.set(settings.session_cookie_name, "whatever")
    assert allowed.get("/guarded").status_code == 200


# ── 测试基建：dependency_overrides 必须真的能覆盖到 ──────────────────────

def test_require_user_override_is_honoured(app_and_client):
    """这条就是那 61 项失败的直接成因。

    `_dep` 必须通过 `Depends(require_user)` 拿身份，而不是自己调用 require_user
    —— 否则测试 override 了也没用，请求照样 401，而代码注释还写着"会尊重
    override"。写在这里，下次谁改回直接调用，当场就红。
    """
    app, client = app_and_client
    app.dependency_overrides[sec.require_user] = lambda: "tester"
    try:
        assert client.get("/guarded").status_code == 200
    finally:
        app.dependency_overrides.clear()
