"""Agent 找工具这一步的两个坑。

都是从一次真实会话里挖出来的：用户在任务台说了一句 28 个字的
「把 UK 站某活动的日预算改成 8」，agent 连着调了 7 次 `ivyea_ops_list_tools`
才找到工具 —— 光找工具就烧掉好几轮。
"""
from __future__ import annotations

from app.services.ivyea_ops_tools import list_tools

ADMIN = {"role": "admin", "email": "admin", "permissions": []}
USER = {"role": "user", "email": "u", "permissions": ["listing", "tools", "skill-hub"]}


def test_lingxing_tools_are_findable_by_module():
    """按 module 找得到。

    坑：这些工具原来注册在 `module="admin"` 下，agent 很自然地按
    `module="lingxing"` 去查 —— 返回空，它只好继续换问法。
    """
    rows = list_tools(module="lingxing", principal=ADMIN)["tools"]
    names = {r["name"] for r in rows}
    assert {"lingxing_read", "lingxing_operate", "lingxing_datasets"} <= names


def test_multi_word_query_matches_any_word():
    """多关键词要按词匹配。

    坑：原来是整串子串匹配（`q not in haystack`），于是 agent 打
    「广告 预算 领星 campaign budget」这种自然的一串词时必定返回空。
    """
    rows = list_tools(query="广告 预算 领星 campaign budget", principal=ADMIN)["tools"]
    assert any(r["name"].startswith("lingxing_") for r in rows)


def test_single_word_still_works():
    rows = list_tools(query="lingxing", principal=ADMIN)["tools"]
    assert len(rows) >= 5


def test_module_rename_did_not_open_the_door():
    """**把 module 从 admin 改成 lingxing 不能顺手放宽权限。**

    `lingxing` 不在可授予的模块目录里，所以非管理员照样一个都看不到 ——
    这条测试就是钉住这一点，别让以后有人往目录里加 `lingxing` 时无声地放开写广告。
    """
    assert list_tools(module="lingxing", principal=USER)["tools"] == []
    assert [r for r in list_tools(principal=USER)["tools"] if r["name"].startswith("lingxing_")] == []


def test_write_tool_is_marked_destructive():
    """改广告是花钱的操作，必须带 destructive 标记（前端据此要二次确认）。"""
    rows = list_tools(module="lingxing", principal=ADMIN)["tools"]
    op = next(r for r in rows if r["name"] == "lingxing_operate")
    assert op.get("destructive") is True
    read = next(r for r in rows if r["name"] == "lingxing_read")
    assert not read.get("destructive")
