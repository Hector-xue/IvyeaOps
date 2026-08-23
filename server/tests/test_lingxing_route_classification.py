"""领星路由的读/写判定 —— 受控写通道的 backstop。

这一层不是主防线（主防线是操作开关 + 三重复核 + 人工确认），但它是**唯一**能
阻止只读通道意外打到一条会改线上数据的路由的东西。它曾经用"路径里含某个子串
就算写"来判定，两个方向都错过：

* 把读判成写：``/basicOpen/promotionalActivities/manage/list`` 是"查管理促销
  活动列表"，撞上 ``/manage/`` 被拒 —— 整块促销数据在只读通道上取不到。
* 把写判成读：``/basicOpen/adReport/spTarget/archiveNegatives`` 是真的归档否定
  词，却一个子串都没撞上，backstop 形同虚设。

所以这份测试把**每一条本仓库真实会调的路由**逐条钉住，新增数据集/写操作时
必须在这里登记，避免再犯。
"""
from __future__ import annotations

import pytest

from app.services import lingxing_openapi as lxo


# 本仓库真实会调用的写路由（与 lingxing_operate.OP_TYPES 对应）
WRITE_ROUTES = [
    "/basicOpen/adReport/manage/putSpCampaign",
    "/basicOpen/adReport/manage/putSpKeyword",
    "/basicOpen/adReport/manage/putSpTarget",
    "/basicOpen/adReport/manage/putSpAdGroup",
    "/basicOpen/adReport/spTarget/addKeywords",
    "/basicOpen/adReport/spTarget/addNegativeKeywords",
    "/basicOpen/adReport/spTarget/archiveNegatives",
]

# 本仓库真实会调用的读路由（数据集注册表里的每一条）
READ_ROUTES = [
    "/erp/sc/data/seller/lists",
    "/erp/sc/routing/fba/fbaStock/fbaList",
    "/bd/profit/statistics/open/asin/list",
    "/pb/openapi/newad/spCampaigns",
    "/pb/openapi/newad/spAdGroups",
    "/pb/openapi/newad/spKeywords",
    "/pb/openapi/newad/spTargets",
    "/pb/openapi/newad/spProductAds",
    "/pb/openapi/newad/spCampaignReports",
    "/pb/openapi/newad/spKeywordReports",
    "/pb/openapi/newad/spTargetReports",
    "/pb/openapi/newad/queryWordReports",
    "/pb/openapi/newad/spCampaignHourData",
    "/pb/openapi/newad/spAdvertiseHourData",
    "/basicOpen/promotionalActivities/coupon/list",
    "/basicOpen/promotionalActivities/secKill/list",
    "/basicOpen/promotionalActivities/manage/list",
    "/basicOpen/promotionalActivities/vipDiscount/list",
    "/basicOpen/promotion/listingList",
]


@pytest.mark.parametrize("route", WRITE_ROUTES)
def test_write_routes_classified_write(route):
    assert lxo.classify_route(route) == "write"


@pytest.mark.parametrize("route", READ_ROUTES)
def test_read_routes_classified_read(route):
    assert lxo.classify_route(route) == "read"


def test_manage_as_business_noun_is_not_a_write():
    """回归：'管理促销' 里的 manage 是名词，不能因为它把查询判成写。"""
    assert lxo.classify_route("/basicOpen/promotionalActivities/manage/list") == "read"


def test_archive_is_a_write_verb():
    """回归：归档否定词是写，子串匹配时代漏掉了它。"""
    assert lxo.classify_route("/basicOpen/adReport/spTarget/archiveNegatives") == "write"


def test_verb_match_is_whole_word_not_prefix():
    """``addressList`` 的词头是 address 而不是 add —— 前缀匹配会误伤。"""
    assert lxo.classify_route("/erp/sc/data/addressList") == "read"
    assert lxo.classify_route("/erp/sc/data/settingList") == "read"
    assert lxo.classify_route("/erp/sc/data/delayReports") == "read"


def test_verb_in_middle_segment_still_counts():
    assert lxo.classify_route("/erp/sc/routing/fba/operate/shipment") == "write"


def test_empty_route_is_unknown():
    assert lxo.classify_route("") == "unknown"
    assert lxo.classify_route("   ") == "unknown"


def test_registered_write_routes_match_operate_op_types():
    """写路由登记表必须覆盖 OP_TYPES 里的每一条 —— 两处不一致就是一个能绕过
    backstop 的洞。"""
    from app.services import lingxing_operate as op

    used = set()
    for spec in op.OP_TYPES.values():
        used.add(spec["route"])
        if spec.get("archive_route"):
            used.add(spec["archive_route"])
    missing = used - lxo.WRITE_ROUTES
    assert not missing, f"OP_TYPES 用到但没登记为写路由: {sorted(missing)}"
    for route in used:
        assert lxo.classify_route(route) == "write"
