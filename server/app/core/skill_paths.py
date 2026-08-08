"""Filesystem layout for Skill Studio.

Two roots are kept strictly separate:

  SKILLS_ROOT  = {data_dir}/skills/
      The real skill directories. We only read/write actual skill dirs here
      (no hidden metadata dirs), so any skill scanner pointed at this path
      stays blind to our bookkeeping.

  STUDIO_ROOT  = {data_dir}/skill-studio/
      All Studio state lives here — snapshots, trash, settings, audit log.
      Completely outside the scanner's path.

历史包袱：这两个目录原来在 ``~/.hermes/`` 下面，因为技能最初是交给 hermes 运行的。
技能执行早已改由 ivyea-agent 承担，但数据还留在一个以别的工具命名的目录里 ——
备份要多备一处、Docker 要多挂一个卷、看着也误导人。现在归位到 ``data_dir``
（其余所有 sqlite 库都在那儿），并在启动时做一次性搬迁，旧目录原样保留当备份。

注意 ``~/.hermes`` **不会**被清掉：claude / codex 的可执行文件也装在
``~/.hermes/node/bin``，那个目录是共用工具链，不归我们管。

Both paths are override-able via env vars for tests; production just uses
the defaults.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from app.core.config import settings


# --- Roots -----------------------------------------------------------------

# 搬迁前的老位置。只用于一次性迁移和"要不要迁"的判断，不再作为默认值。
_LEGACY_HOME = Path(
    os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))
).resolve()
LEGACY_SKILLS_ROOT: Path = _LEGACY_HOME / "skills"
LEGACY_STUDIO_ROOT: Path = _LEGACY_HOME / "skill-studio"

_SKILLS_ENV = os.getenv("IVYEA_OPS_SKILLS_ROOT")
_STUDIO_ENV = os.getenv("IVYEA_OPS_STUDIO_ROOT")

# 直接认 IVYEA_OPS_DATA_DIR（与 config.Settings 同一个开关），这样重载本模块就能
# 换根，不必去 reload app.core.config —— 那会造出第二个 settings 实例，而别的模块
# 还握着旧那个，测试之间会互相串。
_DATA_DIR = Path(os.getenv("IVYEA_OPS_DATA_DIR") or settings.data_dir)

SKILLS_ROOT: Path = Path(_SKILLS_ENV or (_DATA_DIR / "skills")).resolve()
STUDIO_ROOT: Path = Path(_STUDIO_ENV or (_DATA_DIR / "skill-studio")).resolve()

# 迁移只在用默认路径时做。测试/自定义部署显式指定了根目录，就不该把生产数据
# 复制进人家的目录。
_MIGRATION_ALLOWED = not (_SKILLS_ENV or _STUDIO_ENV)
_MIGRATED_MARKER = ".migrated-from-hermes"


# --- Studio sub-paths ------------------------------------------------------

SNAPSHOTS_DIR: Path = STUDIO_ROOT / "snapshots"
TRASH_DIR: Path = STUDIO_ROOT / "trash"
SETTINGS_FILE: Path = STUDIO_ROOT / "settings.json"
AUDIT_LOG_FILE: Path = STUDIO_ROOT / "audit.log"


# Skills bundled with the install (shipped so fresh installs have the Amazon
# audit / listing skills the boards depend on, even without a pre-existing
# Hermes skill library).
#
# Source layout: server/app/core/skill_paths.py → parents[3] = repo root.
# Frozen exe (Windows x64 / PyInstaller --onefile): __file__ lives inside the
# temp _MEIPASS extraction dir, so parents[3] is wrong — the skills are shipped
# next to IvyeaOpsServer.exe instead. Resolve relative to the exe when frozen.
def _bundled_skills_root() -> Path:
    # 可用 IVYEA_OPS_BUNDLED_SKILLS 指向别处：打包方想换一套自带技能时用得上，
    # 测试则指向一个空目录，让"播种"变成 no-op —— 否则每个用 tmp 目录当
    # SKILLS_ROOT 的测试都会被仓库自带技能污染（"期望 3 个，实际 7 个"）。
    # 必须是环境变量而不是 monkeypatch 模块属性：多个测试 fixture 会
    # importlib.reload 这个模块，reload 会把模块属性打回原值，环境变量才留得住。
    override = os.getenv("IVYEA_OPS_BUNDLED_SKILLS")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return (Path(sys.executable).resolve().parent / "skills")
    return (Path(__file__).resolve().parents[3] / "skills")


BUNDLED_SKILLS: Path = _bundled_skills_root().resolve()


def seed_bundled_skills() -> int:
    """Copy repo-bundled skills into SKILLS_ROOT, never overwriting an existing
    skill. Returns how many were newly seeded. Runs on every startup (cheap +
    idempotent), so install.sh / install.ps1 / Docker / manual all get them."""
    import shutil
    if not BUNDLED_SKILLS.is_dir():
        return 0
    SKILLS_ROOT.mkdir(parents=True, exist_ok=True)
    seeded = 0
    for skill_md in BUNDLED_SKILLS.rglob("SKILL.md"):
        rel = skill_md.parent.relative_to(BUNDLED_SKILLS)
        dest = SKILLS_ROOT / rel
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(skill_md.parent, dest)
            seeded += 1
        except Exception:
            pass
    return seeded


# --- Migration -------------------------------------------------------------

def _merge_tree(src: Path, dst: Path) -> tuple[int, int]:
    """把 src 里目标侧还没有的文件递归补过去。返回 (复制文件数, 失败数)。

    为什么必须递归合并、而不是"顶层条目存在就跳过"：``snapshots`` / ``trash``
    这类目录 ``ensure_studio_dirs`` 每次启动都会先建成空目录，顶层一看"已存在"
    就跳过的话，里面 26 个快照永远搬不过来。已存在的文件一律保留目标侧那份。
    """
    copied = failed = 0
    try:
        dst.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 0, 1
    for item in src.iterdir():
        dest = dst / item.name
        try:
            if item.is_dir():
                sub_c, sub_f = _merge_tree(item, dest)
                copied += sub_c
                failed += sub_f
            elif not dest.exists():
                shutil.copy2(item, dest)
                copied += 1
        except Exception:  # noqa: BLE001 — 单个文件失败不该让启动挂掉
            failed += 1
    return copied, failed


def _migrate_one(legacy: Path, target: Path) -> int:
    """把老目录里还没过来的东西补到新位置。返回本次复制的文件数。

    **复制而不是移动**：老目录留在原地当备份。真出了岔子，把 env 指回去就能
    立刻回到旧数据，不用从备份里捞。代价只是一份几十 MB 的技能文本。

    **靠标记文件判断"迁过没有"，不靠目标空不空**。用后者踩过坑：studio 目录会被
    别的模块提前创建（skill_architect 开机播种 prompt、skill_runs 写执行记录），
    等迁移跑到时目标已经"非空"，快照和 settings.json 就永远留在老目录里了。
    """
    if not legacy.is_dir() or legacy.resolve() == target.resolve():
        return 0
    if (target / _MIGRATED_MARKER).exists():
        return 0
    copied, failed = _merge_tree(legacy, target)
    # 有失败就先不落标记，下次启动还会再补；全部处理完了才封口。
    if not failed:
        try:
            (target / _MIGRATED_MARKER).write_text(str(legacy), encoding="utf-8")
        except OSError:
            pass
    return copied


def migrate_legacy_layout() -> dict[str, int]:
    """一次性把 ~/.hermes/{skills,skill-studio} 搬到 data_dir 下。幂等。"""
    if not _MIGRATION_ALLOWED:
        return {}
    out: dict[str, int] = {}
    for name, legacy, target in (
        ("skills", LEGACY_SKILLS_ROOT, SKILLS_ROOT),
        ("skill-studio", LEGACY_STUDIO_ROOT, STUDIO_ROOT),
    ):
        try:
            n = _migrate_one(legacy, target)
        except Exception:  # noqa: BLE001
            n = 0
        if n:
            out[name] = n
    return out


# --- Setup -----------------------------------------------------------------

def ensure_studio_dirs() -> None:
    """Create Studio directories on startup. Idempotent.

    先做一次性搬迁（老装机的数据还在 ~/.hermes 下），再建目录、播种自带技能。
    顺序重要：搬迁必须发生在 seed_bundled_skills 之前，否则空的新目录会先被
    自带技能填上，看起来"非空"，真正的用户技能就永远搬不过来了。
    """
    import logging
    log = logging.getLogger(__name__)
    try:
        moved = migrate_legacy_layout()
        for name, n in moved.items():
            log.info("migrated %d %s entrie(s) from the legacy ~/.hermes layout", n, name)
    except Exception:  # noqa: BLE001
        pass

    STUDIO_ROOT.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    # settings.json and audit.log are created on first write.
    try:
        n = seed_bundled_skills()
        if n:
            log.info("seeded %d bundled skill(s) into %s", n, SKILLS_ROOT)
    except Exception:
        pass


def studio_paths_summary() -> dict[str, str]:
    """Debug helper: return current path layout for logging."""
    return {
        "skills_root": str(SKILLS_ROOT),
        "skills_root_exists": str(SKILLS_ROOT.exists()),
        "legacy_skills_root": str(LEGACY_SKILLS_ROOT),
        "legacy_still_present": str(LEGACY_SKILLS_ROOT.is_dir()),
        "studio_root": str(STUDIO_ROOT),
        "snapshots_dir": str(SNAPSHOTS_DIR),
        "trash_dir": str(TRASH_DIR),
        "settings_file": str(SETTINGS_FILE),
        "audit_log": str(AUDIT_LOG_FILE),
    }
