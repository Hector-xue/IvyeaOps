/**
 * 统计条的数 —— src/lib/turnStats.ts 的用例。
 *
 * 不进浏览器：这是纯计算，没有布局也没有事件时序。但它必须有用例 ——
 * 统计条上的每个数都可能被拿去判断"钱花在哪、慢在哪"，**算错比不显示更糟**。
 * 尤其是那条贯穿全文件的规矩：测不到就是 undefined（界面显示「—」），
 * 绝不返回 0（那是"没花时间/没有 token"这个具体断言）。
 *
 * 用 esbuild 把 TS 转成一个临时 esm 文件再 import —— 这个仓库的前端没有单测框架，
 * 而为了几十行纯函数引进一整套 runner 不划算。
 *
 * 跑：node e2e/turn-stats.mjs
 */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const work = await mkdtemp(path.join(os.tmpdir(), "ivyea-stats-"));
const outfile = path.join(work, "turnStats.mjs");
const build = spawnSync("npx", ["esbuild", "src/lib/turnStats.ts", "--format=esm", `--outfile=${outfile}`],
                        { cwd: path.resolve("."), encoding: "utf8" });
if (build.status !== 0) throw new Error(build.stderr || build.stdout || "bundle failed");
const { aggregateStats, mergeStats } = await import(pathToFileURL(outfile).href);

const step = (phase) => ({ key: Math.random().toString(36), seq: 0, phase, name: "x", title: "x", icon: "x", status: "ok" });

// ── 步数：规划/汇报与老 agent 的自由文本注记不算"步"────────────────────────
{
  const s = aggregateStats([{ steps: [step("tool"), step("mcp"), step("plan"), step("note"), step("subagent")] }]);
  assert.equal(s.steps, 3, "工具/MCP/子agent 算步，规划与注记不算");
  assert.equal(s.turns, 1);
}

// ── 老 agent（不回报 llm_ms）：整项缺席，而不是 0 ──────────────────────────
{
  const s = aggregateStats([{
    elapsedMs: 5000,
    metrics: { startedAt: 0, usage: { prompt_tokens: 100, completion_tokens: 20 } },
  }]);
  assert.equal(s.llmMs, undefined, "老 agent 不回报 llm_ms 时必须是 undefined —— 0 会被读成「没调模型」");
  assert.equal(s.promptTokens, 100);
}

// ── 一个 token 都没等到的轮次：首字/速度不能编 ────────────────────────────
{
  const s = aggregateStats([{ elapsedMs: 3000, metrics: { startedAt: 1000 } }]);
  assert.equal(s.firstTokenMs, undefined);
  assert.equal(s.tokensPerSec, undefined);
  assert.equal(s.promptTokens, undefined, "没有 usage 就不该出现 token 数");
  assert.equal(s.elapsedMs, 3000, "用时仍然照实给");
}

// ── 首字取各轮平均；速度按**正文流式时长**算，不摊进工具时间 ───────────────
{
  const s = aggregateStats([
    { elapsedMs: 60_000, metrics: {                       // 一轮跑了 60s，其中工具占大头
      startedAt: 0, firstTokenAt: 2000, lastTokenAt: 4000,
      usage: { prompt_tokens: 1000, completion_tokens: 200, prompt_cache_hit_tokens: 500, llm_ms: 4000 } } },
    { elapsedMs: 10_000, metrics: {
      startedAt: 100_000, firstTokenAt: 104_000, lastTokenAt: 106_000,
      usage: { prompt_tokens: 1000, completion_tokens: 200, prompt_cache_hit_tokens: 300, llm_ms: 3000 } } },
  ]);
  assert.equal(s.firstTokenMs, 3000, "首字 = (2000 + 4000) / 2");
  // 400 个输出 token / 4 秒流式 = 100 tok/s。若按整轮 70s 算就成了 5.7 —— 那不是模型的速度。
  assert.equal(Math.round(s.tokensPerSec), 100, "速度按流式时长算");
  assert.equal(s.llmMs, 7000);
  assert.equal(s.cacheHitRate, 0.4, "缓存命中 = 800 / 2000");
  assert.equal(s.promptTokens, 2000);
  assert.equal(s.completionTokens, 400);
}

// ── 缓存命中真的是 0：显示 0%，不是「—」（0% 是事实，不是缺数）────────────
{
  const s = aggregateStats([{ metrics: { startedAt: 0, usage: { prompt_tokens: 500, prompt_cache_hit_tokens: 0 } } }]);
  assert.equal(s.cacheHitRate, 0);
}

// ── 空会话不炸 ────────────────────────────────────────────────────────────
{
  const s = aggregateStats([]);
  assert.deepEqual(s, { turns: 0, steps: 0, elapsedMs: 0 });
}

// ── 落盘的累计账 + 本次页面跑的轮 ────────────────────────────────────────
// 打开历史会话时统计条上那些数就是这么来的：恢复出来的轮身上没有计时/用量，
// 全靠服务端那份累计。这一块算错的后果是用户看到别人的账。
{
  const base = { turns: 3, steps: 7, elapsed_ms: 90_000,
                 usage: { prompt_tokens: 30_000, completion_tokens: 1_200,
                          prompt_cache_hit_tokens: 15_000, llm_ms: 40_000 } };
  // 只恢复、没再问：数就是落盘那份
  const restoredOnly = mergeStats(base, aggregateStats([]));
  assert.equal(restoredOnly.turns, 3);
  assert.equal(restoredOnly.steps, 7);
  assert.equal(restoredOnly.elapsedMs, 90_000);
  assert.equal(restoredOnly.promptTokens, 30_000);
  assert.equal(restoredOnly.completionTokens, 1_200);
  assert.equal(restoredOnly.llmMs, 40_000);
  assert.equal(restoredOnly.cacheHitRate, 0.5, "缓存命中是比例，要按 token 重算不是相加");
  // 首字延迟落盘那份没有 —— 没测到就不显示，不能编一个
  assert.equal(restoredOnly.firstTokenMs, undefined);

  // 在历史会话里又问了一轮：两边相加，且**不重复计数**（恢复的轮不进 live）
  const live = aggregateStats([{
    elapsedMs: 10_000, steps: [step("tool")],
    metrics: { startedAt: 0, firstTokenAt: 500, lastTokenAt: 2500,
               usage: { prompt_tokens: 10_000, completion_tokens: 300, llm_ms: 5_000 } },
  }]);
  const merged = mergeStats(base, live);
  assert.equal(merged.turns, 4, "3 轮落盘 + 1 轮刚跑");
  assert.equal(merged.steps, 8);
  assert.equal(merged.elapsedMs, 100_000);
  assert.equal(merged.promptTokens, 40_000);
  assert.equal(merged.llmMs, 45_000);
  assert.equal(merged.firstTokenMs, 500, "这一轮测到的首字延迟照实带上");
}

// ── 老 agent：没有累计账 —— 行为必须和改动前一模一样 ──────────────────────
{
  const live = aggregateStats([{ elapsedMs: 1_000, steps: [step("tool")] }]);
  assert.deepEqual(mergeStats(null, live), live);
  assert.deepEqual(mergeStats(undefined, live), live);
  assert.deepEqual(mergeStats({}, live), live);
}

await rm(work, { recursive: true, force: true }).catch(() => {});
process.stdout.write("turn stats unit checks passed\n");
