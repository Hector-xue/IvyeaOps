/**
 * 「跑着的时候也能说话」的浏览器 E2E。
 *
 * 钉的是这一轮改动的四件事，全部在**真实组件树**上验（harness/ 那套：真 <App/>，
 * 只把 HTTP 层换掉）—— 手写一份 HTML 来验，抄漏的元素就永远看不见。
 *
 *   1. 轮次跑着的时候输入框还能用，按发送 = 追加给这一轮（此前发送键整个变成
 *      「停止」，Enter 被吞，想补一句只能干等几十分钟或者掐掉重说）；
 *   2. 模型拿不准时弹出的选项卡：推荐项要标出来、倒计时要说清楚"到点按推荐项继续"
 *      （不是"到点作废"），点一个选项要真的把答案送回去；
 *   3. 一轮里替用户定的选择，界面**自己**说出来（不指望模型在总结里提）；
 *   4. 时刻：用户气泡旁的发送时间、回答末尾的"结束于 …·用时 …"；
 *      左栏正在跑的那条会话标题左边有个会动的标记。
 *
 * 跑：node e2e/followups.mjs
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
  const profile = await mkdtemp(path.join(os.tmpdir(), "ivyea-followups-profile-"));
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

    await send("Page.navigate", { url: `${ORIGIN}/?r=/console&followups=1` });
    await waitFor(send, `!!document.querySelector(".cc-input")`, "任务台输入框", 60_000);
    await typeAndSend(send, "广告花费为什么涨了");

    // ── 一、跑着的时候输入框还能用 ────────────────────────────────────────
    await waitFor(send, `!!document.querySelector(".af")`, "这一轮跑起来了", 30_000);
    const whileRunning = await evaluate(send, `(() => {
      const ta = document.querySelector(".cc-input");
      const sendBtn = document.querySelector(".cc-send");
      return {
        inputDisabled: !!ta?.disabled,
        sendMissing: !sendBtn,
        // 停止不再是主键，而是旁边那颗次级按钮
        stopIsSecondary: !!document.querySelector(".cc-stop-secondary"),
        stopTitle: document.querySelector(".cc-stop-secondary")?.getAttribute("title") || "",
        placeholder: ta?.getAttribute("placeholder") || "",
      };
    })()`);
    assert.equal(whileRunning.inputDisabled, false, "跑着的时候输入框被禁用了");
    assert.equal(whileRunning.sendMissing, false, "跑着的时候发送键消失了");
    assert.ok(whileRunning.stopIsSecondary, "停止应该降级成次级按钮，别再占着主键");
    assert.ok(/真的中止/.test(whileRunning.stopTitle) && /不再烧 token/.test(whileRunning.stopTitle),
              `停止要说清楚它真的会中止（当前：「${whileRunning.stopTitle}」）`);
    assert.ok(/补一句|追加/.test(whileRunning.placeholder),
              `跑着时的提示语该请人补话（当前：「${whileRunning.placeholder}」）`);

    // ── 二、追加一句：真的送进这一轮 ──────────────────────────────────────
    await typeAndSend(send, "顺便把预算也看一下");
    await waitFor(send, `!!document.querySelector(".cc-queue-item.is-injected")`,
                  "追加指令被这一轮收下（队列条转成「已插入本轮」）", 20_000);
    const queued = await evaluate(send, `(() => {
      const el = document.querySelector(".cc-queue-item.is-injected");
      return { text: (el?.textContent || "").trim(), input: document.querySelector(".cc-input")?.value };
    })()`);
    assert.ok(queued.text.includes("已插入本轮"), `队列条该说清去向：「${queued.text}」`);
    assert.ok(queued.text.includes("顺便把预算也看一下"), "队列条该显示那句话本身");
    assert.equal(queued.input, "", "送出去之后输入框该清空");
    // agent 回播 injected 事件 → 时间线上留一行，模型也确实照着做了
    await waitFor(send, `document.body.innerText.includes("收到追加指令")`,
                  "时间线上留下「收到追加指令」", 20_000);

    // 窄屏：这一行必须换行，不能把正文挤成一列字。
    // （实测栽过：右边"20:34 · 收到追加指令，已插入本轮"是不可压缩的，390px 下
    //  左边那句话被压到一个字宽，竖着排下来一整列。）
    await send("Emulation.setDeviceMetricsOverride",
               { width: 390, height: 800, deviceScaleFactor: 1, mobile: true });
    await delay(300);
    const narrow = await evaluate(send, `(() => {
      const t = document.querySelector(".cc-injected-text");
      return { w: t ? Math.round(t.getBoundingClientRect().width) : null,
               scrollW: document.documentElement.scrollWidth,
               clientW: document.documentElement.clientWidth };
    })()`);
    assert.ok(narrow.w !== null && narrow.w > 120,
              `窄屏下追加指令的正文被挤没了（宽 ${narrow.w}px）`);
    assert.ok(narrow.scrollW <= narrow.clientW, "窄屏下不该出现横向滚动");
    await send("Emulation.setDeviceMetricsOverride",
               { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
    await delay(300);

    // ── 三、选项卡 ────────────────────────────────────────────────────────
    await waitFor(send, `!!document.querySelector(".cs-question")`, "选项卡弹出来", 20_000);
    const card = await evaluate(send, `(() => {
      const el = document.querySelector(".cs-question");
      const rec = el.querySelector(".cs-question-opt.is-rec .cs-question-opt-label em");
      return {
        timer: (el.querySelector(".cs-question-timer")?.textContent || "").trim(),
        recommended: (rec?.textContent || "").trim(),
        // 「其他…」共用 .cs-question-opt 的样式，数给定选项时要把它排掉
        options: [...el.querySelectorAll(".cs-question-opt:not(.cs-question-other)")].length,
        desc: (el.querySelector(".cs-question-opt-desc")?.textContent || "").trim(),
        primaryDisabled: !!el.querySelector(".cs-btn-primary")?.disabled,
        hasOther: !!el.querySelector(".cs-question-other"),
        hasSkip: !!el.querySelector(".cs-question-skip"),
        hasSkipAll: !!el.querySelector(".cs-question-skipall"),
      };
    })()`);
    assert.equal(card.options, 2, "两个选项都要画出来");
    assert.ok(card.hasOther, "给的选项都不合适时要能自己写一个");
    assert.ok(card.hasSkip && card.hasSkipAll, "要能跳过：单题一个、整张卡一个");
    assert.equal(card.recommended, "推荐", "推荐项必须标出来 —— 那是「我不选会发生什么」的答案");
    assert.ok(card.desc.length > 0, "每个选项要说清楚选它意味着什么");
    assert.ok(/按推荐项继续/.test(card.timer),
              `倒计时要说明到点会按推荐项继续，而不是作废（当前：「${card.timer}」）`);
    assert.ok(card.primaryDisabled, "一个都没选时不该能提交");

    // ── 三之二、自己写一个答案 ────────────────────────────────────────────
    // 先验自由作答：写完再改回选给定选项，两条路都走一遍。给的选项永远列不全，
    // 这条路断了，用户就只能干等超时或者另起一句话说。
    await evaluate(send, `document.querySelector(".cs-question-other").click()`);
    await waitFor(send, `!!document.querySelector(".cs-question-input")`, "自由输入框", 10_000);
    await evaluate(send, `(() => {
      const el = document.querySelector(".cs-question-input");
      const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      set.call(el, "都不合适，我要第三种做法");
      el.dispatchEvent(new Event("input", { bubbles: true }));
    })()`);
    const withFree = await evaluate(send, `(() => {
      const el = document.querySelector(".cs-question");
      return JSON.stringify({ primaryDisabled: !!el.querySelector(".cs-btn-primary")?.disabled });
    })()`);
    assert.equal(JSON.parse(withFree).primaryDisabled, false,
                 "写了自己的答案之后就该能提交");
    // 收回自由作答，回到选给定选项那条路（下面接着验答案回送）
    await evaluate(send, `document.querySelector(".cs-question-other").click()`);

    // 用元素自己的 click()，不用坐标点击：这一轮还在流式吐字，卡片会随内容上下漂，
    // 按坐标点很容易落到隔壁（本机连跑 6 次，坐标点法挂了 3 次）。
    await evaluate(send, `document.querySelector(".cs-question-opt:not(.cs-question-other)").click()`);
    await waitFor(send, `!!document.querySelector(".cs-question-opt.is-on")`, "选项被选中", 10_000);
    await evaluate(send, `document.querySelector(".cs-question .cs-btn-primary").click()`);
    await waitFor(send, `!!document.querySelector(".cs-question-done")`, "选完转成回执", 15_000);
    const answered = await evaluate(send, `JSON.stringify(window.__lastQuestionAnswer || null)`);
    const payload = JSON.parse(answered || "null");
    assert.ok(payload, "选项卡的答案没有送回去");
    assert.equal(payload.request_id, "q-demo-1", "答案要带上是哪一张卡");
    assert.equal(payload.session_id, "s-live", "答案要带上会话 id —— ops 按会话归属放行");
    assert.deepEqual(Object.values(payload.answers), ["先否词后观察"], "送回去的选择不对");

    // ── 四、收尾：自动决策说明 + 时刻 ─────────────────────────────────────
    await waitFor(send, `document.body.innerText.includes("先说结论")`, "正文吐完", 40_000);
    // 等 final 真的到（自动决策和收尾时刻都在它里面）—— 正文吐完 ≠ 这一轮结束。
    //
    // 收尾那一行**跟在「复制 / 重新生成」后面**（.cc-acts-meta），不再自己占一行。
    // .cc-turn-clock 现在只兜没有那排按钮的两种轮次：回答失败的，和只有执行过程
    // 没有正文的。两个都认，才既钉得住新位置、又不假设这一轮一定走哪条路。
    const CLOCK = ".cc-acts-meta, .cc-turn-clock";
    await waitFor(send, `!!document.querySelector("${CLOCK}")`, "这一轮收尾", 30_000);
    const wrap = await evaluate(send, `(() => {
      const auto = document.querySelector(".cc-autodec");
      const clock = document.querySelector("${CLOCK}");
      return {
        auto: (auto?.textContent || "").trim(),
        clock: (clock?.textContent || "").trim(),
        inActions: !!clock?.closest(".cc-acts"),
        userTime: (document.querySelector(".cc-user-time")?.textContent || "").trim(),
      };
    })()`);
    assert.ok(/自动定的/.test(wrap.auto),
              `替用户定的那几项要由界面自己说出来（当前：「${wrap.auto}」）`);
    assert.ok(wrap.auto.includes("按环比"), "自动决策说明里要写清楚定的是哪一项");
    assert.ok(/^结束于 \d{1,2}:\d{2}/.test(wrap.clock) && /用时/.test(wrap.clock),
              `回答末尾该有「结束于 …·用时 …」（当前：「${wrap.clock}」）`);
    // 这一轮有正文、没失败 —— 那它该在动作行里，而不是又自己占一行。
    assert.ok(wrap.inActions, "收尾时刻该跟在「复制 / 重新生成」那一行后面");
    assert.ok(/^\d{1,2}:\d{2}:\d{2}$/.test(wrap.userTime),
              `用户那句话旁边该有发送时刻（当前：「${wrap.userTime}」）`);

    // ── 五、左栏：正在跑的那条在闪 ────────────────────────────────────────
    await waitFor(send, `!!document.querySelector(".sb-sess-live")`, "左栏的正在跑标记", 20_000);
    const rail = await evaluate(send, `(() => {
      const el = document.querySelector(".sb-sess-live");
      const row = el.closest(".sb-sess");
      const dot = el.querySelector("i:last-child");
      const cs = getComputedStyle(dot);
      const ring = getComputedStyle(el.querySelector("i:first-child"));
      return {
        title: (row?.querySelector(".sb-sess-title")?.textContent || "").trim(),
        marks: document.querySelectorAll(".sb-sess-live").length,
        // 真的在动（有动画），而不是画了个静止的点冒充
        animated: cs.animationName !== "none" && ring.animationName !== "none",
        beforeTitle: row.firstElementChild?.classList.contains("sb-sess-live"),
        size: [el.getBoundingClientRect().width, el.getBoundingClientRect().height],
      };
    })()`);
    assert.equal(rail.marks, 1, "只有真的在跑的那条会话该有标记");
    assert.equal(rail.title, "测试", "标记要跟着会话 id 走，不是永远画在第一行");
    assert.ok(rail.animated, "这枚标记要会动 —— 静止的点和「最近更新过」分不开");
    assert.ok(rail.beforeTitle, "标记该在标题左边");
    assert.ok(rail.size[0] > 0 && rail.size[1] > 0, "标记有尺寸，不能是个 0×0 的空元素");

    // ── 五点二、没发出去的追加指令，关掉页面也不该丢 ──────────────────────
    // 它此前只活在内存里：排了两句话、手一抖关了标签页，那两句就没了，而用户
    // 以为自己已经说过了。这是"说出去的话必须有着落"的最后一段缺口。
    const stash = await evaluate(send, `(() => {
      const keys = Object.keys(localStorage).filter((k) => k.startsWith("ivyea-ops.console.queue"));
      return JSON.stringify(keys.map((k) => [k, localStorage.getItem(k)]));
    })()`);
    // 这一轮里那条已经被 agent 收下了（injected），所以**不该**留在待发盘里 ——
    // 留着的话刷新之后会被当成"还没发"再发一遍。
    assert.ok(!JSON.parse(stash).some(([, v]) => String(v).includes("顺便把预算也看一下")),
              "已经插进这一轮的指令不该留在待发队列里");

    // ── 五点五、执行过程那一栏的左边界只有两条竖线 ────────────────────────
    // 这块对齐来回改过三次（图标一列、标题一列、旁白又一列，谁也不对着谁）。
    // 钉成数字：头部图标/✓/旁白引线一条线，工具图标/「执行过程」/旁白正文一条线。
    const cols = await evaluate(send, `(() => {
      const L = (sel) => { const el = document.querySelector(sel); if (!el) return null;
        return Math.round(el.getBoundingClientRect().left); };
      const narr = document.querySelector(".cc-narration");
      return {
        headIcon: L(".af-head-ivy"), headLabel: L(".af-head-label"),
        rowMark: L(".af-line .af-mark"), rowIcon: L(".af-line .af-icon"),
        narrBar: L(".cc-narration"),
        narrText: narr ? Math.round((narr.firstElementChild || narr).getBoundingClientRect().left) : null,
        // 头部图标就是那三行「点+横线」，不该再是心电图/常春藤
        mark: !!document.querySelector(".af-head-ivy .steps-mark"),
        ivy: !!document.querySelector(".ivy-grow"),   // 常春藤已全站下线，一株都不该有
      };
    })()`);
    assert.ok(cols.mark, "「执行过程」头部该用步骤清单图标");
    assert.ok(!cols.ivy, "常春藤已经全站换掉，页面上不该再有");
    assert.equal(cols.headIcon, cols.rowMark, "头部图标要和下面那列 ✓ 同一条竖线");
    assert.equal(cols.narrBar, cols.rowMark, "旁白的引线也在这条线上");
    assert.equal(cols.headLabel, cols.rowIcon, "「执行过程」要挨着自己的图标，落在工具图标那一列");
    assert.equal(cols.narrText, cols.headLabel, "旁白正文和「执行过程」同一条竖线");

    // ── 六、停止是真的停止 ────────────────────────────────────────────────
    // 此前这颗按钮只 abort 掉前端那条流，轮次在 agent 那边照跑照烧 token。
    // 这里钉死两件事：真的发出了中止请求；界面照实说"已停止"，而不是装作跑完了。
    await send("Page.navigate", { url: `${ORIGIN}/?r=/console&followups=1` });
    await waitFor(send, `!!document.querySelector(".cc-input")`, "回到任务台", 60_000);
    await typeAndSend(send, "跑个长任务");
    await waitFor(send, `!!document.querySelector(".cc-stop-secondary")`, "停止键", 30_000);
    await evaluate(send, `document.querySelector(".cc-stop-secondary").click()`);
    // 先等 toast（点了就有），再等 agent 的 cancelled 事件把这一轮收尾（慢一拍）。
    await waitFor(send, `document.body.innerText.includes("已停止这一轮")`,
                  "界面明确说这一轮停了", 20_000);
    await waitFor(send, `document.body.innerText.includes("已停止：你中止了这一轮")`,
                  "agent 回执到达、这一轮真的收尾", 20_000);
    const stopped = await evaluate(send, `(() => {
      const feed = document.body.innerText;
      return {
        note: feed.includes("已停止：你中止了这一轮"),
        toast: feed.includes("已停止这一轮"),
        // 停完输入框回到常态：主键是发送，不再是"追加"
        placeholder: document.querySelector(".cc-input")?.getAttribute("placeholder") || "",
        stillRunning: !!document.querySelector(".cc-stop-secondary"),
        // 同上：收尾时刻在动作行里（.cc-acts-meta），.cc-turn-clock 只兜没有动作行的轮次。
        clock: (document.querySelector(CLOCK)?.textContent || "").trim(),
      };
    })()`.replace("CLOCK", JSON.stringify(CLOCK)));
    assert.ok(stopped.note, "时间线上要留一行「已停止」——否则界面只是忽然不动了");
    assert.ok(stopped.toast, "要明确告诉用户这一轮真的停了");
    assert.equal(stopped.stillRunning, false, "停完之后不该还挂着停止键");
    assert.ok(!/补一句|追加/.test(stopped.placeholder), "停完输入框该回到常态");
    assert.ok(/^结束于/.test(stopped.clock), `停掉的轮次也该有收尾时刻（当前：「${stopped.clock}」）`);

    // ── 七、盘里留着的那句，重新打开这条会话时会被补发 ────────────────────
    // 放在最后：它会把页面导航走，前面那几段的判据依赖当时的页面状态。
    // 反过来：盘里留着的那句，重新打开这条会话时必须被发出去（不是静静躺着）。
    await evaluate(send, `localStorage.setItem("ivyea-ops.console.queue:s-live",
      JSON.stringify([{ id: "x", text: "刷新前没发出去的那句", state: "queued" }]))`);
    await send("Page.navigate", { url: `${ORIGIN}/?r=/console&session=s-live` });
    await waitFor(send, `!!document.querySelector(".cc-input")`, "重新打开这条会话", 60_000);
    await waitFor(send, `[...document.querySelectorAll(".cc-bubble")]
                          .some((b) => b.textContent.includes("刷新前没发出去的那句"))`,
                  "上次没发出去的那句被补发了", 30_000);

    assert.deepEqual(errors, [], "浏览器控制台不该有异常");
    console.log("follow-up / question-card / real-stop checks passed");
  } finally {
    chrome.kill("SIGKILL");
    harness.kill("SIGTERM");
    await rm(profile, { recursive: true, force: true }).catch(() => {});
  }
}

await run();
