"""AI synthesis with fallback chain.

Priority:
  1. deepseek  (DeepSeek-chat via HTTP streaming — true token-by-token)
  2. apimart   (Claude via HTTP streaming)
  3. Hermes CLI
  4. Codex CLI
  5. Claude CLI

Exposes a single async generator ``synthesize(...)`` that yields text chunks.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict

import httpx

from app.core.proc import no_window_kwargs
from app.services.runners import _build_runner_cmd, _find_bin, build_child_env

logger = logging.getLogger("ivyea.services.ai_synthesis_service")

_log = logging.getLogger(__name__)

# In-memory observability: last N text-chain calls, surfaced in 系统配置 →
# 「最近 AI 调用」 so admins can see which provider answered without journald.
_AI_CALL_LOG: "deque[dict[str, Any]]" = deque(maxlen=50)


def record_ai_call(provider: str, ok: bool, *, chars: int = 0,
                   failures: list[str] | None = None, kind: str = "text") -> None:
    _AI_CALL_LOG.appendleft({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "provider": provider,
        "ok": ok,
        "chars": chars,
        "kind": kind,
        "failures": list(failures or []),
    })


def recent_ai_calls(limit: int = 50) -> list[dict[str, Any]]:
    return list(_AI_CALL_LOG)[:limit]


def _apimart_key() -> str:
    """Return the configured Apimart key, or '' if unset. No hardcoded
    fallback — past attempts to ship a 'shared' key got banned upstream."""
    from app.core import hub_settings
    val = hub_settings.get("apimart_key")
    return str(val) if val else ""


def _apimart_base() -> str:
    from app.core import hub_settings
    val = hub_settings.get("apimart_base")
    return str(val) if val else "https://api.apimart.ai/v1"


def _read_hermes_env() -> Dict[str, str]:
    """Parse ~/.hermes/.env and return its key=value pairs."""
    result: Dict[str, str] = {}
    try:
        text = (Path.home() / ".hermes" / ".env").read_text(errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    except Exception:
        logger.debug("text = 失败（旁路，已忽略）", exc_info=True)
    return result


def _deepseek_key() -> str:
    """Return DeepSeek API key from hub_settings, falling back to ~/.hermes/.env."""
    from app.core import hub_settings
    val = hub_settings.get("deepseek_api_key")
    if val:
        return str(val)
    return _read_hermes_env().get("DEEPSEEK_API_KEY", "")


# "assistant" == the global fallback text model (the AI 问答 slot, assistant_*).
# It is a user-configured OpenAI-compatible / anthropic HTTP endpoint, so it is
# safe for non-admin users and acts as the standard chain's fallback step.
# NOTE: apimart is IMAGE-GEN ONLY (no text/chat endpoint) — it must NOT appear
# in any text chain; calling its /v1/messages returns 403.
# 2026-08-06：hermes 从文本链候选中移除。旧配置里残留的 "hermes" 会在
# _text_provider_chain() 里被静默过滤掉，不会报错、也不会再被自动调用。
_VALID_TEXT_PROVIDERS = ("ivyea-agent", "codex", "claude", "deepseek", "assistant")


# Providers safe for non-admin users: pure HTTP APIs, no local CLI / shell / MCP.
_HTTP_ONLY_PROVIDERS = ("ivyea-agent", "deepseek", "assistant")


def _text_provider_chain() -> list[str]:
    """Parse the comma-separated text_ai_providers setting and filter to
    known names. Empty / malformed config falls back to the built-in-safe order.

    SECURITY: for non-admin users, the chain is forced to HTTP-only providers
    (deepseek / apimart) so a user request can NEVER spawn a local CLI agent
    (codex/claude) with shell / MCP / filesystem access."""
    from app.core import hub_settings
    # Standard chain: IvyeaAgent first, then HTTP DeepSeek / global fallback,
    # then optional external CLI agents. Hermes is no longer part of any
    # automatic chain (2026-08-06) — it stays available only where the user
    # picks a provider by hand in the /agents board.
    raw = str(hub_settings.get("text_ai_providers") or "").strip()
    if not raw:
        chain = ["ivyea-agent", "deepseek", "assistant", "codex", "claude"]
    else:
        out: list[str] = []
        for p in raw.split(","):
            p = p.strip().lower()
            if p in _VALID_TEXT_PROVIDERS and p not in out:
                out.append(p)
        chain = out or ["ivyea-agent", "deepseek", "assistant", "codex", "claude"]

    # IvyeaAgent is the default brain everywhere: lead with it regardless of the
    # configured order (graceful fallback to the rest if it's down). The panel
    # bridge passes skip_agent=True to drop it and avoid agent→ops→agent nesting.
    def _lead_with_agent(c: list[str]) -> list[str]:
        return ["ivyea-agent"] + [p for p in c if p != "ivyea-agent"]

    # Non-admin (and only when a request context is set) → HTTP-only, with the
    # global fallback model after the agent (HTTP-safe, no local CLI/MCP/shell).
    try:
        from app.core.security import current_user
        cu = current_user.get()
        if cu is not None and cu.get("role") != "admin":
            http = [p for p in chain if p in _HTTP_ONLY_PROVIDERS]
            return _lead_with_agent(http or ["assistant", "deepseek"])
    except Exception:
        logger.debug("current_user.get 失败（旁路，已忽略）", exc_info=True)
    return _lead_with_agent(chain)

_ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

# ─── MCP-native prompts (the agent calls sorftime tools itself) ───────────────
# Used on the native path — no pre-fetched data needed. ivyea-agent has
# sorftime MCP registered as trusted in ~/.ivyea/mcp.json, so it can
# call each tool directly and synthesise from live data in one pass.

_MCP_KEYWORD_NATIVE_PROMPT = """你是亚马逊跨境电商市场分析专家。

## 第一阶段：数据采集（必须全部完成，不得跳过任何一步）

**重要：在调用完下列全部10个工具之前，禁止输出任何报告内容。先把数据收齐，再写报告。**

你的工具列表中有 `mcp_sorftime_*` 系列工具，请**严格按顺序**依次调用：

**步骤 1** — 调用 `mcp_sorftime_keyword_detail`
  参数：keyword="{query}", keyword_support_site="{marketplace}"
  目的：获取关键词月搜索量、CPC、转化率等核心指标

**步骤 2** — 调用 `mcp_sorftime_keyword_trend`
  参数：keyword="{query}", keyword_support_site="{marketplace}"
  目的：获取12个月搜索趋势数据

**步骤 3** — 调用 `mcp_sorftime_keyword_extends`
  参数：keyword="{query}", keyword_support_site="{marketplace}"
  目的：获取长尾词扩展列表（用于第五章长尾词矩阵）

**步骤 4** — 调用 `mcp_sorftime_keyword_search_results`
  参数：keyword="{query}", keyword_support_site="{marketplace}"
  目的：获取首页竞品列表；**记录返回结果中前2个产品的 asin 字段，步骤9和10需要用到**

**步骤 5** — 调用 `mcp_sorftime_category_search_from_product_name`
  参数：product_name="{query}", amz_site="{marketplace}"
  目的：获取品类节点；**记录返回结果中的 node_id 字段，步骤6需要用到**

**步骤 6** — 调用 `mcp_sorftime_category_report`
  参数：node_id=<步骤5返回的node_id值>, amz_site="{marketplace}"
  目的：获取该品类 TOP 100 产品数据（价格分布、销量分布、市场格局）

**步骤 7** — 调用 `mcp_sorftime_similar_product_feature`
  参数：product_name="{query}", amz_site="{marketplace}"
  目的：获取同类产品的共同特征与差异点

**步骤 8** — 调用 `mcp_sorftime_potential_product`
  参数：search_name="{query}", amz_site="{marketplace}"
  目的：获取该品类潜力产品列表

**步骤 9** — 调用 `mcp_sorftime_product_detail`
  参数：asin=<步骤4记录的第1个ASIN>, amz_site="{marketplace}"
  目的：获取首页第一名竞品详细数据

**步骤 10** — 调用 `mcp_sorftime_product_detail`
  参数：asin=<步骤4记录的第2个ASIN>, amz_site="{marketplace}"
  目的：获取首页第二名竞品详细数据

---

## 第二阶段：生成报告

**以上10个工具全部调用完毕后**，根据收集到的真实数据，填写以下报告模板。

**硬性要求（必须遵守，违反则报告无效）：**

【数据真实性——最高优先级】
1. **所有数字必须来自工具实际返回的数据**，包括 ASIN、品牌、月销量、评分、评论数、价格、CPC 等，禁止自行虚构或凭印象填写
2. **工具未返回的字段一律标注"N/A"**，不得用推测值、行业均值或"大约"替代
3. **正文分析中的每个结论必须有数据支撑**，禁止无依据的主观推测；若数据不足以支撑某结论，必须明确注明"数据有限，仅供参考"
4. **竞品 ASIN 必须是工具实际返回的真实 ASIN**，禁止自行编造 ASIN 编号
5. **数据来源存疑时必须标注来源口径**（如"基于 TOP20 样本估算"）

【格式要求】
6. 所有量化数据必须用Markdown表格呈现，禁止在正文段落中罗列数字
7. 月度趋势表必须包含12行（1月–12月），不得省略
8. 价格区间表必须包含每个区间的产品数、销量、占比三列
9. 市场格局表至少列出TOP5产品的ASIN、月销量、市场份额%
10. 长尾词矩阵至少15行
11. 每个章节正文分析不少于80字，禁止只有表格没有分析

---

# 「{query}」市场调研报告（亚马逊 {marketplace} 站）

> **执行摘要**：
> ① **市场规模与趋势**：（月搜索量级别 + 近期趋势方向，一句话）
> ② **竞争格局**：（垄断程度 + 新卖家破局空间，一句话）
> ③ **头部产品核心痛点**：（当前头部产品2–3个具体的产品层面共性问题，如"防水失效/夜视差/续航误报"）
> ④ **可操作切入口**：（具体到价格带 + 目标用户场景 + 差异化功能组合，一句话）
> ⑤ **主要风险**：（最大的1–2个风险点，一句话）
>
> **综合机会评分**：x/10 ｜ **建议决策**：强烈推荐进入 / 谨慎进入 / 暂不推荐

---

