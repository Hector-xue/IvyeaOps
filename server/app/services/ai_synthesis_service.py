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
        pass
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
_VALID_TEXT_PROVIDERS = ("hermes", "codex", "claude", "deepseek", "assistant")


# Providers safe for non-admin users: pure HTTP APIs, no local CLI / shell / MCP.
_HTTP_ONLY_PROVIDERS = ("deepseek", "assistant")


def _text_provider_chain() -> list[str]:
    """Parse the comma-separated text_ai_providers setting and filter to
    known names. Empty / malformed config falls back to deepseek-first order.

    SECURITY: for non-admin users, the chain is forced to HTTP-only providers
    (deepseek / apimart) so a user request can NEVER spawn a local CLI agent
    (hermes/codex/claude) with shell / MCP / filesystem access."""
    from app.core import hub_settings
    # Standard chain: Hermes first, then the global fallback model, then the
    # local CLI agents. Hermes/Codex/Claude unavailable → automatically degrades.
    raw = str(hub_settings.get("text_ai_providers") or "").strip()
    if not raw:
        chain = ["hermes", "assistant", "codex", "claude"]
    else:
        out: list[str] = []
        for p in raw.split(","):
            p = p.strip().lower()
            if p in _VALID_TEXT_PROVIDERS and p not in out:
                out.append(p)
        chain = out or ["hermes", "assistant", "codex", "claude"]

    # Non-admin (and only when a request context is set) → HTTP-only, with the
    # global fallback model first (it is the configured, safe default).
    try:
        from app.core.security import current_user
        cu = current_user.get()
        if cu is not None and cu.get("role") != "admin":
            http = [p for p in chain if p in _HTTP_ONLY_PROVIDERS]
            return http or ["assistant", "deepseek"]
    except Exception:
        pass
    return chain

_ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

# ─── Hermes-native prompts (hermes calls sorftime tools itself) ───────────────
# Used when hermes is the primary provider — no pre-fetched data needed.
# hermes has sorftime MCP configured in ~/.hermes/config.yaml, so it can
# call each tool directly and synthesise from live data in one pass.

_HERMES_KEYWORD_NATIVE_PROMPT = """你是亚马逊跨境电商市场分析专家。

## 第一阶段：数据采集（必须全部完成，不得跳过任何一步）

**重要：在调用完下列全部10个工具之前，禁止输出任何报告内容。先把数据收齐，再写报告。**

你的工具列表中有 `mcp_sorftime_*` 系列工具，请**严格按顺序**依次调用：

**步骤 1** — 调用 `mcp_sorftime_keyword_detail`
  参数：keyword="{query}", keywordSupportSite="{marketplace}"
  目的：获取关键词月搜索量、CPC、转化率等核心指标

**步骤 2** — 调用 `mcp_sorftime_keyword_trend`
  参数：keyword="{query}", keywordSupportSite="{marketplace}"
  目的：获取12个月搜索趋势数据

**步骤 3** — 调用 `mcp_sorftime_keyword_extends`
  参数：keyword="{query}", keywordSupportSite="{marketplace}"
  目的：获取长尾词扩展列表（用于第五章长尾词矩阵）

**步骤 4** — 调用 `mcp_sorftime_keyword_search_results`
  参数：keyword="{query}", keywordSupportSite="{marketplace}"
  目的：获取首页竞品列表；**记录返回结果中前2个产品的 asin 字段，步骤9和10需要用到**

**步骤 5** — 调用 `mcp_sorftime_category_search_from_product_name`
  参数：productName="{query}", amzSite="{marketplace}"
  目的：获取品类节点；**记录返回结果中的 nodeid 字段，步骤6需要用到**

**步骤 6** — 调用 `mcp_sorftime_category_report`
  参数：nodeId=<步骤5返回的nodeid值>, amzSite="{marketplace}"
  目的：获取该品类 TOP 100 产品数据（价格分布、销量分布、市场格局）

**步骤 7** — 调用 `mcp_sorftime_similar_product_feature`
  参数：productName="{query}", amzSite="{marketplace}"
  目的：获取同类产品的共同特征与差异点

**步骤 8** — 调用 `mcp_sorftime_potential_product`
  参数：searchName="{query}", amzSite="{marketplace}"
  目的：获取该品类潜力产品列表

**步骤 9** — 调用 `mcp_sorftime_product_detail`
  参数：asin=<步骤4记录的第1个ASIN>, amzSite="{marketplace}"
  目的：获取首页第一名竞品详细数据

**步骤 10** — 调用 `mcp_sorftime_product_detail`
  参数：asin=<步骤4记录的第2个ASIN>, amzSite="{marketplace}"
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


_HERMES_ASIN_NATIVE_PROMPT = """你是亚马逊跨境电商市场分析专家。

