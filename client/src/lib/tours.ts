import type { TourStep } from "../components/Tour";

/**
 * Interactive tours, keyed by route pathname. Each board's tour auto-runs on the
 * user's first visit (once, remembered in localStorage) and can be replayed via
 * the "?" button in the top bar.
 *
 * Steps target elements by a stable CSS selector (existing class or a
 * `[data-tour="…"]` attribute added on the element); a step without `sel` shows
 * a centered intro card. A selector that matches nothing also renders centered,
 * so steps degrade gracefully.
 *
 * To add a board: add an entry here (+ tag its key element with `data-tour="…"`
 * if it has no stable class).
 */
// 运营驾驶舱的引导。地址从 "/" 搬到了 /dashboard（"/" 现在只做落地分流），
// 两个键都指向同一份步骤，老的 "已看过" 记录也就继续有效。
const DASHBOARD_TOUR: TourStep[] = [
  {
    title: "欢迎使用 IvyeaOps 👋",
    body: "一台服务器、一次登录，把亚马逊运营全流程收进浏览器。\n这个快速引导带你认识控制台。3 步就能开始用：\n① 配一个 AI 模型  ② 配数据源  ③ 去任意板块开干。",
  },
  { sel: '[data-tour="sidebar"]', title: "左侧 = 所有板块入口", body: "「任务台」是一句话把活派给 Agent 的地方；现有板块都收在「更多工具」里，按原来的分组排列。左下角按钮可折叠侧边栏。" },
  { sel: '.sb a[href="/hub-settings"]', title: "第一步：系统配置", body: "新装后先来这里：配一个「全局兜底大模型」（DeepSeek/OpenAI 等任选）+ 数据源 Key。配好这两样，全站 AI 与数据功能就能用了。" },
  { sel: ".home-tabs", title: "首页 · 运营驾驶舱", body: "每日盯盘面板：大盘流量 / 关键词 / 竞品监控 / 自有 ASIN / 类目大盘 五个标签页。点标签切换不同维度。" },
  { sel: '[data-tour="home-source"]', title: "数据源 & 站点", body: "右上角选站点和数据源（Sorftime）。切换数据源会重新加载全部数据。" },
  { sel: '[data-tour="tour-help"]', title: "随时查手册 / 重看引导", body: "📖 是使用手册（各板块详细文档）。旁边的「?」可随时重看当前板块的这个引导。" },
];

