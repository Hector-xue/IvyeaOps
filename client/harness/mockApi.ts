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

/**
 * 终端里敲 `ivyea chat` 开的会话。形态和网页开的**不一样**，所以单列而不是
 * 改上面那批的 source：它们在 ops 的索引表里没有行（`indexed:false`、
 * `owner`/`workspace` 都是空），来源是服务端按 agent 给的 origin 现推的，
 * 另外多一个 `cwd`。少验任何一条，左栏就可能在"没有归属"这条路上出空白。
 */
// 注意 `title` **不能留空**：服务端是 `title = meta.title or preview or sid`，
// 未登记的会话拿首句摘要顶上，永远送不出空标题。留空验出来的是一条现实里不存在
// 的空白行（第一版 fixture 就是这么错的，截图里那条终端会话没有标题）。
const CLI_SESSIONS = [
  { id: "c1", title: "把这个仓库的测试跑一遍", preview: "把这个仓库的测试跑一遍", turns: 6,
    updated: Math.floor((now - 5 * 3600_000) / 1000),
    workspace: "", owner: "", source: "cli", indexed: false, cwd: "/root/ivyea-agent" },
  { id: "c2", title: "改名过的终端会话", preview: "看下 nginx 配置", turns: 3,
    updated: Math.floor((now - 27 * 3600_000) / 1000),
    workspace: "", owner: "admin", source: "cli", indexed: true, cwd: "/etc/nginx" },
];

