/**
 * 执行过程 —— 一行活动行，点开是全过程日志。
 *
 * 为什么从"全部铺开"改成"只留一行"
 * ------------------------------
 * 上一版把一轮里的每一步都渲染成芯片。实测一个 192 步的任务：整页被工具芯片糊满，
 * 回答被挤到屏幕外，而用户真正想知道的只有一件事 —— **它现在在干什么**。
 * 过程本身不是没用，是不该默认占地方：想复盘时点开看，平时只留当前这一行。
 *
 * 三条设计约束
 * ------------
 * * **活动行永远只有一行**。新一条顶掉上一条（旧的上滑淡出），像终端的状态行。
 *   有思考流时优先显示思考 —— 模型在想什么比它在调哪个工具更贴近"现在发生了什么"。
 * * **展开按钮必须看得见**。上一版是摘要行右端一个 9px 的 `▾`，无边框无背景，
 *   在 1920 宽的屏上近乎隐形，用户根本不知道这块能收起来。现在是带文字带边框、
 *   ≥28px 高的按钮。
 * * **展开面板限高内部滚动**。192 步一次性铺开会把页面重新撑爆，等于没改。
 *   跟随底部复用 lib/useStickToBottom —— 那套是按用户意图判定的（滚一下就停止
 *   跟随），别在这里另写一份按滚动位置判的。
 */
import { useMemo, useRef, useState } from "react";
import { formatMs, type ConsoleStep } from "../../lib/stepLabels";
import useStickToBottom from "../../lib/useStickToBottom";

export type MatchedSkill = { id: string; title: string; domain?: string; score?: number };

/** 展开日志的高度上限。再多也不许往下长 —— 它是"一块可翻的日志"，不是页面的一部分。 */
const LOG_MAX_VH = 40;

function StatusMark({ status }: { status: ConsoleStep["status"] }) {
  if (status === "running") return <span className="cs-mark cs-run" aria-label="进行中" />;
  if (status === "error") return <span className="cs-mark cs-err" aria-label="失败">✕</span>;
  // 被护栏拦下是流程纠偏（"先列计划再动手"），不是出错，别用红叉吓人。
  if (status === "blocked") return <span className="cs-mark cs-blocked" aria-label="已拦截">⊘</span>;
  return <span className="cs-mark cs-ok" aria-label="完成">✓</span>;
}

