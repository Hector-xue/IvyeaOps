/**
 * 执行叙述 —— 一轮任务在对话区里长出来的那条时间线。
 *
 * ── 换掉了什么 ────────────────────────────────────────────────────────────
 * 上一版把整轮执行压成**一行**（"思考 …最后半句话… 第 15 步 74.0s 展开"）。
 * 于是一轮跑几分钟的过程里，屏幕上就那一行在原地闪，底下是一大片空白，用户只能
 * 干等 —— 他既不知道刚才那 15 步都干了什么，也不知道接下来要干什么。用户的原话是
 * "完全不知道 agent 现在在干嘛、进行到哪一步了"。
 *
 * 现在铺开成一条**随着跑往下长**的叙述，形状对标 Claude Code：
 *
 *     · 我先把每一件的现状查清楚（读代码 + 读那条传图会话），再出计划。
 *       ⌁ 搜索 2 次 · 跑了 16 条命令
 *     · 调研完了，8 件事的根因我都定位到了。先说计划，然后按序开工。
 *       ✎ 写入 IvyGrow.tsx
 *
 * 两件事让它不会糊满屏：
 *   1. **连续的常规工具折成一行**，按类型计数（192 步也只占一行 —— 上一版把每步
 *      都铺开，实测能把整页糊满、把回答挤出屏幕，那正是它被压成一行的原因；
 *      压成一行是矫枉过正，聚合才是正解）。
 *   2. 只有**写操作、子 agent、MCP/板块能力、计划变更**单独成行 —— 它们各自是
 *      一件独立的事，混进计数里就看不见了。
 *
 * 思考按"批"成段：两次工具调用之间的所有思考合成一段人话（模型的思考流是连续的，
 * 按句切会碎成几百条）。这段人话里就带着"接下来要干什么" —— 那是模型自己说的，
 * 不是我们编的。
 */
import { useMemo, useState } from "react";
import IvyGrow from "./IvyGrow";
import { formatMs, type ConsoleStep } from "../../lib/stepLabels";

export type MatchedSkill = { id: string; title: string; domain?: string; score?: number };

/** 一轮里模型的一段思考。seq = 它被冲刷时已经有多少步 —— 靠它和步骤穿插排序，
 *  不用时钟（两边的时间戳来自不同时刻，排出来会打架）。 */
export type Thought = { seq: number; text: string };

/** 最多铺这么多条。更早的折进顶部一行，点开才看 —— 一条会话里翻几百条过程，
 *  滚动条会变得没法用，而用户真正在看的永远是最新那几条。 */
const FEED_TAIL = 40;

/** 单独成行的步骤：它们各自是一件事，折进"跑了 N 条命令"里就看不见了。 */
function isStandalone(s: ConsoleStep): boolean {
  if (s.phase === "subagent" || s.phase === "mcp" || s.phase === "board") return true;
  if (s.phase === "plan") return true;
  return ["write_file", "edit_file", "code_apply_patch", "remember",
          "memory_write", "execute_actions", "rollback"].includes(s.name);
}

/** 计数用的分类。文案照着"人会怎么说这件事"来写，不是照着工具名。 */
const BUCKETS: { match: (s: ConsoleStep) => boolean; one: (n: number) => string }[] = [
  { match: (s) => ["grep", "glob", "code_search", "code_symbols", "code_impact"].includes(s.name),
    one: (n) => `搜索 ${n} 次` },
  { match: (s) => ["read_file", "view_file"].includes(s.name), one: (n) => `读了 ${n} 个文件` },
  { match: (s) => ["list_dir"].includes(s.name), one: (n) => `看了 ${n} 个目录` },
  { match: (s) => ["run_command", "run_python", "run_tests", "bash_output", "kill_bash"].includes(s.name),
    one: (n) => `跑了 ${n} 条命令` },
  { match: (s) => ["web_search", "web_fetch"].includes(s.name), one: (n) => `上网查了 ${n} 次` },
  { match: (s) => s.phase === "knowledge", one: (n) => `查了 ${n} 次知识库` },
];

