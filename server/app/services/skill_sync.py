"""把 Skill 中心的技能注册进 IvyeaAgent 的技能库。

── 为什么需要这层 ──────────────────────────────────────────────────────────
这台机器上长期有**两套技能库**，格式不是一套：

  Skill 中心（{data_dir}/skills）  SKILL.md + YAML frontmatter，Hermes 时代的产物
  IvyeaAgent（~/.ivyea/skills）    SKILL.md + 旁边一个 skill.json（id/triggers/tools）

agent 加载用户技能的方式是扫 `~/.ivyea/skills/**/skill.json`（见 ivyea_agent/
skills.py 的 `_iter_user`），按 id 索引。所以 Skill 中心里的技能 agent 从来查不到，
任务台的自动匹配也永远匹配不到它们 —— 以前只能把 SKILL.md 正文整篇塞进 system
上下文，还得叮嘱模型"不要去文件系统里找这个 skill 的目录"。

那句叮嘱是有代价的：这些技能**带附属文件**（脚本、参考文档、模板），正文里明确写着
"运行 scripts/xxx.py"「参见 references/xxx.md」。只注入正文等于把说明书给了、材料
扣下了，还不许它去找。

这个模块把技能连同附属文件一起装进 agent 的库，并生成 skill.json。

── 边界 ────────────────────────────────────────────────────────────────────
1. **只同步 amazon 域**。全库近百个技能里有 apple / gaming / creative 这些跟亚马逊
   运营八竿子打不着的，全推过去只会让 agent 每轮在近百个候选里挑，反而挑不准。
2. **只读**。同步过去的是材料，不是执行许可 —— 技能里的 .py/.sh 是给模型读的参考，
   执行策略仍由调用方（plan_mode）决定，这里不改任何权限。
3. **只删自己写的**。每个同步出去的目录里放一个 marker，清理时只认 marker。
   手工放进 agent 库的技能（比如 amazon/listing_image）一根毫毛都不能碰。
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from app.core.skill_paths import SKILLS_ROOT
from app.services import skill_repo

logger = logging.getLogger("ivyea.services.skill_sync")

#: agent 的用户技能库。和 ivyea_agent.config.IVYEA_DIR/"skills" 一致。
AGENT_SKILLS_ROOT = Path.home() / ".ivyea" / "skills"

#: 同步产物的标记文件。清理时只删带这个标记的目录 —— 手工技能不能误伤。
MARKER = ".synced-from-hub"

#: 只同步这些顶层分类。定于 2026-08-17，理由见模块头「边界」第 1 条。
SYNCED_DOMAINS = ("amazon",)

#: agent 自带技能的 id 前缀空间。撞上就要让路：用户在 Skill 中心随手建一个同名技能
#: 就把内置技能顶掉，是很难查的故障（`list_skills` 里 user 覆盖 builtin）。
_BUILTIN_IDS_CACHE: set[str] | None = None

#: 中文查询的救命稻草。
#:
#: agent 的 `_terms()` 是 `[\w一-鿿+.-]+` —— 它把一整句中文当作**一个**词，
#: 不做分词。所以"帮我分析一下搜索词报表"只会整串去比对，命中率约等于零；真正让中文
#: 匹配上的是 triggers 里那些**短词**（`tl in query` 命中就 +3 分）。
#: 内置技能就是这么写的（"预算""出价""放量"）。这里按同样的路子给同步过去的技能配。
_ZH_LEXICON = (
    "广告", "搜索词", "否词", "关键词", "竞品", "报表", "报告", "预算", "出价", "竞价",
    "转化", "点击", "曝光", "类目", "listing", "主图", "评论", "选品", "市场", "调研",
    "审计", "诊断", "优化", "文案", "标题", "五点", "asin", "店铺", "投放", "冷启动",
)


def _builtin_ids() -> set[str]:
    """agent 内置技能的 id 集合。读不到就当空 —— 宁可不防撞，也不能让同步整个挂掉。"""
    global _BUILTIN_IDS_CACHE
    if _BUILTIN_IDS_CACHE is not None:
        return _BUILTIN_IDS_CACHE
    ids: set[str] = set()
    try:
        from ivyea_agent import skills as agent_skills  # type: ignore
        ids = {sk.id for sk in agent_skills.list_skills(include_user=False)}
    except Exception:
        logger.debug("读不到 agent 内置技能 id（旁路，按空集处理）", exc_info=True)
    _BUILTIN_IDS_CACHE = ids
    return ids


def _slug(name: str) -> str:
    """`amazon/zach-search-term-report-analyzer` → `zach_search_term_report_analyzer`。"""
    tail = name.split("/")[-1]
    return re.sub(r"[^a-z0-9]+", "_", tail.lower()).strip("_") or "skill"


def _skill_id(name: str, domain: str) -> str:
    base = f"{domain}.{_slug(name)}"
    if base in _builtin_ids():
        # 内置技能优先。同名的话给同步产物加后缀，绝不顶替 —— 顶替是静默的，
        # 表现为"内置技能突然换了套说法"，没人查得到。
        return base + "_hub"
    return base


def _triggers(name: str, fm: dict[str, Any], description: str, description_zh: str) -> list[str]:
    """作者声明的优先；没有就从标签、名字和中文描述里凑。

    作者可以在 frontmatter 里直接写 `triggers: [搜索词, 否词]` 来接管这件事 ——
    自动凑出来的永远不如作者自己知道用户会怎么问。
    """
    out: list[str] = []

    def add(v: Any) -> None:
        if isinstance(v, str) and v.strip() and v.strip() not in out:
            out.append(v.strip())

    declared = fm.get("triggers")
    if isinstance(declared, list):
        for t in declared:
            add(t)
    meta = fm.get("metadata")
    tags = ((meta or {}).get("hermes") or {}).get("tags") if isinstance(meta, dict) else None
    if isinstance(tags, list):
        for t in tags:
            add(t)
    for token in _slug(name).split("_"):
        if len(token) > 2:
            add(token)
    haystack = f"{name} {description} {description_zh}".lower()
    for word in _ZH_LEXICON:
        if word in haystack:
            add(word)
    return out[:24]


def _body_with_path(body: str, dest: Path, has_assets: bool) -> str:
    """在正文最前面写清楚这个技能的目录在哪。

    必须放**最前面**：自动匹配走 `context_for_query`，它把正文截到 700 字，
    放在末尾的说明根本进不了上下文。

    没有附属文件的技能不加这段 —— 平白多一句"去这个目录找材料"只会诱导它白跑一趟。
    """
    if not has_assets:
        return body.strip()
    head = (
        f"> **本技能的文件目录**：`{dest}`\n"
        f"> 正文里出现的 `scripts/`、`references/`、`assets/` 等相对路径都在这个目录下，\n"
        f"> 需要时直接按绝对路径读取即可（本目录只读，不要往里写东西）。\n"
    )
    return head + "\n" + body.strip()


def _manifest(detail: skill_repo.SkillDetail, skill_id: str, domain: str) -> dict[str, Any]:
    fm = detail.frontmatter or {}
    desc_zh = (detail.description_zh or "").strip()
    desc_en = (detail.description or "").strip()
    return {
        "id": skill_id,
        # 中文描述排前面：这台机器上的人用中文提问，描述会进匹配的 haystack。
        "title": str(fm.get("title") or detail.name.split("/")[-1]),
        "domain": domain,
        "version": str(fm.get("version") or ""),
        "description": desc_zh or desc_en,
        "triggers": _triggers(detail.name, fm, desc_en, desc_zh),
        "knowledge_ids": [],
        "tools": [],
        "source": "skill-hub",
        "source_name": detail.name,
    }


def _is_synced_dir(path: Path) -> bool:
    return (path / MARKER).is_file()


def _clear_stale(keep: set[Path]) -> int:
    """删掉本次没写的历史同步产物（技能被删/改名/移出 amazon 域）。

    **只删带 marker 的目录。** 手工放进 agent 库的技能不在此列。
    """
    removed = 0
    if not AGENT_SKILLS_ROOT.exists():
        return 0
    # **先收集再删。** rglob 是惰性的：边遍历边 rmtree，遍历器下一步走进刚被删掉的
    # 目录就是 FileNotFoundError —— 删两个以上的技能必炸。
    for marker in list(AGENT_SKILLS_ROOT.rglob(MARKER)):
        d = marker.parent
        if d in keep:
            continue
        try:
            shutil.rmtree(d)
            removed += 1
        except Exception:
            logger.warning("清理同步产物失败：%s", d, exc_info=True)
    return removed


def sync_to_agent() -> dict[str, Any]:
    """把 SYNCED_DOMAINS 里的技能同步进 agent 技能库。幂等，可反复调用。

    返回 {"synced": n, "removed": n, "skills": [...], "errors": [...]}。
    **任何一个技能出错都不该拖垮其余的** —— 记进 errors 继续走。
    """
    synced: list[dict[str, str]] = []
    errors: list[str] = []
    keep: set[Path] = set()

    for domain in SYNCED_DOMAINS:
        src_root = Path(SKILLS_ROOT) / domain
        if not src_root.is_dir():
            continue
        for skill_md in sorted(src_root.rglob("SKILL.md")):
            src = skill_md.parent
            rel = skill_md.parent.relative_to(Path(SKILLS_ROOT)).as_posix()
            if any(part.startswith(".") for part in Path(rel).parts):
                continue                                  # .archive 之类的不同步
            try:
                detail = skill_repo.get_skill(rel)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{rel}: 读取失败 {exc}")
                continue

            skill_id = _skill_id(rel, domain)
            dest = AGENT_SKILLS_ROOT / domain / _slug(rel)
            if dest in keep:
                # 两个不同目录下的同名技能（a/x/SKILL.md 和 a/y/x/SKILL.md）会算出
                # 同一个落点和同一个 id，后一个把前一个盖掉且毫无声响。宁可少同步
                # 一个并说清楚。
                errors.append(f"{rel}: 与已同步的技能重名（id={skill_id}），跳过；请改名后重试")
                continue
            try:
                if dest.exists() and not _is_synced_dir(dest):
                    # 这个位置上有手工技能。**绝不覆盖**，换个不撞的目录名。
                    dest = dest.with_name(dest.name + "_hub")
                    if dest.exists() and not _is_synced_dir(dest):
                        errors.append(f"{rel}: 目标目录被占用且不是同步产物，跳过")
                        continue
                if dest.exists():
                    shutil.rmtree(dest)
                assets = [p for p in src.rglob("*") if p.is_file() and p.name != "SKILL.md"]
                shutil.copytree(src, dest, ignore=shutil.ignore_patterns(
                    "SKILL.md", "__pycache__", "*.pyc", ".git"))
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "SKILL.md").write_text(
                    _body_with_path(detail.content_body, dest, bool(assets)) + "\n",
                    encoding="utf-8")
                (dest / "skill.json").write_text(
                    json.dumps(_manifest(detail, skill_id, domain), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
                (dest / MARKER).write_text(rel + "\n", encoding="utf-8")
                keep.add(dest)
                synced.append({"name": rel, "id": skill_id, "path": str(dest)})
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{rel}: 同步失败 {exc}")
                logger.warning("同步技能失败：%s", rel, exc_info=True)

    removed = _clear_stale(keep)
    if errors:
        logger.warning("技能同步完成但有 %d 个失败：%s", len(errors), "; ".join(errors[:3]))
    logger.info("技能同步：写入 %d 个，清理 %d 个", len(synced), removed)
    return {"synced": len(synced), "removed": removed, "skills": synced, "errors": errors}


def sync_quietly() -> None:
    """给写操作后台调用的版本 —— 同步失败绝不能让保存技能这件事失败。"""
    try:
        sync_to_agent()
    except Exception:
        logger.warning("技能同步失败（旁路，已忽略）", exc_info=True)


def synced_dir(name: str) -> str:
    """这个 Skill 中心技能有没有被同步进 agent 库？有就返回它在那边的绝对路径。

    执行时靠它决定说辞：目录真的在，就告诉模型去哪儿读附属文件；不在，才说
    "别去找"。**必须按 marker 里记的原名核对** —— 光看路径存不存在，会把另一个
    同名技能的目录认成自己的。
    """
    if not AGENT_SKILLS_ROOT.exists():
        return ""
    for marker in AGENT_SKILLS_ROOT.rglob(MARKER):
        try:
            if marker.read_text(encoding="utf-8").strip() == name:
                return str(marker.parent)
        except Exception:
            continue
    return ""


def status() -> dict[str, Any]:
    """当前有多少技能同步在 agent 库里 —— 给 Skill 中心显示。"""
    rows: list[dict[str, str]] = []
    if AGENT_SKILLS_ROOT.exists():
        for marker in sorted(AGENT_SKILLS_ROOT.rglob(MARKER)):
            d = marker.parent
            try:
                manifest = json.loads((d / "skill.json").read_text(encoding="utf-8"))
            except Exception:
                continue
            rows.append({"id": manifest.get("id", ""),
                         "name": manifest.get("source_name", ""),
                         "path": str(d)})
    return {"domains": list(SYNCED_DOMAINS), "count": len(rows), "skills": rows}
