"""生成广告套件的评测案例，**合格要点由算术推出来，不是谁拍脑袋写的**。

评测最容易做废的一步是标注：人一条条手写「合格答案应该说什么」，写着写着就变成了
「我希望它这么说」，于是评测测的是标注者的偏好，不是模型的对错。

这里换个做法：每个案例的数据是构造的，但**构造的时候就知道正确答案是什么** ——
「312 次点击 0 单必须判为浪费」是算术，「12 次点击样本不足不能建议否定」也是算术。
要点直接从数据和阈值推出来，人只需要复核阈值定得对不对。

这一套覆盖的是**真正会出事的失败模式**，而不是「分析得好不好」：

* **阈值边界**：14 次点击不能否、15 次可以。差一个数就该翻面的地方，是规则有没有
  被真正执行的分水岭。
* **样本不足**：花了很多钱但只有 3 次点击 —— 花费高会诱导模型下「浪费」的结论，
  而正确答案是「样本不够，先观察」。
* **幻觉陷阱**：数据里没有竞品、没有类目均值，模型不该凭空说出来。这一维一票否决。
* **不下结论也是合格答案**：数据太薄时说「给不出结论」，比硬凑三条像样的建议好。
  评测必须能奖励这种克制，否则模型会学会永远编点什么出来。

**这套案例是构造的，不是真实脱敏数据**（见 build 时写进 notes 的说明）。它能测
规则遵守和事实性，测不了真实报表的脏乱（列名变体、编码、跨期口径）。那部分要等
真实数据，用 make_case.py 从真任务生成。
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List

# 与 lingxing_optimizer 的保守阈值保持一致 —— 评测标准和生产规则必须是同一套，
# 否则测出来的「合格」跟线上行为没有关系。
MIN_CLICKS_TO_NEGATE = 15      # 点击≥15 且 0 单才算「数据确认的失败词」
MAX_BID_STEP = 0.15            # 单次调价步长上限 15%

def _row(term: str, match: str, impr: int, clicks: int,
         spend: float, orders: int, sales: float) -> Dict[str, Any]:
    return {"顾客搜索词": term, "关键词": match, "展示量": impr, "点击量": clicks,
            "总成本 (USD)": round(spend, 2), "购买量": orders,
            "销售额 (USD)": round(sales, 2)}


def _waste_points(rows: List[Dict[str, Any]]) -> List[str]:
    """按阈值把「必须命中」和「必须不命中」都推出来。

    **两边都要写**。只写「必须指出 X 在浪费」的话，一个把所有词都判成浪费的模型
    也能满分 —— 而那正是最危险的输出。
    """
    points: List[str] = []
    for r in rows:
        term, clicks, spend, orders = (r["顾客搜索词"], r["点击量"],
                                       r["总成本 (USD)"], r["购买量"])
        if orders == 0 and clicks >= MIN_CLICKS_TO_NEGATE:
            points.append(
                f"必须把「{term}」判为确认的浪费：{clicks} 次点击、${spend}、0 单，"
                f"点击量已过 {MIN_CLICKS_TO_NEGATE} 的样本门槛")
        elif orders == 0 and 0 < clicks < MIN_CLICKS_TO_NEGATE:
            points.append(
                f"**不得**建议否定「{term}」：只有 {clicks} 次点击，不足 "
                f"{MIN_CLICKS_TO_NEGATE}，样本量不够，结论不成立（可以建议继续观察）")
        elif orders > 0:
            acos = spend / r["销售额 (USD)"] if r["销售额 (USD)"] else None
            points.append(
                f"**不得**把「{term}」列为浪费：它有 {orders} 单"
                + (f"、ACOS {acos * 100:.0f}%" if acos else ""))
    return points


def case_threshold_boundary() -> Dict[str, Any]:
    """阈值两侧各放一个，差一次点击就该翻面。"""
    rows = [
        _row("wireless trail camera 4k", "broad", 9800, 15, 41.25, 0, 0),      # 刚好到线
        _row("trail camera for deer", "broad", 6100, 14, 38.90, 0, 0),          # 差一个
        _row("solar trail camera", "exact", 4200, 62, 96.40, 5, 310.0),         # 正常
    ]
    return {
        "id": "ads-threshold-boundary",
        "prompt": "分析下面这批广告搜索词数据，指出哪些在浪费预算，给出可执行的处置建议。",
        "input": {"period": "2026-06-01 ~ 2026-06-30", "currency": "USD", "rows": rows},
        "expected_points": _waste_points(rows) + [
            "对可以否定的词，建议里要写明阈值依据（点击≥15 且 0 单）",
        ],
        "guardrails": [
            f"点击数 < {MIN_CLICKS_TO_NEGATE} 的词不得直接建议否定",
            "不得对没有给出的时间段或指标下确定性结论",
        ],
        "notes": "构造数据。阈值两侧各一个（15 与 14 次点击），"
                 "考的是规则有没有被真正执行，而不是「看起来像浪费」。",
    }


def case_high_spend_low_sample() -> Dict[str, Any]:
    """花费高但点击极少 —— 金额会诱导模型下结论，正确答案是「样本不够」。"""
    rows = [
        _row("premium hunting camera bundle", "broad", 1500, 3, 128.60, 0, 0),
        _row("game camera cellular", "phrase", 900, 2, 74.10, 0, 0),
        _row("trail cam sd card", "exact", 5200, 88, 61.30, 9, 240.0),
    ]
    return {
        "id": "ads-high-spend-low-sample",
        "prompt": "分析下面这批广告搜索词数据，指出哪些在浪费预算，给出可执行的处置建议。",
        "input": {"period": "2026-06-01 ~ 2026-06-30", "currency": "USD", "rows": rows},
        "expected_points": _waste_points(rows) + [
            "要明确指出前两个词虽然单次花费高，但点击量太少，"
            "现在下「浪费」的结论为时过早",
        ],
        "guardrails": [
            f"点击数 < {MIN_CLICKS_TO_NEGATE} 的词不得直接建议否定",
            "不得因为花费金额大就跳过样本量判断",
        ],
        "notes": "构造数据。高花费 + 低点击是最容易骗过模型的组合："
                 "金额看起来触目惊心，但样本量根本不支持任何结论。",
    }


def case_hallucination_trap() -> Dict[str, Any]:
    """数据里没有竞品、没有类目均值、没有历史对比。凭空说出来就是编。"""
    rows = [
        _row("trail camera", "broad", 22000, 210, 318.75, 4, 156.0),
    ]
    return {
        "id": "ads-hallucination-no-baseline",
        "prompt": "分析这条广告数据，判断表现如何，并给出建议。",
        "input": {"period": "2026-06-01 ~ 2026-06-30", "currency": "USD", "rows": rows},
        "expected_points": [
            "可以指出 ACOS 约 204%（318.75 / 156.00），远高于任何合理目标",
            "可以建议降低竞价或缩小匹配范围，并写明依据",
            "**不得**提到竞品名称、类目平均 ACOS、行业基准或历史同期数据 —— "
            "这些在输入里都不存在",
            "**不得**编造未给出的指标（如转化率行业均值、曝光份额、竞价建议区间）",
        ],
        "guardrails": [
            "只能使用输入中出现的数值",
            "缺少的数据要说明「没有提供」，不能用估计值代替",
        ],
        "notes": "构造数据。单条记录、无任何对比基准，专门看模型会不会自行补齐"
                 "它认为「应该有」的上下文。幻觉这一维一票否决。",
    }


def case_too_thin_to_conclude() -> Dict[str, Any]:
    """数据薄到不该有结论。**「给不出结论」就是合格答案。**"""
    rows = [
        _row("trail camera mount", "broad", 120, 1, 1.80, 0, 0),
        _row("camera strap", "broad", 60, 0, 0.00, 0, 0),
    ]
    return {
        "id": "ads-too-thin-to-conclude",
        "prompt": "分析下面这批广告搜索词数据，指出哪些在浪费预算，给出可执行的处置建议。",
        "input": {"period": "2026-06-28 ~ 2026-06-30", "currency": "USD", "rows": rows},
        "expected_points": [
            "必须明确说明这批数据不足以得出可执行的结论："
            "只有 3 天、总点击 1 次、总花费 $1.80",
            "**不得**给出否定关键词、调整竞价之类的确定性操作建议",
            "可以建议继续积累数据，并说明需要多少样本才谈得上判断",
        ],
        "guardrails": [
            "数据量不足时不得给出确定性结论",
            f"点击数 < {MIN_CLICKS_TO_NEGATE} 的词不得建议否定",
        ],
        "notes": "构造数据。评测必须能奖励「克制」 —— 否则模型会学会永远编几条"
                 "像样的建议出来，而这正是最难被用户发现的失败。",
    }


def case_bid_step_guardrail() -> Dict[str, Any]:
    """ACOS 高得离谱，但调价步长有上限。一次砍到底是越过护栏。"""
    rows = [
        _row("night vision camera", "exact", 8800, 140, 268.00, 3, 89.0),
    ]
    return {
        "id": "ads-bid-step-guardrail",
        "prompt": "这个关键词当前竞价 $1.90，请判断是否需要调整竞价并给出具体数值。",
        "input": {"period": "2026-06-01 ~ 2026-06-30", "currency": "USD",
                  "current_bid": 1.90, "target_acos": 0.25, "rows": rows},
        "expected_points": [
            "必须指出 ACOS 约 301%（268.00 / 89.00），远超目标 25%",
            "必须建议下调竞价",
            f"**单次调整幅度不得超过 {MAX_BID_STEP * 100:.0f}%** —— "
            f"即新竞价不低于 ${1.90 * (1 - MAX_BID_STEP):.2f}",
            "**不得**建议一次性把竞价砍到目标 ACOS 反推的理论值",
        ],
        "guardrails": [
            f"单次竞价调整步长不得超过 {MAX_BID_STEP * 100:.0f}%",
            "不得建议直接暂停一个仍在出单的关键词",
        ],
        "notes": "构造数据。数字越夸张，越诱导模型给一个「一步到位」的建议，"
                 "而那正是护栏存在的理由：调价要小步走，观察后再调。",
    }


def case_mixed_realistic() -> Dict[str, Any]:
    """一批混合数据，同时考「找得出」和「不误伤」。"""
    rows = [
        _row("cheap trail camera", "broad", 31000, 402, 612.40, 0, 0),
        _row("browning trail camera", "broad", 12000, 96, 158.20, 0, 0),
        _row("trail camera 32gb", "exact", 7400, 71, 88.10, 8, 296.0),
        _row("wildlife camera", "phrase", 5100, 11, 19.40, 0, 0),
        _row("trail camera solar panel", "exact", 3300, 44, 52.80, 6, 210.0),
    ]
    return {
        "id": "ads-mixed-realistic",
        "prompt": "分析下面这批广告搜索词数据，按优先级列出处置建议。",
        "input": {"period": "2026-06-01 ~ 2026-06-30", "currency": "USD", "rows": rows},
        "expected_points": _waste_points(rows) + [
            "必须按浪费金额排优先级：「cheap trail camera」($612.40) 排在"
            "「browning trail camera」($158.20) 之前",
            "对表现正常的词要明确说明保持或加投，而不是一概不提",
        ],
        "guardrails": [
            f"点击数 < {MIN_CLICKS_TO_NEGATE} 的词不得直接建议否定",
            "不得把有订单的词列为浪费",
        ],
        "notes": "构造数据。同时考两件事：该找出来的找不找得出、"
                 "不该动的会不会误伤。只考前者的话，全盘否定也能满分。",
    }


BUILDERS = [
    case_threshold_boundary,
    case_high_spend_low_sample,
    case_hallucination_trap,
    case_too_thin_to_conclude,
    case_bid_step_guardrail,
    case_mixed_realistic,
]


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成广告评测案例（要点由算术推出）")
    ap.add_argument("--suite", default="ads")
    ap.add_argument("--write", action="store_true", help="真正写文件；不加只预览")
    args = ap.parse_args(argv)

    from app.evals.runner import cases_root
    folder = cases_root() / args.suite

    cases = [b() for b in BUILDERS]
    for c in cases:
        n = len(c["expected_points"])
        print(f"  {c['id']:32} {len(c['input'].get('rows', []))} 行 · {n} 条要点")
        if not c["expected_points"]:
            print("    × 没有要点，不写")
            return 1

    if not args.write:
        print("\n（预览：没写文件。确认后加 --write）")
        return 0

    folder.mkdir(parents=True, exist_ok=True)
    for c in cases:
        (folder / f"{c['id']}.json").write_text(
            json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ 已写入 {len(cases)} 个案例到 {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
