"""广告看板的聚合、目标 ACOS、异常判定。

本机账号的广告活动全是 ``paused`` + ``ADVERTISER_ARCHIVED``（2022 年就停了），
报表全 0 —— 真数据验收要等一个在投账号。这份测试用 fixtures 把判断逻辑钉住，
真数据一到即可直接对照。

重点守的是**会让人做错决定**的地方：
* 未开广告的店必须跳过而不是报错（实测 code=102）；
* 目标 ACOS 必须由毛利率推，且 per-campaign 目标优先于店铺均值；
* "有花费零销售额" 要满足最小点击数才报，否则一次点击就报警会被无视；
* 每条异常带的调整意图，方向必须对（亏钱降预算、优于目标才提预算）。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from app.services import ads_board_service as ab


SELLERS = [
    {"sid": 1863, "name": "欧洲-UK", "country": "英国", "region": "EU",
     "marketplace_id": "A1F83G8C2ARO7P", "has_ads_setting": 1},
    {"sid": 1870, "name": "欧洲-TR", "country": "土耳其", "region": "TR",
     "marketplace_id": "A33AVAJ2PDY3EV", "has_ads_setting": 0},
]

CAMPAIGNS = [
    {"campaign_id": 111, "name": "主力手动", "state": "enabled",
     "serving_status": "DELIVERING", "daily_budget": 20.0, "targeting_type": "manual"},
    {"campaign_id": 222, "name": "出预算的", "state": "enabled",
     "serving_status": "CAMPAIGN_OUT_OF_BUDGET", "daily_budget": 10.0, "targeting_type": "auto"},
]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _report(cid: int, *, cost, sales, clicks, impressions, orders):
    return {"campaign_id": cid, "cost": cost, "sales": sales, "clicks": clicks,
            "impressions": impressions, "orders": orders}


@pytest.fixture
def board_env(monkeypatch):
    """装一套可控的数据源；返回一个用来改 fixtures 的字典。"""
    state: Dict[str, Any] = {
        "sellers": SELLERS,
        "campaigns": CAMPAIGNS,
        "reports": {},          # {"YYYY-MM-DD": [row, ...]}
        "margin": 0.40,
        "per_campaign_margin": {},
    }

    async def _fetch(dataset, params=None, *, force=False, ttl=None, caller="panel"):
        params = params or {}
        if dataset == "sellers":
            return {"rows": state["sellers"], "count": len(state["sellers"])}
        if dataset == "sp_campaigns":
            rows = state["campaigns"] if not params.get("offset") else []
            return {"rows": rows, "count": len(rows)}
        if dataset == "sp_campaign_report":
            rows = state["reports"].get(params.get("report_date"), [])
            return {"rows": rows, "count": len(rows)}
        if dataset == "sp_campaign_hour":
            return {"rows": state.get("hour_rows", []), "count": 0}
        return {"rows": [], "count": 0}

    async def _store_margin(sid):
        return state["margin"]

    async def _campaign_margins(sid):
        return state["per_campaign_margin"]

    from app.services import lingxing_optimizer as opt
    monkeypatch.setattr(ab._gw, "is_master_enabled", lambda: True)
    monkeypatch.setattr(ab._data, "fetch_dataset", _fetch)
    monkeypatch.setattr(opt, "_store_margin", _store_margin)
    monkeypatch.setattr(opt, "_campaign_margins", _campaign_margins)
    monkeypatch.setattr(ab, "_cfg", lambda: {
        "lingxing_target_acos_factor": 0.7, "lingxing_target_acos_override": 0,
        "lingxing_margin_override": 0, "lingxing_bid_min_clicks": 15,
        "lingxing_neg_min_clicks": 15, "lingxing_bid_step_pct": 15,
        "lingxing_max_change_pct": 20,
    })
    return state


def _all_days(state, rows: List[Dict[str, Any]], days: int = 2):
    """把同一批行铺到最近 2*days+1 天，方便构造"本期 vs 上期"。"""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    for offset in range(0, 2 * days + 1):
        state["reports"][(now - timedelta(days=offset)).strftime("%Y-%m-%d")] = rows


def test_stores_without_ads_are_skipped_not_errored(board_env):
    board = _run(ab.board(days=1))
    assert board["scope"]["store_count"] == 1
    assert board["scope"]["skipped"] == [
        {"sid": 1870, "name": "欧洲-TR", "reason": "该店未开通广告"}]


def test_target_acos_derived_from_margin(board_env):
    board_env["margin"] = 0.40
    _all_days(board_env, [_report(111, cost=10, sales=100, clicks=50, impressions=1000, orders=5)])
    board = _run(ab.board(days=1))
    store = board["by_store"][0]
    assert store["target"]["breakeven_acos"] == 0.40
    assert store["target"]["target_acos"] == 0.28      # 0.7 × 40%
    assert "毛利率" in store["target"]["note"]


def test_per_campaign_target_beats_store_average(board_env):
    board_env["margin"] = 0.40
    board_env["per_campaign_margin"] = {"111": 0.20}   # 这个活动的产品毛利更低
    _all_days(board_env, [_report(111, cost=10, sales=100, clicks=50, impressions=1000, orders=5)])
    camp = _run(ab.board(days=1))["by_campaign"][0]
    assert camp["target_acos"] == 0.14                 # 0.7 × 20%，不是店铺均值的 0.28


def test_no_margin_data_is_reported_not_faked(board_env):
    """拿不到毛利就说拿不到 —— 不能随便给个默认目标让人照着改。"""
    board_env["margin"] = None
    _all_days(board_env, [_report(111, cost=10, sales=100, clicks=50, impressions=1000, orders=5)])
    board = _run(ab.board(days=1))
    assert board["by_store"][0]["target"]["target_acos"] is None
    assert "没有毛利数据" in board["by_store"][0]["target"]["note"]
    assert board["by_campaign"][0]["health"] == "unknown"


def test_health_bands(board_env):
    board_env["margin"] = 0.40          # 平衡 40%，目标 28%
    _all_days(board_env, [
        _report(111, cost=20, sales=100, clicks=50, impressions=1000, orders=5),   # ACOS 20% → good
        _report(222, cost=35, sales=100, clicks=50, impressions=1000, orders=5),   # ACOS 35% → watch
    ])
    board = _run(ab.board(days=1))
    health = {c["campaign_id"]: c["health"] for c in board["by_campaign"]}
    assert health["111"] == "good"
    assert health["222"] == "watch"


def test_spend_with_no_sales_needs_minimum_clicks(board_env):
    """3 次点击零转化不该报警 —— 噪音会让人把整块面板静音。"""
    _all_days(board_env, [_report(111, cost=5, sales=0, clicks=3, impressions=100, orders=0)])
    codes = [a["code"] for a in _run(ab.board(days=1))["anomalies"]]
    assert "ads.spend_no_sales" not in codes

    _all_days(board_env, [_report(111, cost=30, sales=0, clicks=40, impressions=900, orders=0)])
    anomalies = _run(ab.board(days=1))["anomalies"]
    hit = [a for a in anomalies if a["code"] == "ads.spend_no_sales"]
    assert hit and hit[0]["severity"] == "crit"
    # 止血方向：降预算
    assert hit[0]["intent"]["new_value"] < hit[0]["intent"]["cur_value"]


def test_out_of_budget_only_suggests_raising_when_acos_is_good(board_env):
    board_env["margin"] = 0.40                       # 目标 28%
    # 222 出预算了，ACOS 15% 优于目标 → 该提预算
    _all_days(board_env, [_report(222, cost=15, sales=100, clicks=50, impressions=1000, orders=6)])
    hit = [a for a in _run(ab.board(days=1))["anomalies"] if a["code"] == "ads.out_of_budget"]
    assert hit and hit[0]["intent"] is not None
    assert hit[0]["intent"]["new_value"] > hit[0]["intent"]["cur_value"]

    # 同样出预算，但 ACOS 60% 远超目标 → 不给"提预算"这个按钮
    _all_days(board_env, [_report(222, cost=60, sales=100, clicks=50, impressions=1000, orders=6)])
    hit = [a for a in _run(ab.board(days=1))["anomalies"] if a["code"] == "ads.out_of_budget"]
    assert hit and hit[0]["intent"] is None


def test_acos_breach_flags_below_and_above_breakeven(board_env):
    board_env["margin"] = 0.40                       # 平衡 40%，目标 28%
    _all_days(board_env, [_report(111, cost=45, sales=100, clicks=50, impressions=1000, orders=5)])
    hit = [a for a in _run(ab.board(days=1))["anomalies"] if a["code"] == "ads.acos_breach"]
    assert hit and hit[0]["severity"] == "crit"      # 45% > 平衡线 40% → 在亏
    assert "亏" in hit[0]["detail"]


def test_budget_pacing_uses_today_spend(board_env):
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _all_days(board_env, [_report(111, cost=5, sales=50, clicks=20, impressions=500, orders=3)])
    board_env["reports"][today] = [_report(111, cost=19.5, sales=40, clicks=20,
                                           impressions=500, orders=2)]
    camp = _run(ab.board(days=1))["by_campaign"][0]
    assert camp["today_spend"] == 19.5
    assert camp["budget_used_pct"] == 97.5           # 19.5 / 20
    assert any(a["code"] == "ads.budget_capped" for a in camp["anomalies"])


def test_previous_period_delta(board_env):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    # 本期（昨天）花 100，上期（前天）花 50 → +100%
    board_env["reports"][(now - timedelta(days=1)).strftime("%Y-%m-%d")] = [
        _report(111, cost=100, sales=400, clicks=100, impressions=2000, orders=10)]
    board_env["reports"][(now - timedelta(days=2)).strftime("%Y-%m-%d")] = [
        _report(111, cost=50, sales=200, clicks=50, impressions=1000, orders=5)]
    board = _run(ab.board(days=1))
    assert board["delta"]["spend_pct"] == 100.0
    assert board["delta"]["sales_pct"] == 100.0


def test_today_is_excluded_from_the_trend(board_env):
    """今天的数据还在回填，混进趋势会让人以为掉量了。"""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _all_days(board_env, [_report(111, cost=10, sales=100, clicks=50, impressions=1000, orders=5)])
    board = _run(ab.board(days=2))
    assert today not in [point["date"] for point in board["trend"]]


def test_hourly_is_capped_and_reports_truncation(board_env):
    board_env["hour_rows"] = [
        {"hour": 0, "cost": 1.0, "sales": 4.0, "clicks": 2, "impressions": 30, "orders": 1},
        {"hour": 1, "cost": 2.0, "sales": 0.0, "clicks": 3, "impressions": 40, "orders": 0},
    ]
    ids = [str(i) for i in range(ab.MAX_HOURLY_CAMPAIGNS + 5)]
    res = _run(ab.hourly(1863, ids))
    assert len(res["series"]) == ab.MAX_HOURLY_CAMPAIGNS
    assert res["truncated"] is True
    # 累计花费用于"今天几点会烧完预算"
    assert res["series"][0]["points"][1]["spend_cumulative"] == 3.0


def test_impression_zero_gives_no_one_click_fix(board_env):
    """曝光归零多半是账号/合规问题，给"改预算"按钮是南辕北辙。"""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    board_env["reports"][(now - timedelta(days=1)).strftime("%Y-%m-%d")] = [
        _report(111, cost=0, sales=0, clicks=0, impressions=0, orders=0)]
    board_env["reports"][(now - timedelta(days=2)).strftime("%Y-%m-%d")] = [
        _report(111, cost=50, sales=200, clicks=80, impressions=5000, orders=8)]
    hit = [a for a in _run(ab.board(days=1))["anomalies"] if a["code"] == "ads.impression_zero"]
    assert hit and hit[0]["intent"] is None
