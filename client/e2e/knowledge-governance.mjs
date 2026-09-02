/**
 * 知识治理中心的浏览器 E2E。
 *
 * 页面不走验证台的 vite，而是直接加载 `dist/index.html`（file:// + Fetch 域拦截
 * 把 /api/* 全部就地填掉）—— 这条用例验的是**构建产物**能不能跑起来。
 *
 * CDP 走共用的 e2e/cdp.mjs（调试端口 + WebSocket）。它原来自带一份
 * `--remote-debugging-pipe` 的实现，Chrome 147 起那条路会以
 * "Crashing due to FD ownership violation" 直接崩掉 —— 症状是连一条断言都跑不到，
 * 死在 Page.navigate 的超时上。cdp.mjs 当初就是为了绕开这件事抽出来的。
 *
 * 跑：node e2e/knowledge-governance.mjs
 *     IVYEA_E2E_SKIP_BUILD=1 跳过 npm run build（dist 已经是新的时候用）
 */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { WsCDP, chromeArgs, evaluate, waitFor } from "./cdp.mjs";


const governance = {
  ok: true,
  healthy: true,
  summary: {
    pending_reviews: 0,
    approved_not_published: 0,
    published_changes: 1,
    coverage_gaps: 0,
    stale_cards: 0,
    monitor_errors: 0,
    monitor_overdue: 0,
    conflicts: 0,
    unverified_approved: 0,
  },
  reviews: { summary: { pending: 0 }, changes: [] },
  coverage: {
    summary: { requirements: 41, covered: 41, gaps: 0, coverage_rate: 1, primary_current_rate: 0.976 },
    requirements: [{
      domain: "tax_compliance", marketplace: "GLOBAL", status: "strong", covered: true,
      primary_current: true, card_ids: ["tax.tax_reports_and_liability"], source_urls: [],
    }],
    policy: "GLOBAL applies to cross-market reporting and advertising domains.",
  },
  freshness: {
    summary: {
      cards: 71,
      card_freshness: { current: 70, reviewed: 1, stale_needs_review: 0 },
      monitor_sources: 47,
      monitor_status: { current: 47, unseen: 0, overdue: 0, error: 0 },
    },
    cards_requiring_review: [],
    sources: [],
  },
  conflicts: [],
};

let evidenceApplied = false;
function apiPayload(url, method) {
  const pathname = new URL(url).pathname;
  if (pathname === "/api/auth/me") return { username: "e2e-admin", role: "admin", permissions: [] };
  if (pathname === "/api/setup/status") return { needs_setup: false, setup_done: true, checks: {} };
  if (pathname === "/api/health") return { ok: true, version: "e2e" };
  if (pathname === "/api/setup/update-info") {
    return { current: "e2e", latest: "e2e", update_available: false, platform_update_supported: false };
  }
  if (pathname === "/api/skill-tools/pinned") return [];
  if (pathname === "/api/autofix/status") return { enabled: false, job: null };
  if (pathname === "/api/ivyea-agent/knowledge/governance") return governance;
  if (pathname === "/api/ivyea-agent/knowledge/changes") {
    return { ok: true, summary: { changes: 0, pending: 0, published: 0 }, changes: [], review_required: false };
  }
  if (pathname === "/api/ivyea-agent/knowledge/evidence" && method === "GET") {
    return {
      ok: true,
      summary: { evidence: evidenceApplied ? 1 : 0, ready_for_diagnosis: evidenceApplied ? 1 : 0 },
      evidence: evidenceApplied ? [{
        id: "ev-e2e", title: "E2E settlement evidence", kind: "settlement_report", marketplace: "US",
        card_id: "user.evidence.settlement.e2e", diagnostic: { ready_for_diagnosis: true },
      }] : [],
    };
  }
  if (pathname === "/api/ivyea-agent/knowledge/evidence/draft") {
    return {
      ok: true,
      raw_preserved: false,
      evidence: {
        id: "ev-e2e", redactions: { email: 1 },
        diagnostic: { ready_for_diagnosis: true, missing_inputs: [] },
      },
      draft: { diff: "--- old\n+++ new\n+sanitized settlement evidence" },
    };
  }
  if (pathname === "/api/ivyea-agent/knowledge/evidence/apply") {
    evidenceApplied = true;
    return { ok: true, evidence: { id: "ev-e2e" }, result: { ok: true, applied: true } };
  }
  return {};
}

