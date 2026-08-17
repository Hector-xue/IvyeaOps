"""Skill 中心 → IvyeaAgent 技能库的同步。

盯住四件事：格式转换对不对、附属文件有没有跟着走、**手工技能会不会被误删**、
以及内置技能会不会被顶掉。后两条是这个模块最危险的地方。
"""
from __future__ import annotations

import json

import pytest

from app.services import skill_sync


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """两个库都指到临时目录。**绝不能碰真实的 ~/.ivyea/skills** —— 那里有手工技能。"""
    hub = tmp_path / "hub-skills"
    agent = tmp_path / "agent-skills"
    hub.mkdir()
    agent.mkdir()
    monkeypatch.setattr(skill_sync, "AGENT_SKILLS_ROOT", agent)
    monkeypatch.setattr(skill_sync, "SKILLS_ROOT", hub)
    monkeypatch.setattr(skill_sync, "_BUILTIN_IDS_CACHE", set())
    # skill_repo 按自己的 SKILLS_ROOT 找技能，得一起挪。
    from app.services import skill_repo
    monkeypatch.setattr(skill_repo, "SKILLS_ROOT", hub)
    return hub, agent


def _write(hub, name: str, frontmatter: str, body: str = "步骤一。", assets: dict | None = None):
    d = hub / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")
    for rel, content in (assets or {}).items():
        f = d / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    return d


