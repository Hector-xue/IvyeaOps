/**
 * 审批三档的换算 —— src/lib/approvalModes.ts 的用例。
 *
 * 为什么这几十行纯函数值得有测试：**它决定 Agent 能不能改线上数据**。
 * agent 那边的判据是 `execute = 放开审批 && !plan_mode`，所以 plan_mode 和 approval
 * 必须成对；只改一个的结果是"界面上开关变了、行为一点没变"（假开关），或者更糟 ——
 * 选了只读却把 plan_mode 关掉，等于无声开写。这种错在界面上看不出来。
 *
 * 跑：node e2e/approval-modes.mjs
 */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const work = await mkdtemp(path.join(os.tmpdir(), "ivyea-approval-"));
const outfile = path.join(work, "approvalModes.mjs");
const build = spawnSync("npx", ["esbuild", "src/lib/approvalModes.ts", "--format=esm", `--outfile=${outfile}`],
                        { cwd: path.resolve("."), encoding: "utf8" });
if (build.status !== 0) throw new Error(build.stderr || build.stdout || "bundle failed");
const { APPROVAL_MODES, approvalPayload, approvalFromWire, approvalLabel } =
  await import(pathToFileURL(outfile).href);

// ── 三档齐全，且顺序是"越往后越危险"────────────────────────────────────────
assert.deepEqual(APPROVAL_MODES.map((m) => m.value), ["readonly", "ask", "full"]);
assert.deepEqual(APPROVAL_MODES.map((m) => m.wire), ["none", "remote", "auto"]);
assert.deepEqual(APPROVAL_MODES.map((m) => m.tone), ["calm", "warn", "danger"]);
for (const m of APPROVAL_MODES) {
  assert.ok(m.label && m.hint, `${m.value} 必须有给人看的名字和解释`);
}

// ── payload：plan_mode 与 approval 成对 ────────────────────────────────────
assert.deepEqual(approvalPayload("readonly"), { plan_mode: true, approval: "none" });
assert.deepEqual(approvalPayload("ask"), { plan_mode: false, approval: "remote" });
assert.deepEqual(approvalPayload("full"), { plan_mode: false, approval: "auto" });
// 只读**必须**带 plan_mode:true —— 少了它 agent 侧的 execute 判据就只剩 approval 一半
assert.equal(approvalPayload("readonly").plan_mode, true, "只读绝不能把 plan_mode 关掉");

// ── 认不出来的值一律落只读：判错的方向必须是"少做"────────────────────────
assert.deepEqual(approvalPayload("yolo"), { plan_mode: true, approval: "none" });
assert.equal(approvalFromWire("auto"), "full");
assert.equal(approvalFromWire("remote"), "ask");
assert.equal(approvalFromWire("none"), "readonly");
assert.equal(approvalFromWire(undefined), "readonly");
assert.equal(approvalFromWire("bypass"), "readonly");

// ── 名字：界面档位和线上语义两套词都得认（历史预设里存的是线上语义）────────
assert.equal(approvalLabel("full"), approvalLabel("auto"));
assert.equal(approvalLabel("ask"), approvalLabel("remote"));
assert.equal(approvalLabel("readonly"), approvalLabel("none"));
assert.equal(approvalLabel("说不清"), approvalLabel("readonly"), "认不出来按只读说");

await rm(work, { recursive: true, force: true }).catch(() => {});
process.stdout.write("approval mode checks passed\n");
