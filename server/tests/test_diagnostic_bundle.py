"""L0-1d：一键诊断包。

这个文件里**最重要的是脱敏那几条**。诊断包的用途就是让用户贴到 GitHub issue 上，
一旦漏了密钥，等于把用户的凭据公开发布 —— 而且是我们主动教他这么做的。所以：

  · 密钥不能出现在包里的任何一个字节（不只是 config.json 里）；
  · 名字里不带 key/secret 的字段，如果值长得像凭据，也要脱；
  · 库只能报表名和行数，不能带出任何一行业务数据；
  · 整个导出过程不许发任何网络请求（零外发是这个产品的卖点）。
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.services import diagnostics

SECRET = "sk-livekey1234567890ABCDEFghijklmnopqrstuvwxyz0987654321"


@pytest.fixture()
def admin_client(monkeypatch):
    from app.core import security as sec_mod
    from app.main import app

    app.dependency_overrides[sec_mod.require_admin] = lambda: "admin"
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(sec_mod.require_admin, None)


# ── 脱敏 ────────────────────────────────────────────────────────────────

def test_redact_masks_by_field_name():
    out = diagnostics.redact({"deepseek_api_key": SECRET, "hermes_base_url": "http://x"})
    assert out["deepseek_api_key"] == f"sk-***(len={len(SECRET)})"
    assert SECRET not in json.dumps(out)
    # 非敏感字段原样保留，否则诊断包就没信息量了
    assert out["hermes_base_url"] == "http://x"


def test_redact_reaches_nested_structures():
    """配置里有嵌套的 provider 列表，只脱顶层等于没脱。"""
    cfg = {"custom_providers": [{"id": "p1", "api_key": SECRET, "model": "x"}]}
    out = diagnostics.redact(cfg)
    assert SECRET not in json.dumps(out)
    assert out["custom_providers"][0]["model"] == "x"


def test_redact_catches_secret_looking_values_under_innocent_keys():
    """用户完全可能把 key 填进一个名字无关的框里，或 URL 里塞了 token。"""
    out = diagnostics.redact({"note": f"我的 key 是 {SECRET} 别丢了",
                              "callback": "https://x.test/cb?jwt=eyJhbGciOiJIUzI1.eyJzdWIiOiIx."})
    assert SECRET not in json.dumps(out)
    assert "eyJhbGciOiJIUzI1.eyJzdWIiOiIx." not in json.dumps(out)


def test_redact_keeps_length_so_typos_are_debuggable():
    """脱敏不能脱成一团 ***：要能看出"填了个空的"还是"少粘了几位"。"""
    assert diagnostics.redact({"api_key": "abc"})["api_key"] == "***(len=3)"
    assert diagnostics.redact({"api_key": ""})["api_key"] == ""


# ── 打包 ────────────────────────────────────────────────────────────────

def _entries(payload: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def test_bundle_contains_expected_files(tmp_path):
    entries = _entries(diagnostics.build_bundle(data_dir=tmp_path))
    assert set(entries) == {
        "README-给开发者.txt",
        "versions.json",
        "config.redacted.json",
        "db_stats.json",
        "local_checks.json",
        "logs/ivyea-ops.log",
    }
    versions = json.loads(entries["versions.json"])
    assert versions["ivyea_ops"] and versions["python"] and versions["platform"]


def test_bundle_never_contains_a_live_secret(tmp_path, monkeypatch):
    """整包逐字节扫：密钥不许出现在**任何一个文件**里。"""
    from app.core import hub_settings

    monkeypatch.setattr(hub_settings, "load",
                        lambda: {"deepseek_api_key": SECRET, "sorftime_key": SECRET,
                                 "lingxing_openapi_secret": SECRET, "hermes_base_url": "http://x"})
    payload = diagnostics.build_bundle(data_dir=tmp_path)
    assert SECRET.encode() not in payload
    for name, blob in _entries(payload).items():
        assert SECRET not in blob.decode("utf-8", errors="replace"), f"{name} 里泄了密钥"


def test_bundle_reports_tables_but_no_row_content(tmp_path):
    """库只能报结构与计数——带出业务数据就等于把店铺数据发出去了。"""
    import sqlite3

    conn = sqlite3.connect(tmp_path / "demo.sqlite3")
    conn.execute("CREATE TABLE orders (id INTEGER, asin TEXT)")
    conn.execute("INSERT INTO orders VALUES (1, 'B0SECRETASIN')")
    conn.commit()
    conn.close()

    payload = diagnostics.build_bundle(data_dir=tmp_path)
    stats = json.loads(_entries(payload)["db_stats.json"])
    assert stats["demo.sqlite3"]["tables"] == {"orders": 1}
    assert b"B0SECRETASIN" not in payload


def test_bundle_survives_missing_log_file(tmp_path):
    """日志还没生成时也必须导得出来，不能抛。"""
    entries = _entries(diagnostics.build_bundle(data_dir=tmp_path))
    assert entries["logs/ivyea-ops.log"] == b""


def test_bundle_makes_no_network_calls(tmp_path, monkeypatch):
    """零外发：导出过程但凡发一个请求，这条就红。"""
    import socket

    def _boom(*a, **k):
        raise AssertionError("诊断包不允许发起任何网络连接")

    monkeypatch.setattr(socket.socket, "connect", _boom)
    monkeypatch.setattr(socket.socket, "connect_ex", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)

    payload = diagnostics.build_bundle(data_dir=tmp_path)
    assert payload  # 没抛就说明全程没连过网


# ── 接口 ────────────────────────────────────────────────────────────────

def test_endpoint_requires_admin():
    from app.main import app

    r = TestClient(app).get("/api/health/diagnostic-bundle")
    assert r.status_code in (401, 403), "诊断包是整机横截面，不能让匿名或普通成员导走"


def test_endpoint_returns_a_zip_attachment(admin_client):
    r = admin_client.get("/api/health/diagnostic-bundle?lines=100")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]
    assert "ivyea-ops-diagnostic-" in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "versions.json" in zf.namelist()
        assert zf.testzip() is None
