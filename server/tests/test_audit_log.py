"""统一审计流水。

守三条：**不能因为审计把业务动作弄挂**、**流水里不能有凭据**、**接口只给管理员**。
第一条是审计系统最常见的翻车方式——为了"不丢记录"而让主流程失败。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core import audit

SECRET = "sk-livekey1234567890ABCDEFghij"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    audit.init_db()
    yield tmp_path


def test_record_and_query():
    audit.record("git", "commit", target="p1: 修个 bug", actor_name="alice@x.com")
    rows = audit.query()
    assert len(rows) == 1
    row = rows[0]
    assert (row["module"], row["action"], row["outcome"]) == ("git", "commit", "ok")
    assert row["actor_name"] == "alice@x.com"
    assert row["ts"] > 0


def test_filters():
    audit.record("git", "commit", actor_name="alice@x.com")
    audit.record("autofix", "apply", actor_name="bob@x.com")
    assert len(audit.query(module="git")) == 1
    assert len(audit.query(actor="bob@x.com")) == 1
    assert audit.query(module="git")[0]["action"] == "commit"
    assert set(audit.modules()) == {"autofix", "git"}


def test_failures_are_recorded_too():
    """只记成功的流水是没用的——出事的那次恰恰是失败的那次。"""
    audit.record("git", "checkout", target="p1: main", outcome="failed")
    assert audit.query()[0]["outcome"] == "failed"


# ── 不能泄密 ────────────────────────────────────────────────────────────

def test_detail_is_redacted():
    audit.record("settings", "save", detail={"deepseek_api_key": SECRET})
    assert SECRET not in json.dumps(audit.query(), ensure_ascii=False)


def test_inline_command_secrets_are_scrubbed():
    """命令行里夹带的凭据也要脱：留痕的目的是"跑了什么"，不是把 token 抄一份。"""
    audit.record("terminal", "exec", target="curl -H 'Bearer sk-abc123def456' https://x")
    dumped = json.dumps(audit.query(), ensure_ascii=False)
    assert "sk-abc123def456" not in dumped
    assert "curl" in dumped, "脱敏不能把整条命令也抹掉，否则流水就没用了"

    audit.record("terminal", "exec", target="mytool --token=supersecretvalue --verbose")
    dumped = json.dumps(audit.query(), ensure_ascii=False)
    assert "supersecretvalue" not in dumped
    assert "--verbose" in dumped


# ── 不能把业务弄挂 ──────────────────────────────────────────────────────

def test_record_never_raises_even_when_db_is_broken(monkeypatch):
    """审计写不进去时，业务动作必须照常完成。"""
    def _boom(*a, **k):
        raise OSError("磁盘满了")

    monkeypatch.setattr(audit, "_connect", _boom)
    audit.record("git", "commit", target="x")   # 不该抛


def test_query_degrades_to_empty_when_db_is_broken(monkeypatch):
    import sqlite3

    def _boom(*a, **k):
        raise sqlite3.OperationalError("库坏了")

    monkeypatch.setattr(audit, "_connect", _boom)
    assert audit.query() == []
    assert audit.modules() == []


def test_actor_defaults_to_system_outside_a_request():
    """启动任务/定时调度没有登录用户，要记成 system 而不是留空。"""
    audit.record("schedule", "tick")
    assert audit.query()[0]["actor_name"] == "system"


# ── 接口 ────────────────────────────────────────────────────────────────

def test_endpoint_requires_admin():
    from app.main import app

    assert TestClient(app).get("/api/audit").status_code in (401, 403)


def test_endpoint_returns_rows_and_csv(monkeypatch):
    from app.core import security as sec
    from app.main import app

    audit.record("git", "commit", target="p1: 提交", actor_name="alice@x.com")
    app.dependency_overrides[sec.require_admin] = lambda: "admin"
    try:
        c = TestClient(app)
        body = c.get("/api/audit").json()
        assert body["total"] == 1
        assert body["rows"][0]["module"] == "git"
        assert "git" in body["modules"]

        csv_resp = c.get("/api/audit?fmt=csv")
        assert csv_resp.status_code == 200
        assert "text/csv" in csv_resp.headers["content-type"]
        assert "alice@x.com" in csv_resp.text
        assert "attachment" in csv_resp.headers["content-disposition"]
    finally:
        app.dependency_overrides.pop(sec.require_admin, None)


def test_settings_save_is_audited(_isolated):
    """配置变更要留痕，但**只记改了哪些键，不记值**（值全是凭据）。"""
    from app.core import hub_settings

    hub_settings.save({"deepseek_api_key": SECRET, "alert_threshold": 90})
    rows = audit.query(module="settings")
    assert len(rows) == 1
    assert rows[0]["action"] == "save"
    assert "deepseek_api_key" in rows[0]["target"]
    assert SECRET not in json.dumps(rows, ensure_ascii=False)
