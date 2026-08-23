"""Persistent runtime settings stored in {data_dir}/hub_settings.json.

Use hub_settings.get(key) from anywhere in the backend.
Empty stored values fall back to the corresponding env var.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

_DEFAULTS: Dict[str, Any] = {
    # AI — single Apimart key serves two distinct purposes:
    #   1. Image generation (gpt-image-2, /v1/images/generations) — used by Listing module
    #   2. (Optional) Text generation (Claude models, /v1/messages) — only if user opts in
    "apimart_key": "",
    "apimart_base": "https://api.apimart.ai/v1",
    # Comma-separated text-AI fallback chain for market research / ad-audit /
    # AI digest etc. Tried in order. 'assistant' = the global fallback text
    # model (the AI 问答 slot, assistant_*). New installs do not depend on
    # Hermes/GBrain; CLI agents are optional enhancement paths.
    # Valid values: ivyea-agent, assistant, deepseek, codex, claude, hermes
    "text_ai_providers": "ivyea-agent,assistant,deepseek,codex,claude",
    "deepseek_api_key": "",     # dedicated DeepSeek key (only used when 'deepseek' in text_ai_providers)
    # Comma-separated vision-AI fallback chain (for skills that accept file/image inputs).
    # Tried in order; first provider with a configured key wins.
    # Valid values: apimart (Claude Vision), openai (GPT-4o), assistant (custom provider)
    "vision_ai_providers": "openai,assistant",
    # 资讯板块 RSS 源（留空用内置默认源）。每行一个：url | 来源名 | 分类
    # 分类 ∈ {ai_industry, amazon_seller}，省略默认 ai_industry。
    "news_feeds": "",
    # Hermes LLM — primary model (written to ~/.hermes/.env + config.yaml)
    # provider: deepseek | anthropic | openai | openrouter | google | groq | together | custom
    # Saving any of these auto-syncs into Hermes config; no gateway restart needed.
    "hermes_provider": "",
    "hermes_model": "",
    "hermes_api_key": "",
    "hermes_base_url": "",   # leave empty to use provider default
    # Hermes LLM — fallback model (used when primary is rate-limited / down)
    "hermes_fallback_provider": "",
    "hermes_fallback_model": "",
    "hermes_fallback_api_key": "",
    "hermes_fallback_base_url": "",
    # AI 问答（直连大模型，不走智能体）— leave empty to fall back to
    # the default deepseek→apimart chain.
    "assistant_provider": "",     # deepseek | anthropic | openai | openrouter | ...
    "assistant_model": "",
    "assistant_vision_model": "",  # 旧字段：视觉模型曾挂在全局兜底槽下（保留兼容，新配置用 vision_*）
    # 独立视觉复核槽：Listing 成图质检等看图任务；与全局兜底彻底解耦
    "vision_provider": "",
    "vision_model": "",
    "vision_api_key": "",
    "vision_base_url": "",
    "assistant_api_key": "",
    "assistant_base_url": "",
    # IvyeaAgent local service — primary embedded agent/runtime. New installs
    # should not need to edit these; they are here so self-hosted deployments can
    # point Ops at a different local/remote agent service if needed.
    "ivyea_agent_url": "http://127.0.0.1:8765",
    "ivyea_agent_token": "",
    "ivyea_agent_auto_start": True,
    "ivyea_agent_provider": "",
    "ivyea_agent_model": "",
    "ivyea_agent_api_key": "",
    "ivyea_agent_base_url": "",
    # AI 生图（默认 Apimart gpt-image-2）— leave empty to use apimart_key.
    "image_model": "",            # default gpt-image-2
    "image_api_key": "",          # empty = reuse apimart_key
    "image_base_url": "",         # empty = reuse apimart_base
    # 语义检索的 embedding 配置曾经在这里（gbrain_embed_*），已随 GBrain 一起移除：
    # 知识库前门是 IvyeaAgent，它的检索后端由 agent 自己管（ivyea retrieval embeddings），
    # 这三个键写的是 ~/.gbrain/config.json，配了对 agent 一点作用都没有——纯误导。
    # Market data
    "sorftime_key": "",      # sorftime.com — 市场调研、关键词趋势
    "sif_key": "",           # sif.com — 深度分析工具箱（独立账号和 key）
    "sellersprite_key": "",  # sellersprite.com — 竞品关键词分析
    # Listing Generator — imgflow backend
    "imgflow_url": "http://127.0.0.1:3001",
    # 知识库文件根目录（笔记/上传落盘位置，与 GBrain 无关，前门是 IvyeaAgent）
    "brain_root": "",           # empty = use env / default /root/brain
    "openai_api_key": "",       # 视觉识别（AI 图片分析）用的 OpenAI key
    # 通知渠道（见 services/notify）。notify_webhook 留空则退回 alert_webhook，
    # 老部署不用重配。notify_events 存 JSON 数组，留空用 notify.DEFAULT_EVENTS。
    "notify_webhook": "",
    "notify_events": "",
    # AI 花费预算（美元/月）。0 = 不设预算。超了发一次通知，见 services/budget。
    "ai_budget_monthly_usd": 0,
    "ai_budget_alerted_month": "",
    # 飞书 / Lark：**一处填写，两处落地**。
    # 这一组既喂 IvyeaOps 自己的服务器告警（scripts/cpu_alert.py），
    # 也在保存时下推给 IvyeaAgent，供店铺巡检卡片 / 审批回调 / 飞书对话使用
    # （见 services/ivyea_agent_service.sync_feishu_settings）。
    # 键名保留 alert_ 前缀是为了不动存量安装的配置文件和环境变量。
    "alert_webhook": "",
    "alert_app_id": "",
    "alert_app_secret": "",
    "alert_chat_id": "",
    # feishu = open.feishu.cn（国内）；lark = open.larksuite.com（国际）
    "alert_feishu_domain": "feishu",
    # CPU alert thresholds
    "alert_threshold": 80,
    "alert_sustain": 5,
    "alert_cooldown": 30,
    # Embedded service URLs (frontend iframes)
    "dashboard_url": "",
    "terminal_url": "",
    # Account — stores new password hash set via UI (overrides IVYEA_OPS_PASSWORD_HASH)
    "password_hash": "",
    # First-run setup wizard completion flag.
    # False/absent = wizard has not been completed; True = skip wizard on next login.
    # 能力市场（门道社区的 Skill 来源）。**默认关闭**：这是个会往外发请求的功能，
    # 而这个产品的卖点是"数据不出本机"。默认开会让用户在不知情的情况下产生外联 ——
    # 哪怕请求完全匿名，这个信任成本也不该由我们替他付。
    "skill_market_enabled": False,
    # 可换源：用户能指向自建镜像。签名校验保证换源之后依然安全。
    "skill_market_url": "",
    # 门道社区的市场公钥（校验安装包签名）。
    # 校验和只能证明"没传坏"，签名才能证明"是那边发布的那份" —— 这正是
    # 用户把市场换成自建镜像之后，安全性依然成立的前提。
    "skill_market_pubkey": "R3li0pMksP_Ls5lmu5kH86L_PvhH6NMhParfS8lGCXE=",
    # 允许安装含可执行脚本的 B 类技能。**默认关，但它是个真开关，不是死路。**
    # 关着的时候也不是"只能看"：安装包随时可以下载下来自己审、自己放进技能库。
    # 打开之后每次安装仍要逐条确认脚本清单 —— 开关只降低摩擦，不替用户判断。
    "skill_market_allow_class_b": False,
    "setup_done": False,
    # Auto bug-fix: when a feature/tool operation fails, offer to launch an AI
    # repair flow (hermes in an isolated worktree, review-first). Off by default
    # — when off the frontend interceptor and backend engine never fire.
    "autofix_enabled": False,
    # --- 领星 (LingXing) ERP -----------------------------------------------
    # All LingXing traffic funnels through the IvyeaOps gateway; agents never
    # see these credentials. Two backends: OpenAPI (data + ad-write) and the
    # optional MCP (AI-native tools for the analysis agent).
    # OpenAPI backbone (data reads + ad-write operations). Credentials from
    # 领星 ERP 开放接口. Token is fetched/refreshed by the gateway and cached
    # in data_dir/lingxing_token.json (never exposed to agents).
    "lingxing_openapi_host": "https://openapi.lingxing.com",
    "lingxing_openapi_appid": "",
    "lingxing_openapi_secret": "",
    "lingxing_openapi_min_interval_ms": 340,  # conservative pacing (~3/s) to avoid bans
    # MCP backbone (optional — AI-native tools for the analysis agent once an
    # X-Mcp-Key is generated in 领星后台).
    "lingxing_mcp_key": "",
    "lingxing_mcp_url": "http://openmcp.lingxing.com/mcp-servers/lingxing-mcp",
    # Master enable for the whole integration (panels + automation). Off = the
    # gateway refuses every call. Default off.
    "lingxing_enabled": False,
    # WRITE switch ("操作领星"). Off = gateway is strictly read-only and never
    # advertises/permits write tools. Default off. Turning it on starts a
    # countdown after which it auto-reverts to read-only (defence against
    # leaving it on). 0 disables auto-expiry.
    "lingxing_operate_enabled": False,
    "lingxing_operate_expires_at": "",   # ISO ts; gateway treats write as off past this
    "lingxing_operate_ttl_minutes": 120,  # how long the write switch stays on per activation
    # Every write requires a human final confirmation in the UI (locked on by
    # decision — no threshold-based auto-execute tier).
    "lingxing_operate_require_human": True,
    # Circuit breaker: set (with a reason) when an execution fails; operate is
    # forced off and stays off until an operator re-enables (which clears this).
    "lingxing_circuit_reason": "",
    # Heterogeneous triple review: provider per persona (data-rigour / devil's-
    # advocate / business-balance). Missing provider falls back to the default
    # embedded text chain.
    "lingxing_review_providers": "ivyea-agent,deepseek,assistant",
    # Model for the weekly advisory analysis (自动化建议). Same provider space as
    # review (ivyea-agent/deepseek/assistant/hermes/claude/codex/custom:<id>);
    # empty/unavailable falls back to the default embedded text chain.
    "lingxing_analysis_provider": "ivyea-agent",
    # Custom review/analysis model slots: JSON list of
    # {"id","label","base_url","api_key","model"} (OpenAI-compatible). Reference
    # in lingxing_review_providers as "custom:<id>". CLI agents
    # (hermes/claude/codex) are also valid providers.
    "lingxing_custom_models": "[]",
    # Editable optimization methodology. Shown in the UI and injected into the
    # LLM review (and analysis) as the rubric the model must apply.
    "lingxing_rules_doc": (
        "# 亚马逊广告(SP)优化方法论\n"
        "## 数据充分性（底线：数据不足一律不动手）\n"
        "- 否词：搜索词 ≥15 点击且 0 单（或花费 ≥2×目标CPA 且 0 单）\n"
        "- 改 bid：≥15 点击才足以相信 ACOS\n"
        "- 放量(加bid)：≥3 单且 ACOS ≤ 0.8×目标\n"
        "- 收割：搜索词 ≥3 单\n"
        "- 窗口：bid/预算 14–30 天，否词/收割 30–90 天；剔除最近 1–2 天（归因延迟）\n"
        "## 目标\n"
        "- 盈亏平衡 ACOS = 毛利率；目标 ACOS = 系数×毛利率（默认 0.7）\n"
        "- 不同活动目标可不同（打榜可高于平衡换排名；利润收割低于平衡）\n"
        "## 各杠杆规则\n"
        "- 降bid(高ACOS)：新bid = RPC×目标ACOS，单步 ≤15%，不破下限\n"
        "- 加bid(放量)：ACOS≤0.8×目标且有单 → +≤15%，新bid ≤ RPC×目标\n"
        "- 高花费0单词：花费≥目标CPA → 重降或暂停；若是搜索词则否定\n"
        "- 预算：打满且ACOS≤目标 → +X%；超标且没打满 → 先修bid/否词，别加预算\n"
        "- 否词：达阈值 → negative exact；多周期一致才升 phrase\n"
        "- 收割：auto/broad ≥3单搜索词 → 加精准活动(bid≈RPC×目标) + 原活动否定它（毕业）\n"
        "## 顺序与护栏\n"
        "- 顺序：否词 → 收割 → 调bid → 预算；同一对象冷却 7 天内不重复动\n"
        "- 护栏：保护赢家(达标不准降/停)、放量bid不破平衡点、bid上下限、写白名单、单步幅度封顶\n"
    ),
    # Deterministic guardrails (hard caps, enforced in code regardless of AI
    # reviews). Empty scope lists = nothing is writable until you whitelist.
    "lingxing_scope_stores": "",          # comma-separated store ids/names allowed for writes
    "lingxing_scope_asins": "",           # comma-separated ASINs allowed for writes ("*" = any in-store)
    "lingxing_max_ops_per_run": 10,       # max write ops a single automation run may propose
    "lingxing_max_change_pct": 20,        # max +/- % change a single op may make (bid/budget)
    # Weekly advisory automation (P2 — analyse + recommend, never writes).
    "lingxing_auto_enabled": False,       # master enable for the scheduled run (default off)
    "lingxing_auto_weekday": 0,           # 0=Mon … 6=Sun
    "lingxing_auto_hour": 9,              # local hour to fire
    "lingxing_auto_report_days": 7,       # how many days of ad reports to aggregate
    "lingxing_auto_stores": "",           # sids scope (csv); empty = all accessible stores
    "lingxing_auto_max_campaigns": 40,    # cap campaigns sent to the model per run
    # --- 广告优化规则引擎 (deterministic optimizer) ------------------------
    # Target ACOS is derived from product margin (break-even ACOS = margin):
    #   target_acos = target_acos_factor * margin. Manual overrides win if set.
    "lingxing_target_acos_factor": 0.7,
    "lingxing_margin_override": 0,        # >0 = use this margin (fraction, e.g. 0.35) instead of profit data
    "lingxing_target_acos_override": 0,   # >0 = use this target ACOS directly
    "lingxing_opt_window_days": 30,       # aggregation window for the optimizer
    "lingxing_opt_exclude_recent_days": 2,  # drop the most recent N days (attribution lag)
    # conservative significance + step thresholds (user-set: 保守)
    "lingxing_neg_min_clicks": 15,        # negate a search term: >= clicks AND 0 orders
    "lingxing_bid_min_clicks": 15,        # min clicks before trusting ACOS for a bid-down
    "lingxing_scale_min_orders": 3,       # min orders before scaling a winner (bid up)
    "lingxing_harvest_min_orders": 3,     # min orders before harvesting a search term
    "lingxing_bid_step_pct": 15,          # max bid step per change (%)
    "lingxing_cooldown_days": 7,          # don't re-touch the same entity within N days
    "lingxing_bid_floor": 0.02,           # min bid
    "lingxing_bid_ceiling": 0,            # max bid (0 = no cap beyond break-even logic)
    # --- External-integration paths ----------------------------------------
    # Optional: IvyeaOps works standalone without any of these, but the
    # monitor page and agent picker light up when you point at the right
    # binaries / databases.  Leave empty to fall back to PATH lookup or
    # disable the corresponding feature.
    "hermes_bin": "",            # `hermes` CLI absolute path
    "codex_bin": "",             # `codex` CLI absolute path
    "claude_bin": "",            # `claude` CLI absolute path
    "kiro_cli_bin": "",          # `kiro-cli` CLI absolute path
    "hermes_db": "",             # /root/.hermes/state.db (token-usage)
    "codex_db": "",              # /root/.codex/state_5.sqlite
    "feishu_codex_db": "",       # /root/feishu-codex-relay/.codex-home/state_5.sqlite
    "kiro_gateway_db": "",       # /root/kiro-gateway/usage.db
    "kiro_cli_db": "",           # /root/.local/share/kiro-cli/data.sqlite3
    "kiro_cli_sessions_dir": "", # /root/.kiro/sessions/cli
    "claude_projects_dir": "",   # /root/.claude/projects (jsonl token logs)
    "ivyea_sessions_dir": "",    # /root/.ivyea/sessions (ivyea-agent 会话账本)
    "dsh_sessions_dir": "",      # /root/.dsh/sessions (DeepSeek Harness 会话)
    "hermes_node_bin": "",       # /root/.hermes/node/bin (PATH augment for spawns)
    "bun_bin": "",               # /root/.bun/bin (bun-based CLIs)
    # ASIN/广告审计的默认执行智能体（选择器里 "auto" 解析到它）。默认 hermes——它是
    # IvyeaOps 配好 skill + 数据源 MCP 的 runner，审计才能出结构化报告。空=hermes。
    "audit_default_runner": "hermes",
}

_ENV_MAP: Dict[str, str] = {
    "apimart_key": "APIMART_KEY",
    "text_ai_providers": "IVYEA_OPS_TEXT_AI_PROVIDERS",
    "ivyea_agent_url": "IVYEA_AGENT_URL",
    "ivyea_agent_token": "IVYEA_AGENT_TOKEN",
    "ivyea_agent_auto_start": "IVYEA_AGENT_AUTO_START",
    "ivyea_agent_provider": "IVYEA_AGENT_PROVIDER",
    "ivyea_agent_model": "IVYEA_AGENT_MODEL",
    "ivyea_agent_api_key": "IVYEA_AGENT_API_KEY",
    "ivyea_agent_base_url": "IVYEA_AGENT_BASE_URL",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "sorftime_key": "SORFTIME_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "brain_root": "IVYEA_OPS_BRAIN_ROOT",
    "alert_webhook": "IVYEA_OPS_ALERT_WEBHOOK",
    "alert_app_id": "IVYEA_OPS_ALERT_APP_ID",
    "alert_app_secret": "IVYEA_OPS_ALERT_APP_SECRET",
    "alert_chat_id": "IVYEA_OPS_ALERT_CHAT_ID",
    "alert_feishu_domain": "IVYEA_OPS_ALERT_FEISHU_DOMAIN",
    "alert_threshold": "IVYEA_OPS_ALERT_THRESHOLD",
    "alert_sustain": "IVYEA_OPS_ALERT_SUSTAIN",
    "alert_cooldown": "IVYEA_OPS_ALERT_COOLDOWN",
    # External integrations
    "hermes_bin": "IVYEA_OPS_HERMES_BIN",
    "codex_bin": "IVYEA_OPS_CODEX_BIN",
    "claude_bin": "IVYEA_OPS_CLAUDE_BIN",
    "kiro_cli_bin": "IVYEA_OPS_KIRO_CLI_BIN",
    "hermes_db": "IVYEA_OPS_HERMES_DB",
    "codex_db": "IVYEA_OPS_CODEX_DB",
    "feishu_codex_db": "IVYEA_OPS_FEISHU_CODEX_DB",
    "kiro_gateway_db": "IVYEA_OPS_KIRO_GATEWAY_DB",
    "kiro_cli_db": "IVYEA_OPS_KIRO_CLI_DB",
    "kiro_cli_sessions_dir": "IVYEA_OPS_KIRO_CLI_SESSIONS_DIR",
    "claude_projects_dir": "IVYEA_OPS_CLAUDE_PROJECTS_DIR",
    "ivyea_sessions_dir": "IVYEA_OPS_IVYEA_SESSIONS_DIR",
    "dsh_sessions_dir": "IVYEA_OPS_DSH_SESSIONS_DIR",
    "hermes_node_bin": "IVYEA_OPS_HERMES_NODE_BIN",
    "bun_bin": "IVYEA_OPS_BUN_BIN",
}


def _path() -> Path:
    from app.core.config import settings
    return settings.data_dir / "hub_settings.json"


def _read_file() -> Dict[str, Any]:
    p = _path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text("utf-8"))
    except Exception:
        return {}
    # 凭据在盘上是密文（见 core/secrets 的说明）。解密收口在这里，是因为
    # load() 和 get() 都走它 —— 放到上层去解，漏一条路径就是一处明文泄漏。
    # 没有 enc:v1: 前缀的值原样返回，老装机的明文配置照常能用。
    from app.core import secrets as _secrets
    return _secrets.decrypt_mapping(raw)


def load() -> Dict[str, Any]:
    """Return stored settings merged with defaults for missing keys."""
    stored = _read_file()
    result = dict(_DEFAULTS)
    result.update({k: v for k, v in stored.items() if k in _DEFAULTS})
    return result


def get(key: str, default: Any = None) -> Any:
    """Read one setting; empty/missing falls back to env var then default."""
    stored = _read_file()
    val = stored.get(key) if key in stored else None
    if val is not None and val != "":
        return val
    env_key = _ENV_MAP.get(key)
    if env_key:
        from app.core import secret_env
        env_val = secret_env.get(env_key, "")
        if env_val:
            if isinstance(_DEFAULTS.get(key), int):
                try:
                    return int(env_val)
                except (ValueError, TypeError):
                    pass
            return env_val
    return _DEFAULTS.get(key, default)


def save(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge updates and atomically persist; returns full settings."""
    current = load()
    for k, v in updates.items():
        if k in _DEFAULTS:
            current[k] = v
    p = _path()
    tmp = p.with_suffix(".json.tmp")
    # 落盘前把凭据字段加密。注意返回给调用方的仍然是**明文** current ——
    # 保存后前端要回显、runner 配置同步要用真值。
    from app.core import secrets as _secrets
    on_disk = _secrets.encrypt_mapping(current)
    tmp.write_text(json.dumps(on_disk, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(p)
    # 只记**改了哪些键**，不记值 —— 值里全是凭据，留痕的目的是"谁改了什么设置"，
    # 不是把密钥抄一份到审计库里。
    from app.core import audit as _audit
    _audit.record("settings", "save", target=",".join(sorted(updates.keys()))[:1000],
                  detail={"keys": sorted(updates.keys())})
    return current
