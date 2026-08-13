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
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger("ivyea.services.budget")

# 花费聚合要扫遍所有用量来源，本机实测 **8.8 秒**。顶栏要常驻显示这个数，
# 绝不能每次都现算 —— 那等于让用户每开一个页面就给自己的机器来一次全盘扫描。
# 所以这里加一层缓存：读的人拿到的可能是几分钟前的数，但金额本来就不是秒级
# 变化的东西；**宁可显示一个标注了"几分钟前"的旧值，也不能卡住界面**。
_TTL_SECONDS = 300
_cache: Dict[str, Any] = {"value": None, "at": 0.0}
_lock = threading.Lock()
_refreshing = False


def limit_usd() -> float:
    from app.core import hub_settings
    try:
        return float(hub_settings.get("ai_budget_monthly_usd") or 0)
    except (TypeError, ValueError):
        return 0.0


def _month_key() -> str:
    return datetime.now().strftime("%Y-%m")


def month_spend_usd(*, cached: bool = False) -> Optional[float]:
    """本月估算花费。取 monitor 已经算好的月度聚合，**不另起一套口径** ——
    两个地方各算各的，用户迟早会看到两个对不上的数字。

    ``cached=True`` 时只读缓存：**没有就返回 None，绝不现场开算**。顶栏走这条，
    它宁可先显示占位符，也不能因为一个装饰性的数字把请求卡 9 秒。
    """
    if cached:
        with _lock:
            fresh = time.time() - _cache["at"] < _TTL_SECONDS
            value = _cache["value"]
        if not fresh:
            _refresh_async()
        return None if value is None else value["cost_usd"]
    return _compute()["cost_usd"]


def month_tokens(*, cached: bool = True) -> Optional[float]:
    """本月 token 总量。顶栏显示的是这个 —— 见 status() 里的说明。"""
    if cached:
        with _lock:
            fresh = time.time() - _cache["at"] < _TTL_SECONDS
            value = _cache["value"]
        if not fresh:
            _refresh_async()
        return None if value is None else value["total_tokens"]
    return _compute()["total_tokens"]


def cache_age_seconds() -> Optional[float]:
    """缓存里的数是多久以前的。界面要如实标出来 —— 一个不说明新鲜度的金额，
    用户没法判断该不该信它。"""
    with _lock:
        if _cache["value"] is None:
            return None
        return time.time() - _cache["at"]


def _refresh_async() -> None:
    """后台刷一次。同一时刻只允许一个在跑 —— 否则十个页面一起打开，
    就会同时开十次全盘扫描。"""
    global _refreshing
    with _lock:
        if _refreshing:
            return
        _refreshing = True

    def _run() -> None:
        global _refreshing
        try:
            value = _compute()
            with _lock:
                _cache["value"] = value
                _cache["at"] = time.time()
        except Exception as exc:  # noqa: BLE001
            logger.warning("刷新本月花费失败：%s", exc)
        finally:
            with _lock:
                _refreshing = False

    threading.Thread(target=_run, name="budget-refresh", daemon=True).start()


def _compute() -> Dict[str, float]:
    """本月的花费与 token 量。**一次聚合同时取两个** —— 它们出自同一份数据，
    分两次算不但慢一倍，还会因为两次扫描之间又跑了任务而对不上。"""
    out = {"cost_usd": 0.0, "total_tokens": 0.0}
    try:
        from app.routers.monitor import token_usage
        data = token_usage()
        for row in (data.get("monthly") or []):
            if row.get("month") == _month_key():
                out["cost_usd"] = float(row.get("cost_usd") or 0)
                out["total_tokens"] = float(row.get("total_tokens") or 0)
                break
    except Exception as exc:  # noqa: BLE001 — 取不到就当没超，不能因此报错
        logger.warning("读取本月用量失败：%s", exc)
    return out


# 分级：80% 提醒，100% 暂停自动任务。**手动操作永远不停** —— 用户明确要跑的
# 东西，不该被一个估算数按住；被按住的只有"没人盯着也会自己跑"的那些。
WARN_RATIO = 0.8


def status(*, cached: bool = False) -> Dict[str, Any]:
    limit = limit_usd()
    spend = month_spend_usd(cached=cached)
    known = spend is not None
    spend = spend or 0.0
    ratio = (spend / limit) if limit > 0 else 0.0
    level = "ok"
    if limit > 0 and known:
        if ratio >= 1.0:
            level = "exceeded"
        elif ratio >= WARN_RATIO:
            level = "warn"
    return {
        "month": _month_key(),
        "limit_usd": limit,
        "spend_usd": round(spend, 2),
        # **顶栏显示的是 token，不是金额。** 金额是按公开价目表折算的估算值，
        # 天天挂在眼前容易被当成账单；token 是实打实计出来的量，没有这层歧义。
        # 金额仍然保留 —— 预算告警要用它，设置页也要显示。
        "total_tokens": int(month_tokens(cached=cached) or 0),
        "ratio": round(ratio, 3),
        "level": level,                       # ok | warn | exceeded
        "exceeded": level == "exceeded",
        "enabled": limit > 0,
        # 界面要能如实说"这是几分钟前的数" —— 一个不标新鲜度的数字没法被信任。
        "known": known,
        "age_seconds": round(cache_age_seconds() or 0) if cached else 0,
    }


def auto_tasks_paused() -> bool:
    """自动任务是否该暂停。**只读缓存**：这个判断会在每次调度前被问到，
    不能让它触发一次 9 秒的全盘扫描。缓存还没有数时按"不暂停"处理 ——
    宁可多花一点，也不能因为一个还没算出来的数把用户的定时任务全停了。
    """
    if not limit_usd():
        return False
    spend = month_spend_usd(cached=True)
    if spend is None:
        return False
    return spend >= limit_usd()


def check_and_notify(*, cached: bool = False) -> Dict[str, Any]:
    """检查并（必要时）发一次通知。返回本次的状态，供接口直接回给前端。

    两个档各自只提醒一次，且**分开记账** —— 到了 80% 提醒过，冲到 100% 时
    还要再提醒一次，那才是真正需要他动手的时刻。
    """
    st = status(cached=cached)
    if st["level"] == "ok":
        return st

    from app.core import hub_settings
    # 记的是「哪个月的哪一档提醒过」，例如 "2026-08:warn"。
    stamp = f"{st['month']}:{st['level']}"
    if hub_settings.get("ai_budget_alerted_month") == stamp:
        st["already_notified"] = True
        return st

    from app.services import notify
    if st["level"] == "exceeded":
        title = f"本月 AI 花费已超预算：${st['spend_usd']:.2f}"
        body = (f"预算 ${st['limit_usd']:.2f}，已用 {st['ratio'] * 100:.0f}%。\n"
                f"**自动任务（定时/调度）已暂停**，手动操作不受影响。\n"
                f"这是按公开价目表对 token 的本地估算，不是账单。")
    else:
        title = f"本月 AI 花费已用到 {st['ratio'] * 100:.0f}%"
        body = (f"已花 ${st['spend_usd']:.2f} / 预算 ${st['limit_usd']:.2f}。\n"
                f"到 100% 时自动任务会暂停，手动操作不受影响。")
    sent = notify.send_sync("budget.exceeded", title, body, level="warn")
    # **发出去了才记账**。发失败还记上的话，这一档就再也不会提醒了。
    if sent:
        hub_settings.save({"ai_budget_alerted_month": stamp})
    st["notified"] = sent
    return st
