/**
 * 执行叙述 —— 一轮任务在对话区里长出来的那条时间线。形态对标 DeepSeek Harness。
 *
 * ── 规矩只有一条：**一件事一行，单行截断，行高恒定** ──────────────────────
 * 这不是排版偏好，是这块界面唯一的硬约束。它同时解掉三个真实毛病：
 *
 *   1. **不许跳动**。上一版把思考渲染成会换行的段落，而思考是**流式**的 ——
 *      每来几个字就多一行，底下所有内容跟着往下顶；再叠上"内容贴底"的布局，
 *      整块文字每一帧都在往上跳。用户原话："文字上下不停跳动"。
 *      行高恒定之后，新内容只在**末尾追加**，已经排好的行一个像素都不动。
 *   2. **不许糊满屏**。一行 22px，192 步就是一块可以滚的日志，而不是一面
 *      把回答挤出屏幕的芯片墙。
 *   3. **看得清在干什么**。每行是 `图标 类型 · 摘要`（"执行命令 · npm --version"），
 *      和 dsh 的 `Bash · List working directory contents` 是同一种读法。
 *
 * 还有一条同样重要：**流式中的思考显示开头，不显示结尾**。显示结尾的话，字一多
 * 文本就不停左移，一行之内照样在抖；显示开头则前缀恒定，只有尾巴在省略号里增长，
 * 视觉上是静止的。
 *
 * 折叠：整条过程可以一键收起成一行（"执行过程 · 15 步 · 74.0s"）。上一版把它做成
 * 行尾一个 9px 的小箭头，用户根本没认出那是开关 —— 所以它现在是一条明确的、
 * 常驻的头部按钮。
 */
import { useMemo, useState } from "react";
import IvyGrow from "./IvyGrow";
import { formatMs, type ConsoleStep } from "../../lib/stepLabels";

export type MatchedSkill = { id: string; title: string; domain?: string; score?: number };

/** 一轮里模型的一段思考。seq = 它被冲刷时已经有多少步 —— 靠它和步骤穿插排序，
 *  不用时钟（两边的时间戳来自不同时刻，排出来会打架）。 */
export type Thought = { seq: number; text: string };

/** 一行里最多显示多少字。再长也只是把省略号往后推，反而让行与行看起来不齐。 */
const LINE_MAX = 160;

/** 铺开的上限。再多就折进"更早的过程"里 —— 几百行的时候用户看的永远是最新那几十行。 */
const FEED_TAIL = 120;

type FeedItem =
  | { kind: "think"; key: string; text: string }
  | { kind: "step"; key: string; step: ConsoleStep }
  | { kind: "skills"; key: string; skills: MatchedSkill[] };

function build(steps: ConsoleStep[], thoughts: Thought[], skills: MatchedSkill[]): FeedItem[] {
  const items: FeedItem[] = [];
  const bySeq = new Map<number, Thought[]>();
  for (const t of thoughts) {
    const rows = bySeq.get(t.seq) || [];
    rows.push(t);
    bySeq.set(t.seq, rows);
  }
  if (skills.length) items.push({ kind: "skills", key: "sk", skills });
  for (let i = 0; i <= steps.length; i++) {
    // 想在前、做在后：这一段思考是在第 i 步**之前**冲刷的。
    for (const [n, t] of (bySeq.get(i) || []).entries()) {
      items.push({ kind: "think", key: `t${i}-${n}`, text: t.text });
    }
    const s = steps[i];
    if (s) items.push({ kind: "step", key: s.key, step: s });
  }
  return items;
}

/** 一行的文本：单行、压掉换行、超长截断。**截前面留前缀**，见文件头。 */
function oneLine(text: string): string {
  const flat = String(text || "").replace(/\s+/g, " ").trim();
  return flat.length > LINE_MAX ? flat.slice(0, LINE_MAX) + "…" : flat;
}

function StatusMark({ status }: { status: ConsoleStep["status"] }) {
  if (status === "running") return <span className="af-mark af-run" aria-label="进行中" />;
  if (status === "error") return <span className="af-mark af-err" aria-label="失败">✕</span>;
  // 被护栏拦下是流程纠偏（"先列计划再动手"），不是出错，别用红叉吓人。
  if (status === "blocked") return <span className="af-mark af-blocked" aria-label="已拦截">⊘</span>;
  return <span className="af-mark af-ok" aria-label="完成">✓</span>;
}

