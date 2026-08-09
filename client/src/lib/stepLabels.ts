/**
 * 步骤芯片的文案层 —— 把 agent 的原始工具调用翻译成业务语言。
 *
 * 参考产品（beili / MyLevis）的关键体验差异不在动效，而在**过程说的是人话**：
 * 用户看到的是「生成市场调研报告 5.2s」而不是 `ivyea_ops_call_tool`。
 *
 * 文案有两个来源，优先级从高到低：
 *   1. 板块能力目录 `GET /api/ivyea-agent/ops-tools` —— 每条自带中文 `title`、
 *      `module`、`destructive`，是运行时注入的（`primeOpsToolLabels`）。
 *   2. 这里的内置动词表 —— agent 自带工具（读文件/搜索/联网…）的中文名，
 *      与 ivyea_agent/ui.py 的 `_TOOL_VERBS` 保持一致。
 * 都没命中时退回工具原名，绝不显示空白。
 */
import type { IvyeaStepEvent, OpsToolInfo } from "../api/ivyeaAgent";

/** blocked = 被前置护栏拦下（流程纠偏），不是工具出错 —— 别画成红叉。 */
export type StepStatus = "running" | "ok" | "error" | "blocked";
export type StepPhase =
  | "skill" | "board" | "mcp" | "tool" | "subagent" | "knowledge" | "plan" | "note";

/** 时间线上的一行。结构化事件和自由文本兜底都归一到这个形状。 */
export type ConsoleStep = {
  key: string;
  seq: number;
  phase: StepPhase;
  /** 工具原名，用于调试与「查看原始参数」。 */
  name: string;
  /** 展示用中文名。 */
  title: string;
  icon: string;
  /** 参数摘要，如 `garden hose · US`。 */
  detail?: string;
  /** MCP 服务器名（phase === "mcp" 时有）。 */
  server?: string;
  status: StepStatus;
  ms?: number;
  destructive?: boolean;
  /**
   * 这一步和别的步**真的同时在跑**。
   *
   * 不是猜的：agent 的并行分支会先把本步所有工具的「开始」一次性发出来，再并发
   * 执行。所以一条 running 到达时若已有同类还在 running，两者必然重叠 ——
   * 顺序执行的话上一条早就收尾了。没有这个凭据就别在界面上写"并行"。
   */
  parallel?: boolean;
  args?: Record<string, any>;
};

// ── agent 自带工具的中文动词（对齐 ivyea_agent/ui.py:_TOOL_VERBS）──────────────
const TOOL_VERBS: Record<string, string> = {
  read_file: "读取文件", view_file: "读取文件",
  write_file: "写入文件", edit_file: "编辑文件",
  list_dir: "列目录", glob: "查找文件",
  grep: "搜索内容", search_code: "搜索代码", code_search: "搜索代码",
  code_symbols: "解析符号", code_impact: "评估影响面", code_apply_patch: "应用补丁",
  run_command: "执行命令", run_python: "执行 Python", run_tests: "运行测试",
  bash_output: "查看后台输出", kill_bash: "终止后台任务",
  web_search: "联网搜索", web_fetch: "抓取网页",
  knowledge_search: "查知识库", skill_search: "查技能", recall: "回忆记忆",
  remember: "记住结论",
  todo_write: "更新计划", progress_update: "汇报进度", self_critique: "自我复核",
  dispatch_subagent: "派发子任务",
  run_patrol: "跑巡检", run_account_diagnosis: "账户诊断", propose_actions: "生成调整建议",
  execute_actions: "执行调整", rollback: "回滚",
  run_listing_audit: "Listing 审计", run_review_audit: "评论审计",
  run_offer_audit: "Offer 审计", run_competitor_audit: "竞品审计",
  run_image_audit: "主图审计", run_image_ocr: "图片识字",
  mcp_list_tools: "列 MCP 工具", mcp_call_tool: "调用 MCP 工具",
  mcp_list_resources: "列 MCP 资源", mcp_read_resource: "读 MCP 资源",
  ivyea_ops_list_tools: "查板块能力", ivyea_ops_call_tool: "调用板块能力",
  task_read: "读取任务", task_step: "推进任务", task_log: "记录任务", task_resume: "恢复任务",
};

const PHASE_ICONS: Record<StepPhase, string> = {
  skill: "✦",
  board: "◧",
  mcp: "⚑",
  tool: "⊙",
  subagent: "⑂",
  knowledge: "▤",
  plan: "☰",
  note: "·",
};

// ── 板块能力目录（运行时注入）─────────────────────────────────────────────────
let opsToolIndex: Record<string, OpsToolInfo> = {};

/** 任务台挂载时调一次，把板块能力的中文 title/destructive 灌进来。 */
export function primeOpsToolLabels(tools: OpsToolInfo[]): void {
  const next: Record<string, OpsToolInfo> = {};
  for (const t of tools) {
    if (t?.name) next[t.name] = t;
  }
  opsToolIndex = next;
}

export function opsToolInfo(name: string): OpsToolInfo | undefined {
  return opsToolIndex[name];
}

/** 已知的板块能力列表（用于任务台的「能力」提示）。 */
export function knownOpsTools(): OpsToolInfo[] {
  return Object.values(opsToolIndex);
}

// ── 参数摘要 ─────────────────────────────────────────────────────────────────
/** 挑出最能说明「这一步在干嘛」的几个参数值，拼成一行短摘要。 */
const DETAIL_KEYS = [
  "query", "q", "keyword", "asin", "pattern", "path", "url", "command",
  "name", "tool", "server", "marketplace", "country", "site", "mode",
  "project_id", "job_id", "sid", "days", "skill_name",
];

