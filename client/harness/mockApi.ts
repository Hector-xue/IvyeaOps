// 验证台的假后端。
//
// ── 为什么要有这个东西 ────────────────────────────────────────────────────
// 上一轮改外观时，我拿**手写的 HTML** 当验证对象：类名是从 JSX 里抄的，但
// 抄漏的东西就永远看不见。真实页面里的会话搜索框、"加载更多"按钮、右侧产物栏
// 一个都没进那份手写稿，于是它们被新样式改坏了也照样"验证通过"。
//
// 这里换个做法：**渲染真实的 <App/>**，只把 HTTP 那一层换掉。组件树、路由、
// 懒加载、主题启动顺序全都是真的，所以真实页面里长什么样，这里就长什么样。
//
// ⚠️ 只在 vite.harness.config.ts 这个入口里被引用，**不进产物包**。
import axios from "axios";
import { api } from "../src/api/client";

type Canned = Record<string, unknown>;

const now = Date.now();
const ago = (h: number) => new Date(now - h * 3600_000).toISOString();

/** 会话列表要够长，才能逼出"加载更多"和列表滚动 —— 那正是上一轮漏掉的两处。 */
const TITLES = [
  "列一下 /root 下的目录", "广告怎么优化才能降 ACOS", "测试", "广告怎么优化投放结构",
  "你好", "广告怎么优化否词", "广告怎么做冷启动", "广告怎么控制预算",
  "看下我的店铺健康度", "简单讲一下亚马逊 A+ 页面", "广告怎么优化搜索词",
  "我的阿联酋站点数据", "广告怎么优化竞价策略", "测试", "亚马逊卖家注意事项",
];
const HOURS = [3, 3, 3, 4, 6, 8, 22, 22, 23, 26, 30, 96, 97, 98, 99];

/** 字段名照抄 api/ivyeaAgent.ts 的 ConsoleSessionRow —— 猜字段名就等于又验了一遍空列表。 */
const SESSIONS = TITLES.map((title, i) => ({
  id: "s" + (i + 1),
  title,
  preview: title,
  turns: 2 + (i % 5),
  updated: Math.floor((now - HOURS[i] * 3600_000) / 1000),
  workspace: "默认工作区",
  owner: "admin",
  source: "console",
  indexed: true,
}));

/** 一轮真实形态的问答：有执行过程、有 markdown 正文、有表格和代码块。 */
const TURNS = [
  // 带附图的一轮：存档里留下的是 agent 注入的 `[用户附图 …]` 段落 + 原图句柄。
  // 气泡里要**只显示那句问话 + 缩略图**，代读的文字一个字都不该露出来。
  { role: "user", content: "这张图里面是什么？\n\n[用户附图 —— 视觉模型代读的内容]\n"
      + "本轮用户上传了 1 张图。图片本体不在你的上下文里，下面是视觉模型逐张读出的内容。\n"
      + "第 1 张（代读模型 qwen-vl、原图句柄 ivyea-ref://0000000000000abcd）：\n一张露营椅的主图。" },
  { role: "assistant", content: "是一张露营椅的主图（图由视觉模型代读成文字后交给我）。" },
  { role: "user", content: "帮我跑一下广告巡检" },
  {
    role: "assistant",
    content: [
      "好的，跑广告巡检前先确认数据源。我并行查一下：本地是否有报表文件、已配置的 MCP 数据源有哪些。",
      "",
      "MCP 数据源是 sorftime（偏选品/市场数据，未见广告搜索词报表拉取工具）。巡检需要「广告搜索词报表」数据，我再确认本地报表和领星配置。",
      "",
      "## 1. 浪费最集中的三组词根",
      "",
      "- 「folding chair for camping」词根：花费 $1,284，订单 0，点击 612",
      "- 「beach chair heavy duty」词根：花费 $906，订单 2，ACOS 431%",
      "- 竞品品牌词：花费 $538，转化率 0.3%，远低于账户均值",
      "",
      "> 这三组合计 $2,728，占可优化空间的 76% —— 先动它们，其余的暂时不用碰。",
      "",
      "## 2. 建议动作",
      "",
      "| 词根 | 当前花费 | 建议 | 预计节省 |",
      "|---|---|---|---|",
      "| folding chair for camping | $1,284 | 加否定精确 | $1,284 |",
      "| beach chair heavy duty | $906 | 竞价下调 15% | $310 |",
      "| 竞品品牌词 | $538 | 暂停后观察 7 天 | $538 |",
      "",
      "### 执行注意",
      "",
      "否词阈值按既定护栏走（≥15 点击且 0 单），竞价步长不超过 15%，改完冷却 7 天再评估。",
      "",
      "```json",
      '{"negatives": 3, "bid_changes": 1, "cooldown_days": 7}',
      "```",
      "",
      "---",
      "",
      // 作图收进任务台之后，正文里会出现图片和链接 —— 这两种以前渲染器根本不认，
      // 出的图只会显示成一串裸 URL。放进验证台，改坏了能立刻看出来。
      "顺手按你说的风格出了一张主图：",
      "",
      "![生成的主图](/ivyea-logo.png)",
      "",
      "参考的竞品页在 [这里](https://www.amazon.com/dp/B0EXAMPLE1)，原图也留一份：",
      "",
      "/art/bg.png",
      "",
      "要我把这三条改动生成待审批工单吗？",
    ].join("\n"),
  },
];

/**
 * URL → 响应。键是 `api` 实例 baseURL 之后的路径前缀，命中最长的那个。
 * 没命中的一律回 `{}` 而不是 404 —— 验证台的目的是把界面画出来，
 * 不是模拟后端的错误分支。
 */
/** 一份 T3（本地 CV 量化）下产出的套图方案：版式逆向被跳过并留了说明。
 *  用 ?vt=1|2 时降级横幅应当消失，这正是要盯的分支。 */