/** 一步 = 一行。有参数时点开在下面摊一块原始参数。 */
function StepLine({ step }: { step: ConsoleStep }) {
  const [open, setOpen] = useState(false);
  const hasArgs = !!step.args && Object.keys(step.args).length > 0;
  return (
    <div className={"af-item af-" + step.phase}>
      <button type="button" className="af-line" aria-expanded={hasArgs ? open : undefined}
              style={hasArgs ? undefined : { cursor: "default" }}
              title={step.detail ? `${step.title} · ${step.detail}` : step.title}
              onClick={() => hasArgs && setOpen((v) => !v)}>
        <StatusMark status={step.status} />
        <i className="af-icon">{step.icon}</i>
        <span className="af-kind">{step.title}</span>
        <span className="af-text">{step.detail ? `· ${oneLine(step.detail)}` : ""}</span>
        {step.destructive && <span className="af-badge">写操作</span>}
        {/* 计划/汇报类不显示耗时：todo_write 就几毫秒，那个数说明不了任何事。 */}
        {step.phase !== "plan" && step.ms !== undefined && (
          <span className="af-ms">{formatMs(step.ms)}</span>
        )}
      </button>
      {open && hasArgs && <pre className="af-args scroll-thin">{JSON.stringify(step.args, null, 2)}</pre>}
    </div>
  );
}

export default function ActivityFeed({
  steps, thoughts = [], skills = [], running, elapsedMs, liveThought = "",
}: {
  steps: ConsoleStep[];
  /** 模型的思考，按批成段（agent ≥ v1.10.3 且开了 stream_reasoning）。没有就是没有。 */
  thoughts?: Thought[];
  skills?: MatchedSkill[];
  running?: boolean;
  elapsedMs?: number;
  /** 还没被冲刷成段的那一段思考 —— 正在想的话就该边想边显示（同样只占一行）。 */
  liveThought?: string;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const items = useMemo(() => build(steps, thoughts, skills), [steps, thoughts, skills]);
  const live = running && liveThought.trim() ? oneLine(liveThought) : "";

  if (!items.length && !running) return null;

  const realSteps = steps.filter((s) => s.phase !== "note" && s.phase !== "plan").length;
  const hidden = showAll ? 0 : Math.max(0, items.length - FEED_TAIL);
  const shown = hidden ? items.slice(hidden) : items;

  return (
    <div className={"af" + (collapsed ? " af-collapsed" : "")}>
      {/* 折叠开关。常驻、带文字、点得着 —— 上一版是行尾一个 9px 的箭头，没人认得出。 */}
      <button type="button" className="af-head" onClick={() => setCollapsed((v) => !v)}
              aria-expanded={!collapsed}>
        <span className="af-head-ivy">{running ? <IvyGrow /> : <i className="af-head-dot">⌁</i>}</span>
        <span className="af-head-label">执行过程</span>
        <span className="af-head-meta">
          {/* 用 filter 拼，别用固定的分隔符：0 步的时候原来会拼出孤零零一个
              "· 5.4s"，看着像前面掉了什么东西。 */}
          {[realSteps > 0 ? `${realSteps} 步` : (running ? "正在准备" : ""),
            elapsedMs !== undefined ? formatMs(elapsedMs) : ""].filter(Boolean).join(" · ")}
        </span>
        <span className="af-head-toggle">{collapsed ? "展开 ⌄" : "收起 ⌃"}</span>
      </button>

      {!collapsed && (
        <div className="af-body">
          {hidden > 0 && (
            <button type="button" className="af-more" onClick={() => setShowAll(true)}>
              ↑ 展开更早的 {hidden} 行
            </button>
          )}
          {shown.map((item) => {
            if (item.kind === "think") {
              return (
                <div className="af-item" key={item.key}>
                  <div className="af-line af-think" title={item.text}>
                    <span className="af-mark">{"\u00a0"}</span>
                    <i className="af-icon">✻</i>
                    <span className="af-kind">思考</span>
                    <span className="af-text">· {oneLine(item.text)}</span>
                  </div>
                </div>
              );
            }
            if (item.kind === "skills") {
              return (
                <div className="af-item" key={item.key}>
                  <div className="af-line af-think">
                    <span className="af-mark">{"\u00a0"}</span>
                    <i className="af-icon">✦</i>
                    <span className="af-kind">技能</span>
                    <span className="af-text">· {item.skills.map((s) => s.title).join("、")}</span>
                  </div>
                </div>
              );
            }
            return <StepLine key={item.key} step={item.step} />;
          })}
          {/* 正在想的那一段：同样一行。显示**开头**，所以字再多这一行也是静止的。 */}
          {live && (
            <div className="af-item" key="live">
              <div className="af-line af-think af-live">
                <span className="af-mark">{"\u00a0"}</span>
                <i className="af-icon af-icon-live"><IvyGrow /></i>
                <span className="af-kind">思考</span>
                <span className="af-text">· {live}</span>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
