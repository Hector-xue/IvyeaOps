"""White-hat Amazon launch-playbook synthesis.

Sibling of ``ai_synthesis_service`` but with a different deliverable: instead of
a market-research report it produces a *copy-ready, on-site-only (纯白帽) launch
playbook* for a given product + target price.

Reuses the low-level streaming / fallback machinery from ``ai_synthesis_service``
(CLI runners, apimart, deepseek, provider chain) — only the prompts and the two
public generators are new.

Two entry points, mirroring the market module:
  • ``synthesize_native``  — ivyea-agent calls the Sorftime MCP tools itself,
                              then writes the playbook in one pass (preferred).
  • ``synthesize``         — fallback: caller pre-fetches the selected source, we feed
                              it to the provider chain (deepseek / apimart / CLI).
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Dict

from app.services.ai_synthesis_service import (
    _stream_ivyea_agent,
    _text_provider_chain,
    _try_apimart,
    _try_assistant,
    _try_cli,
    _try_deepseek,
    _try_ivyea_agent,
)

# ── Sorftime data-collection instructions (MCP-native path only) ──────────────
# Identical tool sequence to the market module, so the agent gathers the same
# rich category / competitor / review / CPC data the playbook reasons over.

_COLLECT_KEYWORD = """## 第一阶段：数据采集（必须全部完成，不得跳过任何一步）

**重要：在调用完下列全部10个工具之前，禁止输出任何手册内容。先把数据收齐，再写打法。**

你的工具列表中有 `mcp_sorftime_*` 系列工具，请**严格按顺序**依次调用：

**步骤 1** — `mcp_sorftime_keyword_detail`  参数 keyword="__QUERY__", keyword_support_site="__MKT__"
  目的：月搜索量、CPC、转化率等核心指标（用于广告竞价与流量判断）
**步骤 2** — `mcp_sorftime_keyword_trend`  参数 keyword="__QUERY__", keyword_support_site="__MKT__"
  目的：12个月搜索趋势（用于上架时机与备货节奏）
**步骤 3** — `mcp_sorftime_keyword_extends`  参数 keyword="__QUERY__", keyword_support_site="__MKT__"
  目的：长尾词扩展（用于自然流量关键词布局与广告分组）
**步骤 4** — `mcp_sorftime_keyword_search_results`  参数 keyword="__QUERY__", keyword_support_site="__MKT__"
  目的：首页竞品格局；**记录返回结果中前2个产品的 asin 字段，步骤9、10要用**
**步骤 5** — `mcp_sorftime_category_search_from_product_name`  参数 product_name="__QUERY__", amz_site="__MKT__"
  目的：品类节点；**记录返回的 node_id 字段，步骤6要用**
**步骤 6** — `mcp_sorftime_category_report`  参数 node_id=<步骤5的node_id>, amz_site="__MKT__"
  目的：该品类 TOP100 产品（价格带分布、销量分布、评论门槛、垄断度）
**步骤 7** — `mcp_sorftime_similar_product_feature`  参数 product_name="__QUERY__", amz_site="__MKT__"
  目的：同类产品共性特征与差异点（用于差异化卖点）
**步骤 8** — `mcp_sorftime_potential_product`  参数 search_name="__QUERY__", amz_site="__MKT__"
  目的：潜力产品列表
**步骤 9** — `mcp_sorftime_product_detail`  参数 asin=<步骤4第1个ASIN>, amz_site="__MKT__"
  目的：首页第一名竞品详情（价格/评分/评论数/卖点）
**步骤 10** — `mcp_sorftime_product_detail`  参数 asin=<步骤4第2个ASIN>, amz_site="__MKT__"
  目的：首页第二名竞品详情
"""

_COLLECT_ASIN = """## 第一阶段：数据采集（必须全部完成，不得跳过任何一步）

**重要：在调用完下列全部8个工具之前，禁止输出任何手册内容。先把数据收齐，再写打法。**

你的工具列表中有 `mcp_sorftime_*` 系列工具，请**严格按顺序**依次调用：

**步骤 1** — `mcp_sorftime_product_detail`  参数 asin="__QUERY__", amz_site="__MKT__"
  目的：对标竞品画像、月销量、价格、评分、BSR
