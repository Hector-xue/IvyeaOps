"""技能目录从 ~/.hermes 搬到 data_dir 的一次性迁移。

这是在生产机上动真实数据，所以规则写死在测试里：复制而不是移动、幂等、
自带技能的播种必须发生在迁移之后（否则空目录会被先填上，用户技能就永远搬不过来）。
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    """用临时目录重建 skill_paths 模块（它的根是 import 期算的常量）。"""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "legacy"))
    monkeypatch.delenv("IVYEA_OPS_SKILLS_ROOT", raising=False)
    monkeypatch.delenv("IVYEA_OPS_STUDIO_ROOT", raising=False)
    monkeypatch.setenv("IVYEA_OPS_DATA_DIR", str(tmp_path / "data"))

    from app.core import config as config_mod
    importlib.reload(config_mod)
    from app.core import skill_paths as sp
    importlib.reload(sp)
    yield sp
    # 复原，别把重载后的模块留给后面的测试
    monkeypatch.undo()
    importlib.reload(config_mod)
    importlib.reload(sp)


def _make_skill(root, name, body="# hi"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    return d


def test_roots_live_under_data_dir(paths, tmp_path):
    assert paths.SKILLS_ROOT == (tmp_path / "data" / "skills").resolve()
    assert paths.STUDIO_ROOT == (tmp_path / "data" / "skill-studio").resolve()


def test_migrates_legacy_skills_and_keeps_the_original(paths):
    _make_skill(paths.LEGACY_SKILLS_ROOT, "amazon/keyword-report", "# 关键词报告")
    _make_skill(paths.LEGACY_SKILLS_ROOT, "creative/songwriting")
    (paths.LEGACY_STUDIO_ROOT / "snapshots").mkdir(parents=True)
    (paths.LEGACY_STUDIO_ROOT / "settings.json").write_text("{}", encoding="utf-8")

    moved = paths.migrate_legacy_layout()
    assert moved["skills"] == 2 and moved["skill-studio"] == 1

    new = paths.SKILLS_ROOT / "amazon" / "keyword-report" / "SKILL.md"
    assert new.read_text(encoding="utf-8") == "# 关键词报告"
    assert (paths.STUDIO_ROOT / "settings.json").exists()
    # 复制而不是移动：老目录留着当后路
    assert (paths.LEGACY_SKILLS_ROOT / "amazon" / "keyword-report" / "SKILL.md").exists()


def test_migration_is_idempotent(paths):
    _make_skill(paths.LEGACY_SKILLS_ROOT, "amazon/a")
    assert paths.migrate_legacy_layout()["skills"] == 1
    assert paths.migrate_legacy_layout() == {}          # 第二次什么都不做


def test_migration_never_overwrites_existing_new_data(paths):
    """同名条目以新目录那份为准，一个字都不许覆盖。"""
    _make_skill(paths.LEGACY_SKILLS_ROOT, "amazon/a", "老版本")
    _make_skill(paths.SKILLS_ROOT, "amazon/a", "新版本")
    paths.migrate_legacy_layout()
    assert (paths.SKILLS_ROOT / "amazon" / "a" / "SKILL.md").read_text(encoding="utf-8") == "新版本"


def test_partially_populated_target_still_migrates_the_rest(paths):
    """目标目录被别的模块提前建了内容，剩下的照样要搬过来。

    这是实际踩到的坑：skill_architect 开机播种 prompt、skill_runs 写执行记录，
    都会先把 studio 目录填成"非空"。当时用"非空就跳过"做判断，结果 26 个快照和
    settings.json 永远留在了老目录。判断依据必须是标记文件，不是空不空。
    """
    (paths.LEGACY_STUDIO_ROOT / "snapshots" / "s1").mkdir(parents=True)
    (paths.LEGACY_STUDIO_ROOT / "settings.json").write_text('{"a":1}', encoding="utf-8")
    (paths.LEGACY_STUDIO_ROOT / "audit.log").write_text("line", encoding="utf-8")
    # 模拟启动早期就被写入的目录
    (paths.STUDIO_ROOT / "runs" / "x").mkdir(parents=True)

    moved = paths.migrate_legacy_layout()
    assert moved.get("skill-studio") == 2          # settings.json + audit.log（s1 是空目录，无文件）
    assert (paths.STUDIO_ROOT / "snapshots" / "s1").is_dir()
    assert (paths.STUDIO_ROOT / "settings.json").read_text(encoding="utf-8") == '{"a":1}'
    assert (paths.STUDIO_ROOT / "audit.log").exists()
    assert (paths.STUDIO_ROOT / "runs" / "x").is_dir()   # 早先写入的没被动


def test_marker_stops_repeat_migration_even_when_target_looks_empty(paths):
    """封口后就不再回头搬 —— 用户删掉的东西不该被下次启动又复活。"""
    _make_skill(paths.LEGACY_SKILLS_ROOT, "amazon/a")
    assert paths.migrate_legacy_layout()["skills"] == 1
    shutil_rmtree = __import__("shutil").rmtree
    shutil_rmtree(paths.SKILLS_ROOT / "amazon")      # 用户主动删了
    assert paths.migrate_legacy_layout() == {}
    assert not (paths.SKILLS_ROOT / "amazon").exists()


def test_no_legacy_dir_is_a_noop(paths):
    """全新安装：没有老目录，迁移什么也不做，也不该报错。"""
    assert paths.migrate_legacy_layout() == {}


def test_explicit_root_override_disables_migration(tmp_path, monkeypatch):
    """显式指定了根目录（测试/自定义部署）就别把生产数据往人家目录里灌。"""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "legacy"))
    monkeypatch.setenv("IVYEA_OPS_SKILLS_ROOT", str(tmp_path / "custom"))
    from app.core import skill_paths as sp
    importlib.reload(sp)
    try:
        _make_skill(sp.LEGACY_SKILLS_ROOT, "amazon/a")
        assert sp.migrate_legacy_layout() == {}
        assert not (tmp_path / "custom" / "amazon").exists()
    finally:
        monkeypatch.undo()
        importlib.reload(sp)


def test_seeding_runs_after_migration(paths, monkeypatch):
    """顺序护栏：先迁移再播种。

    反过来的话，自带技能会先把空目录填成"非空"，迁移的"目标已有内容就跳过"
    判断随即生效，用户攒的技能就永远留在老目录里了。
    """
    _make_skill(paths.LEGACY_SKILLS_ROOT, "amazon/mine", "用户的技能")
    order: list[str] = []

    real_migrate = paths.migrate_legacy_layout
    real_seed = paths.seed_bundled_skills

    def spy_migrate():
        order.append("migrate")
        return real_migrate()

    def spy_seed():
        order.append("seed")
        return real_seed()

    monkeypatch.setattr(paths, "migrate_legacy_layout", spy_migrate)
    monkeypatch.setattr(paths, "seed_bundled_skills", spy_seed)
    paths.ensure_studio_dirs()

    assert order[:2] == ["migrate", "seed"]
    assert (paths.SKILLS_ROOT / "amazon" / "mine" / "SKILL.md").exists()


def test_pre_created_empty_dirs_do_not_block_their_contents(paths):
    """ensure_studio_dirs 会先把 snapshots/trash 建成空目录 —— 里面的东西照样要搬。

    这是第二次踩的坑：顶层"已存在就跳过"让 26 个快照一个都没过来。
    """
    snap = paths.LEGACY_STUDIO_ROOT / "snapshots" / "zzz-e2e-test-skill" / "v1"
    snap.mkdir(parents=True)
    (snap / "SKILL.md").write_text("快照内容", encoding="utf-8")
    # 模拟启动时先建好的空目录
    (paths.STUDIO_ROOT / "snapshots").mkdir(parents=True)
    (paths.STUDIO_ROOT / "trash").mkdir(parents=True)

    paths.migrate_legacy_layout()
    assert (paths.STUDIO_ROOT / "snapshots" / "zzz-e2e-test-skill" / "v1" / "SKILL.md"
            ).read_text(encoding="utf-8") == "快照内容"


def test_existing_file_wins_over_legacy_copy(paths):
    """同名文件以新目录那份为准，深层嵌套也一样。"""
    old = paths.LEGACY_STUDIO_ROOT / "snapshots" / "s" / "a.md"
    old.parent.mkdir(parents=True)
    old.write_text("老", encoding="utf-8")
    new = paths.STUDIO_ROOT / "snapshots" / "s" / "a.md"
    new.parent.mkdir(parents=True)
    new.write_text("新", encoding="utf-8")

    paths.migrate_legacy_layout()
    assert new.read_text(encoding="utf-8") == "新"
