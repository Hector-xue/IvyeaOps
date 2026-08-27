/**
 * 一轮任务的事件流 → 界面状态。**只此一份**。
 *
 * ── 为什么要抽出来 ────────────────────────────────────────────────────────
 * 同一轮任务现在有两条进得来的路：
 *
 *   · 直连  —— `POST /chat/stream`，自己发起的那一轮；
 *   · 接进去 —— `GET /chat/sessions/{id}/live`，切走再回来 / 刷新 / 换台机器打开
 *     同一条会话时，从服务端的活轮日志把进度接上。
 *
 * 两条路必须把同一轮任务渲染成**同一个东西**。各写一份的下场是可预料的：一边
 * 补了 answer_reset、另一边没补，于是"从别处切回来看到的过程和自己发起时看到的
 * 不一样"，而这种不一致最难被发现 —— 两边看起来都挺正常。
 *
 * 所以事件的语义只在这里说一遍，两条路只是喂给它的字节来源不同。
 */
import {
  answerResetDiscards,
  type IvyeaContextUsage,
  type IvyeaFileChange,
  type IvyeaPermissionRequest,
  type IvyeaStreamHandlers,
} from "../api/ivyeaAgent";
import type { MatchedSkill } from "../components/console/ActivityFeed";
import { mergeStep, noteStep, stepFromEvent, type ConsoleStep } from "./stepLabels";

/** 老版本 agent 的自由文本叙述最多保留最近几行 —— 长任务的叙述能有几十条。 */
const MAX_NOTES = 12;
/** 一轮里最多留多少段思考。再多界面上也翻不完，而 state 每次 patch 都要拷一遍。 */
const THOUGHTS_MAX = 60;

/**
 * 正文被门禁打回重写时，在时间线上留一行 —— 气泡里那一稿被清掉了，
 * 不说一声的话用户只会看到字突然消失。
 */
const GATE_NOTE: Record<string, string> = {
  "gate:citation": "知识引用未通过校验，正在重写这段回答",
  "gate:verify": "完成前自验证未通过，正在重写这段回答",
  "gate:progress": "阶段汇报尚未闭环，正在重写这段回答",
};

/** 这一轮里模型的一段思考。seq = 冲刷时已经有多少步（靠它和步骤穿插排序）。 */
export type Thought = { seq: number; text: string };

/** 被这层改写的那部分轮次状态。Console 的 Turn 是它的超集。 */
export type TurnPatchable = {
  text: string;
  steps?: ConsoleStep[];
  thoughts?: Thought[];
  skills?: MatchedSkill[];
  approvals?: { req: IvyeaPermissionRequest; decision?: string }[];
  reasoning?: string;
  readonlyBlocked?: number;
  /** 正文的分段边界：每段是"两次工具调用之间说的那段话"。见 segments 的注释。 */
  segments?: { seq: number; text: string }[];
};

export type TurnStreamDeps = {
  patch: (patch: Partial<TurnPatchable> | ((t: TurnPatchable) => Partial<TurnPatchable>)) => void;
  notify: (kind: "success" | "warn" | "error" | "info", msg: string) => void;
  setFileChanges: (fn: (prev: IvyeaFileChange[]) => IvyeaFileChange[]) => void;
  setTodos: (rows: any[]) => void;
  setCtxUsage: (u: IvyeaContextUsage) => void;
  setModel?: (m: string) => void;
  setReadOnly?: (v: boolean) => void;
  /** 拿到 session_id（新会话第一次 start 时）。 */
  onSessionId?: (id: string) => void;
  /** final 事件的整包（用量、todos、上下文都在里面）。 */
  onFinal?: (data: any) => void;
  /** 每一帧落地正文时叫一声 —— 调用方用它做"贴底跟随"。 */
  onTick?: () => void;
};

export type TurnStream = {
  handlers: IvyeaStreamHandlers;
  /** 收尾：把还没落地的一帧补上（不补的话回答会缺最后几个字）。 */
  finish: () => void;
  /** 丢掉还没落地的那一帧（final 自带规范文本时用）。 */
  cancel: () => void;
  /** 本轮已收到的正文（final 到达后是终稿）。 */
  text: () => string;
  firstTokenAt: () => number;
  lastTokenAt: () => number;
  usage: () => any;
};