## 一、关键词核心指标

| 指标 | 数值 | 说明 |
|------|------|------|
| 月搜索量 | | |
| 90天搜索趋势 | | 上升↑ / 下降↓ / 平稳→ |
| 点击竞价（CPC） | $ | |
| 购买转化率 | % | |
| 直接竞品数 | | |
| 精准搜索结果数 | | |
| 首页产品均价 | $ | |
| 首页产品平均评论数 | | |
| 首页产品平均评分 | / 5.0 | |

（正文分析：解读以上数据说明该关键词的市场热度、竞争强度、变现效率）

---

## 二、市场容量 × 价格区间分析

**整体市场容量：**

| 指标 | 数值 |
|------|------|
| 品类月总销售额（TOP100估算） | $ |
| TOP100产品月总销量 | 件 |
| 平均客单价 | $ |
| 品类月均增长趋势 | % |

**各价格区间销量分布：**
（根据 `category_report` 实际价格分布划分区间；以下区间为示例，**请按品类真实价格段自行调整边界**，确保每个区间有产品且合计覆盖 90%+ 产品）

| 价格区间 | 产品数 | 月均销量 | 月销售额估算 | 销量占比 | 竞争强度 |
|---------|--------|---------|------------|---------|--------|
| （低价区间） | | | $ | % | |
| （中低价区间） | | | $ | % | |
| （中高价区间） | | | $ | % | |
| （高价区间） | | | $ | % | |
| $100+ | | | $ | % | |
| **汇总** | | | $ | 100% | |

**各价格区间头部卖家 × 评论分析：**

| 价格区间 | 代表 ASIN | 月销量 | 评分 | 评论数 | 核心卖点（高频好评） | 主要差评痛点 | 可改进空间 |
|---------|---------|--------|------|--------|-----------------|------------|---------|
| （低价区） | | | /5.0 | | | | |
| （中低价区） | | | /5.0 | | | | |
| （中高价区） | | | /5.0 | | | | |
| （高价区） | | | /5.0 | | | | |
| $100+ | | | /5.0 | | | | |

（每个价格区间列月销量最高的代表产品；"可改进空间"填写后来者针对该区间差评可做的具体改进；价格区间边界与上方分布表保持一致）

**各价格区间切入机会评估：**

| 价格区间 | 切入难度 | 机会点 | 切入条件 | 建议 |
|---------|---------|-------|---------|-----|
| （低价区） | 高/中/低 | | | 推荐/谨慎/不推荐 |
| （中低价区） | | | | |
| （中高价区） | | | | |
| （高价区） | | | | |
| $100+ | | | | |

**最优切入价格带**：$xx–$xx（理由：xxx）
（正文分析：说明各价格带的市场容量和竞争饱和度，重点分析推荐切入价格带的空间逻辑——头部卖家弱点在哪里、新品靠什么切入、毛利空间是否支撑广告投入）

---

## 三、月度搜索趋势（淡旺季分析）

| 月份 | 搜索指数 | 环比变化 | 季节性 |
|------|---------|--------|-------|
| 1月 | | | |
| 2月 | | | |
| 3月 | | | |
| 4月 | | | |
| 5月 | | | |
| 6月 | | | |
| 7月 | | | |
| 8月 | | | |
| 9月 | | | |
| 10月 | | | |
| 11月 | | | |
| 12月 | | | |

**旺季**：x月–x月（峰值搜索指数 xxx）｜**淡季**：x月–x月
**备货节点**：旺季前 x 个月开始备货，首批建议库存 xxx 件
（正文分析：分析季节性驱动因素，说明与节假日/消费场景的关联）

---

## 四、市场格局与垄断度

**TOP 产品竞争矩阵：**

| 排名 | ASIN | 品牌 | 月销量 | 月销额 | 市场份额% | 评分 | 评论数 | 上架时长 |
|------|------|------|--------|--------|---------|------|--------|---------|
| 1 | | | | $ | % | | | 个月 |
| 2 | | | | $ | % | | | 个月 |
| 3 | | | | $ | % | | | 个月 |
| 4 | | | | $ | % | | | 个月 |
| 5 | | | | $ | % | | | 个月 |
| 6–10名合计 | — | — | | $ | % | — | — | — |

**市场集中度指标：**

| 垄断度指标 | 数值 | 评级 |
|-----------|------|------|
| TOP3市场份额 | % | 高垄断(>60%) / 中等(30-60%) / 分散(<30%) |
| TOP10市场份额 | % | |
| 最大单品市场份额 | % | |
| 近90天新品数量 | 款 | |
| 新品平均月销 | 件 | |
| 首页新卖家占比 | % | <12个月算新 |
| 头部品牌集中度 | | 品牌集中 / 多品牌分散 |

（正文分析：评估市场垄断程度，分析新卖家生存空间，判断是否存在市场破局机会）

---

## 五、长尾词机会矩阵

| 关键词 | 月搜索量 | CPC | 竞争品数 | 首页均价 | 机会指数 | 推荐优先级 |
|--------|---------|-----|---------|---------|---------|----------|
| | | $ | | $ | /10 | 高/中/低 |
| | | | | | | |
| | | | | | | |
（≥15行，按机会指数从高到低排列）

（正文分析：说明长尾词布局逻辑，推荐重点攻克的3–5个词及原因）

---

## 六、用户需求痛点与差异化机会

**数据来源：similar_product_feature、category_report TOP100 产品特征、长尾词修饰词分析**

| 痛点维度 | 当前市场普遍问题 | 消费者真实诉求 | 可切入的差异化方向 |
|---------|--------------|-------------|----------------|
| | | | |
| | | | |
| | | | |
| | | | |
| | | | |
（至少5行，每行代表一个独立痛点方向，结合长尾词中的修饰词如"waterproof/long battery/easy setup/no subscription"等推断）

**新品定义速查：**

| 维度 | 当前市场主流 | 建议新品方向 |
|------|-----------|-----------|
| 功能重点 | | |
| 目标用户场景 | | |
| 最优价格带 | $ | $ |
| 核心卖点方向（标题前5词） | | |
| 需规避的同质化雷区 | | |

（正文分析：综合以上痛点，说明哪个产品方向最有可操盘性，给出具体"做什么产品"建议，越具体越好）

---

## 七、品类市场结构

| 品类指标 | 数值 | 说明 |
|---------|------|------|
| 一级品类 | | |
| 所属节点（Browse Node） | | |
| 品类在售产品总数 | | |
| 品类月总销售额估算 | $ | |
| 品类增长趋势（YoY） | % | |
| 主要头部品牌 | | |
| 品牌注册产品占比 | % | |
| 中国卖家占比 | % | 供应链竞争程度 |
| 上线3个月内新品销量占比 | % | 新品破局可能性 |
| TOP100平均评论数 | 条 | 入场评论门槛参考 |
| TOP100平均评分 | 星 | 品质门槛参考 |
| 亚马逊自营占比 | % | 平台竞争风险 |

（正文分析：描述品类整体发展阶段，分析头部品牌打法和白牌生存空间）

---

## 八、入场门槛评估

| 门槛维度 | 基准要求 | 达标难度 |
|---------|---------|---------|
| 最低起评数（上架3个月） | 条 | 高/中/低 |
| 推荐最低评分 | ≥ 星 | |
| 最优价格定位 | $–$ | |
| 预估启动资金 | $ | （含首批库存+广告费） |
| 图片/视频配置 | | |
| A+页面 / 品牌注册 | | 必须/建议/可选 |
| 合规认证要求 | | |

（正文分析：综合评估入场成本和时间，给出适合什么体量卖家进入的建议）

---

## 九、综合决策建议

**SWOT 速览：**
| | 机会(O) | 威胁(T) |
|--|---------|---------|
| 优势(S) | | |
| 劣势(W) | | |

**利润空间估算：**
- 预估采购成本：$xx–$xx（参考头部均价）
- 头程运费：$xx/件
- FBA费用：$xx/件
- 广告占比：xx%（新品期）
- **预估净利率：xx%–xx%**

**可操盘行动清单：**
1. 【产品】[具体产品差异化方向]
2. 【价格】[具体定价策略]
3. 【关键词】[具体词布局策略]
4. 【时机】[建议几月开始准备，几月上架]
5. 【资金】[建议首期投入规模和节奏]"""


_MCP_ASIN_NATIVE_PROMPT = """你是亚马逊跨境电商市场分析专家。

## 第一阶段：数据采集（必须全部完成，不得跳过任何一步）

**重要：在调用完下列全部8个工具之前，禁止输出任何报告内容。先把数据收齐，再写报告。**

你的工具列表中有 `mcp_sorftime_*` 系列工具，请**严格按顺序**依次调用：

**步骤 1** — 调用 `mcp_sorftime_product_detail`
  参数：asin="{query}", amz_site="{marketplace}"
  目的：获取产品基础画像、月销量、价格、评分、BSR等核心数据

**步骤 2** — 调用 `mcp_sorftime_product_trend`
  参数：asin="{query}", amz_site="{marketplace}"
  目的：获取最近12个月的销量和价格趋势

**步骤 3** — 调用 `mcp_sorftime_product_traffic_terms`
  参数：asin="{query}", amz_site="{marketplace}"
  目的：获取该产品的主要流量词列表；**记录搜索量最大的关键词，步骤6和7需要用到**

**步骤 4** — 调用 `mcp_sorftime_product_reviews`
  参数：asin="{query}", amz_site="{marketplace}"
  目的：获取用户评价摘要、好评/差评分布、高频问题

**步骤 5** — 调用 `mcp_sorftime_product_variations`
  参数：asin="{query}", amz_site="{marketplace}"
  目的：获取全部变体（颜色、尺寸、规格）及各变体销量占比

**步骤 6** — 调用 `mcp_sorftime_keyword_detail`
  参数：keyword=<步骤3记录的最大流量词>, keyword_support_site="{marketplace}"
  目的：获取主流量词的月搜索量、CPC、竞争强度

**步骤 7** — 调用 `mcp_sorftime_keyword_search_results`
  参数：keyword=<步骤3记录的最大流量词>, keyword_support_site="{marketplace}"
  目的：获取主流量词的首页竞品格局，用于竞品对比表

