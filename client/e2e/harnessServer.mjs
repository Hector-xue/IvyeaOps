/**
 * 起验证台的 vite（harness/ 那套：真实 <App/> + 假 HTTP 层）。
 *
 * 从 session-url.mjs 里搬出来的 —— 第二条"跑真实页面"的用例（live-dock.mjs）要用
 * 同一套东西，各抄一份的下场是端口/等待逻辑改一处漏一处。
 */
import { spawn } from "node:child_process";
import path from "node:path";

export const ORIGIN = "http://127.0.0.1:5199";

const delay = (ms) => new Promise((r) => setTimeout(r, ms));

export async function startHarness() {
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
