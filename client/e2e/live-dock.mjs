/**
 * 「跑任务时，永远看得见它在干什么、接下来干什么」的浏览器 E2E。
 *
 * 投诉原话：上下文一长，思考过程还停在自己那句指令下面那一行，画面滚到最底部时
 * 只看得见正文哗哗地流，完全不知道 Agent 在干嘛、进行到哪一步了。
 *
 * 所以这条用例的核心断言不是"状态坞长出来了"，而是 —— **把对话区滚到最上面去，
 * 它依然在视口里**。这正是它与活动行（挂在气泡上、会被滚走）的全部区别，
 * 也只有真实浏览器排完版才证得了。
 *
 * 另外三件事：
 *   · 思考态是常春藤在长（品牌绿），不是那个转圈的紫星号
 *   · "接下来"读的是 Agent 自己排的计划（todos 事件），计划推进时它跟着换
 *   · 这一轮结束，状态坞收掉 —— 它是"正在跑"的信号，跑完还挂着就是撒谎
 *
 * 跑：node e2e/live-dock.mjs
 */
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { WsCDP, chromeArgs, click, delay, evaluate, waitFor } from "./cdp.mjs";
import { ORIGIN, startHarness } from "./harnessServer.mjs";

/** 元素在不在视口里（用真实排版后的坐标判，不看 DOM 里有没有）。 */
const inViewport = (send, sel) => evaluate(send, `(() => {
  const el = document.querySelector("${sel}");
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { top: r.top, bottom: r.bottom, h: r.height, vh: window.innerHeight,
           visible: r.height > 0 && r.top >= 0 && r.bottom <= window.innerHeight + 1 };
})()`);

const text = (send, sel) => evaluate(send,
  `(document.querySelector("${sel}")?.textContent || "").trim()`);

async function typeAndSend(send, msg) {
  await click(send, ".cc-input");
  await send("Input.insertText", { text: msg });
  for (const type of ["keyDown", "keyUp"]) {
    await send("Input.dispatchKeyEvent", {
      type, key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13,
      text: type === "keyDown" ? "\r" : undefined,
    });
  }
}

async function run() {
  const harness = await startHarness();
  const profile = await mkdtemp(path.join(os.tmpdir(), "ivyea-live-dock-profile-"));
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
    // 矮一点的视口：真实场景就是内容比屏幕长，不然"滚走"这件事根本复现不了。
    await send("Emulation.setDeviceMetricsOverride",
               { width: 1280, height: 720, deviceScaleFactor: 1, mobile: false });

    // 先打开一条**有历史的**会话：投诉说的就是"上下文比较长的话"，对话区不够长
    // 就滚不动，第 3 条断言等于什么也没证明（第一版就栽在这里）。
    await send("Page.navigate", { url: `${ORIGIN}/?r=/console` });
    await waitFor(send, `!!document.querySelector(".cc-input")`, "任务台输入框", 30_000);
    assert.equal(await evaluate(send, `!!document.querySelector(".ld")`), false,
                 "没在跑的时候不该有状态坞");
    await send("Page.navigate", { url: `${ORIGIN}/console?session=s3` });
    await waitFor(send, `document.querySelectorAll(".cc-bubble").length > 0`,
                  "历史会话载入", 30_000);

    await typeAndSend(send, "帮我跑一下广告巡检");

    // ── 1. 跑起来就出现，而且当前状态说得出话 ────────────────────────────
    await waitFor(send, `!!document.querySelector(".ld")`, "状态坞出现", 15_000);
    await waitFor(send, `!!document.querySelector(".ld .ivy-grow")`,
                  "思考态是常春藤在长", 15_000);
    const label = await text(send, ".ld-label");
    assert.ok(label.length > 0, "状态坞必须说出此刻在干什么");

    // ── 2. 「接下来」来自 Agent 自己排的计划 ─────────────────────────────
    await waitFor(send, `!!document.querySelector(".ld-next-text")`, "下一步", 15_000);
    assert.equal(await text(send, ".ld-next-text"), "拉搜索词报表，找浪费最集中的词根");
    assert.equal(await text(send, ".ld-count"), "0/3", "计划完成度");

    // ── 3. 核心：把对话区滚到最上面，它**依然在视口里** ──────────────────
    const scrolled = await evaluate(send, `(() => {
      const body = document.querySelector(".cc-thread");   // 对话区自己的滚动容器
      if (!body) return { ok: false, why: "找不到 .cc-thread" };
      body.scrollTop = 0;
      window.scrollTo(0, 0);
      return { ok: true, scrollTop: body.scrollTop, scrollable: body.scrollHeight - body.clientHeight };
    })()`);
    assert.ok(scrolled.ok, `要滚的是对话区：${JSON.stringify(scrolled)}`);
    assert.ok(scrolled.scrollable > 40,
              `对话区得真的能滚，否则这条断言什么也没证明：${JSON.stringify(scrolled)}`);
    await delay(300);
    const pinned = await inViewport(send, ".ld");
    assert.ok(pinned && pinned.visible,
              `滚到顶之后状态坞必须还在视口里（这就是它存在的理由）：${JSON.stringify(pinned)}`);
    // 而且它就贴在输入框上方 —— 位置本身是论点：视线在哪，状态就该在哪。
    const composer = await inViewport(send, ".cc-input");
    assert.ok(composer && pinned.bottom <= composer.top + 2,
              `状态坞要在输入框上方：${JSON.stringify({ pinned, composer })}`);

    // ── 4. 计划推进时，"接下来"跟着换 ───────────────────────────────────
    await waitFor(send, `(document.querySelector(".ld-count")?.textContent || "") === "1/3"`,
                  "计划完成一条", 30_000);
    assert.equal(await text(send, ".ld-next-text"), "给出否词与竞价的具体动作",
                 "第二条开跑后，下一步要指向第三条");

    // ── 5. 展开看完整计划 ───────────────────────────────────────────────
    await click(send, ".ld-main");
    await waitFor(send, `document.querySelectorAll(".ld-plan li").length === 3`,
                  "展开后是三条计划", 10_000);
    assert.equal(await evaluate(send,
      `document.querySelectorAll(".ld-plan .ld-t-done").length`), 1, "已完成一条要划掉");
    assert.equal(await evaluate(send,
      `document.querySelectorAll(".ld-plan .ld-t-doing").length`), 1, "进行中一条");

    // ── 6. 跑完就收掉 ───────────────────────────────────────────────────
    await waitFor(send, `document.body.innerText.includes("其中 28% 来自单次点击成本上升")`,
                  "整轮跑完", 30_000);
    await waitFor(send, `!document.querySelector(".ld")`, "跑完状态坞收掉", 10_000);

    assert.deepEqual(errors, [], "页面不能抛异常");
    process.stdout.write("live dock checks passed\n");
  } finally {
    chrome.kill("SIGKILL");
    harness.kill("SIGTERM");
    await rm(profile, { recursive: true, force: true }).catch(() => {});
  }
}

await run();