**步骤 8** — 调用 `mcp_sorftime_competitor_product_keywords`
  参数：asin="{query}", keyword_support_site="{marketplace}"
  目的：获取竞品词机会列表（本品流量盲区）

---

## 第二阶段：生成报告

**以上8个工具全部调用完毕后**，根据收集到的真实数据，填写以下报告模板。

**硬性要求（必须遵守）：**

【数据真实性——最高优先级】
1. **所有数字必须来自工具实际返回的数据**，包括 ASIN、品牌、月销量、评分、评论数、价格等，禁止自行虚构或凭印象填写
2. **工具未返回的字段一律标注"N/A"**，不得用推测值或"大约"替代
3. **正文分析中的每个结论必须有数据支撑**，禁止无依据的主观推测；数据不足时必须注明"数据有限，仅供参考"
4. **竞品 ASIN 必须是工具实际返回的真实 ASIN**，禁止自行编造 ASIN 编号
5. **数据来源存疑时必须标注口径**（如"基于流量词样本估算"）

【格式要求】
6. 所有量化数据必须用Markdown表格呈现
7. 月度趋势表必须包含最近12个月数据（每月一行）
8. 竞品对比表至少列出5个竞品
9. 流量词表至少15行
10. 差评改进点必须具体到产品层面，不能泛泛而谈
11. 每章节正文分析不少于80字

---

# 「{query}」竞品市场调研报告（亚马逊 {marketplace} 站）

> **执行摘要**：
> ① **产品当前表现**：（月销量 + 价格带 + BSR，一句话）
> ② **竞争定位**：（本品在市场中的位置，优势和短板，一句话）
> ③ **核心产品痛点**：（用户差评中最高频的2–3个具体问题）
> ④ **最大洞察**：（市场正在向哪个方向迁移，或本品流量的关键风险，一句话）
> ⑤ **后来者切入建议**：（具体到价格带 + 改进方向 + 时机，一句话）
>
> **产品综合评分**：x/10 ｜ **跟进该市场建议**：强烈推荐 / 有机会 / 谨慎 / 不推荐

---

## 一、产品基础画像

| 属性 | 数值 |
|------|------|
| 产品名称 | |
| 品牌 | |
| ASIN | {query} |
| 当前售价 | $ |
| 历史价格区间 | $–$ |
| 综合评分 | / 5.0 |
| 总评论数 | |
| 月销量（估算） | 件/月 |
| 月销售额（估算） | $/月 |
| 上架时间 | |
| 当前 BSR 排名 | |
| 品类节点 | |
| FBA / FBM | |
| 是否品牌注册 | 是 / 否 |

（正文分析：综合评价该产品的市场表现，说明其在同品类中的竞争位置）

---

## 二、月度销量 & 价格趋势（最近12个月）

| 月份 | 月销量 | 环比 | 售价 | BSR | 趋势信号 |
|------|--------|------|------|-----|---------|
| （最新月） | | % | $ | | |
| （次新月） | | % | $ | | |
（共12行，按时间倒序填写）

**产品生命周期阶段**：导入期 / 成长期 / 成熟期 / 衰退期
（正文分析：分析销量变化趋势、价格策略演变，判断产品处于哪个生命周期阶段及其影响）

---

## 三、流量词结构分析

| 关键词 | 流量类型 | 月搜索量 | 自然排名 | 流量占比估算 | 竞争指数 | 价值评级 |
|--------|---------|---------|---------|------------|---------|---------|
| | 自然/广告/两者 | | 第X页第X位 | % | /10 | 高/中/低 |
（≥15行，按流量占比从高到低排列；"流量类型"填自然/广告/两者三选一；"自然排名"具体到"第X页第X位"或"第X位"）

**流量健康度评估：**
| 指标 | 数值 | 评价 |
|------|------|------|
| TOP3词流量集中度 | % | 风险高/中/低 |
| 品牌词占比 | % | |
| 长尾词覆盖数 | 个 | |
| 广告依赖度 | % | |

（正文分析：分析该产品的流量结构健康度，指出依赖单一词的风险或多词覆盖的优势）

---

## 四、变体策略分析

| 变体类型 | 规格/颜色 | 售价 | 评论数 | 销量占比估算 | 库存状态 |
|---------|---------|------|--------|------------|---------|
| | | $ | | % | 充足/偏少/缺货 |
（列出全部变体，无变体则标注"单一SKU"）

**变体策略洞察**：[哪个变体销量最好？颜色/尺寸偏好是什么？有哪些空白变体可以切入？]

---

## 五、用户评价深度拆解

**评分分布：**
| 星级 | 占比 | 主要评论主题 |
|------|------|------------|
| ★★★★★ (5星) | % | |
| ★★★★☆ (4星) | % | |
| ★★★☆☆ (3星) | % | |
| ★★☆☆☆ (2星) | % | |
| ★☆☆☆☆ (1星) | % | |

**高频好评点（前5）：**
| 好评维度 | 提及频率 | 具体描述 |
|---------|---------|---------|
| | | |

**高频差评点（改进机会）：**
| 差评维度 | 提及频率 | 具体问题 | 改进建议 | 改进优先级 |
|---------|---------|---------|---------|----------|
| | | | | 必改/建议/可选 |

（正文分析：从差评提炼产品改进机会，说明如何通过差异化设计规避这些问题）

---

## 六、竞争格局 & 价格带分布

**市场整体情况：**
| 指标 | 数值 |
|------|------|
| 主关键词月搜索量 | |
| 首页竞品总数 | |
| 本品估算市场份额 | % |
| 品类月总销售额估算 | $ |
| 市场价格区间 | $–$ |
| 最优价格带 | $–$ |
| 头部垄断程度 | 高/中/低 |
| TOP3市场份额合计 | % |

**主要竞品对比：**
| 竞品 | ASIN | 价格 | 月销量 | 评分 | 评论数 | 上架时长 | 核心差异 |
|-----|------|------|--------|------|--------|---------|---------|
| 本品 | {query} | $ | | | | | — |
| 竞品1 | | $ | | | | | |
| 竞品2 | | $ | | | | | |
| 竞品3 | | $ | | | | | |
| 竞品4 | | $ | | | | | |
| 竞品5 | | $ | | | | | |

**各价格区间销量分布：**
（根据 `keyword_search_results` 实际价格分布划分区间；**请按品类真实价格段自行调整边界**，确保每个区间有产品且合计覆盖 90%+ 产品）

| 价格区间 | 产品数 | 月均销量 | 月销售额估算 | 销量占比 | 竞争强度 |
|---------|--------|---------|------------|---------|--------|
| （低价区） | | | $ | % | |
| （中价区） | | | $ | % | |
| （高价区） | | | $ | % | |
| **汇总** | | | $ | 100% | |

**各价格区间头部卖家 × 评论分析：**

| 价格区间 | 代表 ASIN | 月销量 | 评分 | 评论数 | 核心卖点（高频好评） | 主要差评痛点 | 可改进空间 |
|---------|---------|--------|------|--------|-----------------|------------|---------|
| （低价区） | | | /5.0 | | | | |
| （中价区） | | | /5.0 | | | | |
| （高价区） | | | /5.0 | | | | |

（每个价格区间列月销量最高的代表产品；"可改进空间"填写针对该区间差评可做的具体产品改进；本品所在价格区间单独标注）

**各价格区间切入机会评估：**

| 价格区间 | 切入难度 | 机会点 | 切入条件 | 建议 |
|---------|---------|-------|---------|-----|
| （低价区） | 高/中/低 | | | 推荐/谨慎/不推荐 |
| （中价区） | | | | |
| （高价区） | | | | |

**最优切入价格带**：$xx–$xx（理由：xxx）

（正文分析：分析本品所在价格区间的竞争态势，指出本品与竞品的核心差距；后来者应进入哪个价格区间，差异化改进点是什么，毛利空间能否支撑广告投入）

---

## 七、竞品词机会（本品流量盲区）

| 机会关键词 | 月搜索量 | CPC | 竞品排名情况 | 本品现状 | 获取难度 | 优先级 |
|---------|---------|-----|-----------|---------|---------|-------|
（≥10行，优先列高搜索量、低竞争的词）

（正文分析：给出具体的词布局攻坚建议，哪些词通过优化Listing可以自然获取，哪些需要广告投入）

---

## 八、综合评估与差异化操盘建议

**产品优劣势评分卡：**
| 维度 | 得分(/10) | 说明 |
|------|----------|------|
| 销量表现 | | |
| 流量结构 | | |
| 评价质量 | | |
| 价格竞争力 | | |
| 差异化空间 | | |
| 市场时机 | | |
| **综合评分** | | |

**产品升级路径速查（基于差评痛点）：**

| 层级 | 当前本品问题 | 最低可行改进 | 高阶差异化方向 |
|------|-----------|-----------|-------------|
| 功能层 | | | |
| 结构/材质层 | | | |
| 使用体验层 | | | |
| 配件/附件层 | | | |
| 包装/说明层 | | | |

