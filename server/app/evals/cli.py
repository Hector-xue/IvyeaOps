"""一条命令跑评测。

    python -m app.evals.cli --suite ads
    python -m app.evals.cli --suite ads --system assistant --judge ivyea-agent

`run_suite` 早就写好了，但此前**没有任何东西调用它** —— 施工图里"一条命令出报告"
这条验收其实一直没达成。这个文件补上那条命令。

判官必须与被测不同
------------------
同一个模型给自己打分会系统性偏高：它认可的正是它自己会写出来的那种东西。所以
``--system`` 与 ``--judge`` 默认取两个不同的 provider，**相同时直接拒绝运行** ——
一份自己给自己打的高分报告，比没有报告更有害，因为它会让人以为质量被守住了。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Dict, List, Optional

from app.evals.runner import render, run_suite


def _prompt_for(case: Dict[str, Any]) -> str:
    """把案例拼成给被测系统的输入。**不塞入合格要点** —— 那是答案。"""
    payload = json.dumps(case.get("input") or {}, ensure_ascii=False, indent=2)
    parts = [str(case.get("prompt") or ""), "", "## 数据", payload]
    guards = case.get("guardrails") or []
    if guards:
        # 护栏要给：它是产品在生产里也会给的约束，评测要测的是"给了约束还遵不遵守"，
        # 而不是"能不能猜到有这些约束"。
        parts += ["", "## 必须遵守的约束", *[f"- {g}" for g in guards]]
    return "\n".join(parts)


def _runner(provider: str):
    from app.services.ai_synthesis_service import generate_text_provider

    def run(case_or_prompt) -> str:
        text = (_prompt_for(case_or_prompt) if isinstance(case_or_prompt, dict)
                else str(case_or_prompt))
        return asyncio.run(generate_text_provider(provider, text))
    return run


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="跑一个评测套件")
    ap.add_argument("--suite", default="ads")
    ap.add_argument("--system", default="assistant", help="被测 provider")
    ap.add_argument("--judge", default="deepseek", help="判官 provider（必须与被测不同）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--judge-runs", type=int, default=3, help="每例判几次取中位数")
    ap.add_argument("--json", action="store_true", help="输出原始 json")
    args = ap.parse_args(argv)

    if args.system.strip().lower() == args.judge.strip().lower():
        print(f"× 被测和判官都是 {args.system} —— 同一个模型给自己打分会系统性偏高，"
              f"拒绝运行。\n  一份自己给自己打的高分报告比没有报告更有害。")
        return 2

    report = run_suite(args.suite, _runner(args.system), _runner(args.judge),
                       limit=args.limit, judge_runs=args.judge_runs)
    report["system_provider"] = args.system
    report["judge_provider"] = args.judge

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
        print(f"\n被测：{args.system}    判官：{args.judge}")

    status = report.get("status")
    if status in ("skipped", "empty"):
        print(f"\n（{report.get('detail')}）")
        return 0
    # 有一个案例没过就非零退出，这样它能直接进 CI。
    failed = [r for r in report.get("results", []) if not r.get("passed")]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