**步骤 2** — `mcp_sorftime_product_trend`  参数 asin="__QUERY__", amz_site="__MKT__"
  目的：最近12个月销量与价格趋势
**步骤 3** — `mcp_sorftime_product_traffic_terms`  参数 asin="__QUERY__", amz_site="__MKT__"
  目的：该竞品主要流量词；**记录搜索量最大的关键词，步骤6、7要用**
**步骤 4** — `mcp_sorftime_product_reviews`  参数 asin="__QUERY__", amz_site="__MKT__"
  目的：好评/差评分布与高频痛点（用于差异化与补评策略）
**步骤 5** — `mcp_sorftime_product_variations`  参数 asin="__QUERY__", amz_site="__MKT__"
  目的：变体结构与各变体销量占比
**步骤 6** — `mcp_sorftime_keyword_detail`  参数 keyword=<步骤3最大流量词>, keyword_support_site="__MKT__"
  目的：主流量词月搜索量、CPC、竞争强度
**步骤 7** — `mcp_sorftime_keyword_search_results`  参数 keyword=<步骤3最大流量词>, keyword_support_site="__MKT__"
  目的：主流量词首页竞品格局与价格带
**步骤 8** — `mcp_sorftime_competitor_product_keywords`  参数 asin="__QUERY__", keyword_support_site="__MKT__"
  目的：竞品词机会（自然/广告布词盲区）
"""

# ── Playbook report template (shared by native + fallback paths) ──────────────

_PLAYBOOK_TEMPLATE = """# 「__QUERY__」白帽站内打法手册（亚马逊 __MKT__ 站）

**输入条件**：目标售价 $__PRICE__ ｜ 成本估算 __COST__

**铁律（违反则手册无效）：**
【纯白帽 · 仅站内流量——最高优先级】
1. 只允许**亚马逊站内合规手段**：站内广告(SP/SB/SBV/SD)、Coupon/Prime专享折扣/秒杀(Lightning/Best Deal)、Vine/早期评论计划、Request a Review、Listing/A+/品牌旗舰店优化、合规站内关联流量。
2. **严禁**任何灰黑产手段：刷单/测评补单/Review操纵、自养号、礼品卡返现索评、操纵BSR、虚假Q&A、站外刷量、用任何方式违反亚马逊TOS。如数据显示某打法依赖此类手段，必须明确指出并给出白帽替代方案。
3. **不做站外流量**（Facebook/Google/红人/Deal站导流一律不写入主方案）；若类目确需站外，只在"补充说明"里一句话提示，不展开。
【数据真实性】
4. 所有数字（搜索量、CPC、价格、评分、评论数、ASIN、市场份额等）必须来自工具实际返回数据；缺失填"N/A"，不得虚构或用行业均值冒充，存疑标注口径。
5. 竞品 ASIN 必须是工具返回的真实 ASIN。
【格式】
6. 量化数据用 Markdown 表格；竞价/预算给具体数值或区间，不能只说"适当"。
7. 每章正文分析不少于 60 字，给出可执行的"做什么"，而非泛泛而谈。

---

> **执行摘要 / 打法定档**
> ① **打法档位**：（从下列四档中**明确选一档**并说明依据）
>   - A 低价快速起量：低货值/高频/低评论门槛/价格敏感 → 重广告冲排名 + Coupon + 低价卡位
>   - B 中高价差异化精耕：货值较高/差评痛点明显/评论门槛高 → 改良卖点 + 精准长尾 + SB品牌 + 耐心补评
>   - C 长尾利基卡位：垄断度低/长尾分散 → 长尾词精准卡位，避开大词正面竞争
>   - D 旺季节奏：季节性强 → 提前备货 + 旺季前布局 + 节点冲量
> ② **定价裁决**：目标价 $__PRICE__ 相对类目价格带 = 偏低/适中/偏高，建议落点 $xx–$xx
> ③ **核心切入点**：（结合竞品差评痛点，一句话）
> ④ **预计起量周期**：约 x 周见自然单 ｜ 启动资金约 $xxxx
> ⑤ **综合可操盘评分**：x/10 ｜ **结论**：强力推进 / 谨慎推进 / 不建议进

---

