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
import { imageRefUrl } from "../api/assistant";
import { stripInjected } from "./stripInjected";
import { mergeStep, stepFromEvent, type ConsoleStep } from "./stepLabels";
import type { ServerStats } from "./turnStats";
import { restatedIndexes } from "./restatement";

/** 一份会话附件在记录里的样子：文件名 +（可能有的）原件下载地址。 */
export type RestoredDoc = { name: string; url: string };

export type RestoredTurn = {
  role: "user" | "assistant";
  text: string;
  steps?: ConsoleStep[];
  skills?: { id: string; title: string; domain?: string; score?: number }[];
  /** 这一轮用户发的图（能直接放进 `<img src>` 的地址）。 */
  images?: string[];
  /**
   * 这一轮用户随消息带的**会话附件**文件名（只这轮用、没进知识库的那种）。
   *
   * 存的只有名字，没有正文：正文是 agent 注入给模型的那几万字，气泡里显示它
   * 等于把整份 PDF 糊在用户脸上（stripInjected 会把那段剥掉）。但"我上传过
   * 一份报价.pdf"这件事必须留下来 —— 附图当初就是漏了这一步，用户的原话是
   * "会话记录里面也没有展示我发送的图片"。
   */
  docs?: RestoredDoc[];
  /**
   * 这一格发生的时刻（毫秒）与本轮时长 —— 来自 agent 落盘的 `turn_times`。
   *
   * 为什么必须从服务端拿：跑的时候前端自己掐了表，但刷新、换标签页、换台机器打开
   * 同一条会话之后那些数一个都不剩；而这一轮跑完前端还会重新拉一次存档，纯前端记
   * 的数会被那次拉取冲掉。老 agent 没有这个字段 → 不显示，而不是编一个。
   */
  at?: number;
  endedAt?: number;
  elapsedMs?: number;
};

/**
 * 把存档里那条 user 消息里的附图句柄取出来。
 *
 * 图片本体从来不进模型，存档里留下的是 agent 注入的 `[用户附图 …]` 段落 + 每张图
 * 的 `ivyea-ref://` 句柄。没有这一步，刷新之后"我发过一张图"这件事在界面上就彻底
 * 消失了 —— 用户原话："会话记录里面也没有展示我发送的图片"。
 */
function attachedImages(content: string): string[] {
  const at = content.indexOf("\n\n[用户附图");
  if (at < 0) return [];
  const refs = content.slice(at).match(/ivyea-ref:\/\/[0-9a-f]+/g) || [];
  return [...new Set(refs)].map(imageRefUrl).slice(0, 4);
}

/**
 * 把存档里那条 user 消息里的**会话附件文件名**取出来。
 *
 * agent 注入的那段长这样（见 service._attachments_note）：
 *   [用户附件 —— 文档正文]
 *   本轮用户随消息带了 2 份文档…
 *   第 1 份（报价.pdf）：
 *   …正文…
 *
 * 只取名字。正文由 stripInjected 剥掉 —— 那是给模型看的几万字，不是给人看的。
 */