function summarize(steps: ConsoleStep[]): string {
  const counts = BUCKETS.map(() => 0);
  const other: string[] = [];
  for (const s of steps) {
    const i = BUCKETS.findIndex((b) => b.match(s));
    if (i >= 0) counts[i] += 1;
    else other.push(s.title);
  }
  const parts = BUCKETS.map((b, i) => (counts[i] ? b.one(counts[i]) : "")).filter(Boolean);
  // 分类之外的步骤：一两步就直接报名字（"读取报表"比"另有 1 步"有用得多），
  // 多了才退回计数 —— 那时候名字铺开反而看不清。
  if (other.length && other.length <= 2) parts.push(...other);
  else if (other.length) parts.push(`另有 ${other.length} 步`);
  return parts.join(" · ");
}

type FeedItem =
  | { kind: "think"; key: string; text: string }
  | { kind: "group"; key: string; steps: ConsoleStep[] }
  | { kind: "step"; key: string; step: ConsoleStep }
  | { kind: "skills"; key: string; skills: MatchedSkill[] };

function build(steps: ConsoleStep[], thoughts: Thought[], skills: MatchedSkill[]): FeedItem[] {
  const items: FeedItem[] = [];
  let group: ConsoleStep[] = [];
  const flush = () => {
    if (!group.length) return;
    items.push({ kind: "group", key: "g" + group[0].key, steps: group });
    group = [];
  };
  const bySeq = new Map<number, Thought[]>();
  for (const t of thoughts) {
    const rows = bySeq.get(t.seq) || [];
    rows.push(t);
    bySeq.set(t.seq, rows);
  }
  if (skills.length) items.push({ kind: "skills", key: "sk", skills });
  for (let i = 0; i <= steps.length; i++) {
    for (const t of bySeq.get(i) || []) {
      flush();                                   // 想完了才动手：思考在前，工具在后
      items.push({ kind: "think", key: `t${i}-${items.length}`, text: t.text });
    }
    const s = steps[i];
    if (!s) break;
    // 注记（老 agent 的自由文本叙述）当思考看待 —— 它说的就是同一件事。
    if (s.phase === "note") {
      flush();
      items.push({ kind: "think", key: "n" + s.key, text: s.detail || s.title });
      continue;
    }
    if (isStandalone(s)) {
      flush();
      items.push({ kind: "step", key: s.key, step: s });
      continue;
    }
    group.push(s);
  }
  flush();
  return items;
}

function StatusMark({ status }: { status: ConsoleStep["status"] }) {
  if (status === "running") return <span className="af-mark af-run" aria-label="进行中" />;
  if (status === "error") return <span className="af-mark af-err" aria-label="失败">✕</span>;
  // 被护栏拦下是流程纠偏（"先列计划再动手"），不是出错，别用红叉吓人。
  if (status === "blocked") return <span className="af-mark af-blocked" aria-label="已拦截">⊘</span>;
  return <span className="af-mark af-ok" aria-label="完成">✓</span>;
}

/** 展开后的一步。参数点开看原始值。 */
function StepRow({ step }: { step: ConsoleStep }) {
  const [open, setOpen] = useState(false);
  const hasArgs = !!step.args && Object.keys(step.args).length > 0;
  return (
    <div className={"af-row af-" + step.phase}>
      <button type="button" className="af-row-head" aria-expanded={hasArgs ? open : undefined}
              style={hasArgs ? undefined : { cursor: "default" }}
              onClick={() => hasArgs && setOpen((v) => !v)}>
        <StatusMark status={step.status} />
        <i className="af-row-icon">{step.icon}</i>
        <span className="af-row-title">{step.title}</span>
        {step.detail && <span className="af-row-detail">{step.detail}</span>}
        {step.destructive && <span className="af-badge">写操作</span>}
        {/* 计划/汇报类不显示耗时：todo_write 就是几毫秒，那个数说明不了任何事，
            却占着行尾最显眼的位置。 */}
        {step.phase !== "plan" && <span className="af-row-ms">{formatMs(step.ms)}</span>}
        {hasArgs && <span className="af-caret">{open ? "▾" : "▸"}</span>}
      </button>
      {open && hasArgs && <pre className="af-args scroll-thin">{JSON.stringify(step.args, null, 2)}</pre>}
    </div>
  );
}

