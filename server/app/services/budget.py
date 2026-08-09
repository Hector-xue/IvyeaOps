"""AI 花费预算。

成本数据本来就有（monitor 的 token-usage 已经按日/周/月、按 agent、按模型算好了），
缺的是**主动告知**：用户不会每天去翻监控页，他只想在快花超的时候被拍一下肩膀。

两条设计
--------
* **每月只提醒一次**。超了之后每跑一个任务响一次，等于逼用户关掉提醒。
  记下"哪个月已经提醒过"，跨月自动重置。
* **只提醒，不阻断**。这是本地估算（按公开价目表折算 token），不是账单 ——
  拿一个估算值去掐掉用户正在跑的任务，错一次就是事故。真正的止损开关该由
  用户自己按，不该由一个估算数替他按。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger("ivyea.services.budget")


def limit_usd() -> float:
    from app.core import hub_settings
    try:
        return float(hub_settings.get("ai_budget_monthly_usd") or 0)
    except (TypeError, ValueError):
        return 0.0


def _month_key() -> str:
    return datetime.now().strftime("%Y-%m")


def month_spend_usd() -> float:
    """本月估算花费。取 monitor 已经算好的月度聚合，**不另起一套口径** ——
    两个地方各算各的，用户迟早会看到两个对不上的数字。"""
    try:
        from app.routers.monitor import token_usage
        data = token_usage()
        for row in (data.get("monthly") or []):
            if row.get("month") == _month_key():
                return float(row.get("cost_usd") or 0)
    except Exception as exc:  # noqa: BLE001 — 取不到就当没超，不能因此报错
        logger.warning("读取本月花费失败：%s", exc)
    return 0.0


def status() -> Dict[str, Any]:
    limit = limit_usd()
    spend = month_spend_usd()
    return {
        "month": _month_key(),
        "limit_usd": limit,
        "spend_usd": round(spend, 2),
        "ratio": round(spend / limit, 3) if limit > 0 else 0.0,
        "exceeded": bool(limit > 0 and spend >= limit),
        "enabled": limit > 0,
    }


def check_and_notify() -> Dict[str, Any]:
    """检查并（必要时）发一次通知。返回本次的状态，供接口直接回给前端。"""
    st = status()
    if not st["exceeded"]:
        return st

    from app.core import hub_settings
    if hub_settings.get("ai_budget_alerted_month") == st["month"]:
        st["already_notified"] = True
        return st

    from app.services import notify
    sent = notify.send_sync(
        "budget.exceeded",
        f"本月 AI 花费已达 ${st['spend_usd']:.2f}",
        f"预算 ${st['limit_usd']:.2f}，已用 {st['ratio'] * 100:.0f}%。\n"
        f"这是按公开价目表对 token 的本地估算，不是账单；任务不会被自动停掉。",
        level="warn",
    )
    # **发出去了才记账**。发失败还记上的话，这个月就再也不会提醒了。
    if sent:
        hub_settings.save({"ai_budget_alerted_month": st["month"]})
    st["notified"] = sent
    return st
