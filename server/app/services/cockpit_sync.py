"""驾驶舱后台预热 —— 让「打开就有数」成立。

实测：广告看板冷启动，9 个店 × 1 天的报表要 **24.7 秒**（领星 OpenAPI 限流
340ms/次，一次只能拿一个店一天）。7 天窗口就是分钟级。让页面直连去等这个，
用户只会得出"这玩意儿比亚马逊后台还慢"的结论 —— 那这块就白做了。

所以：后台按周期把数据灌进 ``lingxing_cache``，页面永远读缓存。这也正是它
"比后台好用"的技术来源：亚马逊后台每次都现算，我们提前算好了。

**为什么默认关**：预热会持续消耗领星接口配额。开源发出去的默认值必须是
"装上不动它，什么都不会偷偷发生"。用户在配置页打开它，才开始跑。

**这里不发通知。** 促销临期和广告异常的飞书推送在 IvyeaAgent 的巡检里 ——
它有节流去重、状态跃迁判定、卡片版式和审批按钮。在这边再写一套，结果是同一件事
推两遍。这个模块只负责把数据准备好。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core import hub_settings as _hs
from app.core.config import settings

logger = logging.getLogger("ivyea.services.cockpit_sync")

_STATE_NAME = "cockpit_sync.json"
_lock = asyncio.Lock()


def _state_path():
    return settings.data_dir / _STATE_NAME


def _read_state() -> Dict[str, Any]:
    try:
        return json.loads(_state_path().read_text("utf-8"))
    except Exception:  # noqa: BLE001 — 状态文件坏了不该让预热停摆
        return {}


def _write_state(state: Dict[str, Any]) -> None:
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        path = _state_path()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(path)
    except Exception:  # noqa: BLE001
        logger.debug("写 cockpit_sync 状态失败（旁路，已忽略）", exc_info=True)


def status() -> Dict[str, Any]:
    """给 /api/cockpit/status 用：开没开、上次什么时候、拿了多少、错在哪。"""
    state = _read_state()
    last = state.get("last_finished_at")
    age = None
    if last:
        try:
            age = round((datetime.now(timezone.utc)
                         - datetime.fromisoformat(last)).total_seconds() / 60, 1)
        except ValueError:
            age = None
    return {
        "enabled": bool(_hs.get("cockpit_sync_enabled")),
        "interval_minutes": int(_hs.get("cockpit_sync_minutes") or 30),
        "days": int(_hs.get("cockpit_sync_days") or 7),
        "last_started_at": state.get("last_started_at"),
        "last_finished_at": last,
        "age_minutes": age,
        "last_result": state.get("last_result"),
        "running": bool(state.get("running")),
    }


async def sync_once(trigger: str = "scheduled") -> Dict[str, Any]:
    """跑一轮预热：促销清单 + 广告看板（含上一周期，用于环比）。

    **整轮任何失败都只记录不抛** —— 预热是尽力而为的后台活儿，把一次限流
    或者一个店的接口报错升级成 500，只会让"立即刷新"按钮看起来是坏的。
    """
    from app.services import ads_board_service as _ads
    from app.services import lingxing_service as _gw
    from app.services import promotions_service as _promo

    if _lock.locked():
        return {"ok": False, "skipped": True, "reason": "上一轮还在跑"}

    async with _lock:
        started = datetime.now(timezone.utc)
        state = _read_state()
        state.update({"running": True, "last_started_at": started.isoformat(),
                      "last_trigger": trigger})
        _write_state(state)

        result: Dict[str, Any] = {"ok": True, "trigger": trigger, "steps": []}
        t0 = time.monotonic()
        try:
            if not _gw.is_master_enabled():
                raise _gw.LingXingError("领星集成未启用（总开关关闭）")

            days = int(_hs.get("cockpit_sync_days") or 7)
            promo = await _promo.board(force=True)
            result["steps"].append({
                "step": "promotions", "ok": True,
                "items": len(promo.get("items") or []),
                "stale": bool((promo.get("freshness") or {}).get("stale")),
            })

            ads = await _ads.board(days=days, force=True)
            result["steps"].append({
                "step": "ads", "ok": True,
                "campaigns": ads.get("campaign_count", 0),
                "stores": ads.get("scope", {}).get("store_count", 0),
                "skipped_stores": len(ads.get("scope", {}).get("skipped") or []),
                "anomalies": len(ads.get("anomalies") or []),
            })
        except Exception as exc:  # noqa: BLE001 — 见 docstring
            result["ok"] = False
            result["error"] = f"{type(exc).__name__}: {exc}"
            logger.warning("驾驶舱预热失败（已忽略）：%s", exc)

        result["seconds"] = round(time.monotonic() - t0, 1)
        finished = datetime.now(timezone.utc)
        state.update({"running": False, "last_finished_at": finished.isoformat(),
                      "last_result": result})
        _write_state(state)
        return result


async def scheduler_loop() -> None:
    """后台循环。开关和间隔都是**每轮重新读**的 —— 用户在配置页改完不该重启服务。"""
    await asyncio.sleep(90)  # 让启动先稳下来，别和别的开机任务抢限流配额
    while True:
        interval_minutes = 30
        try:
            interval_minutes = max(5, int(_hs.get("cockpit_sync_minutes") or 30))
            if _hs.get("cockpit_sync_enabled"):
                await sync_once(trigger="scheduled")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.debug("驾驶舱预热循环异常（旁路，已忽略）", exc_info=True)
        await asyncio.sleep(interval_minutes * 60)
