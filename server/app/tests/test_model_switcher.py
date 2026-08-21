"""任务台的模型选择器：按轮次切主脑 + 各槽位的模型清单。

铁律：**不选模型时，下发给 daemon 的 payload 与改动前逐字一致**。老版本 agent
不认识 model 字段，多带一个空字符串下去就是在赌它会忽略。
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("IVYEA_OPS_SECRET", "test-secret")
    monkeypatch.setenv("AGENTS_DB_PATH", str(tmp_path / "agents.db"))
    monkeypatch.setenv("IVYEA_AGENT_URL", "127.0.0.1:9876")

    from app.core import config as cfg_mod
    importlib.reload(cfg_mod)
    from app.services import ivyea_agent_service as svc_mod
    importlib.reload(svc_mod)
    from app.routers import ivyea_agent as router_mod
    importlib.reload(router_mod)
    return svc_mod, router_mod


class FakeRequest:
    base_url = "http://ops.test/"


# ── 按轮次切主脑 ────────────────────────────────────────────────────────────

def test_model_is_forwarded_when_chosen(ctx, monkeypatch):
    svc, router = ctx
    seen: dict = {}

    def _chat(payload):
        seen["p"] = payload
        return {"ok": True}

    monkeypatch.setattr(svc, "chat", _chat)
    router.chat(router.ChatBody(message="你好", model="openrouter:x-ai/grok-4.6"), FakeRequest())
    assert seen["p"]["model"] == "openrouter:x-ai/grok-4.6"


def test_model_is_dropped_when_not_chosen(ctx, monkeypatch):
    """没选模型 = 这个字段根本不出现，老 daemon 收到的东西一个字节都没变。"""
    svc, router = ctx
    seen: dict = {}

    def _chat(payload):
        seen["p"] = payload
        return {"ok": True}

    monkeypatch.setattr(svc, "chat", _chat)
    router.chat(router.ChatBody(message="你好"), FakeRequest())
    assert "model" not in seen["p"]


def test_model_catalog_route_forwards(ctx, monkeypatch):
    svc, router = ctx
    seen: dict = {}

    def _catalog(payload):
        seen["p"] = payload
        return {"ok": True, "catalog": {"models": ["a"]}}

    monkeypatch.setattr(svc, "model_catalog", _catalog)
    out = router.model_catalog(router.ModelCatalogBody(
        provider="siliconflow", base_url="https://api.siliconflow.cn/v1", api_key="sk-x"))
    assert out["catalog"]["models"] == ["a"]
    assert seen["p"]["base_url"] == "https://api.siliconflow.cn/v1"
    assert seen["p"]["api_key"] == "sk-x"


# ── 各槽位的模型清单 ────────────────────────────────────────────────────────

@pytest.fixture
def hub(monkeypatch):
    from app.routers import hub_settings as mod

    stored = {
        "vision_provider": "siliconflow", "vision_api_key": "sk-vision",
        "vision_base_url": "https://api.siliconflow.cn/v1", "vision_model": "Qwen/Qwen3-VL",
        "assistant_provider": "deepseek", "assistant_api_key": "sk-assist", "assistant_base_url": "",
        "ivyea_agent_provider": "deepseek", "ivyea_agent_api_key": "sk-agent", "ivyea_agent_base_url": "",
        "image_api_key": "", "image_base_url": "",
        "apimart_key": "sk-apimart", "apimart_base": "https://api.apimart.ai/v1",
    }
    monkeypatch.setattr(mod._hs, "load", lambda: dict(stored))
    return mod


def _run(coro):
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro)


def _stub_agent(monkeypatch, sink: dict, result=None):
    from app.services import ivyea_agent_service

    def _catalog(payload):
        sink["p"] = payload
        return result or {"ok": True, "catalog": {"ok": True, "models": ["m1"], "source": "live"}}

    monkeypatch.setattr(ivyea_agent_service, "model_catalog", _catalog)


def test_vision_slot_uses_its_own_account(hub, monkeypatch):
    sink: dict = {}
    _stub_agent(monkeypatch, sink)
    out = _run(hub.model_catalog(hub.ModelCatalogBody(slot="vision")))
    assert out["catalog"]["models"] == ["m1"]
    assert sink["p"]["base_url"] == "https://api.siliconflow.cn/v1"
    assert sink["p"]["api_key"] == "sk-vision"


def test_image_slot_falls_back_to_apimart(hub, monkeypatch):
    """生图槽留空时沿用 apimart 那套账号 —— 和真生成时走的是同一个端点。
    这里对不上就会出现"面板列的是 A 家的模型、生成时打的是 B 家"。"""
    sink: dict = {}
    _stub_agent(monkeypatch, sink)
    _run(hub.model_catalog(hub.ModelCatalogBody(slot="image")))
    assert sink["p"]["base_url"] == "https://api.apimart.ai/v1"
    assert sink["p"]["api_key"] == "sk-apimart"


def test_base_url_is_derived_from_provider_when_blank(hub, monkeypatch):
    sink: dict = {}
    _stub_agent(monkeypatch, sink)
    _run(hub.model_catalog(hub.ModelCatalogBody(slot="assistant")))
    assert sink["p"]["base_url"] == "https://api.deepseek.com"


def test_unsaved_form_values_win(hub, monkeypatch):
    """保存**之前**就要能看清单：现填的 key/地址必须盖过库里那份。"""
    sink: dict = {}
    _stub_agent(monkeypatch, sink)
    _run(hub.model_catalog(hub.ModelCatalogBody(
        slot="vision", base_url="https://relay.example.com/v1", api_key="sk-new")))
    assert sink["p"]["base_url"] == "https://relay.example.com/v1"
    assert sink["p"]["api_key"] == "sk-new"


def test_unknown_slot_is_rejected(hub):
    out = _run(hub.model_catalog(hub.ModelCatalogBody(slot="nope")))
    assert out["ok"] is False and out["error"] == "unknown_slot"


def test_unconfigured_slot_says_so(monkeypatch):
    from app.routers import hub_settings as mod
    monkeypatch.setattr(mod._hs, "load", lambda: {})
    out = _run(mod.model_catalog(mod.ModelCatalogBody(slot="vision")))
    assert out["ok"] is False and out["error"] == "not_configured"


def test_agent_down_degrades_instead_of_500(hub, monkeypatch):
    """agent 没起来时面板还得打开（退回手输模型名），不能整页报错。"""
    from app.services import ivyea_agent_service

    def _boom(_payload):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ivyea_agent_service, "model_catalog", _boom)
    out = _run(hub.model_catalog(hub.ModelCatalogBody(slot="vision")))
    assert out["ok"] is False and out["error"] == "agent_unavailable"
    assert "connection refused" in out["catalog"]["error"]


def test_no_secret_is_echoed_back(hub, monkeypatch):
    sink: dict = {}
    _stub_agent(monkeypatch, sink)
    out = _run(hub.model_catalog(hub.ModelCatalogBody(slot="vision")))
    assert "sk-vision" not in repr(out)


# ── 订阅登录代理 ────────────────────────────────────────────────────────────

def test_auth_routes_are_admin_only(ctx):
    """凭据存在服务器上、由 agent 全局共用：谁登录，全站所有对话和定时任务都在烧
    谁的订阅额度。这不是普通用户该按的开关。"""
    import inspect

    from app.core.security import require_admin
    _svc, router = ctx
    for fn in (router.auth_status, router.auth_start, router.auth_poll,
               router.auth_complete, router.auth_logout):
        deps = [p.default for p in inspect.signature(fn).parameters.values()
                if hasattr(p.default, "dependency")]
        assert any(d.dependency is require_admin for d in deps), f"{fn.__name__} 少了 require_admin"


def test_auth_provider_id_is_whitelisted(ctx):
    """provider id 会被拼进转发给 agent 的路径，不能什么都放行。"""
    from fastapi import HTTPException
    _svc, router = ctx
    assert router._checked_auth_provider("qwen-oauth") == "qwen-oauth"
    assert router._checked_auth_provider("kimi-code") == "kimi-code"
    for bad in ("deepseek", "../../health", ""):
        with pytest.raises(HTTPException):
            router._checked_auth_provider(bad)


def test_auth_calls_forward_session_and_value(ctx, monkeypatch):
    svc, router = ctx
    seen: dict = {}

    def _poll(pid, session):
        seen["poll"] = (pid, session)
        return {"ok": True, "status": "pending"}

    def _complete(pid, session, value):
        seen["complete"] = (pid, session, value)
        return {"ok": True}

    monkeypatch.setattr(svc, "auth_poll", _poll)
    monkeypatch.setattr(svc, "auth_complete", _complete)
    router.auth_poll("qwen-oauth", router.AuthActionBody(session="s1"))
    router.auth_complete("anthropic-oauth", router.AuthActionBody(session="s2", value="code#state"))
    assert seen["poll"] == ("qwen-oauth", "s1")
    assert seen["complete"] == ("anthropic-oauth", "s2", "code#state")
