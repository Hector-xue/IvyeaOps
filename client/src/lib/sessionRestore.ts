/**
 * 历史会话详情 → 界面上的轮次。
 *
 * 为什么要有这个文件：刷新之后点开历史会话，此前只剩下光秃秃的问答文本 —— 执行过程
 * 一片空白，因为步骤只活在 SSE 流里。现在 agent 会把结构化步骤和消息一起落盘，这里
 * 把两者重新缝起来。
 *
 * 缝合靠 **call_id**，不靠下标：assistant 消息里的 `tool_calls[].id` 就是步骤事件的 `id`。
 * 下标会被上下文压缩、导入的历史、persist=false 的轮次错开，call_id 不会。
 *
 * 步骤的标签、板块/MCP 拆包、状态与耗时全部复用 lib/stepLabels 里那套（和直播时同一份
 * 代码），所以刷新前后看到的执行过程是同一个东西，而不是另画一版。
 */
import type { IvyeaChatSessionDetail, IvyeaStepEvent } from "../api/ivyeaAgent";
import { stripInjected } from "./stripInjected";
import { mergeStep, stepFromEvent, type ConsoleStep } from "./stepLabels";
import type { ServerStats } from "./turnStats";

export type RestoredTurn = {
  role: "user" | "assistant";
  text: string;
  steps?: ConsoleStep[];
  skills?: { id: string; title: string; domain?: string; score?: number }[];
};

export type RestoredSession = {
  turns: RestoredTurn[];
  /** 还有更早的对话没取回来（顶部要出现「加载更早」）。 */
  hasMore: boolean;
  /** 本页最早的轮号 —— 取更早一页时当游标传回去。 */
  from: number;
  total: number;
  /**
   * 服务端落盘的整会话累计账（agent ≥ v1.16.1）。恢复出来的轮次身上没有计时/用量
   * ——那些数只在跑的那一刻的浏览器里存在过——所以统计条改从这里取。
   * 老 agent 不回报时是 undefined：统计条退回只显示"几轮几步"，不编。
   */
  stats?: ServerStats;
};

export function restoreSession(detail: IvyeaChatSessionDetail | null | undefined): RestoredSession {
  const rows = detail?.messages || [];
  const stepById = new Map<string, IvyeaStepEvent>();
  for (const s of detail?.steps || []) {
    if (s && (s as any).id) stepById.set(String((s as any).id), s);
  }
  const skillByAnchor = new Map<string, RestoredTurn["skills"]>();
  for (const m of detail?.skill_matches || []) {
    if (m?.anchor) skillByAnchor.set(String(m.anchor), m.skills || []);
  }

  const turns: RestoredTurn[] = [];
  // 当前这一轮攒下的步骤。遇到下一条用户消息就清空 —— 步骤属于它所在的那一轮。
  let pending: ConsoleStep[] = [];
  let pendingSkills: RestoredTurn["skills"] | undefined;

  const flushInto = (turn: RestoredTurn) => {
    if (pending.length) turn.steps = pending;
    if (pendingSkills?.length) turn.skills = pendingSkills;
    pending = [];
    pendingSkills = undefined;
  };

  for (const row of rows) {
    const role = row?.role;
    if (role === "user") {
      // 注入给模型的技能/知识块和用户真正打的字存在同一条消息里，气泡里只留后者。
      const text = stripInjected(String(row.content || ""));
      // 上一轮没来得及归位的步骤（比如最后一步之后模型没再说话）挂到上一个 assistant 上
      if (pending.length) {
        const last = [...turns].reverse().find((t) => t.role === "assistant");
        if (last) flushInto(last);
        else { pending = []; pendingSkills = undefined; }
      }
      if (text) turns.push({ role: "user", text });
      continue;
    }
    if (role !== "assistant") continue;      // tool 行只用来对齐，不进气泡

    for (const call of row.tool_calls || []) {
      const ev = stepById.get(String(call.id));
      // 没有对应步骤记录（改动之前落盘的会话）就跳过：宁可少一行，也不拿工具名
      // 编一条"状态未知"的记录出来充数。
      if (!ev) continue;
      pending = mergeStep(pending, stepFromEvent(ev));
      const skills = skillByAnchor.get(String(call.id));
      if (skills?.length && !pendingSkills) pendingSkills = skills;
    }

    const text = stripInjected(String(row.content || ""));
    if (!text) continue;                     // 只带 tool_calls 的中间消息不是一条回答
    const turn: RestoredTurn = { role: "assistant", text };
    flushInto(turn);
    turns.push(turn);
  }

  // 收尾：最后一批步骤还没归位（一轮以工具调用结束、没有收尾正文）
  if (pending.length) {
    const last = [...turns].reverse().find((t) => t.role === "assistant");
    if (last) flushInto(last);
    else turns.push({ role: "assistant", text: "", steps: pending });
  }

  const t = detail?.turns;
  const stats = (detail as any)?.stats;
  return {
    turns,
    hasMore: !!t?.has_more,
    from: t?.from ?? 0,
    total: t?.total ?? 0,
    stats: stats && typeof stats === "object" ? (stats as ServerStats) : undefined,
  };
}

export default restoreSession;
