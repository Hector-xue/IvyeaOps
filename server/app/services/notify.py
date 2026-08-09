"""通知渠道：把"事情发生了"送到用户手机上。

自托管的现实是：机器在服务器上跑，人在别处。任务跑完、任务跑挂、这个月钱花超了 ——
这些事如果只写在网页上，用户就得一直盯着网页。

三条设计
--------
* **按 URL 自动认渠道**。飞书、钉钉、企业微信、Slack 的 webhook 报文格式各不相同，
  但用户手里只有一个链接。让他在下拉框里选"我这是哪一家"是把我们的实现细节
  推给他 —— 链接里本来就写着是哪一家，我们自己认。
* **只发用户勾了的事件**。默认只发失败和预算告警：一个每跑完一个任务就响一次的
  机器人，用户三天就会把它静音，那时候真出事也不会看。
* **绝不影响主流程**。通知失败只记日志。因为一个 webhook 超时而让广告审计任务
  失败，是本末倒置。

隐私：报文里只有事件类型、任务名和时间，**不带任何店铺数据、报表内容或密钥**。
webhook 指向的是用户自己配的地址，但仍然是这台机器唯一会主动外联的地方之一。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List
from urllib.parse import urlparse

logger = logging.getLogger("ivyea.services.notify")

# 事件类型 → 说明。设置页直接拿这个渲染勾选框，别在前端再抄一份。
EVENTS: Dict[str, str] = {
    "job.failed": "任务失败",
    "job.succeeded": "任务完成",
    "budget.exceeded": "AI 花费超出预算",
    "approval.needed": "有操作在等你确认",
}

# 默认只发"需要你动手"的两类。见模块开头第二条。
DEFAULT_EVENTS = ["job.failed", "budget.exceeded", "approval.needed"]

_LEVEL_MARK = {"error": "🔴", "warn": "🟠", "info": "🟢"}


def _channel(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "feishu" in host or "larksuite" in host:
        return "feishu"
    if "dingtalk" in host:
        return "dingtalk"
    if "weixin" in host or "qq.com" in host:
        return "wecom"
    if "slack" in host:
        return "slack"
    return "generic"


def build_payload(url: str, title: str, body: str, level: str = "info") -> Dict[str, Any]:
    """按渠道拼报文。**这个函数不发请求**，方便单独测。"""
    mark = _LEVEL_MARK.get(level, "")
    text = f"{mark} IvyeaOps · {title}\n{body}".strip()
    ch = _channel(url)
    if ch == "feishu":
        return {"msg_type": "text", "content": {"text": text}}
    if ch in ("dingtalk", "wecom"):
        # 两家的文本报文形状恰好一样。钉钉的机器人若设了"自定义关键词"安全设置，
        # 关键词必须出现在正文里 —— 上面固定带的 "IvyeaOps" 就是给这个用的，
        # 让用户把关键词填成 IvyeaOps 即可，不必再开一个配置项。
        return {"msgtype": "text", "text": {"content": text}}
    if ch == "slack":
        return {"text": text}
    # 自建接收端：给结构化字段，别让人去正则扒文本。
    return {"source": "ivyea-ops", "level": level, "title": title,
            "body": body, "text": text}


def _redact(text: str) -> str:
    """兜底脱敏。正文本来就不该带密钥，但通知是往外发的，
    多一道拦截比事后解释便宜。"""
    text = re.sub(r"(?i)\b(sk-|ghp_|ivmcp_)[A-Za-z0-9_\-]{8,}", r"\1***", text)
    return re.sub(r"(?i)((?:api[_-]?key|token|secret|password)\W{0,3})\S{6,}", r"\1***", text)


def enabled_events() -> List[str]:
    from app.core import hub_settings
    raw = hub_settings.get("notify_events")
    if isinstance(raw, str) and raw.strip():
        # 本仓库的列表型设置一律以 JSON 字符串落库（见 hub_settings 的
        # lingxing_custom_models）。逗号分隔只是给手改配置文件的人留的后路。
        import json
        try:
            parsed = json.loads(raw)
            raw = parsed if isinstance(parsed, list) else raw.split(",")
        except ValueError:
            raw = raw.split(",")
    if isinstance(raw, list):
        picked = [str(e).strip() for e in raw]
        return [e for e in picked if e in EVENTS]
    return list(DEFAULT_EVENTS)


def webhook_url() -> str:
    from app.core import hub_settings
    # 没单独配就退回原来的告警 webhook，老用户不用重配一遍。
    return str(hub_settings.get("notify_webhook")
               or hub_settings.get("alert_webhook") or "").strip()


async def send(event: str, title: str, body: str = "", *,
               level: str = "info", url: str = "") -> bool:
    """发一条通知。**任何失败都只记日志，返回 False，绝不上抛。**"""
    target = url or webhook_url()
    if not target:
        return False
    if not url and event not in enabled_events():
        return False
    if not target.lower().startswith(("http://", "https://")):
        logger.warning("通知地址不是 http(s)，已跳过")
        return False

    payload = build_payload(target, title, _redact(body), level)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(target, json=payload)
        ok = resp.status_code < 400
    except Exception as exc:  # noqa: BLE001 — 通知失败绝不能影响主流程
        logger.warning("通知发送失败（已忽略）：%s", exc)
        ok = False

    from app.core import audit
    audit.record("notify", event, target=_channel(target),
                 outcome="ok" if ok else "failed")
    return ok


def send_sync(event: str, title: str, body: str = "", *, level: str = "info") -> bool:
    """给同步代码用的版本（后台任务线程里居多）。"""
    target = webhook_url()
    if not target or event not in enabled_events():
        return False
    if not target.lower().startswith(("http://", "https://")):
        return False
    payload = build_payload(target, title, _redact(body), level)
    ok = False
    try:
        import httpx
        resp = httpx.post(target, json=payload, timeout=8)
        ok = resp.status_code < 400
    except Exception as exc:  # noqa: BLE001
        logger.warning("通知发送失败（已忽略）：%s", exc)

    from app.core import audit
    audit.record("notify", event, target=_channel(target),
                 outcome="ok" if ok else "failed")
    return ok


def test(url: str = "") -> Dict[str, Any]:
    """设置页的"发条测试消息"。同步实现，直接给结果。"""
    target = url or webhook_url()
    if not target:
        return {"ok": False, "detail": "还没有配置通知地址"}
    payload = build_payload(target, "测试通知", "如果你收到了这条，通知就配好了。", "info")
    try:
        import httpx
        resp = httpx.post(target, json=payload, timeout=8)
        body = (resp.text or "")[:200]
        if resp.status_code >= 400:
            return {"ok": False, "detail": f"HTTP {resp.status_code}：{body}"}
        # 几家国内的机器人在**成功的 HTTP 200 里**返回失败码（关键词不匹配、
        # 签名错等）。只看状态码会让用户以为配好了，其实一条都收不到。
        low = body.lower()
        if '"errcode":0' in low.replace(" ", "") or '"code":0' in low.replace(" ", "") \
                or "ok" == low.strip() or not body:
            return {"ok": True, "detail": f"已发送（{_channel(target)}）"}
        if '"errcode"' in low or '"code"' in low:
            return {"ok": False, "detail": f"对方返回：{body}"}
        return {"ok": True, "detail": f"已发送（{_channel(target)}）"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