## 第一阶段：数据采集（必须全部完成，不得跳过任何一步）

**重要：在调用完下列全部8个工具之前，禁止输出任何报告内容。先把数据收齐，再写报告。**

你的工具列表中有 `mcp_sorftime_*` 系列工具，请**严格按顺序**依次调用：

**步骤 1** — 调用 `mcp_sorftime_product_report`
  参数：asin="{query}", amzSite="{marketplace}"
  目的：获取产品基础画像、月销量、价格、评分、BSR等核心数据

**步骤 2** — 调用 `mcp_sorftime_product_trend`
  参数：asin="{query}", amzSite="{marketplace}"
  目的：获取最近12个月的销量和价格趋势

**步骤 3** — 调用 `mcp_sorftime_product_traffic_terms`
  参数：asin="{query}", amzSite="{marketplace}"
  目的：获取该产品的主要流量词列表；**记录搜索量最大的关键词，步骤6和7需要用到**

**步骤 4** — 调用 `mcp_sorftime_product_reviews`
  参数：asin="{query}", amzSite="{marketplace}"
  目的：获取用户评价摘要、好评/差评分布、高频问题

**步骤 5** — 调用 `mcp_sorftime_product_variations`
  参数：asin="{query}", amzSite="{marketplace}"
  目的：获取全部变体（颜色、尺寸、规格）及各变体销量占比

**步骤 6** — 调用 `mcp_sorftime_keyword_detail`
  参数：keyword=<步骤3记录的最大流量词>, keywordSupportSite="{marketplace}"
  目的：获取主流量词的月搜索量、CPC、竞争强度

**步骤 7** — 调用 `mcp_sorftime_keyword_search_results`
  参数：keyword=<步骤3记录的最大流量词>, keywordSupportSite="{marketplace}"
  目的：获取主流量词的首页竞品格局，用于竞品对比表

**步骤 8** — 调用 `mcp_sorftime_competitor_product_keywords`
  参数：asin="{query}", keywordSupportSite="{marketplace}"
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


def _build_hermes_native_prompt(mode: str, query: str, marketplace: str) -> str:
    """Build a tool-calling prompt for hermes to collect and synthesise itself."""
    template = _HERMES_KEYWORD_NATIVE_PROMPT if mode == "keyword" else _HERMES_ASIN_NATIVE_PROMPT
    return template.format(query=query, marketplace=marketplace)


# ─── Fallback prompt builder (codex / claude — no MCP tools) ─────────────────
# Derives the report structure from the hermes-native prompts so there is
# only ONE template to maintain. Strips the tool-calling phase and appends
# the pre-fetched sorftime data instead.


def _build_prompt(mode: str, query: str, marketplace: str, data: Dict[str, Any]) -> str:
    """Build fallback prompt (codex / claude path) from the hermes-native template.

    Strips the tool-calling phase from the native prompt and appends
    pre-fetched sorftime data, so only one template needs to be maintained.
    """
    data_summary = json.dumps(data, ensure_ascii=False, indent=2)
    if len(data_summary) > 40000:
        data_summary = data_summary[:40000] + "\n...(数据已截断)"

    native = _HERMES_KEYWORD_NATIVE_PROMPT if mode == "keyword" else _HERMES_ASIN_NATIVE_PROMPT
    native_filled = native.format(query=query, marketplace=marketplace)

    # Strip the data-collection phase; keep only the report template section.
    phase2_marker = "## 第二阶段：生成报告"
    idx = native_filled.find(phase2_marker)
    if idx != -1:
        # Skip the "以上X个工具全部调用完毕后" instruction line
        after_header = native_filled.find("\n\n", idx + len(phase2_marker))
        report_body = native_filled[after_header:].strip()
        # Replace hermes-tool-specific preamble with data-dump preamble
        report_body = report_body.replace(
            "以上10个工具全部调用完毕后**，根据收集到的真实数据，填写以下报告模板。",
            "请根据下方Sorftime原始数据，生成完整的市场调研报告。",
        ).replace(
            "以上8个工具全部调用完毕后**，根据收集到的真实数据，填写以下报告模板。",
            "请根据下方Sorftime原始数据，生成完整的市场调研报告。",
        )
    else:
        report_body = native_filled

    role = (
        "你是亚马逊跨境电商市场分析专家，擅长从原始数据中提炼可操盘的市场洞察。"
        if mode == "keyword"
        else "你是亚马逊跨境电商市场分析专家，擅长从竞品数据中提炼选品策略和差异化机会。"
    )
    return f"{role}\n\n{report_body}\n\n---\n原始数据（来自Sorftime MCP）：\n{data_summary}"