function attachedDocs(content: string): RestoredDoc[] {
  const at = content.indexOf("\n\n[用户附件");
  if (at < 0) return [];
  const out: RestoredDoc[] = [];
  // 形如「第 1 份（报价.pdf｜原件 /api/assistant/session-file/ab.pdf）：」。
  // 分隔符是**全角**竖线：半角的 | 在文件名里并不罕见，用它切会把名字切断。
  // 老会话没有「｜原件 …」那一段，第二个捕获组就是 undefined —— 那时只有名字，
  // 小标退回成纯文字（不可点），而不是给一个点了 404 的链接。
  const re = /第 \d+ 份（([^｜）]*)(?:｜原件 ([^）]*))?）：/g;
  const seen = new Set<string>();
  let m: RegExpExecArray | null;
  const body = content.slice(at);
  while ((m = re.exec(body)) !== null) {
    const name = (m[1] || "").trim();
    if (!name || seen.has(name)) continue;
    seen.add(name);
    // 只认站内相对地址。存档里的这串是 agent 原样抄过来的，理论上只可能是我们
    // 自己发的，但它毕竟穿过了模型那一侧的存档 —— 不校验就等于允许把任意
    // http(s) 地址渲染成一个用户会点的链接。
    const url = (m[2] || "").trim();
    out.push({ name, url: url.startsWith("/api/assistant/session-file/") ? url : "" });
  }
  return out.slice(0, 4);
}

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

  // 逐轮时刻表。轮号与详情分页同源（都数"第几条真实用户消息"），本页从 turns.from 起。
  const timeByTurn = new Map<number, { started_at: number; ended_at: number; ms: number }>();
  for (const row of detail?.turn_times || []) {
    if (row && typeof row.turn === "number") timeByTurn.set(row.turn, row);
  }
  let turnNo = detail?.turns?.from ?? 0;
  // 每一格属于第几轮 —— 收尾时刻要挂到**这一轮最后一条**回答上，不是每条都挂。
  const turnNoOf = new Map<RestoredTurn, number>();

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
      const raw = String(row.content || "");
      const text = stripInjected(raw);
      const images = attachedImages(raw);
      const docs = attachedDocs(raw);
      // 上一轮没来得及归位的步骤（比如最后一步之后模型没再说话）挂到上一个 assistant 上
      if (pending.length) {
        const last = [...turns].reverse().find((t) => t.role === "assistant");
        if (last) flushInto(last);
        else { pending = []; pendingSkills = undefined; }
      }
      // 只发图/只带附件、一个字没打，也是一轮 —— 不留下这一格，那一轮整个消失。
      if (text || images.length || docs.length) {
        const at = timeByTurn.get(turnNo)?.started_at;
        const turn: RestoredTurn = {
          role: "user", text, ...(images.length ? { images } : {}),
          ...(docs.length ? { docs } : {}),
          ...(at ? { at: at * 1000 } : {}),
        };
        turnNoOf.set(turn, turnNo);
        turns.push(turn);
      }
      // 轮号跟着**每一条**用户消息走，哪怕这一格没画出来 —— 跳一格就会让后面所有
      // 轮次的时间戳整体错位，而错位的时间戳比没有更糟：它看着是真的。
      turnNo += 1;
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
    turnNoOf.set(turn, Math.max(0, turnNo - 1));
    turns.push(turn);
  }

  // 收尾：最后一批步骤还没归位（一轮以工具调用结束、没有收尾正文）
  if (pending.length) {
    const last = [...turns].reverse().find((t) => t.role === "assistant");
    if (last) flushInto(last);
    else turns.push({ role: "assistant", text: "", steps: pending });
  }

  // 同一段话被后面那条**整段重抄了一遍**（判据见 lib/restatement）。有些模型每次
  // 工具调用之后都会把已经说过的结论从头再写一遍，存档里那几条都是真实存在的
  // assistant 消息 —— 恢复出来就是同一张表连着铺三遍，和直播时看到的一样。
  // 只留最后那一份；被作废的那一格如果还挂着执行过程就留下壳（工具是真跑过的，
  // 不能跟着话一起没），什么都不挂的才整格拿掉。
  const assistantsByTurn = new Map<number, RestoredTurn[]>();
  for (const turn of turns) {
    if (turn.role !== "assistant") continue;
    const no = turnNoOf.get(turn);
    if (no === undefined) continue;
    const group = assistantsByTurn.get(no);
    if (group) group.push(turn);
    else assistantsByTurn.set(no, [turn]);
  }
  const superseded = new Set<RestoredTurn>();
  for (const group of assistantsByTurn.values()) {
    const drop = restatedIndexes(group.map((t) => t.text || ""));
    group.forEach((turn, i) => {
      if (!drop.has(i)) return;
      if (turn.steps?.length) turn.text = "";
      else superseded.add(turn);
    });
  }
  for (let i = turns.length - 1; i >= 0; i--) {
    if (superseded.has(turns[i])) turns.splice(i, 1);
  }

  // 收尾时刻与时长挂在**每一轮最后一条**回答上：一轮里 agent 边做边说会产生好几条
  // assistant 消息，每条都挂一遍"结束于 09:49"就成了满屏重复的同一个时刻。
  const lastOfTurn = new Map<number, RestoredTurn>();
  for (const turn of turns) {
    if (turn.role !== "assistant") continue;
    const no = turnNoOf.get(turn);
    if (no !== undefined) lastOfTurn.set(no, turn);
  }
  for (const [no, turn] of lastOfTurn) {
    const row = timeByTurn.get(no);
    if (!row?.ended_at) continue;
    turn.endedAt = row.ended_at * 1000;
    if (row.ms > 0) turn.elapsedMs = row.ms;
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
