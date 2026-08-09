"""结论的统一契约：证据 → 诊断 → 带优先级的动作。

**为什么要统一**：贝狸那 10 个官方技能全长一个样 —— 每条结论都挂着可核对的证据，
后面跟着带阈值、带对象的动作。我们这边 90 多个 skill 广度赢，但每家自己定义输出，
于是同一条"广告在浪费钱"的结论，在不同板块里长得都不一样，前端要为每种再写一遍
渲染，用户也没法一眼判断"这个结论凭什么"。

**证据必须是结构化的，不能是一句话。** 现有的 ``cross_campaign_insights`` 已经有
``evidence`` 字段，但类型是自由文本 —— "花费很高转化很差"这种也能塞进去，读的人
无法核对，也没法点开看原始数据行。这里把它拆成
``metric / value / unit / source / as_of``：**能核对的才叫证据。**

向后兼容
--------
``normalize()`` 接受老结构（``{insight_type, summary, evidence: str, action, priority}``）
并升级成新结构，把自由文本证据原样放进 ``note``。老的产出不会因为这个契约而失效 ——
这个仓库改契约的纪律是"加字段可以、让老消费方变哑不行"。
"""
from __future__ import annotations

import re
from typing import Any, List

from pydantic import BaseModel, Field, ValidationError, field_validator

SEVERITIES = ("critical", "high", "medium", "low")
#: 老结构里的 P0/P1/P2 与新结构的对应。
_PRIORITY_MAP = {"p0": "critical", "p1": "high", "p2": "medium"}


class Evidence(BaseModel):
    """一条可核对的证据。

    ``metric`` + ``value`` 是硬要求：没有这两样就不是证据，是感想。
    """

    metric: str = Field(..., description="指标名，如 spend_30d / clicks / acos")
    value: Any = Field(..., description="指标值")
    unit: str = ""
    target: str = Field("", description="这条证据说的是哪个对象：ASIN / 关键词 / 广告活动")
    source: str = Field("", description="数据来自哪儿：ads_api / lingxing / sorftime / upload")
    as_of: str = Field("", description="数据的时间点或时间窗")
    note: str = Field("", description="补充说明；老结构里的自由文本证据落在这里")

    @field_validator("metric")
    @classmethod
    def _metric_not_blank(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("证据必须写明指标名 —— 没有指标名的不是证据")
        return v.strip()


class Action(BaseModel):
    """一个可执行的动作。

    ``guardrail`` 不是装饰：一个不写清"在什么条件下才该做"的建议，执行者无从判断
    该不该照做，也就没法把责任交给自动化。
    """

    type: str = Field(..., description="动作类型，如 negate_keyword / adjust_bid / edit_listing")
    target: str = Field("", description="作用对象")
    detail: str = ""
    guardrail: str = Field("", description="执行前提/阈值，如「点击≥15 且 0 单」")
    reversible: bool = True
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class Finding(BaseModel):
    id: str = ""
    severity: str = "medium"
    title: str
    reasoning: str = Field("", description="从证据到结论的推理，一两句")
    evidence: List[Evidence] = Field(default_factory=list)
    actions: List[Action] = Field(default_factory=list)
    priority_score: int = Field(0, ge=0, le=100)

    @field_validator("severity")
    @classmethod
    def _known_severity(cls, v: str) -> str:
        v = (v or "medium").strip().lower()
        return v if v in SEVERITIES else "medium"


class FindingList(BaseModel):
    findings: List[Finding] = Field(default_factory=list)
    data_notes: str = ""
    generated_at: str = ""

    def unsupported(self) -> List[str]:
        """列出**没有任何证据**的结论。

        这不是校验错误（模型偶尔会给一条纯提示性的说明），而是质量信号：
        评测的"证据完整性"维度、以及 UI 上的"此结论无证据"标记都用它。
        """
        return [f.id or f.title for f in self.findings if not f.evidence]


_LEGACY_KEYS = {"insight_type", "suggested_action", "expected_impact"}


def _split_legacy_evidence(text: str, target: str = "") -> List[Evidence]:
    """尽量从老的自由文本证据里抠出「指标 数值」对，抠不出就整段留 note。

    刻意只认明确的数字模式，不做激进猜测 —— 把"花费很高"硬解析成
    ``spend=?`` 只会造出假证据，那比没有证据更糟。
    """
    out: list = []
    for metric, value, unit in re.findall(
            r"([A-Za-z_][A-Za-z_0-9]{1,24})\s*[=:：]\s*([\d.]+)\s*(%|USD|\$)?", text or ""):
        out.append(Evidence(metric=metric, value=value, unit=unit or "", target=target,
                            note="由旧结构的文本证据自动解析"))
    if not out and (text or "").strip():
        out.append(Evidence(metric="legacy_note", value=text.strip()[:500],
                            target=target, note="旧结构里的自由文本证据，未能结构化"))
    return out


def normalize(raw: Any, *, source: str = "", as_of: str = "") -> FindingList:
    """把各种历史结构统一成 FindingList。

    接受：新结构 dict、``{"findings": [...]}``、老的
    ``cross_campaign_insights`` / ``action_summary`` 列表。
    """
    if isinstance(raw, FindingList):
        return raw
    if isinstance(raw, dict) and "findings" in raw:
        items = raw.get("findings") or []
        notes = raw.get("data_notes", "")
    elif isinstance(raw, list):
        items, notes = raw, ""
    elif isinstance(raw, dict):
        items, notes = [raw], raw.get("data_notes", "")
    else:
        return FindingList()

    findings: list = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        if _LEGACY_KEYS & set(item) or isinstance(item.get("evidence"), str):
            target = item.get("target") or item.get("from_campaign") or ""
            action_text = (item.get("action") or item.get("suggested_action") or "").strip()
            findings.append(Finding(
                id=item.get("id") or f"{item.get('insight_type', 'finding')}-{idx + 1}",
                severity=_PRIORITY_MAP.get(str(item.get("priority", "")).lower(), "medium"),
                title=item.get("summary") or item.get("title") or "（无标题）",
                reasoning=item.get("detail", ""),
                evidence=[e.model_copy(update={"source": e.source or source,
                                               "as_of": e.as_of or as_of})
                          for e in _split_legacy_evidence(item.get("evidence", ""), target)],
                actions=[Action(type=item.get("insight_type") or "review",
                                target=target, detail=action_text,
                                guardrail=item.get("guardrail", ""))] if action_text else [],
            ))
        else:
            try:
                findings.append(Finding(**item))
            except ValidationError:
                # 上游模型偶尔返回缺字段的条目。丢掉这一条、保住其余的 ——
                # 让整份报告因为一条畸形结论而失败，是最糟的取舍。
                continue

    return FindingList(findings=findings, data_notes=notes)


def schema() -> dict:
    """JSON Schema —— 给提示词里贴、也给测试断言用。

    一份 schema 两处用，避免"提示词里写的"和"代码里校验的"各说各话。
    """
    return FindingList.model_json_schema()
