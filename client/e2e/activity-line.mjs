/**
 * 「一件事一行、行高恒定、可整体折叠」的浏览器 E2E。
 *
 * 这条用例记着这块界面**三次**改错的方向，每一条断言都对应其中一次：
 *   1. 每步都铺成卡片 → 192 步糊满整页，把回答挤出屏幕。
 *   2. 矫枉过正压成一行，只显示最后半句 → 用户不知道刚才干了什么、接下来干什么。
 *   3. 改成会换行的段落 → 思考是流式的，每来几个字就多一行，整屏文字上下跳
 *      （用户原话："文字上下不停跳动"），而且折叠开关做成 9px 小箭头，没人认得出。
 * 现在的形态对标 DeepSeek Harness：**一件事一行、单行截断、行高恒定**，外加一个
 * 常驻的、带文字的折叠开关。
 *
 * 行高恒定是这块界面唯一的硬约束 —— 只有真实浏览器排完版才量得到。
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
  // 一段很长的思考：会换行的实现在这里会变成三四行，把底下所有内容顶下去。
  window.__longthink = () => setThoughts((prev) => [...prev, { seq: STEP_COUNT,
    text: "这一段特别长，长到足以在任何正常宽度下换行好几次".repeat(8) }]);
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
                                   // 字体同理：@font-face 里是运行时的绝对路径
                                   // （/fonts/*.woff2），打包器解析不到就直接报错，
                                   // 整条用例连页面都跑不起来。这条量的是排版不是字形。
                                   "--external:/fonts/*",
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

    // ── 1. 一件事一行，而且**每一行等高** ─────────────────────────────────
    const lines = await evaluate(send, `(() => {
      const rows = [...document.querySelectorAll(".af-line")];
      const hs = rows.map((el) => Math.round(el.getBoundingClientRect().height));
      return { n: rows.length, heights: [...new Set(hs)] };
    })()`);
    // 默认只铺最新的 120 行，更早的折进一个按钮里 —— 几百行时用户看的永远是最新那批。
    assert.equal(lines.n, 120, "默认铺最新 120 行");
    assert.deepEqual(lines.heights, [22],
                     `每一行都必须是同一个高度，实际 ${JSON.stringify(lines.heights)}`);
    assert.ok((await textOf(send, ".af-more")).includes("更早"), "更早的过程要有一条路回去");
    await click(send, ".af-more");
    await delay(200);
    assert.equal(await visibleCount(send, ".af-line"), STEP_COUNT + 1,
                 "点开之后 192 步 + 1 段思考 = 193 行，一件事一行");

    // ── 1b. 每一列在所有行里都从同一个 x 起 ───────────────────────────────
    // 用户原话："好乱啊，感觉参差不齐"。根因是思考行没有状态点，flex 布局下它后面
    // 的每一列都整体左移一格。改网格之后这里钉死：类型名、摘要各自只有一个起点。
    const cols = await evaluate(send, `(() => {
      const pick = (sel) => [...document.querySelectorAll(".af-line " + sel)]
        .map((el) => Math.round(el.getBoundingClientRect().x));
      return { kind: [...new Set(pick(".af-kind"))], text: [...new Set(pick(".af-text"))],
               icon: [...new Set(pick(".af-icon"))] };
    })()`);
    assert.equal(cols.kind.length, 1, `类型名必须只有一个起点：${JSON.stringify(cols.kind)}`);
    assert.equal(cols.icon.length, 1, `图标必须只有一个起点：${JSON.stringify(cols.icon)}`);
    assert.equal(cols.text.length, 1, `摘要必须只有一个起点：${JSON.stringify(cols.text)}`);

    // ── 2. 一行绝不换行：内容再长也是省略号，不是第二行 ─────────────────────
    await evaluate(send, `window.__longthink(), true`);
    await delay(150);
    const tall = await evaluate(send, `(() => {
      const rows = [...document.querySelectorAll(".af-line")];
      return rows.filter((el) => el.getBoundingClientRect().height > 23).length;
    })()`);
    assert.equal(tall, 0, "一段很长的思考也只能占一行 —— 换行就是上下跳动的来源");

    // ── 3. 思考是人话，独立成行 ─────────────────────────────────────────
    assert.ok((await textOf(send, ".af-think")).includes("先把现状查清楚"));

    // ── 4. 写文件看得见文件名 ───────────────────────────────────────────
    await evaluate(send, `window.__write(), true`);
    await delay(150);
    const feedText = await textOf(send, ".af-body");
    assert.ok(feedText.includes("IvyGrow.tsx"), "写了哪个文件要看得见");

    // ── 5. 折叠开关：看得见、点得着、说人话 ──────────────────────────────
    const head = await rectOf(send, ".af-head");
    assert.ok(head.h >= 20, `折叠开关要有正常的可点高度，实际 ${head.h}px`);
    assert.ok((await textOf(send, ".af-head-toggle")).includes("收起"), "开关上要写着字");
    assert.ok((await textOf(send, ".af-head-meta")).includes("步"), "收起前就该看得到几步");
    await click(send, ".af-head");
    await delay(200);
    assert.equal(await visibleCount(send, ".af-line"), 0, "收起后一行都不剩");
    const collapsed = await rectOf(send, ".af");
    assert.ok(collapsed.h <= 30, `收起后整块只剩一行，实际 ${collapsed.h}px`);
    assert.ok((await textOf(send, ".af-head-toggle")).includes("展开"), "收起后开关要改口");
    await click(send, ".af-head");
    await delay(200);
    assert.ok(await visibleCount(send, ".af-line") > 100, "再点一下全都回来");

    // ── 6. 跑完之后不许还有东西在转 ──────────────────────────────────────
    await evaluate(send, `window.__finish(), true`);
    await delay(200);
    assert.equal(await visibleCount(send, ".af-run"), 0, "跑完了不许还有状态点在呼吸");
    assert.equal(await visibleCount(send, ".thinking-dots"), 0, "跑完了思考那三个点要收掉");

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