async def _stream_apimart(prompt: str) -> AsyncGenerator[str, None]:
    """Stream text tokens from apimart (Claude HTTP streaming)."""
    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10)) as client:
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


async def generate_text(prompt: str) -> str:
    """Plain text-only LLM generation — NO tools, NO Sorftime MCP.

    For tasks that just need the model to write text (e.g. authoring a
    SKILL.md from a description). Tries DeepSeek first, then Apimart. This is
    deliberately separate from ``synthesize_native``, which injects sorftime
    tool-calling templates and would (wrongly) try to fetch market data.
    """
    failures: list[str] = []

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
        "请在「系统配置」配置 DeepSeek / Apimart key 或全局兜底大模型。")
    )


async def generate_text_provider(provider: str, prompt: str) -> str:
    """Generate text via a SPECIFIC HTTP provider ('deepseek' | 'apimart').

    Raises if that provider isn't configured/usable — caller can fall back. Used
    for heterogeneous multi-model review (different personas → different models).
    """
    provider = (provider or "").lower().strip()
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
    "groq":       "https://api.groq.com/openai/v1",
    "together":   "https://api.together.xyz/v1",
    "xiaomi":     "https://token-plan-sgp.xiaomimimo.com/v1",
    "kimi":       "https://api.kimi.com/coding/v1",
}


def has_text_provider() -> bool:
    """True when at least one HTTP text provider (DeepSeek / Apimart / the global
    fallback model) is configured — i.e. ``generate_text`` can answer even when
    no local agent CLI (Hermes/Codex/Claude) is installed."""
    try:
        return bool(_deepseek_key() or _apimart_key() or assistant_text_cfg().get("api_key"))
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
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as c:
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
            pass
        failures.append(f"全局兜底模型 HTTP {code}：{body or '请求失败'}")
        _log.warning("assistant fallback failed: HTTP %s — %s", code, body)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"全局兜底模型调用失败：{exc}")
        _log.warning("assistant fallback failed: %s", exc)


# ---------------------------------------------------------------------------
# Vision provider helpers
# ---------------------------------------------------------------------------

def _openai_key() -> str:
    from app.core import hub_settings
    val = hub_settings.get("openai_api_key")
    return str(val) if val else ""


def _assistant_vision_cfg() -> tuple[str, str, str] | None:
    """Return (provider, key, base_url) for the custom assistant if configured,
    or None. Only providers known to support vision are returned."""
    from app.core import hub_settings
    provider = str(hub_settings.get("assistant_provider") or "").lower()
    key = str(hub_settings.get("assistant_api_key") or "")
    base = str(hub_settings.get("assistant_base_url") or "")
    VISION_CAPABLE = ("openai", "anthropic", "openrouter", "google", "together",
                      "custom", "apimart", "deepseek")
    if not key or provider not in VISION_CAPABLE:
        return None
    return provider, key, base