## 一、定价校验与利润测算
| 项目 | 数值 | 说明 |
|------|------|------|
| 目标售价 | $__PRICE__ | 用户输入 |
| 类目主流价格带 | $–$ | 来自 category_report / 首页竞品 |
| 建议定价 | $ | 起量期 / 稳定期可不同 |
| Coupon后到手价 | $ | 建议折扣力度 |
| 单件成本(采购+头程+FBA) | __COST__ | 未填则标注"需补充" |
| 毛利 / 毛利率 | $ / % | |
| 可承受最高ACOS(盈亏平衡) | % | 毛利率推导 |
| 广告投入空间裁决 | 充足/紧张/不支撑 | |

（正文：判断目标价能否在该类目立足、是否留出广告与利润空间；价格不合理时给出调整建议）

## 二、竞争格局与差异化切入点
| 指标 | 数值 |
|------|------|
| 主词月搜索量 | |
| 首页竞品均价 / 价格区间 | $ / $–$ |
| TOP3市场份额（垄断度） | % |
| 首页竞品平均评论数（入场门槛） | |
| 首页竞品平均评分 | /5.0 |
| 近12个月新品破局案例 | 有/无 |

**头部竞品差评痛点 → 差异化机会：**
| 竞品ASIN | 价格 | 评分 | 高频差评痛点 | 我方差异化卖点 |
|---------|------|------|------------|-------------|
（≥3行，痛点要具体到产品层面）

（正文：本品靠什么差异点切入，能否绕开头部正面竞争）

## 三、Listing / SEO 关键词布局（站内自然流量）
| 关键词 | 类型(核心/长尾) | 月搜索量 | 竞争度 | 落位(标题/五点/ST后台) | 优先级 |
|--------|--------------|---------|-------|--------------------|-------|
（≥15行，含足量长尾词）

**Listing 配置建议：**
| 模块 | 建议 |
|------|------|
| 标题(前80字符核心词) | |
| 五点(卖点对应差异化) | |
| 主图 / 副图 / 视频 | |
| A+ / 品牌故事 | 必做/建议/可选 |
| 后台ST关键词 | |

（正文：自然流量靠哪些词承接、如何用差异点写卖点）

## 四、站内广告打法（白帽核心）
**广告活动结构（分组）：**
| 活动 | 类型 | 投放方式 | 目标 | 起始日预算 | 建议起始竞价 | 说明 |
|------|------|---------|------|----------|------------|------|
| SP-Auto | SP | 自动(四种匹配) | 跑词/捡漏 | $ | $ | 否词回流手动 |
| SP-精准 | SP | 手动精准 | 核心词冲排名 | $ | $（参考CPC） | |
| SP-广泛/词组 | SP | 手动广泛 | 拓长尾 | $ | $ | |
| SP-ASIN定向 | SP | 商品投放 | 拦截弱竞品 | $ | $ | 定向差评多的竞品 |
| SB/SBV | SB | 品牌词/类目词 | 品牌占位 | $ | $ | 有品牌注册再开 |
| SD | SD | 再营销/受众 | 复访收口 | $ | $ | 阶段性 |

**分阶段竞价 / ACOS 目标：**
| 阶段 | 周次 | 竞价策略 | 目标ACOS | 重点 |
|------|------|---------|---------|------|
| 冷启动 | 1–2周 | 高于建议价抢曝光 | 可亏 | 拿初始单与排名 |
| 爬坡 | 3–4周 | 按转化降竞价 | 收窄 | 提自然占比 |
| 稳定 | 5周+ | 维持ROI | ≤盈亏线 | 防御性投放 |

（正文：预算如何在各组分配，否词与竞价调整节奏，怎样把广告单转成自然排名）

## 五、合规补评 / 评分爬坡计划（纯白帽）
| 手段 | 合规性 | 预计获评量 | 启用时机 | 备注 |
|------|-------|----------|---------|------|
| Vine 评论计划 | ✅官方 | | 上架即开 | 至多30条 |
| 早期评论 / 站内自动索评(Request a Review) | ✅官方 | | 出单后 | |
| 包装卡合规引导(不返现不指定好评) | ✅ | | | 仅引导真实评价 |

**评分目标 vs 门槛：** 上架3个月需达 ≥ __ 条评论、≥ __ 星，方可与首页竞争（对照第二章门槛）。
（正文：纯白帽下如何在评论门槛内起量；**重申严禁任何刷评/测评补单**）