export const TOURS: Record<string, TourStep[]> = {
  // ── 任务台 ────────────────────────────────────────────────────────────────
  "/console": [
    {
      title: "任务台 · 一个入口把活派出去",
      body: "不用再想「这件事该去哪个板块」。直接说需求，Ivyea 会自己挑技能、调板块能力去拿真实数据，再把结论给你。",
    },
    { sel: ".cc-scenes", title: "不知道说什么？点场景", body: "这些是常见的开场，点一下就把提示词填进输入框，改改就能发。" },
    { sel: ".cc-bar", title: "输入框下面这一行是真开关", body: "工作区 / 审批档位 / 技能 / 模型，选了就真的按那个跑。默认是「只读建议」——Agent 只分析、绝不动线上数据。" },
    { sel: '[data-tour="console-new"]', title: "开新任务", body: "换个话题就点「新建任务」，上一轮的上下文不会串进来。" },
  ],

  // ── 能力市场 ──────────────────────────────────────────────────────────────
  "/capabilities": [
    { title: "能力市场", body: "Agent 能用什么，都摊在这一页：技能、MCP、智能体、授权。看得见才谈得上组合。" },
    { sel: ".home-tabs", title: "四类能力", body: "技能=可复用的做法；MCP=连数据和工具的通道；智能体=主脑模型；授权=数据源密钥状态。" },
    { title: "两套注册表，别混了", body: "技能和 MCP 各有两套：Agent 自己的（~/.ivyea/）和 Skill 中心 / Claude Code 的。页面按来源分区标明了——真正决定「任务台里的 Agent 能干什么」的是 Agent 那一套。" },
    { title: "用起来", body: "技能卡上点「用这个技能」，会带着它跳回任务台并预选好，直接说需求就行。" },
  ],

  // ── 定时任务 ──────────────────────────────────────────────────────────────
  "/schedules": [
    { title: "定时任务", body: "到点让 Agent 自己跑一轮，结果留在运行历史里等你看。比如每天早上跑一遍广告巡检。" },
    { sel: ".sch-notice", title: "始终只读（重要）", body: "没人在屏幕前的时候，Agent 只会巡检、分析、把要改的列清楚，绝不会真的动线上数据。要落地就看完结果再去对应板块执行。" },
    { sel: ".sch-presets", title: "不用手写 cron", body: "常用节奏点一下就好；也可以自己写 cron，下面会实时告诉你接下来几次什么时候跑。" },
    { title: "先试一次", body: "建好后点「立即运行」，不用等到点也能验证它写得对不对。" },
  ],

  // ── 控制台总览（运营驾驶舱）───────────────────────────────────────────────
  "/dashboard": DASHBOARD_TOUR,
  "/": [
    {
      title: "欢迎使用 IvyeaOps 👋",
      body: "一台服务器、一次登录，把亚马逊运营全流程收进浏览器。\n这个快速引导带你认识控制台。3 步就能开始用：\n① 配一个 AI 模型  ② 配数据源  ③ 去任意板块开干。",
    },
    { sel: '[data-tour="sidebar"]', title: "左侧 = 所有板块入口", body: "按「工具 / AI & 系统 / 小工具 / 管理」分组。点任意一项进入对应板块；左下角按钮可折叠侧边栏。" },
    { sel: '.sb a[href="/hub-settings"]', title: "第一步：系统配置", body: "新装后先来这里：配一个「全局兜底大模型」（DeepSeek/OpenAI 等任选）+ 数据源 Key。配好这两样，全站 AI 与数据功能就能用了。" },
    { sel: ".home-tabs", title: "首页 · 运营驾驶舱", body: "每日盯盘面板：大盘流量 / 关键词 / 竞品监控 / 自有 ASIN / 类目大盘 五个标签页。点标签切换不同维度。" },
    { sel: '[data-tour="home-source"]', title: "数据源 & 站点", body: "右上角选站点和数据源（Sorftime）。切换数据源会重新加载全部数据。" },
    { sel: '[data-tour="tour-help"]', title: "随时查手册 / 重看引导", body: "📖 是使用手册（各板块详细文档）。旁边的「?」可随时重看当前板块的这个引导。" },
  ],

  // ── 系统配置 ───────────────────────────────────────────────────────────────
  "/hub-settings": [
    { title: "系统配置：全站的总开关", body: "这里集中配置模型、数据源、集成路径等。每个区块独立保存，点该区块的「保存」即时生效。新用户重点看下面两处。" },
    { sel: '[data-tour="settings-fallback"]', title: "★ 全局兜底大模型（最重要）", body: "所有板块文本 AI 的统一出口。选一个 provider（推荐 DeepSeek：快、便宜、国内直连）+ 填 Key，全站 AI 立即可用。数据源 Key（Sorftime）也在附近的「数据源」区配。" },
    { sel: '[data-tour="settings-health"]', title: "健康检查", body: "一眼看清各服务与 AI 链的就绪状态（文本链 / 全局兜底 / 视觉）。配完回这里确认对应项变绿即可。" },
  ],

  // ── 市场调研 ───────────────────────────────────────────────────────────────
  "/market": [
    { title: "市场调研", body: "输入关键词或 ASIN，一键生成结构化的市场调研报告（市场规模、竞争格局、机会风险、SWOT、行动清单）。" },
    { sel: ".market-query-input", title: "输入调研对象", body: "选「关键词 / ASIN」模式，填入内容，并在右侧选数据源与站点。" },
    { sel: ".market-btn-submit", title: "生成报告", body: "点这里开始生成。AI 优先走「全局兜底大模型」，再按配置回退到可选外部 Agent，过程会流式输出。" },
    { title: "深入分析 & 历史", body: "出报告后，报告下方会出现「深入分析」：选一个角度 + 智能体，把整份报告带进「智能体会话」继续追问。历史记录自动保存，可随时载入 / 导出。" },
  ],

  // ── Listing 工作台 ───────────────────────────────────────────────────────────
  "/listing": [
    { title: "Listing 工作台", body: "从产品采集到 Listing 文案、图文整套直出和交付的生产流水线，按 ① 素材与洞察 → ② Listing 文案 → ③ 视觉套图 → ④ 交付推进。" },
    { sel: '[data-tour="listing-tabs"]', title: "四步流程", body: "视觉套图会自动使用前两步内容和白底产品图，并把手动创意需求编译成整套统一的最终生图提示词。" },
    { title: "开始：新建 / 选 ASIN", body: "左侧填 ASIN 新建项目，点「采集 ASIN 数据」开始。采集、分析、文案、生图全部在服务端后台运行——可以随时刷新或离开页面，回来自动恢复进度；产品信息修改后自动保存。" },
  ],

  // ── 打法推荐 ───────────────────────────────────────────────────────────────
  "/playbook": [
    { title: "打法推荐", body: "输入产品词或竞品 ASIN + 目标售价，生成一份纯白帽站内打法手册：选品定价、关键词布局、广告结构、节奏排期、广告批量表。" },
    { sel: ".market-query-input", title: "输入产品词 / 竞品 ASIN", body: "选「关键词 / ASIN」模式填入；右侧的「目标售价」必填（USD），成本选填。" },
    { sel: ".market-btn-submit", title: "生成手册", body: "点这里生成。出报告后有「深入分析」（落地执行 / 广告细化 / 风险推演）、历史记录、以及 .csv 广告批量表导出。" },
  ],

  // ── 分析工具 ───────────────────────────────────────────────────────────────
  "/tools": [
    { title: "分析工具", body: "三组工具：① ASIN 深度审计  ② 广告 search-term 审计  ③ 深入分析小工具（关键词竞争 / 竞品流量 / 流量诊断 / 评论聚类 / Listing 改写）。" },
    { sel: '[data-tour="tools-asin"]', title: "ASIN 深度审计", body: "输入竞品 ASIN，由 Agent（hermes + MCP 抓数据）生成 COSMO/Rufus 维度审计报告。需要能调工具的本地 hermes，纯文本模型替代不了抓数据。" },
    { title: "广告审计 & 深入分析", body: "往下还有「广告 search-term 审计」（上传 SP/SB/SD 搜索词 xlsx 做根因分析）和「深入分析小工具」。多数出报告后可一键带入「智能体会话」深挖。" },
  ],

  // ── 领星 ERP ─────────────────────────────────────────────────────────────────
  "/lingxing": [
    { title: "领星 ERP", body: "经领星官方 OpenAPI / MCP 接入店铺数据与广告：浏览分析、优化建议、受控写操作（默认全关，开启后三重复核 + 人工确认）。" },
    { sel: ".lx-tabs", title: "功能分区", body: "六个标签：大盘 / 数据浏览 / 优化建议（规则引擎 + AI 分析）/ 工单 / 审计 / 配置。首次使用先在「配置」里填 AppID/Secret 凭证并打开总开关。" },
    { title: "一条闭环", body: "在「优化建议」勾选候选 → 批量生成工单（后台复核）→「工单」tab 出现待确认徽标 → 勾选后人工确认执行。凭证写入后端不入库；真实写受双开关 + 三重复核 + 护栏 + 人工确认保护。帮助文档在「配置」tab 内。" },
  ],

  // ── Skill 中心 ───────────────────────────────────────────────────────────────
  "/skill-hub": [
    { title: "Skill 中心 · 想法工坊", body: "一句话生成 Skill：输入需求 → 多阶段流水线（理解→规划→复核→优化→生成→自检修复）产出 SKILL.md，并可视化其 Tool Spec。" },
    { title: "管理已有", body: "除创建外，还能管理已有 Skill、从 GitHub 导入、查看执行历史、可视化执行 Tool。Skill 生成默认走稳定文本链（应用模型优先 + 可选外部 Agent）。" },
  ],

  // ── AI 问答 ──────────────────────────────────────────────────────────────────
  "/assistant": [
    { title: "AI 问答", body: "直连大模型的自由对话 / 写作，不经过外部 CLI、不碰文件系统，对普通用户安全。用「全局兜底大模型」，留空回退 IvyeaAgent→DeepSeek。" },
    { sel: ".market-query-input", title: "输入问题", body: "输入问题或写作要求，Enter 发送（Shift+Enter 换行）。回答流式输出，不调用任何工具。" },
  ],

  // ── AI 生图 ──────────────────────────────────────────────────────────────────
  "/imagegen": [
    { title: "AI 生图", body: "输入提示词用 AI 生成图片（默认 Apimart gpt-image-2，可在「系统配置 → 应用模型 → AI 生图」改模型/Key/Base）。" },
    { sel: ".market-query-input", title: "写提示词", body: "描述你想要的图片（英文效果更佳），Enter 发送。已有图后可继续描述修改要求、追加生成。" },
  ],

  // ── 智能体会话 ───────────────────────────────────────────────────────────────
  "/agents": [
    { title: "外部智能体", body: "这里保留 Claude Code / Hermes / Codex 等外部 CLI 的完整会话界面：多会话、流式输出、工具可视化、文件上传（≤300MB）、resume。" },
    { title: "怎么用", body: "左侧新建 / 切换会话，顶部选 provider（claude / hermes / codex），底部输入框发消息。默认 IvyeaAgent 会话和知识库在右下角常驻图标里。" },
    { title: "前提", body: "外部 CLI 是可选增强项；新部署不再要求安装 Hermes/GBrain/Ollama。" },
  ],

  // ── 知识库工作台（GBrain 兼容） ────────────────────────────────────────────────
  "/brain": [
    { title: "知识库工作台", body: "这里保留原 GBrain 的上传、粘贴、URL 入库、编辑、搜索和知识库对话工作流。" },
    { sel: ".tabs", title: "功能标签", body: "对话 / 上传 / 搜索 / 页面 / 模板 / 概览 / 设置。右下角 IvyeaAgent 知识库页可把 ~/brain 迁移到 ~/.ivyea/knowledge。" },
    { title: "配置", body: "继续使用 GBrain 兼容流程时才需要配置 GBrain CLI、知识库根目录和 Embedding 模型。" },
  ],

  // ── 服务器终端 ───────────────────────────────────────────────────────────────
  "/terminal": [
    { title: "服务器终端", body: "浏览器内的真实 PTY 终端，直接在服务器上敲命令。仅管理员、谨慎授权。（仅 Linux；Windows 不支持 PTY。）" },
    { sel: ".terminal-session-list", title: "终端列表", body: "左侧是终端会话列表，可新建 / 切换多终端。" },
    { sel: ".terminal-main", title: "主终端", body: "中间是主终端画面；右侧支持会话快照（定时抓取 tmux 画面，断线也能回看）。" },
  ],

  // ── 服务器监控 ───────────────────────────────────────────────────────────────
  "/servmon": [
    { title: "服务器监控", body: "查看服务器 CPU / 内存 / 磁盘 / 流量 / 进程占用，可对高占用进程执行停止。" },
    { sel: ".proc-table-wrap", title: "进程列表", body: "按占用排序的进程表。对高占用进程可停止——会先提示影响并二次确认，避免误杀。" },
  ],

  // ── 头程比价 ───────────────────────────────────────────────────────────────────
  "/freight": [
    { title: "头程比价", body: "上传货代头程报价 Excel，自动解析成统一表格做横向比价。纯解析、不使用 AI。" },
    { sel: ".fq-input", title: "查仓库报价", body: "上传报价文件后，在这里按仓库代码（如 ONT8）查询，横向对比各货代的头程价格。" },
  ],

  // ── 用户管理 ───────────────────────────────────────────────────────────────────
  "/users": [
    { title: "用户管理", body: "多用户与 RBAC：新增 / 禁用用户、重置密码、按板块授权（带 🔒 的板块默认仅管理员，可在此逐项开放给指定用户）。" },
    { sel: ".cat-table-wrap", title: "用户列表 & 授权", body: "这里列出所有用户。新增用户后，可逐板块勾选授予其访问权限。" },
  ],

  // ── 资讯 ───────────────────────────────────────────────────────────────────────
  "/news": [
    { title: "资讯", body: "每日「AI 行业 + 亚马逊卖家」资讯摘要：现场抓 RSS 源 + 标准 AI 链生成中文摘要（重要度、官方标记、标签），无需外部 cron。" },
    { sel: '[data-tour="news-refresh"]', title: "立即刷新", body: "点这里现场抓取并生成当日摘要。RSS 源可在「系统配置 → 高级 → 资讯 RSS 源」自定义；需至少一个可用文本 AI。" },
  ],
};

/** Whether a board at `pathname` has a tour. */
export function hasTour(pathname: string): boolean {
  return Array.isArray(TOURS[pathname]) && TOURS[pathname].length > 0;
}
