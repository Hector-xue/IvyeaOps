"""Listing 工作台的 AI 入口：统一文本降级链 + 统一视觉链。

规则（整改后）：任何文本生成走 run_text_chain（IvyeaAgent 恒为第一位，其余按
text_ai_providers 配置逐级降级），任何看图任务走 stream_vision 统一视觉链。Apimart 只允许出现在生图
提交里 —— 它的 /messages 端点对文本/视觉一律 403，历史上把复核与文案兜底接到
它上面造成了"复核永远失败"的系统性故障，禁止回退到那种写法。
"""
from __future__ import annotations

import logging

from fastapi import HTTPException

logger = logging.getLogger("ivyea.routers.listing.ai")


_PROVIDER_LABELS = {
    "ivyea-agent": "IvyeaAgent", "hermes": "Hermes", "deepseek": "DeepSeek",
    "assistant": "全局兜底", "codex": "Codex", "claude": "Claude",
}


def text_chain_label() -> str:
    """真实调用链的人类可读描述（如 "IvyeaAgent → DeepSeek → 全局兜底"）。

    进度条/报错里的链路描述必须从这里取——曾经写死 "Hermes → 全局兜底 → Codex
    → Claude" 与实际顺序（IvyeaAgent 恒第一）不符，误导排查。"""
    try:
        from app.services.ai_synthesis_service import _text_provider_chain
        chain = _text_provider_chain()
    except Exception:
        chain = []
    if not chain:
        return "统一 AI 降级链"
    return " → ".join(_PROVIDER_LABELS.get(p, p) for p in chain)


async def _call_ai(prompt: str, web_search: bool = True) -> str:
    """Generate text via the standard fallback chain (IvyeaAgent first, then the
    configured text_ai_providers order).

    Listing AI is a pure text engine (the prompt forbids tools/commands), so it
    rides the shared ``run_text_chain`` orchestrator — the exact same chain every
    other board uses, gaining the global fallback model and Claude automatically.
    """
    from app.services import ai_synthesis_service

    task_prompt = (
        "你正在作为 Listing 生成板块的纯文本生成引擎。"
        "禁止执行命令、禁止读写文件、禁止修改系统、禁止调用工具；只根据提示词内容返回最终文本。\n\n"
        + prompt
    )
    if not web_search:
        task_prompt = "不要联网搜索，不要调用工具，只基于下面提供的信息回答。\n\n" + task_prompt
    task_prompt = (
        f"{task_prompt}\n\n"
        "输出要求：直接输出最终内容，不要解释调用过程，不要添加 Markdown 代码块。"
    )

    try:
        _provider, text = await ai_synthesis_service.run_text_chain(task_prompt)
        return text
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"AI 调用失败（{text_chain_label()} 均不可用）：{e}")


def has_vision() -> bool:
    from app.services import ai_synthesis_service
    return ai_synthesis_service.has_vision_capability()


def vision_tier() -> int:
    """当前生效档位：1 主脑直读 / 2 第三方视觉旁路 / 3 本地 CV 量化 / 0 无。

    Listing 用它决定哪些分析做得了：T1/T2 是真的"看见了画面"；T3 只有度量读数
    （尺寸/白底/占比/配色/OCR 文字），语义类分析（版式逆向、审美判断）在 T3 下
    **必须跳过并明说**，不能拿读数硬编一个假结果出来。
    """
    from app.services import ai_synthesis_service
    return ai_synthesis_service.vision_tier()


def vision_tier_label() -> str:
    from app.services import ai_synthesis_service
    return ai_synthesis_service.vision_tier_label()


def has_semantic_vision() -> bool:
    """能不能真正"看懂画面"（而不只是量出读数）。T1/T2 为真，T3/无 为假。"""
    return vision_tier() in (1, 2)


async def _collect_vision(prompt: str, images_b64: list[str]) -> str:
    """Run the vision fallback chain and collect its text output."""
    from app.services import ai_synthesis_service
    parts: list[str] = []
    async for prov, chunk in ai_synthesis_service.stream_vision(prompt, images_b64):
        if prov != "error":
            parts.append(chunk)
    return "".join(parts).strip()


def _load_skill_knowledge() -> str:
    """Load relevant skill knowledge for analysis prompts."""
    from app.services.skill_repo import get_skill
    parts = []
    try:
        creative = get_skill("amazon/amazon-listing-creative")
        parts.append(f"[LISTING CREATIVE STRATEGY]\n{creative.content_body[:3000]}")
    except Exception:
        logger.debug("get_skill 失败（旁路，已忽略）", exc_info=True)
    try:
        audit = get_skill("amazon/amazon-asin-cosmo-rufus-audit")
        parts.append(f"[ASIN AUDIT METHODOLOGY]\n{audit.content_body[:2000]}")
    except Exception:
        logger.debug("get_skill 失败（旁路，已忽略）", exc_info=True)
    return "\n\n".join(parts)