/** 一轮真实形态的问答：有执行过程、有 markdown 正文、有表格和代码块。 */
const TURNS = [
  // 带附图的一轮：存档里留下的是 agent 注入的 `[用户附图 …]` 段落 + 原图句柄。
  // 气泡里要**只显示那句问话 + 缩略图**，代读的文字一个字都不该露出来。
  { role: "user", content: "这张图里面是什么？\n\n[用户附图 —— 视觉模型代读的内容]\n"
      + "本轮用户上传了 1 张图。图片本体不在你的上下文里，下面是视觉模型逐张读出的内容。\n"
      + "第 1 张（代读模型 qwen-vl、原图句柄 ivyea-ref://0000000000000abcd）：\n一张露营椅的主图。" },
  { role: "assistant", content: "是一张露营椅的主图（图由视觉模型代读成文字后交给我）。" },
  // Agent 用 show_image 夹进回答里的图。地址是**站内相对路径且带扩展名** ——
  // 两件事都要验：safeUrl 只放行 http(s)/data:/「/」开头，而裸链接要不要当成图
  // 是按扩展名判的（looksLikeImage）。所以显式 `![]()` 和裸链接两种写法都摆上，
  // 模型不会每次都规规矩矩写成图片语法。
  { role: "user", content: "把你截的那张图给我看看" },
  { role: "assistant", content:
      "截好了：\n\n![设置面板](/api/assistant/session-image/1a05105adda00002a949.png)\n\n"
      // 模型不总会写成 `![]()`，很常见的是把地址单甩一行。那条路走的是
      // soleImage/looksLikeImage，**按扩展名**判要不要当成图 —— 所以出口的 URL
      // 必须带 `.png` 这种后缀。（内联在句子中间的地址不会变成图，那是纯文本，
      // 验过了；工具的 note 因此明确要求模型写成图片语法。）
      + "再来一张：\n\n/api/assistant/session-image/1a05105adda00002a949.png" },
  // 带会话附件的一轮：存档里留下的是 agent 注入的 `[用户附件 …]` 段落。
  // 气泡里要**只显示那句问话 + 文件名小标**，抽出来的正文一个字都不该露出来
  // （几万字的 PDF 糊进气泡是这个功能最容易翻的车）。
  { role: "user", content: "这份报价单里最贵的三项是什么？\n\n[用户附件 —— 文档正文]\n"
      + "本轮用户随消息带了 2 份文档，正文抄在下面。**这些文档只属于这次对话，没有进知识库**。\n"
      + "第 1 份（2026Q1 报价单.pdf）：\n单价表：A 项 12 美元，B 项 340 美元……\n"
      + "第 2 份（供应商清单.xlsx）：\n深圳 XX 电子、东莞 YY 五金……" },
  { role: "assistant", content: "最贵的三项是 B 项（340 美元）、……（依据你这次带的报价单，它没有进知识库）。" },
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
      product_source_url: "https://example.com/p.jpg", slot: "main", role: "白底主图",
    },
    // 第二张**必须有成图**：单张下载（PNG / PSD）和成图墙只在 final_url 存在时才
    // 渲染，全是空图的方案里它们根本挂不上去，等于验不到。
    {
      shot_type: "hero_feature", layout: "poster_hero", product_presence: "hero",
      selling_point: "一箱装下两周行李", headline: "PACK TWO WEEKS", asset_mode: "generate",
      text_on_image: true, canvas: "2000x2000", evidence: "容量实测",
      render_prompt: "hero feature poster", slot: "sub1", role: "核心利益",
      final_url: "/art/bg.png", base_url: "/art/bg.png",
      product_source_url: "https://example.com/p.jpg",
      human_reviewed: true, render_qa: { ready: true, score: 92 },
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
  /* ── 运营驾驶舱 / 用户管理 ────────────────────────────────────────────────
     这几条以前没有，兜底的 `{}` 让 MarketTraffic 的 `items.filter` 直接把整页
     炸成「页面渲染出错」—— 于是驾驶舱和用户管理在验证台上根本画不出来，
     排版问题也就无从验起。字段名照抄 api/home.ts 的 interface，不是编的。 */
  ["/home/market-watch", [
    { id: "mw1", query: "wireless earbuds", marketplace: "US", data_source: "sorftime", label: "无线耳机", ts: 1780000000 },
    { id: "mw2", query: "yoga mat", marketplace: "US", data_source: "sorftime", label: "瑜伽垫", ts: 1780100000 },
    { id: "mw3", query: "desk lamp", marketplace: "DE", data_source: "sorftime", label: "台灯", ts: 1780200000 },
  ]],
  ["/home/market-series", (() => {
    // MarketSeries 的字段是 market / own / competitor（见 api/home.ts）。
    const days = Array.from({ length: 30 }, (_, i) =>
      new Date(1780300000000 - (29 - i) * 86400000).toISOString().slice(0, 10));
    return {
      query: "wireless earbuds", marketplace: "US", data_source: "sorftime",
      market: days.map((day, i) => ({
        day,
        search_volume: 42000 + Math.round(Math.sin(i / 4) * 6000) + i * 180,
        total_sales: 8600 + Math.round(Math.cos(i / 5) * 900) + i * 40,
        avg_price: 26.5 + Math.sin(i / 7) * 1.8,
      })),
      own: days.map((day, i) => ({ day, value: 320 + Math.round(Math.sin(i / 3) * 40) + i * 3 })),
      competitor: days.map((day, i) => ({ day, value: 480 + Math.round(Math.cos(i / 3) * 55) + i * 2 })),
    };
  })()],
  ["/home/keywords", [
    { id: "k1", keyword: "wireless earbuds", marketplace: "US", data_source: "sorftime", label: "", ts: 1780000000, data: null, data_ts: null },
    { id: "k2", keyword: "bluetooth headphones", marketplace: "US", data_source: "sorftime", label: "", ts: 1780100000, data: null, data_ts: null },
  ]],
  ["/home/keyword-pulse", []],
  ["/home/watch-snapshots", []],
  ["/home/watch", [
    { id: "w1", asin: "B0CXXXX111", marketplace: "US", data_source: "sorftime", kind: "own", label: "自家主推", ts: 1780000000 },
    { id: "w2", asin: "B0CYYYY222", marketplace: "US", data_source: "sorftime", kind: "competitor", label: "竞品 A", ts: 1780100000 },
  ]],
  ["/home/alerts", [
    { asin: "B0CYYYY222", marketplace: "US", kind: "competitor", label: "竞品 A",
      metric: "price", from: 29.99, to: 24.99, diff: -5, ts: 1780300000 },
    { asin: "B0CXXXX111", marketplace: "US", kind: "own", label: "自家主推",
      metric: "bsr", from: 1820, to: 2540, diff: 720, ts: 1780310000 },
  ]],
  /* ── 驾驶舱 · 促销日历 / 广告看板 ────────────────────────────────────────
     字段名照抄 api/cockpit.ts 的 interface。倒计时用**相对现在**的时刻生成，
     否则钉死的时间戳过两天就全变成"已结束"，验证台上永远看不到红色的紧急态。 */
  ["/cockpit/promotions", (() => {
    const iso = (h: number) => new Date(now + h * 3600_000).toISOString();
    const local = (h: number) => new Date(now + h * 3600_000).toISOString().slice(0, 19).replace("T", " ");
    const mk = (o: Record<string, unknown>) => ({
      currency_icon: "£", status_raw: "ACTIVE", status_label: "进行中",
      country: "英国", tz: "Europe/London", discount: "",
      sales_amount: 0, sales_volume: 0, budget: null, cost: null,
      budget_used_pct: null, last_sync_time: iso(-2), sync_age_hours: 2,
      asins: [], asin_count: 0, seconds_to_start: -3600, ...o,
    });
    return {
      generated_at: iso(0), source: "lingxing",
      scope: { sids: [1863, 1872], store_count: 2, horizon_days: 30, include_ended: false },
      stores: [{ sid: 1863, name: "欧洲-UK", code: "UK", currency: "GBP" },
               { sid: 1872, name: "日本-JP", code: "JP", currency: "JPY" }],
      items: [
        mk({ id: "coupon:1863:c1", promotion_id: "c1", kind: "coupon", kind_label: "优惠券",
             name: "Save £2 on 折叠露营椅", sid: 1863, store: "欧洲-UK", marketplace: "UK",
             start_at: iso(-40), end_at: iso(3.2), start_local: local(-40), end_local: local(3.2),
             seconds_to_end: 3.2 * 3600, phase: "running", discount: "£2.00",
             budget: 1000, cost: 870, budget_used_pct: 87, sales_amount: 2140, sales_volume: 96,
             asin_count: 2, asins: [
               { asin: "B0CXXXX111", title: "折叠露营椅 便携带靠背", msku: "CH-001",
                 image: "", url: "https://www.amazon.co.uk/dp/B0CXXXX111", sales_price: 28.99, stock: 412 },
               { asin: "B0CXXXX112", title: "折叠露营椅 双人款", msku: "CH-002",
                 image: "", url: "https://www.amazon.co.uk/dp/B0CXXXX112", sales_price: 46.5, stock: 88 },
             ] }),
        mk({ id: "seckill:1872:s1", promotion_id: "s1", kind: "seckill", kind_label: "秒杀",
             name: "アウトドアチェア タイムセール", sid: 1872, store: "日本-JP", marketplace: "JP",
             currency_icon: "JP¥", tz: "Asia/Tokyo", country: "日本",
             start_at: iso(-6), end_at: iso(19), start_local: local(-6), end_local: local(19),
             seconds_to_end: 19 * 3600, phase: "running", type_label: "Lightning Deal",
             product_quantity: 3, sold_rate: 0.62, sales_amount: 184000, sales_volume: 41,
             asin_count: 1, asins: [{ asin: "B0JPPPP777", title: "アウトドアチェア 軽量", msku: "JP-CH-1",
               image: "", url: "https://www.amazon.co.jp/dp/B0JPPPP777", sales_price: 4480, stock: 156 }] }),
        mk({ id: "manage:1863:m1", promotion_id: "m1", kind: "manage", kind_label: "管理促销",
             name: "买二免一 · 秋季清仓", sid: 1863, store: "欧洲-UK", marketplace: "UK",
             status_raw: "APPROVED", status_label: "已通过·未开始",
             start_at: iso(52), end_at: iso(220), start_local: local(52), end_local: local(220),
             seconds_to_start: 52 * 3600, seconds_to_end: 220 * 3600, phase: "upcoming",
             product_quantity: 12 }),
        mk({ id: "vip_discount:1863:v1", promotion_id: "v1", kind: "vip_discount",
             kind_label: "会员折扣", name: "Prime 专享 15% OFF", sid: 1863, store: "欧洲-UK",
             marketplace: "UK", start_at: iso(-100), end_at: iso(96),
             start_local: local(-100), end_local: local(96), seconds_to_end: 96 * 3600,
             phase: "running", discount: "15%" }),
      ],
      summary: { total: 4, running: 3, upcoming: 1, ending_24h: 2, ending_72h: 2,
                 budget_risk: 1, with_asin: 2 },
      freshness: { known: true, stale: false, age_hours: 2,
                   hint: "促销数据由「LINGXING助手」浏览器插件同步。最近一次同步在 2 小时前。" },
      errors: [],
    };
  })()],
  ["/cockpit/ads", (() => {
    const m = (spend: number, sales: number, orders: number, clicks: number, impressions: number) => ({
      spend, sales, orders, clicks, impressions,
      acos: sales ? +(spend / sales).toFixed(4) : null,
      roas: spend ? +(sales / spend).toFixed(2) : null,
      ctr: impressions ? +(clicks / impressions).toFixed(4) : null,
      cvr: clicks ? +(orders / clicks).toFixed(4) : null,
      cpc: clicks ? +(spend / clicks).toFixed(2) : null,
    });
    const camp = (o: Record<string, unknown>) => ({
      marketplace: "UK", currency: "GBP", state: "enabled", serving_status: "DELIVERING",
      targeting_type: "manual", breakeven_acos: 0.4, target_acos: 0.28,
      prev: null, spend_change_pct: null, anomalies: [], ...o,
    });
    return {
      generated_at: new Date(now).toISOString(), source: "lingxing",
      scope: { sids: [1863, 1872], days: 7, store_count: 2,
               skipped: [{ sid: 1870, name: "欧洲-TR", reason: "该店未开通广告" },
                         { sid: 1871, name: "欧洲-PL", reason: "该店未开通广告" }] },
      totals: m(1284.4, 4630.2, 173, 2416, 148900),
      prev_totals: m(1102.0, 4890.0, 191, 2288, 151200),
      delta: { spend_pct: 16.6, sales_pct: -5.3, orders_pct: -9.4, acos_delta: 0.0524 },
      by_store: [
        { sid: 1863, store: "欧洲-UK", marketplace: "UK", currency: "GBP", ...m(902.1, 3410.5, 128, 1702, 101300),
          target: { margin: 0.4, breakeven_acos: 0.4, target_acos: 0.28,
                    note: "毛利率≈40%(店铺均值)，目标ACOS=28%(=0.7×毛利)；6 个活动用各自产品毛利单独定目标" } },
        { sid: 1872, store: "日本-JP", marketplace: "JP", currency: "JPY", ...m(382.3, 1219.7, 45, 714, 47600),
          target: { margin: 0.33, breakeven_acos: 0.33, target_acos: 0.23, note: "毛利率≈33%(店铺均值)，目标ACOS=23%" } },
      ],
      by_campaign: [
        camp({ sid: 1863, store: "欧洲-UK", campaign_id: "125876403477002", name: "绿植零号手动",
               daily_budget: 20, today_spend: 19.5, budget_used_pct: 97.5,
               ...m(412.6, 1680.0, 62, 806, 42100), acos_vs_target: -0.033, health: "good",
               spend_change_pct: 12.4,
               anomalies: [{ code: "ads.budget_capped", severity: "info", label: "今日预算见底",
                             detail: "今天已花掉日预算的 98%，接近停投。",
                             intent: { op_type: "campaign_budget", sid: 1863, target_id: "125876403477002",
                                       target_name: "绿植零号手动", cur_value: 20, new_value: 23,
                                       rationale: "今日预算见底，小幅提预算" } }] }),
        camp({ sid: 1863, store: "欧洲-UK", campaign_id: "125876403477010", name: "露营椅自动-宽泛",
               serving_status: "CAMPAIGN_OUT_OF_BUDGET", daily_budget: 10, today_spend: 10,
               budget_used_pct: 100, ...m(286.9, 402.1, 11, 512, 31800),
               acos_vs_target: 0.433, health: "bad", spend_change_pct: 41.2,
               anomalies: [
                 { code: "ads.acos_breach", severity: "crit", label: "ACOS 破位",
                   detail: "ACOS 71% 已是目标 28% 的 2.5 倍，且高于盈亏平衡线 —— 每卖一单都在亏。",
                   intent: { op_type: "campaign_budget", sid: 1863, target_id: "125876403477010",
                             target_name: "露营椅自动-宽泛", cur_value: 10, new_value: 8.5,
                             rationale: "ACOS 71% 超目标 28%，降预算止血" } },
                 { code: "ads.out_of_budget", severity: "info", label: "预算已耗尽停投",
                   detail: "预算已花完，今天不再出广告。ACOS 未达目标，先别急着加预算。", intent: null },
               ] }),
        camp({ sid: 1872, store: "日本-JP", campaign_id: "225876403499001", name: "JP-手动-精准",
               marketplace: "JP", currency: "JPY", target_acos: 0.23, breakeven_acos: 0.33,
               daily_budget: 30, today_spend: 12.4, budget_used_pct: 41.3,
               ...m(382.3, 1219.7, 45, 714, 47600), acos_vs_target: 0.084, health: "watch" }),
        camp({ sid: 1863, store: "欧洲-UK", campaign_id: "125876403477033", name: "新品测试-广泛",
               daily_budget: 8, today_spend: 3.1, budget_used_pct: 38.8,
               ...m(202.6, 0, 0, 168, 12600), acos_vs_target: null, health: "bad",
               anomalies: [{ code: "ads.spend_no_sales", severity: "crit", label: "有花费零销售额",
                             detail: "168 次点击、花了 202.60，一单没出。",
                             intent: { op_type: "campaign_budget", sid: 1863, target_id: "125876403477033",
                                       target_name: "新品测试-广泛", cur_value: 8, new_value: 6.8,
                                       rationale: "168 次点击零转化，先降预算止血" } }] }),
      ],
      campaign_count: 4,
      trend: Array.from({ length: 7 }, (_, i) => ({
        date: new Date(now - (6 - i) * 86400000).toISOString().slice(0, 10),
        ...m(160 + i * 9, 600 + i * 22, 22 + i, 330 + i * 12, 20000 + i * 700),
      })),
      anomalies: [
        { code: "ads.spend_no_sales", severity: "crit", label: "有花费零销售额",
          detail: "168 次点击、花了 202.60，一单没出。", intent: null,
          sid: 1863, store: "欧洲-UK", campaign_id: "125876403477033", name: "新品测试-广泛" },
        { code: "ads.acos_breach", severity: "crit", label: "ACOS 破位",
          detail: "ACOS 71% 已是目标 28% 的 2.5 倍。", intent: null,
          sid: 1863, store: "欧洲-UK", campaign_id: "125876403477010", name: "露营椅自动-宽泛" },
        { code: "ads.budget_capped", severity: "info", label: "今日预算见底",
          detail: "今天已花掉日预算的 98%。", intent: null,
          sid: 1863, store: "欧洲-UK", campaign_id: "125876403477002", name: "绿植零号手动" },
      ],
      errors: [],
    };
  })()],
  ["/cockpit/ads/hourly", {
    sid: 1863, date: new Date(now).toISOString().slice(0, 10),
    series: [{ campaign_id: "125876403477002",
      points: Array.from({ length: 18 }, (_, h) => {
        const spend = 0.4 + Math.abs(Math.sin(h / 3)) * 1.9;
        return { hour: h, spend: +spend.toFixed(2), sales: +(spend * 3.8).toFixed(2),
                 clicks: 3 + (h % 5), impressions: 120 + h * 14, orders: h % 3,
                 acos: 0.26, cpc: 0.52,
                 spend_cumulative: +(Array.from({ length: h + 1 },
                   (_, k) => 0.4 + Math.abs(Math.sin(k / 3)) * 1.9)
                   .reduce((a, b) => a + b, 0)).toFixed(2) };
      }), spend_total: 19.5 }],
    truncated: false, max_campaigns: 12, errors: [],
  }],
  ["/cockpit/status", {
    lingxing_enabled: true, operate_active: false,
    fast_lane: { enabled: true, max_pct: 15, require_human: true },
    sync: { enabled: true, interval_minutes: 30, days: 7,
            last_started_at: ago(0.4), last_finished_at: ago(0.3),
            age_minutes: 18, last_result: { ok: true, seconds: 41.2 }, running: false },
    op_types: [{ key: "campaign_budget", label: "广告活动·预算/启停", category: "modify" }],
  }],
  // 领星配置页。**必须给 openapi_configured / master_enabled 都填 true** ——
  // 两者任一为假，LingXing 会直接落到"未启用"的空态，④ 驾驶舱预热那张卡就渲染不到。
  ["/lingxing/status", {
    master_enabled: true, operate_active: false, openapi_configured: true,
    ticket_counts: { awaiting_human: 2, reviewing: 1, executing: 0 },
  }],
  ["/lingxing/read/sellers", { rows: [
    { sid: 101, name: "Ivyea-US", marketplace: "US", currency: "USD", has_ads_setting: 1 },
    { sid: 102, name: "Ivyea-UK", marketplace: "UK", currency: "GBP", has_ads_setting: 1 },
    { sid: 103, name: "Ivyea-DE", marketplace: "DE", currency: "EUR", has_ads_setting: 0 },
  ], count: 3, cached: false, synced_at: new Date(now).toISOString() }],
  ["/lingxing/datasets", { datasets: [
    { key: "sellers", label: "店铺列表", group: "基础", params: [], columns: [
      { key: "sid", label: "SID" }, { key: "name", label: "店铺" },
      { key: "marketplace", label: "站点" }, { key: "currency", label: "币种" }] },
    { key: "sp_campaigns", label: "SP 广告活动", group: "广告", params: [
      { name: "sid", type: "int", required: true, label: "店铺SID" },
      { name: "length", type: "int", default: 200, label: "条数" },
      { name: "offset", type: "int", default: 0, label: "偏移" }], columns: [
      { key: "campaign_id", label: "活动ID" }, { key: "name", label: "活动名" },
      { key: "daily_budget", label: "日预算" }, { key: "state", label: "状态" }] },
  ] }],
  ["/lingxing/review/providers", {
    available: [
      { id: "ivyea-agent", label: "IvyeaAgent（内置）", ok: true },
      { id: "deepseek", label: "DeepSeek", ok: true },
      { id: "assistant", label: "全局兜底大模型", ok: true },
      { id: "hermes", label: "Hermes CLI", ok: false },
    ],
    personas: ["保守派", "增长派", "魔鬼代言人"],
    review_providers: "ivyea-agent,deepseek,assistant",
    analysis_provider: "ivyea-agent",
    rules_doc: "# 广告优化方法论\n\n- 数据不够不动手\n- 否词：≥15 次点击且 0 单\n",
    rules_doc_default: "# 广告优化方法论（默认）\n",
  }],
  ["/home/category-result", null],
  ["/home/category", []],
  ["/home/pulse", []],
  ["/auth/admin/users", [
    { id: 1, email: "admin@ivyea.com", role: "admin", status: "active",
      created_at: 1779000000, approved_at: 1779000000, position: "运营负责人",
      permissions: ["dashboard", "market", "playbook", "listing", "lingxing"] },
    { id: 2, email: "operator-zhang@ivyea.com", role: "user", status: "active",
      created_at: 1779500000, approved_at: 1779600000, position: "广告优化师",
      permissions: ["lingxing", "market"] },
    { id: 3, email: "newcomer@ivyea.com", role: "user", status: "pending",
      created_at: 1780400000, approved_at: null, position: "", permissions: [] },
    { id: 4, email: "left-the-company@ivyea.com", role: "user", status: "suspended",
      created_at: 1778000000, approved_at: 1778100000, position: "选品", permissions: ["market"] },
  ]],
  ["/auth/admin/permissions-catalog", {
    modules: [
      { key: "dashboard", label: "运营驾驶舱", sensitive: false },
      { key: "market", label: "市场调研", sensitive: false },
      { key: "playbook", label: "打法推荐", sensitive: false },
      { key: "listing", label: "Listing 工作台", sensitive: false },
      { key: "lingxing", label: "领星广告", sensitive: true },
      { key: "hub-settings", label: "系统配置", sensitive: true },
    ],
    positions: {
      "运营负责人": ["dashboard", "market", "playbook", "listing", "lingxing"],
      "广告优化师": ["lingxing", "market"],
      "选品": ["market"],
    },
  }],

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
  // 会话附件：抽正文、**不进知识库**。字段照抄 routers/ivyea_agent.session_file_extract。
  ["/ivyea-agent/session-files", {
    ok: true, name: "2026Q1 报价单.pdf", text: "单价表：A 项 12 美元……",
    chars: 8421, truncated: false, warnings: [],
  }],
  ["/ivyea-agent/console/sessions", {
    // 服务端是按 updated 倒序端出来的，这里也排一遍 —— 让终端会话夹在网页会话
    // 中间，而不是整齐地垫在最后。"夹在中间"才验得到来源小标在混排下的对齐。
    ok: true,
    sessions: [...SESSIONS, ...CLI_SESSIONS].sort((a, b) => b.updated - a.updated),
    agent_available: true,
    workspaces: [
      { name: "默认工作区", path: "/root", builtin: true, count: 189 },
      { name: "Amazon", path: "/root/amazon-image-workflow", builtin: false, count: 0 },
    ],
    total: 189, offset: 0, has_more: true,
  }],
  // 谁在跑（左栏的闪烁标记）。字段照抄 routers/ivyea_agent.chat_live_sessions。
  // 挑第 3 条（"测试"）是故意的：它不是列表里的第一条，验的是"标记跟着 id 走"
  // 而不是"第一行永远在闪"。
  ["/ivyea-agent/chat/live-sessions", {
    ok: true, available: true, sessions: [{ id: "s3", started_ms: Date.now() - 42_000, seq: 12 }],
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

  // ── 记忆面板 ──────────────────────────────────────────────────────────────
  // 假数据要**覆盖每一种视觉分支**，否则验证台看着好看、真实数据一来就露馅：
  // 推断/你说过的、不常用（已退出常驻目录）、已过期、待确认的观察次数。
  ["/ivyea-agent/memory/pending", {
    ok: true, total: 2,
    pending: [
      { name: "汇报偏好", category: "feedback", description: "从行为推断的：他似乎更想先看结论再看过程",
        body: "多次在长回答后要求「先说结论」。", source: "reflection", confidence: 0.49,
        uncertain: true, evidence: "3 条支撑 · 取自 2026-08-01~08-14 的 120 条经历",
        sightings: 2, promote_after: 3 },
      { name: "发版节奏", category: "user", description: "（反思建议修改一条你亲口定的规矩，需你确认）",
        body: "他习惯开发完就发版。", source: "reflection", confidence: 0.56, uncertain: true,
        evidence: "3 条支撑", sightings: 1, promote_after: 3 },
    ],
  }],
  ["/ivyea-agent/memory/list", {
    ok: true, total: 4,
    entries: [
      { name: "发版纪律", category: "feedback", description: "未经批准绝不 push / 发版",
        source: "user", confidence: 1.0, uncertain: false, updated: "2026-08-26",
        decay: { score: 0.91, in_index: true }, valid: true },
      { name: "领星广告方法论", category: "domain", description: "规则引擎 + LLM 复核，数据不够不动手",
        source: "user", confidence: 1.0, uncertain: false, updated: "2026-08-22",
        decay: { score: 0.74, in_index: true }, valid: true },
      { name: "汇报语言", category: "user", description: "推断：日常沟通用中文",
        source: "reflection", confidence: 0.62, uncertain: true, updated: "2026-08-10",
        decay: { score: 0.31, in_index: false }, valid: true },
      { name: "旧活动预算表", category: "project", description: "2026 上半年的预算安排",
        source: "manual", confidence: 1.0, uncertain: false, updated: "2026-06-30",
        decay: { score: 0.12, in_index: false }, valid: false },
    ],
  }],
  ["/ivyea-agent/memory/core", {
    ok: true, limit: 4000,
    blocks: [
      { block: "user", file: "USER.md", hint: "关于用户本人的长期事实：身份、角色、偏好、说话/汇报方式、绝对红线。",
        text: "# 用户画像（USER.md）\n\n- [2026-08-20] 汇报一律用中文，先结论后过程。\n- [2026-08-22] 未经我明确批准，绝不 push / 发版。\n" },
      { block: "agents", file: "AGENTS.md", hint: "账户运营打法与边界：目标 ACoS、保护词、否词/调价阈值、禁区。",
        text: "# 账户运营指令（AGENTS.md）\n\n- [2026-08-18] 品牌词永远不否。\n- [2026-08-18] 单次调价不超过 15%，冷却 7 天。\n" },
    ],
  }],
  ["/ivyea-agent/memory/stats", {
    ok: true, running: false,
    store: { total: 4, by_category: { feedback: 1, domain: 1, user: 1, project: 1 },
             dir: "/root/.ivyea/memory", index_chars: 1322 },
    core: { user: { file: "USER.md", exists: true, chars: 128, limit: 4000, crowded: false },
            agents: { file: "AGENTS.md", exists: true, chars: 96, limit: 4000, crowded: false } },
    reflect: { auto: true, last_reflect: "2026-08-27 20:35", pending_episodes: 5, threshold: 8, ready: false },
    episodes: { indexed: 427, tokenized: 427, db: "/root/.ivyea/memory.db" },
  }],
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
  // **简介长短必须拉开差距。** 卡片"有的高有的矮"只有在同一行里既有一句话的
  // 简介、又有三行的简介时才看得见；清一色一句话的假数据永远验不出这个问题。
  ["/skill-market/skills", { total: 6, items: [
    { slug: "ops/keyword-cluster", name: "关键词聚类", version: "1.0.0",
      summary: "把搜索词报表聚成可执行的词簇", author: "门道社区", installed: false },
    { slug: "ops/negative-guard", name: "否词护栏", version: "2.1.0",
      summary: "按点击、花费、转化三条线扫全部搜索词，挑出连续两周零单且花费超过阈值的低效词，生成可直接导入广告后台的否定精准/否定词组清单，并附上每一条的判定依据和回滚方式。",
      author: "门道社区", installed: true },
    { slug: "ops/bid-tuner", name: "竞价调优", version: "0.9.3",
      summary: "按 ACOS 目标推荐竞价步长", author: "Hector", installed: false },
    { slug: "ops/listing-audit", name: "Listing 体检", version: "1.4.2",
      summary: "标题、五点、A+、图片、后台关键词逐项对照类目 Top 10 做差距分析，输出优先级排序的整改清单。",
      author: "门道社区", installed: false },
    { slug: "ops/review-digest", name: "评论摘要", version: "1.0.1",
      summary: "把差评聚成产品问题清单", author: "门道社区", installed: false },
    { slug: "ops/season-forecast", name: "季节性预测", version: "3.0.0",
      summary: "按历史同期搜索量与出货量拟合季节曲线，给出未来 12 周的备货建议区间；数据不足 8 周时只给趋势方向，不给数值，避免拿噪声当预测。",
      author: "Hector", installed: false },
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
      // ?live=1 —— 装成"这条会话正跑着一轮"。真 agent 在会话详情里回这一行
      // （service.chat_session_detail 的 live），前端据此接进活轮日志。
      // 不铺这条，"切走再切回来能看到进度"这条链路在验证台里一次都验不到 ——
      // 而它恰恰是这次要修的那个毛病。
      ...(new URLSearchParams(location.search).get("live") === "1"
        ? { live: { running: true, seq: 3, started_ms: Date.now() - 42_000 } }
        : {}),
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
  // 订阅登录：**三种流程各留一个**（device / paste / token），还要留"已登录"和
  // "未登录"两种状态 —— 面板的按钮、徽标、流程面板都只在特定状态下才渲染得出来。
  ["/ivyea-agent/auth", {
    ok: true,
    providers: [
      { id: "anthropic-oauth", label: "Claude 订阅 OAuth", kind: "paste",
        status: "not-authenticated", ready: false,
        hint: "授权后页面会显示一段 `code#state`，整段复制粘回来。" },
      { id: "openai-codex", label: "OpenAI Codex OAuth", kind: "device",
        status: "authenticated+refresh", ready: true, source: "device-code",
        hint: "打开页面后输入下面的代码并确认授权，这里会自动完成。" },
      { id: "google-gemini-cli", label: "Gemini Code Assist OAuth", kind: "paste",
        status: "expired", ready: false,
        hint: "授权后浏览器会跳到一个打不开的 127.0.0.1 地址（正常现象）—— 把地址栏里那条完整 URL 复制粘回来。" },
      { id: "qwen-oauth", label: "Qwen OAuth / Portal", kind: "device",
        status: "not-authenticated", ready: false,
        hint: "在打开的页面上确认授权即可，这里会自动完成。" },
      { id: "copilot", label: "GitHub Copilot / GitHub Models", kind: "token",
        status: "configured:COPILOT_GITHUB_TOKEN", ready: true,
        hint: "填一个有 Copilot 权限的 GitHub Token。" },
    ],
  }],
  ["/ivyea-agent/auth/", (url: string) => {
    const parts = (url.split("/ivyea-agent/auth/")[1] || "").split("/");
    const pid = parts[0] || "";
    const action = (parts[1] || "").split("?")[0];
    if (action === "start") {
      if (pid === "qwen-oauth" || pid === "openai-codex") {
        return { ok: true, provider: pid, kind: "device", session: "sess-1",
                 user_code: "PR7X-NERO", verification_uri: "https://example.invalid/activate",
                 interval: 2, expires_in: 900, hint: "在打开的页面上确认授权即可。" };
      }
      if (pid === "copilot") {
        return { ok: true, provider: pid, kind: "token", session: "sess-1",
                 hint: "填一个有 Copilot 权限的 GitHub Token。" };
      }
      return { ok: true, provider: pid, kind: "paste", session: "sess-1",
               url: "https://example.invalid/oauth/authorize?code=true",
               expires_in: 900, hint: "授权后把回调内容整段粘回来。" };
    }
    if (action === "poll") return { ok: true, status: "pending", interval: 2, note: "" };
    return { ok: true };
  }],
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
      // ?nocred=1 —— 装成"agent 那边配好了、ops 这边空着"，专验那条红字提示
      ...(new URLSearchParams(location.search).get("nocred") === "1"
        ? { alert_app_id: "", alert_app_secret: "" }
        : { alert_app_id: "cli_demo0336427e785d", alert_app_secret: "demo-secret" }),
      alert_chat_id: "oc_demo7a0933a4af7e82980367a", alert_feishu_domain: "feishu",
      alert_webhook: "", alert_threshold: 80, alert_sustain: 5, alert_cooldown: 30,
      // 领星：凭证已配（secret 非空 = 界面显示"已配置，留空不改"）
      lingxing_openapi_host: "https://openapi.lingxing.com",
      lingxing_openapi_appid: "ak_demo_12345", lingxing_openapi_secret: "••••",
      lingxing_mcp_key: "", lingxing_enabled: true,
      lingxing_operate_require_human: true,
      lingxing_fast_lane_enabled: true, lingxing_fast_lane_max_pct: 15,
      lingxing_target_acos_factor: 0.7, lingxing_max_change_pct: 20,
      lingxing_bid_step_pct: 15, lingxing_neg_min_clicks: 15,
      lingxing_cooldown_days: 7, lingxing_opt_window_days: 30,
      lingxing_scope_stores: "", lingxing_custom_models: "[]",
      // 驾驶舱预热：开着，且 /cockpit/status 的 sync 段与这里一致
      cockpit_sync_enabled: true, cockpit_sync_minutes: 30, cockpit_sync_days: 7,
    },
    secret_keys: ["apimart_key", "ivyea_agent_api_key", "vision_api_key", "alert_app_secret",
                  "alert_webhook"],
  }],
  // 亚马逊官方 API。**故意给"凭据配好了、站点只配了一半"**：
  // 全绿的话，未配置态和逐步自检那两条分支根本渲染不到。
  ["/settings/amazon", () => ({
    ok: true, configured: true, ads_configured: false, ads_uses_own_app: false,
    region: "eu", spapi_host: "https://sellingpartnerapi-eu.amazon.com",
    ads_host: "https://advertising-api-eu.amazon.com",
    seller_id: "A23SU2M9XL8R0O",
    marketplaces: [
      { sid: "1863", marketplace_id: "A1F83G8C2ARO7P", name: "欧洲-UK",
        country: "UK", region: "eu", ads_profile_id: "3344556677" },
      { sid: "1864", marketplace_id: "APJ6JRA9NG5V4", name: "欧洲-IT",
        country: "IT", region: "eu", ads_profile_id: "" },
    ],
    marketplace_count: 2, with_ads_profile: 1,
    catalog: [
      { marketplace_id: "ATVPDKIKX0DER", country: "US", region: "na" },
      { marketplace_id: "A1F83G8C2ARO7P", country: "UK", region: "eu" },
      { marketplace_id: "APJ6JRA9NG5V4", country: "IT", region: "eu" },
      { marketplace_id: "A1VC38T7YXB528", country: "JP", region: "fe" },
    ],
  })],
  // 飞书配置向导。**故意给一个"配了一半"的状态**：全绿的话，未完成步骤和
  // 能力矩阵里的 blockers 那两条分支根本渲染不到，等于没验。
  ["/settings/feishu", () => ({
    ok: true,
    app: { app_id: "cli_demo0336427e785d", app_id_masked: "cli_de…5d", configured: true,
           secret_configured: true, domain: "feishu", source: "env" },
    chat: { chat_id: "oc_demo7a0933a4af7e82980367a", configured: true },
    webhook: { configured: false, url_masked: "" },
    gates: { allowed_senders: [], allowed_chats: [] },
    // ?relay=off —— 装机第一天的样子：接收端没装、触发器没启用。
    // 全绿的话那两个「一键安装」按钮根本渲染不到，等于没验。
    relay: new URLSearchParams(location.search).get("relay") === "off"
      ? { state: "inactive", running: false, sdk: false, can_install: true,
          detail: "接收端未运行 —— 先装 SDK：pip install \"ivyea-agent[feishu]\"，再 `ivyea relay install`" }
      : { state: "active", running: true, sdk: true, detail: "ivyea-feishu-relay.service 运行中" },
    patrol: {
      jobs: [{ name: "patrol-l1", task: "store_l1", enabled: true, every_minutes: 60,
               channel: "feishu_app", notify: true, scope: "all", sids: [], sid: "",
               last_run: 0 }],
      any_enabled: true, pushing_to_feishu: 1,
      defaults: {
        l1: { task: "store_l1", label: "实时层 L1", every_minutes: 60,
              desc: "库存断货 / 活动被暂停 / 预算被外部改动 / listing 上下架" },
        l2: { task: "store_l2", label: "日内层 L2", every_minutes: 720,
              desc: "当日花费突增 / 曝光归零 / 点击暴涨零转化" },
        daily: { task: "store_daily", label: "每日早报", every_minutes: 1440,
                 desc: "昨日指标 + 环比 + 待你决定的建议（带按钮）" },
        weekly: { task: "store_weekly", label: "每周周报", every_minutes: 10080,
                  desc: "本周 vs 上周 + 本周批了/执行了什么（只回顾，无按钮）" },
        monthly: { task: "store_monthly", label: "每月月报", every_minutes: 43200,
                   desc: "本月 vs 上月，同样只回顾" },
      },
      timer: new URLSearchParams(location.search).get("relay") === "off"
        ? { state: "inactive", running: false, can_install: true,
            detail: "ivyea-schedule.timer 未启用 —— 注册的 3 个任务不会被触发" }
        : { state: "active", running: true, detail: "ivyea-schedule.timer 运行中（3 个任务在册）" },
    },
    probe: { ran: false },
    channels: {
      text_alert: { ready: true, blockers: [], note: "" },
      cards: { ready: true, blockers: [], note: "" },
      approval: { ready: false, blockers: ["审批白名单为空 —— 按安全默认，此时没有人能点按钮"], note: "" },
      chat: new URLSearchParams(location.search).get("relay") === "off"
        ? { ready: false, blockers: ["relay 未运行"], note: "" }
        : { ready: true, blockers: [], note: "" },
      patrol_push: new URLSearchParams(location.search).get("relay") === "off"
        ? { ready: false, blockers: ["触发器未启用，注册的巡检任务不会被触发"], note: "" }
        : { ready: true, blockers: [], note: "" },
    },
    steps: [
      { key: "app", title: "创建自建应用，填 App ID / App Secret", done: true, detail: "cli_de…5d", hint: "" },
      { key: "permission", title: "开权限并发布版本", done: false, detail: "未验证", hint: "需要 im:message 等权限" },
      { key: "chat", title: "选一个接收会话（群）", done: true, detail: "oc_demo7a0933a4af7e82980367a", hint: "" },
      { key: "whitelist", title: "指定谁能点审批按钮", done: false, detail: "空 —— 按安全默认，没有人能点", hint: "" },
      ...(new URLSearchParams(location.search).get("relay") === "off"
        ? [{ key: "relay", title: "启动长连接接收端 relay", done: false,
             detail: "接收端未运行", hint: "不装的话卡片按钮点了不会有任何反应" },
           { key: "patrol", title: "打开店铺巡检并推到飞书", done: false,
             detail: "触发器未启用", hint: "" }]
        : [{ key: "relay", title: "启动长连接接收端 relay", done: true,
             detail: "ivyea-feishu-relay.service 运行中", hint: "" },
           { key: "patrol", title: "打开店铺巡检并推到飞书", done: true,
             detail: "1 条任务在推", hint: "" }]),
      { key: "test", title: "发一条测试消息确认真能收到", done: false, detail: "未测试", hint: "" },
    ],
    last_test_at: 0,
  })],
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
    // 采集只拿到 1 张白底图 + 本机没浏览器 —— 这正是 ProductStep 那条兜底提示的
    // 触发条件（Docker 按钮拆掉后，这里要能验出取代它的是重试 + 浏览器提示）。
    scrape_data: JSON.stringify({
      title: "便携旅行收纳箱", bullets: ["硬壳防摔", "可扩容 8L"], images: [],
      reference_images: ["/art/bg.png"], scrape_source: "sorftime",
      full_images_available: false, browser_available: false,
    }),
    analysis_data: null, copy_result: null, copy_job_id: null,
    creative_sets: JSON.stringify({ gallery: DEMO_PLAN }),
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
  // 字段名照抄 client.ts 的 BrainFileItem —— 这里原来写的是 `title`，组件读的是
  // `name`，于是文件名整列都是空的（渲染出来只剩一个删除叉），漏验了一整块。
  // **名字必须掺进真实的长条目**：手机端窄栏里"长到放不下的标题/路径"才是会撑破
  // 布局的那一类，清一色四个字的短名字什么都验不出来。
  ["/brain/files", { root: "~/brain", total: 5, files: [
    { path: "amazon/ads/negative.md", name: "否词护栏", category: "ads", size: 2048, mtime: 1755300000, summary: "低效搜索词批量否定" },
    { path: "amazon/ads/sp-structure-cold-start-playbook.md", name: "SP 广告投放结构与冷启动打法（美国站 2026 版）", category: "ads", size: 8192, mtime: 1755301000, summary: "冷启动分阶段预算与竞价" },
    { path: "amazon/listing/main-image.md", name: "主图合规", category: "listing", size: 1024, mtime: 1755310000, summary: "主图白底与占比要求" },
    { path: "amazon/policy/vat.md", name: "VAT 税务", category: "policy", size: 4096, mtime: 1755320000, summary: "欧洲站 VAT 申报" },
    { path: "amazon/policy/eu-vat-jct-registration-and-filing-checklist.md", name: "欧盟 VAT / 日本 JCT 注册与申报核对清单", category: "policy", size: 6144, mtime: 1755321000, summary: "跨站点税务注册与申报" },
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
      {"day": "2026-08-21", "sessions": 6, "input_tokens": 5902, "output_tokens": 2373095, "cache_read_tokens": 746586632, "cache_write_tokens": 5336637, "total_tokens": 754302266, "cost_usd": 543.0976},
      {"day": "2026-08-20", "sessions": 4, "input_tokens": 22214, "output_tokens": 885569, "cache_read_tokens": 327830563, "cache_write_tokens": 1697365, "total_tokens": 330435711, "cost_usd": 237.9137},
      {"day": "2026-08-19", "sessions": 1, "input_tokens": 1016, "output_tokens": 306921, "cache_read_tokens": 127520185, "cache_write_tokens": 729204, "total_tokens": 128557326, "cost_usd": 92.5613},
      {"day": "2026-08-18", "sessions": 6, "input_tokens": 106483, "output_tokens": 1678110, "cache_read_tokens": 1024843147, "cache_write_tokens": 5791556, "total_tokens": 1032419296, "cost_usd": 743.3419},
      {"day": "2026-08-17", "sessions": 1, "input_tokens": 670, "output_tokens": 377509, "cache_read_tokens": 41812978, "cache_write_tokens": 1261216, "total_tokens": 43452373, "cost_usd": 31.2857},
      {"day": "2026-08-16", "sessions": 6, "input_tokens": 73525, "output_tokens": 1949377, "cache_read_tokens": 981840601, "cache_write_tokens": 4567539, "total_tokens": 988431042, "cost_usd": 711.6704},
      {"day": "2026-08-15", "sessions": 2, "input_tokens": 1488, "output_tokens": 657583, "cache_read_tokens": 140928349, "cache_write_tokens": 2109447, "total_tokens": 143696867, "cost_usd": 103.4617},
      {"day": "2026-08-14", "sessions": 1, "input_tokens": 1260, "output_tokens": 427344, "cache_read_tokens": 182176767, "cache_write_tokens": 958079, "total_tokens": 183563450, "cost_usd": 132.1657},
      {"day": "2026-08-13", "sessions": 1, "input_tokens": 81840, "output_tokens": 837736, "cache_read_tokens": 412802027, "cache_write_tokens": 3694725, "total_tokens": 417416328, "cost_usd": 300.5398},
      {"day": "2026-08-12", "sessions": 1, "input_tokens": 81155, "output_tokens": 628242, "cache_read_tokens": 207330536, "cache_write_tokens": 2225344, "total_tokens": 210265277, "cost_usd": 151.391},
      {"day": "2026-08-11", "sessions": 1, "input_tokens": 81155, "output_tokens": 628242, "cache_read_tokens": 207330536, "cache_write_tokens": 2225344, "total_tokens": 210265277, "cost_usd": 151.391},
      {"day": "2026-08-10", "sessions": 4, "input_tokens": 425, "output_tokens": 112057, "cache_read_tokens": 10971154, "cache_write_tokens": 827631, "total_tokens": 11911267, "cost_usd": 8.5761},
      {"day": "2026-08-09", "sessions": 1, "input_tokens": 663, "output_tokens": 25698, "cache_read_tokens": 2494257, "cache_write_tokens": 115923, "total_tokens": 2636541, "cost_usd": 1.8983},
      {"day": "2026-08-08", "sessions": 1, "input_tokens": 4431, "output_tokens": 1925938, "cache_read_tokens": 1036208062, "cache_write_tokens": 14692718, "total_tokens": 1052831149, "cost_usd": 758.0384},
      {"day": "2026-08-07", "sessions": 3, "input_tokens": 18125, "output_tokens": 44118, "cache_read_tokens": 20396244, "cache_write_tokens": 1209677, "total_tokens": 21668164, "cost_usd": 15.6011},
      {"day": "2026-08-06", "sessions": 11, "input_tokens": 341802, "output_tokens": 807605, "cache_read_tokens": 260464933, "cache_write_tokens": 3210300, "total_tokens": 264824640, "cost_usd": 190.6737},
      {"day": "2026-08-05", "sessions": 14, "input_tokens": 266170, "output_tokens": 963489, "cache_read_tokens": 415717261, "cache_write_tokens": 4933958, "total_tokens": 421880878, "cost_usd": 303.7542},
      {"day": "2026-08-04", "sessions": 12, "input_tokens": 109904, "output_tokens": 604043, "cache_read_tokens": 287351952, "cache_write_tokens": 1338776, "total_tokens": 289404675, "cost_usd": 208.3714},
      {"day": "2026-08-03", "sessions": 13, "input_tokens": 192236, "output_tokens": 254052, "cache_read_tokens": 14644453, "cache_write_tokens": 565375, "total_tokens": 15656116, "cost_usd": 11.2724},
      {"day": "2026-08-02", "sessions": 2, "input_tokens": 16414, "output_tokens": 309374, "cache_read_tokens": 109430411, "cache_write_tokens": 616956, "total_tokens": 110373155, "cost_usd": 79.4687},
      {"day": "2026-08-01", "sessions": 3, "input_tokens": 49039, "output_tokens": 761233, "cache_read_tokens": 315627623, "cache_write_tokens": 1430191, "total_tokens": 317868086, "cost_usd": 228.865},
      {"day": "2026-07-31", "sessions": 6, "input_tokens": 142913, "output_tokens": 792344, "cache_read_tokens": 362339458, "cache_write_tokens": 2467060, "total_tokens": 365741775, "cost_usd": 263.3341},
      {"day": "2026-07-30", "sessions": 7, "input_tokens": 172742, "output_tokens": 56936, "cache_read_tokens": 9773963, "cache_write_tokens": 748844, "total_tokens": 10752485, "cost_usd": 7.7418},
      {"day": "2026-07-29", "sessions": 6, "input_tokens": 182339, "output_tokens": 338999, "cache_read_tokens": 122339600, "cache_write_tokens": 693234, "total_tokens": 123554172, "cost_usd": 88.959},
      {"day": "2026-07-28", "sessions": 8, "input_tokens": 308197, "output_tokens": 636543, "cache_read_tokens": 246393015, "cache_write_tokens": 4504724, "total_tokens": 251842479, "cost_usd": 181.3266},
      {"day": "2026-07-27", "sessions": 6, "input_tokens": 95979, "output_tokens": 1188015, "cache_read_tokens": 294188475, "cache_write_tokens": 5447559, "total_tokens": 300920028, "cost_usd": 216.6624},
      {"day": "2026-07-26", "sessions": 7, "input_tokens": 21685, "output_tokens": 604032, "cache_read_tokens": 76034342, "cache_write_tokens": 1902635, "total_tokens": 78562694, "cost_usd": 56.5651},
      {"day": "2026-07-25", "sessions": 6, "input_tokens": 66, "output_tokens": 17482, "cache_read_tokens": 1144435, "cache_write_tokens": 362498, "total_tokens": 1524481, "cost_usd": 1.0976},
      {"day": "2026-07-24", "sessions": 6, "input_tokens": 77106, "output_tokens": 302372, "cache_read_tokens": 32595736, "cache_write_tokens": 1265007, "total_tokens": 34240221, "cost_usd": 24.653},
      {"day": "2026-07-23", "sessions": 10, "input_tokens": 261374, "output_tokens": 986833, "cache_read_tokens": 202651166, "cache_write_tokens": 23120240, "total_tokens": 227019613, "cost_usd": 163.4541},
      {"day": "2026-07-22", "sessions": 6, "input_tokens": 159336, "output_tokens": 853679, "cache_read_tokens": 118475130, "cache_write_tokens": 2610592, "total_tokens": 122098737, "cost_usd": 87.9111},
      {"day": "2026-07-21", "sessions": 6, "input_tokens": 142258, "output_tokens": 354034, "cache_read_tokens": 59459568, "cache_write_tokens": 774110, "total_tokens": 60729970, "cost_usd": 43.7256},
      {"day": "2026-07-20", "sessions": 7, "input_tokens": 194573, "output_tokens": 2829110, "cache_read_tokens": 967371197, "cache_write_tokens": 14192691, "total_tokens": 984587571, "cost_usd": 708.9031},
      {"day": "2026-07-19", "sessions": 3, "input_tokens": 13939, "output_tokens": 970068, "cache_read_tokens": 139799764, "cache_write_tokens": 3913934, "total_tokens": 144697705, "cost_usd": 104.1823},
      {"day": "2026-07-18", "sessions": 4, "input_tokens": 36762, "output_tokens": 264428, "cache_read_tokens": 23117686, "cache_write_tokens": 1089380, "total_tokens": 24508256, "cost_usd": 17.6459},
      {"day": "2026-07-17", "sessions": 5, "input_tokens": 207710, "output_tokens": 184121, "cache_read_tokens": 13358555, "cache_write_tokens": 787604, "total_tokens": 14537990, "cost_usd": 10.4674},
      {"day": "2026-07-16", "sessions": 7, "input_tokens": 438447, "output_tokens": 624964, "cache_read_tokens": 85941267, "cache_write_tokens": 2349207, "total_tokens": 89353885, "cost_usd": 64.3348},
      {"day": "2026-07-15", "sessions": 3, "input_tokens": 41174, "output_tokens": 237225, "cache_read_tokens": 24613525, "cache_write_tokens": 604334, "total_tokens": 25496258, "cost_usd": 18.3573},
      {"day": "2026-07-14", "sessions": 7, "input_tokens": 155661, "output_tokens": 1568661, "cache_read_tokens": 349715616, "cache_write_tokens": 5362418, "total_tokens": 356802356, "cost_usd": 256.8977},
      {"day": "2026-07-13", "sessions": 16, "input_tokens": 148779, "output_tokens": 1376381, "cache_read_tokens": 427989275, "cache_write_tokens": 7377307, "total_tokens": 436891742, "cost_usd": 314.5621},
      {"day": "2026-07-12", "sessions": 4, "input_tokens": 4, "output_tokens": 6276, "cache_read_tokens": 14752, "cache_write_tokens": 29526, "total_tokens": 50558, "cost_usd": 0.0364},
      {"day": "2026-07-11", "sessions": 6, "input_tokens": 24373, "output_tokens": 18649, "cache_read_tokens": 2963078, "cache_write_tokens": 313649, "total_tokens": 3319749, "cost_usd": 2.3902},
      {"day": "2026-07-10", "sessions": 5, "input_tokens": 55258, "output_tokens": 926668, "cache_read_tokens": 256464432, "cache_write_tokens": 5889888, "total_tokens": 263336246, "cost_usd": 189.6021},
      {"day": "2026-07-09", "sessions": 4, "input_tokens": 49097, "output_tokens": 357156, "cache_read_tokens": 47528929, "cache_write_tokens": 709513, "total_tokens": 48644695, "cost_usd": 35.0242},
      {"day": "2026-07-08", "sessions": 8, "input_tokens": 212366, "output_tokens": 29647, "cache_read_tokens": 2105088, "cache_write_tokens": 0, "total_tokens": 2347101, "cost_usd": 1.6899},
      {"day": "2026-07-07", "sessions": 2, "input_tokens": 47030, "output_tokens": 178504, "cache_read_tokens": 13445402, "cache_write_tokens": 509392, "total_tokens": 14180328, "cost_usd": 10.2098},
      {"day": "2026-07-06", "sessions": 7, "input_tokens": 91587452, "output_tokens": 199602, "cache_read_tokens": 1306752, "cache_write_tokens": 0, "total_tokens": 93093806, "cost_usd": 67.0275},
      {"day": "2026-07-05", "sessions": 14, "input_tokens": 49814735, "output_tokens": 3618542, "cache_read_tokens": 924951516, "cache_write_tokens": 15816473, "total_tokens": 994201266, "cost_usd": 715.8249},
      {"day": "2026-07-04", "sessions": 5, "input_tokens": 241421, "output_tokens": 2120323, "cache_read_tokens": 579568550, "cache_write_tokens": 11950190, "total_tokens": 593880484, "cost_usd": 427.5939},
      {"day": "2026-07-03", "sessions": 6, "input_tokens": 133607, "output_tokens": 73661, "cache_read_tokens": 5554703, "cache_write_tokens": 508236, "total_tokens": 6270207, "cost_usd": 4.5145},
      {"day": "2026-07-02", "sessions": 2, "input_tokens": 276572, "output_tokens": 3940264, "cache_read_tokens": 1498044968, "cache_write_tokens": 15395562, "total_tokens": 1517657366, "cost_usd": 1092.7133},
      {"day": "2026-07-01", "sessions": 4, "input_tokens": 164647, "output_tokens": 818319, "cache_read_tokens": 167568731, "cache_write_tokens": 3055993, "total_tokens": 171607690, "cost_usd": 123.5575},
      {"day": "2026-06-30", "sessions": 4, "input_tokens": 102807281, "output_tokens": 883176, "cache_read_tokens": 177902738, "cache_write_tokens": 1319568, "total_tokens": 282912763, "cost_usd": 203.6972},
      {"day": "2026-06-29", "sessions": 6, "input_tokens": 167308921, "output_tokens": 3933450, "cache_read_tokens": 1410025200, "cache_write_tokens": 33390597, "total_tokens": 1614658168, "cost_usd": 1162.5539},
      {"day": "2026-06-28", "sessions": 2, "input_tokens": 95889737, "output_tokens": 157962, "cache_read_tokens": 0, "cache_write_tokens": 0, "total_tokens": 96047699, "cost_usd": 69.1543},
      {"day": "2026-06-27", "sessions": 3, "input_tokens": 239924, "output_tokens": 3606417, "cache_read_tokens": 1409690352, "cache_write_tokens": 33390597, "total_tokens": 1446927290, "cost_usd": 1041.7876},
      {"day": "2026-06-26", "sessions": 2, "input_tokens": 193580, "output_tokens": 3049396, "cache_read_tokens": 1244972286, "cache_write_tokens": 26155049, "total_tokens": 1274370311, "cost_usd": 917.5466},
      {"day": "2026-06-25", "sessions": 7, "input_tokens": 461132, "output_tokens": 2757623, "cache_read_tokens": 1086632464, "cache_write_tokens": 15778519, "total_tokens": 1105629738, "cost_usd": 796.0534},
      {"day": "2026-06-24", "sessions": 11, "input_tokens": 449352757, "output_tokens": 2056216, "cache_read_tokens": 93436402, "cache_write_tokens": 954113, "total_tokens": 545799488, "cost_usd": 392.9756},
      {"day": "2026-06-23", "sessions": 3, "input_tokens": 402766075, "output_tokens": 1219585, "cache_read_tokens": 787200, "cache_write_tokens": 0, "total_tokens": 404772860, "cost_usd": 291.4365},
      {"day": "2026-06-22", "sessions": 5, "input_tokens": 209587510, "output_tokens": 644687, "cache_read_tokens": 377856, "cache_write_tokens": 0, "total_tokens": 210610053, "cost_usd": 151.6392},
      {"day": "2026-06-21", "sessions": 5, "input_tokens": 129112411, "output_tokens": 426453, "cache_read_tokens": 1396736, "cache_write_tokens": 0, "total_tokens": 130935600, "cost_usd": 94.2736},
      {"day": "2026-06-20", "sessions": 6, "input_tokens": 88203900, "output_tokens": 309256, "cache_read_tokens": 1742720, "cache_write_tokens": 0, "total_tokens": 90255876, "cost_usd": 64.9842},
      {"day": "2026-06-19", "sessions": 5, "input_tokens": 35989482, "output_tokens": 132437, "cache_read_tokens": 1467776, "cache_write_tokens": 0, "total_tokens": 37589695, "cost_usd": 27.0646},
      {"day": "2026-06-18", "sessions": 4, "input_tokens": 17140450, "output_tokens": 1150060, "cache_read_tokens": 379549888, "cache_write_tokens": 18176061, "total_tokens": 416016459, "cost_usd": 299.5319},
      {"day": "2026-06-17", "sessions": 4, "input_tokens": 149752, "output_tokens": 194319, "cache_read_tokens": 8216235, "cache_write_tokens": 701575, "total_tokens": 9261881, "cost_usd": 6.6686},
      {"day": "2026-06-16", "sessions": 8, "input_tokens": 771472, "output_tokens": 5367357, "cache_read_tokens": 1872320655, "cache_write_tokens": 50567863, "total_tokens": 1929027347, "cost_usd": 1388.8997},
      {"day": "2026-06-15", "sessions": 13, "input_tokens": 911042, "output_tokens": 4497387, "cache_read_tokens": 1449900531, "cache_write_tokens": 43192443, "total_tokens": 1498501403, "cost_usd": 1078.921},
      {"day": "2026-06-14", "sessions": 6, "input_tokens": 356481, "output_tokens": 3562712, "cache_read_tokens": 1289174243, "cache_write_tokens": 37973883, "total_tokens": 1331067319, "cost_usd": 958.3685},
      {"day": "2026-06-13", "sessions": 5, "input_tokens": 103792, "output_tokens": 29006, "cache_read_tokens": 1332096, "cache_write_tokens": 0, "total_tokens": 1464894, "cost_usd": 1.0547},
      {"day": "2026-06-12", "sessions": 9, "input_tokens": 480109, "output_tokens": 3256167, "cache_read_tokens": 1123343307, "cache_write_tokens": 29869653, "total_tokens": 1156949236, "cost_usd": 833.0034},
      {"day": "2026-06-11", "sessions": 8, "input_tokens": 350732, "output_tokens": 2259949, "cache_read_tokens": 755845297, "cache_write_tokens": 13941447, "total_tokens": 772397425, "cost_usd": 556.1261},
      {"day": "2026-06-10", "sessions": 10, "input_tokens": 53954730, "output_tokens": 1254984, "cache_read_tokens": 314478839, "cache_write_tokens": 2020674, "total_tokens": 371709227, "cost_usd": 267.6306},
      {"day": "2026-06-09", "sessions": 9, "input_tokens": 5669725, "output_tokens": 104550, "cache_read_tokens": 2990303, "cache_write_tokens": 0, "total_tokens": 8764578, "cost_usd": 6.3105},
      {"day": "2026-06-08", "sessions": 8, "input_tokens": 1287642, "output_tokens": 4475815, "cache_read_tokens": 1736636825, "cache_write_tokens": 16174575, "total_tokens": 1758574857, "cost_usd": 1266.1739},
      {"day": "2026-06-07", "sessions": 19, "input_tokens": 419781, "output_tokens": 164311, "cache_read_tokens": 6428708, "cache_write_tokens": 163250, "total_tokens": 7176050, "cost_usd": 5.1668},
      {"day": "2026-06-06", "sessions": 17, "input_tokens": 371252, "output_tokens": 56611, "cache_read_tokens": 2942754, "cache_write_tokens": 28945, "total_tokens": 3399562, "cost_usd": 2.4477},
      {"day": "2026-06-05", "sessions": 14, "input_tokens": 653687, "output_tokens": 2792229, "cache_read_tokens": 783244527, "cache_write_tokens": 7913243, "total_tokens": 794603686, "cost_usd": 572.1147},
      {"day": "2026-06-04", "sessions": 24, "input_tokens": 465139, "output_tokens": 1864967, "cache_read_tokens": 473277670, "cache_write_tokens": 9084227, "total_tokens": 484692003, "cost_usd": 348.9782},
      {"day": "2026-06-03", "sessions": 23, "input_tokens": 426152, "output_tokens": 2020675, "cache_read_tokens": 631656129, "cache_write_tokens": 5961885, "total_tokens": 640064841, "cost_usd": 460.8467},
      {"day": "2026-06-02", "sessions": 27, "input_tokens": 84053, "output_tokens": 1926, "cache_read_tokens": 261723, "cache_write_tokens": 45631, "total_tokens": 393333, "cost_usd": 0.2832},
      {"day": "2026-06-01", "sessions": 17, "input_tokens": 364337, "output_tokens": 1261156, "cache_read_tokens": 239051141, "cache_write_tokens": 9557474, "total_tokens": 250234108, "cost_usd": 180.1686},
      {"day": "2026-05-31", "sessions": 6, "input_tokens": 74964, "output_tokens": 1008891, "cache_read_tokens": 319722254, "cache_write_tokens": 7207799, "total_tokens": 328013908, "cost_usd": 236.17},
      {"day": "2026-05-30", "sessions": 10, "input_tokens": 297844, "output_tokens": 5795574, "cache_read_tokens": 52497842, "cache_write_tokens": 17715191, "total_tokens": 76306451, "cost_usd": 54.9406},
      {"day": "2026-05-29", "sessions": 13, "input_tokens": 228582, "output_tokens": 4154977, "cache_read_tokens": 384966492, "cache_write_tokens": 14957209, "total_tokens": 404307260, "cost_usd": 291.1012},
      {"day": "2026-05-28", "sessions": 4, "input_tokens": 257677, "output_tokens": 33319, "cache_read_tokens": 604608, "cache_write_tokens": 0, "total_tokens": 895604, "cost_usd": 0.6448},
      {"day": "2026-05-27", "sessions": 5, "input_tokens": 1997027, "output_tokens": 114602, "cache_read_tokens": 82279552, "cache_write_tokens": 0, "total_tokens": 84391181, "cost_usd": 60.7617},
      {"day": "2026-05-26", "sessions": 13, "input_tokens": 1125266, "output_tokens": 2406112, "cache_read_tokens": 701572080, "cache_write_tokens": 14695463, "total_tokens": 719798921, "cost_usd": 518.2552},
      {"day": "2026-05-25", "sessions": 5, "input_tokens": 1004207, "output_tokens": 41855, "cache_read_tokens": 2095104, "cache_write_tokens": 0, "total_tokens": 3141166, "cost_usd": 2.2616},
      {"day": "2026-05-24", "sessions": 5, "input_tokens": 153570, "output_tokens": 1559307, "cache_read_tokens": 176689714, "cache_write_tokens": 4154175, "total_tokens": 182556766, "cost_usd": 131.4409},
      {"day": "2026-05-23", "sessions": 11, "input_tokens": 359036, "output_tokens": 663234, "cache_read_tokens": 50513055, "cache_write_tokens": 1820526, "total_tokens": 53355851, "cost_usd": 38.4162}
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

/**
 * **这几个接口的假响应必须慢一点。**
 *
 * 假后端默认在一个微任务里就 resolve —— 比 React 的重渲染还早。于是"响应回来时
 * 组件已经重渲染过一轮"这个真实网络下的常态，在验证台里永远复现不出来。
 * 实测漏掉过一个死锁：effect 里 setLoading(true) 让依赖变化，React 先跑 cleanup 把
 * 回调作废，请求回来时状态一个都没落地，面板永远停在"正在取模型清单…"，而验证台
 * 一路绿灯。给这类"开面板才去取"的接口加几百毫秒，那个顺序才验得到。
 */
const SLOW_PATHS: [string, number][] = [
  ["/ivyea-agent/model/providers", 320],
  ["/settings/model-catalog", 320],
];

const delayFor = (url: string): number => {
  for (const [prefix, ms] of SLOW_PATHS) if (url.includes(prefix)) return ms;
  return 0;
};

/**
 * 页面送进来的追加指令（POST /chat/inject）。agentStream 跑到那一段时把它们回播成
 * `injected` 事件 —— 这条来回必须真的走一遍：只发事件不收请求，验的就只是渲染，
 * 而"说出去的话有没有真的送到"恰恰是这个功能的全部。
 */
const injected: { id: string; text: string }[] = [];

/**
 * 「停止」按下之后置位。假流在每一拍读它 —— 验证台里必须能复现"真的停下来"，
 * 否则验的只是按钮变没变样，而这个功能的全部意义在于**它真的不再往下跑**。
 */
const stopped = { at: 0 };

/** 装上假适配器。必须在 render 之前调用。 */
export function installMockApi(): void {
  const reply = (config: { url?: string; baseURL?: string; data?: unknown }) => {
    // 用 baseURL + url 去匹配：Listing 等板块各自 axios.create 了实例，
    // 它们的 config.url 是 "/projects" 这种**相对自己 baseURL** 的短路径，
    // 只看 url 会和别的板块撞名。
    const base = (config.baseURL || "/api").replace(/^\/api/, "");
    const full = base + (config.url || "");
    let body: any = {};
    try { body = JSON.parse(String(config.data || "{}")); } catch { /* 非 JSON 请求 */ }
    return {
      data: full.startsWith("/ivyea-agent/chat/inject")
        ? (() => {
            const item = { id: "inj-" + (injected.length + 1), text: String(body.text || "") };
            injected.push(item);
            return { ok: true, accepted: true, item, pending: injected.length };
          })()
        : full.startsWith("/ivyea-agent/chat/cancel")
        ? (((stopped.at = Date.now())), { ok: true, cancelled: true })
      : full.startsWith("/ivyea-agent/chat/question")
          ? (((window as any).__lastQuestionAnswer = body), { ok: true })
          : match(full || config.url || ""),
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    } as never;
  };

  // ?lxoff=1 —— 装成"领星还没配"：经营侧两块面板的接口 400，且 /cockpit/status
  // 里 lingxing_enabled=false。**必须两件事一起装**：LingXingGate 是靠回头问
  // status 来区分"没配置"和"真故障"的，只让面板报错验到的是另一条分支。
  const lxOff = new URLSearchParams(location.search).get("lxoff") === "1";
  // ?lxerr=1 —— 领星配好了，但面板接口真的挂了。验的是反面：这时候**必须**把原始
  // 错误照实显示，绝不能被 Gate 当成"没配置"藏掉。
  const lxErr = new URLSearchParams(location.search).get("lxerr") === "1";

  const slowReply = async (config: { url?: string; baseURL?: string }) => {
    const ms = delayFor((config.baseURL || "") + (config.url || ""));
    if (ms) await new Promise((r) => setTimeout(r, ms));
    const full = (config.baseURL || "/api").replace(/^\/api/, "") + (config.url || "");
    if (lxOff || lxErr) {
      if (lxOff && full.startsWith("/cockpit/status")) {
        const r: any = reply(config);
        r.data = { ...r.data, lingxing_enabled: false };
        return r;
      }
      if (full.startsWith("/cockpit/ads") || full.startsWith("/cockpit/promotions")) {
        throw Object.assign(new Error("Request failed with status code 400"), {
          isAxiosError: true,
          response: { status: 400, data: {
            detail: lxOff ? "领星集成未启用（总开关关闭）" : "领星接口超时（504 upstream）" } },
        });
      }
    }
    return reply(config);
  };

  api.defaults.adapter = async (config) => slowReply(config);
  // 各板块自建的 axios 实例（listing/api.ts 就是一个）不共享上面那个 adapter，
  // 但会在发请求时回落到全局默认值——不设这个，整个 Listing 板块在验证台里
  // 是打不开的。
  axios.defaults.adapter = async (config) => slowReply(config);

  // ?agentdown=1 —— 装成 IvyeaAgent 没起来，用来验任务台的兜底通道。
  // AI 问答那一页并进任务台后，"agent 掉线还能纯聊"这条退路就只剩这一个入口了，
  // 它坏没坏在真实页面上验不到就等于没验。
  const agentDown = new URLSearchParams(location.search).get("agentdown") === "1";
  /**
   * 开关必须在**装配时**读一次记住，不能在流跑到一半时现读 `location.search`：
   * 任务台拿到 session_id 之后会 `navigate("/console?session=…", {replace:true})`
   * 把地址栏整个换掉，那一刻起 `?followups=1` 就不在地址里了 —— 现读的话这一段
   * 永远不会执行，而症状是"验证台里什么都没发生"，根本看不出是开关掉了。
   */
  const wantFollowups = new URLSearchParams(location.search).get("followups") === "1";

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
    stopped.at = 0;
    // 每一拍都看一眼有没有人按停止 —— 真 agent 是在模型流的每个事件和每个工具步
    // 边界读中止标志的，这里照同一个粒度模拟。
    const beat = (ms: number) => new Promise((r) => setTimeout(r, ms));
    return new Response(new ReadableStream({
      async start(ctrl) {
        const send = (event: string, data: unknown) =>
          ctrl.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
        const halted = () => {
          if (!stopped.at) return false;
          send("cancelled", { session_id: "s-live", text: "（这一轮被你停掉了）",
                              injected_pending: [] });
          ctrl.close();
          return true;
        };
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
        for (const t of think) {
          await beat(1200);
          if (halted()) return;
          send("reasoning", { text: t });
        }
        await beat(700);
        // **工具之前先说一段话** —— 真 agent 就是这么干的（模型先写"我先去查一下"，
        // 再调工具，回来接着说）。假流里此前一个字都不在工具之前，于是"分段汇报"
        // 这件事在验证台里永远看不出来，改坏了也验不到。
        for (const t of ["先确认数据源。", "我并行看一下本地有没有报表文件、",
                         "以及已配置的 MCP 数据源里有没有广告相关的工具。"]) {
          await beat(320); send("token", { text: t });
        }
        await beat(400);
        // 一批常规工具：界面上应该折成"搜索 2 次 · 读了 2 个文件 · 跑了 1 条命令"
        for (const [i, st] of [["grep", "搜索内容"], ["glob", "查找文件"],
                               ["read_file", "读取文件"], ["read_file", "读取文件"],
                               ["run_command", "执行命令"]].entries()) {
          if (halted()) return;
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
        // 第二段话：上一批工具的结论，下一批工具的理由。
        for (const t of ["\n\nMCP 数据源是 sorftime，偏选品，没有广告报表拉取工具。",
                         "那就用本地这份搜索词报表跑，缺的字段回头再补。"]) {
          await beat(320); send("token", { text: t });
        }
        await beat(400);
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
        // ── 写操作审批卡 ──────────────────────────────────────────────
        // ?approval=1 时插一张确认卡。**它此前在验证台里根本渲染不出来** ——
        // 假流从不发 permission_request，于是"审批档位选了放行、卡片长什么样、
        // 点了确认之后流怎么继续"这一整条最要紧的链路，一次都没被验过。
        if (new URLSearchParams(location.search).get("approval") === "1") {
          await beat(500);
          send("permission_request", {
            request_id: "req-demo-1", session_id: "s-live", op_type: "lingxing_write",
            title: "把 3 个搜索词加为否定关键词", destructive: true,
            preview: "广告活动：Trail Camera - Auto\n否词：cheap camera / free camera / camera app",
            options: [{ key: "approve", label: "批准这一次" },
                      { key: "session", label: "本会话都允许" },
                      { key: "deny", label: "拒绝" }],
            expires_at: Math.floor(Date.now() / 1000) + 600,
          });
          await beat(2500);
        }
        // ── 选项卡 + 追加指令 ─────────────────────────────────────────
        // ?followups=1 时铺这一段。两件事在验证台里都必须**真的发生过**，否则
        // "跑着的时候能不能说话""拿不准时弹的那张卡长什么样"就只能靠读代码判断。
        const followups = wantFollowups;
        if (followups) {
          await beat(400);
          send("question_request", {
            request_id: "q-demo-1", session_id: "s-live",
            timeout_s: 300, expires_at: Date.now() / 1000 + 300,
            questions: [{
              question: "这三组词根按哪种力度处理？",
              header: "处理力度",
              options: [
                { label: "先否词后观察", description: "只加否定精确，7 天后再看竞价", recommended: true },
                { label: "否词 + 降竞价", description: "同时下调 15% 竞价，见效快但波动大" },
              ],
            }],
          });
          // 追加指令：等页面把它送进来（POST /chat/inject 会把文本塞进这个数组）。
          for (let i = 0; i < 40 && !injected.length; i++) await beat(150);
          for (const item of injected) {
            send("injected", { session_id: "s-live", id: item.id, text: item.text,
                               ts: Date.now() / 1000 });
            await beat(200);
            send("token", { text: `\n\n（收到追加要求：${item.text}）` });
          }
        }
        await beat(600);
        for (const t of ["\n\n## 先说结论\n\n", "这一周花费涨了 34%，", "其中 28% 来自单次点击成本上升。"]) {
          await beat(500);
          if (halted()) return;
          send("token", { text: t });
        }
        send("final", { session_id: "s-live", turn_id: "t-live",
                        ...(followups ? {
                          started_ms: Date.now() - 42_000, ended_ms: Date.now(), ms: 42_000,
                          auto_decisions: [{ question: "报表口径按哪个来？", header: "口径",
                                             chosen: "按环比", reason: "timeout" }],
                        } : {}),
                        usage: { prompt_tokens: 9860, completion_tokens: 178,
                                 prompt_cache_hit_tokens: 0, llm_ms: 2500 },
                        context: { used: 11240, window: 128000, percent: 8.8, estimated: true,
                                   model: "deepseek-v4-pro",
                                   breakdown: { system: 1520, tools: 6910, messages: 2810 } } });
        ctrl.close();
      },
    }), { status: 200, headers: { "content-type": "text/event-stream" } });
  };

  /**
   * 活轮日志的回放流（`GET /chat/sessions/{id}/live`）。
   *
   * 形状照抄 agent 的 live_turn.follow：先 live_begin，再把已经发生过的事件按
   * 原顺序补一遍（正文和工具**交错**，这是分段汇报的凭据），最后继续实时跟随。
   */
  const liveStream = () => {
    const enc = new TextEncoder();
    const beat = (ms: number) => new Promise((r) => setTimeout(r, ms));
    return new Response(new ReadableStream({
      async start(ctrl) {
        const send = (event: string, data: unknown) =>
          ctrl.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
        send("live_begin", { running: true, seq: 6, dropped: 0,
                             started_ms: Date.now() - 42_000 });
        send("start", { session_id: "s-live", model: "deepseek-v4-pro", read_only: true });
        send("todos", { todos: [
          { content: "确认数据源与口径", status: "completed" },
          { content: "拉搜索词报表，找浪费最集中的词根", status: "in_progress" },
          { content: "给出否词与竞价的具体动作", status: "pending" },
        ] });
        // —— 回放：这一轮在别的标签页里已经跑掉的部分 ——
        send("token", { text: "先确认数据源。本地报表和 MCP 数据源我并行看了一遍。" });
        for (const [i, st] of [["grep", 40], ["read_file", 120], ["run_command", 320]].entries()) {
          send("step", { type: "step", id: "r" + i, seq: i, phase: "tool",
                         name: st[0], status: "ok", ms: st[1] });
        }
        send("token", { text: "\n\nsorftime 没有广告报表拉取工具，改用本地那份搜索词报表。" });
        send("step", { type: "step", id: "r9", seq: 3, phase: "tool", name: "read_report",
                       status: "running" });
        // —— 跟随：回放完之后继续实时往下跑 ——
        await beat(2000);
        send("step", { type: "step", id: "r9", seq: 3, phase: "tool", name: "read_report",
                       status: "ok", ms: 2000 });
        for (const t of ["\n\n## 先说结论\n\n", "这一周花费涨了 34%，", "其中 28% 来自单次点击成本上升。"]) {
          await beat(400); send("token", { text: t });
        }
        send("final", { session_id: "s-live", usage: { prompt_tokens: 9860, completion_tokens: 178 } });
        send("live_end", { ended_ms: Date.now() });
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
    if (/\/api\/ivyea-agent\/chat\/sessions\/[^/]+\/live/.test(url)) {
      return Promise.resolve(liveStream());
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
