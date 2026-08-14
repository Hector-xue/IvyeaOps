/**
 * 会话统计的聚合 —— 从每轮的原始计时/用量算出统计条上的那几个数。
 *
 * 单独成文件是为了能脱开 React 直接测：这里每一个数都可能被用来判断"钱花在哪、
 * 慢在哪"，算错比不显示更糟。
 *
 * 一条贯穿全文件的规矩：**测不到就返回 undefined，不返回 0**。
 * 上层据此显示「—」。0 是一个具体的断言（"没花时间/没有 token"），
 * 而我们想说的往往是"这一项没测到"。
 */
import type { ConsoleStep } from "./stepLabels";

/** agent 回报的用量。llm_ms 只有 ≥ v1.10.3 的 agent 有。 */
export type TurnUsage = {
  prompt_tokens?: number;
  completion_tokens?: number;
  prompt_cache_hit_tokens?: number;
  llm_ms?: number;
  [k: string]: any;
};

/** 一轮的原始计时。全部由前端在流式过程中记下。 */
export type TurnMetrics = {
  startedAt: number;
  /** 第一个正文 token 到达的时刻。 */
  firstTokenAt?: number;
  /** 最后一个正文 token 到达的时刻。 */
  lastTokenAt?: number;
  usage?: TurnUsage;
};

export type TurnStats = {
  turns: number;
  steps: number;
  elapsedMs: number;
  llmMs?: number;
  firstTokenMs?: number;
  tokensPerSec?: number;
  cacheHitRate?: number;
  promptTokens?: number;
  completionTokens?: number;
};

export type StatsInput = {
  steps?: ConsoleStep[];
  elapsedMs?: number;
  metrics?: TurnMetrics;
};

const num = (v: any): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);

export function aggregateStats(turns: StatsInput[]): TurnStats {
  const out: TurnStats = { turns: 0, steps: 0, elapsedMs: 0 };

  let llmMs = 0;
  let sawLlmMs = false;
  let firstTokenTotal = 0;
  let firstTokenCount = 0;
  let streamMs = 0;
  let prompt = 0;
  let completion = 0;
  let cached = 0;
  let sawUsage = false;

  for (const t of turns) {
    out.turns += 1;
    // 规划/汇报（todo_write、progress_update）与老 agent 的自由文本注记不算"步"——
    // 它们讲的是"在组织怎么做"，混进来会让步数虚高一倍不止（实测 19 步里 12 步是它们）。
    out.steps += (t.steps || []).filter((s) => s.phase !== "note" && s.phase !== "plan").length;
    out.elapsedMs += num(t.elapsedMs);

    const m = t.metrics;
    if (!m) continue;
    // 用 isFinite 而不是真值判断：时刻是可以为 0 的（测试里就是），
    // `if (m.startedAt)` 会把那一轮整个跳过，平均值随之算错。
    const has = (v: any) => typeof v === "number" && Number.isFinite(v);
    if (has(m.firstTokenAt) && has(m.startedAt) && m.firstTokenAt! > m.startedAt) {
      firstTokenTotal += m.firstTokenAt! - m.startedAt;
      firstTokenCount += 1;
    }
    if (has(m.firstTokenAt) && has(m.lastTokenAt) && m.lastTokenAt! > m.firstTokenAt!) {
      streamMs += m.lastTokenAt! - m.firstTokenAt!;
    }
    const u = m.usage;
    if (!u) continue;
    sawUsage = true;
    prompt += num(u.prompt_tokens);
    completion += num(u.completion_tokens);
    cached += num(u.prompt_cache_hit_tokens);
    if (typeof u.llm_ms === "number" && Number.isFinite(u.llm_ms)) {
      sawLlmMs = true;
      llmMs += u.llm_ms;
    }
  }

  // 老 agent 一个字都不回报 llm_ms —— 那就整项不显示。显示 0 会被读成"没调模型"。
  if (sawLlmMs) out.llmMs = llmMs;
  if (firstTokenCount) out.firstTokenMs = firstTokenTotal / firstTokenCount;
  // 速度按**正文流式时长**算，不按整轮时长：整轮里工具执行占的那几分钟不该摊进
  // "模型每秒吐多少字"，否则一个慢工具就能把速度算成个位数。
  if (streamMs > 0 && completion > 0) out.tokensPerSec = completion / (streamMs / 1000);
  if (sawUsage) {
    out.promptTokens = prompt;
    out.completionTokens = completion;
    if (prompt > 0) out.cacheHitRate = cached / prompt;
  }
  return out;
}

export default aggregateStats;
