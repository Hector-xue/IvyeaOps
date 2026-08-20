/**
 * 「执行叙述铺开，但铺不爆页面」的浏览器 E2E。
 *
 * 这条用例的历史值得写下来，因为它拦的是**同一个错的两个方向**：
 *   · 最早：一轮里每一步都铺开渲染 —— 192 步能把整页糊满，把回答挤出屏幕。
 *   · 上一版矫枉过正：整轮压成**一行**，只显示最后半句话 —— 用户盯着屏幕，
 *     既不知道刚才那 15 步干了什么，也不知道接下来要干什么（真实投诉）。
 * 现在的解法是**聚合**：连续的常规工具折成一行并按类型计数，写操作/子 agent/
 * MCP/计划这些"各自是一件事"的步骤才单独成行。所以 192 步 → 一行，而一段思考、
 * 一次写文件都看得见。
 *
 * 三件事只有真实浏览器排完版才证得了：铺出来占几行、展开后不许把页面撑爆、
 * 折叠开关看得见点得着。
 *
 * 跑的是 src/components/console/ActivityFeed.tsx + src/styles/workbench.css 的真实代码。
 *
 * 跑：node e2e/activity-line.mjs
 */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { WsCDP, chromeArgs, click, delay, evaluate, waitFor } from "./cdp.mjs";

/** 步数照着用户那张截图来：192 步，铺开就是一整屏芯片墙。 */
const STEP_COUNT = 192;

const HARNESS = `
import { useState } from "react";
import { createRoot } from "react-dom/client";
import ActivityFeed from "../src/components/console/ActivityFeed";
import "../src/styles/workbench.css";

const STEP_COUNT = ${STEP_COUNT};

const NAMES = ["run_command", "read_file", "grep", "list_dir"];

function makeSteps(n, runningLast) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const name = NAMES[i % NAMES.length];
    out.push({
      key: "s" + i, seq: i, phase: "tool", name,
      title: name, icon: "⊙", detail: "#" + i,
      status: runningLast && i === n - 1 ? "running" : "ok",
      ms: 624, at: Date.now() - (n - i) * 1000, args: { command: "npm --version" },
    });
  }
  return out;
}

function App() {
  const [steps, setSteps] = useState(() => makeSteps(STEP_COUNT, true));
  const [running, setRunning] = useState(true);
  const [thoughts, setThoughts] = useState([
    { seq: 0, text: "先把现状查清楚：这一批工具是用来确认数据源的。" },
  ]);
  window.__push = () => setSteps((prev) => [...prev, {
    key: "extra-" + prev.length, seq: prev.length, phase: "tool", name: "run_command",
    title: "最新一步", icon: "⊙", detail: "tasklist", status: "running", at: Date.now(),
  }]);
  window.__write = () => setSteps((prev) => [...prev, {
    key: "w-" + prev.length, seq: prev.length, phase: "tool", name: "write_file",
    title: "写入文件", icon: "⊙", detail: "IvyGrow.tsx", status: "ok", ms: 12, at: Date.now(),
  }]);
  window.__think = () => setThoughts((prev) => [...prev,
    { seq: STEP_COUNT, text: "查完了，接下来把结论写进文件。" }]);
  window.__finish = () => { setRunning(false); setSteps((prev) => prev.map((s) => ({ ...s, status: "ok" }))); };

  return (
    <div style={{ width: "900px", padding: "16px" }}>
      <ActivityFeed steps={steps} thoughts={thoughts} skills={[]}
                    elapsedMs={1183200} running={running} />
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
`;

const PAGE = `<!doctype html><html><body style="margin:0"><div id="root"></div>
<link rel="stylesheet" href="./bundle.css">
<script type="module" src="./bundle.js"></script></body></html>`;

const visibleCount = (send, selector) => evaluate(send, `(() => {
  return [...document.querySelectorAll("${selector}")]
    .filter((el) => el.getBoundingClientRect().height > 0).length;
})()`);

const rectOf = (send, selector) => evaluate(send, `(() => {
  const el = document.querySelector("${selector}");
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { w: r.width, h: r.height };
})()`);

const textOf = (send, selector) => evaluate(send,
  `(document.querySelector("${selector}")?.textContent || "").trim()`);

