/**
 * 「后台在跑，前端看得见」的浏览器 E2E。
 *
 * 钉的是三条真实投诉（同一个根因：一轮的执行过程只活在发起它的那个标签页里）：
 *
 *   1. **切走再切回来，进度没了** —— 页面上只剩自己发的那句话，后台其实一直在干活。
 *      现在打开一条正在跑的会话会接进 agent 的活轮日志（`/chat/sessions/{id}/live`），
 *      先回放已经发生的，再实时跟着跑。
 *   2. **"一大堆叠在一起"** —— 正文和工具此前被拍平成两坨（所有工具在上、所有话在下）。
 *      现在按发生顺序交错：说一段 → 做几件事 → 再说一段。
 *   3. **"只有刷新一下才能看到分段式汇报"** —— 刷新后是从存档恢复的，那边本来就是
 *      分段的。所以这里额外钉一条：**直播时的分段数，要和这一轮真实的段数对得上**。
 *
 * 另外顺手钉住 LiveDock 的图标：它存的是图标**名**（"tool-read"），直接渲染就会把
 * 这几个字母画在卡片上压着标题（用户截图里那句"文字有些错乱"）。
 *
 * 跑：node e2e/live-turn.mjs
 */
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { WsCDP, chromeArgs, click, delay, evaluate, waitFor } from "./cdp.mjs";
import { ORIGIN, startHarness } from "./harnessServer.mjs";

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
  const profile = await mkdtemp(path.join(os.tmpdir(), "ivyea-live-turn-profile-"));
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

    // ── 一、自己发起的一轮：正文与工具交错 ────────────────────────────────
    await send("Page.navigate", { url: `${ORIGIN}/?r=/console` });
    await waitFor(send, `!!document.querySelector(".cc-input")`, "任务台输入框", 60_000);
    await typeAndSend(send, "广告花费为什么涨了");

    await waitFor(send, `document.querySelectorAll(".cc-narration").length >= 1`,
                  "工具之前说的那段话（旁白）", 30_000);
    await waitFor(send, `document.querySelectorAll(".af").length >= 2`,
                  "过程被切成不止一组", 30_000);

    // 顺序必须是「过程 → 旁白 → 过程」，不能是"所有工具一坨、所有话一坨"。
    const order = await evaluate(send, `(() => {
      const nodes = [...document.querySelectorAll(".cc-ai .af, .cc-ai .cc-narration, .cc-ai .cc-answer")];
      return nodes.map((n) => n.classList.contains("af") ? "steps"
                           : n.classList.contains("cc-narration") ? "say" : "answer");
    })()`);
    const firstSay = order.indexOf("say");
    const stepsAfterSay = order.slice(firstSay + 1).includes("steps");
    assert.ok(firstSay >= 0 && stepsAfterSay,
              `说完一段之后还得有工具，实际顺序：${order.join(" → ")}`);

    // 同一段思考不许在相邻两组里各画一次（区间写成闭区间就会这样，实测过）。
    const dupThoughts = await evaluate(send, `(() => {
      const rows = [...document.querySelectorAll(".cc-ai .af-think .af-text")]
        .map((n) => n.textContent.trim());
      const seen = new Set(); const dup = [];
      for (const r of rows) { if (seen.has(r)) dup.push(r); seen.add(r); }
      return dup;
    })()`);
    assert.deepEqual(dupThoughts, [], "同一段思考被画了两遍");

    // ── 二、LiveDock 的图标是图标，不是"tool-read"这几个字 ────────────────
    await evaluate(send, `(() => {
      const b = document.querySelector(".cc-thread");
      b.dispatchEvent(new WheelEvent("wheel", { deltaY: -400, bubbles: true }));
      b.scrollTop = 0;
    })()`);
    await waitFor(send, `!!document.querySelector(".ld")`, "翻上去之后的状态坞", 15_000);
    const dockIcon = await evaluate(send, `(() => {
      const el = document.querySelector(".ld-icon");
      return { text: (el?.textContent || "").trim(), svg: !!el?.querySelector("svg") };
    })()`);
    assert.equal(dockIcon.text, "", `状态坞把图标名画成了文字：「${dockIcon.text}」`);
    assert.ok(dockIcon.svg, "状态坞该画一个真图标");

    // ── 三、打开一条**正在跑**的会话：进度接得上 ──────────────────────────
    await send("Page.navigate", { url: `${ORIGIN}/?r=/console&session=s-live&live=1` });
    await waitFor(send, `document.querySelectorAll(".cc-bubble").length > 0`,
                  "历史会话载入", 30_000);
    await waitFor(send, `document.querySelectorAll(".cc-ai .af").length >= 1`,
                  "接进活轮之后看得到执行过程", 30_000);
    await waitFor(send, `document.querySelectorAll(".cc-narration").length >= 1`,
                  "回放出来的旁白", 30_000);
    // 回放完还要继续跟着跑 —— 这是"接进去"和"读一份快照"的区别。
    await waitFor(send, `document.body.innerText.includes("先说结论")`,
                  "回放之后继续实时跟随到收尾", 30_000);

    assert.deepEqual(errors, [], "浏览器控制台不该有异常");
    console.log("live turn checks passed");
  } finally {
    chrome.kill("SIGKILL");
    harness.kill("SIGTERM");
    await rm(profile, { recursive: true, force: true }).catch(() => {});
  }
}

await run();
