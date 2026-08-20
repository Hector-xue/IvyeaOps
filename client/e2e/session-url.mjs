/**
 * 「新建会话切走再切回，指令和输出还在」的浏览器 E2E。
 *
 * 这条用例存在的理由是一条真实投诉：用户在任务台里发了一条指令，切到别的板块再切
 * 回来，指令和输出全不见了 —— 他以为自己压根没发出去，于是又打了一遍（会话落盘
 * 文件里两条几乎重复的提问就是证据）。后端一个字都没丢，丢的是**视图**：新会话的
 * id 只存在内存 state 里，组件一重建就归零，而"恢复哪条会话"的唯一依据是地址栏的
 * `?session=`。
 *
 * 所以这里钉死四件事，全是渲染 + 路由 + 生命周期一起才成立的属性，单测测不到：
 *   1. 发出第一条消息后，会话 id 立刻进地址栏
 *   2. 写地址栏**不能打断正在跑的那一轮**（恢复 effect 里有 abort，被误触发就是灾难）
 *   3. 刷新、以及"切到别的板块再点回任务台"，两条路都要能把会话捞回来
 *   4. 「新建任务」仍然是干净的空白 —— 别为了恢复把新建也一起恢复了
 *
 * 跑的是真实的 <App/>（harness/main.tsx + mockApi），路由、懒加载、侧边栏全是真的。
 *
 * 跑：node e2e/session-url.mjs
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { WsCDP, chromeArgs, click, delay, evaluate, waitFor } from "./cdp.mjs";

const ORIGIN = "http://127.0.0.1:5199";

/** 起验证台的 vite，等它真的能响应了再往下走。 */
async function startHarness() {
  const vite = spawn("npx", ["vite", "--config", "vite.harness.config.ts"],
                     { cwd: path.resolve("."), stdio: ["ignore", "pipe", "pipe"] });
  let log = "";
  vite.stdout.on("data", (c) => { log += c.toString("utf8"); });
  vite.stderr.on("data", (c) => { log += c.toString("utf8"); });
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(ORIGIN + "/", { signal: AbortSignal.timeout(2000) });
      if (r.ok) return vite;
    } catch { /* 还没起来 */ }
    if (vite.exitCode !== null) throw new Error(`vite 退出了(${vite.exitCode})：${log.slice(-2000)}`);
    await delay(300);
  }
  throw new Error(`验证台 60s 内没起来：${log.slice(-2000)}`);
}

const sessionParam = (send) => evaluate(send,
  `new URLSearchParams(location.search).get("session") || ""`);

/** 页面上所有对话气泡的文本 —— 视图有没有归零看这个，不看 DOM 有没有渲染。 */
const bubbles = (send) => evaluate(send,
  `[...document.querySelectorAll(".cc-bubble")].map((el) => el.textContent.trim())`);

const bodyText = (send) => evaluate(send, `document.body.innerText`);

async function typeAndSend(send, text) {
  await click(send, ".cc-input");
  await send("Input.insertText", { text });
  for (const type of ["keyDown", "keyUp"]) {
    await send("Input.dispatchKeyEvent", {
      type, key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13,
      text: type === "keyDown" ? "\r" : undefined,
    });
  }
}