def _vision_provider_chain() -> list[str]:
    """Parse vision_ai_providers setting; filter to providers that actually
    have a key configured in the current environment."""
    from app.core import hub_settings
    raw = str(hub_settings.get("vision_ai_providers") or "").strip()
    # apimart is image-GEN only (no vision/analysis), so it is NOT a vision
    # provider — image understanding needs a real vision model (openai / the
    # vision-capable global fallback).
    valid = ("openai", "assistant")
    order = [p.strip().lower() for p in (raw or "openai,assistant").split(",")
             if p.strip().lower() in valid]
    order = list(dict.fromkeys(order)) or ["openai", "assistant"]

    # Only keep providers that have credentials configured right now.
    available = []
    for p in order:
        if p == "openai" and _openai_key():
            available.append(p)
        elif p == "assistant" and _assistant_vision_cfg():
            available.append(p)
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
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10)) as client:
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
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10)) as client:
        async with client.stream(
            "POST", f"{base_url.rstrip('/')}/v1/chat/completions", json=payload,
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


async def stream_vision(prompt: str, images_b64: list[str]) -> AsyncGenerator[tuple[str, str], None]:
    """Vision fallback chain: tries each configured vision provider in order.

    Each element of images_b64 should be a data-URI string like
    'data:image/jpeg;base64,...' as produced by FileReader.readAsDataURL().
    Yields (provider_name, text_chunk) tuples; on total failure yields ('error', detail).
    """
    chain = _vision_provider_chain()
    if not chain:
        yield "error", (
            "当前没有配置支持视觉分析的模型。"
            "请在「系统配置 → AI 服务」配置以下任意一项：\n"
            "  · Apimart key（Claude Vision）\n"
            "  · OpenAI API key（GPT-4o）\n"
            "  · 自定义 assistant provider（支持视觉的模型）"
        )
        return

    failures: list[str] = []
    for provider in chain:
        try:
            got = False
            if provider == "apimart":
                label = "claude"
                gen = _stream_apimart_vision(prompt, images_b64)
            elif provider == "openai":
                label = "gpt-4o"
                gen = _stream_openai_vision(prompt, images_b64, _openai_key())
            else:  # assistant
                cfg = _assistant_vision_cfg()
                if not cfg:
                    continue
                _, key, base = cfg
                label = "assistant"
                # Anthropic-format providers go through Apimart-style; others use OpenAI compat.
                if "anthropic" in cfg[0] or "apimart" in cfg[0]:
                    gen = _stream_apimart_vision(prompt, images_b64)
                else:
                    gen = _stream_openai_vision(prompt, images_b64, key, base or "https://api.openai.com")

            async for chunk in gen:
                got = True
                yield label, chunk
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
    MCP. Tries DeepSeek then Apimart. On total failure yields ('error', detail).
    """
    failures: list[str] = []

    dkey = _deepseek_key()
    if dkey:
        try:
            got = False
            async for chunk in _stream_openai_compat(
                dkey, "https://api.deepseek.com", "deepseek-chat", prompt
            ):
                got = True
                yield "deepseek", chunk
            if got:
                return
            failures.append("DeepSeek 返回空")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"DeepSeek: {exc}")

    if _apimart_key():
        try:
            got = False
            async for chunk in _stream_apimart(prompt):
                got = True
                yield "claude", chunk
            if got:
                return
            failures.append("Apimart 返回空")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"Apimart: {exc}")

    yield "error", (
        "无可用文本模型。"
        + (" / ".join(failures) if failures else "请在「系统配置」配置 DeepSeek 或 Apimart key。")
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
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as client:
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
            pass
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
            pass

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
                pass

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
            pass
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
) -> AsyncGenerator[tuple[str, str], None]:
    """Async generator yielding (provider_name, text_chunk) tuples.

    Provider order is read from hub_settings.text_ai_providers (default
    'deepseek,hermes,codex,claude'). deepseek uses true HTTP streaming so
    tokens arrive immediately; CLI runners buffer their output internally and
    are kept as fallbacks. On total failure, yields ('error', diagnostic_text).
    """
    prompt = _build_prompt(mode, query, marketplace, data)
    failures: list[str] = []
    chain = _text_provider_chain()

    for provider in chain:
        if provider == "deepseek":
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
        + "\n  2. 安装 hermes/codex/claude 任一 CLI，并在「系统配置 → 外部集成路径」配置绝对路径"
        + "\n  3. 或在「系统配置 → AI 服务」填入有 Claude 权限的 Apimart 密钥"
        + f"\n\n当前提供商顺序：{', '.join(chain) or '（空）'}"
    )
    yield "error", detail


async def synthesize_native(
    mode: str,
    query: str,
    marketplace: str,
) -> AsyncGenerator[tuple[str, str], None]:
    """Hermes-native path: skip sorftime pre-fetch, give hermes a tool-calling
    prompt so it fetches data via its own sorftime MCP and writes the report.

    Only tries hermes (other CLI/HTTP providers have no MCP access).
    Yields (provider, chunk) tuples; on failure yields ('error', detail).
    Caller should fall back to standard ``synthesize()`` with pre-fetched data
    if this generator yields an error.
    """
    prompt = _build_hermes_native_prompt(mode, query, marketplace)
    failures: list[str] = []
    got_real_chunk = False
    async for prov, chunk in _try_cli("hermes", prompt, failures):
        yield prov, chunk
        if prov != "_attempt":
            got_real_chunk = True
    if not got_real_chunk:
        reason = failures[0] if failures else "hermes 无输出"
        yield "error", reason


async def run_text_chain(
    prompt: str, *, order: list[str] | None = None
) -> tuple[str, str]:
    """Canonical text generation over the standard fallback chain.

    The single entry point every board should use for "write me text" tasks:
    Hermes → global fallback model → Codex → Claude (or a custom ``order``).
    Returns ``(provider, text)`` for the first provider that yields output.
    Raises ``RuntimeError`` with a diagnostic if the whole chain fails.

    This is the collected (non-streaming) sibling of ``synthesize()``; use
    ``synthesize()`` directly when you need token-by-token streaming.
    """
    chain = order or _text_provider_chain()
    failures: list[str] = []
    for provider in chain:
        if provider == "deepseek":
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
