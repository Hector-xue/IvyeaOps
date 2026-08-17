"""把 Skill 中心的技能库**原地**挂给 IvyeaAgent。

── 这个模块取代了什么 ──────────────────────────────────────────────────────
上一版这里是一个 240 行的「同步器」：因为两边格式不同（Skill 中心是 SKILL.md +
YAML frontmatter，agent 当时只认 SKILL.md + 旁边一个 skill.json），只能把技能连同
附属文件**复制**一份到 `~/.ivyea/skills`，顺便生成 skill.json、往正文里塞一行目录
说明。复制就有一整套麻烦：要防误删手工技能、要防顶掉内置技能、要在每次写操作后
重新同步、还永远慢半拍。

IvyeaAgent v1.14 起直接认 frontmatter，并支持外部技能库根目录（`skill_roots` /
`IVYEA_SKILL_ROOTS`）。于是复制和转换全都不需要了 —— 把目录挂上去就行，Skill 中心
里改完**立即生效**，没有同步这一步。

── 边界 ────────────────────────────────────────────────────────────────────
只挂 amazon 域（`{data_dir}/skills/amazon`）。全库近百个技能里有 apple / gaming /
creative 这些跟亚马逊运营无关的，全挂上去只会让 agent 每轮在一堆噪音里挑，匹配质量
只会更差。其余技能照旧在 Skill 中心「工具」页手动运行。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from app.core.skill_paths import SKILLS_ROOT

logger = logging.getLogger("ivyea.services.agent_skills")

#: 挂给 agent 的分类。定于 2026-08-17，理由见模块头「边界」。
EXPOSED_DOMAINS = ("amazon",)


def _agent_settings_file() -> Path:
    """agent 的 settings.json —— 和它自己一样认 IVYEA_HOME。"""
    home = os.environ.get("IVYEA_HOME") or str(Path.home() / ".ivyea")
    return Path(home) / "settings.json"


def exposed_roots() -> list[Path]:
    return [Path(SKILLS_ROOT) / d for d in EXPOSED_DOMAINS]


def register_roots() -> dict[str, Any]:
    """把技能库根目录写进 agent 的 settings.json。幂等。

    **合并而不是覆盖** `skill_roots`：用户可能自己挂了别的库，我们只负责自己这几条。
    """
    wanted = [str(p) for p in exposed_roots() if p.is_dir()]
    path = _agent_settings_file()
    try:
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(settings, dict):
            settings = {}
    except Exception:
        logger.warning("agent settings.json 读不出来，按空配置处理", exc_info=True)
        settings = {}

    current = settings.get("skill_roots") or []
    if isinstance(current, str):
        current = [current]
    current = [str(x) for x in current if str(x).strip()]

    # 自己这几条之外的保持原样；我们负责的那几个域，只保证"在里面"。
    ours = {str(Path(SKILLS_ROOT) / d) for d in EXPOSED_DOMAINS}
    merged = [x for x in current if x not in ours] + wanted

    if merged == current:
        return {"changed": False, "roots": merged}
    settings["skill_roots"] = merged
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("写 agent settings.json 失败：%s", exc, exc_info=True)
        return {"changed": False, "roots": current, "error": str(exc)}
    logger.info("已把技能库挂给 IvyeaAgent：%s", ", ".join(wanted) or "(无)")
    return {"changed": True, "roots": merged}


def register_quietly() -> None:
    try:
        register_roots()
    except Exception:
        logger.warning("挂载技能库失败（旁路，已忽略）", exc_info=True)


def skill_dir(name: str) -> str:
    """一个 Skill 中心技能在磁盘上的绝对路径。

    执行技能时把它告诉模型，说明书里的 `scripts/` `references/` 才拿得到 ——
    以前这里只能反过来叮嘱它"不要去文件系统里找"。
    """
    try:
        d = (Path(SKILLS_ROOT) / name).resolve()
        root = Path(SKILLS_ROOT).resolve()
        if d.is_dir() and (d == root or root in d.parents):
            return str(d)
    except Exception:
        logger.debug("skill_dir 解析失败（旁路）", exc_info=True)
    return ""


def status() -> dict[str, Any]:
    """挂了哪些目录、agent 那边实际认出多少个技能 —— 给 Skill 中心显示。"""
    roots = exposed_roots()
    registered: list[str] = []
    try:
        settings = json.loads(_agent_settings_file().read_text(encoding="utf-8"))
        registered = [str(x) for x in (settings.get("skill_roots") or [])]
    except Exception:
        logger.debug("读 agent settings.json 失败（旁路）", exc_info=True)

    # 数一下这些目录里到底有多少个技能。**按 agent 的判据数**（有 SKILL.md 就算），
    # 别自己另算一套，否则界面显示的数和 agent 真认的数会对不上。
    count = 0
    for r in roots:
        if r.is_dir():
            count += sum(1 for p in r.rglob("SKILL.md")
                         if not any(seg.startswith(".") for seg in p.relative_to(r).parts))
    return {
        "domains": list(EXPOSED_DOMAINS),
        "roots": [str(r) for r in roots],
        "registered": all(str(r) in registered for r in roots if r.is_dir()),
        "count": count,
    }