**差异化切入4步策略：**
1. **产品层**：[基于差评改进的具体产品差异化方向，要具体到材质/功能/包装]
2. **价格层**：[定价策略，建议比该品低x%/高x%，原因是...]
3. **流量层**：[Listing关键词布局，哪些词主攻自然，哪些词用广告补充]
4. **时机层**：[建议几月开始备货，几月上架，理由是季节性/竞争周期]"""


def _build_mcp_native_prompt(mode: str, query: str, marketplace: str) -> str:
    """Build a tool-calling prompt for the agent to collect and synthesise itself."""
    template = _MCP_KEYWORD_NATIVE_PROMPT if mode == "keyword" else _MCP_ASIN_NATIVE_PROMPT
    return template.format(query=query, marketplace=marketplace)


# ─── Fallback prompt builder (codex / claude — no MCP tools) ─────────────────
# Derives the report structure from the MCP-native prompts so there is
# only ONE template to maintain. Strips the tool-calling phase and appends
# the pre-fetched sorftime data instead.


def _build_prompt(mode: str, query: str, marketplace: str, data: Dict[str, Any],
                  source: str = "Sorftime") -> str:
    """Build fallback prompt (codex / claude path) from the MCP-native template.

    Strips the tool-calling phase from the native prompt and appends the
    pre-fetched data. `source` is the human name of the data source (Sorftime /
    卖家精灵 / …) so the report's 数据声明 doesn't always say "Sorftime".
    """
    from app.services.sorftime_service import summarize_for_prompt
    # Budget per source rather than cutting the tail: a raw pipeline dump is
    # ~148KB, and a blind cut drops whole sources (the category report and
    # potential products sit last) so those chapters get written blind.
    data_summary = summarize_for_prompt(data)

    native = _MCP_KEYWORD_NATIVE_PROMPT if mode == "keyword" else _MCP_ASIN_NATIVE_PROMPT
    native_filled = native.format(query=query, marketplace=marketplace)

    # Strip the data-collection phase; keep only the report template section.
    phase2_marker = "## 第二阶段：生成报告"
    idx = native_filled.find(phase2_marker)
    if idx != -1:
        # Skip the "以上X个工具全部调用完毕后" instruction line
        after_header = native_filled.find("\n\n", idx + len(phase2_marker))
        report_body = native_filled[after_header:].strip()
        # Replace MCP-tool-specific preamble with data-dump preamble
        report_body = report_body.replace(
            "以上10个工具全部调用完毕后**，根据收集到的真实数据，填写以下报告模板。",
            f"请根据下方{source}原始数据，生成完整的市场调研报告。",
        ).replace(
            "以上8个工具全部调用完毕后**，根据收集到的真实数据，填写以下报告模板。",
            f"请根据下方{source}原始数据，生成完整的市场调研报告。",
        )
    else:
        report_body = native_filled

    role = (
        "你是亚马逊跨境电商市场分析专家，擅长从原始数据中提炼可操盘的市场洞察。"
        if mode == "keyword"
        else "你是亚马逊跨境电商市场分析专家，擅长从竞品数据中提炼选品策略和差异化机会。"
    )
    return f"{role}\n\n{report_body}\n\n---\n原始数据（来自{source}）：\n{data_summary}"


async def _stream_apimart(prompt: str) -> AsyncGenerator[str, None]:
    """Stream text tokens from apimart (Claude HTTP streaming)."""
    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=30)) as client:
        async with client.stream(
            "POST",
            f"{_apimart_base()}/messages",
            json=payload,
            headers={
                "Authorization": f"Bearer {_apimart_key()}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    event = json.loads(raw)
                except Exception:
                    continue
                evt_type = event.get("type", "")
                if evt_type == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield text
                elif evt_type == "error":
                    raise RuntimeError(f"apimart error: {event.get('error', event)}")


async def generate_text(prompt: str, skip_agent: bool = False,
                        inject_retrieval: bool = True) -> str:
    """Plain text-only LLM generation — NO tools, NO Sorftime MCP.

    For tasks that just need the model to write text (e.g. authoring a
    SKILL.md from a description). Tries IvyeaAgent / configured HTTP models first,
    then legacy fallbacks. This is
    deliberately separate from ``synthesize_native``, which injects sorftime
    tool-calling templates and would (wrongly) try to fetch market data.
    """
    failures: list[str] = []

    if not skip_agent and "ivyea-agent" in _text_provider_chain():
        try:
            parts: list[str] = []
            # inject_retrieval=False 给的是**要被机器读的输出**（标题、JSON）：
            # 开着检索注入时 agent 会在正文后面缀上 [K1] 之类的引用标记，
            # 一条 14 字的标题能被它顶掉三分之一（实测起出来的名字末尾挂着 "[K2"）。
            async for chunk in _stream_ivyea_agent(prompt, inject_retrieval=inject_retrieval):
                parts.append(chunk)
            text = "".join(parts).strip()
            if text:
                return text
            failures.append("IvyeaAgent 返回空")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"IvyeaAgent: {exc}")

    if "assistant" in _text_provider_chain() and assistant_text_cfg().get("api_key"):
        try:
            parts = []
            async for chunk in stream_assistant_prompt(prompt):
                parts.append(chunk)
            text = "".join(parts).strip()
            if text:
                return text
            failures.append("全局兜底大模型返回空")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"全局兜底大模型: {exc}")

    dkey = _deepseek_key()
    if dkey:
        try:
            parts: list[str] = []
            async for chunk in _stream_openai_compat(
                dkey, "https://api.deepseek.com", "deepseek-chat", prompt
            ):
                parts.append(chunk)
            text = "".join(parts).strip()
            if text:
                return text
            failures.append("DeepSeek 返回空")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"DeepSeek: {exc}")

    if _apimart_key():
        try:
            parts = []
            async for chunk in _stream_apimart(prompt):
                parts.append(chunk)
            text = "".join(parts).strip()
            if text:
                return text
            failures.append("Apimart 返回空")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"Apimart: {exc}")

    # Global fallback model (assistant slot) — last-resort HTTP provider so any
    # text task degrades gracefully even when deepseek/apimart are unavailable.
    if assistant_text_cfg().get("api_key"):
        try:
            parts = []
            async for chunk in stream_assistant_prompt(prompt):
                parts.append(chunk)
            text = "".join(parts).strip()
            if text:
                return text
            failures.append("全局兜底大模型返回空")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"全局兜底大模型: {exc}")

    raise RuntimeError(
        "无可用文本模型。" + (" / ".join(failures) if failures else
        "请在「系统配置」配置 IvyeaAgent / 全局兜底大模型 / DeepSeek。")
    )


async def generate_text_provider(provider: str, prompt: str) -> str:
    """Generate text via a SPECIFIC safe provider.

    Raises if that provider isn't configured/usable — caller can fall back. Used
    for heterogeneous multi-model review (different personas → different models).
    """
    provider = (provider or "").lower().strip()
    if provider == "ivyea-agent":
        parts: list[str] = []
        async for chunk in _stream_ivyea_agent(prompt):
            parts.append(chunk)
        text = "".join(parts).strip()
        if not text:
            raise RuntimeError("IvyeaAgent 返回空")
        return text
    if provider == "assistant":
        if not assistant_text_cfg().get("api_key"):
            raise RuntimeError("全局兜底大模型未配置")
        parts = []
        async for chunk in stream_assistant_prompt(prompt):
            parts.append(chunk)
        text = "".join(parts).strip()
        if not text:
            raise RuntimeError("全局兜底大模型返回空")
        return text
    if provider == "deepseek":
        dkey = _deepseek_key()
        if not dkey:
            raise RuntimeError("DeepSeek 未配置")
        parts: list[str] = []
        async for chunk in _stream_openai_compat(dkey, "https://api.deepseek.com", "deepseek-chat", prompt):
            parts.append(chunk)
        text = "".join(parts).strip()
        if not text:
            raise RuntimeError("DeepSeek 返回空")
        return text
    if provider == "apimart":
        if not _apimart_key():
            raise RuntimeError("Apimart 未配置")
        parts = []
        async for chunk in _stream_apimart(prompt):
            parts.append(chunk)
        text = "".join(parts).strip()
        if not text:
            raise RuntimeError("Apimart 返回空")
        return text
    raise RuntimeError(f"未知 provider: {provider}")


async def generate_openai_compat(base_url: str, api_key: str, model: str, prompt: str) -> str:
    """Generate text via any OpenAI-compatible endpoint (custom model slots)."""
    if not (base_url and model):
        raise RuntimeError("自定义模型缺少 base_url/model")
    parts: list[str] = []
    async for chunk in _stream_openai_compat(api_key or "", base_url.rstrip("/"), model, prompt):
        parts.append(chunk)
    text = "".join(parts).strip()
    if not text:
        raise RuntimeError("自定义模型返回空")
    return text


# ---------------------------------------------------------------------------
# Global fallback text model ("assistant" slot) — also powers the AI 问答 panel.
# This is the canonical home; app.routers.assistant imports from here (DRY).
# ---------------------------------------------------------------------------

# OpenAI-compatible base URLs per provider name, for when base_url is left blank.
ASSISTANT_PROVIDER_BASE = {
    "deepseek":   "https://api.deepseek.com",
    "openai":     "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
    "dashscope":  "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "zhipu":      "https://open.bigmodel.cn/api/paas/v4",
    # GLM Coding Plan（订阅）走的是**另一个地址**：官方明确要求 coding 专用端点
    # /api/coding/paas/v4，填成上面那个通用端点不通，而报错完全指不到"地址错了"上。
    "zai-coding":  "https://api.z.ai/api/coding/paas/v4",
    "glm-coding":  "https://open.bigmodel.cn/api/coding/paas/v4",
    # Kimi Code 订阅（授权登录后用）。
    "kimi-code":   "https://api.kimi.com/coding/v1",
    "groq":       "https://api.groq.com/openai/v1",
    "together":   "https://api.together.xyz/v1",
    "xiaomi":     "https://token-plan-sgp.xiaomimimo.com/v1",
    "kimi":       "https://api.kimi.com/coding/v1",
}


def has_text_provider() -> bool:
    """True when at least one text provider (IvyeaAgent / DeepSeek / Apimart / global
    fallback model) is configured — i.e. ``generate_text`` can answer even when
    no local agent CLI (IvyeaAgent/Codex/Claude) is installed."""
    try:
        from app.services import ivyea_agent_service as ivyea
        return bool(
            ivyea.availability().get("available")
            or _deepseek_key()
            or _apimart_key()
            or assistant_text_cfg().get("api_key")
        )
    except Exception:  # noqa: BLE001
        return False


def assistant_text_cfg() -> dict:
    """The global fallback text model (== AI 问答 assistant slot), or {} if the
    user hasn't configured one. Keys: provider / model / api_key / base_url."""
    from app.core import hub_settings
    provider = str(hub_settings.get("assistant_provider") or "").strip()
    if not provider:
        return {}
    return {
        "provider": provider,
        "model":    str(hub_settings.get("assistant_model") or "").strip(),
        "api_key":  str(hub_settings.get("assistant_api_key") or "").strip(),
        "base_url": str(hub_settings.get("assistant_base_url") or "").strip(),
    }


