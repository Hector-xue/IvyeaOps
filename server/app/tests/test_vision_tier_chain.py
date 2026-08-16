"""视觉三档降级链在 IvyeaOps 侧的判定与排序。

盯住三个具体故障：
  1. 只看主脑 caps.vision → 主脑是纯文本模型时整条视觉链被判死，Listing 静默空转；
  2. agent 恒排链首这条**文本链**的规矩被照抄到视觉链 → 只有 T3（本地 CV 读数）
     的 agent 顶掉了真视觉模型，纯粹的质量倒退；
  3. 视觉槽只读 vision_* → 只配了全局兜底槽的存量用户拿不到 T2。
"""
from __future__ import annotations

import pytest


@pytest.fixture
def ai(monkeypatch):
    from app.services import ai_synthesis_service as mod
    # 默认清干净：每个用例自己声明有哪些 provider，避免被本机真实配置带偏。
    monkeypatch.setattr(mod, "_openai_key", lambda: "")
    monkeypatch.setattr(mod, "_assistant_vision_cfg", lambda: None)
    monkeypatch.setattr(mod, "_agent_vision_chain", lambda: {})
    monkeypatch.setattr(mod, "_ivyea_agent_vision_available", lambda: False)
    from app.core import hub_settings
    monkeypatch.setattr(hub_settings, "get", lambda key, default=None: "" if key == "vision_ai_providers" else default)
    return mod


def _agent_at(tier: int) -> dict:
    return {"tier": tier, "effective": tier > 0,
            "main": {"vision": tier == 1}, "sidecar": {"model": "qwen2.5-vl-72b" if tier == 2 else ""},
            "local_cv": {"ocr_engine": "RapidOCR (ONNX) 1.4.4" if tier == 3 else ""}}


def test_text_only_main_brain_no_longer_kills_the_chain(ai, monkeypatch):
    """主脑没视觉 ≠ 这条链没视觉。T3 也必须算"能接带图任务"。"""
    monkeypatch.setattr(ai, "_agent_vision_chain", lambda: _agent_at(3))
    monkeypatch.setattr(ai, "_ivyea_agent_vision_available", lambda: True)

    assert ai.has_vision_capability() is True
    assert ai._vision_provider_chain() == ["ivyea-agent"]
    assert ai.vision_tier() == 3
    assert "本地 CV" in ai.vision_tier_label()
    assert "RapidOCR" in ai.vision_tier_label()


def test_local_cv_agent_must_not_outrank_a_real_vision_model(ai, monkeypatch):
    """核心排序规则：只有 T3 的 agent 要排到真视觉 provider **之后**。

    agent 是本产品的一等 provider，文本链里恒第一；但视觉链里 T3 给的是读数
    而不是画面，让它顶掉 openai/assistant 里坐着的真视觉模型是质量倒退。
    """
    monkeypatch.setattr(ai, "_agent_vision_chain", lambda: _agent_at(3))
    monkeypatch.setattr(ai, "_ivyea_agent_vision_available", lambda: True)
    monkeypatch.setattr(ai, "_openai_key", lambda: "sk-real")

    chain = ai._vision_provider_chain()
    assert chain == ["openai", "ivyea-agent"]      # 真视觉在前，T3 兜底在后
    assert ai.vision_tier() == 1                   # 实际生效的是真视觉模型


@pytest.mark.parametrize("tier", [1, 2])
def test_agent_keeps_pole_position_when_it_really_sees(ai, monkeypatch, tier):
    """T1/T2 是真的看得见画面，agent 保持链首（与文本链一致）。"""
    monkeypatch.setattr(ai, "_agent_vision_chain", lambda: _agent_at(tier))
    monkeypatch.setattr(ai, "_ivyea_agent_vision_available", lambda: True)
    monkeypatch.setattr(ai, "_openai_key", lambda: "sk-real")

    assert ai._vision_provider_chain()[0] == "ivyea-agent"
    assert ai.vision_tier() == tier


def test_no_provider_at_all_is_tier_zero(ai):
    assert ai.has_vision_capability() is False
    assert ai.vision_tier() == 0
    assert ai.vision_tier_label() == "无视觉能力"