/** 一批常规工具折成的那一行。点开看每一步。 */
function GroupRow({ steps, live }: { steps: ConsoleStep[]; live: boolean }) {
  const [open, setOpen] = useState(false);
  const running = steps.some((s) => s.status === "running");
  const failed = steps.some((s) => s.status === "error");
  return (
    <div className="af-group">
      <button type="button" className="af-group-head" onClick={() => setOpen((v) => !v)}
              aria-expanded={open} title={open ? "收起这一批" : "看这一批都做了什么"}>
        <i className={"af-group-icon" + (running && live ? " af-spin" : "")}>
          {failed ? "✕" : running && live ? "◐" : "⌁"}
        </i>
        <span className="af-group-text">{summarize(steps)}</span>
        {/* 正在跑的那一步单独露出来：一批里最想知道的就是"此刻卡在哪一步" */}
        {running && live && (
          <span className="af-group-now">
            {steps.filter((s) => s.status === "running").slice(-1)[0]?.title}
          </span>
        )}
        <span className="af-caret">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="af-group-body scroll-thin">
          {steps.map((s) => <StepRow key={s.key} step={s} />)}
        </div>
      )}
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
  /** 还没被冲刷成段的那一段思考 —— 正在想的话就该边想边显示。 */
  liveThought?: string;
}) {
  const [showAll, setShowAll] = useState(false);
  const items = useMemo(() => build(steps, thoughts, skills), [steps, thoughts, skills]);

  if (!items.length && !running && !liveThought) return null;

  const hidden = showAll ? 0 : Math.max(0, items.length - FEED_TAIL);
  const shown = hidden ? items.slice(hidden) : items;
  const realSteps = steps.filter((s) => s.phase !== "note" && s.phase !== "plan").length;

  return (
    <div className="af">
      {hidden > 0 && (
        <button type="button" className="af-more" onClick={() => setShowAll(true)}>
          ↑ 展开更早的 {hidden} 段执行过程
        </button>
      )}
      {shown.map((item, i) => {
        const last = i === shown.length - 1;
        if (item.kind === "think") {
          return (
            <div className="af-think" key={item.key}>
              <span className="af-dot" />
              <p>{item.text}</p>
            </div>
          );
        }
        if (item.kind === "skills") {
          return (
            <div className="af-think af-skills" key={item.key}>
              <span className="af-dot af-dot-skill">✦</span>
              <p>已匹配 {item.skills.length} 项技能：{item.skills.map((s) => s.title).join("、")}</p>
            </div>
          );
        }
        if (item.kind === "group") {
          return <GroupRow key={item.key} steps={item.steps} live={!!running && last} />;
        }
        return (
          <div className="af-single" key={item.key}>
            <StepRow step={item.step} />
          </div>
        );
      })}
      {/* 正在想的那一段：边想边出字。想完（去调工具了）它就变成上面的一段。 */}
      {running && liveThought.trim() && (
        <div className="af-think af-live" key="live">
          <span className="af-dot af-dot-live"><IvyGrow /></span>
          <p>{liveThought}</p>
        </div>
      )}
      {running && (
        <div className="af-foot">
          {!liveThought.trim() && <span className="af-foot-ivy"><IvyGrow /></span>}
          <span>{realSteps > 0 ? `第 ${realSteps} 步` : "正在准备"}</span>
          {elapsedMs !== undefined && <span>· {formatMs(elapsedMs)}</span>}
        </div>
      )}
    </div>
  );
}
