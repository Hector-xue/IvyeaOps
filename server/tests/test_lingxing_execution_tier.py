"""领星写操作的执行档位。

这一条是「Agent 能不能自己动手」的唯一防线，必须有测试守着：

  只读     lingxing_operate_enabled = false
  逐项确认 enabled + require_human = true   （默认；真实账号该用这档）
  自主执行 enabled + require_human = false  （测试账号 / 明确放开时）

背景：`lingxing_operate_require_human` 这个设置以前**只在状态接口里显示，没有任何
地方执行它** —— 界面上写着"需要人工确认"，实际 Agent 传个 decided_by 就能执行。
一个不生效的安全开关比没有开关更危险，因为它让人以为自己被保护着。
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import lingxing_operate as lxo


@pytest.fixture()
def ticket(monkeypatch, tmp_path):
    """一张停在 awaiting_human 的工单，外加放行的开关和护栏。"""
    t = {
        "id": "t-test-1",
        "status": "awaiting_human",
        "intent": {"op_type": "campaign_budget", "sid": 1863,
                   "target_id": "123", "value": 8.0},
    }
    monkeypatch.setattr(lxo, "get_ticket", lambda tid: t if tid == t["id"] else None)
    monkeypatch.setattr(lxo, "_save", lambda *_a, **_k: None)
    monkeypatch.setattr(lxo._gw, "is_operate_active", lambda: True)
    monkeypatch.setattr(lxo, "check_guardrails", lambda intent: {"ok": True})
    return t


def _set_tier(monkeypatch, require_human: bool):
    real = lxo._hs.get
    monkeypatch.setattr(lxo._hs, "get", lambda k, d=None: (
        require_human if k == "lingxing_operate_require_human" else real(k, d)))


def test_agent_cannot_execute_in_confirm_tier(monkeypatch, ticket):
    """逐项确认档：Agent 的确认必须被拒 —— 这是这条闸存在的全部意义。"""
    _set_tier(monkeypatch, True)
    with pytest.raises(lxo._gw.LingXingError, match="逐项确认"):
        asyncio.run(lxo.confirm_ticket("t-test-1", decided_by="agent"))
    assert ticket["status"] == "awaiting_human"      # 工单原封不动


def test_error_message_tells_you_how_to_change_it(monkeypatch, ticket):
    """报错要说清楚去哪儿改 —— 否则用户只会觉得"它坏了"。"""
    _set_tier(monkeypatch, True)
    with pytest.raises(lxo._gw.LingXingError) as e:
        asyncio.run(lxo.confirm_ticket("t-test-1", decided_by="agent"))
    assert "系统配置" in str(e.value) and "自主执行" in str(e.value)


def test_human_can_always_execute(monkeypatch, ticket):
    """人点的永远算数，哪一档都一样 —— 档位管的是 Agent，不是人。"""
    _set_tier(monkeypatch, True)
    called = {}

    async def _fake_current_value(intent):
        called["snapshot"] = True
        raise RuntimeError("到此为止：本用例只验证它没有在档位这一步被拦住")

    monkeypatch.setattr(lxo, "_current_value", _fake_current_value)
    with pytest.raises(RuntimeError, match="到此为止"):
        asyncio.run(lxo.confirm_ticket("t-test-1", decided_by="human"))
    assert called.get("snapshot")


def test_agent_can_execute_in_auto_tier(monkeypatch, ticket):
    """自主执行档：Agent 放行，并且**仍然继续走后面的护栏和快照**，不是抄近路。"""
    _set_tier(monkeypatch, False)
    called = {}

    async def _fake_current_value(intent):
        called["snapshot"] = True
        raise RuntimeError("到此为止")

    monkeypatch.setattr(lxo, "_current_value", _fake_current_value)
    with pytest.raises(RuntimeError, match="到此为止"):
        asyncio.run(lxo.confirm_ticket("t-test-1", decided_by="agent"))
    assert called.get("snapshot"), "自主执行档也必须照常取回滚快照"


def test_write_switch_off_blocks_every_tier(monkeypatch, ticket):
    """写开关是更外层的闸：它关着的时候，哪一档都不许执行。"""
    _set_tier(monkeypatch, False)
    monkeypatch.setattr(lxo._gw, "is_operate_active", lambda: False)
    for who in ("human", "agent"):
        with pytest.raises(lxo._gw.LingXingError, match="操作开关"):
            asyncio.run(lxo.confirm_ticket("t-test-1", decided_by=who))


def test_default_is_confirm_not_auto(monkeypatch, ticket):
    """**没配过这个设置时必须默认要人确认。** 默认值错了，等于所有老部署一升级
    就变成 Agent 可以自己改广告。"""
    real = lxo._hs.get
    monkeypatch.setattr(lxo._hs, "get", lambda k, d=None: (
        d if k == "lingxing_operate_require_human" else real(k, d)))
    with pytest.raises(lxo._gw.LingXingError, match="逐项确认"):
        asyncio.run(lxo.confirm_ticket("t-test-1", decided_by="agent"))
