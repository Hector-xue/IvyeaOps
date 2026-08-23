"""驾驶舱接口的契约与权限。

守两件事：

1. **权限**：这两块读的是自家店铺经营数据，``/ads/adjust`` 还能创建真会改线上
   投放的工单 —— 必须和 ``/api/lingxing`` 一样是管理员专属。用结构断言检查每条
   路由都挂了 require_admin（为什么不是"拿普通用户打一发看 403"，见那条测试的
   注释：require_admin 在无 cookie 时会信任测试的 override，那样测的是后路）。
2. **契约**：前端按字段名取值，字段改名了测试要红。这里断言的是前端真正用到的
   那几个字段。

不联网：领星那层全部打桩。
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


def _auth_deps(app, names=("require_admin", "require_user")):
    """把路由树上真正在用的鉴权依赖函数对象捞出来（按函数名认）。

    **为什么不能直接写 `dependency_overrides[security.require_admin]`**：
    `dependency_overrides` 拿函数对象当键。别的测试文件会 `reload(app.core.security)`，
    但 `app.routers.*` 这些模块**不会**跟着重载（它们已经在 sys.modules 里），
    于是路由里绑的还是老的函数对象，而 `import app.core.security` 拿到的是新的 ——
    两个对象不相等，override 静静地不生效，请求照样走真鉴权拿 401。
    单跑这个文件全绿、跟着全量跑就红，根因就是这个。

    反过来"我也 reload 一次 security"能治好自己，却会把同样的问题甩给下一个
    只 reload config+main 的测试文件（实测把 test_ad_audit 打红了 14 条）。
    按名字从路由上取，谁都不用改。
    """
    found = set()
    for route in app.routes:
        stack = list(getattr(getattr(route, "dependant", None), "dependencies", []) or [])
        while stack:
            dep = stack.pop()
            if dep.call is not None and getattr(dep.call, "__name__", "") in names:
                found.add(dep.call)
            stack.extend(dep.dependencies)
    return found


@pytest.fixture
def app_mod(monkeypatch):
    monkeypatch.setenv("IVYEA_OPS_DEV_MODE", "1")
    monkeypatch.setenv("IVYEA_OPS_ALLOWED_ORIGINS", "https://test.example.com")
    monkeypatch.setenv("IVYEA_OPS_SECRET", "test-secret")
    from app.core import config as cfg_mod
    importlib.reload(cfg_mod)
    from app import main as main_mod
    importlib.reload(main_mod)
    return main_mod


@pytest.fixture
def admin_client(app_mod, monkeypatch):
    for dep in _auth_deps(app_mod.app):
        app_mod.app.dependency_overrides[dep] = lambda: "admin"

    async def _promo_board(sids=None, **kw):
        return {
            "generated_at": "2026-08-23T00:00:00+00:00", "source": "lingxing",
            "scope": {"sids": [1863], "store_count": 1, "horizon_days": 30,
                      "include_ended": False},
            "stores": [{"sid": 1863, "name": "欧洲-UK", "code": "UK", "currency": "GBP"}],
            "items": [{
                "id": "coupon:1863:c1", "promotion_id": "c1", "kind": "coupon",
                "kind_label": "优惠券", "name": "Save £2", "sid": 1863, "store": "欧洲-UK",
                "marketplace": "UK", "country": "英国", "tz": "Europe/London",
                "currency_icon": "£", "status_raw": "ACTIVE", "status_label": "进行中",
                "start_at": "2026-08-22T00:00:00+01:00", "end_at": "2026-08-25T23:59:00+01:00",
                "start_local": "2026-08-22 00:00:00", "end_local": "2026-08-25 23:59:00",
                "seconds_to_start": -1000, "seconds_to_end": 100000, "phase": "running",
                "budget": 1000.0, "cost": 850.0, "budget_used_pct": 85.0,
                "sales_amount": 1.0, "sales_volume": 1.0, "discount": "£2",
                "last_sync_time": None, "sync_age_hours": None,
                "asins": [], "asin_count": 0,
            }],
            "summary": {"total": 1, "running": 1, "upcoming": 0, "ending_24h": 0,
                        "ending_72h": 1, "budget_risk": 1, "with_asin": 0},
            "freshness": {"known": False, "stale": False, "age_hours": None, "hint": "x"},
            "errors": [],
        }

    async def _ads_board(sids=None, **kw):
        return {
            "generated_at": "2026-08-23T00:00:00+00:00", "source": "lingxing",
            "scope": {"sids": [1863], "days": 7, "store_count": 1,
                      "skipped": [{"sid": 1870, "name": "欧洲-TR", "reason": "该店未开通广告"}]},
            "totals": {"spend": 10.0, "sales": 40.0, "orders": 2, "clicks": 20,
                       "impressions": 500, "acos": 0.25, "roas": 4.0, "ctr": 0.04,
                       "cvr": 0.1, "cpc": 0.5},
            "prev_totals": None,
            "delta": {"spend_pct": None, "sales_pct": None, "orders_pct": None,
                      "acos_delta": None},
            "by_store": [], "by_campaign": [], "campaign_count": 0,
            "trend": [], "anomalies": [], "errors": [],
        }

    async def _hourly(sid, ids, date=None, force=False):
        return {"sid": sid, "date": "2026-08-23", "series": [], "truncated": False,
                "max_campaigns": 12, "errors": []}

    from app.services import ads_board_service, promotions_service
    monkeypatch.setattr(promotions_service, "board", _promo_board)
    monkeypatch.setattr(ads_board_service, "board", _ads_board)
    monkeypatch.setattr(ads_board_service, "hourly", _hourly)

    with TestClient(app_mod.app) as c:
        c.headers.update({"Origin": "https://test.example.com"})
        yield c
    app_mod.app.dependency_overrides.clear()


def test_promotions_contract(admin_client):
    r = admin_client.get("/api/cockpit/promotions")
    assert r.status_code == 200
    body = r.json()
    # 前端真正读的字段
    assert body["summary"]["ending_24h"] == 0
    item = body["items"][0]
    for key in ("id", "kind", "name", "end_at", "phase", "seconds_to_end",
                "budget_used_pct", "asin_count", "status_label", "marketplace"):
        assert key in item, f"契约缺字段 {key}"
    assert "hint" in body["freshness"]


def test_ads_contract(admin_client):
    r = admin_client.get("/api/cockpit/ads?days=7")
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["acos"] == 0.25
    # 未开广告的店必须以"跳过 + 原因"的形式出现，不是静默消失也不是报错
    assert body["scope"]["skipped"][0]["reason"] == "该店未开通广告"


def test_hourly_requires_campaign_ids(admin_client):
    assert admin_client.get("/api/cockpit/ads/hourly?sid=1863&campaign_ids=").status_code == 400
    assert admin_client.get("/api/cockpit/ads/hourly?sid=1863&campaign_ids=1,2").status_code == 200


def test_status_exposes_fast_lane_and_sync(admin_client):
    body = admin_client.get("/api/cockpit/status").json()
    assert set(body["fast_lane"]) >= {"enabled", "max_pct", "require_human"}
    assert set(body["sync"]) >= {"enabled", "interval_minutes", "last_finished_at"}


def test_adjust_creates_a_ticket_and_does_not_execute(admin_client, monkeypatch):
    """发起调整 ≠ 执行。这条接口只能造出一张待确认的工单。"""
    created = {}

    async def _create(payload):
        created.update(payload)
        return {"id": "t1", "status": "reviewing", "intent": payload,
                "fast_lane": None, "guardrail": None, "reviews": None,
                "created_at": "", "source": "manual", "snapshot": None,
                "result": None, "decided_by": "", "error": ""}

    from app.services import lingxing_operate
    monkeypatch.setattr(lingxing_operate, "create_manual_ticket", _create)
    r = admin_client.post("/api/cockpit/ads/adjust", json={
        "op_type": "campaign_budget", "sid": 1863, "target_id": "111",
        "target_name": "主力", "cur_value": 20, "new_value": 17,
    })
    assert r.status_code == 200
    assert r.json()["status"] == "reviewing"
    assert created["new_value"] == 17
    assert created["rationale"] == "(驾驶舱直调)"


def test_every_cockpit_route_is_admin_gated(app_mod):
    """驾驶舱这两块读的是自家经营数据、还能发起改投放的工单 —— 每一条路由都必须
    挂着 require_admin。

    这里做的是**结构断言**而不是"用普通用户打一发看是不是 403"：
    ``require_admin`` 在没有会话 cookie 时会信任上游被 override 的 require_user
    （见它自己的 docstring，那是给测试留的后路）。所以拿 TestClient + override
    去模拟"普通用户"，测的其实是那条后路，不是生产行为。直接检查依赖挂没挂上，
    才是真的在守生产的那道闸。
    """
    def dep_names(route):
        out = set()
        stack = list(getattr(route.dependant, "dependencies", []))
        while stack:
            d = stack.pop()
            if d.call is not None:
                out.add(getattr(d.call, "__name__", ""))
            stack.extend(d.dependencies)
        return out

    cockpit = [r for r in app_mod.app.routes
               if getattr(r, "path", "").startswith("/api/cockpit")]
    assert cockpit, "没有找到任何 /api/cockpit 路由"
    for route in cockpit:
        assert "require_admin" in dep_names(route), f"{route.path} 没有挂 require_admin"


def test_cockpit_is_gated_like_lingxing(app_mod):
    """和 /api/lingxing 同源同敏感度 —— 两处权限必须一致，别一边紧一边松。"""
    def has_admin(prefix):
        for r in app_mod.app.routes:
            if getattr(r, "path", "").startswith(prefix):
                stack = list(getattr(r.dependant, "dependencies", []))
                names = set()
                while stack:
                    d = stack.pop()
                    if d.call is not None:
                        names.add(getattr(d.call, "__name__", ""))
                    stack.extend(d.dependencies)
                if "require_admin" not in names:
                    return False
        return True

    assert has_admin("/api/lingxing")
    assert has_admin("/api/cockpit")