## 六、站内促销与流量节奏
| 活动 | 时机 | 力度 | 目的 |
|------|------|------|------|
| Coupon | 上架即挂 | %/$ | 提转化、点击率 |
| Prime专享折扣 | 爬坡期 | | 冲销量权重 |
| Lightning/Best Deal | 评分达标后 | | 节点冲量 |
| 关联流量(捆绑/虚拟捆绑) | 有第二SKU时 | | 站内导流 |

（正文：促销与广告如何配合冲排名；**不写任何站外引流**）

## 七、上架起量时间表（逐周行动清单）
| 周次 | 阶段 | 关键动作 | 预算 | KPI |
|------|------|---------|------|-----|
| 第0周 | 准备 | Listing/图片/A+/Vine/Coupon就位、首批库存到仓 | — | 上架就绪 |
| 第1–2周 | 冷启动 | 开SP-Auto+精准、Coupon、Vine | $ | 首单/首评 |
| 第3–4周 | 爬坡 | 加词、否词、ASIN定向、降竞价 | $ | 自然排名进首页 |
| 第5–8周 | 稳定 | 维持ROI、开SB/Deal | $ | TACOS下降、自然单占比↑ |

## 八、预算与盈亏测算
| 项目 | 金额 |
|------|------|
| 首批备货量建议 | 件 |
| 启动资金合计 | $ |
| 各阶段广告预算合计 | $ |
| 预计稳定期 ACOS / TACOS | % / % |
| 盈亏平衡月销量 | 件 |

## 九、风险与监控指标
| 监控指标 | 健康阈值 | 触发动作 |
|---------|---------|---------|
| 转化率(CVR) | ≥ % | 低于则查Listing/价格 |
| 广告占比(TACOS) | ≤ % | 超则收缩竞价 |
| 评分 | ≥ 星 | 跌破则查差评/质量 |
| 自然排名(主词) | 周环比↑ | 停滞则加投/优化 |

（正文：什么信号说明打法跑通、什么信号要止损或换档）

---

## 十、广告投放批量表（CSV）
说明：以下为 **Amazon Sponsored Products 批量(bulksheet)兼容列**，含真实关键词与基于 CPC 推导的建议竞价。**导入前请在广告活动管理后台核对列映射**（不同账户/语言模板列名可能略有差异）。请用真实数据填充，至少包含 1 个 SP-Auto 活动、1 个手动精准活动及其 ≥10 个核心/长尾关键词行。