async def stream_assistant_prompt(prompt: str) -> AsyncGenerator[str, None]:
    """Stream a single-prompt completion from the global fallback slot.

    Handles anthropic-native and OpenAI-compatible providers, mirroring the
    AI 问答 panel's behaviour so the two never diverge. Raises if unconfigured.
    """
    cfg = assistant_text_cfg()
    if not cfg:
        raise RuntimeError("全局兜底大模型未配置（系统配置 → 全局兜底大模型）")
    provider = cfg["provider"]
    key = cfg["api_key"]
    if not key:
        raise RuntimeError(f"{provider} key 未配置")

    if provider == "anthropic":
        base = (cfg["base_url"] or "https://api.anthropic.com/v1").rstrip("/")
        payload = {
            "model": cfg["model"] or "claude-sonnet-4-6",
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30)) as c:
            async with c.stream(
                "POST", f"{base}/messages", json=payload,
                headers={"Authorization": f"Bearer {key}", "anthropic-version": "2023-06-01"},
            ) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except Exception:
                        continue
                    if ev.get("type") == "content_block_delta":
                        t = ev.get("delta", {}).get("text", "")
                        if t:
                            yield t
        return

    base = cfg["base_url"] or ASSISTANT_PROVIDER_BASE.get(provider, "")
    if not base:
        raise RuntimeError(f"{provider} 需要填写 Base URL")
    if not cfg["model"]:
        raise RuntimeError(f"{provider} 需要填写模型名")
    async for chunk in _stream_openai_compat(key, base.rstrip("/"), cfg["model"], prompt):
        yield chunk


async def _try_assistant(prompt: str, failures: list[str]) -> AsyncGenerator[tuple[str, str], None]:
    """Chain step for the global fallback model — same contract as _try_deepseek."""
    yield "_attempt", "assistant"
    if not assistant_text_cfg().get("api_key"):
        failures.append(
            "全局兜底大模型未配置 — 在「系统配置 → 全局兜底大模型」填写 provider / model / key"
        )
        return
    try:
        got = False
        async for chunk in stream_assistant_prompt(prompt):
            got = True
            yield "assistant", chunk
        if not got:
            failures.append("全局兜底大模型返回空")
        return
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code if exc.response is not None else "?"
        body = ""
        try:
            body = exc.response.text[:200] if exc.response is not None else ""
        except Exception:
            logger.debug("body = exc.response.text 失败（旁路，已忽略）", exc_info=True)
        failures.append(f"全局兜底模型 HTTP {code}：{body or '请求失败'}")
        _log.warning("assistant fallback failed: HTTP %s — %s", code, body)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"全局兜底模型调用失败：{exc}")
        _log.warning("assistant fallback failed: %s", exc)


