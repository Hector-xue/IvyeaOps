"""飞书配置的两处落地：ops 自己的告警 + 下推给 IvyeaAgent。

盯住的故障：
  1. 界面上换了飞书应用，服务器告警走新应用、巡检卡片还在用旧的（两处不同步）；
  2. 打开系统配置什么都没改、保存一下，把 agent 那边的飞书配置清空了；
  3. agent 没起来（或版本旧）时，保存设置整个失败——CPU 告警那条链路本不该受牵连。
"""
from __future__ import annotations

import pytest


@pytest.fixture
def svc(monkeypatch):
    from app.services import ivyea_agent_service as mod
    return mod


def test_payload_carries_every_field_the_agent_needs(svc):
    payload = svc._agent_feishu_payload({
        "alert_app_id": "cli_x", "alert_app_secret": "s3",
        "alert_chat_id": "oc_1", "alert_feishu_domain": "lark",
        "alert_webhook": "https://open.larksuite.com/hook/x",
    })
    assert payload == {"app_id": "cli_x", "app_secret": "s3", "chat_id": "oc_1",
                       "domain": "lark", "webhook_url": "https://open.larksuite.com/hook/x"}


def test_blank_fields_are_never_pushed(svc):
    """界面上没填的框传空串。把空串推下去 = 打开配置页保存一下就把 agent 清空。"""
    assert svc._agent_feishu_payload({"alert_app_id": "cli_x", "alert_chat_id": "   ",
                                      "alert_app_secret": ""}) == {"app_id": "cli_x"}


def test_unconfigured_install_pushes_nothing(svc, monkeypatch):
    called = []
    monkeypatch.setattr(svc, "configure_feishu", lambda p: called.append(p) or {"ok": True})
    out = svc.sync_feishu_settings({"alert_app_id": "", "alert_webhook": ""})
    assert out["skipped"] is True and called == []


def test_agent_down_does_not_break_saving_settings(svc, monkeypatch):
    """CPU 告警是看门狗，它那条链路不依赖 agent；agent 挂了也得能存配置。"""
    def _boom(_payload):
        raise svc.IvyeaAgentError("connection refused")

    monkeypatch.setattr(svc, "configure_feishu", _boom)
    out = svc.sync_feishu_settings({"alert_app_id": "cli_x", "alert_app_secret": "s"})
    assert out["ok"] is False and "connection refused" in out["error"]


def test_sync_is_triggered_by_credential_keys():
    """凭据键在同步集合里，否则界面改了 agent 那边永远不知道。"""
    from app.routers.hub_settings import _FEISHU_SYNC_KEYS

    assert {"alert_app_id", "alert_app_secret", "alert_chat_id"} <= _FEISHU_SYNC_KEYS
    # 阈值只跟 CPU 告警有关，不该白白往 agent 推一次
    assert "alert_threshold" not in _FEISHU_SYNC_KEYS


def test_domain_key_exists_so_international_installs_can_switch(monkeypatch):
    """open.feishu.cn 与 open.larksuite.com 是两套域名，开源用户两边都有。"""
    from app.core import hub_settings

    assert hub_settings._DEFAULTS["alert_feishu_domain"] == "feishu"


def test_patrol_tiers_are_not_dropped_by_the_proxy():
    """档位由 IvyeaAgent 定义（l1/l2/daily/weekly/monthly，以后可能更多）。

    ops 这一层若逐个列字段，新增一档就会被 pydantic 静默丢掉 ——
    表现是「界面上勾了周报、保存成功、什么都没发生」，最难查的那种假开关。
    """
    from app.routers.hub_settings import FeishuAction

    body = FeishuAction(action="patrol", scope="all",
                        weekly={"enabled": True, "every_minutes": 10080},
                        monthly={"enabled": True, "every_minutes": 43200})
    payload = body.model_dump(exclude_none=True)
    assert payload["weekly"] == {"enabled": True, "every_minutes": 10080}
    assert payload["monthly"] == {"enabled": True, "every_minutes": 43200}
