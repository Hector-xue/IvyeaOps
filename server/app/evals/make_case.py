"""把一个真实任务变成一个脱敏的评测案例骨架。

评测框架早就写好了，卡住的从来不是框架，是**攒案例太贵**：每个套件要 20 个真实
案例，而每个案例都得从真实报表里手工抠数据、手工脱敏、手工排版。这个工具把前面
三步自动化，只把**该由人做的那一步**留给人。

留给人的那一步是 ``expected_points``
------------------------------------
合格要点必须**人工标注**。绝不能拿系统自己上次的输出去填 —— 那样等于让模型跟
自己的旧答案比，分数再高也只证明它前后一致，不证明它对。这是整套评测唯一不能
自动化的地方，所以这里生成的骨架把它留空，并且 ``load_cases`` 会拒绝加载一个
要点为空的案例（空要点会让判官在没有评判标准的情况下打分，结果必然虚高，
比直接报错更糟）。

脱敏做什么
----------
* **ASIN** 换成稳定的化名。同一个案例里同一个 ASIN 换成同一个化名，
  关联关系才不会被打乱。
* **金额与量级**按同一个系数缩放。分析依赖的是比值（ACOS、转化率、单次点击成本），
  比值不变，而绝对花费不再泄露你的经营规模。
* **搜索词里的品牌**由你指名，工具不猜。竞品品牌本来就出现在任何人的搜索词报表里，
  不算隐私；但**你自己的品牌**出现就等于指名了这家店。

  这里刻意不做"自动识别可疑品牌词"：没有词典的情况下分不出 belro（品牌）和
  backyard（普通词），实测一次会报出十几个 battery / camera / cheap 这样的常见词。
  一个每次都报十几条的警告，只会训练用户条件反射地跳过它 —— 那道防线就等于没有。
  所以改成问你**唯一知道答案的那件事**：你的品牌叫什么。一次输入，精确替换。

发现处理不了的标识就**拒绝写文件**并列出来，不做"尽力而为"的脱敏 —— 案例是要进
git、随开源仓库发出去的，这里宁可挡住也不能漏。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ivyea.evals.make_case")

_ASIN = re.compile(r"\bB0[A-Z0-9]{8}\b", re.IGNORECASE)
# 报表里常见的金额/量级列名（中英两套）。认不出的列一律原样保留 —— 猜错了缩放，
# 会把一份数据悄悄改成另一份，比不缩放危险得多。
_MONEY_HINTS = ("cost", "spend", "sales", "成本", "花费", "销售额", "预算")
_COUNT_HINTS = ("impress", "click", "order", "unit", "展示", "点击", "购买", "订单")
# **CPC 属于这里，不属于金额**。它是派生比值（花费÷点击）：花费和点击同乘一个
# 系数之后 CPC 本来就不变，再乘一次就会让 CPC ≠ 花费÷点击，整份数据自相矛盾 ——
# 模型只要一做交叉校验就会读出"这份报表是假的"，评测也就测不出真东西了。
# 同理任何"单价/每次"的列。
_RATIO_HINTS = ("acos", "roas", "ctr", "cvr", "cpc", "cpa", "单价", "每次", "率", "rate")


def _pseudo_asin(real: str, salt: str) -> str:
    h = hashlib.sha256((salt + real.upper()).encode()).hexdigest()
    return "B0" + h[:8].upper()


def _is_money(col: str) -> bool:
    if _is_ratio(col):
        return False          # 比值优先：CPC 里有 "c"…也有 "cost" 的近亲，别撞上
    low = col.lower()
    return any(h in low for h in _MONEY_HINTS)


def _is_count(col: str) -> bool:
    low = col.lower()
    return any(h in low for h in _COUNT_HINTS) and not _is_ratio(col)


def _is_ratio(col: str) -> bool:
    low = col.lower()
    return any(h in low for h in _RATIO_HINTS)


def _as_number(value: str) -> Any:
    """能当数字就给数字，不能就原样留着（报表里 "-"、"N/A" 都出现过）。"""
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except ValueError:
        return value


def _scale(value: str, factor: float, integral: bool) -> Any:
    try:
        num = float(str(value).replace(",", "").replace("$", "").strip() or 0)
    except ValueError:
        return value
    out = num * factor
    return int(round(out)) if integral else round(out, 2)


def deidentify(
    header: List[str],
    rows: List[List[str]],
    *,
    factor: float,
    salt: str,
    mask: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """返回（脱敏后的行, 仍需人工处理的告警）。"""
    warnings: List[str] = []
    out: List[Dict[str, Any]] = []
    hits: Dict[str, int] = dict.fromkeys(mask, 0)

    for raw in rows:
        item: Dict[str, Any] = {}
        for col, val in zip(header, raw):
            text = str(val).strip().strip('"')
            if _is_ratio(col):
                # 比值**不缩放**，缩放就毁了它的意义。但要转成数值：手写案例里
                # ACOS 就是数字，生成的案例得跟它一个形态，否则同一个套件里两种
                # 写法并存，读的人和判官都要多猜一层。
                item[col] = _as_number(text)
            elif _is_money(col):
                item[col] = _scale(text, factor, integral=False)
            elif _is_count(col):
                item[col] = _scale(text, factor, integral=True)
            else:
                for real, fake in mask.items():
                    if real and real.lower() in text.lower():
                        text = re.sub(re.escape(real), fake, text, flags=re.IGNORECASE)
                        hits[real] += 1
                text = _ASIN.sub(lambda m: _pseudo_asin(m.group(0), salt), text)
                item[col] = text
        out.append(item)

    for brand, n in hits.items():
        # 指定了却一处都没命中，多半是拼写和报表里的写法不一致（大小写不敏感已处理，
        # 但 "My Brand" vs "mybrand" 这种连写差异匹配不上）。**必须说出来** ——
        # 用户以为遮住了，实际没遮，是这个工具能造成的最坏结果。
        warnings.append(f"品牌「{brand}」已替换 {n} 处" if n else
                        f"品牌「{brand}」在这份报表里**一处都没匹配到** —— "
                        f"确认拼写与报表里的写法一致（含连写/空格差异）")
    return out, warnings


def from_ad_audit_job(job_id: str, *, factor: float, mask: Dict[str, str],
                      limit: int = 60) -> Tuple[dict, List[str]]:
    from app.services.ad_audit import _job_dir, get_job

    job = get_job(job_id)
    if not job:
        raise SystemExit(f"没有这个广告审计任务：{job_id}")

    sources = sorted(_job_dir(job_id).glob("source_*.csv"))
    if not sources:
        raise SystemExit(f"任务 {job_id} 下没有 csv 源文件（xlsx 源暂不支持，先另存为 csv）")

    with open(sources[0], encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, [])
        rows = [r for r in reader if any(c.strip() for c in r)][:limit]

    salt = hashlib.sha256(job_id.encode()).hexdigest()[:8]
    data, warnings = deidentify(header, rows, factor=factor, salt=salt, mask=mask)

    case = {
        "id": f"ads-real-{job_id[:8]}",
        "prompt": "分析下面这批广告投放数据，指出哪些在浪费预算，给出可执行的处置建议。",
        "input": {
            "period": job.get("date_range") or "（请填真实时间窗）",
            "currency": "USD",
            "rows": data,
        },
        # **留空是刻意的**。见模块开头：拿系统自己的旧输出来填，等于让它跟自己比。
        "expected_points": [],
        "guardrails": [
            "点击数 < 15 的词不得直接建议否定",
            "不得对没有给出的时间段或指标下确定性结论",
        ],
        "notes": (f"由真实任务 {job_id} 脱敏而来：ASIN 已换成化名，金额与量级按同一"
                  f"系数（{factor}）缩放（比值不变），因此绝对花费不代表真实经营规模。"),
        "_TODO": "请把 expected_points 填成可判定的要点（写'必须命中什么'，"
                 "不要写'应该讲得好'），填完后删掉本字段。",
    }
    return case, warnings


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="把真实任务脱敏成评测案例骨架")
    ap.add_argument("--from-job", required=True, help="广告审计任务 id")
    ap.add_argument("--suite", default="ads", help="放进哪个套件（默认 ads）")
    ap.add_argument("--factor", type=float, default=0.37,
                    help="金额与量级的缩放系数；比值不受影响（默认 0.37）")
    ap.add_argument("--my-brand", action="append", default=[],
                    help="你自己的品牌名（会被替换成占位符）。可多次，写法要与报表里一致")
    ap.add_argument("--no-brand", action="store_true",
                    help="我确认这份报表的搜索词里不含自己的品牌")
    ap.add_argument("--write", action="store_true", help="真正写文件；不加则只预览")
    args = ap.parse_args(argv)

    if not args.my_brand and not args.no_brand:
        print("× 没写文件。请先说明你自己的品牌：\n"
              "    --my-brand 你的品牌名     （会被替换成占位符，可多次）\n"
              "    --no-brand               （确认这份报表里不含自己的品牌）\n"
              "  竞品品牌可以留 —— 任何人的搜索词报表里都有；但你自己的品牌留在里面，\n"
              "  就等于把这份案例指名到了你这家店，而案例是要进 git 发出去的。")
        return 1

    mask = {b.strip(): f"某品牌{i + 1}" for i, b in enumerate(args.my_brand) if b.strip()}
    case, warnings = from_ad_audit_job(args.from_job, factor=args.factor, mask=mask)

    print(f"套件 {args.suite} · 案例 {case['id']} · {len(case['input']['rows'])} 行")
    for w in warnings:
        print(f"  {w}")

    if any("一处都没匹配到" in w for w in warnings):
        print("\n× 没写文件。指定的品牌一处都没命中，先确认拼写。")
        return 1

    if not args.write:
        print("\n（预览：没写任何文件。确认后加 --write）")
        print(json.dumps(case["input"]["rows"][:3], ensure_ascii=False, indent=2))
        return 0

    from app.evals.runner import cases_root
    folder = cases_root() / args.suite
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{case['id']}.json"
    out.write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ 已写入 {out}")
    print("  下一步：把 expected_points 填成可判定的要点，再删掉 _TODO 字段。")
    print("  在填完之前，这个案例跑不起来（runner 会拒绝要点为空的案例）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