export function summarizeArgs(args?: Record<string, any>, skip: string[] = []): string {
  if (!args || typeof args !== "object") return "";
  const parts: string[] = [];
  for (const k of DETAIL_KEYS) {
    if (skip.includes(k)) continue;
    const v = (args as any)[k];
    if (v === undefined || v === null || v === "") continue;
    if (typeof v === "object") continue;
    const s = String(v).trim();
    if (!s) continue;
    parts.push(s.length > 42 ? s.slice(0, 42) + "…" : s);
    if (parts.length >= 3) break;
  }
  return parts.join(" · ");
}

// ── 结构化事件 → 时间线行 ────────────────────────────────────────────────────
/**
 * 把一条 `step` 事件翻译成时间线行。
 *
 * 三种需要特殊拆包的调用（agent 不把 MCP/板块工具扁平化进工具名空间，
 * 真实工具藏在参数里，所以必须提到顶层才能显示成人话）：
 *   - `ivyea_ops_call_tool` → 板块能力，中文名来自 ops-tools 目录
 *   - `mcp_call_tool`       → MCP 工具，标签是 `<server> · <tool>`
 *   - `dispatch_subagent`   → 子任务
 */
export function stepFromEvent(ev: IvyeaStepEvent): ConsoleStep {
  const args = ev.args || {};
  const status: StepStatus = ev.status || "running";
  const ms = typeof ev.ms === "number" ? ev.ms : undefined;
  const base = { key: ev.id || `${ev.seq}`, seq: ev.seq ?? 0, status, ms, args, name: ev.name };

  // 板块能力：serve 已把真实工具名提到 ev.tool；老事件里退回读 args.name。
  if (ev.name === "ivyea_ops_call_tool" || ev.phase === "board") {
    const toolName = ev.tool || String(args.name || "");
    const info = opsToolIndex[toolName];
    return {
      ...base,
      phase: "board",
      icon: PHASE_ICONS.board,
      title: info?.title || toolName || "板块能力",
      detail: summarizeArgs(args.arguments || args, ["name", "tool"]),
      destructive: info?.destructive,
    };
  }

  // MCP：server / tool 同样藏在参数里。
  if (ev.name === "mcp_call_tool" || ev.phase === "mcp") {
    const server = ev.server || String(args.server || "");
    const toolName = ev.tool || String(args.tool || "");
    return {
      ...base,
      phase: "mcp",
      icon: PHASE_ICONS.mcp,
      server,
      title: [server, toolName].filter(Boolean).join(" · ") || "MCP 工具",
      detail: summarizeArgs(args.arguments || {}, []),
    };
  }

  if (ev.name === "dispatch_subagent" || ev.phase === "subagent") {
    // 子 agent 的**调研任务**才是有信息量的东西，"子任务"三个字等于没说。
    // 放进 title 而不是 detail：detail 在芯片里会被挤成一小截。
    const task = String(args.task || "").trim();
    return {
      ...base,
      phase: "subagent",
      icon: PHASE_ICONS.subagent,
      title: task || "子 agent 调研",
    };
  }

  const phase: StepPhase =
    ev.phase === "knowledge" ? "knowledge" : ev.phase === "plan" ? "plan" : "tool";
  return {
    ...base,
    phase,
    icon: PHASE_ICONS[phase],
    title: TOOL_VERBS[ev.name] || ev.name || "执行步骤",
    detail: summarizeArgs(args),
  };
}

/**
 * 自由文本兜底 —— agent serve < v1.9 只发人话叙述，没有结构化步骤。
 * 这时不做正则猜工具名（叙述文案一改就全错），老老实实渲染成一行注记，
 * 用户至少能看到「它正在做什么」，而不是一个不动的转圈。
 */
export function noteStep(text: string, seq: number): ConsoleStep {
  const line = text.trim();
  return {
    key: `note-${seq}`,
    seq,
    phase: "note",
    name: "",
    icon: PHASE_ICONS.note,
    title: line.length > 160 ? line.slice(0, 160) + "…" : line,
    status: "ok",
  };
}

/** 耗时显示：<1s 用毫秒，其余保留一位小数。 */
export function formatMs(ms?: number): string {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms <= 0) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** 把同一 id 的 running → ok/error 就地合并，保持时间线只有一行。 */
export function mergeStep(list: ConsoleStep[], next: ConsoleStep): ConsoleStep[] {
  const idx = list.findIndex((s) => s.key === next.key && s.phase !== "note");
  if (idx < 0) {
    // 新来的子 agent，若此刻已有同类在跑 —— 它们是真并行（见 ConsoleStep.parallel）。
    if (next.phase === "subagent" && next.status === "running") {
      const running = list.filter((s) => s.phase === "subagent" && s.status === "running");
      if (running.length) {
        return [
          ...list.map((s) => (running.includes(s) ? { ...s, parallel: true } : s)),
          { ...next, parallel: true },
        ];
      }
    }
    return [...list, next];
  }
  const prev = list[idx];
  const merged: ConsoleStep = {
    ...prev,
    ...next,
    // 收尾事件常常不再带参数，别把已经显示出来的明细擦掉。
    detail: next.detail || prev.detail,
    title: next.title || prev.title,
    args: next.args && Object.keys(next.args).length ? next.args : prev.args,
    destructive: next.destructive ?? prev.destructive,
    // 收尾事件不带这个标记，别把已经判定出来的并行关系擦掉
    parallel: next.parallel ?? prev.parallel,
  };
  const out = [...list];
  out[idx] = merged;
  return out;
}
