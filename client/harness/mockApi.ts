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
  api.defaults.adapter = async (config) => {
    const url = config.url || "";
    return {
      data: match(url),
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    } as never;
  };

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
