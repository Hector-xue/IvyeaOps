/**
 * 「流式输出时还能往上翻」的浏览器 E2E。
 *
 * 这条用例存在的理由：这个 bug 只在**真实浏览器的事件时序**下成立 ——
 * scroll 事件是异步派发的，而流式追加内容是同步的。jsdom / 手工调 dispatchEvent
 * 都复现不了；只有真的用 CDP 派发 mouseWheel、真的让内容每 16ms 长一截，才能
 * 证明"用户往上翻之后不会被拽回底部"。
 *
 * 测的是 src/lib/useStickToBottom.ts 的真实代码（esbuild 打包进 harness），
 * 顺带跑一份"旧写法"（按滚动位置判）作为反面对照，钉死这次修的到底是什么。
 *
 * 跑：node e2e/stream-scroll.mjs
 */
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * CDP over WebSocket。
 *
 * 不用 knowledge-governance.mjs 那套 --remote-debugging-pipe：Chrome 147 起
 * 那条路会以 "Crashing due to FD ownership violation" 直接崩掉（本机实测，
 * 那条老 E2E 现在同样跑不起来）。调试端口 + WebSocket 走的是同一套协议。
 */
class WsCDP {
  constructor(ws, chrome) {
    this.ws = ws;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
    this.stderr = "";
    chrome.stderr.on("data", (chunk) => { this.stderr += chunk.toString("utf8"); });
    ws.addEventListener("message", (ev) => this.consume(String(ev.data)));
    ws.addEventListener("close", () => {
      for (const pending of this.pending.values()) pending.reject(new Error("CDP socket closed"));
      this.pending.clear();
    });
  }

  /** 起一个 headless Chrome 并连上它的浏览器级 CDP 端点。 */
  static async launch(args) {
    const chrome = spawn("google-chrome", args, { stdio: ["ignore", "ignore", "pipe"] });
    let buf = "";
    const url = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`Chrome never printed a DevTools URL: ${buf.slice(-2000)}`)), 20_000);
      chrome.stderr.on("data", (chunk) => {
        buf += chunk.toString("utf8");
        const hit = buf.match(/ws:\/\/[^\s]+/);
        if (hit) { clearTimeout(timer); resolve(hit[0]); }
      });
      chrome.on("exit", (code) => { clearTimeout(timer); reject(new Error(`Chrome exited early (${code}): ${buf.slice(-2000)}`)); });
    });
    const ws = new WebSocket(url);
    await new Promise((resolve, reject) => {
      ws.addEventListener("open", resolve, { once: true });
      ws.addEventListener("error", () => reject(new Error("CDP socket failed")), { once: true });
    });
    return { cdp: new WsCDP(ws, chrome), chrome };
  }

  consume(raw) {
    const message = JSON.parse(raw);
    if (message.id) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message));
      else pending.resolve(message.result || {});
      return;
    }
    for (const listener of this.listeners.get(message.method) || []) {
      listener(message.params || {}, message.sessionId || "");
    }
  }

  send(method, params = {}, sessionId = "", timeout = 15_000) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (!this.pending.delete(id)) return;
        reject(new Error(`CDP request timed out after ${timeout}ms (${method}): ${this.stderr.slice(-2000)}`));
      }, timeout);
      this.pending.set(id, {
        resolve: (value) => { clearTimeout(timer); resolve(value); },
        reject: (error) => { clearTimeout(timer); reject(error); },
      });
      const message = { id, method, params };
      if (sessionId) message.sessionId = sessionId;
      this.ws.send(JSON.stringify(message));
    });
  }

  on(method, listener) {
    const rows = this.listeners.get(method) || [];
    rows.push(listener);
    this.listeners.set(method, rows);
  }
}

