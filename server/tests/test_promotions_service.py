"""促销倒计时的归一化逻辑。

本机账号没有任何促销数据（实测四个接口 code=0 total=0，历史窗口也空），所以
真数据验收要等有在投账号。这份测试用 fixtures 把**每一条会算错的地方**钉住：

* 时区：站点裸时间必须按店铺时区解释，不能按服务器时区；
* 金额：``"JP¥10,084.0"`` 这种带货币符号和千分位的字符串要能解析；
* 相位：已取消/已过期的活动没有"还剩多久"，不能混进倒计时；
* ASIN：活动列表接口不带 ASIN，必须从 listing 维度按 promotion_id 挂回来；
* 新鲜度：插件掉线时接口照样返回旧数据，必须能识别出来。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.services import promotions_service as ps


SELLERS = [
    {"sid": 1863, "name": "欧洲-UK", "seller_id": "A23", "country": "英国",
     "region": "EU", "marketplace_id": "A1F83G8C2ARO7P", "has_ads_setting": 1},
    {"sid": 1872, "name": "日本-JP", "seller_id": "A24", "country": "日本",
     "region": "JP", "marketplace_id": "A1VC38T7YXB528", "has_ads_setting": 1},
]


def _fake_fetch(datasets):
    """构造一个替代 lingxing_data.fetch_dataset 的协程。"""
    async def _fetch(dataset, params=None, *, force=False, ttl=None, caller="panel"):
        rows = datasets.get(dataset, [])
        return {"dataset": dataset, "rows": rows, "count": len(rows),
                "synced_at": "", "cached": False, "params": params or {}}
    return _fetch


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def patch_gateway(monkeypatch):
    monkeypatch.setattr(ps._gw, "is_master_enabled", lambda: True)

    def _install(datasets):
        merged = {"sellers": SELLERS, **datasets}
        monkeypatch.setattr(ps._data, "fetch_dataset", _fake_fetch(merged))
    return _install


#: 测试里用店铺代码指定站点，偏移量**实时从 tzdata 取**。写死 "伦敦=UTC+1"
#: 会在冬令时那半年制造假失败 —— 这类跟着日历漂的测试比没有测试更糟。
_TZ_BY_STORE = {1863: "A1F83G8C2ARO7P", 1872: "A1VC38T7YXB528"}


def _in_hours(hours: float, sid: int = 1863) -> str:
    """生成"该店铺站点当地时间 N 小时后"的裸字符串（领星就是这个格式）。"""
    from app.core import marketplaces as mkt

    site_now = datetime.now(mkt.tzinfo(_TZ_BY_STORE[sid]))
    return (site_now + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def test_money_parses_currency_prefixed_strings():
    assert ps._money("JP¥10,084.0") == 10084.0
    assert ps._money("0.00") == 0.0
    assert ps._money(12.5) == 12.5
    # 空 / 无数字 → None，不是 0：「没有预算」和「预算 0」在界面上必须能区分
    assert ps._money("") is None
    assert ps._money(None) is None
    assert ps._money("—") is None


def test_site_time_is_interpreted_in_store_timezone():
    """同一个裸时间字符串，UK 店和 JP 店必须解析成不同的绝对时刻。"""
    from app.core import marketplaces as mkt

    raw = "2026-08-24 23:59:00"
    uk = ps._parse_site_time(raw, mkt.tzinfo("A1F83G8C2ARO7P"))
    jp = ps._parse_site_time(raw, mkt.tzinfo("A1VC38T7YXB528"))
    assert uk is not None and jp is not None
    # 伦敦夏令时 UTC+1，东京 UTC+9 —— 同样的墙上时间差 8 小时
    assert (uk - jp).total_seconds() == 8 * 3600


def test_countdown_uses_site_timezone_not_server(patch_gateway):
    """回归：按服务器时区算会把 UK 的倒计时算错几个小时。"""
    end_local = _in_hours(5)  # 伦敦当地时间的 5 小时后
    patch_gateway({"promo_coupon": [{
        "promotion_id": "c1", "name": "Save £2", "sid": 1863,
        "origin_status": "ACTIVE", "currency_icon": "£",
        "budget": "£1000.00", "cost": "100.00",
        "promotion_start_time": _in_hours(-24),
        "promotion_end_time": end_local,
        "last_sync_time": _in_hours(-1),
    }]})
    board = _run(ps.board())
    item = board["items"][0]
    hours_left = item["seconds_to_end"] / 3600
    assert 4.5 < hours_left < 5.5, f"倒计时应约 5 小时，实际 {hours_left}"
    assert item["phase"] == "running"


def test_cancelled_and_expired_are_excluded_from_countdown(patch_gateway):
    patch_gateway({"promo_coupon": [
        {"promotion_id": "ok", "name": "在跑", "sid": 1863, "origin_status": "ACTIVE",
         "promotion_start_time": _in_hours(-2), "promotion_end_time": _in_hours(10)},
        {"promotion_id": "no1", "name": "取消了", "sid": 1863, "origin_status": "CANCELED",
         "promotion_start_time": _in_hours(-2), "promotion_end_time": _in_hours(10)},
        {"promotion_id": "no2", "name": "失败了", "sid": 1863, "origin_status": "FAILED",
         "promotion_start_time": _in_hours(-2), "promotion_end_time": _in_hours(10)},
        {"promotion_id": "no3", "name": "已过期", "sid": 1863, "origin_status": "ACTIVE",
         "promotion_start_time": _in_hours(-48), "promotion_end_time": _in_hours(-10)},
    ]})
    board = _run(ps.board())
    ids = [i["promotion_id"] for i in board["items"]]
    assert ids == ["ok"]


def test_ended_can_be_included_on_request(patch_gateway):
    patch_gateway({"promo_coupon": [
        {"promotion_id": "done", "name": "结束了", "sid": 1863, "origin_status": "EXPIRED",
         "promotion_start_time": _in_hours(-48), "promotion_end_time": _in_hours(-10)},
    ]})
    assert _run(ps.board())["items"] == []
    assert len(_run(ps.board(include_ended=True))["items"]) == 1


def test_soonest_ending_sorts_first(patch_gateway):
    patch_gateway({"promo_coupon": [
        {"promotion_id": "late", "name": "晚", "sid": 1863, "origin_status": "ACTIVE",
         "promotion_start_time": _in_hours(-1), "promotion_end_time": _in_hours(48)},
        {"promotion_id": "soon", "name": "早", "sid": 1863, "origin_status": "ACTIVE",
         "promotion_start_time": _in_hours(-1), "promotion_end_time": _in_hours(3)},
    ]})
    board = _run(ps.board())
    assert [i["promotion_id"] for i in board["items"]] == ["soon", "late"]
    assert board["summary"]["ending_24h"] == 1
    assert board["summary"]["ending_72h"] == 2


def test_budget_usage_and_risk_summary(patch_gateway):
    patch_gateway({"promo_coupon": [{
        "promotion_id": "c1", "name": "券", "sid": 1872, "origin_status": "ACTIVE",
        "currency_icon": "JP¥", "budget": "JP¥10,000.0", "cost": "8,500.00",
        "promotion_start_time": _in_hours(-5, 1872), "promotion_end_time": _in_hours(20, 1872),
    }]})
    item = _run(ps.board())["items"][0]
    assert item["budget"] == 10000.0
    assert item["cost"] == 8500.0
    assert item["budget_used_pct"] == 85.0
    assert _run(ps.board())["summary"]["budget_risk"] == 1


def test_asins_are_attached_from_listing_dimension(patch_gateway):
    """活动列表接口不带 ASIN —— 必须从 listingList 按 promotion_id 挂回来。"""
    patch_gateway({
        "promo_coupon": [{
            "promotion_id": "p-1", "name": "券", "sid": 1863, "origin_status": "ACTIVE",
            "promotion_start_time": _in_hours(-1), "promotion_end_time": _in_hours(12),
        }],
        "promo_listing": [{
            "sid": 1863, "asin": "B0TEST0001", "item_name": "测试商品", "seller_sku": "SKU-1",
            "small_image_url": "https://x/1.jpg", "asin_url": "https://amazon.co.uk/dp/B0TEST0001",
            "sales_price": "8.11", "afn_fulfillable_quantity": "42",
            "promotion_list": [{"promotion_id": "p-1", "category": 1,
                                "category_text": "优惠券", "origin_status": "ACTIVE"}],
        }],
    })
    item = _run(ps.board())["items"][0]
    assert item["asin_count"] == 1
    assert item["asins"][0]["asin"] == "B0TEST0001"
    assert item["asins"][0]["stock"] == 42
    assert item["asins"][0]["sales_price"] == 8.11


def test_listing_only_promotions_are_not_dropped(patch_gateway):
    """两份数据同步节奏不一致时，只在 listing 里出现的促销也要显示。"""
    patch_gateway({
        "promo_coupon": [],
        "promo_listing": [{
            "sid": 1863, "asin": "B0ONLY0001", "item_name": "只在listing里",
            "promotion_list": [{
                "promotion_id": "orphan-1", "name": "孤儿券", "category": 1,
                "category_text": "优惠券", "origin_status": "ACTIVE",
                "promotion_start_time": _in_hours(-1),
                "promotion_end_time": _in_hours(6),
            }],
        }],
    })
    items = _run(ps.board())["items"]
    assert len(items) == 1
    assert items[0]["promotion_id"] == "orphan-1"
    assert items[0]["from_listing_only"] is True
    assert items[0]["asin_count"] == 1


def test_stale_plugin_sync_is_flagged(patch_gateway):
    patch_gateway({"promo_coupon": [{
        "promotion_id": "c1", "name": "券", "sid": 1863, "origin_status": "ACTIVE",
        "promotion_start_time": _in_hours(-30), "promotion_end_time": _in_hours(30),
        "last_sync_time": _in_hours(-50),
    }]})
    fresh = _run(ps.board())["freshness"]
    assert fresh["known"] is True
    assert fresh["stale"] is True
    assert "LINGXING助手" in fresh["hint"]


def test_fresh_plugin_sync_is_not_flagged(patch_gateway):
    patch_gateway({"promo_coupon": [{
        "promotion_id": "c1", "name": "券", "sid": 1863, "origin_status": "ACTIVE",
        "promotion_start_time": _in_hours(-2), "promotion_end_time": _in_hours(30),
        "last_sync_time": _in_hours(-2),
    }]})
    fresh = _run(ps.board())["freshness"]
    assert fresh["stale"] is False


def test_upcoming_beyond_horizon_is_filtered(patch_gateway):
    patch_gateway({"promo_coupon": [
        {"promotion_id": "near", "name": "近", "sid": 1863, "origin_status": "APPROVED",
         "promotion_start_time": _in_hours(24), "promotion_end_time": _in_hours(48)},
        {"promotion_id": "far", "name": "远", "sid": 1863, "origin_status": "APPROVED",
         "promotion_start_time": _in_hours(24 * 45), "promotion_end_time": _in_hours(24 * 50)},
    ]})
    ids = [i["promotion_id"] for i in _run(ps.board(horizon_days=30))["items"]]
    assert ids == ["near"]


def test_one_dataset_failing_does_not_sink_the_board(patch_gateway, monkeypatch):
    """秒杀接口挂了，优惠券照样要出来 —— 并把失败如实报给界面。"""
    async def _fetch(dataset, params=None, *, force=False, ttl=None, caller="panel"):
        if dataset == "sellers":
            return {"rows": SELLERS, "count": len(SELLERS)}
        if dataset == "promo_seckill":
            raise ps._gw.LingXingError("接口超时")
        if dataset == "promo_coupon":
            return {"rows": [{"promotion_id": "c1", "name": "券", "sid": 1863,
                              "origin_status": "ACTIVE",
                              "promotion_start_time": _in_hours(-1),
                              "promotion_end_time": _in_hours(9)}], "count": 1}
        return {"rows": [], "count": 0}

    monkeypatch.setattr(ps._gw, "is_master_enabled", lambda: True)
    monkeypatch.setattr(ps._data, "fetch_dataset", _fetch)
    board = _run(ps.board())
    assert len(board["items"]) == 1
    assert any(e["source"] == "promo_seckill" for e in board["errors"])


def test_seckill_type_label(patch_gateway):
    patch_gateway({"promo_seckill": [{
        "promotion_id": "s1", "name": "秒杀", "sid": 1863, "origin_status": "APPROVED",
        "promotion_type": 2, "product_quantity": 3, "sold_rate": "0.42",
        "promotion_start_time": _in_hours(2), "promotion_end_time": _in_hours(8),
    }]})
    item = _run(ps.board())["items"][0]
    assert item["kind"] == "seckill"
    assert item["type_label"] == "Lightning Deal"
    assert item["phase"] == "upcoming"
    assert 1.5 < item["seconds_to_start"] / 3600 < 2.5
