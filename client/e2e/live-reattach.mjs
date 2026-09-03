/**
 * 断链重接不能把正文摞两遍。
 *
 * 用户报的是"还是一个劲的重复输出，只有再刷新一下才正常显示"，截图里同一张表连着
 * 出现三份、一份比一份长。根因不在模型（那一轮存档里 assistant 的正文从头到尾只有
 * 一份），在这条路上：管子断了之后前端**不重发**、改接活轮日志（agent 的
 * live_turn.follow），而活轮日志是**从头回放**的 —— 它不知道客户端手上已经有多少字。
 * 接回来的这一格里还攒着断线前那半截，回放的完整正文直接摞在后面。断两次摞三份。
 * 刷新之所以"就正常了"：刷新走存档，存档里本来就只有一份。
 *
 * 跑：node e2e/live-reattach.mjs
 */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

// turnStream 会在 requestAnimationFrame 里批量落字（一个字一次 setState 时长报告的
// markdown 每秒要重解析几十遍）。这里让它同步执行 —— 断言看的是最终落地的内容。
globalThis.window = {
  requestAnimationFrame: (cb) => { cb(); return 0; },
  cancelAnimationFrame: () => {},
};

const work = await mkdtemp(path.join(os.tmpdir(), "ivyea-reattach-"));
const outfile = path.join(work, "turnStream.mjs");
const build = spawnSync(
  "npx", ["esbuild", "src/lib/turnStream.ts", "--bundle", "--format=esm", `--outfile=${outfile}`],
  { cwd: path.resolve("."), encoding: "utf8" });
if (build.status !== 0) throw new Error(build.stderr || build.stdout || "bundle failed");
const { createTurnStream } = await import(pathToFileURL(outfile).href);

/** 一格轮次的最小状态机，形状照 Console 的 patchTurn。 */
function makeTurn() {
  const turn = { text: "", segments: [], steps: [], thoughts: [] };
  const stream = createTurnStream({
    patch: (p) => Object.assign(turn, typeof p === "function" ? p(turn) : p),
    notify: () => {}, setFileChanges: () => {}, setTodos: () => {}, setCtxUsage: () => {},
  });
  return { turn, stream };
}

const HEAD = "## 大厂具名薪资\n\n| 公司 | 月薪 |\n|---|---|\n| 字节 | 3.5–7 万 |";
const FULL = `${HEAD}\n| 阿里云 | 2–5 万 |\n| 腾讯云 | 3.5–6.5 万 |`;

// ── 断链前：已经流出半截正文 ──────────────────────────────────────────────
const { turn, stream } = makeTurn();
stream.handlers.onToken(HEAD);
assert.equal(turn.text, HEAD, "断线前手上有半截正文");

// ── 重新接进活轮日志：回放**从头再来一遍** ────────────────────────────────
stream.handlers.onLiveBegin({ running: true, seq: 12, dropped: 0 });
assert.equal(turn.text, "", "接进回放的第一件事是把旧的半截清掉");
assert.deepEqual(turn.segments, [], "分段同理 —— 回放会重新封一遍");

for (const chunk of [HEAD, "\n| 阿里云 | 2–5 万 |", "\n| 腾讯云 | 3.5–6.5 万 |"]) {
  stream.handlers.onToken(chunk);
}
stream.handlers.onFinal({ text: FULL });

assert.equal(turn.text, FULL, "回放完只有一份正文");
assert.equal(turn.text.split("| 字节 |").length - 1, 1, "那一行只出现一次（这条是回归用例）");
assert.equal(stream.text(), FULL, "内部缓冲也只有一份 —— 跟进建议、落盘都读它");

// ── 没断过的那条路一个字都不能变 ──────────────────────────────────────────
const plain = makeTurn();
plain.stream.handlers.onToken("正常的一轮");
plain.stream.handlers.onFinal({ text: "正常的一轮" });
assert.equal(plain.turn.text, "正常的一轮");

console.log("live-reattach: 全部通过");
