"""Regression tests for admin session role restoration.

The bug: /api/auth/login returned role=admin, but subsequent /api/auth/me and
admin-only routes could see the same session as a plain user because they
relied on a ContextVar that did not survive FastAPI's sync dependency/endpoint
thread hop.
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import bcrypt
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    password_hash = bcrypt.hashpw(b"test-admin-pass", bcrypt.gensalt()).decode("utf-8")
    monkeypatch.setenv("IVYEA_OPS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("IVYEA_OPS_SECRET", "test-secret-auth-admin")
    monkeypatch.setenv("IVYEA_OPS_USER", "admin")
    monkeypatch.setenv("IVYEA_OPS_PASSWORD_HASH", password_hash)
    monkeypatch.setenv("IVYEA_OPS_ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("IVYEA_OPS_COOKIE_DOMAIN", "")
    monkeypatch.setenv("IVYEA_OPS_DEV", "1")

    class _DummyScreen:
        def __init__(self, *args, **kwargs):
            self.display = []

    class _DummyByteStream:
        def __init__(self, *args, **kwargs):
            pass

        def feed(self, *args, **kwargs):
            return None

    pyte_stub = types.ModuleType("pyte")
    setattr(pyte_stub, "Screen", _DummyScreen)
    setattr(pyte_stub, "ByteStream", _DummyByteStream)
    sys.modules.setdefault("pyte", pyte_stub)

    from app.core import config as config_mod
    importlib.reload(config_mod)
    from app.core import security as security_mod
    importlib.reload(security_mod)
    from app.services import users_service as users_service_mod
    importlib.reload(users_service_mod)
    from app.routers import auth as auth_mod
    importlib.reload(auth_mod)
    from app import main as main_mod
    importlib.reload(main_mod)

    with TestClient(main_mod.app) as c:
        yield c


def _login_admin(client: TestClient):
    return client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-pass"},
        headers={"Origin": "http://testserver", "Referer": "http://testserver/login"},
    )


def test_admin_session_me_keeps_admin_role(client: TestClient):
    login = _login_admin(client)
    assert login.status_code == 200, login.text
    assert login.json()["role"] == "admin"

    me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    # 只断言这条测试真正关心的两个字段。原来是整个字典全等，于是多用户功能给
    # /auth/me 加上 permissions / position 之后它就红了 —— 对一个会长字段的
    # 响应用全等断言，等于把"新增字段"也当成回归。
    body = me.json()
    assert body["username"] == "admin"
    assert body["role"] == "admin"


def test_admin_session_can_access_admin_routes(client: TestClient):
    login = _login_admin(client)
    assert login.status_code == 200, login.text

    users = client.get("/api/auth/admin/users")
    assert users.status_code == 200, users.text
    assert users.json() == []
