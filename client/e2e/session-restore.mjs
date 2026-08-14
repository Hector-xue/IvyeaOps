/**
 * 历史会话的恢复映射 —— src/lib/sessionRestore.ts 的用例。
 *
 * 这段代码修的是一条真实投诉："刷新之后再点开历史会话，自己发的一部分指令和整个
 * 执行过程都不见了"。所以这里钉的三件事就是那条投诉的反面：
 *   1. 提问一条都不能少（原因见 agent 侧的末 30 条截断）
 *   2. 步骤要挂到**它所属的那一轮**，不能整堆糊在最后一轮上
 *   3. 老 agent 的响应（没有 steps / turns 字段）不能把页面搞崩
 *
 * 缝合靠 call_id 而不是下标 —— 上下文压缩、导入的历史、persist=false 的轮次都会
 * 让下标错位，那种错位在界面上表现为"步骤挂错了轮"，而且不会有任何报错。
 *
 * 跑：node e2e/session-restore.mjs
 */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const work = await mkdtemp(path.join(os.tmpdir(), "ivyea-restore-"));
const outfile = path.join(work, "sessionRestore.mjs");
const build = spawnSync("npx", ["esbuild", "src/lib/sessionRestore.ts", "--bundle", "--format=esm",
                                `--outfile=${outfile}`],
                        { cwd: path.resolve("."), encoding: "utf8" });
if (build.status !== 0) throw new Error(build.stderr || build.stdout || "bundle failed");
const { restoreSession } = await import(pathToFileURL(outfile).href);

const step = (id, name, extra = {}) => ({
  type: "step", id, seq: 0, phase: "tool", name, status: "ok", ms: 120,
  args: { command: "ls" }, ...extra,
});

// ── 两轮，各带一次工具调用 ────────────────────────────────────────────────
const detail = {
  messages: [
    { role: "user", content: "第一个问题" },
    { role: "assistant", content: "", tool_calls: [{ id: "c1", name: "run_command" }] },
    { role: "tool", tool_call_id: "c1", content: "结果一" },
    { role: "assistant", content: "第一个回答" },
    { role: "user", content: "第二个问题" },
    { role: "assistant", content: "", tool_calls: [{ id: "c2", name: "list_dir" }] },
    { role: "tool", tool_call_id: "c2", content: "结果二" },
    { role: "assistant", content: "第二个回答" },
  ],
  steps: [step("c1", "run_command"), step("c2", "list_dir")],
  skill_matches: [{ anchor: "c2", skills: [{ id: "amazon.ads", title: "广告优化" }] }],
  turns: { total: 9, from: 7, to: 9, has_more: true },
};

{
  const out = restoreSession(detail);
  assert.deepEqual(out.turns.map((t) => t.role),
                   ["user", "assistant", "user", "assistant"], "问答成对恢复");
  assert.deepEqual(out.turns.map((t) => t.text),
                   ["第一个问题", "第一个回答", "第二个问题", "第二个回答"]);
  // 步骤挂到各自那一轮 —— 全糊在最后一轮上是这类缝合最典型的错法
  assert.deepEqual(out.turns[1].steps.map((s) => s.name), ["run_command"]);
  assert.deepEqual(out.turns[3].steps.map((s) => s.name), ["list_dir"]);
  // 中文工具名来自 stepLabels 那套（和直播时同一份代码）
  assert.equal(out.turns[1].steps[0].title, "执行命令");
  assert.equal(out.turns[3].steps[0].title, "列目录");
  assert.equal(out.turns[1].steps[0].ms, 120, "耗时照实带回来");
  // 技能锚在第二轮
  assert.equal(out.turns[1].skills, undefined);
  assert.deepEqual(out.turns[3].skills.map((s) => s.title), ["广告优化"]);
  assert.equal(out.hasMore, true);
  assert.equal(out.from, 7);
}

// ── 注入给模型的技能/知识块不许进气泡 ──────────────────────────────────────
{
  const out = restoreSession({
    messages: [{ role: "user", content: "帮我看下广告\n\n[Ivyea Skill：本轮相关可复用流程]\n一大坨说明书" }],
  });
  assert.deepEqual(out.turns.map((t) => t.text), ["帮我看下广告"]);
}

// ── 老 agent 的响应：没有 steps / skill_matches / turns ────────────────────
{
  const out = restoreSession({
    messages: [
      { role: "user", content: "问题" },
      { role: "assistant", content: "", tool_calls: [{ id: "x1", name: "run_command" }] },
      { role: "assistant", content: "回答" },
    ],
  });
  assert.deepEqual(out.turns.map((t) => t.text), ["问题", "回答"]);
  // 没有步骤记录时宁可少一行，也不拿工具名编一条"状态未知"的出来充数
  assert.equal(out.turns[1].steps, undefined);
  assert.equal(out.hasMore, false);
  assert.equal(out.total, 0);
}

// ── 一轮以工具调用收尾（模型没再说话）：步骤不能丢 ──────────────────────────
{
  const out = restoreSession({
    messages: [
      { role: "user", content: "问题" },
      { role: "assistant", content: "先看看", tool_calls: [{ id: "c1", name: "run_command" }] },
      { role: "tool", tool_call_id: "c1", content: "ok" },
    ],
    steps: [step("c1", "run_command")],
  });
  const withSteps = out.turns.filter((t) => t.steps?.length);
  assert.equal(withSteps.length, 1, "最后一批步骤要归到 assistant 轮上，不能凭空消失");
  assert.equal(withSteps[0].steps[0].name, "run_command");
}

// ── 同一次调用的 running / 收尾两条记录要合成一行 ──────────────────────────
{
  const out = restoreSession({
    messages: [
      { role: "user", content: "问题" },
      { role: "assistant", content: "", tool_calls: [{ id: "c1", name: "run_command" }] },
      { role: "assistant", content: "回答" },
    ],
    steps: [step("c1", "run_command", { status: "error" })],
  });
  assert.equal(out.turns[1].steps.length, 1);
  assert.equal(out.turns[1].steps[0].status, "error", "失败状态照实显示，不粉饰成完成");
}

// ── 空响应不炸 ────────────────────────────────────────────────────────────
{
  assert.deepEqual(restoreSession(null).turns, []);
  assert.deepEqual(restoreSession({ messages: [] }).turns, []);
}

await rm(work, { recursive: true, force: true }).catch(() => {});
process.stdout.write("session restore checks passed\n");
