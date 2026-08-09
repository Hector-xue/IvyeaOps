"""通知渠道与 AI 预算。

盯的是两件容易出事的事：**发出去的报文对不对家**（认错渠道 = 用户一条都收不到，
而且 HTTP 还是 200，排查起来很痛苦），以及**通知失败绝不能反噬主流程**。
"""
from __future__ import annotations

import pytest

from app.services import budget, notify


# ── 按 URL 认渠道 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("url,expect", [
    ("https://open.feishu.cn/open-apis/bot/v2/hook/abc", "feishu"),
    ("https://open.larksuite.com/open-apis/bot/v2/hook/abc", "feishu"),
    ("https://oapi.dingtalk.com/robot/send?access_token=x", "dingtalk"),
    ("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x", "wecom"),
    ("https://hooks.slack.com/services/T/B/x", "slack"),
    ("https://ops.example.com/hooks/ivyea", "generic"),
])
def test_channel_detection(url, expect):
    assert notify._channel(url) == expect


def test_payload_shape_per_channel():
    """各家的字段名不一样，拼错了对方会用 200 静默丢掉。"""
    fs = notify.build_payload("https://open.feishu.cn/x", "标题", "正文")
    assert fs["msg_type"] == "text" and "text" in fs["content"]

    dd = notify.build_payload("https://oapi.dingtalk.com/x", "标题", "正文")
    assert dd["msgtype"] == "text" and "content" in dd["text"]

    sl = notify.build_payload("https://hooks.slack.com/x", "标题", "正文")
    assert set(sl) == {"text"}

    gen = notify.build_payload("https://example.com/x", "标题", "正文", "error")
    assert gen["source"] == "ivyea-ops" and gen["level"] == "error"
    assert gen["title"] == "标题"      # 自建接收端拿结构化字段，不用扒文本


def test_dingtalk_keyword_is_always_present():
    """钉钉机器人若开了"自定义关键词"，关键词不在正文里就会被拒。
    正文固定带 IvyeaOps，用户把关键词填成它即可 —— 不必再开一个配置项。"""
    for url in ("https://oapi.dingtalk.com/x", "https://open.feishu.cn/x"):
        blob = str(notify.build_payload(url, "任务失败", "原因"))
        assert "IvyeaOps" in blob


def test_secrets_are_scrubbed_before_leaving_the_machine():
    """正文本来就不该带密钥，但这是往外发的，多一道拦截比事后解释便宜。"""
    dirty = "失败：api_key=sk-abcdef1234567890 token: ghp_ABCDEFGH12345678"
    clean = notify._redact(dirty)
    assert "sk-abcdef1234567890" not in clean
    assert "ghp_ABCDEFGH12345678" not in clean


# ── 事件开关 ────────────────────────────────────────────────────────────
def test_default_events_exclude_success(monkeypatch):
    """默认不发"任务完成"。每跑完一个任务就响一次的机器人会被静音，
    那时候真出事也没人看。"""
    from app.core import hub_settings
    monkeypatch.setattr(hub_settings, "get", lambda k, *a: "" if k == "notify_events" else "")
    events = notify.enabled_events()
    assert "job.failed" in events
    assert "job.succeeded" not in events


def test_events_parse_json_and_reject_unknown(monkeypatch):
    from app.core import hub_settings
    monkeypatch.setattr(hub_settings, "get",
                        lambda k, *a: '["job.succeeded", "rm -rf"]' if k == "notify_events" else "")
    assert notify.enabled_events() == ["job.succeeded"]


def test_events_tolerate_comma_separated(monkeypatch):
    """给手改配置文件的人留的后路。"""
    from app.core import hub_settings
    monkeypatch.setattr(hub_settings, "get",
                        lambda k, *a: "job.failed,job.succeeded" if k == "notify_events" else "")
    assert set(notify.enabled_events()) == {"job.failed", "job.succeeded"}


# ── 失败不反噬 ──────────────────────────────────────────────────────────
def test_send_sync_never_raises_when_the_endpoint_is_down(monkeypatch):
    """一个 webhook 超时把广告审计任务带挂，是本末倒置。"""
    from app.core import hub_settings
    monkeypatch.setattr(hub_settings, "get",
                        lambda k, *a: "https://127.0.0.1:1/hook" if "webhook" in k else "")
    import httpx

    def boom(*a, **kw):
        raise httpx.ConnectError("拒绝连接")
    monkeypatch.setattr(httpx, "post", boom)
    assert notify.send_sync("job.failed", "任务失败", "原因") is False


