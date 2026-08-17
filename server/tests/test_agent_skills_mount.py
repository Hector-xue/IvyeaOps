"""把 Skill 中心的技能库挂给 IvyeaAgent。

这层现在只做一件事：往 agent 的 settings.json 里写一个目录。**不复制、不转格式** ——
agent 从 v1.14 起直接认 SKILL.md + frontmatter。
"""
from __future__ import annotations

import json

import pytest

from app.services import agent_skills


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """技能库和 agent home 都指到临时目录，绝不碰真实的 ~/.ivyea。"""
    hub = tmp_path / "skills"
    (hub / "amazon" / "demo").mkdir(parents=True)
    (hub / "amazon" / "demo" / "SKILL.md").write_text("---\nname: demo\n---\n正文", encoding="utf-8")
    monkeypatch.setattr(agent_skills, "SKILLS_ROOT", hub)
    monkeypatch.setenv("IVYEA_HOME", str(tmp_path / "agent-home"))
    return hub, tmp_path / "agent-home"


def _settings(home):
    return json.loads((home / "settings.json").read_text(encoding="utf-8"))


def test_registers_the_amazon_root(_isolated):
    hub, home = _isolated
    res = agent_skills.register_roots()
    assert res["changed"] is True
    assert _settings(home)["skill_roots"] == [str(hub / "amazon")]


def test_is_idempotent(_isolated):
    agent_skills.register_roots()
    second = agent_skills.register_roots()
    assert second["changed"] is False


def test_other_peoples_roots_are_kept(_isolated):
    """用户可能自己挂了别的技能库。我们只负责自己那几条，别把人家的抹掉。"""
    hub, home = _isolated
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text(
        json.dumps({"skill_roots": ["/opt/my-skills"], "model": "deepseek"}), encoding="utf-8")

    agent_skills.register_roots()
    st = _settings(home)
    assert "/opt/my-skills" in st["skill_roots"]
    assert str(hub / "amazon") in st["skill_roots"]
    assert st["model"] == "deepseek"          # 别的设置一个字都不能动


def test_a_corrupt_settings_file_does_not_lose_the_mount(_isolated):
    hub, home = _isolated
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text("{ 这不是 json", encoding="utf-8")

    agent_skills.register_roots()
    assert _settings(home)["skill_roots"] == [str(hub / "amazon")]


def test_missing_domain_dir_is_not_registered(_isolated, monkeypatch, tmp_path):
    """目录不存在就别往配置里塞 —— agent 那边解析不到只会多一条噪音。"""
    monkeypatch.setattr(agent_skills, "SKILLS_ROOT", tmp_path / "nope")
    res = agent_skills.register_roots()
    assert res["roots"] == []


def test_status_counts_what_the_agent_would_see(_isolated):
    hub, _ = _isolated
    (hub / "amazon" / "second").mkdir()
    (hub / "amazon" / "second" / "SKILL.md").write_text("---\nname: second\n---\nx", encoding="utf-8")
    (hub / "amazon" / ".archive" / "old").mkdir(parents=True)
    (hub / "amazon" / ".archive" / "old" / "SKILL.md").write_text("---\nname: old\n---\nx", encoding="utf-8")

    agent_skills.register_roots()
    st = agent_skills.status()
    assert st["count"] == 2                    # 归档的不算 —— 判据要和 agent 一致
    assert st["registered"] is True


def test_status_says_not_registered_before_mounting(_isolated):
    assert agent_skills.status()["registered"] is False


def test_skill_dir_returns_the_real_path(_isolated):
    hub, _ = _isolated
    assert agent_skills.skill_dir("amazon/demo") == str((hub / "amazon" / "demo").resolve())


def test_skill_dir_refuses_to_escape_the_library(_isolated):
    """名字来自请求参数，不能拿它遍历文件系统。"""
    assert agent_skills.skill_dir("../../etc") == ""
    assert agent_skills.skill_dir("amazon/nope") == ""