const DEMO_PLAN = {
  deliverable: "gallery",
  planner: "ai",
  style: { direction: "clean studio", palette: "蓝白", accent_color: "#1C4A8C" },
  product_lock: "藏青蓝硬壳收纳箱",
  template_story: [],
  template_images: [],
  images: [
    {
      shot_type: "white_main", layout: "white_main", product_presence: "hero",
      selling_point: "纯白底主图", headline: "", asset_mode: "generate",
      text_on_image: false, canvas: "2000x2000", evidence: "产品实拍",
      render_prompt: "white background product shot", final_url: "",
      product_source_url: "https://example.com/p.jpg",
    },
  ],
  quality: {
    score: 88,
    ready: true,
    issues: [{ code: "no_semantic_vision", severity: "info",
               message: "当前视觉能力为「本地 CV 度量（OCR：RapidOCR (ONNX) 1.4.4）」，只能量化图片而无法逆向版式，本项已跳过。配置一个支持视觉的模型即可解锁竞品套图版式复刻。" }],
    skipped_analyses: [{ stage: "reference_templates", code: "no_semantic_vision",
                         message: "当前视觉能力为「本地 CV 度量（OCR：RapidOCR (ONNX) 1.4.4）」，只能量化图片而无法逆向版式，本项已跳过。配置一个支持视觉的模型即可解锁竞品套图版式复刻。" }],
    vision_tier: 3,
    vision_tier_label: "本地 CV 度量（OCR：RapidOCR (ONNX) 1.4.4）",
  },
  set_qa: null,
};

