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
import { useMemo, useState, useSyncExternalStore } from "react";
import Icon from "../Icon";
import StepsMark from "./StepsMark";
import ThinkingDots from "./ThinkingDots";
import { formatMs, type ConsoleStep } from "../../lib/stepLabels";
import { ivyeaMemoryIrrelevant } from "../../api/ivyeaAgent";
import { isFeedCollapsed, subscribeFeedCollapse, toggleFeed } from "../../lib/feedCollapse";

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
  | { kind: "skills"; key: string; skills: MatchedSkill[] }
  | { kind: "memory"; key: string; names: string[] };

function build(steps: ConsoleStep[], thoughts: Thought[], skills: MatchedSkill[],
               memoryRecall: string[] = []): FeedItem[] {
  const items: FeedItem[] = [];
  const bySeq = new Map<number, Thought[]>();
  for (const t of thoughts) {
    const rows = bySeq.get(t.seq) || [];
    rows.push(t);
    bySeq.set(t.seq, rows);
  }
  // 记忆排在技能前面：它发生在这一轮最开头（开口前先回忆）。
  if (memoryRecall.length) items.push({ kind: "memory", key: "mem", names: memoryRecall });
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
  if (status === "error") {
    return <span className="af-mark af-err" aria-label="失败"><Icon name="step-err" size={12} strokeWidth={2.6} /></span>;
  }
  // 被护栏拦下是流程纠偏（"先列计划再动手"），不是出错，别用红叉吓人。
  if (status === "blocked") {
    return <span className="af-mark af-blocked" aria-label="已拦截"><Icon name="step-blocked" size={12} strokeWidth={2.2} /></span>;
  }
  return <span className="af-mark af-ok" aria-label="完成"><Icon name="step-ok" size={13} strokeWidth={2.8} /></span>;
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
        <i className="af-icon"><Icon name={step.icon} size={14} /></i>
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
  steps, thoughts = [], skills = [], memoryRecall = [], running, elapsedMs, liveThought = "",
  feedKey = "", stage = "",
}: {
  steps: ConsoleStep[];
  /** 模型的思考，按批成段（agent ≥ v1.10.3 且开了 stream_reasoning）。没有就是没有。 */
  thoughts?: Thought[];
  skills?: MatchedSkill[];
  memoryRecall?: string[];
  running?: boolean;
  elapsedMs?: number;
  /** 还没被冲刷成段的那一段思考 —— 正在想的话就该边想边显示（同样只占一行）。 */
  liveThought?: string;
  /**
   * 这一块在整页里的身份，供"一键收起"记住单块覆盖。
   * 一轮会渲染多个过程块，所以不能只用轮 id。
   */
  feedKey?: string;
  /**
   * 准备阶段在做什么（agent ≥ v1.16.6 的 stage 事件）。
   * 没有它时退回原来那句"正在准备" —— 不假装知道。
   */
  stage?: string;
}) {
  /*
   * 折叠状态走全局 store 而不是各自的 useState：一轮里有多个过程块、一个会话又有
   * 很多轮，逐个点收起是用户实际抱怨过的事。全局值管"没被单独动过"的块，
   * 用户点某一块就给那一块建个覆盖（见 lib/feedCollapse）。
   */
  const collapsed = useSyncExternalStore(
    subscribeFeedCollapse,
    () => isFeedCollapsed(feedKey),
    () => false, // 服务端渲染快照：一律按展开算
  );
  const [showAll, setShowAll] = useState(false);
  // 点过"这条没关系"的记忆：只在本地划掉，不重排也不隐藏整行 —— 用户要看得见
  // 自己刚才做了什么。真正的降权发生在 agent 那边（扣命中 + 记 misses）。
  const [dismissed, setDismissed] = useState<string[]>([]);
  const dismissMemory = (name: string) => {
    setDismissed((prev) => (prev.includes(name) ? prev : [...prev, name]));
    // 失败也不回滚：这只是一条反馈，为它弹错误提示的干扰大于价值。
    void ivyeaMemoryIrrelevant(name).catch(() => {});
  };
  const items = useMemo(() => build(steps, thoughts, skills, memoryRecall),
                        [steps, thoughts, skills, memoryRecall]);
  const live = running && liveThought.trim() ? oneLine(liveThought) : "";

  if (!items.length && !running) return null;

  const realSteps = steps.filter((s) => s.phase !== "note" && s.phase !== "plan").length;
  /*
   * 阶段性汇报：最近一个还没跑完的步骤在做什么；都跑完了就报最后一个。
   * 只取"类型"不取参数 —— 参数动辄是一整条命令，塞进头部这一行会把耗时挤出去，
   * 而这一行的硬约束是**永远只占一行**。
   */
  const nowDoing = useMemo(() => {
    const real = steps.filter((s) => s.phase !== "note" && s.phase !== "plan");
    const active = [...real].reverse().find((s) => s.status === "running");
    return (active || real[real.length - 1])?.title || "";
  }, [steps]);
  const hidden = showAll ? 0 : Math.max(0, items.length - FEED_TAIL);
  const shown = hidden ? items.slice(hidden) : items;

  return (
    <div className={"af" + (collapsed ? " af-collapsed" : "")}>
      {/* 折叠开关。常驻、带文字、点得着 —— 上一版是行尾一个 9px 的箭头，没人认得出。 */}
      <button type="button" className="af-head" onClick={() => toggleFeed(feedKey)}
              aria-expanded={!collapsed}>
        {/* 一个图标管两个状态（跑完/跑着），形状不变、只是行依次亮 —— 换图标时
            页面上不会有一次形状跳变。见 components/console/StepsMark。 */}
        <span className="af-head-ivy">
          <StepsMark running={running} />
        </span>
        {/* 标题**挨着自己的图标**，于是它的左边缘落在下面那一列工具图标上：
            整块从上到下只剩两条竖线（状态/引线一条、图标/标题一条），而不是三条。
            这里曾经有一个空占位格把标题推到第三列（和「联网搜索」同列），
            用户看下来还是觉得标题被推得太靠右 —— 现在贴回来。 */}
        <span className="af-head-label">执行过程</span>
        <span className="af-head-meta">
          {/* 用 filter 拼，别用固定的分隔符：0 步的时候原来会拼出孤零零一个
              "· 5.4s"，看着像前面掉了什么东西。 */}
          {/*
              跑着的时候这里要回答"现在到底在干什么"：
              - 还没有步骤 → 显示准备阶段（"载入会话历史与记忆"/"等待模型响应"）。
                agent 老版本没有 stage 事件，退回原来那句"正在准备"，不假装知道。
              - 已经有步骤 → 显示**阶段性汇报**："37 步 · 正在 写入文件 · 2m14s"。
                此前只有一个步数，Windows 上一轮几十步跑十几分钟，界面上那个数字
                一直在涨却说不出在涨什么（用户原话："一大串的执行列表，没有阶段性
                的输出汇报"）。
           */}
          {[realSteps > 0 ? `${realSteps} 步` : (running ? (stage || "正在准备") : ""),
            running && realSteps > 0 && nowDoing ? `正在 ${nowDoing}` : "",
            elapsedMs !== undefined ? formatMs(elapsedMs) : ""].filter(Boolean).join(" · ")}
        </span>
        <span className="af-head-toggle">
          {collapsed ? "展开" : "收起"}
          <Icon name={collapsed ? "chev-down" : "chev-up"} size={13} />
        </span>
      </button>

      {!collapsed && (
        <div className="af-body">
          {hidden > 0 && (
            <button type="button" className="af-more" onClick={() => setShowAll(true)}>
              <Icon name="chev-up" size={12} /> 展开更早的 {hidden} 行
            </button>
          )}
          {shown.map((item) => {
            if (item.kind === "think") {
              return (
                <div className="af-item" key={item.key}>
                  <div className="af-line af-think" title={item.text}>
                    <span className="af-mark" />
                    <i className="af-icon"><Icon name="step-think" size={14} /></i>
                    <span className="af-kind">思考</span>
                    <span className="af-text">· {oneLine(item.text)}</span>
                  </div>
                </div>
              );
            }
            if (item.kind === "memory") {
              return (
                <div className="af-item" key={item.key}>
                  <div className="af-line af-think">
                    <span className="af-mark" />
                    <i className="af-icon">🧠</i>
                    <span className="af-kind">记忆</span>
                    <span className="af-text">
                      · 回忆了 {item.names.length} 条 ·{" "}
                      {/* 每条后面挂一个"×"：看到它引用了不相干的东西，顺手点一下。
                          这是误召唯一可持续的发现渠道 —— 靠人翻日志找不现实，而
                          这一行本来就在眼前。点过之后 agent 会给那条降权。 */}
                      {item.names.map((n, i) => (
                        <span className="af-mem" key={n}>
                          {i > 0 && "、"}
                          <span className={dismissed.includes(n) ? "af-mem-off" : undefined}>
                            {n.split("/").pop()}
                          </span>
                          {!dismissed.includes(n) && (
                            <button type="button" className="af-mem-x"
                                    title="这条跟我问的没关系"
                                    onClick={() => dismissMemory(n)}>×</button>
                          )}
                        </span>
                      ))}
                    </span>
                  </div>
                </div>
              );
            }
            if (item.kind === "skills") {
              return (
                <div className="af-item" key={item.key}>
                  <div className="af-line af-think">
                    <span className="af-mark" />
                    <i className="af-icon"><Icon name="step-skill" size={14} /></i>
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
                <span className="af-mark" />
                <i className="af-icon af-icon-live"><ThinkingDots /></i>
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