def test_no_webhook_means_silence_not_error(monkeypatch):
    from app.core import hub_settings
    monkeypatch.setattr(hub_settings, "get", lambda k, *a: "")
    assert notify.send_sync("job.failed", "x") is False


def test_non_http_url_is_refused(monkeypatch):
    """防止 file:// 之类的地址被当成 webhook。"""
    from app.core import hub_settings
    monkeypatch.setattr(hub_settings, "get",
                        lambda k, *a: "file:///etc/passwd" if "webhook" in k else "")
    assert notify.send_sync("job.failed", "x") is False


def test_disabled_event_is_not_sent(monkeypatch):
    from app.core import hub_settings
    monkeypatch.setattr(hub_settings, "get", lambda k, *a: (
        "https://open.feishu.cn/x" if "webhook" in k
        else '["job.failed"]' if k == "notify_events" else ""))
    sent = []
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: sent.append(a) or _Resp(200))
    assert notify.send_sync("job.succeeded", "完成") is False
    assert not sent


class _Resp:
    def __init__(self, code, text=""):
        self.status_code = code
        self.text = text


# ── 测试按钮：200 里藏着的失败 ───────────────────────────────────────────
def test_test_button_catches_failure_hidden_in_a_200(monkeypatch):
    """几家国内机器人在成功的 HTTP 200 里返回失败码（关键词不匹配、签名错）。
    只看状态码会让用户以为配好了，其实一条都收不到 —— 这正是最难查的那种。"""
    import httpx
    monkeypatch.setattr(httpx, "post",
                        lambda *a, **kw: _Resp(200, '{"errcode":310000,"errmsg":"keyword not in content"}'))
    res = notify.test("https://oapi.dingtalk.com/x")
    assert res["ok"] is False
    assert "310000" in res["detail"]


def test_test_button_accepts_a_real_success(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _Resp(200, '{"errcode":0,"errmsg":"ok"}'))
    assert notify.test("https://oapi.dingtalk.com/x")["ok"] is True


# ── 预算 ────────────────────────────────────────────────────────────────
def test_budget_off_by_default(monkeypatch):
    from app.core import hub_settings
    monkeypatch.setattr(hub_settings, "get", lambda k, *a: 0)
    st = budget.status()
    assert st["enabled"] is False and st["exceeded"] is False


def test_budget_exceeded_notifies_once_per_month(monkeypatch):
    from app.core import hub_settings
    store = {"ai_budget_monthly_usd": 10, "ai_budget_alerted_month": ""}
    monkeypatch.setattr(hub_settings, "get", lambda k, *a: store.get(k, ""))
    monkeypatch.setattr(hub_settings, "save", lambda d: store.update(d) or store)
    # 替身要收 **kw：status() 会带 cached= 调它（顶栏走只读缓存那条路）。
    monkeypatch.setattr(budget, "month_spend_usd", lambda **kw: 12.5)

    sent = []
    monkeypatch.setattr(notify, "send_sync",
                        lambda *a, **kw: sent.append(a) or True)

    first = budget.check_and_notify()
    assert first["exceeded"] and first["notified"] is True and len(sent) == 1

    second = budget.check_and_notify()
    assert second.get("already_notified") is True
    assert len(sent) == 1          # 同一档只响一次，否则等于逼用户关掉提醒


def test_failed_notification_is_not_recorded_as_sent(monkeypatch):
    """发失败还记账的话，这个月就再也不会提醒了。"""
    from app.core import hub_settings
    store = {"ai_budget_monthly_usd": 10, "ai_budget_alerted_month": ""}
    monkeypatch.setattr(hub_settings, "get", lambda k, *a: store.get(k, ""))
    monkeypatch.setattr(hub_settings, "save", lambda d: store.update(d) or store)
    monkeypatch.setattr(budget, "month_spend_usd", lambda **kw: 99.0)
    monkeypatch.setattr(notify, "send_sync", lambda *a, **kw: False)

    budget.check_and_notify()
    assert store["ai_budget_alerted_month"] == ""


def test_spend_lookup_failure_does_not_raise(monkeypatch):
    """取不到花费就当没超，绝不能让预算检查把设置页打挂。"""
    import app.routers.monitor as mon
    monkeypatch.setattr(mon, "token_usage", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("挂了")))
    assert budget.month_spend_usd() == 0.0