async function run() {
  const work = await mkdtemp(path.join(os.tmpdir(), "ivyea-feed-e2e-"));
  const entry = path.resolve("e2e/.activity-harness.jsx");
  await writeFile(entry, HARNESS, "utf8");
  const bundle = spawnSync("npx", ["esbuild", entry, "--bundle", "--format=esm", "--jsx=automatic",
                                   // 背景图是运行时由服务端提供的绝对路径（/art/bg.png），
                                   // 打包器解析不到也不需要解析 —— 这条用例量的是排版，不是背景。
                                   "--external:/art/*",
                                   `--outfile=${path.join(work, "bundle.js")}`],
                           { cwd: path.resolve("."), encoding: "utf8" });
  if (bundle.status !== 0) throw new Error(bundle.stderr || bundle.stdout || "harness bundle failed");
  await writeFile(path.join(work, "index.html"), PAGE, "utf8");

  const profile = await mkdtemp(path.join(os.tmpdir(), "ivyea-feed-profile-"));
  const { cdp, chrome } = await WsCDP.launch(chromeArgs(profile));
  try {
    const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
    const send = (method, params = {}) => cdp.send(method, params, sessionId);
    const errors = [];
    cdp.on("Runtime.exceptionThrown", (params, s) => {
      if (s === sessionId) errors.push(params.exceptionDetails?.text || "browser exception");
    });
    await Promise.all([send("Page.enable"), send("Runtime.enable")]);
    await send("Emulation.setDeviceMetricsOverride",
               { width: 1200, height: 800, deviceScaleFactor: 1, mobile: false });
    await send("Page.navigate", { url: "file://" + path.join(work, "index.html") });
    await waitFor(send, `!!document.querySelector(".af")`, "执行叙述", 20_000);

    // ── 1. 192 步只折成**一行**，而且那一行说得出都干了什么 ────────────────
    assert.equal(await visibleCount(send, ".af-group"), 1, "连续的常规工具要折成一行");
    const summary = await textOf(send, ".af-group-text");
    for (const need of ["跑了", "读了", "搜索"]) {
      assert.ok(summary.includes(need), `摘要要按类型说清楚：${JSON.stringify(summary)}`);
    }
    assert.ok(/48/.test(summary), `192 步按四类平分，每类 48：${JSON.stringify(summary)}`);

    // 整条叙述在没展开时必须很矮 —— 它是一条叙述，不是一面芯片墙
    const feed = await rectOf(send, ".af");
    assert.ok(feed.h < 220, `没展开时不该超过 220px，实际 ${feed.h}px`);

    // ── 2. 思考是人话，独立成行 ─────────────────────────────────────────
    assert.equal(await visibleCount(send, ".af-think"), 1);
    assert.ok((await textOf(send, ".af-think p")).includes("先把现状查清楚"));

    // ── 3. 写文件不许被折进计数里 —— 它是独立的一件事 ─────────────────────
    await evaluate(send, `window.__think(), window.__write(), true`);
    await delay(150);
    assert.equal(await visibleCount(send, ".af-single"), 1, "写操作单独成行");
    assert.ok((await textOf(send, ".af-single")).includes("IvyGrow.tsx"), "写了哪个文件要看得见");
    assert.equal(await visibleCount(send, ".af-think"), 2, "第二段思考跟着出现");

    // ── 4. 展开那一批：能看，但不许把页面撑爆 ────────────────────────────
    await click(send, ".af-group-head");
    await waitFor(send, `document.querySelectorAll(".af-row").length > 100`, "展开", 10_000);
    const body = await rectOf(send, ".af-group-body");
    assert.ok(body.h <= 800 * 0.4 + 2, `展开的日志自己滚，最多 40vh，实际 ${body.h}px`);
    const scrollable = await evaluate(send, `(() => {
      const el = document.querySelector(".af-group-body");
      return el.scrollHeight - el.clientHeight;
    })()`);
    assert.ok(scrollable > 100, "展开后是一块可翻的日志，不是把页面顶长");

    // ── 5. 跑完之后：不再有转的图标，收尾行消失 ──────────────────────────
    await click(send, ".af-group-head");          // 收起
    await evaluate(send, `window.__finish(), true`);
    await delay(200);
    assert.equal(await visibleCount(send, ".af-foot"), 0, "跑完就不该再有『第几步·用时』那行");
    assert.equal(await visibleCount(send, ".af-spin"), 0, "跑完了不许还有图标在转");

    assert.deepEqual(errors, [], "页面不能抛异常");
    process.stdout.write("activity feed browser E2E passed\n");
  } finally {
    chrome.kill("SIGKILL");
    await rm(entry, { force: true }).catch(() => {});
    await rm(work, { recursive: true, force: true }).catch(() => {});
    await rm(profile, { recursive: true, force: true }).catch(() => {});
  }
}

await run();
