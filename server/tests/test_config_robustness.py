"""Config self-check probes + IvyeaAgent upgrade-from-IvyeaOps."""
from __future__ import annotations

import asyncio

from app.services import settings_test as st
from app.services import ai_synthesis_service, ivyea_agent_service as svc


def test_catchall_is_graceful_not_scary(monkeypatch):
    monkeypatch.setattr(st, "_hub_get", lambda k: "")
    # a setting with no online test + a value → soft-ok, never "未知配置项"
    r = asyncio.run(st.test_value("ivyea_agent_auto_start", "True"))
    assert r["ok"] is True and "未知配置项" not in r["detail"]
    # empty → clear "not configured"
    r2 = asyncio.run(st.test_value("whatever_key", ""))
    assert r2["ok"] is False and "未配置" in r2["detail"]


def test_provider_list_validation():
    good = asyncio.run(st.test_value("text_ai_providers", "ivyea-agent,deepseek"))
    assert good["ok"] is True
    bad = asyncio.run(st.test_value("text_ai_providers", "ivyea-agent,bogus"))
    assert bad["ok"] is False and "bogus" in bad["detail"]


def test_text_provider_roundtrip_catches_bad_key(monkeypatch):
    async def fake_gen(provider, prompt):
        if provider == "deepseek":
            return "可用"
        raise RuntimeError("401 unauthorized")
    monkeypatch.setattr(ai_synthesis_service, "generate_text_provider", fake_gen)
    monkeypatch.setattr(st, "_hub_get", lambda k: "sk-x")
    ok = asyncio.run(st.test_value("deepseek_api_key", "sk-x"))
    assert ok["ok"] is True
    bad = asyncio.run(st.test_value("assistant_api_key", "sk-x"))
    assert bad["ok"] is False and "401" in bad["detail"]


def test_self_check_returns_matrix(monkeypatch):
    # only deepseek + text_ai_providers configured
    monkeypatch.setattr(st, "_hub_get",
                        lambda k: "sk-x" if k == "deepseek_api_key"
                        else ("ivyea-agent,deepseek" if k == "text_ai_providers" else ""))

    async def fake_gen(provider, prompt):
        return "可用"
    monkeypatch.setattr(ai_synthesis_service, "generate_text_provider", fake_gen)

    res = asyncio.run(st.self_check())
    assert res["total"] == len(st._SELF_CHECK_KEYS)
    by_key = {r["key"]: r for r in res["results"]}
    assert by_key["deepseek_api_key"]["status"] == "ok"
    assert by_key["text_ai_providers"]["status"] == "ok"
    assert by_key["sorftime_key"]["status"] == "skip"   # not configured
    assert res["ok"] >= 2 and res["skip"] >= 1


def test_upgrade_agent_prefers_self_update_and_restarts(monkeypatch):
    monkeypatch.setattr(svc, "_find_ivyea_cli", lambda: "/root/.local/bin/ivyea")
    monkeypatch.setattr(svc, "_venv_python", lambda cli: "/usr/bin/python")
    versions = iter(["1.0.23", "1.0.24"])
    # version now comes from the installed package (not the possibly-stale serve)
    monkeypatch.setattr(svc, "_installed_agent_version", lambda py: next(versions))
    ran = []

    def fake_run(cmd, timeout=300.0):
        ran.append(cmd)
        return {"cmd": " ".join(cmd[:4]), "returncode": 0, "stdout": "", "stderr": ""}
    monkeypatch.setattr(svc, "_run_step", fake_run)
    monkeypatch.setattr(svc, "start_local_service", lambda: {"ok": True})

    r = svc.upgrade_agent()
    assert r["ok"] is True and r["before"] == "1.0.23" and r["after"] == "1.0.24"
    assert ran[0][-2:] == ["self", "update"]              # updater 自选 git pull/pip
    assert not any("pip" in " ".join(c) for c in ran)     # 成功则不再碰 pip
    assert any("service-stop" in " ".join(c) for c in ran)  # serve restart


def test_upgrade_agent_falls_back_to_pip_when_self_update_unavailable(monkeypatch):
    monkeypatch.setattr(svc, "_find_ivyea_cli", lambda: "/root/.local/bin/ivyea")
    monkeypatch.setattr(svc, "_venv_python", lambda cli: "/usr/bin/python")
    versions = iter(["1.0.23", "1.0.24"])
    monkeypatch.setattr(svc, "_installed_agent_version", lambda py: next(versions))
    ran = []

    def fake_run(cmd, timeout=300.0):
        ran.append(cmd)
        # 老版本 agent 不认识 self update → 非零退出，触发 pip 回退
        rc = 2 if cmd[-2:] == ["self", "update"] else 0
        return {"cmd": " ".join(cmd[:4]), "returncode": rc, "stdout": "", "stderr": ""}
    monkeypatch.setattr(svc, "_run_step", fake_run)
    monkeypatch.setattr(svc, "start_local_service", lambda: {"ok": True})

    r = svc.upgrade_agent()
    assert r["ok"] is True and r["before"] == "1.0.23" and r["after"] == "1.0.24"
    pip_cmd = " ".join(ran[1])
    assert "pip" in pip_cmd and "--no-cache-dir" in pip_cmd and "--force-reinstall" in pip_cmd


def test_upgrade_agent_no_cli(monkeypatch):
    monkeypatch.setattr(svc, "_find_ivyea_cli", lambda: "")
    r = svc.upgrade_agent()
    assert r["ok"] is False and "未找到" in r["error"]


def test_auto_sync_skips_editable_install(monkeypatch):
    monkeypatch.setattr(svc, "_agent_is_editable", lambda: True)
    called = []
    monkeypatch.setattr(svc, "upgrade_agent", lambda: called.append(1) or {})
    svc.maybe_sync_agent_on_upgrade()
    import time
    time.sleep(0.1)
    assert called == []  # dev/source install must never be auto-clobbered


def test_auto_sync_runs_once_on_version_change(monkeypatch, tmp_path):
    import time
    from app.core import hub_settings
    from app.core.config import settings as ops_settings
    import app.core.version as ver
    monkeypatch.setattr(svc, "_agent_is_editable", lambda: False)
    monkeypatch.setattr(hub_settings, "get", lambda k=None: True)
    monkeypatch.setattr(ver, "app_version", lambda: "v1.1.69")
    monkeypatch.setattr(ops_settings, "data_dir", tmp_path)
    calls = []
    monkeypatch.setattr(svc, "upgrade_agent",
                        lambda: calls.append(1) or {"ok": True, "before": "1.0.19", "after": "1.0.24"})

    svc.maybe_sync_agent_on_upgrade()
    marker = tmp_path / "agent_sync.json"
    for _ in range(200):  # up to 10s — daemon thread can be starved under full-suite load
        if marker.exists():
            break
        time.sleep(0.05)
    assert marker.exists() and len(calls) == 1
    # same version on next boot → no-op
    svc.maybe_sync_agent_on_upgrade()
    time.sleep(0.15)
    assert len(calls) == 1