def test_old_serve_without_vision_chain_keeps_old_behaviour(monkeypatch):
    """老版本 agent 不回 vision_chain → 退回旧判据，绝不因为字段缺失就判它更强。"""
    from app.services import ai_synthesis_service as mod

    monkeypatch.setattr(mod, "_agent_vision_chain", lambda: {})
    fake = {"available": True, "health": {"model": {"capabilities": {"vision": False}}}}
    monkeypatch.setattr("app.services.ivyea_agent_service.availability", lambda: fake)
    assert mod._ivyea_agent_vision_available() is False

    fake["health"]["model"]["capabilities"]["vision"] = True
    assert mod._ivyea_agent_vision_available() is True


# ── 视觉槽下推 ────────────────────────────────────────────────────────────

def test_vision_slot_push_prefers_the_dedicated_slot():
    from app.services.ivyea_agent_service import _agent_vision_payload

    got = _agent_vision_payload({
        "vision_provider": "siliconflow", "vision_api_key": "sk-v",
        "vision_base_url": "https://api.siliconflow.cn/v1", "vision_model": "Qwen/Qwen2.5-VL-72B",
        "assistant_provider": "openai", "assistant_api_key": "sk-a", "assistant_model": "gpt-4o",
    })
    assert got["model"] == "Qwen/Qwen2.5-VL-72B"
    assert got["provider"] == "siliconflow"


def test_vision_slot_push_falls_back_to_the_global_assistant_slot():
    """只配了全局兜底槽的存量用户也要拿到 T2，不能停在 T3。"""
    from app.services.ivyea_agent_service import _agent_vision_payload

    got = _agent_vision_payload({
        "assistant_provider": "openai", "assistant_api_key": "sk-a",
        "assistant_vision_model": "gpt-4o",
    })
    assert got["model"] == "gpt-4o" and got["provider"] == "openai"

    # 没有专门的视觉模型时退到 assistant_model
    got2 = _agent_vision_payload({
        "assistant_provider": "openai", "assistant_api_key": "sk-a", "assistant_model": "gpt-4o-mini",
    })
    assert got2["model"] == "gpt-4o-mini"


def test_empty_slot_resolves_to_a_clear_payload():
    from app.services.ivyea_agent_service import _agent_vision_payload

    assert _agent_vision_payload({}) == {"model": ""}
    # 有 key 没 model 也算没配
    assert _agent_vision_payload({"vision_api_key": "sk-v"}) == {"model": ""}


def test_sync_never_pushes_an_unsolicited_clear(monkeypatch):
    """ops 没配视觉槽 ≠ agent 那边也该没有。

    CLI 用户完全可能自己 `ivyea config set vision_slot`。每次同步都无脑推一条
    空 model 会把它悄悄清掉，用户只会看到"某天起视觉突然降级了"且毫无线索。
    只有本进程确实推过非空槽位、之后又被清空（= 用户在界面上删了它）才推清除。
    """
    from app.services import ivyea_agent_service as svc

    calls: list = []
    monkeypatch.setattr(svc, "request_json",
                        lambda method, path, payload=None, **k: calls.append((path, payload)) or {"ok": True})
    monkeypatch.setattr(svc, "_VISION_SLOT_PUSHED", False)
    monkeypatch.setattr(svc, "_LAST_MODEL_SYNC_SIGNATURE", "")

    base = {"ivyea_agent_provider": "deepseek", "ivyea_agent_model": "deepseek-chat",
            "ivyea_agent_api_key": "sk-a"}

    # 1) ops 没配视觉槽 → 完全不碰 agent 的视觉配置
    svc.sync_model_settings(dict(base), force=True)
    assert [p for p, _ in calls] == ["/v1/model/configure"]

    # 2) 用户在界面配上了 → 推下去
    calls.clear()
    svc.sync_model_settings({**base, "vision_provider": "openai",
                             "vision_api_key": "sk-v", "vision_model": "gpt-4o"}, force=True)
    assert ("/v1/config/vision", {"provider": "openai", "model": "gpt-4o",
                                  "base_url": "", "api_key": "sk-v"}) in calls

    # 3) 用户又把它删了 → 这次要推清除
    calls.clear()
    svc.sync_model_settings(dict(base), force=True)
    assert ("/v1/config/vision", {"model": ""}) in calls

    # 4) 已经清过了，再同步就不该反复推
    calls.clear()
    svc.sync_model_settings(dict(base), force=True)
    assert [p for p, _ in calls] == ["/v1/model/configure"]


# ── Listing 按档位分流 ────────────────────────────────────────────────────

def _run(coro):
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro)