async function run() {
  const harness = await startHarness();
  const profile = await mkdtemp(path.join(os.tmpdir(), "ivyea-session-url-profile-"));
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
               { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });

    // ── 1. 空白任务台里发一条消息 ─────────────────────────────────────────
    await send("Page.navigate", { url: `${ORIGIN}/?r=/console` });
    await waitFor(send, `!!document.querySelector(".cc-input")`, "任务台输入框", 30_000);
    assert.equal(await sessionParam(send), "", "刚进来地址栏不该有 session");

    await typeAndSend(send, "帮我跑一下广告巡检");

    // ── 2. 会话 id 立刻进地址栏（onStart 一到就写，不等这一轮跑完）──────────
    await waitFor(send, `new URLSearchParams(location.search).get("session") === "s-live"`,
                  "会话 id 写进地址栏", 15_000);
    assert.equal(await evaluate(send, `location.pathname`), "/console", "路径不能被改掉");

    // ── 3. 写地址栏不能打断这一轮 ─────────────────────────────────────────
    // mock 的流按真实节奏铺：思考 ~6s → 工具 2.2s → 正文。恢复 effect 里有
    // abortRef.abort()，它要是被地址栏变化误触发，这段正文永远不会出现。
    await waitFor(send, `document.body.innerText.includes("其中 28% 来自单次点击成本上升")`,
                  "整轮流式跑完（没被会话恢复 effect 掐断）", 30_000);
    const live = await bubbles(send);
    assert.ok(live.some((t) => t.includes("帮我跑一下广告巡检")), `发出去的指令要在页面上：${JSON.stringify(live)}`);

    // ── 4. 刷新：靠地址栏把会话捞回来 ─────────────────────────────────────
    await send("Page.navigate", { url: `${ORIGIN}/console?session=s-live` });
    await waitFor(send, `!!document.querySelector(".cc-input")`, "刷新后任务台", 30_000);
    await waitFor(send, `document.querySelectorAll(".cc-bubble").length > 0`,
                  "刷新后会话被恢复", 15_000);
    const afterReload = await bubbles(send);
    assert.ok(afterReload.some((t) => t.includes("帮我跑一下广告巡检")),
              `刷新后指令要还在：${JSON.stringify(afterReload)}`);
    assert.equal(await sessionParam(send), "s-live", "刷新后地址栏要保住 session");

    // ── 5. 切到别的板块，再从侧边栏点回任务台 ────────────────────────────
    // 这就是用户投诉里的那条路。侧边栏「任务台」是个死链接 /console 的话，
    // 回来的是空白新任务 —— 地址栏写了也白写。
    await click(send, `a[href='/capabilities']`);
    await waitFor(send, `location.pathname === "/capabilities"`, "切到能力市场", 15_000);
    await click(send, `a[href^='/console']`);
    await waitFor(send, `location.pathname === "/console"`, "切回任务台", 15_000);
    await waitFor(send, `!!document.querySelector(".cc-input")`, "任务台重新挂载", 20_000);
    assert.equal(await sessionParam(send), "s-live", "从别的板块点回来要带着刚才那条会话");
    await waitFor(send, `document.querySelectorAll(".cc-bubble").length > 0`,
                  "切回来会话被恢复", 15_000);
    const afterSwitch = await bubbles(send);
    assert.ok(afterSwitch.some((t) => t.includes("帮我跑一下广告巡检")),
              `切走再切回，指令要还在：${JSON.stringify(afterSwitch)}`);

    // ── 6. 「新建任务」仍然是空白 ────────────────────────────────────────
    await click(send, `[data-tour='console-new']`);
    await waitFor(send, `new URLSearchParams(location.search).get("session") === null`,
                  "新建任务把 session 从地址栏抹掉", 15_000);
    await delay(500);
    assert.deepEqual(await bubbles(send), [], "新建任务必须是干净的空白");

    // 侧边栏「任务台」这时也不该再指回旧会话，否则点一下就把刚清空的会话又拽回来
    const consoleHref = await evaluate(send,
      `document.querySelector('a[href^="/console"]').getAttribute("href")`);
    assert.equal(consoleHref, "/console", "清空之后侧边栏链接要跟着回到空白任务台");

    assert.deepEqual(errors, [], "页面不能抛异常");
    if ((await bodyText(send)).includes("页面渲染出错")) throw new Error("页面渲染出错");
    process.stdout.write("session url checks passed\n");
  } finally {
    chrome.kill("SIGKILL");
    harness.kill("SIGTERM");
    await rm(profile, { recursive: true, force: true }).catch(() => {});
  }
}

await run();