function clockOf(at?: number): string {
  if (!at) return "";
  const d = new Date(at);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** 日志里的一行。有参数时可点开看原始参数（沿用上一版的 <pre>）。 */
function LogRow({ step }: { step: ConsoleStep }) {
  const [open, setOpen] = useState(false);
  const hasArgs = !!step.args && Object.keys(step.args).length > 0;

  return (
    <div className={"cs-row cs-" + step.phase + (step.destructive ? " cs-destructive" : "")}>
      <button
        type="button"
        className="cs-row-head"
        onClick={() => hasArgs && setOpen((v) => !v)}
        style={hasArgs ? undefined : { cursor: "default" }}
        aria-expanded={hasArgs ? open : undefined}
      >
        <StatusMark status={step.status} />
        <span className="cs-time">{clockOf(step.at)}</span>
        <i className="cs-icon">{step.icon}</i>
        <span className="cs-title">{step.title}</span>
        {step.detail && <span className="cs-detail">{step.detail}</span>}
        {step.destructive && <span className="cs-badge">写操作</span>}
        <span className="cs-ms">{formatMs(step.ms)}</span>
        {hasArgs && <span className="cs-caret">{open ? "▾" : "▸"}</span>}
      </button>
      {open && hasArgs && (
        <pre className="cs-args scroll-thin">{JSON.stringify(step.args, null, 2)}</pre>
      )}
    </div>
  );
}

/**
 * 活动行的内容。取舍顺序 = "此刻最能说明它在干什么"：
 *   思考流 > 正在跑的那一步 > 最后一步 > 命中的技能 > 兜底
 */
function liveLine(
  steps: ConsoleStep[], skills: MatchedSkill[], reasoning: string, running: boolean,
): { key: string; icon: string; label: string; detail?: string; thinking?: boolean } | null {
  if (running && reasoning.trim()) {
    // 思考是连续的流，没有天然的"条"。取最后一句、单行显示 —— 换行糊进来会让
    // 这一行忽高忽低，整块跟着抖。
    const last = reasoning.replace(/\s+/g, " ").trim().slice(-90);
    return { key: "thinking", icon: "✻", label: "思考", detail: last, thinking: true };
  }
  // 倒着找而不是 [...steps].reverse().find(…)：这个函数每帧都会被调用一次，
  // 而 steps 在长任务里是几百条 —— 没必要每帧复制一遍整个数组。
  let step: ConsoleStep | undefined;
  for (let i = steps.length - 1; i >= 0; i--) {
    if (steps[i].status === "running") { step = steps[i]; break; }
  }
  step = step || steps[steps.length - 1];
  if (step) {
    return { key: step.key, icon: step.icon, label: step.title, detail: step.detail };
  }
  if (skills.length) {
    return { key: "skills", icon: "✦", label: `已匹配 ${skills.length} 项技能` };
  }
  return running ? { key: "boot", icon: "⟳", label: "正在准备" } : null;
}

export default function StepTimeline({
  steps,
  skills,
  elapsedMs,
  running,
  reasoning = "",
}: {
  steps: ConsoleStep[];
  skills: MatchedSkill[];
  elapsedMs?: number;
  running?: boolean;
  /** 模型思考流（agent ≥ v1.10.3 且开了 stream_reasoning）。没有就是没有，不伪造。 */
  reasoning?: string;
}) {
  const [open, setOpen] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  // 展开时跟随最新一行；用户往上翻就停住（useStickToBottom 按用户意图判）。
  useStickToBottom(logRef, [steps.length, open]);

  // 规划/汇报（todo_write、progress_update）在一轮里能占多数步 —— 实测 19 步里
  // 12 步是它们。它们讲的是"在组织怎么做"，不是"做了什么"，所以不进主日志，
  // 折进末尾一格。子 agent 同理单独成组：委派出去一块活是性质不同的一件事。
  const { subs, real, plans, notes } = useMemo(() => ({
    subs: steps.filter((s) => s.phase === "subagent"),
    real: steps.filter((s) => s.phase !== "note" && s.phase !== "plan" && s.phase !== "subagent"),
    plans: steps.filter((s) => s.phase === "plan"),
    notes: steps.filter((s) => s.phase === "note"),
  }), [steps]);
  const [planOpen, setPlanOpen] = useState(false);

  const live = liveLine(steps, skills, reasoning, !!running);
  if (!steps.length && !skills.length && !running) return null;

  // 收起态的右半边：执行中给进度（第几步 · 用时），结束后给总账。
  const el = formatMs(elapsedMs);
  const tail = running
    ? [real.length ? `第 ${real.length} 步` : "", el].filter(Boolean).join(" · ")
    : [
        skills.length ? `已匹配 ${skills.length} 项技能` : "",
        subs.length ? `${subs.length} 个子 agent` : "",
        real.length ? `${real.length} 步` : "",
        el,
      ].filter(Boolean).join(" · ");

  return (
    <div className={"cs-timeline" + (open ? " cs-open" : "")}>
      <div className="cs-live">
        {/* 活动行整行可点（大目标好点），右侧另有一个明确的按钮。 */}
        <button
          type="button"
          className="cs-live-main"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          title={open ? "收起执行过程" : "展开执行过程"}
        >
          {/*
            * 前面只留**一枚**图标。原来是"绿色 ⟳ 在转"外加紧跟着的 ✻/工具图标两枚，
            * 转的那枚还和它描述的事情没关系 —— 用户读到的是"一个绿圈在转，旁边不知
            * 为何还有个紫色星号"。现在跑的时候转/呼吸的就是当前这件事自己的图标，
            * 收尾了才换成 ✓。
            */}
          <span
            className={
              "cs-live-icon"
              + (running ? (live?.thinking ? " spinning cs-live-think" : " breathing") : " cs-live-done")
            }
          >
            {running ? (live?.icon || "✻") : "✓"}
          </span>
          {live ? (
            /* key 换了就重新挂载 —— 新一行从下方滑入，视觉上"顶掉"上一行。 */
            <span className={"cs-live-text" + (live.thinking ? " cs-live-think" : "")} key={live.key}>
              <span className="cs-live-label">{live.label}</span>
              {live.detail && <span className="cs-live-detail">{live.detail}</span>}
            </span>
          ) : (
            <span className="cs-live-text"><span className="cs-live-label">执行过程</span></span>
          )}
          {tail && <span className="cs-live-tail">{tail}</span>}
        </button>
        <button
          type="button"
          className="cs-toggle"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          {open ? "收起" : "展开"}
          <span className="cs-toggle-caret">{open ? "⌃" : "⌄"}</span>
        </button>
      </div>

      {open && (
        <div className="cs-log-wrap">
          {skills.length > 0 && (
            <div className="cs-log-head">
              <span className="cs-ok">✓</span> 理解问题，匹配最合适的技能
              <span className="cs-skills">
                {skills.map((s) => (
                  <span className="cs-skill-chip" key={s.id} title={s.id}>✦ {s.title}</span>
                ))}
              </span>
            </div>
          )}

          {subs.length > 0 && (
            <div className="cs-log-head">
              <span className="cs-ok">✓</span>{" "}
              {/* 只有拿到并行凭据才敢写"并行"——见 ConsoleStep.parallel */}
              {subs.length > 1 && subs.some((s) => s.parallel)
                ? `派出 ${subs.length} 个子 agent 并行调研`
                : subs.length > 1
                  ? `派出 ${subs.length} 个子 agent 调研`
                  : "派出子 agent 调研"}
            </div>
          )}

          <div className="cs-log scroll-thin" ref={logRef} style={{ maxHeight: `${LOG_MAX_VH}vh` }}>
            {subs.map((s) => <LogRow key={s.key} step={s} />)}
            {real.map((s) => <LogRow key={s.key} step={s} />)}
            {notes.map((s) => <LogRow key={s.key} step={s} />)}
            {!subs.length && !real.length && !notes.length && (
              <div className="cs-log-empty">还没有执行步骤</div>
            )}
          </div>

          {plans.length > 0 && (
            <div className="cs-plans">
              <button type="button" className="cs-fold" onClick={() => setPlanOpen((v) => !v)}
                      aria-expanded={planOpen}>
                <i className="cs-icon">☰</i>
                <span>规划与汇报 {plans.length} 步</span>
                <span className="cs-caret">{planOpen ? "▾" : "▸"}</span>
              </button>
              {planOpen && (
                <div className="cs-log scroll-thin" style={{ maxHeight: `${LOG_MAX_VH}vh` }}>
                  {plans.map((s) => <LogRow key={s.key} step={s} />)}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