```csv
Product,Entity,Operation,Campaign Name,Ad Group Name,Campaign Daily Budget,Ad Group Default Bid,Targeting Type,Match Type,Keyword Text,Product Targeting Expression,Bid,State
Sponsored Products,Campaign,Create,__QUERY__-Auto,,20,,Auto,,,,,enabled
Sponsored Products,Ad group,Create,__QUERY__-Auto,Auto-AdGroup,,0.75,,,,,,enabled
Sponsored Products,Campaign,Create,__QUERY__-Exact,,30,,Manual,,,,,enabled
Sponsored Products,Ad group,Create,__QUERY__-Exact,Exact-AdGroup,,0.90,,,,,,enabled
Sponsored Products,Keyword,Create,__QUERY__-Exact,Exact-AdGroup,,,,exact,<真实核心词>,,1.10,enabled
```
（请把示例行替换为真实活动结构与关键词；竞价参考各词 CPC 设定）"""


def _fill(text: str, query: str, marketplace: str, price: str, cost: str) -> str:
    """Token substitution (avoids str.format brace clashes with CSV/markdown)."""
    return (
        text.replace("__QUERY__", query)
        .replace("__MKT__", marketplace)
        .replace("__PRICE__", price)
        .replace("__COST__", cost)
    )


def _native_prompt(mode: str, query: str, marketplace: str, price: str, cost: str) -> str:
    collect = _COLLECT_KEYWORD if mode == "keyword" else _COLLECT_ASIN
    head = "你是亚马逊跨境电商运营操盘专家，只用纯白帽、站内合规手段把新品推起来。\n\n"
    bridge = "\n---\n\n## 第二阶段：生成打法手册\n**以上工具全部调用完毕后**，根据真实数据填写下方手册模板。\n\n"
    return _fill(head + collect + bridge + _PLAYBOOK_TEMPLATE, query, marketplace, price, cost)


def _fallback_prompt(
    mode: str, query: str, marketplace: str, price: str, cost: str, data: Dict[str, Any],
    source: str = "Sorftime",
) -> str:
    from app.services.sorftime_service import summarize_for_prompt
    # Per-source budget — see ai_synthesis_service._build_prompt.
    data_summary = summarize_for_prompt(data)
    head = (
        "你是亚马逊跨境电商运营操盘专家，只用纯白帽、站内合规手段把新品推起来。"
        f"请根据下方 {source} 原始数据，生成完整的白帽站内打法手册。\n\n"
    )
    body = _fill(_PLAYBOOK_TEMPLATE, query, marketplace, price, cost)
    return f"{head}{body}\n\n---\n原始数据（来自{source}）：\n{data_summary}"


async def synthesize_native(
    mode: str,
    query: str,
    marketplace: str,
    price: str,
    cost: str,
) -> AsyncGenerator[tuple[str, str], None]:
    """MCP-native path: the agent fetches Sorftime data via MCP, then writes the
    playbook. Yields (provider, chunk); on failure yields ('error', detail).

    Runs on ivyea-agent (2026-08-06; previously hermes) — same MCP-native shape
    as ``ai_synthesis_service.synthesize_native``."""
    prompt = _native_prompt(mode, query, marketplace, price, cost)
    got_real_chunk = False
    try:
        async for chunk in _stream_ivyea_agent(
            prompt,
            inject_retrieval=False,
            use_tools=True,
            plan_mode=False,      # 计划模式会拒绝 mcp_call_tool，取不到真实数据
            max_steps=40,
            system=(
                "你正在作为 IvyeaOps 的打法生成智能体。"
                "先用 mcp_list_tools 发现已配置的数据源工具，再用 mcp_call_tool 抓真实数据，"
                "然后基于真实数据写打法。抓不到数据时必须直说，不要编造数字。"
            ),
        ):
            got_real_chunk = True
            yield "ivyea-agent", chunk
    except Exception as exc:  # noqa: BLE001
        if not got_real_chunk:
            yield "error", f"IvyeaAgent 原生取数失败：{exc}"
            return
    if not got_real_chunk:
        yield "error", "IvyeaAgent 无输出"


async def synthesize(
    mode: str,
    query: str,
    marketplace: str,
    price: str,
    cost: str,
    data: Dict[str, Any],
    skip_agent: bool = False,
    source: str = "Sorftime",
) -> AsyncGenerator[tuple[str, str], None]:
    """Fallback path: feed pre-fetched selected-source data to the provider chain
    (ivyea-agent / deepseek / assistant / codex / claude). Yields (provider, chunk); on total
    failure yields ('error', diagnostic).

    skip_agent=True drops ivyea-agent (caller is already the agent — the panel bridge)."""
    prompt = _fallback_prompt(mode, query, marketplace, price, cost, data, source=source)
    failures: list[str] = []
    # 2026-08-06: hermes 已不在 _text_provider_chain() 的候选里（见 _VALID_TEXT_PROVIDERS），
    # 这里不必再单独过滤；兜底顺序按真实可用性排（deepseek 用官方 key，assistant 槽随用户配置）。
    chain = _text_provider_chain() or ["deepseek", "assistant", "codex", "claude"]
    if skip_agent:
        chain = [p for p in chain if p != "ivyea-agent"]

    for provider in chain:
        if provider == "ivyea-agent":
            gen = _try_ivyea_agent(prompt, failures)
        elif provider == "deepseek":
            gen = _try_deepseek(prompt, failures)
        elif provider == "apimart":
            gen = _try_apimart(prompt, failures)
        elif provider == "assistant":
            gen = _try_assistant(prompt, failures)
        else:
            gen = _try_cli(provider, prompt, failures)
        got_real_chunk = False
        async for prov, chunk in gen:
            yield prov, chunk
            if prov != "_attempt":
                got_real_chunk = True
        if got_real_chunk:
            return

    yield "error", (
        "所有文本 AI 提供商均不可用：\n"
        + "\n".join(f"  • {f}" for f in failures)
        + f"\n\n当前提供商顺序：{', '.join(chain) or '（空）'}"
    )