def test_reference_templates_skipped_loudly_without_semantic_vision(monkeypatch):
    """T3 下版式逆向必须**留下跳过原因**，而不是静默返回空。

    旧行为 `if not has_vision(): return []` —— 用户拿到一份莫名其妙少了参考版式的
    方案，没有任何解释，也不知道配个视觉模型就能解锁。
    """
    from app.routers.listing import visuals

    monkeypatch.setattr(visuals, "has_semantic_vision", lambda: False)
    monkeypatch.setattr(visuals, "vision_tier_label", lambda: "本地 CV 度量（OCR：RapidOCR）")
    monkeypatch.setattr(visuals, "_reference_images", lambda d: [f"http://x/{i}.jpg" for i in range(6)])

    scrape: dict = {}
    assert _run(visuals._analyze_reference_templates(scrape)) == []

    notes = visuals._analysis_notes(scrape)
    assert len(notes) == 1
    assert notes[0]["code"] == "no_semantic_vision"
    assert "本地 CV 度量" in notes[0]["message"]
    assert "已跳过" in notes[0]["message"]


def test_skip_notes_reach_the_plan_quality(monkeypatch):
    """跳过说明要走前端已有的 issues 通道，且 severity=info 不参与扣分——
    降级不是方案缺陷，标成 warning/error 会误导用户去改一个没问题的方案。"""
    from app.routers.listing import visuals

    monkeypatch.setattr(visuals, "_cached_white_product_source", lambda d: "")
    monkeypatch.setattr(visuals, "_creative_plan_quality", lambda *a: {"score": 90, "ready": True, "issues": []})
    monkeypatch.setattr(visuals, "vision_tier", lambda: 3)
    monkeypatch.setattr(visuals, "vision_tier_label", lambda: "本地 CV 度量")

    scrape: dict = {}
    visuals._note_skipped_analysis(scrape, "reference_templates", "no_semantic_vision", "本项已跳过。")
    plan = visuals._bind_reference_templates({"images": []}, "p1", scrape, "gallery")

    quality = plan["quality"]
    assert quality["score"] == 90                       # 未扣分
    assert quality["vision_tier"] == 3
    assert quality["vision_tier_label"] == "本地 CV 度量"
    assert [i["severity"] for i in quality["issues"]] == ["info"]
    assert quality["skipped_analyses"][0]["stage"] == "reference_templates"


def test_visual_profile_lists_are_deduped():
    """supporting_palette 这种"给我五个颜色"的位置最容易吐重复值。

    实测 T3 出过 [白,白,红,红,黑]——生图的背景/承载面/辅助/强调/深中性五个槽位
    塌成三种颜色，整套图没了层次。
    """
    from app.routers.listing import visuals

    fallback = {"supporting_palette": [], "product_colours": [], "materials_and_finish": [],
                "fidelity_anchors": [], "natural_interactions": [], "scene_families": [],
                "visual_opportunities": [], "avoid": [], "category_family": "",
                "object_behavior": "", "form_and_scale": ""}
    got = visuals._normalise_product_visual_profile(
        {"supporting_palette": ["#FFFFFF", "#ffffff", "#D42A2A", "#d42a2a", "#2A2A2A"]}, fallback)
    assert got["supporting_palette"] == ["#FFFFFF", "#D42A2A", "#2A2A2A"]   # 保序、首现胜出


def test_skip_notes_are_deduped():
    """同一步骤重复记录只留一条——一次生成里同一个原因不该刷屏。"""
    from app.routers.listing import visuals

    scrape: dict = {}
    visuals._note_skipped_analysis(scrape, "reference_templates", "a", "第一次")
    visuals._note_skipped_analysis(scrape, "reference_templates", "b", "第二次")
    assert len(visuals._analysis_notes(scrape)) == 1


def test_vision_payload_reads_the_settings_passed_in(monkeypatch):
    """必须按入参解析，不能绕过去读全局配置——同步预演/测试都走显式 settings。"""
    from app.core import hub_settings
    from app.services.ivyea_agent_service import _agent_vision_payload

    monkeypatch.setattr(hub_settings, "get", lambda *a, **k: "SHOULD-NOT-BE-READ")
    got = _agent_vision_payload({"vision_provider": "openai", "vision_api_key": "sk-x",
                                 "vision_model": "gpt-4o"})
    assert got["model"] == "gpt-4o"