async def _stream_ivyea_agent(
    prompt: str,
    *,
    inject_retrieval: bool = True,
    use_tools: bool = False,
    plan_mode: bool = True,
    max_steps: int = 3,
    system: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream text from the embedded IvyeaAgent service.

    ``inject_retrieval=False`` is for structured-output tasks (strict JSON):
    retrieval injection makes the agent append 引用说明/[K1] citation markers
    that corrupt machine-parsed responses.

    ``use_tools=False`` (the default) runs the turn with no tool schemas at all.
    Every caller here hands the agent the material it needs and wants prose or
    JSON back — with tools attached the agent instead spends its steps hunting
    for data (and in plan_mode it is refused anyway), and that narration streams
    straight into the panel as if it were the report.

    ``plan_mode=False`` + ``use_tools=True`` + a raised ``max_steps`` is the
    MCP-native mode (市场调研 / 打法 的原生取数路径): plan mode refuses
    ``mcp_call_tool``, so leaving it on means the agent can never reach sorftime
    and silently writes from nothing. Same shape as asin_audit._run_ivyea_agent.
    """
    from app.services import ivyea_agent_service as ivyea

    status = await asyncio.to_thread(ivyea.ensure_available)
    if not status.get("available"):
        raise RuntimeError(status.get("error") or "IvyeaAgent 服务未连接")
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    token = ivyea._token()  # same bridge auth path; secret never leaves backend
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {
        "message": prompt,
        "max_steps": max_steps,
        "use_tools": use_tools,
        "plan_mode": plan_mode,
        # persist=False — this is IvyeaOps' internal text engine (report/analysis/
        # ingest cleaning), not a user chat. Persisting would spam the shared
        # agent session history that the dock and workbench chat now both read.
        "persist": False,
        "inject_retrieval": inject_retrieval,
        # We consume the token stream and ignore the agent's `final` event, so a
        # superseded draft can never be taken back. The citation gate makes the
        # model rewrite the whole answer with [K#] markers — without this we
        # streamed both copies and the panel showed the report twice.
        "defer_citation_text": True,
        "system": system or (
            "你正在作为 IvyeaOps 的内置文本生成引擎。"
            "直接基于输入数据生成最终成品，不要输出查数据/找工具一类的过程叙述。"
        ),
    }
    got_token = False
    final_text = ""
    event = "message"
    # 纯文本生成 300s 够用；带工具的原生取数要先跑一串 MCP 调用再写报告，
    # 沿用 hermes 时代给这条路径的 600s（当时就是被 MCP 往返拖长才加的）。
    read_timeout = 600 if use_tools else 300
    async with httpx.AsyncClient(timeout=httpx.Timeout(read_timeout, connect=30)) as client:
        async with client.stream("POST", f"{ivyea.base_url()}/v1/chat/stream", json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    event = "message"
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip() or "message"
                    continue
                if not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[5:].strip() or "{}")
                except Exception:
                    continue
                if event == "token":
                    text = str(data.get("text") or "")
                    if text:
                        got_token = True
                        yield text
                elif event == "final":
                    final_text = str(data.get("text") or "")
                elif event == "error":
                    raise RuntimeError(str(data.get("detail") or data.get("error") or data))
    if not got_token and final_text:
        yield final_text


_NATIVE_SYSTEM = (
    "你正在作为 IvyeaOps 的{role}。"
    "先用 mcp_list_tools 发现已配置的数据源工具，再用 mcp_call_tool 抓真实数据，"
    "然后基于真实数据写{deliv}。抓不到数据时必须直说“未取到数据源数据”，不要编造数字。"
    "把【完整{deliv}正文】作为你最后一条消息一次性完整输出——不要在正文之后再追加"
    "“已输出完毕/已交付”之类的收尾轮次，也不要说“{deliv}在上方”。"
)


async def run_ivyea_native(prompt: str, system: str, *, max_steps: int = 40) -> str:
    """MCP-native turn: let the agent fetch its own data, return the FULL report.

    为什么不复用 _stream_ivyea_agent 的 token 流：agent 是多步循环，token 流只
    是**最后一轮**的内容，而正文往往产在更早一轮，最后一轮只剩一句「报告已输出
    完毕」的收尾（实测 823s 只流回 159 字）。所以这里照搬 asin_audit 验证过的
    做法——跑完后从落盘的完整会话里捞出真正的报告：
      1) 完整持久化会话（未截断，正文可能在几十条之前）
      2) final 事件带回的 messages（末 30 条）
      3) final.text / token 流兜底
    也刻意不边流边发：流出去的是收尾句，末尾再补全文会让面板出现两份内容
    （这个双份渲染以前就踩过）。
    """
    from app.services import ivyea_agent_service as ivyea

    status = await asyncio.to_thread(ivyea.ensure_available)
    if not status.get("available"):
        raise RuntimeError(status.get("error") or "IvyeaAgent 服务未连接")
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    token = ivyea._token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {
        "message": prompt,
        "max_steps": max_steps,
        "use_tools": True,
        "plan_mode": False,      # 计划模式会拒绝 mcp_call_tool，取不到真实数据
        "persist": True,         # 必须落盘，否则下面捞不回正文
        "inject_retrieval": False,
        "system": system,
    }
    streamed: list[str] = []
    final_text = ""
    final_messages: list[dict] = []
    session_id = ""
    event = "message"
    # 取数轮次多、单步可能几十秒，给足读超时（实测一次完整 10 步调研约 14 分钟）。
    async with httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=30)) as client:
        async with client.stream("POST", f"{ivyea.base_url()}/v1/chat/stream",
                                 json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    event = "message"
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip() or "message"
                    continue
                if not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[5:].strip() or "{}")
                except Exception:
                    continue
                if not session_id and data.get("session_id"):
                    session_id = str(data["session_id"])
                if event == "token":
                    streamed.append(str(data.get("text") or ""))
                elif event == "final":
                    final_text = str(data.get("text") or "")
                    if isinstance(data.get("messages"), list):
                        final_messages = data["messages"]
                elif event == "error":
                    raise RuntimeError(str(data.get("detail") or data.get("error") or data))

    from app.services.asin_audit import _best_report_from_messages, _report_from_session
    candidates = [
        _report_from_session(session_id) if session_id else "",
        _best_report_from_messages(final_messages),
        final_text,
        "".join(streamed),
    ]
    # 取最长的那份：正文一定比「已输出完毕」的收尾句长得多。
    return max((c or "" for c in candidates), key=len).strip()


async def _try_ivyea_agent(
    prompt: str, failures: list[str], *, inject_retrieval: bool = True
) -> AsyncGenerator[tuple[str, str], None]:
    yield "_attempt", "ivyea-agent"
    try:
        got = False
        async for chunk in _stream_ivyea_agent(prompt, inject_retrieval=inject_retrieval):
            got = True
            yield "ivyea-agent", chunk
        if not got:
            failures.append("IvyeaAgent 返回空")
        return
    except Exception as exc:  # noqa: BLE001
        failures.append(f"IvyeaAgent 调用失败：{exc}")
        _log.warning("ivyea-agent failed: %s", exc)


# ---------------------------------------------------------------------------
# Vision provider helpers
# ---------------------------------------------------------------------------

def _openai_key() -> str:
    from app.core import hub_settings
    val = hub_settings.get("openai_api_key")
    return str(val) if val else ""


_VISION_CAPABLE_PROVIDERS = ("openai", "anthropic", "openrouter", "google", "together",
                             "custom", "apimart", "deepseek",
                             "siliconflow", "dashscope", "zhipu")


def _assistant_vision_cfg() -> tuple[str, str, str, str] | None:
    """Return (provider, key, base_url, model) for the configured vision
    reviewer, or None.

    独立视觉复核槽（vision_provider/vision_api_key/vision_base_url/vision_model）
    优先——它与全局兜底彻底解耦，支持"文本兜底用 A 家、看图用 B 家"。
    未配置时回退旧行为：全局兜底槽 + assistant_vision_model（或 assistant_model），
    这样 2026-07 之前按 CONFIG.md 配置的用户无需迁移。
    Base URL 为空时按 provider 预设解析（openrouter 等网关需要完整模型 slug，
    不能再写死 gpt-4o）。"""
    from app.core import hub_settings

    def _resolve(provider: str, key: str, base: str, model: str):
        provider = provider.lower().strip()
        if not key or provider not in _VISION_CAPABLE_PROVIDERS:
            return None
        base = base.strip() or ASSISTANT_PROVIDER_BASE.get(provider, "")
        return provider, key, base, model.strip()

    independent = _resolve(
        str(hub_settings.get("vision_provider") or ""),
        str(hub_settings.get("vision_api_key") or ""),
        str(hub_settings.get("vision_base_url") or ""),
        str(hub_settings.get("vision_model") or ""),
    )
    if independent:
        return independent
    return _resolve(
        str(hub_settings.get("assistant_provider") or ""),
        str(hub_settings.get("assistant_api_key") or ""),
        str(hub_settings.get("assistant_base_url") or ""),
        str(hub_settings.get("assistant_vision_model")
            or hub_settings.get("assistant_model") or ""),
    )


_VISION_CHAIN_CACHE: tuple[float, dict] = (0.0, {})
# 缓存窗口。取 5 秒是因为它只需要覆盖**一次请求内部的扇出**：
# _vision_provider_chain / vision_tier / vision_tier_label 会连环调这个函数，
# 不缓存的话一次设置页刷新就是五六个 HTTP 往返（每个还带 2 秒超时）。
# 窗口再长就会让"刚配好视觉模型"的用户刷新后仍看到旧档位。
_VISION_CHAIN_TTL = 5.0


def _agent_vision_chain(*, fresh: bool = False) -> dict:
    """agent 自报的视觉三档链状态（/health 的 vision_chain）。

    老版本 serve 没有这个字段，回 {} —— 调用方要按"老行为"处理，
    不能把缺字段当成"没有视觉"。
    """
    global _VISION_CHAIN_CACHE
    now = time.monotonic()
    if not fresh:
        stamp, cached = _VISION_CHAIN_CACHE
        if now - stamp < _VISION_CHAIN_TTL:
            return cached
    try:
        from app.services import ivyea_agent_service as ivyea
        avail = ivyea.availability()
        chain = (avail.get("health") or {}).get("vision_chain") if avail.get("available") else None
        result = chain if isinstance(chain, dict) else {}
    except Exception:
        result = {}
    _VISION_CHAIN_CACHE = (now, result)
    return result


def _ivyea_agent_vision_available() -> bool:
    """ivyea-agent 能不能接带图任务。

    **看整条链，不是看主脑。** agent v1.13 起内部有三档降级：
      T1 主脑自带视觉 / T2 第三方视觉模型旁路代读 / T3 本地 CV+OCR 量化。
    此前这里只读 `model.capabilities.vision`（主脑那一档），于是用户主脑一旦是
    DeepSeek 这类纯文本模型，整条视觉链就被判死，Listing 的图片分析静默空转。

    老版本 serve 没有 vision_chain → 退回旧判据（只有主脑有视觉才算数），
    行为与升级前完全一致，不会把老 agent 判成能力更强。
    """
    chain = _agent_vision_chain()
    if chain:
        return bool(chain.get("effective"))
    try:
        from app.services import ivyea_agent_service as ivyea
        avail = ivyea.availability()
        if not avail.get("available"):
            return False
        caps = (((avail.get("health") or {}).get("model") or {}).get("capabilities") or {})
        return bool(caps.get("vision"))
    except Exception:
        return False


def vision_tier() -> int:
    """当前实际生效的视觉档位：1 主脑直读 / 2 旁路 / 3 本地 CV / 0 无。

    Listing 要靠它决定哪些分析做得了、哪些必须明说"跳过"——CV 能量化的
    （合规/比例/占比/配色/文字）照做，语义类的（版式逆向、审美）在 T3 下
    做不了就不要产出假结果。

    只有 agent 那一档能自报档位；走 openai / assistant 直连视觉模型时，
    那本来就是真视觉模型，等价于 T1。
    """
    chain = _vision_provider_chain()
    if not chain:
        return 0
    if chain[0] == "ivyea-agent":
        tier = _agent_vision_chain().get("tier")
        if isinstance(tier, int) and tier > 0:
            return tier
        return 1        # 老 serve：能进链就说明主脑有视觉
    return 1


def vision_tier_label() -> str:
    labels = {1: "主脑直读", 2: "视觉旁路", 3: "本地 CV 度量", 0: "无视觉能力"}
    tier = vision_tier()
    base = labels.get(tier, "未知")
    if tier == 2:
        model = ((_agent_vision_chain().get("sidecar") or {}).get("model") or "").strip()
        return f"{base} · {model}" if model else base
    if tier == 3:
        ocr = ((_agent_vision_chain().get("local_cv") or {}).get("ocr_engine") or "").strip()
        return f"{base}（OCR：{ocr}）" if ocr else f"{base}（无 OCR）"
    return base


def _vision_provider_chain() -> list[str]:
    """Parse vision_ai_providers setting; filter to providers that actually
    have a key configured in the current environment."""
    from app.core import hub_settings
    raw = str(hub_settings.get("vision_ai_providers") or "").strip()
    # apimart is image-GEN only (no vision/analysis), so it is NOT a vision
    # provider — image understanding needs a real vision model (ivyea-agent /
    # openai / the vision-capable global fallback).
    valid = ("ivyea-agent", "openai", "assistant")
    order = [p.strip().lower() for p in (raw or "ivyea-agent,openai,assistant").split(",")
             if p.strip().lower() in valid]
    order = list(dict.fromkeys(order)) or ["ivyea-agent", "openai", "assistant"]
    # 用户配置里没提 ivyea-agent 的旧配置也自动获得 agent 视觉（排最前，与文本链
    # 一致：agent 是本产品的一等 provider）。
    if "ivyea-agent" not in order:
        order.insert(0, "ivyea-agent")

    # Only keep providers that have credentials configured right now.
    available = []
    for p in order:
        if p == "ivyea-agent" and _ivyea_agent_vision_available():
            available.append(p)
        elif p == "openai" and _openai_key():
            available.append(p)
        elif p == "assistant" and _assistant_vision_cfg():
            available.append(p)

    # agent 恒排第一是**文本链**的规矩，视觉链不能照抄：agent 只在 T3（本地 CV
    # 量化）时，它给的是读数而不是画面，而 openai/assistant 槽里坐着的是真视觉
    # 模型。让 T3 顶掉真视觉模型是纯粹的质量倒退，所以这里把"只有 T3 的 agent"
    # 降到真视觉 provider 之后——它仍在链上，作为真视觉模型全挂时的兜底。
    if len(available) > 1 and available[0] == "ivyea-agent":
        if _agent_vision_chain().get("tier") == 3:
            available = available[1:] + ["ivyea-agent"]
    return available


def has_vision_capability() -> bool:
    """True if at least one vision provider is currently configured."""
    return bool(_vision_provider_chain())


async def _stream_apimart_vision(prompt: str, images_b64: list[str]) -> AsyncGenerator[str, None]:
    """Claude Vision via Apimart (Anthropic messages format)."""
    content: list[dict] = []
    for uri in images_b64[:4]:
        try:
            header, data = uri.split(",", 1)
            media_type = header.split(":")[1].split(";")[0]
        except (ValueError, IndexError):
            continue
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
    content.append({"type": "text", "text": prompt})

    payload = {"model": "claude-sonnet-4-6", "max_tokens": 8192,
               "messages": [{"role": "user", "content": content}], "stream": True}
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=30)) as client:
        async with client.stream(
            "POST", f"{_apimart_base()}/messages", json=payload,
            headers={"Authorization": f"Bearer {_apimart_key()}",
                     "Content-Type": "application/json",
                     "anthropic-version": "2023-06-01"},
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                if ev.get("type") == "content_block_delta":
                    text = ev.get("delta", {}).get("text", "")
                    if text:
                        yield text
                elif ev.get("type") == "error":
                    raise RuntimeError(f"Apimart Vision 错误: {ev.get('error', ev)}")


async def _stream_openai_vision(
    prompt: str, images_b64: list[str],
    api_key: str, base_url: str = "https://api.openai.com",
    model: str = "gpt-4o",
) -> AsyncGenerator[str, None]:
    """GPT-4o / OpenAI-compatible vision (image_url content blocks)."""
    content: list[dict] = []
    for uri in images_b64[:4]:
        content.append({"type": "image_url", "image_url": {"url": uri, "detail": "high"}})
    content.append({"type": "text", "text": prompt})

    payload = {"model": model, "max_tokens": 8192,
               "messages": [{"role": "user", "content": content}], "stream": True}
    # 与文本链同一约定：assistant_base_url 可能已含 /v1（openrouter 等），别重复拼。
    base = base_url.rstrip("/")
    url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=30)) as client:
        async with client.stream(
            "POST", url, json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                text = (ev.get("choices") or [{}])[0].get("delta", {}).get("content", "")
                if text:
                    yield text


async def _stream_ivyea_agent_vision(prompt: str, images_b64: list[str],
                                     observed: dict | None = None) -> AsyncGenerator[str, None]:
    """ivyea-agent 作视觉 provider：serve /v1/chat/stream 带 images（v1.8.3+）。

    persist=False —— 成图复核/图片分析属于内部管线调用，不进 agent 会话历史。
    max_steps=1 —— 看图回答是单步任务，不需要 agent 工具循环。

    `observed` 传进来时，会把 agent 上报的**本次请求实际档位**（vision_tier 事件）
    写进去。用它而不是 /health 快照：旁路在请求中途失败会就地降到 T3，快照还停在
    T2，标签就会虚报成"真看见了"。
    """
    from app.services import ivyea_agent_service as ivyea

    status = await asyncio.to_thread(ivyea.ensure_available)
    if not status.get("available"):
        raise RuntimeError(status.get("error") or "IvyeaAgent 服务未连接")
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    token = ivyea._token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {
        "message": prompt,
        "images": [uri for uri in images_b64[:4] if str(uri).startswith("data:image/")],
        "max_steps": 1,
        "plan_mode": True,
        "persist": False,
        "inject_retrieval": False,
    }
    got_token = False
    final_text = ""
    event = "message"
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=15)) as client:
        async with client.stream("POST", f"{ivyea.base_url()}/v1/chat/stream", json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    event = "message"
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip() or "message"
                    continue
                if not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[5:].strip() or "{}")
                except Exception:
                    continue
                if event == "token":
                    text = str(data.get("text") or "")
                    if text:
                        got_token = True
                        yield text
                elif event == "vision_tier":
                    if observed is not None and isinstance(data, dict):
                        observed.update(data)
                elif event == "final":
                    final_text = str(data.get("text") or "")
                    if observed is not None and isinstance(data.get("vision_tier"), dict):
                        observed.update(data["vision_tier"])
                elif event == "error":
                    raise RuntimeError(str(data.get("detail") or data.get("error") or data))
    if not got_token and final_text:
        yield final_text


async def stream_vision(prompt: str, images_b64: list[str]) -> AsyncGenerator[tuple[str, str], None]:
    """Vision fallback chain: tries each configured vision provider in order.

    Each element of images_b64 should be a data-URI string like
    'data:image/jpeg;base64,...' as produced by FileReader.readAsDataURL().
    Yields (provider_name, text_chunk) tuples; on total failure yields ('error', detail).
    """
    chain = _vision_provider_chain()
    if not chain:
        # 走到这里说明连 agent 都不可达（agent 只要在线就至少有 T3 本地 CV）。
        # 所以文案不能再说"没配视觉模型就做不了"——没配视觉模型是能做的，
        # 前提是 agent 服务活着。
        yield "error", (
            "当前没有任何可用的视觉通道。\n"
            "  · 首选：确认 IvyeaAgent 服务在线——它自带三档降级，"
            "即使主脑不支持图片，也会用第三方视觉模型旁路或本地 CV+OCR 量化处理。\n"
            "  · 或在「系统配置 → AI 服务」配置 OpenAI API key（GPT-4o）"
            "或支持视觉的自定义 assistant provider。"
        )
        return

    failures: list[str] = []
    for provider in chain:
        try:
            got = False
            observed: dict = {}      # agent 上报的本次实际档位，其余 provider 留空
            if provider == "ivyea-agent":
                # 标签带上档位：上层（Listing）要按档位决定哪些分析做得了，
                # 前端也要显示"本次是哪一档的结果"。降级本身不是问题，
                # 降级了却不说才是。
                #
                # 先用 /health 快照兜个底，拿到 agent 上报的 vision_tier 事件后
                # 立刻换成**本次实测**的档位——旁路中途失败会就地降到 T3，
                # 只信快照就会把编不出来的那一档报成"真看见了"。
                snapshot = _agent_vision_chain().get("tier")
                label = f"ivyea-agent/t{snapshot}" if isinstance(snapshot, int) and snapshot else "ivyea-agent"
                gen = _stream_ivyea_agent_vision(prompt, images_b64, observed)
            elif provider == "apimart":
                label = "claude"
                gen = _stream_apimart_vision(prompt, images_b64)
            elif provider == "openai":
                label = "gpt-4o"
                gen = _stream_openai_vision(prompt, images_b64, _openai_key())
            else:  # assistant
                cfg = _assistant_vision_cfg()
                if not cfg:
                    continue
                _, key, base, model = cfg
                label = "assistant"
                # Anthropic-format providers go through Apimart-style; others use OpenAI compat.
                if "anthropic" in cfg[0] or "apimart" in cfg[0]:
                    gen = _stream_apimart_vision(prompt, images_b64)
                else:
                    gen = _stream_openai_vision(prompt, images_b64, key,
                                                base or "https://api.openai.com/v1",
                                                model=model or "gpt-4o")

            async for chunk in gen:
                got = True
                # vision_tier 事件在首个 token 之前到达，所以从第一段起标签就是真的。
                live = observed.get("tier")
                yield (f"ivyea-agent/t{live}" if isinstance(live, int) and live else label), chunk
            if got:
                return
            failures.append(f"{provider}: 返回空")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{provider}: {exc}")
            _log.warning("vision provider %s failed: %s", provider, exc)

    yield "error", (
        "所有视觉 AI 提供商均不可用：\n"
        + "\n".join(f"  · {f}" for f in failures)
        + "\n\n已尝试的提供商顺序：" + " → ".join(chain)
    )


async def stream_text(prompt: str) -> AsyncGenerator[tuple[str, str], None]:
    """Streaming counterpart of ``generate_text``: yields (provider, chunk).

    For llm-only skill execution where we want token-by-token UX but NO tools /
    MCP. Uses the configured safe text chain first. On total failure yields
    ('error', detail).
    """
    failures: list[str] = []
    tried: set[str] = set()

    async def _run_provider(provider: str) -> AsyncGenerator[tuple[str, str], None]:
        tried.add(provider)
        if provider == "ivyea-agent":
            gen = _try_ivyea_agent(prompt, failures)
        elif provider == "assistant":
            gen = _try_assistant(prompt, failures)
        elif provider == "deepseek":
            gen = _try_deepseek(prompt, failures)
        elif provider == "apimart":
            gen = _try_apimart(prompt, failures)
        else:
            return
        async for prov, chunk in gen:
            if prov == "_attempt":
                continue
            yield prov, chunk

    for provider in [p for p in _text_provider_chain() if p in ("ivyea-agent", "assistant", "deepseek", "apimart")]:
        if provider in tried:
            continue
        got = False
        async for prov, chunk in _run_provider(provider):
            got = True
            yield prov, chunk
        if got:
            return

    # Legacy fallback: keep Apimart reachable for older configs that still
    # expect it, even though it is no longer part of the default text chain.
    for provider in ("ivyea-agent", "assistant", "deepseek", "apimart"):
        if provider in tried:
            continue
        got = False
        async for prov, chunk in _run_provider(provider):
            got = True
            yield prov, chunk
        if got:
            return

    yield "error", (
        "无可用文本模型。"
        + (" / ".join(failures) if failures else "请在「系统配置」配置 IvyeaAgent / 全局兜底大模型 / DeepSeek。")
    )


async def _stream_openai_compat(
    api_key: str, base_url: str, model: str, prompt: str
) -> AsyncGenerator[str, None]:
    """Stream tokens via any OpenAI-compatible chat/completions endpoint."""
    payload = {
        "model": model,
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30)) as client:
        async with client.stream(
            "POST",
            f"{base_url}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    event = json.loads(raw)
                except Exception:
                    continue
                choices = event.get("choices", [])
                if not choices:
                    continue
                text = choices[0].get("delta", {}).get("content", "")
                if text:
                    yield text


async def _try_deepseek(prompt: str, failures: list[str]) -> AsyncGenerator[tuple[str, str], None]:
    """Stream market research synthesis via DeepSeek HTTP API (true streaming).

    DeepSeek uses the OpenAI-compatible streaming format, so each token
    arrives as a separate SSE event — no waiting for the full response.
    Falls back gracefully by pushing a diagnostic to ``failures``.
    """
    yield "_attempt", "deepseek"
    key = _deepseek_key()
    if not key:
        failures.append(
            "DeepSeek API key 未配置 — 在「系统配置」中添加 deepseek_api_key，"
            "或在 ~/.hermes/.env 中设置 DEEPSEEK_API_KEY=sk-..."
        )
        return
    try:
        async for chunk in _stream_openai_compat(
            key, "https://api.deepseek.com", "deepseek-chat", prompt
        ):
            yield "deepseek", chunk
        return
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code if exc.response is not None else "?"
        body = ""
        try:
            body = exc.response.text[:200] if exc.response is not None else ""
        except Exception:
            logger.debug("body = exc.response.text 失败（旁路，已忽略）", exc_info=True)
        failures.append(f"DeepSeek HTTP {code}：{body or '请求失败'}")
        _log.warning("deepseek failed: HTTP %s — %s", code, body)
    except Exception as exc:
        failures.append(f"DeepSeek 调用失败：{exc}")
        _log.warning("deepseek failed: %s", exc)


_HERMES_STREAM_WRAPPER = str(
    Path(__file__).parent / "hermes_stream_wrapper.py"
)


def _hermes_venv_python() -> "str | None":
    """Path to hermes-agent's venv Python (token-by-token streaming wrapper),
    platform-aware. Returns None if not present — caller then drives hermes via
    its plain CLI, which works on Windows and any non-venv install layout."""
    base = Path.home() / ".hermes" / "hermes-agent" / "venv"
    cand = base / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(cand) if cand.exists() else None


async def _stream_cli_runner(runner: str, prompt: str) -> AsyncGenerator[str, None]:
    """Stream stdout from a CLI runner as it produces output.

    For hermes: invokes hermes_stream_wrapper.py via the hermes venv Python,
    which calls AIAgent.chat() with a stream_callback — so each token arrives
    on stdout immediately instead of after the full response is assembled.

    For codex / claude: passes the prompt as a command-line argument (their
    own --print / exec modes already stream stdout progressively).

    Raises RuntimeError if the binary is missing, the 300 s deadline is
    exceeded, or the process exits without producing any output.
    """
    binary = _find_bin(runner)
    if not binary:
        raise RuntimeError(f"{runner} CLI 不可用")

    env = build_child_env(binary)
    env.setdefault("TERM", "dumb")
    env.setdefault("FORCE_COLOR", "0")
    env.setdefault("NO_COLOR", "1")
    env.setdefault("HERMES_ACCEPT_HOOKS", "1")

    # hermes 分支：自动链路已不再走它（RUNNER_ORDER/文本链均已移除），
    # 保留仅为兼容显式传入 runner="hermes" 的调用方。
    hermes_py = _hermes_venv_python() if runner == "hermes" else None
    if runner == "hermes" and hermes_py:
        # Use the streaming wrapper: prompt delivered via stdin, no argv length
        # limit, and the wrapper calls AIAgent.chat(stream_callback=...) which
        # fires for every token rather than waiting for the full response.
        argv = [hermes_py, _HERMES_STREAM_WRAPPER]
        stdin_data = prompt.encode("utf-8")
        stdin_mode = asyncio.subprocess.PIPE
    else:
        # codex / claude — and hermes on Windows / non-venv installs — via plain CLI.
        argv = _build_runner_cmd(runner, binary, prompt)
        stdin_data = None
        stdin_mode = asyncio.subprocess.DEVNULL

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=stdin_mode,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=str(Path.home()),
        env=env,
        **no_window_kwargs(),
    )

    if runner == "hermes" and stdin_data is not None:
        # Write prompt to stdin then close the pipe so the wrapper sees EOF.
        try:
            proc.stdin.write(stdin_data)
            await proc.stdin.drain()
            proc.stdin.close()
        except Exception:
            logger.debug("proc.stdin.write 失败（旁路，已忽略）", exc_info=True)

    total_chars = 0
    timed_out = False
    loop = asyncio.get_running_loop()
    # 600 s for hermes: sorftime MCP calls during synthesis add latency on top
    # of the generation time itself. Other CLIs keep 300 s.
    timeout_s = 600 if runner == "hermes" else 300
    deadline = loop.time() + timeout_s
    # Use asyncio.wait() rather than wait_for() so we never cancel the reader
    # coroutine mid-flight; cancelling StreamReader.read() can corrupt the
    # internal buffer on Python < 3.12.
    read_task: asyncio.Task[bytes] = asyncio.create_task(proc.stdout.read(4096))

    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                timed_out = True
                break
            done, _ = await asyncio.wait([read_task], timeout=min(remaining, 30))
            if not done:
                # 30 s of silence: if the process already exited, the pipe
                # won't produce EOF until the read_task drains — break out.
                if proc.returncode is not None:
                    read_task.cancel()
                    break
                continue  # still running — keep waiting
            chunk = read_task.result()
            if not chunk:  # EOF
                break
            text = _ANSI_RE.sub("", chunk.decode("utf-8", errors="replace"))
            if text:
                total_chars += len(text)
                yield text
            read_task = asyncio.create_task(proc.stdout.read(4096))
    finally:
        if not read_task.done():
            read_task.cancel()
        if proc.returncode is None:
            proc.kill()
            try:
                await asyncio.wait_for(proc.communicate(), timeout=5)
            except Exception:
                logger.debug("asyncio.wait_for 失败（旁路，已忽略）", exc_info=True)

    if timed_out:
        raise RuntimeError(f"{runner} CLI 超时（{timeout_s}s）")
    if total_chars == 0:
        raise RuntimeError(
            f"{runner} CLI 返回空内容"
            + (f"（退出码 {proc.returncode}）" if proc.returncode else "")
        )
    if proc.returncode and proc.returncode != 0:
        _log.warning("%s exited %s after streaming %d chars", runner, proc.returncode, total_chars)


async def _try_apimart(prompt: str, failures: list[str]) -> AsyncGenerator[tuple[str, str], None]:
    """Yield (provider, chunk) tuples from Apimart streaming; on failure
    push a human-readable reason into ``failures`` and return.

    Emits a sentinel ('_attempt', 'apimart') *before* the real network call
    so the UI can show 'trying apimart…' without waiting for a token.
    """
    yield "_attempt", "apimart"
    if not _apimart_key():
        failures.append("Apimart 密钥未配置（系统配置 → AI 服务）")
        return
    try:
        async for chunk in _stream_apimart(prompt):
            yield "claude", chunk
        return
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code if exc.response is not None else "?"
        body_preview = ""
        try:
            body_preview = exc.response.text[:200] if exc.response is not None else ""
        except Exception:
            logger.debug("body_preview = exc.response.text 失败（旁路，已忽略）", exc_info=True)
        if code in (401, 403):
            failures.append(
                f"Apimart 密钥被拒（HTTP {code}）— 该密钥可能仅有图片权限，没买 Claude 文本。"
                " 在「系统配置 → 文本 AI 提供商」把 apimart 从列表中删掉即可避免重试。"
                + (f"  返回: {body_preview}" if body_preview else "")
            )
        elif code == 429:
            failures.append("Apimart 限流（HTTP 429）— 稍后重试或升级套餐。")
        else:
            failures.append(f"Apimart HTTP {code}：{body_preview or '请求失败'}")
        _log.warning("apimart failed: HTTP %s — %s", code, body_preview)
    except Exception as exc:
        failures.append(f"Apimart 调用失败：{exc}")
        _log.warning("apimart failed: %s", exc)


async def _try_cli(runner: str, prompt: str, failures: list[str]) -> AsyncGenerator[tuple[str, str], None]:
    """Yield ('_attempt', runner) sentinel then stream chunks from the CLI."""
    yield "_attempt", runner
    if not _find_bin(runner):
        failures.append(f"{runner} CLI 未安装（PATH 找不到 / 未在「系统配置 → 外部集成路径」配置）")
        return
    try:
        async for chunk in _stream_cli_runner(runner, prompt):
            yield runner, chunk
    except Exception as exc:
        failures.append(f"{runner} CLI 失败：{exc}")
        _log.warning("%s failed: %s", runner, exc)


async def synthesize(
    mode: str,
    query: str,
    marketplace: str,
    data: Dict[str, Any],
    skip_agent: bool = False,
    source: str = "Sorftime",
) -> AsyncGenerator[tuple[str, str], None]:
    """Async generator yielding (provider_name, text_chunk) tuples.

    Provider order is read from hub_settings.text_ai_providers (default
    'assistant,deepseek,codex,claude'). deepseek uses true HTTP streaming so
    tokens arrive immediately; CLI runners buffer their output internally and
    are kept as fallbacks. On total failure, yields ('error', diagnostic_text).

    skip_agent=True drops the ivyea-agent provider — used when the caller is
    *itself* the IvyeaAgent (the panel bridge), to avoid agent→ops→agent nesting.
    """
    prompt = _build_prompt(mode, query, marketplace, data, source=source)
    failures: list[str] = []
    chain = _text_provider_chain()
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
            # '_attempt' is a UI-only sentinel; propagate it but don't
            # count it as a successful synthesis result.
            yield prov, chunk
            if prov != "_attempt":
                got_real_chunk = True
        if got_real_chunk:
            return

    detail = (
        "所有文本 AI 提供商均不可用：\n"
        + "\n".join(f"  • {f}" for f in failures)
        + "\n\n常见修法："
        + "\n  1. 在 ~/.hermes/.env 中设置 DEEPSEEK_API_KEY=sk-xxx（或在系统配置中添加）"
        + "\n  2. 安装 ivyea/codex/claude 任一 CLI，并在「系统配置 → 外部集成路径」配置绝对路径"
        + "\n  3. 或在「系统配置 → AI 服务」填入有 Claude 权限的 Apimart 密钥"
        + f"\n\n当前提供商顺序：{', '.join(chain) or '（空）'}"
    )
    yield "error", detail


async def synthesize_native(
    mode: str,
    query: str,
    marketplace: str,
    prompt_override: str | None = None,
) -> AsyncGenerator[tuple[str, str], None]:
    """MCP-native path: skip sorftime pre-fetch, hand the agent a tool-calling
    prompt so it fetches the data through its own MCP servers and writes the
    report from real numbers.

    Runs on ivyea-agent (2026-08-06; previously hermes). ~/.ivyea/mcp.json has
    sorftime / sellersprite / sif_mcp registered as trusted, so the MCP access
    that used to be hermes' exclusive advantage is now the agent's too.
    Yields (provider, chunk) tuples; on failure yields ('error', detail).
    Caller should fall back to standard ``synthesize()`` with pre-fetched data
    if this generator yields an error.

    ``prompt_override`` replaces the generic mode prompt with a task-specific
    one (e.g. 评论聚类 / Listing 改写) while keeping the same agent+MCP path.
    """
    prompt = prompt_override or _build_mcp_native_prompt(mode, query, marketplace)
    try:
        report = await run_ivyea_native(prompt, _NATIVE_SYSTEM.format(
            role="市场调研智能体", deliv="报告"))
    except Exception as exc:  # noqa: BLE001
        _log.warning("ivyea-agent native path failed: %s", exc)
        yield "error", f"IvyeaAgent 原生取数失败：{exc}"
        return
    if not report:
        yield "error", "IvyeaAgent 无输出"
        return
    yield "ivyea-agent", report


async def run_text_chain(
    prompt: str, *, order: list[str] | None = None, agent_retrieval: bool = True
) -> tuple[str, str]:
    """Canonical text generation over the standard fallback chain.

    The single entry point every board should use for "write me text" tasks:
    IvyeaAgent → DeepSeek → global fallback model → Codex → Claude
    (or a custom ``order``).
    Returns ``(provider, text)`` for the first provider that yields output.
    Raises ``RuntimeError`` with a diagnostic if the whole chain fails.

    This is the collected (non-streaming) sibling of ``synthesize()``; use
    ``synthesize()`` directly when you need token-by-token streaming.
    """
    chain = order or _text_provider_chain()
    failures: list[str] = []
    for provider in chain:
        if provider == "ivyea-agent":
            gen = _try_ivyea_agent(prompt, failures, inject_retrieval=agent_retrieval)
        elif provider == "deepseek":
            gen = _try_deepseek(prompt, failures)
        elif provider == "apimart":
            gen = _try_apimart(prompt, failures)
        elif provider == "assistant":
            gen = _try_assistant(prompt, failures)
        else:
            gen = _try_cli(provider, prompt, failures)
        parts: list[str] = []
        async for prov, chunk in gen:
            if prov != "_attempt":
                parts.append(chunk)
        text = "".join(parts).strip()
        if text:
            # Observability: record which provider in the chain actually answered
            # (and how many earlier ones were skipped/failed) for ops debugging.
            if failures:
                _log.info(
                    "run_text_chain: '%s' answered after %d failure(s): %s",
                    provider, len(failures), " | ".join(failures)[:300],
                )
            else:
                _log.info("run_text_chain: '%s' answered (%d chars)", provider, len(text))
            record_ai_call(provider, True, chars=len(text), failures=failures)
            return provider, text
    record_ai_call("(none)", False, failures=failures)
    raise RuntimeError(
        "所有文本 AI 提供商均不可用：\n"
        + "\n".join(f"  • {f}" for f in failures)
        + f"\n\n当前提供商顺序：{', '.join(chain) or '（空）'}"
    )
