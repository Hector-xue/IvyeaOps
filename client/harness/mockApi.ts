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
  ["/auth/me", { username: "admin", role: "admin", permissions: [] }],
  ["/setup/status", { needs_setup: false, checks: {} }],
  ["/setup/update-info", {
    current: "1.6.1", latest: "1.7.0", update_available: true,
    release_url: "", platform_update_supported: true, detail: "发现新版本 1.7.0",
  }],
  ["/budget/summary", {
    known: true, total_tokens: 13_900_000, spend_usd: 42.7, enabled: false,
    limit_usd: 0, ratio: 0, level: "ok", age_seconds: 30,
  }],
  ["/ivyea-agent/console/sessions", {
    ok: true, sessions: SESSIONS, agent_available: true,
    workspaces: [{ name: "默认工作区", path: "/root", builtin: true }],
    total: 189, offset: 0, has_more: true,
  }],
  ["/ivyea-agent/console/presets", { ok: true, presets: [] }],
  // 会话详情：/ivyea-agent/chat/sessions/<id>。**必须比列表那条更长**才会先命中
  // （match() 按前缀长度排序），否则打开一条会话拿到的是空列表，永远验不到会话态。
  ["/ivyea-agent/chat/sessions/", () => ({
    ok: true,
    session: {
      id: "s3", model: "deepseek-v4-pro", messages: TURNS,
      total_turns: 2, has_more: false,
    },
  })],
  ["/ivyea-agent/chat/sessions", { ok: true, sessions: [] }],
  ["/ivyea-agent/status", { ok: true, model: "deepseek-v4-pro", ready: true }],
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

  // MainLayout 的健康检查走的是裸 fetch，不经过 axios 实例。
  const realFetch = window.fetch.bind(window);
  window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.startsWith("/api/")) {
      return Promise.resolve(new Response(JSON.stringify(match(url.slice(4))), {
        status: 200, headers: { "content-type": "application/json" },
      }));
    }
    return realFetch(input as RequestInfo, init);
  }) as typeof window.fetch;
}
