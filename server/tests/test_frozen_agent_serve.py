"""Frozen build (Windows x64 exe / macOS .app): the bundled agent runs from the
exe via `<exe> agent-serve`, with no separate pip/Python install."""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from app.services import ivyea_agent_service as svc

_SERVER_DIR = Path(__file__).resolve().parents[1]   # server/


def test_frozen_start_local_service_uses_exe(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/IvyeaOps/IvyeaOpsServer", raising=False)
    monkeypatch.setattr(svc, "_service_bind", lambda: ("127.0.0.1", 8765))
    monkeypatch.setattr(svc, "_token", lambda: "")
    captured = {}

    class FakePopen:
        def __init__(self, cmd, **kw):
            captured["cmd"] = cmd
            self.pid = 999

    monkeypatch.setattr(svc.subprocess, "Popen", FakePopen)
    r = svc.start_local_service()
    assert r["ok"] is True and r["frozen"] is True and r["pid"] == 999
    assert captured["cmd"][:2] == ["/opt/IvyeaOps/IvyeaOpsServer", "agent-serve"]
    assert "--port" in captured["cmd"]


def test_frozen_upgrade_is_bundled(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    r = svc.upgrade_agent()
    assert r["bundled"] is True and "随 IvyeaOps" in r["note"]


def test_frozen_installed_version_imports(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert svc._installed_agent_version("")   # imports ivyea_agent → non-empty


def test_agent_serve_entry_mode_runs_the_agent():
    """`ivyeaops_server.py agent-serve` boots the bundled agent's HTTP serve."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    p = subprocess.Popen(
        [sys.executable, "ivyeaops_server.py", "agent-serve", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(_SERVER_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        ok = False
        for _ in range(40):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
                    ok = bool(json.loads(r.read()).get("ok"))
                    break
            except Exception:
                time.sleep(0.3)
        assert ok, "agent-serve mode did not answer /health"
    finally:
        p.terminate()
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()


def test_ivyea_entry_mode_runs_the_agent_cli():
    """`ivyeaops_server.py ivyea <args>` 跑内置 agent CLI —— 供 Windows 的 `ivyea` 启动器用
    exe 内置 agent（PowerShell 里 `ivyea` 能打开、随 IvyeaOps 更新）。"""
    r = subprocess.run(
        [sys.executable, "ivyeaops_server.py", "ivyea", "--version"],
        cwd=str(_SERVER_DIR), capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert "ivyea-agent" in (r.stdout + r.stderr).lower()


def test_service_start_does_not_capture_through_pipes():
    """启动 agent 的那条命令**不能用管道捕获输出**。

    `ivyea self service-start` 会起一个守护进程，而 Windows 上守护进程会继承管道的
    写端 —— `subprocess.run` 的 reader 线程永远等不到 EOF，连它自己 18 秒的 timeout
    都会越过去，调用方永久卡死。

    实测代价：Windows CI 上整个测试作业挂了 25 分钟（堆栈停在 `_readerthread`），
    而 GitHub 的日志要等作业结束才可读，挂着的时候什么线索都拿不到。
    对真实用户就是"点一下，页面再也不响应"。

    正解是落临时文件：守护进程照样可以持有文件句柄，但这边不必等任何人。
    """
    import inspect

    src = inspect.getsource(svc.start_local_service)
    assert "capture_output=True" not in src, "启动命令不能用 capture_output（Windows 会挂死）"
    assert "subprocess.PIPE" not in src, "启动命令不能接管道（Windows 会挂死）"
    assert "tempfile" in src or "TemporaryFile" in src, "输出应落临时文件"
