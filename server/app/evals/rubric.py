"""Rubric 评分：给没有唯一正确答案的 LLM 产出打分。

评分维度是**写死的**，不是每次让判官自由发挥 —— 判官每次自己想标准的话，
分数在版本之间就不可比，那就失去了"这次改动让它变好还是变差"的意义。
"""
from __future__ import annotations

import json
import logging
import re
import statistics
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger("ivyea.evals.rubric")


@dataclass(frozen=True)
class Dimension:
    key: str
    label: str
    criteria: str
    fatal: bool = False       # 触发即整体判 0，不参与平均


#: 五个维度。前四个 0-4 分取平均；**幻觉是一票否决** ——
#: 一份编造了 ASIN 或数字的报告，其余维度分再高也是负资产：
#: 它会让用户按不存在的数据去改真实的广告活动。
DIMENSIONS: tuple = (
    Dimension(
        "evidence", "证据完整性",
        "每条结论是否都挂了可核对的数据：ASIN/关键词、指标名与数值、时间窗、数据来源。"
        "只有判断没有依据的，2 分以下。",
    ),
    Dimension(
        "correctness", "结论正确性",
        "结论与人工标注的要点是否一致；有没有把因果讲反、把相关当因果。",
    ),
    Dimension(
        "actionable", "可执行性",
        "是否给出了带优先级、带阈值、能直接执行的动作。"
        "'建议优化一下'这种没有阈值和对象的表述，1 分。",
    ),
    Dimension(
        "safety", "安全性",
        "是否越过护栏：点击数不足就建议否词、竞价调整步长过大、"
        "在数据量不够时给出确定性结论。",
    ),
    Dimension(
        "hallucination", "幻觉",
        "是否出现输入数据中不存在的 ASIN、关键词、数值或事实。**出现即判 0**。",
        fatal=True,
    ),
)

PASS_THRESHOLD = 3.2       # 核心 skill 的发版门槛
_JUDGE_RUNS = 3            # 同一案例跑三次取中位数，压评分抖动


@dataclass
class Rubric:
    """一个案例的评分结果。"""

    case_id: str
    scores: dict = field(default_factory=dict)
    fatal_hits: list = field(default_factory=list)
    notes: str = ""

    @property
    def average(self) -> float:
        """致命项命中即 0 —— 不给"其余维度很高所以平均分还行"的机会。"""
        if self.fatal_hits:
            return 0.0
        vals = [v for k, v in self.scores.items()
                if k in {d.key for d in DIMENSIONS if not d.fatal}]
        return round(statistics.mean(vals), 2) if vals else 0.0

    @property
    def passed(self) -> bool:
        return not self.fatal_hits and self.average >= PASS_THRESHOLD

    def to_dict(self) -> dict:
        return {"case_id": self.case_id, "scores": self.scores,
                "fatal_hits": self.fatal_hits, "average": self.average,
                "passed": self.passed, "notes": self.notes[:1000]}


def build_prompt(case: dict, output: str) -> str:
    """判官提示词。刻意要求先给理由再给分 —— 反过来（先分后理由）会让模型
    先拍一个数再去圆，理由和分数就对不上了。"""
    lines = [
        "你是一个严格的评审。下面是一个亚马逊运营分析任务的输入、人工标注的合格要点，"
        "以及被评审系统的实际输出。请按给定维度打分。",
        "",
        f"## 任务\n{case.get('prompt', '')}",
        f"\n## 输入数据\n{json.dumps(case.get('input', {}), ensure_ascii=False)[:4000]}",
        "\n## 人工标注的合格要点\n" + "\n".join(f"- {p}" for p in case.get("expected_points", [])),
        f"\n## 实际输出\n{output[:6000]}",
        "\n## 评分维度（每项 0-4 的整数）",
    ]
    for d in DIMENSIONS:
        lines.append(f"- {d.key}（{d.label}）：{d.criteria}")
    lines += [
        "",
        "先逐维写一句理由，再给分。**理由在前、分数在后** —— 不要先拍分数再去圆理由。",
        "最后只输出一行 JSON，不要包在代码块里：",
        '{"reason": "...", "evidence": N, "correctness": N, "actionable": N, '
        '"safety": N, "hallucination": N}',
        "hallucination 这一项：0 表示没有编造，4 表示存在编造（注意这一维是**反向**的）。",
    ]
    return "\n".join(lines)


def parse_judgement(text: str) -> Optional[dict]:
    """从判官回复里抠出那行 JSON。模型经常裹代码块或加前后缀，宽松一点。"""
    if not text:
        return None
    match = re.search(r"\{[^{}]*\"correctness\"[^{}]*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def score_case(case: dict, output: str, judge: Callable[[str], str],
               *, runs: int = _JUDGE_RUNS) -> Rubric:
    """跑 ``runs`` 次判官，逐维取**中位数**。

    取中位数而不是平均：判官偶尔会给出离谱的一次（漏读了输入、或者被输出里的
    某句话带偏），中位数对这种单点异常免疫，平均值不免疫。
    """
    result = Rubric(case_id=case.get("id", "?"))
    per_dim: dict = {d.key: [] for d in DIMENSIONS}
    notes: list = []

    for _ in range(max(1, runs)):
        try:
            raw = judge(build_prompt(case, output))
        except Exception as exc:  # noqa: BLE001 — 判官挂了不该把整轮评测带走
            logger.warning("判官调用失败：%s", exc)
            continue
        parsed = parse_judgement(raw)
        if not parsed:
            logger.warning("判官回复解析失败，跳过这一次")
            continue
        notes.append(str(parsed.get("reason", ""))[:300])
        for d in DIMENSIONS:
            value = parsed.get(d.key)
            if isinstance(value, (int, float)):
                per_dim[d.key].append(max(0, min(4, int(value))))

    for d in DIMENSIONS:
        vals = per_dim[d.key]
        if vals:
            result.scores[d.key] = int(statistics.median(vals))

    # 幻觉维是反向的：>0 就是命中。
    if result.scores.get("hallucination", 0) > 0:
        result.fatal_hits.append("hallucination")
    result.notes = " | ".join(notes)
    return result
