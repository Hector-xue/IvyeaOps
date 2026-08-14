/**
 * 「执行过程只占一行」的浏览器 E2E。
 *
 * 这条用例存在的理由：上一版把一轮里的每一步都铺开渲染，192 步的任务能把整页糊满、
 * 把回答挤出屏幕，而收起它的开关是行尾一个 9px 的箭头，用户根本看不出那是个按钮。
 * 这三件事——**只显示一行**、**开关看得见点得着**、**展开后不许把页面撑爆**——
 * 全是渲染后才成立的属性：JSX 快照测不出高度，jsdom 没有布局，只有真实浏览器
 * 拿真实 CSS 排完版才能证明。
 *
 * 跑的是 src/components/console/StepTimeline.tsx + src/styles/workbench.css 的真实代码
 * （esbuild 打包进 harness）。
 *
 * 跑：node e2e/activity-line.mjs
 */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { WsCDP, chromeArgs, click, delay, evaluate, waitFor } from "./cdp.mjs";

/** 步数照着用户那张截图来：192 步，铺开就是一整屏芯片墙。 */
const STEP_COUNT = 192;

const HARNESS = `
import { useState } from "react";
import { createRoot } from "react-dom/client";
import StepTimeline from "../src/components/console/StepTimeline";
import "../src/styles/workbench.css";

const STEP_COUNT = ${STEP_COUNT};

function makeSteps(n, runningLast) {
  const out = [];
  for (let i = 0; i < n; i++) {
    out.push({
      key: "s" + i,
      seq: i,
      phase: "tool",
      name: "run_command",
      title: "执行命令",
      icon: "⚙",
      detail: "npm --version #" + i,
      status: runningLast && i === n - 1 ? "running" : "ok",
      ms: 624,
      at: Date.now() - (n - i) * 1000,
      args: { command: "npm --version" },
    });
  }
  return out;
}

function App() {
  const [steps, setSteps] = useState(() => makeSteps(STEP_COUNT, true));
  const [running, setRunning] = useState(true);
  // 用例从外面驱动状态变化，模拟流式过程中新步骤不断到达
  window.__push = () => setSteps((prev) => [...prev, {
    key: "extra-" + prev.length, seq: prev.length, phase: "tool", name: "run_command",
    title: "最新一步", icon: "⚙", detail: "tasklist", status: "running", at: Date.now(),
  }]);
  window.__finish = () => { setRunning(false); setSteps((prev) => prev.map((s) => ({ ...s, status: "ok" }))); };

  return (
    <div style={{ width: "900px", padding: "16px" }}>
      <StepTimeline steps={steps} skills={[]} elapsedMs={1183200} running={running} />
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
`;

const PAGE = `<!doctype html><html><body style="margin:0"><div id="root"></div>
<link rel="stylesheet" href="./bundle.css">
<script type="module" src="./bundle.js"></script></body></html>`;

/** 可见元素个数 —— display:none / 高度为 0 的不算。 */
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

async function run() {
  const work = await mkdtemp(path.join(os.tmpdir(), "ivyea-activity-e2e-"));
  // 入口必须落在 client/ 里面：放 /tmp 的话 esbuild 找不到 react / react-dom，
  // 也解析不到 ../src。跑完在 finally 里删掉。
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

  const profile = await mkdtemp(path.join(os.tmpdir(), "ivyea-activity-profile-"));
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
    // 视口高度决定 40vh 的绝对值，钉死它这条断言才有意义
    await send("Emulation.setDeviceMetricsOverride",
               { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
    await send("Page.navigate", { url: pathToFileURL(path.join(work, "index.html")).href });
    await waitFor(send, `!!document.querySelector(".cs-timeline")`, "timeline");

    // ── 1. 192 步，页面上只有一行 ──────────────────────────────────────────
    assert.equal(await visibleCount(send, ".cs-live-text"), 1, "活动行必须有且只有一行");
    assert.equal(await visibleCount(send, ".cs-row"), 0,
                 "收起时一条步骤日志都不该渲染（上一版正是在这里把 192 步全铺开的）");
    const collapsed = await rectOf(send, ".cs-timeline");
    assert.ok(collapsed.h <= 60,
              `收起态整块不该超过一行的高度（实测 ${collapsed.h}px）`);

    // ── 2. 展开开关必须看得见、点得着 ──────────────────────────────────────
    const toggle = await rectOf(send, ".cs-toggle");
    assert.ok(toggle, "得有一个展开按钮");
    assert.ok(toggle.h >= 28, `按钮高度至少 28px（实测 ${toggle.h}px）`);
    assert.ok(toggle.w >= 40, `按钮宽度至少 40px，得放得下文字（实测 ${toggle.w}px）`);
    assert.ok(String(await evaluate(send, `document.querySelector(".cs-toggle").textContent`)).includes("展开"),
              "按钮上要有文字，不能只有一个箭头");

    // ── 3. 展开：全部步骤都在，但面板不许把页面撑爆 ────────────────────────
    await click(send, ".cs-toggle");        // 真·点击：坐标点得中才算数
    await waitFor(send, `document.querySelectorAll(".cs-row").length > 0`, "log rows");
    assert.equal(await visibleCount(send, ".cs-row"), STEP_COUNT,
                 "展开后每一步都要能翻到");
    const log = await rectOf(send, ".cs-log");
    assert.ok(log.h <= 900 * 0.4 + 2, `日志面板不得超过 40vh（900×0.4=360，实测 ${log.h}px）`);
    const scrollable = await evaluate(send, `(() => {
      const el = document.querySelector(".cs-log");
      return el.scrollHeight > el.clientHeight + 10;
    })()`);
    assert.ok(scrollable, "192 步应当是在面板内部滚动，而不是把页面撑长");

    // ── 4. 收起后回到一行 ──────────────────────────────────────────────────
    await click(send, ".cs-toggle");
    await waitFor(send, `document.querySelectorAll(".cs-row").length === 0`, "collapsed again");
    assert.equal(await visibleCount(send, ".cs-live-text"), 1, "收起后仍然只有一行");

    // ── 5. 新一步到达时，活动行显示的是**最新**那一步 ──────────────────────
    await evaluate(send, `window.__push()`);
    await delay(300);
    const liveText = await evaluate(send, `document.querySelector(".cs-live-text").textContent`);
    assert.ok(String(liveText).includes("最新一步"),
              `活动行要跟着换成最新一步（实测「${liveText}」）`);
    assert.equal(await visibleCount(send, ".cs-live-text"), 1, "换行之后也还是只有一行");

    // ── 6. 结束后：仍是一行，且给出总账 ────────────────────────────────────
    await evaluate(send, `window.__finish()`);
    await delay(200);
    assert.equal(await visibleCount(send, ".cs-live-text"), 1, "结束后仍然只有一行");
    const tail = await evaluate(send, `document.querySelector(".cs-live-tail").textContent`);
    assert.ok(String(tail).includes("步"), `结束后要给出总步数（实测「${tail}」）`);

    assert.deepEqual(errors, []);
    process.stdout.write("activity line browser E2E passed\n");
  } finally {
    try { chrome.kill("SIGKILL"); } catch { /* ignore */ }
    await delay(200);        // 等 Chrome 真的放下 profile 里的文件句柄
    // 清理失败不许盖掉真正的失败原因（ENOTEMPTY 会把断言错误顶掉）
    for (const target of [profile, work]) {
      await rm(target, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }).catch(() => {});
    }
    await rm(path.resolve("e2e/.activity-harness.jsx"), { force: true }).catch(() => {});
  }
}

run().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exit(1);
});
