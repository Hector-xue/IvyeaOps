/**
 * 实时状态坞 —— 钉在输入框正上方那一条"它现在在干什么、下一步要干什么"。
 *
 * ── 为什么非要再来一条 ────────────────────────────────────────────────────
 * 执行叙述（ActivityFeed）挂在**那一轮的气泡下面**，跟着对话一起滚。人翻上去看
 * 历史时它就被滚出视口了 —— 屏幕上只剩正文在流，不知道 Agent 在跑第几步、卡在哪。
 *
 * 所以这一条**不参与滚动**，跟着输入框走。规矩是**同一时刻只出现一个**：人在底部
 * 时叙述就在眼前，这条不出现（否则就是同一句话说两遍，用户截图里正是上下两条
 * 一模一样的"思考…"）；翻上去了它才顶上来接着说。
 *
 * ── 它比活动行多说什么 ────────────────────────────────────────────────────
 * 活动行只回答"刚才做了什么"。这里多回答两件事：
 *   · **接下来要干什么** —— 取 Agent 自己写的计划（todo_write）里紧跟在
 *     进行中那条后面的第一条待办。这不是猜的，是它自己排的队。
 *   · **进行到哪了** —— 计划完成度（3/7）、第几步、已用时。
 * 计划一条都没有时就不显示"下一步"，绝不编一句"正在处理中"充数：编出来的下一步
 * 比没有更糟，用户会拿它当承诺。
 */
import { useState } from "react";
import IvyGrow from "./IvyGrow";
import { formatMs, type ConsoleStep } from "../../lib/stepLabels";
import type { RailTodo } from "./ArtifactRail";

export type LiveDockProps = {
  running: boolean;
  steps: ConsoleStep[];
  /** 模型思考流的尾巴。没有会思考的模型时是空串 —— 那就退回显示当前步骤。 */
  reasoning?: string;
  elapsedMs?: number;
  /** Agent 自己写的计划（todo_write）。流式过程中就在变。 */
  todos: RailTodo[];
  onStop?: () => void;
};

const isDone = (t: RailTodo) => String(t.status || "") === "completed";
const isDoing = (t: RailTodo) => String(t.status || "") === "in_progress";

/** 现在这一刻最能说明"在干什么"的一行：思考 > 正在跑的步 > 最后一步。 */
function nowLine(steps: ConsoleStep[], reasoning: string) {
  if (reasoning.trim()) {
    // 取**开头**不取结尾：取结尾的话字一多这行就不停左移，一行之内照样在抖。
    return { icon: "", label: "思考", detail: reasoning.replace(/\s+/g, " ").trim().slice(0, 70),
             thinking: true };
  }
  for (let i = steps.length - 1; i >= 0; i--) {
    if (steps[i].status === "running") {
      return { icon: steps[i].icon, label: steps[i].title, detail: steps[i].detail, thinking: false };
    }
  }
  const last = steps[steps.length - 1];
  if (last) return { icon: last.icon, label: last.title, detail: last.detail, thinking: false };
  return { icon: "", label: "正在准备", detail: "", thinking: true };
}

export default function LiveDock({
  running, steps, reasoning = "", elapsedMs, todos, onStop,
}: LiveDockProps) {
  const [open, setOpen] = useState(false);
  if (!running) return null;

  const now = nowLine(steps, reasoning);
  // 规划/汇报类的步不算"步"——它们讲的是怎么组织，不是做了什么（和统计条同一条口径）。
  const realSteps = steps.filter((s) => s.phase !== "note" && s.phase !== "plan").length;

  const done = todos.filter(isDone).length;
  const doing = todos.find(isDoing);
  // "下一步" = 计划里进行中那条**后面**的第一条待办；没有进行中的就取第一条待办。
  const doingAt = doing ? todos.indexOf(doing) : -1;
  const next = todos.slice(doingAt + 1).find((t) => !isDone(t) && !isDoing(t));

  return (
    <div className={"ld" + (open ? " ld-open" : "")}>
      <button type="button" className="ld-main" onClick={() => setOpen((v) => !v)}
              aria-expanded={open}
              title={open ? "收起计划" : "展开 Agent 的计划"}>
        <span className={"ld-icon" + (now.thinking ? " ld-think" : " ld-work")}>
          {now.thinking ? <IvyGrow /> : now.icon}
        </span>
        <span className="ld-now">
          <span className="ld-label">{now.label}</span>
          {now.detail && <span className="ld-detail">{now.detail}</span>}
        </span>
        {/* 下一步：Agent 自己排的队，没有就整块不出现。 */}
        {next?.content && (
          <span className="ld-next" title={`接下来：${next.content}`}>
            <span className="ld-arrow">→</span>
            <span className="ld-next-text">{next.content}</span>
          </span>
        )}
        <span className="ld-tail">
          {todos.length > 0 && <span className="ld-count">{done}/{todos.length}</span>}
          {realSteps > 0 && <span>第 {realSteps} 步</span>}
          {elapsedMs !== undefined && <span>{formatMs(elapsedMs)}</span>}
          {todos.length > 0 && <span className="ld-caret">{open ? "▾" : "▸"}</span>}
        </span>
      </button>
      {onStop && (
        <button type="button" className="ld-stop" onClick={onStop} title="停止这一轮">
          停止
        </button>
      )}
      {open && todos.length > 0 && (
        <ol className="ld-plan scroll-thin">
          {todos.map((t, i) => (
            <li key={i} className={isDone(t) ? "ld-t-done" : isDoing(t) ? "ld-t-doing" : "ld-t-todo"}>
              <i>{isDone(t) ? "✓" : isDoing(t) ? "▶" : "○"}</i>
              <span>{t.content || "（这条计划没写内容）"}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