/** harness：两个一模一样的流式面板，一个用新 hook，一个用旧写法。 */
const HARNESS = `
import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { useStickToBottom } from "../src/lib/useStickToBottom";

// 600px 高 —— 旧写法的"贴底"判据是一屏的 20%（=120px），比一格滚轮（~100px）
// 还大，这正是真实界面上"滑一下弹一下"的成因。面板太矮就复现不出来。
const PANEL = { height: "600px", overflowY: "auto", border: "1px solid #ccc", width: "400px" };

/** 每 16ms 长一行，模拟 token 流。 */
function useStream() {
  const [lines, setLines] = useState(() => Array.from({ length: 40 }, (_, i) => "起始行 " + i));
  useEffect(() => {
    const id = setInterval(() => setLines((v) => [...v, "新行 " + v.length]), 16);
    window.__stop = () => clearInterval(id);
    return () => clearInterval(id);
  }, []);
  return lines;
}

function Fixed() {
  const lines = useStream();
  const ref = useRef(null);
  const { atBottom, scrollToBottom } = useStickToBottom(ref, [lines]);
  return (
    <div>
      <div id="fixed" ref={ref} style={PANEL}>
        {lines.map((l, i) => <div key={i} style={{ height: "20px" }}>{l}</div>)}
      </div>
      {!atBottom && <button id="fixed-tobottom" onClick={scrollToBottom}>回到底部</button>}
    </div>
  );
}

/** 反面对照：改动前的写法 —— 用 scroll 事件测"离底部多远"来决定粘不粘。 */
function Legacy() {
  const lines = useStream();
  const ref = useRef(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = ref.current;
    const onScroll = () => {
      const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
      stick.current = gap <= Math.max(40, el.clientHeight * 0.2);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [lines]);
  return (
    <div id="legacy" ref={ref} style={PANEL}>
      {lines.map((l, i) => <div key={i} style={{ height: "20px" }}>{l}</div>)}
    </div>
  );
}

createRoot(document.getElementById("root")).render(
  <div style={{ display: "flex", gap: "20px", padding: "10px" }}><Fixed /><Legacy /></div>
);
`;

const PAGE = `<!doctype html><html><body style="margin:0"><div id="root"></div>
<script type="module" src="./bundle.js"></script></body></html>`;

async function evaluate(send, expression) {
  const { result, exceptionDetails } = await send("Runtime.evaluate", {
    expression, returnByValue: true, awaitPromise: true,
  });
  if (exceptionDetails) throw new Error(exceptionDetails.text || "evaluate failed");
  return result.value;
}

async function waitFor(send, expression, label, timeout = 10_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await evaluate(send, expression)) return;
    await delay(50);
  }
  throw new Error(`timed out waiting for ${label}`);
}

/** 真·滚轮：走浏览器输入管线，和用户拨轮子走的是同一条路。 */
async function wheel(send, selector, deltaY) {
  const box = await evaluate(send, `(() => {
    const r = document.querySelector("${selector}").getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  })()`);
  await send("Input.dispatchMouseEvent", {
    type: "mouseWheel", x: box.x, y: box.y, deltaX: 0, deltaY, pointerType: "mouse",
  });
}

const top = (send, id) => evaluate(send, `document.getElementById("${id}").scrollTop`);
const maxTop = (send, id) => evaluate(send,
  `(() => { const e = document.getElementById("${id}"); return e.scrollHeight - e.clientHeight; })()`);