const ROUTES: Array<[string, Canned | ((url: string) => Canned)]> = [
  // ?as=user —— 换成一个没被授予任何模块的注册用户。
  // 权限相关的界面（能力市场里哪些格子该出现）只有用非管理员身份打开才验得到：
  // require_module 对 admin 无条件放行，管理员视角下永远看不见问题。
  ["/auth/me", () => (new URLSearchParams(location.search).get("as") === "user"
    ? { username: "zhang", role: "user", permissions: [] }
    : { username: "admin", role: "admin", permissions: [] })],
  ["/setup/status", { needs_setup: false, checks: {} }],
  ["/setup/update-info", {
    current: "1.6.1", latest: "1.7.0", update_available: true,
    release_url: "", platform_update_supported: true, detail: "发现新版本 1.7.0",
  }],
  ["/budget/summary", {
    known: true, total_tokens: 13_900_000, spend_usd: 42.7, enabled: false,
    limit_usd: 0, ratio: 0, level: "ok", age_seconds: 30,
  }],
  // 两个工作区 + count，才验得到"折叠了不该冒加载更多"和"计数不是本页条数"这两条。
  // count 是**真实**条数（服务端分页前数的），故意和 SESSIONS 的长度不一致。
  ["/ivyea-agent/console/sessions", {
    ok: true, sessions: SESSIONS, agent_available: true,
    workspaces: [
      { name: "默认工作区", path: "/root", builtin: true, count: 189 },
      { name: "Amazon", path: "/root/amazon-image-workflow", builtin: false, count: 0 },
    ],
    total: 189, offset: 0, has_more: true,
  }],
  // 目录选择器。字段名照抄 server/app/agents/routers/files.py 的 browse_filesystem。
  ["/agents/browse-filesystem", {
    path: "/root", parent: "/",
    suggestions: [
      { path: "/root/amazon-image-workflow", name: "amazon-image-workflow", type: "directory" },
      { path: "/root/backups", name: "backups", type: "directory" },
      { path: "/root/brain", name: "brain", type: "directory" },
      { path: "/root/claudecodeui", name: "claudecodeui", type: "directory" },
      { path: "/root/dev-history", name: "dev-history", type: "directory" },
      { path: "/root/dsh-workspace", name: "dsh-workspace", type: "directory" },
      { path: "/root/feishu-claude-relay", name: "feishu-claude-relay", type: "directory" },
      { path: "/root/harness", name: "harness", type: "directory" },
      { path: "/root/ivyea-agent", name: "ivyea-agent", type: "directory" },
      { path: "/root/ivyea-ops", name: "ivyea-ops", type: "directory" },
      { path: "/root/.cache", name: ".cache", type: "directory" },
    ],
  }],
  ["/ivyea-agent/console/presets", { ok: true, presets: [] }],
  // 能力市场的三个数据源。真实部署里 /skill/* 和 /skill-market/* 都挂在
  // require_module("skill-hub") 后面，没这个模块的用户会 403 —— 前端要在
  // 请求之前就把对应区块收起来，这里给的是**有权限时**该看到的样子。
  // Skill 中心并入能力市场后，「技能」标签下的三段各自要的数据。
  // 字段名照抄 server/app/routers/skill_tools.py 的 SkillToolListResponse / SkillToolMeta。
  // **categories 不能漏** —— 少一个字段就是整页白屏（SkillTools 直接 Object.entries 它）。
  ["/skill-tools/list", {
    tools: [
      { name: "amazon/search-term", category: "amazon", description: "Search term report analysis",
        description_zh: "搜索词报表分析", icon: "⚡", pinned: false, has_execution: true,
        inputs: [{ name: "asin", label: "ASIN", required: true }],
        kind: "report", runtime: "llm-only", output_format: "markdown",
        exportable: true, sample_params: {} },
      { name: "amazon/market-research", category: "amazon", description: "Market research",
        description_zh: "市场调研", icon: "◎", pinned: false, has_execution: true,
        inputs: [], kind: "report", runtime: "llm-only", output_format: "markdown",
        exportable: false, sample_params: {} },
    ],
    categories: { amazon: 2 },
  }],
  ["/skill-tools/runs", { runs: [] }],
  ["/skill/stats", { total_skills: 98, total_size_bytes: 1048576,
                     categories: { amazon: 5, research: 12 }, recently_edited: [] }],
  ["/skill/agent-sync", { domains: ["amazon"], roots: ["/root/ivyea-ops/data/skills/amazon"],
                          registered: true, count: 5 }],
  // 字段名照抄 server/app/services/skill_repo.py 的 SkillMeta。
  // updated_at 漏了的话界面上是「Invalid Date」—— 猜字段名就是这么露馅的。
  ["/skill/list", { skills: [
    { name: "amazon/ad_negative_guard", category: "amazon", description: "Negative keyword guard",
      description_zh: "否词护栏：低效搜索词批量加否", pinned: false, editable: true,
      source: "user", updated_at: ago(6), size_bytes: 4096, file_count: 3 },
    { name: "custom/weekly_review", category: "custom", description: "Weekly account review",
      description_zh: "账户周检清单", pinned: false, editable: true,
      source: "user", updated_at: ago(30), size_bytes: 2048, file_count: 1 },
  ], total: 2 }],
  ["/skill-market/status", { enabled: true, reachable: true, base_url: "https://mendao.example" }],
  ["/skill-market/skills", { total: 1, items: [
    { slug: "ops/keyword-cluster", name: "关键词聚类", version: "1.0.0",
      summary: "把搜索词报表聚成可执行的词簇", author: "门道社区", installed: false },
  ] }],
  // 附图换句柄：任务台发送前会调它，图生图靠这个句柄把原图交给作图链路。
  ["/assistant/image/ref", { ref: "ivyea-ref://0000000000000abcd", bytes: 1234 }],
  ["/ivyea-agent/vision/describe", { ok: true, provider: "qwen-vl", text: "一张露营椅的主图。" }],
  // 会话详情：/ivyea-agent/chat/sessions/<id>。**必须比列表那条更长**才会先命中
  // （match() 按前缀长度排序），否则打开一条会话拿到的是空列表，永远验不到会话态。
  // 会话详情带 context：真 agent 在这里回一份"整条会话占了多少上下文"
  // （service._public_session_detail），进度条打开历史会话时就靠它。
  // used 按会话 id 派生，好让"切会话要换成另一条的数"这件事在验证台里看得出来。
  ["/ivyea-agent/chat/sessions/", (url: string) => {
    const id = decodeURIComponent((url.split("/ivyea-agent/chat/sessions/")[1] || "").split("?")[0]);
    const seed = [...id].reduce((n, c) => n + c.charCodeAt(0), 0);
    const messages = 4000 + (seed % 40) * 700;
    return {
      ok: true,
      session: {
        id: id || "s3", model: "deepseek-v4-pro", messages: TURNS,
        total_turns: 2, has_more: false,
        // 整条会话的累计账（agent ≥ v1.16.1 落盘的那份）。**历史会话的统计条全靠它**
        // ——恢复出来的轮次身上没有计时/用量，不铺这条就永远验不到"打开旧会话也看得见
        // 用时/输入/输出"。
        // ?nostats=1 —— 装成**这次改动之前存下的老会话**（没有累计账）。统计条这时
        // 必须退回"几轮几步"，而不是整块空白：为了新增一项弄丢已有的那项是最糟的。
        ...(new URLSearchParams(location.search).get("nostats") === "1" ? {} : {
          stats: { turns: 2, steps: 5, elapsed_ms: 78_400,
                   usage: { prompt_tokens: 24_600, completion_tokens: 1_180,
                            prompt_cache_hit_tokens: 12_300, llm_ms: 31_500 } },
        }),
        context: {
          used: 1520 + 6910 + messages, window: 128000,
          percent: Number((((1520 + 6910 + messages) * 100) / 128000).toFixed(2)),
          estimated: true, model: "deepseek-v4-pro",
          breakdown: { system: 1520, tools: 6910, messages },
        },
      },
    };
  }],
  ["/ivyea-agent/chat/sessions", { ok: true, sessions: [] }],
  // health.version 决定附图走哪条路：≥ 1.15.3 的 agent 认识 attachments（附图的
  // 文字版进 user 消息、跟着历史走），老的只能退回塞 system。?oldagent=1 验后者。
  ["/ivyea-agent/status", () => ({
    ok: true, available: true, model: "deepseek-v4-pro", ready: true,
    // ≥ 1.15.4 才认 payload.model —— 模型芯片能不能真的切就看它。
    // ?oldagent=1 装成老 agent，验"退回只跳系统配置、不给假开关"那条路。
    health: { version: new URLSearchParams(location.search).get("oldagent") === "1" ? "1.15.1" : "1.15.4",
              model: { model: "deepseek-v4-pro" } },
  })],
  // 模型选择器的数据源。**必须有没配 key 的那几家**：面板把它们收进「未配置密钥」
  // 分组，那一段的样式只在这种数据下才渲染得出来。
  ["/ivyea-agent/model/providers", {
    ok: true,
    providers: [
      { id: "deepseek", label: "DeepSeek", key_status: "configured", default_model: "deepseek-v4-flash",
        models: ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"], model_count: 3 },
      { id: "openrouter", label: "OpenRouter", key_status: "configured", default_model: "x-ai/grok-4.6",
        models: ["x-ai/grok-4.6", "anthropic/claude-sonnet-4-6", "google/gemini-3.7-flash",
                 "deepseek/deepseek-v4-pro-0813", "qwen/qwen3.8-27b"], model_count: 5 },
      { id: "ollama", label: "Ollama / Local OpenAI-compatible", key_status: "none",
        models: ["qwen3:8b", "llama3.3:70b"], model_count: 2 },
      { id: "anthropic", label: "Anthropic Claude API", key_status: "missing:ANTHROPIC_API_KEY",
        models: ["claude-sonnet-4-6"], model_count: 1 },
      { id: "openai", label: "OpenAI API", key_status: "missing:OPENAI_API_KEY",
        models: ["gpt-5.6"], model_count: 1 },
      { id: "gemini", label: "Google Gemini API", key_status: "missing:GEMINI_API_KEY",
        models: ["gemini-3.7-flash"], model_count: 1 },
    ],
  }],
  ["/ivyea-agent/model/providers/", (url: string) => {
    const pid = (url.split("/ivyea-agent/model/providers/")[1] || "").split("/")[0];
    return { ok: true, catalog: { ok: true, provider_id: pid, label: pid, source: "live",
                                  models: ["刷新到的-模型-1", "刷新到的-模型-2"], default_model: "刷新到的-模型-1" } };
  }],
  // 系统配置：**必须给几个槽位填上 provider**，否则 LLMModelBlock 的模型名那一块
  // 根本不渲染（它只在选了 provider 之后才出现）—— 模型下拉就永远验不到。
  ["/settings", {
    settings: {
      ivyea_agent_url: "http://127.0.0.1:8765", ivyea_agent_auto_start: true,
      ivyea_agent_provider: "deepseek", ivyea_agent_model: "deepseek-v4-flash",
      ivyea_agent_api_key: "sk-demo-agent", ivyea_agent_base_url: "",
      assistant_provider: "deepseek", assistant_model: "deepseek-v4-flash",
      assistant_api_key: "sk-demo-assistant", assistant_base_url: "",
      vision_provider: "siliconflow", vision_model: "Qwen/Qwen3-VL-30B-A3B-Instruct",
      vision_api_key: "sk-demo-vision", vision_base_url: "https://api.siliconflow.cn/v1",
      apimart_key: "sk-demo-apimart", apimart_base: "https://api.apimart.ai/v1",
      image_model: "", image_api_key: "", image_base_url: "",
      text_ai_providers: "ivyea-agent,deepseek,assistant",
      vision_ai_providers: "apimart,openai,assistant",
    },
    secret_keys: ["apimart_key", "ivyea_agent_api_key", "vision_api_key"],
  }],
  // ?catalogfail=1 —— 装成中转商问不到清单（实测 Apimart 余额不足会返回 402）。
  // 那条降级路径（"常见模型名"兜底 + 说清原因 + 仍可手输）只有这种数据下才渲染得出来。
  ["/settings/model-catalog", () => (
    new URLSearchParams(location.search).get("catalogfail") === "1"
      ? { ok: false, error: "", catalog: { ok: false, source: "builtin", models: [],
            error: 'HTTP 402: {"error":{"message":"insufficient balance"}}' } }
      : { ok: true, catalog: { ok: true, source: "live", label: "硅基流动",
            default_model: "Qwen/Qwen3-VL-30B-A3B-Instruct",
            models: ["Qwen/Qwen3-VL-30B-A3B-Instruct", "deepseek-ai/DeepSeek-V4-Flash",
                     "zai-org/GLM-5.2", "Tongyi-MAI/Z-Image-Turbo"] } }
  )],
  ["/ivyea-agent/skills", { ok: true, skills: [] }],
  ["/ivyea-agent/ops-tools", { ok: true, tools: [] }],
  ["/skill-tools/pinned", []],
  // ── Listing 工作台 ──────────────────────────────────────────────────────
  // 键是 "/listing/..."（见 reply()：按 baseURL+url 匹配）。
  // 这里只铺到"能打开视觉步骤并看到一份方案"为止——目的就是验降级横幅这类
  // 只在特定状态下才出现的界面，它们此前在验证台里根本渲染不出来。
  ["/listing/projects/p-demo/reference-images", { uploaded: [], scraped: [] }],
  ["/listing/projects/p-demo/jobs", { active: null, jobs: [] }],
  ["/listing/projects/p-demo", () => ({
    id: "p-demo", asin: "B0DEMO0001", marketplace: "US", status: "planned",
    title: "便携旅行收纳箱", created_at: Date.now() / 1000, updated_at: Date.now() / 1000,
    scrape_data: JSON.stringify({ title: "便携旅行收纳箱", bullets: [], images: [] }),
    analysis_data: null, copy_result: null, copy_job_id: null,
    creative_sets: JSON.stringify({ gallery: DEMO_PLAN }),
    imgflow_project_id: null,
  })],
  ["/listing/projects", [{
    id: "p-demo", asin: "B0DEMO0001", marketplace: "US", status: "planned",
    title: "便携旅行收纳箱", created_at: Date.now() / 1000, updated_at: Date.now() / 1000,
    active_jobs: [],
  }]],
  // 「系统状态与更多设置」折叠区里的通知与 MCP 两块。缺了它们整棵子树会在
  // Object.entries(undefined) / tokens.length 上炸掉——而系统状态卡就在同一棵
  // 子树里，于是根本验不到。这是 harness 的坑，不是产品的。
  ["/notify/config", {
    events: { task_done: "任务完成", task_failed: "任务失败", budget_warn: "预算告警" },
    default_events: ["task_failed"], enabled_events: ["task_failed"],
    webhook_set: false, channel: "",
  }],
  ["/notify/budget/summary", { month: "2026-08", limit_usd: 0, spend_usd: 0, total_tokens: 0, ratio: 0, level: "ok", exceeded: false }],
  ["/notify/budget", { month: "2026-08", limit_usd: 0, spend_usd: 0, total_tokens: 0, ratio: 0, level: "ok", exceeded: false }],
  ["/mcp-admin/tokens", { tokens: [], scopes: ["read", "write"] }],
  ["/mcp-admin/config", { config: "{}" }],
  // 系统状态卡。视觉那行是三档链（1 主脑直读 / 2 旁路 / 3 本地 CV），
  // 用 ?vt=1|2|3 切档来验徽标与文案——默认给 T3，因为它是最容易被误当成
  // "功能坏了"的那一档，也是最需要盯住渲染的。
  // 使用手册对话框：文档目录 + 正文。
  // 服务器终端：两列栅格（终端列表 + 终端）。第三栏「会话内容快照」已移除，
  // 这里要能验出终端确实铺满了右边、没留白。
  ["/terminal/live/sessions", { sessions: [
    { id: "s-1", title: "默认终端", cwd: "/root", status: "running", archived: false,
      created_at: 1755400000, updated_at: 1755400000 },
  ] }],
  // 字段名照抄 server/app/routers/terminal.py 的 get_live_history —— 少一个 items
  // 前端就是 `data.items.length` 直接崩，整页变「页面渲染出错」。
  ["/terminal/live/sessions/s-1/history", { items: [], total: 0 }],
  ["/terminal/status", { active: true, running: true, url: "" }],
  ["/terminal/bash-history", { items: [] }],
  // 知识库工作台：页头统计 + 四个标签。
  ["/brain/files", { files: [
    { path: "amazon/ads/negative.md", title: "否词护栏", category: "ads", size: 2048, mtime: 1755300000 },
    { path: "amazon/listing/main-image.md", title: "主图合规", category: "listing", size: 1024, mtime: 1755310000 },
    { path: "amazon/policy/vat.md", title: "VAT 税务", category: "policy", size: 4096, mtime: 1755320000 },
  ] }],
  ["/brain/uploads", { uploads: [] }],
  ["/brain/chat/status", { configured: true, provider: "ivyea-agent", model: "deepseek-v4-pro" }],
  ["/brain/overview", { ready: { db_ready: true, embed_ready: true, actions: [], hint: "" }, counts: {} }],
  // 服务器监控 / 资讯 / 分析工具：字段名照抄各自的 response model。
  // 这三个页面此前在验证台里整页崩溃 —— 那是 mock 缺字段，不是页面坏了；
  // 但也说明它们在真实降级场景下同样脆弱（见 CHANGELOG 的加固条目）。
  ["/monitor/snapshot", {
    cpu: { percent: 12.5, count: 2, load_1m: 0.4, load_5m: 0.3, load_15m: 0.2 },
    memory: { total: 3999997952, used: 2200000000, available: 1700000000, percent: 55.1, percent_used_raw: 58.2 },
    disk: { total: 52000000000, used: 31000000000, free: 21000000000, percent: 59.6,
            total_hardware: 53687091200, percent_hardware: 57.7 },
    network: { bytes_sent_total: 12000000, bytes_recv_total: 34000000,
               bytes_sent_rate: 1200, bytes_recv_rate: 3400, interface: "eth0" },
    uptime_seconds: 864000,
  }],
  ["/monitor/services", []],
  ["/monitor/processes", []],
  ["/monitor/logs", { lines: [] }],
  // Token 统计面板。**数据是真机 /api/monitor/token-usage 的真实快照**，不是编的：
  // 这一屏要验的恰恰是量级关系（缓存占了 99%，总计必然远大于输入+输出），
  // 随手编一组"好看"的数只会把口径 bug 验没了。
  ["/monitor/token-usage", {
    "totals": {
      "sessions": 2036,
      "input_tokens": 2415083805,
      "output_tokens": 126179342,
      "cache_read_tokens": 35334591515,
      "cache_write_tokens": 635824502,
      "total_tokens": 38511679164,
      "cost_usd": 27240.7241
    },
    "daily": [
      {
        "day": "2026-08-21",
        "sessions": 3,
        "input_tokens": 1582,
        "output_tokens": 789975,
        "cache_read_tokens": 139934767,
        "cache_write_tokens": 2563077,
        "total_tokens": 143289401,
        "cost_usd": 105.7439
      },
      {
        "day": "2026-08-20",
        "sessions": 3,
        "input_tokens": 12226,
        "output_tokens": 884789,
        "cache_read_tokens": 327820579,
        "cache_write_tokens": 1697365,
        "total_tokens": 330414959,
        "cost_usd": 196.629
      },
      {
        "day": "2026-08-19",
        "sessions": 1,
        "input_tokens": 1016,
        "output_tokens": 306921,
        "cache_read_tokens": 127520185,
        "cache_write_tokens": 729204,
        "total_tokens": 128557326,
        "cost_usd": 75.9957
      },
      {
        "day": "2026-08-18",
        "sessions": 6,
        "input_tokens": 106483,
        "output_tokens": 1678110,
        "cache_read_tokens": 1024843147,
        "cache_write_tokens": 5791556,
        "total_tokens": 1032419296,
        "cost_usd": 586.545
      },
      {
        "day": "2026-08-17",
        "sessions": 1,
        "input_tokens": 670,
        "output_tokens": 377509,
        "cache_read_tokens": 41812978,
        "cache_write_tokens": 1261216,
        "total_tokens": 43452373,
        "cost_usd": 38.2302
      },
      {
        "day": "2026-08-16",
        "sessions": 6,
        "input_tokens": 73525,
        "output_tokens": 1949377,
        "cache_read_tokens": 981840601,
        "cache_write_tokens": 4567539,
        "total_tokens": 988431042,
        "cost_usd": 568.1969
      }
    ],
    "weekly": [
      {
        "week": "2026-W33",
        "sessions": 14,
        "input_tokens": 121977,
        "output_tokens": 4037304,
        "cache_read_tokens": 1661931656,
        "cache_write_tokens": 12042418,
        "total_tokens": 1678133355,
        "cost_usd": 1003.1438
      },
      {
        "week": "2026-W32",
        "sessions": 19,
        "input_tokens": 330249,
        "output_tokens": 8157921,
        "cache_read_tokens": 3635834593,
        "cache_write_tokens": 27544531,
        "total_tokens": 3671867294,
        "cost_usd": 2195.2972
      },
      {
        "week": "2026-W31",
        "sessions": 62,
        "input_tokens": 1035853,
        "output_tokens": 5048994,
        "cache_read_tokens": 2183986936,
        "cache_write_tokens": 28001637,
        "total_tokens": 2218073420,
        "cost_usd": 1349.014
      },
      {
        "week": "2026-W30",
        "sessions": 110,
        "input_tokens": 2587335,
        "output_tokens": 6937470,
        "cache_read_tokens": 2326010445,
        "cache_write_tokens": 26009907,
        "total_tokens": 2361545157,
        "cost_usd": 1470.8641
      }
    ],
    "monthly": [
      {
        "month": "2026-08",
        "sessions": 124,
        "input_tokens": 2001314,
        "output_tokens": 18901910,
        "cache_read_tokens": 8056500927,
        "cache_write_tokens": 71819785,
        "total_tokens": 8149223936,
        "cost_usd": 4897.7832
      },
      {
        "month": "2026-07",
        "sessions": 477,
        "input_tokens": 262579055,
        "output_tokens": 30695423,
        "cache_read_tokens": 7941615051,
        "cache_write_tokens": 143180609,
        "total_tokens": 8378070138,
        "cost_usd": 7504.5993
      },
      {
        "month": "2026-06",
        "sessions": 519,
        "input_tokens": 1806827837,
        "output_tokens": 55126096,
        "cache_read_tokens": 16602722047,
        "cache_write_tokens": 356361272,
        "total_tokens": 18821037252,
        "cost_usd": 12272.4768
      },
      {
        "month": "2026-05",
        "sessions": 739,
        "input_tokens": 275558827,
        "output_tokens": 20200009,
        "cache_read_tokens": 2458477544,
        "cache_write_tokens": 64462836,
        "total_tokens": 2818699216,
        "cost_usd": 2391.3362
      }
    ],
    "models": [
      {
        "model": "claude-opus-4-8",
        "sessions": 49,
        "total_tokens": 15350110742,
        "cost_usd": 10998.7078
      },
      {
        "model": "claude-opus-5",
        "sessions": 57,
        "total_tokens": 9766636384,
        "cost_usd": 5978.7952
      },
      {
        "model": "claude-code",
        "sessions": 7,
        "total_tokens": 5511397017,
        "cost_usd": 735.4627
      },
      {
        "model": "gpt-5.5",
        "sessions": 282,
        "total_tokens": 2578600314,
        "cost_usd": 4525.4664
      },
      {
        "model": "claude-fable-5",
        "sessions": 13,
        "total_tokens": 2186249495,
        "cost_usd": 3116.5281
      },
      {
        "model": "claude-opus-4-7",
        "sessions": 87,
        "total_tokens": 1024330898,
        "cost_usd": 1234.1318
      }
    ],
    "agents": [
      {
        "sessions": 140,
        "input_tokens": 4668048,
        "output_tokens": 109288627,
        "cache_read_tokens": 33943066040,
        "cache_write_tokens": 631912029,
        "total_tokens": 34688934744,
        "cost_usd": 21921.9762,
        "credits": 0.0,
        "agent": "Claude Code",
        "sources": [
          "Claude Code"
        ]
      },
      {
        "sessions": 59,
        "input_tokens": 2117096535,
        "output_tokens": 5872918,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 2122969453,
        "cost_usd": 4292.9225,
        "credits": 0.0,
        "agent": "Codex",
        "sources": [
          "Codex"
        ]
      },
      {
        "sessions": 1805,
        "input_tokens": 290156787,
        "output_tokens": 10888150,
        "cache_read_tokens": 1387072739,
        "cache_write_tokens": 3912473,
        "total_tokens": 1692030149,
        "cost_usd": 1023.1785,
        "credits": 0.0,
        "agent": "Hermes",
        "sources": [
          "Hermes/Feishu Codex Relay",
          "Hermes/cli",
          "Hermes/cron",
          "Hermes/feishu"
        ]
      },
      {
        "sessions": 4,
        "input_tokens": 94005,
        "output_tokens": 83416,
        "cache_read_tokens": 4452736,
        "cache_write_tokens": 0,
        "total_tokens": 4630157,
        "cost_usd": 0.2374,
        "credits": 0.0,
        "agent": "DeepSeek Harness",
        "sources": [
          "DeepSeek Harness"
        ]
      },
      {
        "sessions": 26,
        "input_tokens": 3056966,
        "output_tokens": 46231,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 3103197,
        "cost_usd": 2.3978,
        "credits": 0.0,
        "agent": "Ivyea Agent",
        "sources": [
          "Ivyea Agent"
        ]
      },
      {
        "sessions": 2,
        "input_tokens": 11464,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 11464,
        "cost_usd": 0.0115,
        "credits": 0.128151,
        "agent": "Kiro",
        "sources": [
          "Kiro CLI estimate"
        ]
      }
    ],
    "today_agents": [
      {
        "sessions": 3,
        "input_tokens": 1582,
        "output_tokens": 789975,
        "cache_read_tokens": 139934767,
        "cache_write_tokens": 2563077,
        "total_tokens": 143289401,
        "cost_usd": 105.7439,
        "credits": 0.0,
        "agent": "Claude Code",
        "sources": [
          "Claude Code"
        ]
      }
    ],
    "coverage": [
      {
        "source": "Hermes",
        "path": "/root/.hermes/state.db",
        "status": "included",
        "sessions": 1783,
        "total_tokens": 1681975008,
        "credits": 0.0
      },
      {
        "source": "Kiro Gateway",
        "path": "(unconfigured)",
        "status": "missing",
        "sessions": 0,
        "total_tokens": 0,
        "credits": 0.0
      },
      {
        "source": "Kiro CLI sessions",
        "path": "/root/.kiro/sessions/cli",
        "status": "estimated-from-context-usage",
        "sessions": 2,
        "total_tokens": 11464,
        "credits": 0.128151
      },
      {
        "source": "Codex",
        "path": "/root/.codex/state_5.sqlite",
        "status": "included",
        "sessions": 51,
        "total_tokens": 1092573972,
        "credits": 0.0
      },
      {
        "source": "Hermes/Feishu Codex Relay",
        "path": "/root/feishu-codex-relay/.codex-home/state_5.sqlite",
        "status": "included",
        "sessions": 22,
        "total_tokens": 10055141,
        "credits": 0.0
      },
      {
        "source": "Claude Code",
        "path": "/root/.claude/projects",
        "status": "included",
        "sessions": 76,
        "total_tokens": 10644841336,
        "credits": 0.0
      },
      {
        "source": "Ivyea Agent",
        "path": "/root/.ivyea/sessions",
        "status": "included",
        "sessions": 26,
        "total_tokens": 3103197,
        "credits": 0.0
      },
      {
        "source": "DeepSeek Harness",
        "path": "/root/.dsh/sessions",
        "status": "included",
        "sessions": 4,
        "total_tokens": 4630157,
        "credits": 0.0
      },
      {
        "source": "Claude Code (归档)",
        "path": "token_archive.sqlite3",
        "status": "from-archive",
        "sessions": 0,
        "total_tokens": 24044093408,
        "credits": 0
      },
      {
        "source": "Codex (归档)",
        "path": "token_archive.sqlite3",
        "status": "from-archive",
        "sessions": 0,
        "total_tokens": 1030395481,
        "credits": 0
      }
    ],
    "timezone": "Asia/Shanghai"
  }],
  ["/news/dates", { dates: ["2026-08-17"], latest: "2026-08-17" }],
  ["/news/day", { date: "2026-08-17", items: [], generated_at: "", summary: "" }],
  ["/deep-analysis/history", { items: [] }],
  ["/help/docs", { docs: [
    { name: "usage", title: "使用手册" },
    { name: "config", title: "配置说明" },
    { name: "troubleshooting", title: "故障排查" },
  ] }],
  ["/help/doc/", (url: string) => ({
    markdown: `# ${url.split("/").pop()}\n\n这是验证台里的示例文档正文。\n\n- 一条\n- 两条\n`,
  })],
  ["/settings/health", () => {
    const tier = Number(new URLSearchParams(location.search).get("vt") || 3) as 0 | 1 | 2 | 3;
    const label = { 1: "主脑直读", 2: "视觉旁路 · Qwen/Qwen3-VL-30B-A3B-Instruct",
                    3: "本地 CV 度量（OCR：RapidOCR (ONNX) 1.4.4）", 0: "无视觉能力" }[tier];
    const detail = {
      1: "主脑直读：主脑模型自带视觉，全部图片分析可用",
      2: "视觉旁路：主脑不支持图片，已由独立视觉模型代读，全部图片分析可用",
      3: "本地 CV 度量：未配置视觉模型，图片走本地量化——合规/比例/主体占比/配色/图上文字照常分析；版式逆向与审美判断需配置一个支持视觉的模型",
      0: "不可用：IvyeaAgent 未连接且未配置视觉模型（影响 Listing 图片识别 / 视觉 Skill）",
    }[tier];
    return {
      version: { ok: true, detail: "1.6.1" },
      ivyea_agent: { ok: true, detail: "127.0.0.1:8765" },
      apimart: { ok: false, detail: "未配置" },
      sorftime: { ok: true, detail: "已配置" },
      imgflow: { ok: false, detail: "未配置" },
      ollama: { ok: false, detail: "未安装" },
      brain_root: { ok: true, detail: "~/.ivyea/knowledge" },
      openai: { ok: false, detail: "未配置" },
      ai_chain: {
        text: { ok: true, detail: "至少一个文本 AI 可用" },
        global_fallback: { ok: true, detail: "已配置" },
        vision: { ok: tier > 0, tier, tier_label: label, detail },
        chain_order: "ivyea-agent, deepseek, assistant",
      },
      runners: {
        hermes: { ok: false, detail: "未安装" },
        codex: { ok: false, detail: "未安装" },
        claude: { ok: false, detail: "未安装" },
      },
    };
  }],
  ["/health", { version: "1.6.1" }],
];

function match(url: string): Canned {
  const hit = ROUTES
    .filter(([p]) => url.startsWith(p))
    .sort((a, b) => b[0].length - a[0].length)[0];
  if (!hit) return {};
  return typeof hit[1] === "function" ? hit[1](url) : hit[1];
}

/** 装上假适配器。必须在 render 之前调用。 */
export function installMockApi(): void {
  const reply = (config: { url?: string; baseURL?: string }) => {
    // 用 baseURL + url 去匹配：Listing 等板块各自 axios.create 了实例，
    // 它们的 config.url 是 "/projects" 这种**相对自己 baseURL** 的短路径，
    // 只看 url 会和别的板块撞名。
    const base = (config.baseURL || "/api").replace(/^\/api/, "");
    const full = base + (config.url || "");
    return {
      data: match(full || config.url || ""),
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    } as never;
  };

  api.defaults.adapter = async (config) => reply(config);
  // 各板块自建的 axios 实例（listing/api.ts 就是一个）不共享上面那个 adapter，
  // 但会在发请求时回落到全局默认值——不设这个，整个 Listing 板块在验证台里
  // 是打不开的。
  axios.defaults.adapter = async (config) => reply(config);

  // ?agentdown=1 —— 装成 IvyeaAgent 没起来，用来验任务台的兜底通道。
  // AI 问答那一页并进任务台后，"agent 掉线还能纯聊"这条退路就只剩这一个入口了，
  // 它坏没坏在真实页面上验不到就等于没验。
  const agentDown = new URLSearchParams(location.search).get("agentdown") === "1";

  /** 一段真的 SSE —— 兜底通道读的是流，喂 JSON 它一个字也解不出来。 */
  const sse = (chunks: string[]) => new Response(
    new ReadableStream({
      start(ctrl) {
        const enc = new TextEncoder();
        for (const t of chunks) {
          ctrl.enqueue(enc.encode(`data: ${JSON.stringify({ type: "token", text: t, provider: "deepseek" })}\n\n`));
        }
        ctrl.enqueue(enc.encode(`data: ${JSON.stringify({ type: "done", provider: "deepseek" })}\n\n`));
        ctrl.close();
      },
    }),
    { status: 200, headers: { "content-type": "text/event-stream" } },
  );

  /**
   * 一段**会跑一阵子**的 agent 流。运行态（活动行在转、思考流一句句冒出来）此前在
   * 验证台里根本复现不出来 —— 而任务台最容易被改坏的恰恰是这一段。
   * 节奏按真实一轮铺：先思考 ~6s，再一步工具，最后才吐正文。
   */
  const agentStream = () => {
    const enc = new TextEncoder();
    const beat = (ms: number) => new Promise((r) => setTimeout(r, ms));
    return new Response(new ReadableStream({
      async start(ctrl) {
        const send = (event: string, data: unknown) =>
          ctrl.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
        send("start", { session_id: "s-live", turn_id: "t-live", model: "deepseek-v4-pro",
                        approval: "none", read_only: true });
        // 上下文用量：真 agent 在第一个 token 之前就发一份，收尾再发一份（见
        // service.chat_stream）。验证台不铺这条，上下文进度条就永远验不到。
        send("context", { used: 9860, window: 128000, percent: 7.7, estimated: true,
                          model: "deepseek-v4-pro",
                          breakdown: { system: 1520, tools: 6910, messages: 1430 } });
        // Agent 自己排的计划：先播一份（对应 agent 的 todo_write → todos 事件），
        // 跑到一半再播一份把第一条划掉。状态坞的"接下来要干什么"读的就是它。
        send("step", { type: "step", id: "p1", seq: 0, phase: "plan", name: "todo_write",
                       status: "ok", ms: 12 });
        send("todos", { todos: [
          { content: "确认数据源与口径", status: "in_progress" },
          { content: "拉搜索词报表，找浪费最集中的词根", status: "pending" },
          { content: "给出否词与竞价的具体动作", status: "pending" },
        ] });
        // 两批思考、两批工具 —— 叙述的形状就是"想 → 做 → 想 → 做"，
        // 验证台不铺成这样就验不到执行叙述（上一版把整轮压成一行，正是因为
        // 假流里只有一批，看不出铺开之后会长成什么样）。
        const think = [
          "用户问的是广告花费为什么涨了。",
          "先确认口径：是同比还是环比，",
          "再看是点击涨了还是单次点击成本涨了。",
          "手里没有报表，得先查数据源。",
        ];
        for (const t of think) { await beat(1200); send("reasoning", { text: t }); }
        await beat(700);
        // 一批常规工具：界面上应该折成"搜索 2 次 · 读了 2 个文件 · 跑了 1 条命令"
        for (const [i, st] of [["grep", "搜索内容"], ["glob", "查找文件"],
                               ["read_file", "读取文件"], ["read_file", "读取文件"],
                               ["run_command", "执行命令"]].entries()) {
          send("step", { type: "step", id: "b" + i, seq: i, phase: "tool", name: st[0],
                         status: "running" });
          await beat(160);
          send("step", { type: "step", id: "b" + i, seq: i, phase: "tool", name: st[0],
                         status: "ok", ms: 160 });
        }
        await beat(500);
        for (const t of ["数据源确认了：只有 sorftime，没有广告报表拉取工具。",
                         "那就先用本地那份搜索词报表跑，缺的字段回头再补。"]) {
          await beat(900); send("reasoning", { text: t });
        }
        await beat(600);
        send("step", { type: "step", id: "s1", seq: 1, phase: "tool", name: "读取报表",
                       tool: "read_report", status: "running" });
        await beat(2200);
        send("step", { type: "step", id: "s1", seq: 1, phase: "tool", name: "读取报表",
                       tool: "read_report", status: "ok", ms: 2200 });
        send("todos", { todos: [
          { content: "确认数据源与口径", status: "completed" },
          { content: "拉搜索词报表，找浪费最集中的词根", status: "in_progress" },
          { content: "给出否词与竞价的具体动作", status: "pending" },
        ] });
        await beat(600);
        for (const t of ["先说结论：", "这一周花费涨了 34%，", "其中 28% 来自单次点击成本上升。"]) {
          await beat(500); send("token", { text: t });
        }
        send("final", { session_id: "s-live", turn_id: "t-live",
                        usage: { prompt_tokens: 9860, completion_tokens: 178,
                                 prompt_cache_hit_tokens: 0, llm_ms: 2500 },
                        context: { used: 11240, window: 128000, percent: 8.8, estimated: true,
                                   model: "deepseek-v4-pro",
                                   breakdown: { system: 1520, tools: 6910, messages: 2810 } } });
        ctrl.close();
      },
    }), { status: 200, headers: { "content-type": "text/event-stream" } });
  };

  // MainLayout 的健康检查走的是裸 fetch，不经过 axios 实例。
  const realFetch = window.fetch.bind(window);
  window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.startsWith("/api/assistant/chat")) {
      return Promise.resolve(sse(["兜底通道", "答的这一段。"]));
    }
    if (url.startsWith("/api/ivyea-agent/chat/stream")) {
      // 发出去的 payload 留一份：附图这类字段"前端明明处理了、agent 却没收到"的
      // 错法不会有任何报错，只能靠量最后一次请求体验到。
      try { (window as any).__lastChatBody = JSON.parse(String(init?.body || "{}")); } catch { /* ignore */ }
      if (agentDown) return Promise.resolve(new Response("agent 未就绪", { status: 503 }));
      return Promise.resolve(agentStream());
    }
    if (url.startsWith("/api/")) {
      return Promise.resolve(new Response(JSON.stringify(match(url.slice(4))), {
        status: 200, headers: { "content-type": "application/json" },
      }));
    }
    return realFetch(input as RequestInfo, init);
  }) as typeof window.fetch;
}
