/**
 * E2E 共用的 Chrome 驱动（CDP over WebSocket）。
 *
 * 从 stream-scroll.mjs 原样搬出来的 —— 第二条浏览器用例（activity-line.mjs）要用
 * 同一套东西。复制一份的话，下次 Chrome 又改行为就得改两处，漏改的那处会以
 * "只有这条用例莫名其妙挂了"的形式出现。
 *
 * 不用 --remote-debugging-pipe：Chrome 147 起那条路会以
 * "Crashing due to FD ownership violation" 直接崩掉（本机实测，那条老 E2E
 * knowledge-governance.mjs 现在就是这么跑不起来的）。调试端口 + WebSocket
 * 走的是同一套协议。
 */
import { spawn } from "node:child_process";

export const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export class WsCDP {
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

/** headless 启动参数。别加 --disable-crashpad-for-testing（见文件头）。 */
export function chromeArgs(profile) {
  return [
    "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
    "--disable-breakpad", "--disable-crash-reporter", "--noerrdialogs",
    "--allow-file-access-from-files", "--disable-web-security", "--remote-debugging-port=0",
    `--user-data-dir=${profile}`, "about:blank",
  ];
}

export async function evaluate(send, expression) {
  const { result, exceptionDetails } = await send("Runtime.evaluate", {
    expression, returnByValue: true, awaitPromise: true,
  });
  if (exceptionDetails) throw new Error(exceptionDetails.text || "evaluate failed");
  return result.value;
}

export async function waitFor(send, expression, label, timeout = 10_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await evaluate(send, expression)) return;
    await delay(50);
  }
  throw new Error(`timed out waiting for ${label}`);
}

/** 真·滚轮：走浏览器输入管线，和用户拨轮子走的是同一条路。 */
export async function wheel(send, selector, deltaY) {
  const box = await evaluate(send, `(() => {
    const r = document.querySelector("${selector}").getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  })()`);
  await send("Input.dispatchMouseEvent", {
    type: "mouseWheel", x: box.x, y: box.y, deltaX: 0, deltaY, pointerType: "mouse",
  });
}

/**
 * 真·点击（走输入管线，不是 el.click()）。
 * 差别不是形式主义：真实点击落在**坐标**上，所以它顺带证明了这个按钮点得着 ——
 * 没有被别的元素盖住、没有小到点不中。这条用例要验的恰恰就是"看得见、点得着"。
 */
export async function click(send, selector) {
  const box = await evaluate(send, `(() => {
    const el = document.querySelector("${selector}");
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  })()`);
  if (!box) throw new Error(`click: 找不到 ${selector}`);
  for (const type of ["mousePressed", "mouseReleased"]) {
    await send("Input.dispatchMouseEvent", {
      type, x: box.x, y: box.y, button: "left", clickCount: 1, pointerType: "mouse",
    });
  }
}
