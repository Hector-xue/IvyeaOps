"""评测执行器：加载案例、跑被测系统、评分、出报告。

案例是**数据文件**（``server/evals/cases/<suite>/*.json``）而不是代码里的常量：
攒案例这件事应该谁都能做 —— 运营从真实工单里脱敏出一个来，往目录里丢一个 json
就行，不必懂 Python。
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from app.evals.rubric import PASS_THRESHOLD, score_case

logger = logging.getLogger("ivyea.evals.runner")


def cases_root() -> Path:
    from app.core.config import settings
    return Path(settings.root_dir) / "server" / "evals" / "cases"


def load_cases(suite: str, *, root: Optional[Path] = None, limit: int = 0) -> list[dict]:
    """加载一个 suite 的案例。文件坏了要**指名道姓地报**出来，
    而不是静默跳过 —— 少跑了几个案例却显示"全过"，比直接失败更糟。"""
    folder = (root or cases_root()) / suite
    if not folder.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(folder.glob("*.json")):
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"案例文件坏了：{path.name}（{exc}）") from exc
        case.setdefault("id", path.stem)
        missing = [k for k in ("prompt", "expected_points") if k not in case]
        if missing:
            raise ValueError(f"案例 {path.name} 缺字段：{', '.join(missing)}")
        # **要点为空要当场报错**，不能当成"没有要求所以都算过"。空要点会让判官
        # 在没有评判标准的情况下打分，结果必然虚高 —— 一个半成品案例悄悄计入
        # 通过率，比直接失败糟得多。make_case 生成的骨架正是这个状态。
        if not case["expected_points"]:
            raise ValueError(
                f"案例 {path.name} 的 expected_points 是空的 —— 还没人工标注合格要点。"
                f"填完再跑；要点要写「必须命中什么」，不要写「应该讲得好」。")
        out.append(case)

    # **id 撞了要当场报错。** 报告是按 id 列行的，两个案例同名就意味着看报告的人
    # 分不清哪一行对应哪个案例 —— 而评测报告的全部价值就是"哪里退步了"。
    seen: dict = {}
    for case in out:
        cid = case["id"]
        if cid in seen:
            raise ValueError(
                f"案例 id 重复：{cid}（至少出现在两个文件里）。"
                f"id 不写时取文件名；写了就必须唯一。")
        seen[cid] = True
    return out[:limit] if limit else out


def run_suite(
    suite: str,
    system: Callable[[dict], str],
    judge: Optional[Callable[[str], str]] = None,
    *,
    root: Optional[Path] = None,
    limit: int = 0,
    judge_runs: int = 3,
) -> dict:
    """跑一个 suite。

    ``system`` 收案例、返回被测系统的输出；``judge`` 是判官。
    **没有配判官时不假装通过** —— 返回 ``skipped`` 并说明原因，
    免得 CI 上一片绿而实际上什么都没评。
    """
    cases = load_cases(suite, root=root, limit=limit)
    started = datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "suite": suite,
        "started_at": started.isoformat(timespec="seconds"),
        "total": len(cases),
        "threshold": PASS_THRESHOLD,
        "results": [],
    }
    if not cases:
        report["status"] = "empty"
        report["detail"] = f"{suite} 下没有案例"
        return report
    if judge is None:
        report["status"] = "skipped"
        report["detail"] = "没有配置判官模型（判官必须与被测模型不同），本轮未评分"
        return report

    for case in cases:
        try:
            output = system(case)
        except Exception as exc:  # noqa: BLE001 — 一个案例炸了不该带走整轮
            logger.exception("案例执行失败 %s", case.get("id"))
            report["results"].append({"case_id": case.get("id"), "error": str(exc),
                                      "average": 0.0, "passed": False})
            continue
        report["results"].append(score_case(case, output, judge, runs=judge_runs).to_dict())

    scored = [r for r in report["results"] if "error" not in r]
    report["average"] = round(statistics.mean([r["average"] for r in scored]), 2) if scored else 0.0
    report["passed"] = sum(1 for r in report["results"] if r.get("passed"))
    report["hallucinations"] = sum(1 for r in report["results"] if r.get("fatal_hits"))
    # 发版门槛：均分达标**且**零幻觉。两个条件缺一不可 —— 幻觉是一票否决。
    report["status"] = ("pass" if report["average"] >= PASS_THRESHOLD
                        and report["hallucinations"] == 0 else "fail")
    return report


def render(report: dict) -> str:
    """给终端看的一页纸。"""
    lines = [f"== 评测 {report['suite']} =="]
    status = report.get("status")
    if status in ("empty", "skipped"):
        return "\n".join(lines + [f"  {status}: {report.get('detail', '')}"])
    lines.append(f"  案例 {report['total']} · 达标 {report['passed']} · "
                 f"均分 {report['average']}（门槛 {report['threshold']}）")
    if report["hallucinations"]:
        lines.append(f"  ⚠ 幻觉 {report['hallucinations']} 例 —— 一票否决，不允许发版")
    for r in report["results"]:
        mark = "✓" if r.get("passed") else "✗"
        extra = " [幻觉]" if r.get("fatal_hits") else ""
        lines.append(f"  {mark} {r['case_id']}  {r.get('average', 0)}{extra}")
    lines.append(f"  结论：{status}")
    return "\n".join(lines)