def test_generates_the_manifest_the_agent_actually_reads(_isolated):
    hub, agent = _isolated
    _write(hub, "amazon/search-term-report",
           "name: search-term-report\ndescription: Analyze search term reports\n"
           "description_zh: 分析广告搜索词报表并给出否词建议\nversion: 1.2.0\n"
           "metadata:\n  hermes:\n    tags: [ads, search-term]")

    res = skill_sync.sync_to_agent()
    assert res["synced"] == 1 and not res["errors"]

    d = agent / "amazon" / "search_term_report"
    manifest = json.loads((d / "skill.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "amazon.search_term_report"
    assert manifest["domain"] == "amazon"
    assert manifest["version"] == "1.2.0"
    # 中文描述优先：这台机器上的人用中文提问，描述会进匹配的 haystack。
    assert manifest["description"] == "分析广告搜索词报表并给出否词建议"
    assert (d / "SKILL.md").is_file()


def test_chinese_triggers_are_generated_or_matching_is_dead(_isolated):
    """agent 的 `_terms()` 不分词，一整句中文算一个词 —— 中文查询靠的就是这些短词。"""
    hub, _ = _isolated
    _write(hub, "amazon/negative-guard",
           "name: negative-guard\ndescription: negative keyword guard\n"
           "description_zh: 广告否词护栏，按点击和转化批量加否定关键词")

    skill_sync.sync_to_agent()
    m = json.loads((_isolated[1] / "amazon" / "negative_guard" / "skill.json").read_text(encoding="utf-8"))
    assert "广告" in m["triggers"] and "否词" in m["triggers"]


def test_author_declared_triggers_win(_isolated):
    hub, agent = _isolated
    _write(hub, "amazon/xx", "name: xx\ndescription: d\ntriggers: [清库存, 断货]")
    skill_sync.sync_to_agent()
    m = json.loads((agent / "amazon" / "xx" / "skill.json").read_text(encoding="utf-8"))
    assert m["triggers"][:2] == ["清库存", "断货"]


def test_assets_come_along_and_the_body_says_where_they_are(_isolated):
    """只给说明书、扣下材料，还叮嘱它别去找 —— 这正是这次要修的毛病。"""
    hub, agent = _isolated
    _write(hub, "amazon/xlsx-plan",
           "name: xlsx-plan\ndescription: d",
           body="按 scripts/render.py 渲染，规格见 references/spec.md。",
           assets={"scripts/render.py": "print(1)", "references/spec.md": "# 规格"})

    skill_sync.sync_to_agent()
    d = agent / "amazon" / "xlsx_plan"
    assert (d / "scripts" / "render.py").is_file()
    assert (d / "references" / "spec.md").is_file()

    body = (d / "SKILL.md").read_text(encoding="utf-8")
    assert str(d) in body
    # 位置很重要：自动匹配只截前 700 字，写在末尾等于没写。
    assert str(d) in body[:700]


def test_no_assets_means_no_pointless_directory_note(_isolated):
    hub, agent = _isolated
    _write(hub, "amazon/plain", "name: plain\ndescription: d", body="就按这几步做。")
    skill_sync.sync_to_agent()
    body = (agent / "amazon" / "plain" / "SKILL.md").read_text(encoding="utf-8")
    assert "本技能的文件目录" not in body


def test_handmade_agent_skills_are_never_touched(_isolated):
    """~/.ivyea/skills 里有手工技能（listing_image 就是）。误删一次就没了。"""
    hub, agent = _isolated
    handmade = agent / "amazon" / "listing_image"
    handmade.mkdir(parents=True)
    (handmade / "skill.json").write_text('{"id": "amazon.listing_image"}', encoding="utf-8")
    (handmade / "SKILL.md").write_text("手工写的", encoding="utf-8")

    _write(hub, "amazon/other", "name: other\ndescription: d")
    skill_sync.sync_to_agent()          # 同步一次
    skill_sync.sync_to_agent()          # 再来一次，清理逻辑跑第二遍

    assert (handmade / "SKILL.md").read_text(encoding="utf-8") == "手工写的"


def test_a_handmade_skill_sitting_on_the_target_path_is_not_overwritten(_isolated):
    hub, agent = _isolated
    squatter = agent / "amazon" / "clash"
    squatter.mkdir(parents=True)
    (squatter / "SKILL.md").write_text("手工的", encoding="utf-8")

    _write(hub, "amazon/clash", "name: clash\ndescription: d")
    res = skill_sync.sync_to_agent()

    assert (squatter / "SKILL.md").read_text(encoding="utf-8") == "手工的"
    assert (agent / "amazon" / "clash_hub" / "skill.json").is_file()
    assert res["synced"] == 1


def test_builtin_ids_are_never_shadowed(_isolated, monkeypatch):
    """user 技能按 id 覆盖 builtin。撞名却静默顶替，是最难查的那种故障。"""
    monkeypatch.setattr(skill_sync, "_BUILTIN_IDS_CACHE", {"amazon.budget_pacing"})
    hub, agent = _isolated
    _write(hub, "amazon/budget-pacing", "name: budget-pacing\ndescription: d")

    skill_sync.sync_to_agent()
    m = json.loads((agent / "amazon" / "budget_pacing" / "skill.json").read_text(encoding="utf-8"))
    assert m["id"] == "amazon.budget_pacing_hub"


def test_deleted_skill_is_cleaned_up_next_run(_isolated):
    hub, agent = _isolated
    d = _write(hub, "amazon/gone", "name: gone\ndescription: d")
    skill_sync.sync_to_agent()
    assert (agent / "amazon" / "gone").is_dir()

    import shutil
    shutil.rmtree(d)
    res = skill_sync.sync_to_agent()
    assert res["removed"] == 1
    assert not (agent / "amazon" / "gone").exists()


def test_only_amazon_is_synced(_isolated):
    hub, agent = _isolated
    _write(hub, "amazon/keep", "name: keep\ndescription: d")
    _write(hub, "research/arxiv", "name: arxiv\ndescription: d")
    _write(hub, "gaming/whatever", "name: whatever\ndescription: d")

    res = skill_sync.sync_to_agent()
    assert res["synced"] == 1
    assert (agent / "amazon" / "keep").is_dir()
    assert not (agent / "research").exists() and not (agent / "gaming").exists()


def test_archived_skills_are_skipped(_isolated):
    hub, agent = _isolated
    _write(hub, "amazon/.archive/old", "name: old\ndescription: d")
    _write(hub, "amazon/live", "name: live\ndescription: d")
    res = skill_sync.sync_to_agent()
    assert res["synced"] == 1 and (agent / "amazon" / "live").is_dir()


def test_one_broken_skill_does_not_sink_the_rest(_isolated):
    hub, agent = _isolated
    _write(hub, "amazon/good", "name: good\ndescription: d")
    bad = hub / "amazon" / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\n: : bad yaml : :\n---\n正文", encoding="utf-8")

    res = skill_sync.sync_to_agent()
    assert (agent / "amazon" / "good").is_dir()      # 好的那个照样同步
    assert res["synced"] >= 1


def test_sync_is_idempotent(_isolated):
    hub, agent = _isolated
    _write(hub, "amazon/xx", "name: xx\ndescription: d", assets={"a.md": "A"})
    first = skill_sync.sync_to_agent()
    second = skill_sync.sync_to_agent()
    assert first["synced"] == second["synced"] == 1
    assert second["removed"] == 0
    assert (agent / "amazon" / "xx" / "a.md").read_text(encoding="utf-8") == "A"


def test_status_reports_what_is_registered(_isolated):
    hub, _ = _isolated
    _write(hub, "amazon/xx", "name: xx\ndescription: d")
    skill_sync.sync_to_agent()
    st = skill_sync.status()
    assert st["count"] == 1
    assert st["skills"][0]["id"] == "amazon.xx"
    assert st["skills"][0]["name"] == "amazon/xx"


def test_synced_dir_finds_the_registered_copy(_isolated):
    hub, agent = _isolated
    _write(hub, "amazon/found", "name: found\ndescription: d")
    skill_sync.sync_to_agent()
    assert skill_sync.synced_dir("amazon/found") == str(agent / "amazon" / "found")


def test_synced_dir_is_empty_for_unsynced_skills(_isolated):
    """非 amazon 域的技能没被同步 —— 执行时才会继续说"别去文件系统里找"。"""
    hub, _ = _isolated
    _write(hub, "research/arxiv", "name: arxiv\ndescription: d")
    skill_sync.sync_to_agent()
    assert skill_sync.synced_dir("research/arxiv") == ""


def test_synced_dir_matches_by_source_name_not_by_path(_isolated):
    """两个不同分类下的同名技能，不能把对方的目录认成自己的。"""
    hub, agent = _isolated
    _write(hub, "amazon/dup", "name: dup\ndescription: d")
    skill_sync.sync_to_agent()
    assert skill_sync.synced_dir("other/dup") == ""