/**
 * 建一条"事件流 → 这一轮的界面状态"的管子。
 *
 * 所有 per-turn 的可变状态（token 缓冲、计时、注记序号）都关在这里面，
 * 调用方只管把字节喂进来。
 */
export function createTurnStream(deps: TurnStreamDeps): TurnStream {
  let pending = "";
  let pendingThink = "";
  let flushRaf = 0;
  let noteSeq = 0;
  let finalText = "";
  let firstTokenAt = 0;
  let lastTokenAt = 0;
  let turnUsage: any = null;

  // token 按帧批量落地。一个字一次 setState 时，长报告的 markdown 每秒被重解析
  // 几十遍；合并到一帧一次，内容一模一样，但渲染成本掉一个数量级。
  const flushTokens = () => {
    flushRaf = 0;
    const add = pending;
    const think = pendingThink;
    pending = "";
    pendingThink = "";
    if (add) deps.patch((t) => ({ text: t.text + add }));
    if (think) deps.patch((t) => ({ reasoning: ((t.reasoning || "") + think).slice(0, 400) }));
    if (add || think) deps.onTick?.();
  };
  const schedule = () => {
    if (!flushRaf) flushRaf = window.requestAnimationFrame(flushTokens);
  };
  const cancelFlush = () => {
    if (flushRaf) { window.cancelAnimationFrame(flushRaf); flushRaf = 0; }
    pending = "";
  };
  const finishFlush = () => {
    if (flushRaf) window.cancelAnimationFrame(flushRaf);
    flushTokens();
  };

  /**
   * 把"到此为止想的这一段"收成叙述里的一条。
   *
   * 触发点是**它开始动手**（下一个 step 到达）或这一轮收尾 —— 那才是一段思考
   * 真正结束的时刻。按句号切会把一段完整的推理碎成几十条，铺在页面上是字墙。
   */
  const flushThought = () => {
    finishFlush();                       // 先把还在缓冲里的思考落地，别丢半句
    deps.patch((t) => {
      const text = (t.reasoning || "").trim();
      if (!text) return {};
      const rows = [...(t.thoughts || []), { seq: (t.steps || []).length, text }];
      return { thoughts: rows.slice(-THOUGHTS_MAX), reasoning: "" };
    });
  };

  /**
   * **正文封段** —— 这是"分段式汇报"的全部机关。
   *
   * agent 干活的形状本来就是「说一段 → 调几个工具 → 再说一段」：模型先写一句
   * "先看一眼配置"，然后去读文件，回来再写"确认没问题，开始部署"。刷新之后从存档
   * 里恢复出来的就是这个形状（每条 assistant 消息一段，见 lib/sessionRestore），
   * 所以用户说"刷新一下才能看到分段式汇报"。
   *
   * 而直播时前端把所有 token 拼进同一个气泡、所有步骤堆进同一块过程 —— 于是
   * 一屏"一大堆叠在一起"。这里在**工具开始跑的那一刻**把已经说完的这段话封起来，
   * 让它和它下面那批工具锚在一起（seq = 当时已有多少步）。直播和刷新后看到的
   * 因此是同一个东西。
   */
  const sealSegment = () => {
    deps.patch((t) => {
      const body = (t.text || "").trim();
      if (!body) return {};
      return {
        segments: [...(t.segments || []), { seq: (t.steps || []).length, text: t.text }],
        text: "",
      };
    });
  };

  const handlers: IvyeaStreamHandlers = {
    onFileChange: (d) => deps.setFileChanges((prev) => [...prev, d]),
    onStart: (d) => {
      if (d?.session_id) deps.onSessionId?.(String(d.session_id));
      if (d?.model) deps.setModel?.(typeof d.model === "string" ? d.model : d.model?.model || "");
      if (typeof d?.read_only === "boolean") deps.setReadOnly?.(d.read_only);
    },
    onContext: (d) => deps.setCtxUsage(d),
    onSkillMatch: (d) => deps.patch({ skills: (d?.skills || []) as MatchedSkill[] }),
    onStep: (ev) => {
      // 顺序就是叙述的顺序：想 → 说 → 做。先把这一批思考收成一条，再把刚说完的
      // 那段话封段，最后记这一步。
      flushThought();
      const step = stepFromEvent(ev);
      if (step.status === "running") sealSegment();
      deps.patch((t) => ({ steps: mergeStep(t.steps || [], step) }));
    },
    // 计划**当场**落地：原来只在 onFinal 收一次，于是"接下来要干什么"要等这一轮
    // 跑完才看得到 —— 而那正是最不需要它的时刻。
    onTodos: (d) => { if (Array.isArray(d?.todos)) deps.setTodos(d.todos); },
    onPermission: (req) => deps.patch((t) => ({ approvals: [...(t.approvals || []), { req }] })),
    onPermissionTimeout: (d) => {
      deps.patch((t) => ({
        approvals: (t.approvals || []).map((a) =>
          a.req.request_id === d.request_id ? { ...a, decision: "deny" } : a),
      }));
      deps.notify("warn", "审批等待超时，这一步已自动取消。");
    },
    onEvent: (d) => {
      // 自由文本叙述是**老版本 agent 的兜底**（< v1.9 只发人话、没有结构化步骤）。
      // 新版两种都发，而它们说的是同一批动作 —— 两个都渲染就是每个动作出现两次。
      // 所以：这一轮只要收到过结构化步骤，就不再渲染叙述。
      const line = String(d?.text || "").trim().split("\n").filter(Boolean).pop() || "";
      if (!line) return;
      deps.patch((t) => {
        const steps = t.steps || [];
        if (steps.some((x) => x.phase !== "note")) return {};
        const notes = steps.filter((x) => x.phase === "note");
        const next = [...steps, noteStep(line, noteSeq++)];
        return { steps: notes.length >= MAX_NOTES ? next.slice(next.length - MAX_NOTES) : next };
      });
    },
    onToken: (chunk) => {
      finalText += chunk;
      pending += chunk;
      const now = Date.now();
      if (!firstTokenAt) firstTokenAt = now;
      lastTokenAt = now;
      schedule();
    },
    onReasoning: (d) => {
      const t = String(d?.text || "");
      if (!t) return;
      pendingThink += t;
      schedule();
    },
    // 正文的分段边界。门禁打回 = 整篇重写，旧稿作废（不清就是"同一张表连出三遍"）；
    // 去调工具 = 这段没说完，只断段不丢字。判据见 answerResetDiscards。
    onAnswerReset: (d) => {
      const reason = String(d?.reason || "");
      if (!answerResetDiscards(reason)) {
        pending += "\n\n";                 // 两段之间留个空行，别糊成一段
        schedule();
        return;
      }
      cancelFlush();
      finalText = "";
      const note = GATE_NOTE[reason] || "正在重写这段回答";
      deps.patch((t) => ({
        text: "",
        // 字凭空少了，必须说一声 —— 否则用户只会看到回答突然被清空。
        steps: mergeStep(t.steps || [], noteStep(note, noteSeq++)),
      }));
    },
    onFinal: (d) => {
      // final 到达时手里常常还攥着没落地的一帧：收尾那几个字和 final 多半在同一个
      // 网络分片里到。两种走法 —— final 自带规范文本（引证门通过后的终稿）→ 草稿
      // 作废、整体替换；不带文本（老 agent、兜底通道）→ 必须把那一帧补落地。
      if (d?.text) cancelFlush();
      else finishFlush();
      if (typeof d?.readonly_blocked === "number" && d.readonly_blocked > 0) {
        deps.patch({ readonlyBlocked: d.readonly_blocked });
      }
      if (Array.isArray(d?.todos)) deps.setTodos(d.todos);
      if (d?.usage) turnUsage = d.usage;
      if (d?.context) deps.setCtxUsage(d.context as IvyeaContextUsage);
      if (d?.text) { finalText = String(d.text); deps.patch({ text: finalText }); }
      deps.onFinal?.(d);
    },
  };

  return {
    handlers,
    finish: () => { finishFlush(); flushThought(); },
    cancel: cancelFlush,
    text: () => finalText,
    firstTokenAt: () => firstTokenAt,
    lastTokenAt: () => lastTokenAt,
    usage: () => turnUsage,
  };
}

export default createTurnStream;