async function setValue(send, selector, value) {
  await evaluate(send, `(() => {
    const element = document.querySelector(${JSON.stringify(selector)});
    // 选择器要**再序列化一次**才能拼进这句报错里：这些 data-testid 选择器自带双引号，
    // 直接插进字符串字面量会把它提前闭合，整段表达式变成语法错误 —— 而语法错误是在
    // 解析时炸的，元素在不在根本轮不到判断，报出来的也只是一句 "missing )"。
    if (!element) throw new Error("missing element: " + ${JSON.stringify(selector)});
    const proto = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
      : element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(element, ${JSON.stringify(value)});
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  })()`);
}

async function run() {
  if (process.env.IVYEA_E2E_SKIP_BUILD !== "1") {
    const build = spawnSync("npm", ["run", "build"], { cwd: path.resolve("."), encoding: "utf8" });
    if (build.status !== 0) throw new Error(build.stderr || build.stdout || "client build failed");
  }
  const dist = path.resolve("dist");
  const rawHtml = await readFile(path.join(dist, "index.html"), "utf8");
  const assetRoot = pathToFileURL(path.join(dist, "assets")).href.replace(/\/$/, "");
  const appHtml = rawHtml
    .replace(/(?:<link rel="icon"[^>]*>|<link rel="apple-touch-icon"[^>]*>)/g, "")
    .replace(/(["'])\/assets\//g, `$1${assetRoot}/`)
    .replace("<script type=\"module\"", `<script>
      const originalFetch = window.fetch.bind(window);
      window.fetch = (input, init) => {
        const value = typeof input === "string" && input.startsWith("/api/") ? "https://ivyea-e2e.local" + input : input;
        return originalFetch(value, init);
      };
      const originalOpen = XMLHttpRequest.prototype.open;
      XMLHttpRequest.prototype.open = function(method, url, ...rest) {
        const value = typeof url === "string" && url.startsWith("/api/") ? "https://ivyea-e2e.local" + url : url;
        return originalOpen.call(this, method, value, ...rest);
      };
    </script><script type="module"`);

  const profile = await mkdtemp(path.join(os.tmpdir(), "ivyea-knowledge-e2e-"));
  const { cdp, chrome } = await WsCDP.launch(chromeArgs(profile));
  try {
    const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
    const send = (method, params = {}) => cdp.send(method, params, sessionId);
    const browserErrors = [];
    cdp.on("Runtime.exceptionThrown", (params, eventSession) => {
      if (eventSession === sessionId) browserErrors.push(params.exceptionDetails?.text || "browser exception");
    });
    cdp.on("Fetch.requestPaused", async ({ requestId, request }, eventSession) => {
      if (eventSession !== sessionId) return;
      let body;
      let contentType;
      if (request.url.startsWith("file:///brain")) {
        body = appHtml;
        contentType = "text/html; charset=utf-8";
      } else if (request.url.startsWith("file:///assets/")) {
        // 懒加载分块的 CSS/JS。index.html 里那些 "/assets/…" 上面已经改写成绝对
        // file:// 地址了，但 **vite 运行时自己算的那一份没经过改写** —— 它按站点根
        // 拼出 /assets/Brain-*.css，在 file:// 下就是 file:///assets/…，取不到就是
        // 一句 "Unable to preload CSS for /assets/Brain-*.css"，整页变「页面渲染出错」。
        // 这条分支就是把它们指回 dist/assets。
        const name = path.basename(new URL(request.url).pathname);
        body = await readFile(path.join(dist, "assets", name));
        contentType = name.endsWith(".css") ? "text/css; charset=utf-8"
          : name.endsWith(".js") ? "text/javascript; charset=utf-8"
          : "application/octet-stream";
      } else {
        body = JSON.stringify(apiPayload(request.url, request.method));
        contentType = "application/json; charset=utf-8";
      }
      await send("Fetch.fulfillRequest", {
        requestId,
        responseCode: 200,
        responseHeaders: [
          { name: "Content-Type", value: contentType },
          { name: "Access-Control-Allow-Origin", value: "*" },
        ],
        body: Buffer.from(body).toString("base64"),
      });
    });
    await Promise.all([
      send("Page.enable"),
      send("Runtime.enable"),
      send("Fetch.enable", { patterns: [
        { urlPattern: "file:///brain*", requestStage: "Request" },
        { urlPattern: "file:///assets/*", requestStage: "Request" },
        { urlPattern: "https://ivyea-e2e.local/api/*", requestStage: "Request" },
      ] }),
    ]);
    await send("Page.navigate", { url: "file:///brain?tab=governance" });
    // **要先等文档本身出现**：navigate 返回只代表请求发出去了，这一刻 document.body
    // 还可能是 null，而 waitFor 里的表达式一抛异常就直接失败、不会重试。
    await waitFor(send, `!!document.body`, "文档就绪", 20_000);
    await waitFor(send, `document.body.innerText.includes("IvyeaAgent 知识治理中心")`, "governance center", 20_000);
    assert.equal(await evaluate(send, `document.body.innerText.includes("41/41")`), true);

    await evaluate(send, `document.querySelector('[data-testid="knowledge-view-evidence"]').click()`);
    await waitFor(send, `!!document.querySelector('[data-testid="knowledge-evidence-view"]')`, "evidence view", 20_000);
    await setValue(send, '[data-testid="evidence-kind"]', "settlement_report");
    await setValue(send, '[data-testid="evidence-title"]', "E2E settlement evidence");
    await setValue(send, '[data-testid="evidence-message"]', "Payment released for settlement");
    await setValue(send, '[data-testid="evidence-content"]', "Contact email owner@example.com; settlement reconciled.");
    await evaluate(send, `document.querySelector('[data-testid="evidence-authorized"]').click()`);
    await evaluate(send, `document.querySelector('[data-testid="evidence-rights"]').click()`);
    await waitFor(send, `!document.querySelector('[data-testid="evidence-preview-button"]').disabled`, "enabled preview button", 20_000);
    await evaluate(send, `document.querySelector('[data-testid="evidence-preview-button"]').click()`);
    await waitFor(send, `!!document.querySelector('[data-testid="evidence-preview"]')`, "sanitized evidence preview", 20_000);
    assert.equal(await evaluate(send, `document.body.innerText.includes("原始文件保留：否")`), true);

    await evaluate(send, `document.querySelector('[data-testid="evidence-apply-button"]').click()`);
    await waitFor(send, `!!document.querySelector('.confirm-ok-normal')`, "confirmation dialog", 20_000);
    await evaluate(send, `document.querySelector('.confirm-ok-normal').click()`);
    await waitFor(send, `document.body.innerText.includes("user.evidence.settlement.e2e")`, "applied evidence row", 20_000);
    assert.deepEqual(browserErrors, []);
    process.stdout.write("knowledge governance browser E2E passed\n");
  } finally {
    // **先等 Chrome 真的退出再删 profile。** SIGKILL 之后它还要一小会儿才收摊，
    // 立刻删会撞上它正在写的缓存目录（ENOTEMPTY）—— 而这个来自 finally 的错误会
    // 把真正的失败原因整个盖掉，排查时看到的只有一句 rmdir 失败。
    try { chrome.kill("SIGKILL"); } catch { /* 已经没了 */ }
    await new Promise((resolve) => {
      chrome.once("exit", resolve);
      setTimeout(resolve, 3_000);
    });
    await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
      .catch(() => { /* 临时目录删不掉不该让用例失败 */ });
  }
}

await run();
