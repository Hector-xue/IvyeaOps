"""Localhost must bypass a system/VPN proxy so the embedded IvyeaAgent (:8765),
imgflow (:3001) etc. don't 502 through the proxy on Windows/macOS."""
from __future__ import annotations

import os

from app.core.config import _ensure_localhost_no_proxy

# Windows 的环境变量名**大小写不敏感**：`NO_PROXY` 和 `no_proxy` 是同一个变量。
# 所以"设了大写、再删小写"这个前置状态在 Windows 上根本不存在 —— 照做的话
# delenv 删掉的正是上一行刚设的那个，测试于是断言失败。
# 产品代码本身没问题：它把两个名字都读进来合并，Windows 上不过是读了同一个值两次。
_CASE_SENSITIVE_ENV = os.name != "nt"


def test_adds_localhost_preserving_existing(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "example.com")
    if _CASE_SENSITIVE_ENV:
        monkeypatch.delenv("no_proxy", raising=False)
    _ensure_localhost_no_proxy()
    val = os.environ["NO_PROXY"]
    assert "example.com" in val            # existing kept
    for h in ("127.0.0.1", "localhost", "::1"):
        assert h in val                    # localhost added
    assert os.environ["no_proxy"] == val   # lowercase mirrored


def test_idempotent_no_duplicates(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    _ensure_localhost_no_proxy()
    _ensure_localhost_no_proxy()
    assert os.environ["NO_PROXY"].split(",").count("127.0.0.1") == 1