async function run() {
  const work = await mkdtemp(path.join(os.tmpdir(), "ivyea-scroll-e2e-"));
  // 入口必须落在 client/ 里面：放 /tmp 的话 esbuild 找不到 react / react-dom，
  // 也解析不到 ../src。跑完在 finally 里删掉。
  const entry = path.resolve("e2e/.harness.jsx");
  await writeFile(entry, HARNESS, "utf8");
  const bundle = spawnSync("npx", ["esbuild", entry, "--bundle", "--format=esm", "--jsx=automatic",
                                   `--outfile=${path.join(work, "bundle.js")}`],
                           { cwd: path.resolve("."), encoding: "utf8" });
  if (bundle.status !== 0) throw new Error(bundle.stderr || bundle.stdout || "harness bundle failed");
  await writeFile(path.join(work, "index.html"), PAGE, "utf8");

  const profile = await mkdtemp(path.join(os.tmpdir(), "ivyea-scroll-profile-"));
  const { cdp, chrome } = await WsCDP.launch([
    "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
    // 别加 --disable-crashpad-for-testing：Chrome 147 上它会让网络服务以
    // "Crashing due to FD ownership violation" 崩掉，页面永远加载不出来
    // （knowledge-governance.mjs 现在跑不起来就是这个原因）。
    "--disable-breakpad", "--disable-crash-reporter", "--noerrdialogs",
    "--allow-file-access-from-files", "--disable-web-security", "--remote-debugging-port=0",
    `--user-data-dir=${profile}`, "about:blank",
  ]);
  try {
    const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
    const send = (method, params = {}) => cdp.send(method, params, sessionId);
    const errors = [];
    cdp.on("Runtime.exceptionThrown", (params, s) => {
      if (s === sessionId) errors.push(params.exceptionDetails?.text || "browser exception");
    });
    await Promise.all([send("Page.enable"), send("Runtime.enable")]);
    await send("Page.navigate", { url: pathToFileURL(path.join(work, "index.html")).href });
    await waitFor(send, `!!document.getElementById("fixed") && !!document.getElementById("legacy")`, "panels");
    // 先让内容长到溢出，并确认两边都在跟随
    await waitFor(send, `(() => { const e = document.getElementById("fixed");
      return e.scrollHeight - e.scrollTop - e.clientHeight < 5 && e.scrollHeight > 1600; })()`, "following bottom");

    // ── 1. 用户往上滚，流还在跑 ────────────────────────────────────────────
    // 一格滚轮 —— 真实用户的动作，也是旧写法判不出来的那个量级
    await wheel(send, "#fixed", -100);
    await wheel(send, "#legacy", -100);
    const fixedAfterWheel = await top(send, "fixed");
    const legacyAfterWheel = await top(send, "legacy");
    await delay(600);                                  // 这 600ms 里内容长了 ~37 行
    const fixedNow = await top(send, "fixed");
    const legacyNow = await top(send, "legacy");
    const legacyMax = await maxTop(send, "legacy");

    assert.equal(fixedNow, fixedAfterWheel,
      `新写法：滚上去之后位置必须一动不动（期望 ${fixedAfterWheel}，实际 ${fixedNow}）`);
    // 反面对照：旧写法把用户按回了底部（差的那几十像素是这一帧刚长出来的内容）
    assert.ok(legacyNow > legacyAfterWheel + 50,
      `反面对照：旧写法应当把用户拽走（翻上去后 ${legacyAfterWheel}，600ms 后 ${legacyNow}）`);
    assert.ok(legacyMax - legacyNow <= 60,
      `反面对照：旧写法应当贴回底部（scrollTop=${legacyNow} / max=${legacyMax}）`);

    // ── 2. 脱离底部时给出「回到底部」────────────────────────────────────────
    await waitFor(send, `!!document.getElementById("fixed-tobottom")`, "back-to-bottom button");
    await evaluate(send, `document.getElementById("fixed-tobottom").click()`);
    await delay(400);                                  // 点完之后要能继续跟着长
    const resumed = await top(send, "fixed");
    const resumedMax = await maxTop(send, "fixed");
    // 容差 60px：跟随走 rAF，测量的这一刻内容又长了一两行，本来就该差这么点
    assert.ok(resumedMax - resumed <= 60,
      `点「回到底部」后应恢复跟随（scrollTop=${resumed} / max=${resumedMax}）`);
    assert.equal(await evaluate(send, `!!document.getElementById("fixed-tobottom")`), false,
      "回到底部之后按钮该消失");

    // ── 3. 用户自己滚回底部，跟随也要恢复 ──────────────────────────────────
    await wheel(send, "#fixed", -100);
    await waitFor(send, `!!document.getElementById("fixed-tobottom")`, "detached again");
    await wheel(send, "#fixed", 900);                  // 一路滚回底
    await delay(400);
    const back = await top(send, "fixed");
    const backMax = await maxTop(send, "fixed");
    assert.ok(backMax - back <= 60,
      `用户自己滚回底部后应恢复跟随（scrollTop=${back} / max=${backMax}）`);

    await evaluate(send, `window.__stop && window.__stop()`);
    assert.deepEqual(errors, []);
    process.stdout.write("stream scroll browser E2E passed\n");
  } finally {
    try { chrome.kill("SIGKILL"); } catch { /* ignore */ }
    await delay(200);        // 等 Chrome 真的放下 profile 里的文件句柄
    // 清理失败不许盖掉真正的失败原因（ENOTEMPTY 会把断言错误顶掉）
    for (const target of [profile, work]) {
      await rm(target, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }).catch(() => {});
    }
    await rm(path.resolve("e2e/.harness.jsx"), { force: true }).catch(() => {});
  }
}

run().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exit(1);
});
